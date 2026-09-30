#!/usr/bin/env python3
"""Add bounded inferred station spurs and repartition large OSM MV graphs.

Public HV/MV stations farther than the strict 0.5 km OSM anchor threshold can
represent missing mapped station lead-ins.  This scenario admits a station only
when its same-voltage nearest OSM node is within a bounded distance and both
public MV short-circuit values are available.  The spur length is included in
the multi-source distance, so a distant candidate does not receive an
artificial zero-cost advantage.
"""

from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
from collections import Counter
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/mv_station_spur_zones"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--minimum-component-nodes", type=int, default=5001)
    parser.add_argument("--maximum-station-spur-km", type=float, default=2.0)
    args = parser.parse_args()
    if args.maximum_station_spur_km <= 0:
        parser.error("--maximum-station-spur-km must be positive")
    args.output.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(args.database), read_only=True) as con:
        components = con.execute(
            """SELECT component_id,node_count FROM candidate.osm_components
               WHERE node_count>=? AND near_station_count>0 ORDER BY component_id""",
            [args.minimum_component_nodes],
        ).fetchall()
        component_ids = [row[0] for row in components]
        nodes = con.execute(
            """SELECT node_id,component_id,lat,lon FROM candidate.osm_nodes
               WHERE component_id IN (SELECT unnest(?)) ORDER BY node_id""",
            [component_ids],
        ).fetchall()
        edges = con.execute(
            """SELECT edge_id,from_node,to_node,length_km,component_id
               FROM candidate.osm_segments
               WHERE component_id IN (SELECT unnest(?)) ORDER BY edge_id""",
            [component_ids],
        ).fetchall()
        stations = con.execute(
            """SELECT station_code,mv_voltage_v,nearest_osm_node,component_id,
                      node_distance_km,lat,lon,anchor_status,
                      short_circuit_max_mv_mva_public,short_circuit_min_mv_mva_public
               FROM candidate.station_sources
               WHERE component_id IN (SELECT unnest(?))
                 AND node_distance_km<=?
                 AND short_circuit_max_mv_mva_public>0
                 AND short_circuit_min_mv_mva_public>0
               ORDER BY component_id,station_code""",
            [component_ids, args.maximum_station_spur_km],
        ).fetchall()
        ptds = con.execute(
            """SELECT ptd_code,osm_segment_id_candidate,component_id,
                      connection_status,distance_to_osm_segment_km
               FROM candidate.ptd_segment_candidates
               WHERE component_id IN (SELECT unnest(?)) ORDER BY ptd_code""",
            [component_ids],
        ).fetchall()

    node_index = {row[0]: index for index, row in enumerate(nodes)}
    adjacency: list[list[tuple[int, float]]] = [[] for _ in nodes]
    for edge_id, left, right, length_km, _component in edges:
        if float(length_km) <= 0:
            raise ValueError(f"Nonpositive OSM edge {edge_id}")
        li, ri = node_index[left], node_index[right]
        adjacency[li].append((ri, float(length_km)))
        adjacency[ri].append((li, float(length_km)))

    distance = [math.inf] * len(nodes)
    owner: list[str | None] = [None] * len(nodes)
    queue: list[tuple[float, str, int]] = []
    station_rows: list[dict[str, object]] = []
    collision_count = 0
    for code, voltage, nearest, component, gap, lat, lon, status, smax, smin in stations:
        node_id = f"OSM:{int(voltage)}:{int(nearest)}"
        if node_id not in node_index:
            raise KeyError(f"Station {code} anchor {node_id} absent")
        idx = node_index[node_id]
        seed = float(gap)
        if owner[idx] is not None and owner[idx] != code:
            collision_count += 1
        if seed < distance[idx] - 1e-12 or (
            abs(seed - distance[idx]) <= 1e-12 and (owner[idx] is None or code < owner[idx])
        ):
            owner[idx] = code
            distance[idx] = seed
            heapq.heappush(queue, (seed, code, idx))
        station_rows.append({
            "station_code": code,
            "component_id": component,
            "mv_voltage_v": int(voltage),
            "station_lat": float(lat),
            "station_lon": float(lon),
            "nearest_osm_node": node_id,
            "geometric_spur_length_km": seed,
            "solver_spur_length_km": max(seed, 0.005),
            "original_anchor_status": status,
            "scenario_source_status": (
                "PUBLIC_STATION_NEAR_OSM_SOURCE" if status == "NEAR_STATION_CANDIDATE"
                else "PUBLIC_STATION_WITH_INFERRED_OSM_CONNECTION_SPUR"
            ),
            "short_circuit_max_mv_mva_public": float(smax),
            "short_circuit_min_mv_mva_public": float(smin),
            "spur_standard_catalog_id": "336903" if int(voltage) == 15000 else "336905",
            "spur_parameter_status": "PUBLIC_CABLE_STANDARD_INFERRED_ASSET_ASSIGNMENT",
        })

    while queue:
        current, code, idx = heapq.heappop(queue)
        if current > distance[idx] + 1e-12 or owner[idx] != code:
            continue
        for neighbour, length in adjacency[idx]:
            candidate = current + length
            if candidate < distance[neighbour] - 1e-10 or (
                abs(candidate - distance[neighbour]) <= 1e-10
                and (owner[neighbour] is None or code < owner[neighbour])
            ):
                distance[neighbour] = candidate
                owner[neighbour] = code
                heapq.heappush(queue, (candidate, code, neighbour))

    node_rows: list[dict[str, object]] = []
    for (node_id, component, _lat, _lon), code, dist in zip(nodes, owner, distance):
        node_rows.append({
            "node_id": node_id,
            "component_id": component,
            "candidate_station_code": code,
            "path_including_station_spur_km": round(dist, 6),
            "evidence_status": "BOUNDED_STATION_SPUR_GRAPH_DISTANCE_ZONE_UNVERIFIED",
        })

    edge_owner: dict[str, tuple[str | None, str]] = {}
    edge_rows: list[dict[str, object]] = []
    boundaries = Counter()
    for edge_id, left, right, length, component in edges:
        left_owner, right_owner = owner[node_index[left]], owner[node_index[right]]
        cross = left_owner != right_owner
        zone = None if cross else left_owner
        status = "VIRTUAL_OPEN_BOUNDARY_UNVERIFIED" if cross else "INTRA_ZONE_OSM_SEGMENT"
        if cross:
            boundaries[component] += 1
        edge_owner[edge_id] = (zone, status)
        edge_rows.append({
            "edge_id": edge_id,
            "component_id": component,
            "from_station_code": left_owner,
            "to_station_code": right_owner,
            "candidate_station_code": zone,
            "boundary_status": status,
            "length_km": float(length),
        })

    ptd_rows: list[dict[str, object]] = []
    ambiguous = 0
    for code, edge_id, component, connection, gap in ptds:
        zone, boundary_status = edge_owner[edge_id]
        if zone is None:
            ambiguous += 1
        ptd_rows.append({
            "ptd_code": code,
            "component_id": component,
            "osm_segment_id_candidate": edge_id,
            "candidate_station_code": zone,
            "zone_status": "BOUNDARY_AMBIGUOUS" if zone is None else "STATION_SPUR_GRAPH_DISTANCE_CANDIDATE",
            "connection_status": connection,
            "distance_to_osm_segment_km": float(gap),
            "edge_boundary_status": boundary_status,
        })

    outputs = (
        ("station_source_spurs.csv", station_rows, "scenario.mv_station_source_spur"),
        ("zone_nodes.csv", node_rows, "scenario.mv_station_spur_zone_node"),
        ("zone_edges.csv", edge_rows, "scenario.mv_station_spur_zone_edge"),
        ("zone_ptds.csv", ptd_rows, "scenario.mv_station_spur_zone_ptd"),
    )
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS scenario")
        for filename, data, table in outputs:
            path = args.output / filename
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(data[0]))
                writer.writeheader()
                writer.writerows(data)
            con.execute(
                f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto(?,header=true)",
                [str(path)],
            )

        changed_nodes = con.execute(
            """SELECT count(*) FROM scenario.mv_station_spur_zone_node n
               JOIN candidate.mv_zone_nodes b USING(node_id,component_id)
               WHERE n.candidate_station_code<>b.candidate_station_code"""
        ).fetchone()[0]
        changed_ptds = con.execute(
            """SELECT count(*) FROM scenario.mv_station_spur_zone_ptd n
               JOIN candidate.ptd_zone_candidates b USING(ptd_code,component_id)
               WHERE n.candidate_station_code IS DISTINCT FROM b.candidate_station_code"""
        ).fetchone()[0]
        new_sources = con.execute(
            """SELECT count(*) FROM scenario.mv_station_source_spur
               WHERE original_anchor_status='DISTANT_STATION_CANDIDATE'"""
        ).fetchone()[0]
        max_zone_nodes = con.execute(
            """SELECT max(n) FROM (SELECT component_id,candidate_station_code,count(*) n
               FROM scenario.mv_station_spur_zone_node GROUP BY 1,2)"""
        ).fetchone()[0]
        max_zone_ptds = con.execute(
            """SELECT max(n) FROM (SELECT component_id,candidate_station_code,count(*) n
               FROM scenario.mv_station_spur_zone_ptd
               WHERE candidate_station_code IS NOT NULL GROUP BY 1,2)"""
        ).fetchone()[0]

    checks = {
        "large_components": len(components),
        "eligible_public_station_voltage_records": len(station_rows),
        "new_distant_station_sources_with_inferred_spur": new_sources,
        "osm_nodes": len(node_rows),
        "unreached_nodes": sum(not math.isfinite(value) for value in distance),
        "changed_zone_owner_nodes": changed_nodes,
        "changed_zone_owner_ptds": changed_ptds,
        "boundary_ambiguous_ptds": ambiguous,
        "virtual_open_boundary_segments": sum(boundaries.values()),
        "maximum_zone_nodes": max_zone_nodes,
        "maximum_zone_ptds": max_zone_ptds,
        "station_anchor_collisions": collision_count,
    }
    errors = []
    if checks["unreached_nodes"]:
        errors.append("Some source-anchored large-component nodes were unreached")
    validation = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "rules": {
            "maximum_station_spur_km": args.maximum_station_spur_km,
            "source_eligibility": "public station voltage record, same-voltage nearest OSM node, positive public maximum and minimum MV short-circuit power",
            "partition_cost": "station-to-OSM spur length plus OSM shortest-path distance",
            "spur_solver_length_floor_km": 0.005,
            "15_kv_spur_standard": "E-REDES DMA-C33-251 LXHIOZ1 1x240/16 8.7/15 catalog 336903",
            "30_kv_spur_standard": "E-REDES DMA-C33-251 LXHIOZ1 1x240/16 18/30 catalog 336905",
        },
        "interpretation": [
            "The public station point is real; its exact OSM terminal and spur route are inferred.",
            "The bounded spur repairs likely missing mapped station lead-ins without admitting arbitrarily distant stations.",
            "Zone boundaries remain candidate normally-open points and require power-flow validation.",
        ],
        "errors": errors,
    }
    (args.output / "validation.json").write_text(
        json.dumps(validation, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(validation, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
