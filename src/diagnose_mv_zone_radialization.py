#!/usr/bin/env python3
"""Test deterministic source-rooted radialization for every failed MV zone.

The diagnostic keeps the public source boundary, PTDs, loads and equipment
parameters fixed.  It opens only OSM MV edges that are not part of a
source-rooted shortest-path tree.  These are candidate normally-open points,
not claims about operator switch locations.
"""

from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
import warnings
from pathlib import Path

import duckdb
import pandapower as pp


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
BATCH = ROOT / "output/all_voltage/mv_zone_batch"
OUT = ROOT / "output/all_voltage/mv_zone_radialization"
SCALES = (0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0)


def shortest_path_tree_lines(net: pp.pandapowerNet) -> tuple[set[int], set[int], dict[int, float]]:
    mv_buses = set(int(i) for i in net.bus.index[net.bus.vn_kv > 1.0])
    sources = sorted(set(int(i) for i in net.ext_grid.bus if int(i) in mv_buses))
    if len(sources) != 1:
        raise ValueError(f"Expected one source in a source-distance zone, found {len(sources)}")
    adjacency: dict[int, list[tuple[int, float, str, int]]] = {bus: [] for bus in mv_buses}
    mv_lines: set[int] = set()
    for index, line in net.line.sort_index().iterrows():
        left, right = int(line.from_bus), int(line.to_bus)
        if left not in mv_buses or right not in mv_buses:
            continue
        length = float(line.length_km)
        name = str(line.get("name") or index)
        adjacency[left].append((right, length, name, int(index)))
        adjacency[right].append((left, length, name, int(index)))
        mv_lines.add(int(index))

    source = sources[0]
    distance = {bus: math.inf for bus in mv_buses}
    parent: dict[int, tuple[int, str, int]] = {}
    distance[source] = 0.0
    queue: list[tuple[float, int]] = [(0.0, source)]
    while queue:
        current_distance, bus = heapq.heappop(queue)
        if current_distance > distance[bus] + 1e-12:
            continue
        for neighbour, length, name, line_index in sorted(adjacency[bus], key=lambda item: (item[0], item[2], item[3])):
            candidate = current_distance + length
            old_parent = parent.get(neighbour)
            better_tie = (abs(candidate - distance[neighbour]) <= 1e-12
                          and (old_parent is None or (name, line_index, bus) < (old_parent[1], old_parent[2], old_parent[0])))
            if candidate < distance[neighbour] - 1e-12 or better_tie:
                distance[neighbour] = candidate
                parent[neighbour] = (bus, name, line_index)
                heapq.heappush(queue, (candidate, neighbour))
    unreachable = {bus for bus, value in distance.items() if not math.isfinite(value)}
    tree_lines = {item[2] for bus, item in parent.items() if bus != source}
    return tree_lines, unreachable, distance


