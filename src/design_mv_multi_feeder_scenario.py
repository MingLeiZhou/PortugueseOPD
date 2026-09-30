#!/usr/bin/env python3
"""Design graph-aware multi-feeder circuits for stressed OSM MV zones.

PTDs are ordered by a deterministic source-rooted shortest-path tree and packed
into contiguous feeder groups by synchronized apparent power.  Each group uses
the union of OSM paths from the public source to its PTDs.  Shared trunks gain
one circuit per feeder group while downstream branches remain single circuit
where possible.  This preserves public line geometry and labels circuit counts
as a planning inference rather than installed-asset evidence.
"""

from __future__ import annotations

import argparse
import copy
import csv
import heapq
import json
import math
import warnings
from collections import defaultdict
from pathlib import Path

import duckdb
import pandapower as pp


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
BATCH = ROOT / "output/all_voltage/mv_zone_batch"
LOADS = ROOT / "output/all_voltage/mv_zone_topology_consistent_peak/zone_synchronized_peak_ptd_loads.csv"
OUT = ROOT / "output/all_voltage/mv_multi_feeder_design"
TARGET_MVA = (6.0, 5.0, 4.0, 3.0, 2.0)
MAX_FEEDERS = 16


def load_snapshot(path: Path) -> dict[tuple[str, str], tuple[float, float]]:
    result = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["scenario"] == "ORIGINAL_CANDIDATE_GRAPH":
                result[(row["zone_id"], row["ptd_code"])] = (
                    float(row["p_mw"]), float(row["q_mvar"])
                )
    return result


def source_tree(net: pp.pandapowerNet) -> tuple[int, dict[int, tuple[int, int]], dict[int, list[int]]]:
    mv_buses = set(int(i) for i in net.bus.index[net.bus.vn_kv > 1.0])
    sources = sorted(set(int(i) for i in net.ext_grid.bus if int(i) in mv_buses))
    if len(sources) != 1:
        raise ValueError(f"Expected one MV source, found {len(sources)}")
    source = sources[0]
    adjacency: dict[int, list[tuple[int, float, int, str]]] = {bus: [] for bus in mv_buses}
    for index, line in net.line.sort_index().iterrows():
        left, right = int(line.from_bus), int(line.to_bus)
        if left not in mv_buses or right not in mv_buses:
            continue
        item = (float(line.length_km), int(index), str(line.get("name") or index))
        adjacency[left].append((right, *item))
        adjacency[right].append((left, *item))
    distance = {bus: math.inf for bus in mv_buses}
    parent: dict[int, tuple[int, int]] = {}
    distance[source] = 0.0
    queue = [(0.0, source)]
    while queue:
        current, bus = heapq.heappop(queue)
        if current > distance[bus] + 1e-12:
            continue
        for neighbour, length, line_index, name in sorted(
            adjacency[bus], key=lambda item: (item[0], item[3], item[2])
        ):
            candidate = current + length
            old = parent.get(neighbour)
            tie = abs(candidate - distance[neighbour]) <= 1e-12 and (
                old is None or (name, line_index, bus) < (str(net.line.at[old[1], "name"]), old[1], old[0])
            )
            if candidate < distance[neighbour] - 1e-12 or tie:
                distance[neighbour] = candidate
                parent[neighbour] = (bus, line_index)
                heapq.heappush(queue, (candidate, neighbour))
    unreachable = [bus for bus, value in distance.items() if not math.isfinite(value)]
    if unreachable:
        raise ValueError(f"{len(unreachable)} unreachable MV buses")
    children: dict[int, list[int]] = defaultdict(list)
    for child, (upstream, _line) in parent.items():
        children[upstream].append(child)
    for value in children.values():
        value.sort(key=lambda bus: (str(net.bus.at[bus, "name"]), bus))
    return source, parent, children


def dfs_order(source: int, children: dict[int, list[int]]) -> dict[int, int]:
    order: dict[int, int] = {}
    stack = [source]
    position = 0
    while stack:
        bus = stack.pop()
        order[bus] = position
        position += 1
        stack.extend(reversed(children.get(bus, [])))
    return order


