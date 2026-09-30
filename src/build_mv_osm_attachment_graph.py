#!/usr/bin/env python3
"""Build a reproducible PTD-to-OSM MV attachment candidate graph.

The graph preserves public OSM line geometry and public E-REDES PTD points.
For PTDs close to a mapped MV segment, it inserts an inferred tap at the
orthogonal projection and a spur to the PTD point.  Distant PTDs and unstable
voltage matches stay in the synthetic equivalent layer.  No inferred tap is
labelled as an observed utility terminal.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
REPORT = ROOT / "output/all_voltage/mv_osm_attachment_graph.validation.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--maximum-spur-km", type=float, default=0.2)
    parser.add_argument("--tight-spur-km", type=float, default=0.05)
    parser.add_argument("--minimum-solver-spur-km", type=float, default=0.005)
    args = parser.parse_args()
    if not 0 < args.tight_spur_km <= args.maximum_spur_km:
        parser.error("Require 0 < tight-spur-km <= maximum-spur-km")
    if args.minimum_solver_spur_km <= 0:
        parser.error("minimum-solver-spur-km must be positive")

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS candidate")
        con.execute("""CREATE OR REPLACE TABLE candidate.ptd_mv_attachment_assessment AS
            WITH station_component AS (
              SELECT component_id,
                     count(*) FILTER (WHERE anchor_status='NEAR_STATION_CANDIDATE')
                       AS near_station_count
              FROM candidate.station_sources GROUP BY 1
            ), zone AS (
              SELECT ptd_code,candidate_station_code,zone_status
              FROM candidate.ptd_zone_candidates
            )
            SELECT p.ptd_code,c.lat AS ptd_lat,c.lon AS ptd_lon,
                   p.osm_segment_id_candidate,p.osm_way_id_candidate,
                   p.voltage_kv_candidate,p.component_id,
                   p.projected_lat,p.projected_lon,p.segment_fraction,
                   p.distance_to_osm_segment_km,
                   p.vertex_candidate_component_id,p.distance_to_osm_node_km,
                   p.same_component_as_vertex_candidate,
                   coalesce(sc.near_station_count,0) AS near_station_count,
                   z.candidate_station_code,z.zone_status,
                   e.mv_voltage_kv AS equivalent_mv_voltage_kv,
                   p.voltage_kv_candidate=e.mv_voltage_kv AS voltage_assignment_consistent,
                   CASE
                     WHEN p.voltage_kv_candidate<>e.mv_voltage_kv
                       THEN 'VOLTAGE_ASSIGNMENT_CONFLICT_SYNTHETIC_FALLBACK'
                     WHEN p.distance_to_osm_segment_km>?
                       THEN 'DISTANT_FROM_PUBLIC_OSM_GEOMETRY_SYNTHETIC_FALLBACK'
                     WHEN coalesce(sc.near_station_count,0)>0
                          AND p.distance_to_osm_segment_km<=?
                       THEN 'OSM_SOURCE_ANCHORED_TIGHT_INFERRED_TAP'
                     WHEN coalesce(sc.near_station_count,0)>0
                       THEN 'OSM_SOURCE_ANCHORED_NEAR_INFERRED_TAP'
                     WHEN p.distance_to_osm_segment_km<=?
                       THEN 'OSM_UNANCHORED_TIGHT_INFERRED_TAP'
                     ELSE 'OSM_UNANCHORED_NEAR_INFERRED_TAP'
                   END AS attachment_class,
                   CASE
                     WHEN p.voltage_kv_candidate<>e.mv_voltage_kv
                       OR p.distance_to_osm_segment_km>?
                       THEN 'SYNTHETIC_EQUIVALENT_FALLBACK'
                     ELSE 'OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'
                   END AS selected_model_layer,
                   CASE
                     WHEN p.voltage_kv_candidate<>e.mv_voltage_kv THEN 0.15
                     WHEN p.distance_to_osm_segment_km>? THEN 0.20
                     WHEN coalesce(sc.near_station_count,0)>0
                          AND p.distance_to_osm_segment_km<=? THEN 0.90
                     WHEN coalesce(sc.near_station_count,0)>0 THEN 0.75
                     WHEN p.distance_to_osm_segment_km<=? THEN 0.65
                     ELSE 0.50
                   END AS engineering_confidence_score,
                   CASE
                     WHEN p.voltage_kv_candidate<>e.mv_voltage_kv
                       OR p.distance_to_osm_segment_km>?
                       THEN 'NO_PUBLIC_MV_TERMINAL_SYNTHETIC_CONNECTION_REQUIRED'
                     WHEN coalesce(sc.near_station_count,0)>0
                       THEN 'UNVERIFIED_TERMINAL_INFERENCE_ON_SOURCE_ANCHORED_OSM_COMPONENT'
                     ELSE 'UNVERIFIED_TERMINAL_INFERENCE_ON_UNANCHORED_OSM_COMPONENT'
                   END AS electrical_connection_status,
                   CASE
                     WHEN z.zone_status='BOUNDARY_AMBIGUOUS'
                       THEN 'SOURCE_ZONE_AMBIGUOUS'
                     WHEN z.candidate_station_code IS NOT NULL
                       THEN 'GRAPH_DISTANCE_SOURCE_ZONE_CANDIDATE'
                     WHEN coalesce(sc.near_station_count,0)>0
                       THEN 'SOURCE_ANCHORED_COMPONENT_ZONE_NOT_PARTITIONED'
                     ELSE 'NO_NEAR_PUBLIC_SOURCE'
                   END AS source_assignment_status,
                   'Confidence score is an engineering ranking, not a probability' AS confidence_note
            FROM candidate.ptd_segment_candidates p
            JOIN candidate.ptd_coordinates_national c USING(ptd_code)
            JOIN equivalent.ptd_connections e USING(ptd_code)
            LEFT JOIN station_component sc USING(component_id)
            LEFT JOIN zone z USING(ptd_code)
            ORDER BY p.ptd_code""", [
                args.maximum_spur_km, args.tight_spur_km, args.tight_spur_km,
                args.maximum_spur_km, args.maximum_spur_km, args.tight_spur_km,
                args.tight_spur_km, args.maximum_spur_km,
            ])

        # Fractions are quantized only for stable node identifiers.  The
        # projection coordinates and the original fraction remain available.
        con.execute("""CREATE OR REPLACE TABLE candidate.mv_inferred_tap_node AS
            SELECT 'OSMTAP:'||osm_segment_id_candidate||':'||
                     CAST(round(segment_fraction*100000000) AS BIGINT) AS node_id,
                   osm_segment_id_candidate AS parent_segment_id,
                   min(osm_way_id_candidate) AS osm_way_id,
                   min(voltage_kv_candidate) AS voltage_kv,
                   min(component_id) AS component_id,
                   round(segment_fraction*100000000)/100000000.0 AS segment_fraction,
                   avg(projected_lat) AS lat,avg(projected_lon) AS lon,
                   count(*) AS ptd_attachment_count,
                   'OSM_SEGMENT_PROJECTION_INFERRED_TAP_NOT_OPERATOR_TERMINAL'
                     AS evidence_status
            FROM candidate.ptd_mv_attachment_assessment
            WHERE selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'
              AND segment_fraction>0.00000001 AND segment_fraction<0.99999999
            GROUP BY 1,2,6""")

        con.execute("""CREATE OR REPLACE TABLE candidate.ptd_mv_terminal_candidate AS
            SELECT a.*,
                   'PTDMV:'||a.ptd_code AS ptd_terminal_node_id,
                   CASE WHEN a.selected_model_layer<>'OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'
                          THEN NULL
                        WHEN a.segment_fraction<=0.00000001 THEN s.from_node
                        WHEN a.segment_fraction>=0.99999999 THEN s.to_node
                        ELSE 'OSMTAP:'||a.osm_segment_id_candidate||':'||
                             CAST(round(a.segment_fraction*100000000) AS BIGINT)
                   END AS attachment_node_id,
                   a.distance_to_osm_segment_km AS geometric_spur_length_km,
                   CASE WHEN a.selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'
                        THEN greatest(a.distance_to_osm_segment_km,?)
                        ELSE NULL END AS solver_spur_length_km,
                   CASE WHEN a.selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'
                             AND a.distance_to_osm_segment_km<?
                        THEN 'MINIMUM_SOLVER_LENGTH_FLOOR_APPLIED'
                        WHEN a.selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'
                        THEN 'PUBLIC_POINT_TO_OSM_PROJECTION_DISTANCE'
                        ELSE 'NO_OSM_SPUR_SYNTHETIC_EQUIVALENT_RETAINED' END
                     AS spur_length_status
            FROM candidate.ptd_mv_attachment_assessment a
            JOIN candidate.osm_segments s
              ON a.osm_segment_id_candidate=s.edge_id""",
                    [args.minimum_solver_spur_km, args.minimum_solver_spur_km])

        con.execute("""CREATE OR REPLACE TABLE candidate.mv_attachment_graph_node AS
            SELECT node_id,voltage_kv,lat,lon,component_id,
                   'OSM_MAPPED_NODE' AS node_type,source_status AS evidence_status
            FROM candidate.osm_nodes WHERE voltage_kv>1
            UNION ALL
            SELECT node_id,voltage_kv,lat,lon,component_id,
                   'INFERRED_SEGMENT_TAP' AS node_type,evidence_status
            FROM candidate.mv_inferred_tap_node
            UNION ALL
            SELECT ptd_terminal_node_id,voltage_kv_candidate,ptd_lat,ptd_lon,component_id,
                   'PUBLIC_PTD_POINT_WITH_INFERRED_MV_TERMINAL' AS node_type,
                   electrical_connection_status AS evidence_status
            FROM candidate.ptd_mv_terminal_candidate
            WHERE selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'""")

        con.execute("""CREATE OR REPLACE TEMP TABLE split_points AS
            SELECT s.edge_id AS parent_segment_id,0.0 AS fraction,s.from_node AS node_id
            FROM candidate.osm_segments s
            WHERE s.voltage_kv>1 AND EXISTS (
              SELECT 1 FROM candidate.ptd_mv_terminal_candidate p
              WHERE p.osm_segment_id_candidate=s.edge_id
                AND p.selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP')
            UNION
            SELECT parent_segment_id,segment_fraction,node_id
            FROM candidate.mv_inferred_tap_node
            UNION
            SELECT s.edge_id,1.0,s.to_node
            FROM candidate.osm_segments s
            WHERE s.voltage_kv>1 AND EXISTS (
              SELECT 1 FROM candidate.ptd_mv_terminal_candidate p
              WHERE p.osm_segment_id_candidate=s.edge_id
                AND p.selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP')""")

        con.execute("""CREATE OR REPLACE TABLE candidate.mv_attachment_graph_edge AS
            WITH ordered AS (
              SELECT p.parent_segment_id,p.fraction,p.node_id,
                     lead(p.fraction) OVER w AS next_fraction,
                     lead(p.node_id) OVER w AS next_node,
                     row_number() OVER w AS part_number
              FROM split_points p
              WINDOW w AS (PARTITION BY p.parent_segment_id ORDER BY p.fraction,p.node_id)
            ), split_edge AS (
              SELECT 'OSMSPLIT:'||o.parent_segment_id||':'||o.part_number AS edge_id,
                     o.node_id AS from_node,o.next_node AS to_node,
                     s.voltage_kv,s.length_km*(o.next_fraction-o.fraction) AS geometric_length_km,
                     s.length_km*(o.next_fraction-o.fraction) AS solver_length_km,
                     s.component_id,'OSM_SEGMENT_SPLIT_AT_INFERRED_PTD_TAP' AS edge_type,
                     s.edge_id AS parent_segment_id,s.osm_way_id,
                     'OSM_GEOMETRY_WITH_INFERRED_SPLIT' AS evidence_status
              FROM ordered o JOIN candidate.osm_segments s
                ON o.parent_segment_id=s.edge_id
              WHERE o.next_node IS NOT NULL AND o.next_fraction>o.fraction
            ), untouched AS (
              SELECT s.edge_id,s.from_node,s.to_node,s.voltage_kv,
                     s.length_km AS geometric_length_km,s.length_km AS solver_length_km,
                     s.component_id,'OSM_UNCHANGED_SEGMENT' AS edge_type,
                     s.edge_id AS parent_segment_id,s.osm_way_id,s.source_status AS evidence_status
              FROM candidate.osm_segments s
              WHERE s.voltage_kv>1 AND NOT EXISTS (
                SELECT 1 FROM split_points p WHERE p.parent_segment_id=s.edge_id)
            ), spur AS (
              SELECT 'PTDSPUR:'||ptd_code AS edge_id,attachment_node_id AS from_node,
                     ptd_terminal_node_id AS to_node,voltage_kv_candidate AS voltage_kv,
                     geometric_spur_length_km AS geometric_length_km,
                     solver_spur_length_km,component_id,
                     'INFERRED_PTD_CONNECTION_SPUR' AS edge_type,
                     osm_segment_id_candidate AS parent_segment_id,
                     osm_way_id_candidate AS osm_way_id,electrical_connection_status AS evidence_status
              FROM candidate.ptd_mv_terminal_candidate
              WHERE selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'
            )
            SELECT * FROM untouched UNION ALL SELECT * FROM split_edge UNION ALL SELECT * FROM spur""")

        checks = {
            "national_ptds": con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0],
            "assessed_ptds": con.execute("SELECT count(*) FROM candidate.ptd_mv_attachment_assessment").fetchone()[0],
            "distinct_assessed_ptds": con.execute("SELECT count(DISTINCT ptd_code) FROM candidate.ptd_mv_attachment_assessment").fetchone()[0],
            "osm_attached_ptds": con.execute("""SELECT count(*) FROM candidate.ptd_mv_terminal_candidate
                WHERE selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'""").fetchone()[0],
            "synthetic_fallback_ptds": con.execute("""SELECT count(*) FROM candidate.ptd_mv_terminal_candidate
                WHERE selected_model_layer='SYNTHETIC_EQUIVALENT_FALLBACK'""").fetchone()[0],
            "voltage_assignment_conflicts": con.execute("""SELECT count(*) FROM candidate.ptd_mv_attachment_assessment
                WHERE NOT voltage_assignment_consistent""").fetchone()[0],
            "inferred_interior_tap_nodes": con.execute("SELECT count(*) FROM candidate.mv_inferred_tap_node").fetchone()[0],
            "attachment_graph_nodes": con.execute("SELECT count(*) FROM candidate.mv_attachment_graph_node").fetchone()[0],
            "attachment_graph_edges": con.execute("SELECT count(*) FROM candidate.mv_attachment_graph_edge").fetchone()[0],
            "ptd_spur_edges": con.execute("""SELECT count(*) FROM candidate.mv_attachment_graph_edge
                WHERE edge_type='INFERRED_PTD_CONNECTION_SPUR'""").fetchone()[0],
            "orphan_graph_edge_endpoints": con.execute("""SELECT count(*) FROM candidate.mv_attachment_graph_edge e
                LEFT JOIN candidate.mv_attachment_graph_node a ON e.from_node=a.node_id
                LEFT JOIN candidate.mv_attachment_graph_node b ON e.to_node=b.node_id
                WHERE a.node_id IS NULL OR b.node_id IS NULL""").fetchone()[0],
            "duplicate_graph_node_ids": con.execute("""SELECT count(*)-count(DISTINCT node_id)
                FROM candidate.mv_attachment_graph_node""").fetchone()[0],
            "duplicate_graph_edge_ids": con.execute("""SELECT count(*)-count(DISTINCT edge_id)
                FROM candidate.mv_attachment_graph_edge""").fetchone()[0],
            "nonpositive_osm_graph_edges": con.execute("""SELECT count(*) FROM candidate.mv_attachment_graph_edge
                WHERE edge_type<>'INFERRED_PTD_CONNECTION_SPUR' AND geometric_length_km<=0""").fetchone()[0],
            "split_parent_length_error_km": con.execute("""WITH actual AS (
                  SELECT parent_segment_id,sum(geometric_length_km) AS length_km
                  FROM candidate.mv_attachment_graph_edge
                  WHERE edge_type='OSM_SEGMENT_SPLIT_AT_INFERRED_PTD_TAP' GROUP BY 1)
                SELECT coalesce(max(abs(a.length_km-s.length_km)),0)
                FROM actual a JOIN candidate.osm_segments s ON a.parent_segment_id=s.edge_id""").fetchone()[0],
            "maximum_public_geometry_spur_km": con.execute("""SELECT max(geometric_spur_length_km)
                FROM candidate.ptd_mv_terminal_candidate
                WHERE selected_model_layer='OSM_GEOMETRY_WITH_INFERRED_PTD_TAP'""").fetchone()[0],
        }
        classes = dict(con.execute("""SELECT attachment_class,count(*)
            FROM candidate.ptd_mv_attachment_assessment GROUP BY 1 ORDER BY 1""").fetchall())
        source_status = dict(con.execute("""SELECT source_assignment_status,count(*)
            FROM candidate.ptd_mv_attachment_assessment GROUP BY 1 ORDER BY 1""").fetchall())

    errors: list[str] = []
    if checks["national_ptds"] != checks["assessed_ptds"] or checks["assessed_ptds"] != checks["distinct_assessed_ptds"]:
        errors.append("PTD assessment coverage or uniqueness failed")
    if checks["osm_attached_ptds"] + checks["synthetic_fallback_ptds"] != checks["national_ptds"]:
        errors.append("OSM attachment and fallback layers do not partition all PTDs")
    if checks["ptd_spur_edges"] != checks["osm_attached_ptds"]:
        errors.append("Every OSM-attached PTD must have exactly one inferred spur")
    for key in ("orphan_graph_edge_endpoints", "duplicate_graph_node_ids",
                "duplicate_graph_edge_ids", "nonpositive_osm_graph_edges"):
        if checks[key]:
            errors.append(f"{key}={checks[key]}")
    if checks["split_parent_length_error_km"] > 1e-9:
        errors.append(f"split_parent_length_error_km={checks['split_parent_length_error_km']}")
    if checks["maximum_public_geometry_spur_km"] > args.maximum_spur_km + 1e-12:
        errors.append("An OSM attachment exceeds the maximum accepted spur distance")

    report = {
        "result": "PASS" if not errors else "FAIL",
        "scenario_id": "PTD_OSM_MV_ATTACHMENT_CANDIDATE_V1",
        "rules": {
            "tight_spur_km": args.tight_spur_km,
            "maximum_osm_attachment_spur_km": args.maximum_spur_km,
            "minimum_solver_spur_km": args.minimum_solver_spur_km,
            "voltage_conflict_action": "retain synthetic equivalent; do not insert OSM tap",
            "distant_ptd_action": "retain synthetic equivalent; do not claim public line connection",
        },
        "checks": checks,
        "attachment_class_counts": classes,
        "source_assignment_status_counts": source_status,
        "errors": errors,
        "scope": "Public OSM MV geometry split at constrained inferred taps to public E-REDES PTD points",
        "limitations": [
            "Tap and spur edges are spatial engineering inferences, not operator terminal records",
            "Unanchored OSM components still require a synthetic source before power-flow use",
            "Source zones and virtual open boundaries are graph-distance scenarios without public switch states",
            "PTDs beyond 200 m or with unstable voltage assignment remain in the synthetic equivalent topology",
            "The engineering confidence score is an ordinal ranking and must not be read as a probability",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