def solve(net: pp.pandapowerNet, scale: float) -> tuple[bool, float | None, float | None, float | None]:
    net.load["scaling"] = scale
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pp.runpp(net, algorithm="nr", numba=False, max_iteration=100, init="flat")
        return (bool(net.converged), float(net.res_bus.vm_pu.min()),
                float(net.res_line.loading_percent.max()),
                float(net.res_trafo.loading_percent.max()))
    except (pp.LoadflowNotConverged, ValueError, RuntimeError, FloatingPointError):
        return False, None, None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--batch", type=Path, default=BATCH)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    batch = json.loads((args.batch / "validation.json").read_text())
    failures = [(item["station_code"], item["component_id"]) for item in batch["failures"]]
    if not failures:
        parser.error("No failed zones in the batch report")

    summaries: list[dict[str, object]] = []
    scale_rows: list[dict[str, object]] = []
    open_rows: list[dict[str, object]] = []
    for position, (station_code, component_id) in enumerate(failures, start=1):
        zone_id = f"{station_code}__{component_id.replace(':', '_')}"
        path = args.batch / zone_id / "unsolved_network.json"
        if not path.exists():
            raise FileNotFoundError(path)
        net = pp.from_json(str(path))
        tree_lines, unreachable, distances = shortest_path_tree_lines(net)
        mv_line_indices = {
            int(index) for index, line in net.line.iterrows()
            if float(net.bus.at[int(line.from_bus), "vn_kv"]) > 1.0
            and float(net.bus.at[int(line.to_bus), "vn_kv"]) > 1.0
        }
        opened = sorted(mv_line_indices - tree_lines)
        for index in opened:
            line = net.line.loc[index]
            open_rows.append({
                "station_code": station_code,
                "component_id": component_id,
                "zone_id": zone_id,
                "line_index": index,
                "edge_id": line["name"],
                "from_bus": int(line.from_bus),
                "to_bus": int(line.to_bus),
                "length_km": float(line.length_km),
                "action": "OPEN",
                "evidence_status": "SOURCE_SHORTEST_PATH_RADIALIZATION_DIAGNOSTIC_UNVERIFIED",
            })
        net.line.loc[opened, "in_service"] = False
        lv_leaf_only = all(
            int((net.trafo.lv_bus == bus).sum()) == 1
            and int((net.line.from_bus == bus).sum() + (net.line.to_bus == bus).sum()) == 0
            for bus in net.trafo.lv_bus
        )
        if not lv_leaf_only:
            raise ValueError(f"Non-leaf LV bus prevents clock-angle suppression in {zone_id}")
        net.trafo["shift_degree"] = 0.0
        best_scale = None
        full_result = (False, None, None, None)
        for scale in SCALES:
            converged, min_v, max_line, max_trafo = solve(net, scale)
            scale_rows.append({
                "station_code": station_code,
                "component_id": component_id,
                "zone_id": zone_id,
                "load_scale": scale,
                "converged": converged,
                "min_voltage_pu": min_v,
                "max_line_loading_percent": max_line,
                "max_transformer_loading_percent": max_trafo,
                "diagnostic_status": "RADIALIZATION_AND_LOAD_SCALE_NOT_OPERATOR_VALIDATED",
            })
            if converged:
                best_scale = scale
            if scale == 1.0:
                full_result = (converged, min_v, max_line, max_trafo)
        full_converged, full_min_v, full_max_line, full_max_trafo = full_result
        summaries.append({
            "station_code": station_code,
            "component_id": component_id,
            "zone_id": zone_id,
            "source_count": len(net.ext_grid),
            "mv_node_count": sum(net.bus.vn_kv > 1.0),
            "original_mv_line_count": len(mv_line_indices),
            "radial_tree_line_count": len(tree_lines),
            "candidate_open_edge_count": len(opened),
            "unreachable_mv_node_count": len(unreachable),
            "max_source_distance_km": max((value for value in distances.values() if math.isfinite(value)), default=None),
            "ptd_count": len(net.trafo),
            "full_peak_proxy_mw": float(net.load.p_mw.sum()),
            "max_converged_load_scale": best_scale,
            "full_load_converged": full_converged,
            "full_load_min_voltage_pu": full_min_v,
            "full_load_max_line_loading_percent": full_max_line,
            "full_load_max_transformer_loading_percent": full_max_trafo,
            "full_load_within_proxy_limits": bool(full_converged and full_min_v is not None
                                                  and full_min_v >= 0.9 and full_max_line <= 100
                                                  and full_max_trafo <= 100),
            "evidence_status": "SOURCE_SHORTEST_PATH_RADIALIZATION_DIAGNOSTIC_UNVERIFIED",
        })
        print(f"{position}/{len(failures)} {zone_id} full={full_converged} max_scale={best_scale}", flush=True)

    outputs = [
        ("zone_radialization_summary.csv", summaries, "study.mv_zone_radialization_diagnostic"),
        ("zone_radialization_scale.csv", scale_rows, "study.mv_zone_radialization_scale_diagnostic"),
        ("zone_radialization_open_edges.csv", open_rows, "scenario.mv_zone_radialization_open_edge"),
    ]
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("CREATE SCHEMA IF NOT EXISTS scenario")
        for filename, rows, table in outputs:
            path = args.output / filename
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
                writer.writeheader(); writer.writerows(rows)
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto(?,header=true)", [str(path)])

    full_converged = sum(bool(row["full_load_converged"]) for row in summaries)
    full_within = sum(bool(row["full_load_within_proxy_limits"]) for row in summaries)
    report = {
        "result": "PARTIAL",
        "checks": {
            "failed_zones_screened": len(failures),
            "radialized_zones_with_unreachable_nodes": sum(int(row["unreachable_mv_node_count"]) > 0 for row in summaries),
            "candidate_open_edges": len(open_rows),
            "full_load_converged_after_radialization": full_converged,
            "full_load_within_proxy_limits_after_radialization": full_within,
            "scale_diagnostic_rows": len(scale_rows),
        },
        "zone_summary": summaries,
        "interpretation": [
            "Opening non-tree OSM edges tests one deterministic normally-open-point hypothesis.",
            "A successful solve does not prove that the inferred open edges match operator switch states.",
            "Transformer clock angles are suppressed only after confirming every LV bus is a leaf; this avoids a balanced-solver initialization artifact and does not alter stored Dyn5 asset data.",
        ],
    }
    (args.output / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "checks": report["checks"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