def feeder_design(
    net: pp.pandapowerNet,
    target_mva: float,
) -> tuple[dict[int, int], dict[int, int], int, list[float]]:
    source, parent, children = source_tree(net)
    order = dfs_order(source, children)
    loads = []
    for index, row in net.load.iterrows():
        lv_bus = int(row.bus)
        trafos = net.trafo.index[net.trafo.lv_bus == lv_bus]
        if len(trafos) != 1:
            raise ValueError(f"Load {index} does not have exactly one PTD transformer")
        mv_bus = int(net.trafo.at[int(trafos[0]), "hv_bus"])
        apparent = math.hypot(float(row.p_mw), float(row.q_mvar))
        loads.append((order[mv_bus], str(row["name"]), int(index), mv_bus, apparent))
    loads.sort()
    groups: list[list[tuple[int, str, int, int, float]]] = []
    current: list[tuple[int, str, int, int, float]] = []
    current_mva = 0.0
    for item in loads:
        if current and current_mva + item[4] > target_mva:
            groups.append(current)
            current = []
            current_mva = 0.0
        current.append(item)
        current_mva += item[4]
    if current:
        groups.append(current)
    if len(groups) > MAX_FEEDERS:
        return {}, {}, len(groups), [sum(item[4] for item in group) for group in groups]

    line_groups: dict[int, set[int]] = defaultdict(set)
    load_group: dict[int, int] = {}
    for group_id, group in enumerate(groups, start=1):
        for _position, _name, load_index, bus, _mva in group:
            load_group[load_index] = group_id
            cursor = bus
            while cursor != source:
                upstream, line_index = parent[cursor]
                line_groups[line_index].add(group_id)
                cursor = upstream
    circuit_count = {index: max(1, len(ids)) for index, ids in line_groups.items()}
    return circuit_count, load_group, len(groups), [sum(item[4] for item in group) for group in groups]


