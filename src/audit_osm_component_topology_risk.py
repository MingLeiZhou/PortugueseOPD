#!/usr/bin/env python3
"""Compare failed and solved OSM MV components using reproducible graph metrics."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import networkx as nx
import duckdb
import pandapower as pp


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
BATCH = ROOT / "output/all_voltage/osm_component_batch"
OUT = ROOT / "output/all_voltage/osm_component_topology_risk"


def percentile_rank(values: list[float], value: float) -> float:
    """Return an inclusive empirical percentile in [0, 100]."""
    if not values:
        return math.nan
    return 100.0 * sum(candidate <= value for candidate in values) / len(values)


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[midpoint])
    return float((ordered[midpoint - 1] + ordered[midpoint]) / 2)


def component_metrics(folder: Path, failed_ids: set[str]) -> dict[str, object] | None:
    validation_path = folder / "validation.json"
    if not validation_path.exists():
        return None
    validation = json.loads(validation_path.read_text())
    component_id = validation.get("component_id")
    if not component_id:
        return None
    if folder.name != component_id.replace(":", "_"):
        return None
    failed = component_id in failed_ids
    network_path = folder / ("unsolved_network.json" if failed else "network.json")
    if not network_path.exists():
        return None
    net = pp.from_json(str(network_path))
    mv_bus_ids = set(net.bus.index[net.bus.vn_kv > 1.0])
    graph = nx.Graph()
    graph.add_nodes_from(mv_bus_ids)
    for _, line in net.line.iterrows():
        left, right = int(line.from_bus), int(line.to_bus)
        if left not in mv_bus_ids or right not in mv_bus_ids:
            continue
        length = float(line.length_km)
        if graph.has_edge(left, right):
            graph[left][right]["length_km"] = min(graph[left][right]["length_km"], length)
        else:
            graph.add_edge(left, right, length_km=length)

    sources = [int(bus) for bus in net.ext_grid.bus if int(bus) in graph]
    distances = nx.multi_source_dijkstra_path_length(graph, sources, weight="length_km") if sources else {}
    ptd_hv_buses = [int(bus) for bus in net.trafo.hv_bus]
    ptd_distances = [float(distances[bus]) for bus in ptd_hv_buses if bus in distances]
    unreachable_ptds = sum(bus not in distances for bus in ptd_hv_buses)
    connected_components = nx.number_connected_components(graph) if graph else 0
    cycle_rank = graph.number_of_edges() - graph.number_of_nodes() + connected_components
    degrees = dict(graph.degree())
    total_load_mw = float(net.load.p_mw.sum())
    total_line_km = float(net.line.length_km.sum())
    row: dict[str, object] = {
        "component_id": component_id,
        "status": "FAILED" if failed else "SOLVED",
        "source_count": len(sources),
        "mv_node_count": graph.number_of_nodes(),
        "mv_edge_count": graph.number_of_edges(),
        "connected_component_count": connected_components,
        "cycle_rank": cycle_rank,
        "dead_end_node_count": sum(degree == 1 for degree in degrees.values()),
        "branch_node_count": sum(degree > 2 for degree in degrees.values()),
        "ptd_count": len(net.trafo),
        "unreachable_ptd_count": unreachable_ptds,
        "total_line_km": total_line_km,
        "total_peak_proxy_mw": total_load_mw,
        "mw_per_line_km": total_load_mw / total_line_km if total_line_km else math.nan,
        "ptd_per_line_km": len(net.trafo) / total_line_km if total_line_km else math.nan,
        "max_source_to_ptd_km": max(ptd_distances) if ptd_distances else math.nan,
        "mean_source_to_ptd_km": sum(ptd_distances) / len(ptd_distances) if ptd_distances else math.nan,
        "max_single_ptd_mw": float(net.load.p_mw.max()) if len(net.load) else 0.0,
    }
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--batch", type=Path, default=BATCH)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    batch = json.loads((args.batch / "validation.json").read_text())
    failed_ids = {item["component_id"] for item in batch["failures"]}
    rows = []
    for folder in sorted(path for path in args.batch.iterdir() if path.is_dir()):
        row = component_metrics(folder, failed_ids)
        if row is not None:
            rows.append(row)
    if not rows:
        parser.error("No component networks found")

    numeric_fields = [
        "mv_node_count", "cycle_rank", "dead_end_node_count", "branch_node_count",
        "ptd_count", "total_line_km", "total_peak_proxy_mw", "mw_per_line_km",
        "ptd_per_line_km", "max_source_to_ptd_km", "mean_source_to_ptd_km",
        "max_single_ptd_mw",
    ]
    populations = {
        field: [float(row[field]) for row in rows if not math.isnan(float(row[field]))]
        for field in numeric_fields
    }
    for row in rows:
        for field in numeric_fields:
            value = float(row[field])
            row[f"{field}_percentile"] = percentile_rank(populations[field], value) if not math.isnan(value) else math.nan

    csv_path = args.output / "component_topology_metrics.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute(
            "CREATE OR REPLACE TABLE study.osm_component_topology_risk AS SELECT * FROM read_csv_auto(?,header=true)",
            [str(csv_path)],
        )

    solved = [row for row in rows if row["status"] == "SOLVED"]
    failed = [row for row in rows if row["status"] == "FAILED"]
    comparison = {}
    for field in numeric_fields:
        comparison[field] = {
            "solved_median": median([float(row[field]) for row in solved if not math.isnan(float(row[field]))]),
            "failed_median": median([float(row[field]) for row in failed if not math.isnan(float(row[field]))]),
        }
    report = {
        "result": "PASS",
        "checks": {
            "expected_attempted_components": int(batch["counts"]["attempted_components"]),
            "audited_components": len(rows),
            "solved_components": len(solved),
            "failed_components": len(failed),
            "components_with_unreachable_ptds": sum(int(row["unreachable_ptd_count"]) > 0 for row in rows),
        },
        "failed_component_metrics": failed,
        "solved_vs_failed_medians": comparison,
        "interpretation": [
            "Percentiles compare each failed component with all attempted bounded components.",
            "High distance, load, or density percentiles indicate a candidate feeder-boundary or PTD-allocation risk; they do not prove the operator topology.",
            "Shared OSM nodes remain geometry adjacency candidates; switch states and energized terminals are unavailable.",
        ],
    }
    if len(rows) != int(batch["counts"]["attempted_components"]):
        report["result"] = "FAIL"
    (args.output / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": report["result"], "checks": report["checks"]}, indent=2))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
