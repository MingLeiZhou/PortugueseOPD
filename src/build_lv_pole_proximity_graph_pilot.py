#!/usr/bin/env python3
"""Build distance-threshold candidate graphs from official LV pole points.

The public dataset provides coordinates only. Every edge produced here means
two poles are geographically near; it is not evidence of an installed cable
or an electrical connection. Radius sensitivity exposes unstable components.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import numpy as np
from pyproj import Transformer
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/lv_poles_1211_graph_pilot"
RADII = (40, 50, 60)


def write_csv(path: Path, columns: tuple[str, ...], rows: list[tuple]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(args.database), read_only=True) as con:
        poles = con.execute("""SELECT pole_id,try_cast(lon AS DOUBLE),try_cast(lat AS DOUBLE),
            municipality_code,source_sha256 FROM candidate.lv_public_poles_pilot
            ORDER BY pole_id""").fetchall()
        near_ptds = con.execute("""SELECT ptd_code,pole_id,distance_m
            FROM candidate.ptd_lv_pole_proximity_pilot ORDER BY ptd_code""").fetchall()
    if not poles or len({r[4] for r in poles})!=1 or len({r[3] for r in poles})!=1:
        parser.error("Pilot poles require one municipality and one source checksum")
    pole_ids = [r[0] for r in poles]
    index = {code: i for i, code in enumerate(pole_ids)}
    project = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)
    x, y = project.transform([r[1] for r in poles], [r[2] for r in poles])
    xy = np.column_stack((x, y))
    tree = cKDTree(xy)
    nearest, _ = tree.query(xy, k=2)
    pairs = np.asarray(sorted(tree.query_pairs(max(RADII))), dtype=int)
    distance = np.linalg.norm(xy[pairs[:,0]]-xy[pairs[:,1]], axis=1)
    source_sha = poles[0][4]
    municipality = poles[0][3]
    edge_rows = [
        (f"POLEPAIR:{municipality}:{pole_ids[a]}:{pole_ids[b]}", pole_ids[a], pole_ids[b],
         float(d), source_sha, "GEOMETRIC_PROXIMITY_NOT_ELECTRICAL_EDGE")
        for (a,b),d in zip(pairs,distance)
    ]
    write_csv(args.output/"pole_candidate_pairs.csv",
              ("candidate_pair_id","from_pole_id","to_pole_id","distance_m",
               "source_sha256","evidence_status"), edge_rows)
    memberships = []
    component_rows = []
    ptd_links = []
    radius_checks = {}
    for radius in RADII:
        use = pairs[distance<=radius]
        n = len(poles)
        graph = coo_matrix((np.ones(2*len(use)),
                            (np.r_[use[:,0],use[:,1]],np.r_[use[:,1],use[:,0]])),
                           shape=(n,n)).tocsr()
        count, labels = connected_components(graph, directed=False)
        members: dict[int,list[int]] = defaultdict(list)
        for i, label in enumerate(labels):
            members[int(label)].append(i)
        component_id = {
            label:f"POLECOMP:{municipality}:{radius}:{min(pole_ids[i] for i in indices)}"
            for label,indices in members.items()
        }
        seeds: dict[int,list[tuple[str,float]]] = defaultdict(list)
        for code,pole_id,dist in near_ptds:
            if dist<=100:
                seeds[int(labels[index[pole_id]])].append((code,float(dist)))
            ptd_links.append((code,radius,component_id[int(labels[index[pole_id]])],float(dist),
                              "WITHIN_100M_GEOMETRIC_SEED" if dist<=100 else
                              "DISTANT_NO_COMPONENT_SEED",
                              "GEOMETRIC_PROXIMITY_NOT_ELECTRICAL_CONNECTION"))
        for label,indices in members.items():
            cid = component_id[label]
            for i in indices:
                memberships.append((radius,pole_ids[i],cid,len(indices),
                                    "DISTANCE_THRESHOLD_GRAPH_NOT_ELECTRICAL_TOPOLOGY"))
            n_seed=len(seeds[label])
            component_rows.append((radius,cid,len(indices),n_seed,
                                   min((d for _,d in seeds[label]),default=None),
                                   "NO_NEAR_PTD" if n_seed==0 else
                                   "UNIQUE_NEAR_PTD_CANDIDATE" if n_seed==1 else
                                   "AMBIGUOUS_MULTIPLE_NEAR_PTDS",
                                   "DISTANCE_THRESHOLD_GRAPH_NOT_ELECTRICAL_TOPOLOGY"))
        seed_counts = Counter(len(seeds[label]) for label in members)
        sizes = Counter(labels)
        radius_checks[str(radius)] = {
            "candidate_pairs_within_radius":int(len(use)),
            "components":int(count),
            "largest_component_poles":int(max(sizes.values())),
            "isolated_poles":int(sum(v==1 for v in sizes.values())),
            "components_with_unique_near_ptd":int(seed_counts[1]),
            "components_with_multiple_near_ptds":int(sum(v for k,v in seed_counts.items() if k>1)),
            "components_without_near_ptd":int(seed_counts[0]),
        }
    write_csv(args.output/"pole_component_membership.csv",
              ("radius_m","pole_id","candidate_component_id","component_pole_count",
               "evidence_status"), memberships)
    write_csv(args.output/"pole_component_ptd_summary.csv",
              ("radius_m","candidate_component_id","pole_count","near_ptd_count",
               "nearest_ptd_seed_distance_m","link_status","evidence_status"), component_rows)
    write_csv(args.output/"ptd_component_candidates.csv",
              ("ptd_code","radius_m","candidate_component_id","nearest_pole_distance_m",
               "seed_status","evidence_status"), ptd_links)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS candidate")
        for table,file in (
            ("lv_pole_proximity_pairs_pilot","pole_candidate_pairs.csv"),
            ("lv_pole_component_membership_pilot","pole_component_membership.csv"),
            ("lv_pole_component_ptd_summary_pilot","pole_component_ptd_summary.csv"),
            ("ptd_lv_pole_component_candidates_pilot","ptd_component_candidates.csv"),
        ):
            con.execute(f"CREATE OR REPLACE TABLE candidate.{table} AS "
                        "SELECT * FROM read_csv_auto(?,header=true)",[str(args.output/file)])
        orphans = con.execute("""SELECT count(*)
            FROM candidate.lv_pole_proximity_pairs_pilot e
            LEFT JOIN candidate.lv_public_poles_pilot a ON e.from_pole_id=a.pole_id
            LEFT JOIN candidate.lv_public_poles_pilot b ON e.to_pole_id=b.pole_id
            WHERE a.pole_id IS NULL OR b.pole_id IS NULL""").fetchone()[0]
        membership_errors = con.execute("""SELECT count(*) FROM (
            SELECT radius_m,pole_id,count(*) AS n
            FROM candidate.lv_pole_component_membership_pilot
            GROUP BY 1,2 HAVING count(*)<>1)""").fetchone()[0]
    checks = {
        "municipality_code":municipality,
        "official_poles":len(poles),
        "official_ptds":len(near_ptds),
        "ptds_within_100m_of_nearest_pole":sum(d<=100 for _,_,d in near_ptds),
        "nearest_pole_to_pole_distance_median_m":float(np.median(nearest[:,1])),
        "max_radius_candidate_pairs":len(edge_rows),
        "orphan_pair_references":orphans,
        "duplicate_memberships":membership_errors,
        "radii":radius_checks,
    }
    errors = []
    if orphans or membership_errors or any(v["components"]!=
        v["components_with_unique_near_ptd"]+v["components_with_multiple_near_ptds"]+
        v["components_without_near_ptd"] for v in radius_checks.values()):
        errors.append("Candidate pole graph references or component accounting failed")
    report = {
        "result":"PASS" if not errors else "FAIL",
        "checks":checks,
        "source_sha256":source_sha,
        "source_url":"https://e-redes.opendatasoft.com/explore/dataset/apoios-baixa-tensao/",
        "scope":"Municipality pilot of pole-point proximity components at 40/50/60 m",
        "limitations":[
            "A pair within a radius is not an observed overhead or underground cable",
            "PTD seed is nearest-pole geography only, not a verified feeder terminal",
            "Components and ambiguity vary with the chosen radius",
            "This municipality cannot establish the nationwide LV electrical topology",
        ],
        "errors":errors,
    }
    (args.output/"validation.json").write_text(
        json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=="__main__":
    raise SystemExit(main())