def solve(net: pp.pandapowerNet) -> tuple[bool, float | None, float | None, float | None]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            pp.runpp(net, algorithm="nr", numba=False, max_iteration=100, init="flat")
        return (
            bool(net.converged),
            float(net.res_bus.vm_pu.min()),
            float(net.res_line.loading_percent.max()),
            float(net.res_trafo.loading_percent.max()),
        )
    except (pp.LoadflowNotConverged, ValueError, RuntimeError, FloatingPointError):
        return False, None, None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--batch", type=Path, default=BATCH)
    parser.add_argument("--loads", type=Path, default=LOADS)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    snapshots = load_snapshot(args.loads)
    failures = json.loads((args.batch / "validation.json").read_text())["failures"]
    zones = [(row["station_code"], row["component_id"]) for row in failures]

    rows: list[dict[str, object]] = []
    selected_lines: list[dict[str, object]] = []
    selected_loads: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    for position, (station, component) in enumerate(zones, start=1):
        zone_id = f"{station}__{component.replace(':', '_')}"
        path = args.batch / zone_id / "unsolved_network.json"
        base = pp.from_json(str(path))
        for index, name in base.load.name.items():
            code = str(name).split(":")[1]
            base.load.at[index, "p_mw"], base.load.at[index, "q_mvar"] = snapshots[(zone_id, code)]
        if not all(
            int((base.trafo.lv_bus == bus).sum()) == 1
            and int((base.line.from_bus == bus).sum() + (base.line.to_bus == bus).sum()) == 0
            for bus in base.trafo.lv_bus
        ):
            raise ValueError(f"Non-leaf LV bus in {zone_id}")
        base.trafo["shift_degree"] = 0.0
        selected = None
        for target in TARGET_MVA:
            counts, load_groups, feeder_count, feeder_mva = feeder_design(base, target)
            if feeder_count > MAX_FEEDERS:
                rows.append({
                    "station_code": station, "component_id": component, "zone_id": zone_id,
                    "target_feeder_mva": target, "feeder_count": feeder_count,
                    "maximum_corridor_circuit_count": None, "incremental_circuit_km": None,
                    "converged": False, "min_voltage_pu": None,
                    "max_line_loading_percent": None, "max_transformer_loading_percent": None,
                    "within_proxy_operating_limits": False,
                    "status": "SKIPPED_MORE_THAN_16_FEEDERS",
                })
                continue
            net = copy.deepcopy(base)
            incremental_km = 0.0
            for line_index, count in counts.items():
                if count <= 1:
                    continue
                incremental_km += float(net.line.at[line_index, "length_km"]) * (count - 1)
                net.line.at[line_index, "r_ohm_per_km"] /= count
                net.line.at[line_index, "x_ohm_per_km"] /= count
                net.line.at[line_index, "c_nf_per_km"] *= count
                net.line.at[line_index, "max_i_ka"] *= count
            converged, min_v, max_line, max_trafo = solve(net)
            within = bool(
                converged and min_v is not None and min_v >= 0.9
                and max_line is not None and max_line <= 100
                and max_trafo is not None and max_trafo <= 100
            )
            result = {
                "station_code": station, "component_id": component, "zone_id": zone_id,
                "target_feeder_mva": target, "feeder_count": feeder_count,
                "maximum_corridor_circuit_count": max(counts.values(), default=1),
                "incremental_circuit_km": incremental_km,
                "converged": converged, "min_voltage_pu": min_v,
                "max_line_loading_percent": max_line,
                "max_transformer_loading_percent": max_trafo,
                "within_proxy_operating_limits": within,
                "status": "TESTED_GRAPH_AWARE_MULTI_FEEDER_DESIGN",
            }
            rows.append(result)
            if within and selected is None:
                selected = (result, counts, load_groups, feeder_mva)
                break
        if selected is not None:
            result, counts, load_groups, feeder_mva = selected
            for line_index, line in base.line.iterrows():
                from_mv = float(base.bus.at[int(line.from_bus), "vn_kv"]) > 1.0
                to_mv = float(base.bus.at[int(line.to_bus), "vn_kv"]) > 1.0
                if not (from_mv and to_mv):
                    continue
                count = counts.get(int(line_index), 1)
                selected_lines.append({
                    "zone_id": zone_id, "station_code": station, "component_id": component,
                    "line_index": int(line_index), "edge_part_id": str(line["name"]),
                    "inferred_circuit_count": count,
                    "geometric_length_km": float(line.length_km),
                    "electrical_circuit_km": float(line.length_km) * count,
                    "evidence_status": "OSM_GEOMETRY_GRAPH_AWARE_MULTI_FEEDER_CIRCUIT_COUNT_INFERRED",
                })
            for load_index, group_id in load_groups.items():
                code = str(base.load.at[load_index, "name"]).split(":")[1]
                selected_loads.append({
                    "zone_id": zone_id, "station_code": station, "component_id": component,
                    "ptd_code": code, "inferred_feeder_number": group_id,
                    "target_feeder_mva": result["target_feeder_mva"],
                    "evidence_status": "SOURCE_TREE_CONTIGUOUS_LOAD_PACKING_INFERRED",
                })
            summaries.append({**result, "feeder_mva_min": min(feeder_mva), "feeder_mva_max": max(feeder_mva)})
        else:
            summaries.append({
                "station_code": station, "component_id": component, "zone_id": zone_id,
                "target_feeder_mva": None, "feeder_count": None,
                "maximum_corridor_circuit_count": None, "incremental_circuit_km": None,
                "converged": False, "min_voltage_pu": None,
                "max_line_loading_percent": None, "max_transformer_loading_percent": None,
                "within_proxy_operating_limits": False,
                "status": "NO_TESTED_MULTI_FEEDER_DESIGN_WITHIN_LIMITS",
                "feeder_mva_min": None, "feeder_mva_max": None,
            })
        print(f"{position}/{len(zones)} {zone_id} selected={selected is not None}", flush=True)

    outputs = (
        ("zone_multi_feeder_sensitivity.csv", rows, "study.mv_multi_feeder_sensitivity"),
        ("zone_multi_feeder_summary.csv", summaries, "scenario.mv_multi_feeder_design"),
        ("zone_multi_feeder_line_circuits.csv", selected_lines, "scenario.mv_multi_feeder_line_circuit"),
        ("zone_multi_feeder_ptd_assignment.csv", selected_loads, "scenario.mv_multi_feeder_ptd_assignment"),
    )
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("CREATE SCHEMA IF NOT EXISTS scenario")
        for filename, data, table in outputs:
            path = args.output / filename
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(data[0]))
                writer.writeheader(); writer.writerows(data)
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto(?,header=true)", [str(path)])

    passed = sum(bool(row["within_proxy_operating_limits"]) for row in summaries)
    validation = {
        "result": "PASS" if passed == len(zones) else "PARTIAL",
        "checks": {
            "stressed_zones": len(zones),
            "zones_with_selected_multi_feeder_design": passed,
            "zones_without_feasible_tested_design": len(zones) - passed,
            "selected_line_parts": len(selected_lines),
            "selected_ptd_assignments": len(selected_loads),
            "maximum_selected_feeder_count": max((int(row["feeder_count"]) for row in summaries if row["feeder_count"]), default=0),
            "maximum_selected_corridor_circuit_count": max((int(row["maximum_corridor_circuit_count"]) for row in summaries if row["maximum_corridor_circuit_count"]), default=0),
        },
        "rules": {
            "target_feeder_mva_tested": list(TARGET_MVA),
            "maximum_feeders_per_station_zone": MAX_FEEDERS,
            "grouping": "deterministic DFS-contiguous packing on source-rooted OSM shortest-path tree",
            "corridor_circuit_count": "number of distinct inferred feeder groups whose source-to-PTD paths use the OSM line part",
            "parallel_equivalent": "R/N, X/N, N*C, N*Imax",
        },
        "interpretation": [
            "OSM geometry is retained; feeder identity and circuit multiplicity are inferred planning values.",
            "The selected design is the least reinforced tested target, in descending target-MVA order, that converges and meets voltage, line and transformer limits.",
            "The design is suitable for a synthetic public-data-based simulation network and is not evidence of the operator's installed circuit layout.",
        ],
    }
    (args.output / "validation.json").write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(validation, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
