#!/usr/bin/env python3
"""Fuse independent public LV geometry evidence per PTD.

This classifies evidence coverage. It deliberately does not turn nearest
features into electrical terminals, feeders, or installed line segments.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    args = parser.parse_args()
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS candidate")
        con.execute("""CREATE OR REPLACE TABLE candidate.ptd_lv_topology_evidence AS
            SELECT p.ptd_code,a.construction_type,
                   p.nearest_pole_row_number,p.nearest_pole_distance_m,
                   p.pole_count_50m,p.pole_count_100m,p.pole_count_200m,
                   p.nearest_pole_same_municipality,
                   o.osm_lv_edge_id,o.osm_lv_component_id,
                   o.distance_km AS distance_to_osm_lv_segment_km,
                   o.near_status AS osm_lv_near_status,
                   c.near_ptd_count AS osm_component_near_ptd_count,
                   c.link_status AS osm_component_link_status,
                   CASE
                     WHEN o.near_status='WITHIN_100M_CANDIDATE'
                          AND p.nearest_pole_distance_m<=100
                       THEN 'OSM_LV_AND_PUBLIC_POLE_WITHIN_100M'
                     WHEN o.near_status='WITHIN_100M_CANDIDATE'
                       THEN 'OSM_LV_WITHIN_100M_ONLY'
                     WHEN p.nearest_pole_distance_m<=50
                       THEN 'PUBLIC_POLE_WITHIN_50M_ONLY'
                     WHEN p.nearest_pole_distance_m<=100
                       THEN 'PUBLIC_POLE_WITHIN_100M_ONLY'
                     WHEN p.nearest_pole_distance_m<=200
                       THEN 'PUBLIC_POLE_WITHIN_200M_ONLY'
                     ELSE 'NO_PUBLIC_LV_GEOMETRY_WITHIN_200M'
                   END AS evidence_class,
                   CASE
                     WHEN o.near_status='WITHIN_100M_CANDIDATE'
                          AND c.link_status='UNIQUE_NEAR_PTD_CANDIDATE'
                       THEN 'OSM_COMPONENT_UNIQUE_PTD_CANDIDATE_NOT_TERMINAL'
                     ELSE 'SYNTHETIC_LV_TOPOLOGY_REQUIRED'
                   END AS model_topology_status,
                   'NO_VERIFIED_LV_TERMINAL_OR_FEEDER' AS electrical_connection_status
            FROM candidate.ptd_lv_pole_density_national p
            JOIN candidate.ptd_public_attributes a USING(ptd_code)
            JOIN candidate.ptd_lv_segment_candidates o USING(ptd_code)
            LEFT JOIN candidate.lv_component_ptd_links c
              ON o.osm_lv_component_id=c.osm_lv_component_id""")
        con.execute("""CREATE OR REPLACE VIEW candidate.ptd_lv_topology_evidence_summary AS
            SELECT evidence_class,model_topology_status,construction_type,
                   count(*) AS ptd_count,
                   median(nearest_pole_distance_m) AS median_nearest_pole_m,
                   median(pole_count_200m) AS median_poles_200m
            FROM candidate.ptd_lv_topology_evidence GROUP BY 1,2,3""")
        coverage = con.execute("""SELECT evidence_class,count(*)
            FROM candidate.ptd_lv_topology_evidence GROUP BY 1 ORDER BY 1""").fetchall()
        topology = con.execute("""SELECT model_topology_status,count(*)
            FROM candidate.ptd_lv_topology_evidence GROUP BY 1 ORDER BY 1""").fetchall()
        checks = {
            "national_ptds":con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0],
            "fused_ptds":con.execute("SELECT count(*) FROM candidate.ptd_lv_topology_evidence").fetchone()[0],
            "distinct_fused_ptds":con.execute("SELECT count(DISTINCT ptd_code) FROM candidate.ptd_lv_topology_evidence").fetchone()[0],
            "null_evidence_class":con.execute("SELECT count(*) FROM candidate.ptd_lv_topology_evidence WHERE evidence_class IS NULL").fetchone()[0],
            "verified_lv_electrical_terminals":con.execute("""SELECT count(*)
                FROM candidate.ptd_lv_topology_evidence
                WHERE electrical_connection_status<>'NO_VERIFIED_LV_TERMINAL_OR_FEEDER'""").fetchone()[0],
            "evidence_class_counts":dict(coverage),
            "model_topology_status_counts":dict(topology),
        }
    errors = []
    if (checks["national_ptds"]!=checks["fused_ptds"]
            or checks["fused_ptds"]!=checks["distinct_fused_ptds"]
            or checks["null_evidence_class"]):
        errors.append("National PTD fusion coverage, uniqueness, or classification failed")
    report = {
        "result":"PASS" if not errors else "FAIL",
        "checks":checks,
        "scope":"Per-PTD fusion of public E-REDES pole density and OSM 0.4-kV segment proximity",
        "limitations":[
            "Dual-source proximity is stronger geographic context but is not an electrical connection",
            "OSM LV coverage is sparse and public pole points contain no edges or stable asset IDs",
            "Construction type describes the PTD and does not prove every outgoing feeder is aerial or underground",
            "Synthetic LV topology remains necessary wherever actual terminals and feeder edges are unavailable",
        ],
        "errors":errors,
    }
    path = args.database.parent/"lv_topology_evidence_fusion.validation.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=="__main__":
    raise SystemExit(main())
