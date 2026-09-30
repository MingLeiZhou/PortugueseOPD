#!/usr/bin/env python3
"""Crosswalk official LV pole points and PTDs in one municipality by distance.

The public pole dataset has no pole identifiers, line identifiers, or PTD
references. Proximity is geographic evidence only, never an electrical edge.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
from pyproj import Transformer
from scipy.spatial import cKDTree

ROOT=Path(__file__).resolve().parents[1]
POLES=ROOT/"output/feasibility/lv_poles_1211.csv"
PTDS=ROOT/"output/feasibility/ptd_full_export.csv"
DB=ROOT/"output/all_voltage/all_voltage_staging.duckdb"
OUT=ROOT/"output/all_voltage/lv_poles_1211_pilot"


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poles",type=Path,default=POLES)
    parser.add_argument("--ptds",type=Path,default=PTDS)
    parser.add_argument("--database",type=Path,default=DB)
    parser.add_argument("--output",type=Path,default=OUT)
    parser.add_argument("--municipality-code",default="1211")
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    pole_hash=hashlib.sha256(args.poles.read_bytes()).hexdigest()
    with args.poles.open(encoding="utf-8-sig",newline="") as handle:
        raw_poles=list(csv.DictReader(handle,delimiter=";"))
    with args.ptds.open(encoding="utf-8-sig",newline="") as handle:
        raw_ptds=list(csv.DictReader(handle,delimiter=";"))
    poles=[]
    for index,row in enumerate(raw_poles,1):
        if row["concelho"]!=args.municipality_code:
            continue
        lat,lon=(float(v.strip()) for v in row["geo_point_2d"].split(","))
        poles.append((f"E_REDES_POLE:{args.municipality_code}:{index}",lon,lat,
                      row["distrito"],row["concelho"],pole_hash))
    ptds=[]
    for row in raw_ptds:
        if row["coddistritoconcelho"]!=args.municipality_code:
            continue
        lat,lon=(float(v.strip()) for v in row["coordenadas_geo"].split(","))
        ptds.append((row["cod_instalacao"],lon,lat))
    if not poles or not ptds:
        parser.error("No valid pole/PTD coordinates for municipality")
    project=Transformer.from_crs("EPSG:4326","EPSG:3035",always_xy=True)
    pole_xy=np.asarray([project.transform(lon,lat) for _,lon,lat,_,_,_ in poles])
    ptd_xy=np.asarray([project.transform(lon,lat) for _,lon,lat in ptds])
    distance,index=cKDTree(pole_xy).query(ptd_xy)
    links=[]
    for (code,_,_),dist,j in zip(ptds,distance,index):
        links.append((code,poles[int(j)][0],float(dist),
                      "WITHIN_100M_GEOGRAPHIC_CANDIDATE" if dist<=100 else
                      "DISTANT_POLE_NO_DIRECT_EVIDENCE",
                      "E_REDES_POINT_PROXIMITY_NOT_ELECTRICAL_CONNECTION"))
    pole_path=args.output/"lv_poles.csv"
    with pole_path.open("w",newline="",encoding="utf-8") as handle:
        w=csv.writer(handle);w.writerow(("pole_id","lon","lat","district_code",
                                         "municipality_code","source_sha256"));w.writerows(poles)
    link_path=args.output/"ptd_nearest_pole.csv"
    with link_path.open("w",newline="",encoding="utf-8") as handle:
        w=csv.writer(handle);w.writerow(("ptd_code","pole_id","distance_m",
                                         "proximity_status","evidence_status"));w.writerows(links)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS candidate")
        con.execute("CREATE OR REPLACE TABLE candidate.lv_public_poles_pilot AS SELECT * FROM read_csv_auto(?,header=true,all_varchar=true)",
                    [str(pole_path)])
        con.execute("CREATE OR REPLACE TABLE candidate.ptd_lv_pole_proximity_pilot AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(link_path)])
        orphan=con.execute("""SELECT count(*) FROM candidate.ptd_lv_pole_proximity_pilot l
            LEFT JOIN candidate.lv_public_poles_pilot p USING(pole_id)
            LEFT JOIN candidate.ptd_public_attributes t USING(ptd_code)
            WHERE p.pole_id IS NULL OR t.ptd_code IS NULL""").fetchone()[0]
    counts={"municipality_code":args.municipality_code,"public_lv_poles":len(poles),
            "public_ptds":len(ptds),"ptds_within_50m_of_pole":int(np.sum(distance<=50)),
            "ptds_within_100m_of_pole":int(np.sum(distance<=100)),
            "ptds_within_200m_of_pole":int(np.sum(distance<=200)),
            "median_ptd_nearest_pole_m":float(np.median(distance)),
            "orphan_candidate_references":orphan}
    errors=[]
    if orphan or len({p[0] for p in poles})!=len(poles) or len({p[0] for p in ptds})!=len(ptds):
        errors.append("Pole/PTD identifiers or candidate references invalid")
    report={"result":"PASS" if not errors else "FAIL","checks":counts,
            "source_sha256":pole_hash,
            "source_url":"https://e-redes.opendatasoft.com/explore/dataset/apoios-baixa-tensao/",
            "scope":"Official LV pole point proximity to PTDs in one municipality",
            "limitations":["Pole records contain no public line or PTD identifiers",
                           "No electrical terminal or pole-to-pole edge can be inferred from point proximity",
                           "Pilot municipality cannot establish national LV topology"],
            "errors":errors}
    (args.output/"validation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=='__main__':
    raise SystemExit(main())
