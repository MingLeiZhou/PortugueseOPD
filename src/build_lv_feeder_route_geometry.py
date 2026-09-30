#!/usr/bin/env python3
"""Build nationwide LV feeder route geometry with explicit evidence hierarchy.

The electrical feeder design remains the accepted two-section ABCN equivalent.
This script adds geographic route objects for every designed feeder.  A unique
near OSM 0.4-kV component supplies the preferred bearing; an aerial feeder may
otherwise use the direction to the nearest public E-REDES LV pole within
200 m.  Remaining routes use a deterministic simulated bearing.  Pole
proximity is never converted into an observed conductor edge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from pathlib import Path

import duckdb
from pyproj import Transformer


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/lv_feeder_route_geometry.validation.json"
SCENARIO_ID = "LV_FEEDER_ROUTE_GEOMETRY_V1"


def deterministic_angle(key: str) -> float:
    raw = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(raw[:8], "big") / 2**64 * 360.0


def direction_angle(dx: float, dy: float, fallback_key: str) -> float:
    if math.hypot(dx, dy) < 1.0:
        return deterministic_angle(fallback_key)
    return math.degrees(math.atan2(dy, dx)) % 360.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--pole-direction-limit-m", type=float, default=200.0)
    parser.add_argument("--maximum-feeder-fan-deg", type=float, default=120.0)
    args = parser.parse_args()
    if args.pole_direction_limit_m <= 0 or not 0 <= args.maximum_feeder_fan_deg <= 180:
        parser.error("Invalid pole limit or feeder fan angle")

    with duckdb.connect(str(args.database), read_only=True) as con:
        rows = con.execute("""SELECT d.ptd_code,d.feeder_count,d.section_count,
                   d.section_length_km,d.installation_scenario,d.cable_designation,
                   p.lat,p.lon,
                   pole.source_row_number,pole.lat,pole.lon,pd.nearest_pole_distance_m,
                   o.osm_lv_edge_id,o.osm_lv_component_id,o.distance_km,
                   o.projected_lat,o.projected_lon,o.segment_fraction,
                   l.link_status,s.power_tag,s.from_node,s.to_node,
                   na.lat,na.lon,nb.lat,nb.lon
            FROM phase.lv_feeder_root_peak_design_scenario d
            JOIN candidate.ptd_coordinates_national p USING(ptd_code)
            JOIN candidate.ptd_lv_pole_density_national pd USING(ptd_code)
            JOIN candidate.lv_public_poles_national pole
              ON pd.nearest_pole_row_number=pole.source_row_number
            LEFT JOIN candidate.ptd_lv_segment_candidates o USING(ptd_code)
            LEFT JOIN candidate.lv_component_ptd_links l
              ON o.osm_lv_component_id=l.osm_lv_component_id
            LEFT JOIN candidate.osm_segments s ON o.osm_lv_edge_id=s.edge_id
            LEFT JOIN candidate.osm_nodes na ON s.from_node=na.node_id
            LEFT JOIN candidate.osm_nodes nb ON s.to_node=nb.node_id
            ORDER BY d.ptd_code""").fetchall()
        national_ptds = con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0]
    if len(rows) != national_ptds:
        parser.error("LV feeder design or route evidence does not cover every PTD")
    expected_routes = sum(int(row[1]) for row in rows)

    forward = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)
    inverse = Transformer.from_crs("EPSG:3035", "EPSG:4326", always_xy=True)
    point_rows: dict[str, tuple] = {}
    edge_rows: list[tuple] = []
    route_rows: list[tuple] = []
    basis_counts: Counter[str] = Counter()

    for row in rows:
        (code, feeder_count, section_count, section_length_km, installation, cable,
         ptd_lat, ptd_lon, pole_id, pole_lat, pole_lon, pole_distance_m,
         osm_edge, osm_component, osm_distance_km, projected_lat, projected_lon,
         segment_fraction, component_link_status, power_tag, from_node, to_node,
         from_lat, from_lon, to_lat, to_lon) = row
        feeder_count = int(feeder_count)
        section_count = int(section_count)
        if section_count != 2:
            raise RuntimeError(f"Expected two LV sections for {code}, got {section_count}")
        section_m = float(section_length_km) * 1000.0
        ptd_x, ptd_y = forward.transform(float(ptd_lon), float(ptd_lat))

        use_osm = (osm_edge is not None and osm_distance_km is not None
                   and float(osm_distance_km) <= 0.1
                   and component_link_status == "UNIQUE_NEAR_PTD_CANDIDATE")
        use_pole = (not use_osm and installation == "aerial_bundle"
                    and pole_distance_m is not None
                    and float(pole_distance_m) <= args.pole_direction_limit_m)
        source_ref = None
        if use_osm:
            target_x, target_y = forward.transform(float(projected_lon), float(projected_lat))
            if math.hypot(target_x-ptd_x, target_y-ptd_y) < 1.0 and None not in (
                    from_lat, from_lon, to_lat, to_lon):
                ax, ay = forward.transform(float(from_lon), float(from_lat))
                bx, by = forward.transform(float(to_lon), float(to_lat))
                base_angle = direction_angle(bx-ax, by-ay, code+":osm")
                if deterministic_angle(code+":orientation") >= 180:
                    base_angle = (base_angle+180.0) % 360.0
            else:
                base_angle = direction_angle(target_x-ptd_x, target_y-ptd_y, code+":osm")
            basis = "PUBLIC_OSM_LV_UNIQUE_COMPONENT_BEARING"
            confidence = 0.80
            source_ref = str(osm_edge)
            evidence = "OSM_LV_GEOMETRY_DIRECTION_WITH_INFERRED_RADIAL_ROUTE"
        elif use_pole:
            pole_x, pole_y = forward.transform(float(pole_lon), float(pole_lat))
            base_angle = direction_angle(pole_x-ptd_x, pole_y-ptd_y, code+":pole")
            distance = float(pole_distance_m)
            if distance <= 50:
                basis, confidence = "PUBLIC_EREDES_POLE_BEARING_WITHIN_50M", 0.65
            elif distance <= 100:
                basis, confidence = "PUBLIC_EREDES_POLE_BEARING_WITHIN_100M", 0.55
            else:
                basis, confidence = "PUBLIC_EREDES_POLE_BEARING_WITHIN_200M", 0.45
            source_ref = str(int(pole_id))
            evidence = "PUBLIC_POLE_DIRECTION_ONLY_NO_CONDUCTOR_EDGE"
        else:
            base_angle = deterministic_angle(code+":synthetic-lv-route")
            basis = ("DETERMINISTIC_UNDERGROUND_ROUTE_SCENARIO" if installation == "underground_armoured"
                     else "DETERMINISTIC_AERIAL_ROUTE_NO_NEAR_PUBLIC_GEOMETRY")
            confidence = 0.20
            evidence = "DETERMINISTIC_SIMULATION_GEOMETRY"
        basis_counts[basis] += 1

        root_id = f"LVROUTE:{code}:ROOT"
        point_rows[root_id] = (SCENARIO_ID, root_id, code, None, 0,
                               float(ptd_lat), float(ptd_lon), 0.0,
                               "PUBLIC_EREDES_PTD_POINT", code,
                               "PUBLIC_PTD_LOCATION_ROUTE_ROOT")
        step = (0.0 if feeder_count == 1 else
                min(20.0, args.maximum_feeder_fan_deg / (feeder_count-1)))
        for feeder_index in range(1, feeder_count+1):
            offset = (feeder_index-(feeder_count+1)/2.0)*step
            angle = (base_angle+offset) % 360.0
            radians = math.radians(angle)
            ux, uy = math.cos(radians), math.sin(radians)
            feeder_id = f"LVFEEDER:{code}:{feeder_index}"
            previous = root_id
            for section_index in range(1, section_count+1):
                radial_m = section_index*section_m
                lon, lat = inverse.transform(ptd_x+ux*radial_m, ptd_y+uy*radial_m)
                node_id = f"LVROUTE:{code}:{feeder_index}:{section_index}"
                point_rows[node_id] = (
                    SCENARIO_ID, node_id, code, feeder_id, section_index,
                    float(lat), float(lon), radial_m, basis, source_ref, evidence,
                )
                edge_rows.append((
                    SCENARIO_ID, f"LVROUTEEDGE:{code}:{feeder_index}:{section_index}",
                    feeder_id, code, feeder_index, section_index, previous, node_id,
                    section_m, float(section_length_km), installation, cable,
                    base_angle, angle, offset, basis, confidence, source_ref, evidence,
                    "ENGINEERING_CONFIDENCE_SCORE_NOT_PROBABILITY",
                ))
                previous = node_id
            route_rows.append((
                SCENARIO_ID, feeder_id, code, feeder_index, feeder_count,
                section_count, section_m*section_count,
                float(section_length_km)*section_count, installation, cable,
                base_angle, angle, offset, basis, confidence, source_ref, evidence,
                "ROUTE_GEOMETRY_SCENARIO_NOT_INSTALLED_FEEDER",
            ))

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS phase")
        con.execute("""CREATE OR REPLACE TABLE phase.lv_feeder_route_scenario (
            scenario_id VARCHAR,route_id VARCHAR,ptd_code VARCHAR,feeder_index INTEGER,
            feeder_count INTEGER,section_count INTEGER,total_geometry_length_m DOUBLE,
            total_solver_length_km DOUBLE,installation_scenario VARCHAR,
            cable_designation VARCHAR,base_bearing_deg DOUBLE,feeder_bearing_deg DOUBLE,
            feeder_bearing_offset_deg DOUBLE,route_basis VARCHAR,
            engineering_confidence_score DOUBLE,source_geometry_ref VARCHAR,
            evidence_status VARCHAR,model_status VARCHAR)""")
        con.executemany("INSERT INTO phase.lv_feeder_route_scenario VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        route_rows)
        con.execute("""CREATE OR REPLACE TABLE phase.lv_feeder_route_point_scenario (
            scenario_id VARCHAR,route_point_id VARCHAR,ptd_code VARCHAR,route_id VARCHAR,
            point_sequence INTEGER,lat DOUBLE,lon DOUBLE,radial_distance_m DOUBLE,
            geometry_basis VARCHAR,source_geometry_ref VARCHAR,evidence_status VARCHAR)""")
        con.executemany("INSERT INTO phase.lv_feeder_route_point_scenario VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        list(point_rows.values()))
        con.execute("""CREATE OR REPLACE TABLE phase.lv_feeder_route_edge_scenario (
            scenario_id VARCHAR,route_edge_id VARCHAR,route_id VARCHAR,ptd_code VARCHAR,
            feeder_index INTEGER,section_index INTEGER,from_route_point_id VARCHAR,
            to_route_point_id VARCHAR,geometry_length_m DOUBLE,solver_length_km DOUBLE,
            installation_scenario VARCHAR,cable_designation VARCHAR,
            base_bearing_deg DOUBLE,feeder_bearing_deg DOUBLE,
            feeder_bearing_offset_deg DOUBLE,route_basis VARCHAR,
            engineering_confidence_score DOUBLE,source_geometry_ref VARCHAR,
            evidence_status VARCHAR,confidence_note VARCHAR)""")
        con.executemany("INSERT INTO phase.lv_feeder_route_edge_scenario VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        edge_rows)
        checks = {
            "national_ptds": national_ptds,
            "route_ptds": con.execute("SELECT count(DISTINCT ptd_code) FROM phase.lv_feeder_route_scenario").fetchone()[0],
            "route_count": con.execute("SELECT count(*) FROM phase.lv_feeder_route_scenario").fetchone()[0],
            "route_point_count": con.execute("SELECT count(*) FROM phase.lv_feeder_route_point_scenario").fetchone()[0],
            "route_edge_count": con.execute("SELECT count(*) FROM phase.lv_feeder_route_edge_scenario").fetchone()[0],
            "duplicate_route_ids": con.execute("SELECT count(*)-count(DISTINCT route_id) FROM phase.lv_feeder_route_scenario").fetchone()[0],
            "duplicate_route_point_ids": con.execute("SELECT count(*)-count(DISTINCT route_point_id) FROM phase.lv_feeder_route_point_scenario").fetchone()[0],
            "duplicate_route_edge_ids": con.execute("SELECT count(*)-count(DISTINCT route_edge_id) FROM phase.lv_feeder_route_edge_scenario").fetchone()[0],
            "orphan_route_edge_points": con.execute("""SELECT count(*)
                FROM phase.lv_feeder_route_edge_scenario e
                LEFT JOIN phase.lv_feeder_route_point_scenario a
                  ON e.from_route_point_id=a.route_point_id
                LEFT JOIN phase.lv_feeder_route_point_scenario b
                  ON e.to_route_point_id=b.route_point_id
                WHERE a.route_point_id IS NULL OR b.route_point_id IS NULL""").fetchone()[0],
            "routes_without_two_edges": con.execute("""SELECT count(*) FROM (
                SELECT route_id,count(*) n FROM phase.lv_feeder_route_edge_scenario
                GROUP BY 1 HAVING n<>2)""").fetchone()[0],
            "invalid_coordinates": con.execute("""SELECT count(*) FROM phase.lv_feeder_route_point_scenario
                WHERE lat NOT BETWEEN 36 AND 43 OR lon NOT BETWEEN -10 AND -6""").fetchone()[0],
            "solver_length_mismatches": con.execute("""SELECT count(*)
                FROM phase.lv_feeder_route_edge_scenario
                WHERE abs(solver_length_km*1000-geometry_length_m)>1e-9""").fetchone()[0],
            "unlabelled_route_evidence": con.execute("""SELECT count(*)
                FROM phase.lv_feeder_route_edge_scenario
                WHERE route_basis IS NULL OR evidence_status IS NULL""").fetchone()[0],
        }

    errors: list[str] = []
    if checks["route_ptds"] != checks["national_ptds"] or checks["route_count"] != expected_routes:
        errors.append("National PTD or accepted feeder-count coverage failed")
    if checks["route_point_count"] != checks["national_ptds"] + 2*checks["route_count"]:
        errors.append("Route point cardinality does not match shared PTD roots plus two points per feeder")
    if checks["route_edge_count"] != 2*checks["route_count"]:
        errors.append("Every feeder must have two route edges")
    for key in ("duplicate_route_ids", "duplicate_route_point_ids", "duplicate_route_edge_ids",
                "orphan_route_edge_points", "routes_without_two_edges", "invalid_coordinates",
                "solver_length_mismatches", "unlabelled_route_evidence"):
        if checks[key]:
            errors.append(f"{key}={checks[key]}")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "scenario_id": SCENARIO_ID,
        "checks": checks,
        "ptd_route_basis_counts": dict(sorted(basis_counts.items())),
        "rules": {
            "priority": [
                "unique OSM 0.4-kV component within 100 m",
                "nearest public E-REDES LV pole direction within 200 m for aerial feeder designs",
                "deterministic simulated bearing",
            ],
            "section_geometry_length_m": 50.0,
            "two_sections_per_feeder": True,
            "parallel_feeder_fan_maximum_degrees": args.maximum_feeder_fan_deg,
        },
        "scope": "Nationwide geographic route scenario for every accepted LV feeder design",
        "limitations": [
            "OSM and pole evidence determines direction only; it does not prove an electrical feeder or terminal",
            "Public pole row numbers are snapshot locators rather than stable asset identifiers",
            "Underground and evidence-poor routes use deterministic simulated bearings",
            "Every route retains the accepted two-section 50-m electrical equivalent rather than claiming surveyed length",
            "Parallel feeder fan-out is a visualization and simulation layout rule, not an observed corridor",
        ],
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
