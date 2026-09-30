#!/usr/bin/env python3
"""Close the three remaining bounded OSM-component AC validation gaps.

The same synchronized-load, public-standard conductor, inferred radial feeder
and PTD capacity rules used for large source zones are applied here.  A
multi-source component is split into a deterministic shortest-path forest with
candidate normally-open cross-source boundaries.
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
import pandapower.shortcircuit as sc

import build_mv_validated_operating_scenario as mv
from design_mv_multi_feeder_scenario import solve


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
BATCH = ROOT / "output/all_voltage/osm_component_batch"
OUT = ROOT / "output/all_voltage/osm_small_component_validated_scenario"


def source_forest(net: pp.pandapowerNet):
    mv_buses = set(int(i) for i in net.bus.index[net.bus.vn_kv > 1.0])
    sources = sorted(set(int(i) for i in net.ext_grid.bus if int(i) in mv_buses))
    adjacency = {bus: [] for bus in mv_buses}
    mv_lines = set()
    for index, line in net.line.sort_index().iterrows():
        left, right = int(line.from_bus), int(line.to_bus)
        if left not in mv_buses or right not in mv_buses:
            continue
        adjacency[left].append((right, float(line.length_km), int(index), str(line["name"])))
        adjacency[right].append((left, float(line.length_km), int(index), str(line["name"])))
        mv_lines.add(int(index))
    distance = {bus: math.inf for bus in mv_buses}
    owner = {bus: None for bus in mv_buses}
    parent = {}
    queue = []
    for source in sources:
        distance[source] = 0.0
        owner[source] = source
        heapq.heappush(queue, (0.0, source, source))
    while queue:
        current, source, bus = heapq.heappop(queue)
        if current > distance[bus] + 1e-12 or owner[bus] != source:
            continue
        for neighbour, length, line_index, name in sorted(adjacency[bus]):
            candidate = current + length
            old = parent.get(neighbour)
            tie = abs(candidate - distance[neighbour]) <= 1e-12 and (
                owner[neighbour] is None or source < owner[neighbour]
                or (source == owner[neighbour] and old is not None
                    and (name, line_index, bus) < (str(net.line.at[old[1], "name"]), old[1], old[0]))
            )
            if candidate < distance[neighbour] - 1e-12 or tie:
                distance[neighbour] = candidate
                owner[neighbour] = source
                parent[neighbour] = (bus, line_index)
                heapq.heappush(queue, (candidate, source, neighbour))
    if any(value is None for value in owner.values()):
        raise ValueError("Unreached MV buses in multi-source forest")
    tree_lines = {line_index for _upstream, line_index in parent.values()}
    children = defaultdict(list)
    for child, (upstream, _line) in parent.items():
        if owner[child] == owner[upstream]:
            children[upstream].append(child)
    for values in children.values():
        values.sort(key=lambda bus: (str(net.bus.at[bus, "name"]), bus))
    return sources, owner, parent, children, tree_lines, mv_lines


def forest_feeders(net: pp.pandapowerNet, target_mva: float):
    sources, owner, parent, children, tree_lines, mv_lines = source_forest(net)
    circuit_groups = defaultdict(set)
    load_group = {}
    group_mva = []
    group_number = 0
    for source in sources:
        order = {}
        stack = [source]
        while stack:
            bus = stack.pop()
            order[bus] = len(order)
            stack.extend(reversed(children.get(bus, [])))
        loads = []
        for load_index, load in net.load.iterrows():
            lv_bus = int(load.bus)
            trafos = net.trafo.index[net.trafo.lv_bus == lv_bus]
            if len(trafos) != 1:
                raise ValueError("PTD load-transformer cardinality failure")
            mv_bus = int(net.trafo.at[int(trafos[0]), "hv_bus"])
            if owner[mv_bus] != source:
                continue
            apparent = math.hypot(float(load.p_mw), float(load.q_mvar))
            loads.append((order[mv_bus], str(load["name"]), int(load_index), mv_bus, apparent))
        loads.sort()
        groups = []
        current = []
        total = 0.0
        for item in loads:
            if current and total + item[4] > target_mva:
                groups.append(current); current = []; total = 0.0
            current.append(item); total += item[4]
        if current:
            groups.append(current)
        for group in groups:
            group_number += 1
            group_mva.append(sum(item[4] for item in group))
            key = (source, group_number)
            for _order, _name, load_index, bus, _apparent in group:
                load_group[load_index] = group_number
                cursor = bus
                while cursor != source:
                    upstream, line_index = parent[cursor]
                    circuit_groups[line_index].add(key)
                    cursor = upstream
    counts = {index: max(1, len(groups)) for index, groups in circuit_groups.items()}
    return counts, load_group, group_number, group_mva, tree_lines, mv_lines


def apply_forest_design(base, target, conductor, r20):
    counts, groups, feeder_count, feeder_mva, tree_lines, mv_lines = forest_feeders(base, target)
    if feeder_count > mv.MAX_FEEDERS:
        return None
    net = copy.deepcopy(base)
    net.ext_grid["vm_pu"] = mv.SOURCE_VM_PU
    open_lines = mv_lines - tree_lines
    net.line.loc[sorted(open_lines), "in_service"] = False
    catalog_id, x_ohm, max_i = mv.CONDUCTORS[conductor]
    r75 = r20[catalog_id] * (1 + 0.00403 * 55)
    for index in tree_lines:
        circuits = counts.get(index, 1)
        if str(net.line.at[index, "type"]) == "ol":
            net.line.at[index, "r_ohm_per_km"] = r75
            net.line.at[index, "x_ohm_per_km"] = x_ohm
            net.line.at[index, "max_i_ka"] = max_i
            net.line.at[index, "r0_ohm_per_km"] = 3 * r75
            net.line.at[index, "x0_ohm_per_km"] = 3 * x_ohm
        net.line.at[index, "r_ohm_per_km"] /= circuits
        net.line.at[index, "x_ohm_per_km"] /= circuits
        net.line.at[index, "c_nf_per_km"] *= circuits
        net.line.at[index, "max_i_ka"] *= circuits
        net.line.at[index, "r0_ohm_per_km"] /= circuits
        net.line.at[index, "x0_ohm_per_km"] /= circuits
        net.line.at[index, "c0_nf_per_km"] *= circuits
    return net, counts, groups, feeder_count, feeder_mva, tree_lines, open_lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--batch", type=Path, default=BATCH)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--scope", choices=("all", "failed"), default="all")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    batch_report = json.loads((args.batch / "validation.json").read_text())
    baseline_failures = {row["component_id"] for row in batch_report["failures"]}
    if args.scope == "failed":
        components = sorted(baseline_failures)
        component_result = {component: "FAIL" for component in components}
    else:
        with (args.batch / "component_results.csv").open(newline="", encoding="utf-8") as handle:
            batch_rows = list(csv.DictReader(handle))
        components = [row["component_id"] for row in batch_rows]
        component_result = {row["component_id"]: row["result"] for row in batch_rows}
    standards = mv.load_transformer_standards(args.database)
    with duckdb.connect(str(args.database), read_only=True) as con:
        r20 = dict(con.execute("""SELECT catalog_id,r20_ohm_per_km
            FROM parameter.mv_overhead_conductor_catalog
            WHERE catalog_id IN ('136-AL1/22-ST1A','203-AL1/32-ST1A','264-AL1/62-ST1A')""").fetchall())

    networks = {}
    zone_ptds = []
    for component in components:
        zone_id = component.replace(":", "_")
        folder = args.batch / zone_id
        solved = component_result[component] in {"PASS", "PARTIAL"}
        path = folder / ("network.json" if solved else "unsolved_network.json")
        if not path.exists():
            raise FileNotFoundError(path)
        net = pp.from_json(str(path))
        networks[component] = path
        zone_ptds.extend((zone_id, str(name).split(":")[1]) for name in net.load.name)
    loads, peaks, load_details = mv.build_all_zone_synchronized_snapshot(args.database, zone_ptds)

    summaries = []
    lines = []
    feeders = []
    transformers = []
    fault_rows = []
    for component, path in networks.items():
        zone_id = component.replace(":", "_")
        base = pp.from_json(str(path))
        for index, name in base.load.name.items():
            code = str(name).split(":")[1]
            base.load.at[index, "p_mw"], base.load.at[index, "q_mvar"] = loads[(zone_id, code)]
        base.trafo["shift_degree"] = 0.0
        transformer_design = mv.apply_ptd_capacity_design(base, standards)
        selected = None
        for target in mv.TARGETS:
            for conductor in mv.CONDUCTORS:
                candidate = apply_forest_design(base, target, conductor, r20)
                if candidate is None:
                    continue
                net, counts, groups, feeder_count, feeder_mva, tree_lines, open_lines = candidate
                converged, min_v, max_line, max_trafo = solve(net)
                if converged and min_v >= 0.9 and max_line <= 100 and max_trafo <= 100:
                    selected = (net, counts, groups, feeder_count, feeder_mva,
                                tree_lines, open_lines, target, conductor,
                                min_v, max_line, max_trafo)
                    break
            if selected is not None:
                break
        if selected is None:
            raise RuntimeError(f"No bounded forest design passes for {component}")
        (net, counts, groups, feeder_count, feeder_mva, tree_lines, open_lines,
         target, conductor, min_v, max_line, max_trafo) = selected
        component_fault_errors = []
        for ext_index, ext in net.ext_grid.iterrows():
            source_bus = int(ext.bus)
            source_name = str(ext["name"])
            mv_kv = float(net.bus.at[source_bus, "vn_kv"])
            for case, column in (("max", "s_sc_max_mva"), ("min", "s_sc_min_mva")):
                reference = float(ext[column])
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore")
                    sc.calc_sc(net, case=case, fault="3ph", branch_results=False)
                calculated = math.sqrt(3) * mv_kv * float(net.res_bus_sc.at[source_bus, "ikss_ka"])
                error = 100 * (calculated - reference) / reference
                component_fault_errors.append(abs(error))
                fault_rows.append({
                    "component_id": component, "source_name": source_name,
                    "case": case, "public_fault_mva": reference,
                    "calculated_fault_mva": calculated, "relative_error_percent": error,
                })
        summaries.append({
            "component_id": component, "synchronized_peak_timestamp_utc": peaks[zone_id][0],
            "source_count": len(net.ext_grid), "ptd_count": len(net.load),
            "synchronized_load_mw": float(net.load.p_mw.sum()),
            "target_feeder_mva": target, "inferred_feeder_count": feeder_count,
            "selected_overhead_conductor_design": conductor,
            "candidate_open_line_parts": len(open_lines),
            "converged": bool(net.converged), "min_voltage_pu": min_v,
            "max_line_loading_percent": max_line,
            "max_transformer_loading_percent": max_trafo,
            "max_abs_fault_error_percent": max(component_fault_errors),
            "reinforced_ptd_transformers": sum(
                row["capacity_action"] != "PUBLIC_CAPACITY_RETAINED" for row in transformer_design
            ),
            "evidence_status": "PUBLIC_OSM_GEOMETRY_WITH_INFERRED_MULTI_SOURCE_RADIAL_FOREST_DESIGN",
        })
        for index, line in net.line.iterrows():
            if float(net.bus.at[int(line.from_bus), "vn_kv"]) <= 1.0:
                continue
            lines.append({
                "component_id": component, "line_index": int(index),
                "edge_part_id": str(line["name"]), "in_service": bool(line.in_service),
                "switching_role": "INFERRED_FEEDER_PATH" if int(index) in tree_lines else "INFERRED_NORMALLY_OPEN_POINT",
                "inferred_circuit_count": counts.get(int(index), 1),
                "selected_catalog_id": mv.CONDUCTORS[conductor][0] if int(index) in tree_lines and str(line["type"]) == "ol" else "RETAINED_BASE_PARAMETER",
                "evidence_status": "OSM_GEOMETRY_PUBLIC_SWITCH_AND_CIRCUIT_COUNT_INFERRED",
            })
        for load_index, group_number in groups.items():
            feeders.append({
                "component_id": component,
                "ptd_code": str(net.load.at[load_index, "name"]).split(":")[1],
                "inferred_feeder_number": group_number,
                "p_mw": float(net.load.at[load_index, "p_mw"]),
                "q_mvar": float(net.load.at[load_index, "q_mvar"]),
                "evidence_status": "MULTI_SOURCE_FOREST_CONTIGUOUS_LOAD_PACKING_INFERRED",
            })
        transformers.extend({"component_id": component, **row} for row in transformer_design)
        print(f"{component} PASS feeders={feeder_count} Vmin={min_v:.4f}", flush=True)

    outputs = (
        ("component_design.csv", summaries, "scenario.osm_small_component_validated_design"),
        ("component_line_state.csv", lines, "scenario.osm_small_component_validated_line_state"),
        ("component_ptd_feeder.csv", feeders, "scenario.osm_small_component_validated_ptd_feeder"),
        ("component_ptd_transformer_design.csv", transformers, "scenario.osm_small_component_ptd_transformer_design"),
        ("component_fault_validation.csv", fault_rows, "study.osm_small_component_fault_validation"),
    )
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS scenario")
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        for filename, data, table in outputs:
            path = args.output / filename
            with path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(data[0]))
                writer.writeheader(); writer.writerows(data)
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto(?,header=true)", [str(path)])
    checks = {
        "scope": args.scope,
        "selected_baseline_components": len(components),
        "selected_components_failed_in_baseline": sum(component in baseline_failures for component in components),
        "validated_design_components": len(summaries),
        "converged_and_within_limits": sum(
            row["converged"] and row["min_voltage_pu"] >= 0.9
            and row["max_line_loading_percent"] <= 100
            and row["max_transformer_loading_percent"] <= 100 for row in summaries
        ),
        "source_fault_cases": len(fault_rows),
        "source_fault_cases_within_10_percent": sum(abs(row["relative_error_percent"]) <= 10 for row in fault_rows),
        "line_state_rows": len(lines), "ptd_feeder_rows": len(feeders),
        "ptd_transformer_design_rows": len(transformers),
        "reinforced_ptd_transformers": sum(
            row["capacity_action"] != "PUBLIC_CAPACITY_RETAINED" for row in transformers
        ),
    }
    errors = []
    if checks["validated_design_components"] != checks["selected_baseline_components"]:
        errors.append("Component design coverage failure")
    if checks["converged_and_within_limits"] != checks["selected_baseline_components"]:
        errors.append("Component AC acceptance failure")
    if checks["source_fault_cases_within_10_percent"] != checks["source_fault_cases"]:
        errors.append("Source fault acceptance failure")
    report = {
        "result": "PASS" if not errors else "FAIL", "checks": checks,
        "rules": {
            "load": "topology-consistent synchronized common-time component peak",
            "topology": "multi-source shortest-path radial forest; non-tree edges are candidate normally-open points",
            "feeder_and_conductor": "same bounded public-standard search as the 146 large source zones",
            "ptd_capacity": "retain public capacity or smallest public standard reinforcement meeting 80% target utilization",
        },
        "limitations": [
            "Feeder identity, open points, circuit count and installed conductor remain inferred planning values.",
            "The scenario closes numerical and electrical constraints; it does not assert operator switch states.",
        ],
        "errors": errors,
    }
    (args.output / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
