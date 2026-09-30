#!/usr/bin/env python3
"""Import nationwide official LV pole points and quantify PTD proximity.

Pole IDs in this import are snapshot row locators, because the public CSV has
no stable pole asset ID. All PTD links and radius counts are geographic only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
POLES = ROOT / "output/feasibility/lv_poles_national.csv"
PTDS = ROOT / "output/feasibility/ptd_full_export.csv"
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
SOURCE_URL = "https://e-redes.opendatasoft.com/explore/dataset/apoios-baixa-tensao/"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4*1024*1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poles", type=Path, default=POLES)
    parser.add_argument("--ptds", type=Path, default=PTDS)
    parser.add_argument("--database", type=Path, default=DB)
    args = parser.parse_args()
    pole_sha, ptd_sha = sha256(args.poles), sha256(args.ptds)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS candidate")
        con.execute("""CREATE OR REPLACE TABLE candidate.lv_public_poles_national AS
            WITH parsed AS (
              SELECT row_number() OVER () AS source_row_number,
                     try_cast(trim(split_part(geo_point_2d,',',1)) AS DOUBLE) AS lat,
                     try_cast(trim(split_part(geo_point_2d,',',2)) AS DOUBLE) AS lon,
                     distrito AS district_code,concelho AS municipality_code
              FROM read_csv(?,delim=';',header=true,all_varchar=true,encoding='utf-8')
            )
            SELECT source_row_number,lat,lon,district_code,municipality_code,
                   ? AS source_sha256,
                   'PUBLIC_EREDES_LV_POLE_POINT_NO_ASSET_ID' AS evidence_status
            FROM parsed""", [str(args.poles),pole_sha])
        con.execute("""CREATE OR REPLACE TABLE candidate.ptd_coordinates_national AS
            SELECT cod_instalacao AS ptd_code,
                   try_cast(trim(split_part(coordenadas_geo,',',1)) AS DOUBLE) AS lat,
                   try_cast(trim(split_part(coordenadas_geo,',',2)) AS DOUBLE) AS lon,
                   coddistritoconcelho AS municipality_code,
                   ? AS source_sha256,'PUBLIC_EREDES_PTD_POINT' AS evidence_status
            FROM read_csv(?,delim=';',header=true,all_varchar=true,encoding='utf-8')""",
                    [ptd_sha,str(args.ptds)])
        pole_arrays = con.execute("""SELECT source_row_number,lon,lat,
            coalesce(municipality_code,'') AS municipality_code
            FROM candidate.lv_public_poles_national ORDER BY source_row_number""").fetchnumpy()
        ptd_rows = con.execute("""SELECT ptd_code,lon,lat,municipality_code
            FROM candidate.ptd_coordinates_national ORDER BY ptd_code""").fetchall()
        pole_quality = con.execute("""SELECT count(*),count(DISTINCT source_row_number),
            count(DISTINCT municipality_code),
            count(*) FILTER (WHERE lat NOT BETWEEN 36 AND 43 OR lon NOT BETWEEN -10 AND -6),
            count(*) FILTER (WHERE municipality_code IS NULL OR municipality_code='')
            FROM candidate.lv_public_poles_national""").fetchone()
        ptd_quality = con.execute("""SELECT count(*),count(DISTINCT ptd_code),
            count(*) FILTER (WHERE lat NOT BETWEEN 36 AND 43 OR lon NOT BETWEEN -10 AND -6)
            FROM candidate.ptd_coordinates_national""").fetchone()
    if pole_quality[3] or ptd_quality[2]:
        parser.error("Invalid national pole/PTD coordinates; refusing proximity construction")
    project = Transformer.from_crs("EPSG:4326","EPSG:3035",always_xy=True)
    px, py = project.transform(pole_arrays["lon"],pole_arrays["lat"])
    pole_xy = np.column_stack((px,py))
    tree = cKDTree(pole_xy)
    ptd_x,ptd_y = project.transform([r[1] for r in ptd_rows],
                                    [r[2] for r in ptd_rows])
    ptd_xy = np.column_stack((ptd_x,ptd_y))
    distance,nearest = tree.query(ptd_xy,k=1)
    counts = {radius:tree.query_ball_point(ptd_xy,radius,return_length=True,workers=-1)
              for radius in (50,100,200)}
    pole_id = pole_arrays["source_row_number"]
    pole_municipality = pole_arrays["municipality_code"]
    rows = []
    for i,(code,_,_,ptd_municipality) in enumerate(ptd_rows):
        j = int(nearest[i])
        rows.append((code,int(pole_id[j]),float(distance[i]),
                     int(counts[50][i]),int(counts[100][i]),int(counts[200][i]),
                     None if not str(pole_municipality[j]) else
                     bool(str(ptd_municipality)==str(pole_municipality[j])),
                     "WITHIN_100M_GEOGRAPHIC_CANDIDATE" if distance[i]<=100 else
                     "DISTANT_POLE_NO_DIRECT_EVIDENCE",pole_sha,ptd_sha,
                     "GEOGRAPHIC_DENSITY_NOT_ELECTRICAL_CONNECTION"))
    with duckdb.connect(str(args.database)) as con:
        con.execute("""CREATE OR REPLACE TABLE candidate.ptd_lv_pole_density_national (
            ptd_code VARCHAR,nearest_pole_row_number BIGINT,nearest_pole_distance_m DOUBLE,
            pole_count_50m INTEGER,pole_count_100m INTEGER,pole_count_200m INTEGER,
            nearest_pole_same_municipality BOOLEAN,proximity_status VARCHAR,
            pole_source_sha256 VARCHAR,ptd_source_sha256 VARCHAR,evidence_status VARCHAR)""")
        con.executemany("INSERT INTO candidate.ptd_lv_pole_density_national VALUES (?,?,?,?,?,?,?,?,?,?,?)",rows)
        orphan = con.execute("""SELECT count(*) FROM candidate.ptd_lv_pole_density_national d
            LEFT JOIN candidate.lv_public_poles_national p
              ON d.nearest_pole_row_number=p.source_row_number
            LEFT JOIN candidate.ptd_coordinates_national t USING(ptd_code)
            WHERE p.source_row_number IS NULL OR t.ptd_code IS NULL""").fetchone()[0]
        pilot = con.execute("""SELECT count(*),
            count(*) FILTER (WHERE n.nearest_pole_same_municipality),
            count(*) FILTER (WHERE n.nearest_pole_same_municipality=false),
            max(abs(n.nearest_pole_distance_m-p.distance_m))
              FILTER (WHERE n.nearest_pole_same_municipality)
            FROM candidate.ptd_lv_pole_density_national n
            JOIN candidate.ptd_lv_pole_proximity_pilot p USING(ptd_code)""").fetchone()
    checks = {
        "public_pole_rows":int(pole_quality[0]),
        "unique_pole_snapshot_row_numbers":int(pole_quality[1]),
        "municipalities_with_poles":int(pole_quality[2]),
        "poles_without_municipality_code":int(pole_quality[4]),
        "public_ptd_rows":int(ptd_quality[0]),
        "unique_ptd_codes":int(ptd_quality[1]),
        "ptds_with_pole_within_50m":int(np.sum(distance<=50)),
        "ptds_with_pole_within_100m":int(np.sum(distance<=100)),
        "ptds_with_pole_within_200m":int(np.sum(distance<=200)),
        "ptds_with_at_least_one_pole_within_200m":int(np.sum(counts[200]>0)),
        "nearest_pole_same_municipality_unknown_ptds":sum(r[6] is None for r in rows),
        "nearest_pole_cross_municipality_ptds":sum(r[6] is False for r in rows),
        "median_nearest_pole_distance_m":float(np.median(distance)),
        "median_poles_within_200m":float(np.median(counts[200])),
        "orphan_references":int(orphan),
        "pilot_comparison_ptds":int(pilot[0]),
        "pilot_same_municipality_nearest_ptds":int(pilot[1]),
        "pilot_cross_municipality_nearest_ptds":int(pilot[2]),
        "pilot_same_municipality_distance_max_difference_m":float(pilot[3]) if pilot[3] is not None else None,
    }
    errors = []
    if (checks["public_pole_rows"]!=checks["unique_pole_snapshot_row_numbers"]
            or checks["public_ptd_rows"]!=checks["unique_ptd_codes"]
            or checks["public_ptd_rows"]!=len(rows) or orphan):
        errors.append("Nationwide pole/PTD coverage or foreign-key integrity failed")
    if checks["ptds_with_pole_within_200m"]!=checks["ptds_with_at_least_one_pole_within_200m"]:
        errors.append("Nearest-pole radius and ball-count results disagree")
    report = {
        "result":"PASS" if not errors else "FAIL",
        "checks":checks,
        "pole_source_sha256":pole_sha,
        "ptd_source_sha256":ptd_sha,
        "source_url":SOURCE_URL,
        "scope":"Nationwide public LV pole coordinates and PTD radius-density evidence",
        "limitations":[
            "Pole snapshot row numbers are locators only; source contains no stable pole asset IDs",
            "Nearest poles and radius counts provide no cable, phase, terminal, or feeder relation",
            "Different municipalities can share an electrical feeder; municipality agreement is descriptive only",
            "No inferred candidate pair is promoted to a verified electrical edge",
        ],
        "errors":errors,
    }
    path = args.database.parent/"lv_poles_national.validation.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=="__main__":
    raise SystemExit(main())
