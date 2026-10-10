#!/usr/bin/env python3
"""Build and validate the final inferred MV operating-design layer.

The layer keeps public OSM geometry, public station short-circuit boundaries,
public PTD capacities and synchronized PTD loads.  Missing feeder identities,
normally-open points, circuit multiplicity and installed conductor identity are
filled by a deterministic planning rule and remain explicitly labelled as
inferred scenario data.
"""

from __future__ import annotations

import argparse
import copy
import csv
import json
import math
import sys
import warnings
from pathlib import Path

import duckdb
import pandapower as pp
import pandapower.shortcircuit as sc

from design_mv_multi_feeder_scenario import feeder_design, load_snapshot, solve, source_tree


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
BATCH = ROOT / "output/all_voltage/mv_zone_batch"
LOADS = ROOT / "output/all_voltage/mv_zone_topology_consistent_peak/zone_synchronized_peak_ptd_loads.csv"
OUT = ROOT / "output/all_voltage/mv_validated_operating_scenario"

TARGETS = (4.0, 3.0, 2.0, 1.5, 1.0)
CONDUCTORS = {
    "ACSR_160": ("136-AL1/22-ST1A", 0.320, 0.390),
    "ACSR_235": ("203-AL1/32-ST1A", 0.300, 0.510),
    "ACSR_325": ("264-AL1/62-ST1A", 0.290, 0.650),
}
SOURCE_VM_PU = 1.05
MAX_FEEDERS = 32
PTD_TARGET_UTILIZATION = 0.80


def build_all_zone_synchronized_snapshot(
    database: Path,
    zone_ptds: list[tuple[str, str]],
) -> tuple[
    dict[tuple[str, str], tuple[float, float]],
    dict[str, tuple[object, float, int, int]],
    list[dict[str, object]],
]:
    """Return one topology-consistent common-time peak for every zone."""
    with duckdb.connect(str(database), read_only=True) as con:
        con.execute("CREATE TEMP TABLE selected_zone_ptd(zone_id VARCHAR,ptd_code VARCHAR)")
        con.executemany("INSERT INTO selected_zone_ptd VALUES (?,?)", zone_ptds)
        con.execute("""CREATE TEMP TABLE selected_zone_profile_weight AS
            SELECT z.zone_id,a.scenario_profile_station_code AS profile_station_code,
                   sum(CASE WHEN a.scenario_lv_share_mode='STATION_TIME_VARYING' THEN a.scenario_factor ELSE 0 END) AS weight_tv,
                   sum(CASE WHEN a.scenario_lv_share_mode='STATION_TIME_VARYING' THEN 0 ELSE a.scenario_factor*a.scenario_station_lv_fraction END) AS weight
            FROM selected_zone_ptd z
            JOIN scenario.osm_zone_ptd_assignment a USING(ptd_code)
            GROUP BY 1,2""")
        con.execute("""CREATE TEMP TABLE selected_zone_peak AS
            SELECT zone_id,timestamp_utc,p_mw,profile_count,public_profile_count
            FROM (
              SELECT w.zone_id,s.timestamp_utc,sum((w.weight_tv*s.lv_share+w.weight)*s.p_mw) AS p_mw,
                     count(*) AS profile_count,
                     count(*) FILTER (WHERE s.data_status='PUBLIC_EREDES_STATION_AGGREGATE') AS public_profile_count,
                     row_number() OVER (PARTITION BY w.zone_id
                       ORDER BY sum((w.weight_tv*s.lv_share+w.weight)*s.p_mw) DESC,s.timestamp_utc) AS rank
              FROM selected_zone_profile_weight w
              JOIN operating.station_15min_complete s
                ON s.station_code=w.profile_station_code
              GROUP BY 1,2
            ) WHERE rank=1""")
        peak_rows = con.execute(
            """SELECT zone_id,timestamp_utc,p_mw,profile_count,public_profile_count
               FROM selected_zone_peak"""
        ).fetchall()
        load_rows = con.execute("""
            SELECT z.zone_id,z.ptd_code,p.timestamp_utc,
                   s.p_mw*a.scenario_factor*(CASE WHEN a.scenario_lv_share_mode='STATION_TIME_VARYING' THEN s.lv_share ELSE a.scenario_station_lv_fraction END) AS p_mw,
                   s.p_mw*a.scenario_factor*(CASE WHEN a.scenario_lv_share_mode='STATION_TIME_VARYING' THEN s.lv_share ELSE a.scenario_station_lv_fraction END)*tan(acos(0.97)) AS q_mvar,
                   s.data_status,a.profile_status
            FROM selected_zone_ptd z
            JOIN scenario.osm_zone_ptd_assignment a USING(ptd_code)
            JOIN selected_zone_peak p USING(zone_id)
            JOIN operating.station_15min_complete s
              ON s.station_code=a.scenario_profile_station_code
             AND s.timestamp_utc=p.timestamp_utc
        """).fetchall()
    peaks = {
        row[0]: (row[1], float(row[2]), int(row[3]), int(row[4]))
        for row in peak_rows
    }
    loads = {(row[0], row[1]): (float(row[3]), float(row[4])) for row in load_rows}
    details = [
        {
            "zone_id": row[0], "ptd_code": row[1], "timestamp_utc": row[2],
            "p_mw": float(row[3]), "q_mvar": float(row[4]),
            "profile_data_status": row[5], "profile_status": row[6],
            "evidence_status": "SYNCHRONIZED_TOPOLOGY_CONSISTENT_STATION_TOTAL_SCENARIO",
        }
        for row in load_rows
    ]
    if len(peaks) != len({zone_id for zone_id, _code in zone_ptds}):
        raise RuntimeError("Synchronized peak does not cover every selected MV zone")
    if len(loads) != len(zone_ptds):
        raise RuntimeError("Synchronized peak does not cover every selected zone PTD")
    return loads, peaks, details


def load_transformer_standards(database: Path) -> dict[int, list[dict[str, float | str]]]:
    """Read one canonical row for each public standard transformer rating."""
    with duckdb.connect(str(database), read_only=True) as con:
        rows = con.execute("""
            SELECT hv_kv,standard_catalog_id,vk_percent,vkr_percent,
                   vk0_percent,vkr0_percent,max_load_loss_w,max_no_load_loss_w
            FROM (
              SELECT *,row_number() OVER (
                PARTITION BY hv_kv,standard_catalog_id ORDER BY ptd_code
              ) AS rank
              FROM parameter.ptd_transformer_parameters
              WHERE hv_kv IN (10,15,30)
                AND standard_catalog_id LIKE 'OIL:%'
            ) WHERE rank=1
        """).fetchall()
    result: dict[int, list[dict[str, float | str]]] = {10: [], 15: [], 30: []}
    seen = set()
    for hv, catalog_id, vk, vkr, vk0, vkr0, load_loss, no_load_loss in rows:
        rating_kva = int(str(catalog_id).split(":")[-1])
        key = (int(hv), rating_kva)
        if key in seen:
            continue
        seen.add(key)
        result[int(hv)].append({
            "catalog_id": str(catalog_id), "unit_mva": rating_kva / 1000.0,
            "vk_percent": float(vk), "vkr_percent": float(vkr),
            "vk0_percent": float(vk0), "vkr0_percent": float(vkr0),
            "max_load_loss_w": float(load_loss), "max_no_load_loss_w": float(no_load_loss),
        })
    for family in result:
        result[family].sort(key=lambda row: float(row["unit_mva"]))
        if not result[family]:
            raise RuntimeError(f"No public transformer standards for {family} kV")
    return result


def apply_ptd_capacity_design(
    net: pp.pandapowerNet,
    standards: dict[int, list[dict[str, float | str]]],
) -> list[dict[str, object]]:
    """Retain public capacity or choose the smallest repeated public rating."""
    rows = []
    for trafo_index, trafo in net.trafo.iterrows():
        lv_bus = int(trafo.lv_bus)
        load_indices = net.load.index[net.load.bus == lv_bus]
        if len(load_indices) != 1:
            raise ValueError(f"Transformer {trafo_index} does not have exactly one PTD load")
        load_index = int(load_indices[0])
        p_mw = float(net.load.at[load_index, "p_mw"])
        q_mvar = float(net.load.at[load_index, "q_mvar"])
        apparent_mva = math.hypot(p_mw, q_mvar)
        public_mva = float(trafo.sn_mva)
        required_mva = apparent_mva / PTD_TARGET_UTILIZATION
        selected = None
        unit_count = 1
        status = "PUBLIC_CAPACITY_RETAINED"
        if public_mva + 1e-12 < required_mva:
            family = 10 if float(trafo.vn_hv_kv) <= 10 else 15 if float(trafo.vn_hv_kv) < 20 else 30
            candidates = []
            for standard in standards[family]:
                unit_mva = float(standard["unit_mva"])
                count = max(1, math.ceil(required_mva / unit_mva - 1e-12))
                candidates.append((count, unit_mva * count, unit_mva, standard))
            unit_count, _total, _unit_mva, selected = min(candidates, key=lambda item: item[:3])
            design_mva = float(selected["unit_mva"]) * unit_count
            net.trafo.at[trafo_index, "sn_mva"] = design_mva
            net.trafo.at[trafo_index, "vk_percent"] = float(selected["vk_percent"])
            net.trafo.at[trafo_index, "vkr_percent"] = float(selected["vkr_percent"])
            if "vk0_percent" in net.trafo:
                net.trafo.at[trafo_index, "vk0_percent"] = float(selected["vk0_percent"])
            if "vkr0_percent" in net.trafo:
                net.trafo.at[trafo_index, "vkr0_percent"] = float(selected["vkr0_percent"])
            status = "INFERRED_CAPACITY_REINFORCEMENT_TO_PUBLIC_STANDARD"
        else:
            design_mva = public_mva
        code = str(trafo["name"]).split(":")[1]
        rows.append({
            "ptd_code": code,
            "public_capacity_mva": public_mva,
            "synchronized_apparent_load_mva": apparent_mva,
            "target_utilization": PTD_TARGET_UTILIZATION,
            "required_design_capacity_mva": required_mva,
            "design_capacity_mva": design_mva,
            "design_unit_count": unit_count,
            "design_standard_catalog_id": (
                str(selected["catalog_id"]) if selected is not None else "PUBLIC_CAPACITY_RETAINED"
            ),
            "capacity_action": status,
            "evidence_status": (
                "PUBLIC_EREDES_CAPACITY" if selected is None
                else "PUBLIC_STANDARD_RATING_WITH_INFERRED_PLANNING_REINFORCEMENT"
            ),
        })
    return rows


def apply_design(
    base: pp.pandapowerNet,
    target_mva: float,
    conductor: str,
    topology: str,
    r20: dict[str, float],
) -> tuple[pp.pandapowerNet, dict[int, int], dict[int, int], int, list[float], set[int], set[int]] | None:
    # The imported helper exposes its cap as a module global; reject here using
    # the final scenario cap so this builder is independent of that diagnostic.
    import design_mv_multi_feeder_scenario as helper

    old_cap = helper.MAX_FEEDERS
    helper.MAX_FEEDERS = MAX_FEEDERS
    try:
        counts, load_groups, feeder_count, feeder_mva = feeder_design(base, target_mva)
    finally:
        helper.MAX_FEEDERS = old_cap
    if not counts or feeder_count > MAX_FEEDERS:
        return None
    _source, parent, _children = source_tree(base)
    tree_lines = {line_index for _upstream, line_index in parent.values()}
    mv_lines = {
        int(index)
        for index, line in base.line.iterrows()
        if float(base.bus.at[int(line.from_bus), "vn_kv"]) > 1.0
        and float(base.bus.at[int(line.to_bus), "vn_kv"]) > 1.0
    }
    open_lines = mv_lines - tree_lines if topology == "RADIAL_TREE" else set()
    net = copy.deepcopy(base)
    net.ext_grid["vm_pu"] = SOURCE_VM_PU
    if open_lines:
        net.line.loc[sorted(open_lines), "in_service"] = False
    catalog_id, x_ohm, max_i = CONDUCTORS[conductor]
    r75 = r20[catalog_id] * (1 + 0.00403 * (75 - 20))
    for line_index in tree_lines:
        circuits = counts.get(line_index, 1)
        if str(net.line.at[line_index, "type"]) == "ol":
            net.line.at[line_index, "r_ohm_per_km"] = r75
            net.line.at[line_index, "x_ohm_per_km"] = x_ohm
            net.line.at[line_index, "max_i_ka"] = max_i
            net.line.at[line_index, "r0_ohm_per_km"] = 3 * r75
            net.line.at[line_index, "x0_ohm_per_km"] = 3 * x_ohm
        net.line.at[line_index, "r_ohm_per_km"] /= circuits
        net.line.at[line_index, "x_ohm_per_km"] /= circuits
        net.line.at[line_index, "c_nf_per_km"] *= circuits
        net.line.at[line_index, "max_i_ka"] *= circuits
        if "r0_ohm_per_km" in net.line:
            net.line.at[line_index, "r0_ohm_per_km"] /= circuits
        if "x0_ohm_per_km" in net.line:
            net.line.at[line_index, "x0_ohm_per_km"] /= circuits
        if "c0_nf_per_km" in net.line:
            net.line.at[line_index, "c0_nf_per_km"] *= circuits
    return net, counts, load_groups, feeder_count, feeder_mva, tree_lines, open_lines


def fault_errors(net: pp.pandapowerNet) -> tuple[float, float, float, float]:
    if len(net.ext_grid) != 1:
        raise ValueError("Final station zone must have one public source boundary")
    source_bus = int(net.ext_grid.bus.iloc[0])
    mv_kv = float(net.bus.at[source_bus, "vn_kv"])
    values = []
    for case, column in (("max", "s_sc_max_mva"), ("min", "s_sc_min_mva")):
        reference = float(net.ext_grid.iloc[0][column])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sc.calc_sc(net, case=case, fault="3ph", branch_results=False)
        calculated = math.sqrt(3) * mv_kv * float(net.res_bus_sc.at[source_bus, "ikss_ka"])
        values.extend((calculated, 100 * (calculated - reference) / reference))
    return values[0], values[1], values[2], values[3]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--batch", type=Path, default=BATCH)
    parser.add_argument("--loads", type=Path, default=LOADS)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--scope", choices=("all", "stressed"), default="all")
    parser.add_argument("--save-networks", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.save_networks:
        (args.output / "networks").mkdir(exist_ok=True)

    batch_report = json.loads((args.batch / "validation.json").read_text())
    if args.scope == "stressed":
        zone_records = [
            (row["station_code"], row["component_id"], "unsolved_network.json")
            for row in batch_report["failures"]
        ]
    else:
        with (args.batch / "zone_results.csv").open(newline="", encoding="utf-8") as handle:
            batch_rows = list(csv.DictReader(handle))
        zone_records = []
        for row in batch_rows:
            station, component = row["station_code"], row["component_id"]
            zone_id = f"{station}__{component.replace(':', '_')}"
            folder = args.batch / zone_id
            filename = "network.json" if (folder / "network.json").exists() else "unsolved_network.json"
            if not (folder / filename).exists():
                raise FileNotFoundError(folder / filename)
            zone_records.append((station, component, filename))
    zones = [(station, component) for station, component, _filename in zone_records]

    network_paths: dict[str, Path] = {}
    zone_ptds: list[tuple[str, str]] = []
    for station, component, filename in zone_records:
        zone_id = f"{station}__{component.replace(':', '_')}"
        path = args.batch / zone_id / filename
        net = pp.from_json(str(path))
        codes = [str(name).split(":")[1] for name in net.load.name]
        if len(codes) != len(set(codes)):
            raise ValueError(f"Duplicate PTD load names in {zone_id}")
        network_paths[zone_id] = path
        zone_ptds.extend((zone_id, code) for code in codes)
    if args.scope == "all":
        snapshots, peak_meta, synchronized_detail_rows = build_all_zone_synchronized_snapshot(
            args.database, zone_ptds
        )
    else:
        snapshots = load_snapshot(args.loads)
        peak_meta = {}
        synchronized_detail_rows = []
    with duckdb.connect(str(args.database), read_only=True) as con:
        r20 = dict(
            con.execute(
                """SELECT catalog_id,r20_ohm_per_km
                   FROM parameter.mv_overhead_conductor_catalog
                   WHERE catalog_id IN ('136-AL1/22-ST1A','203-AL1/32-ST1A','264-AL1/62-ST1A')"""
            ).fetchall()
        )
    if len(r20) != len(CONDUCTORS):
        raise RuntimeError("Required public E-REDES overhead conductor standards are missing")
    transformer_standards = load_transformer_standards(args.database)

    summary_rows: list[dict[str, object]] = []
    line_rows: list[dict[str, object]] = []
    load_rows: list[dict[str, object]] = []
    transformer_rows: list[dict[str, object]] = []
    for position, (station, component) in enumerate(zones, start=1):
        zone_id = f"{station}__{component.replace(':', '_')}"
        path = network_paths[zone_id]
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
        zone_transformers = apply_ptd_capacity_design(base, transformer_standards)

        selected = None
        # Prefer an inferred radial operating state.  A meshed fallback is used
        # only when every bounded radial design fails the AC constraints.
        for topology in ("RADIAL_TREE", "OSM_MESH_RETAINED"):
            for target in TARGETS:
                for conductor in CONDUCTORS:
                    candidate = apply_design(base, target, conductor, topology, r20)
                    if candidate is None:
                        continue
                    net, counts, groups, feeder_count, feeder_mva, tree_lines, open_lines = candidate
                    converged, min_v, max_line, max_trafo = solve(net)
                    within = bool(
                        converged and min_v is not None and min_v >= 0.9
                        and max_line is not None and max_line <= 100
                        and max_trafo is not None and max_trafo <= 100
                    )
                    if within:
                        selected = (
                            net, counts, groups, feeder_count, feeder_mva,
                            tree_lines, open_lines, topology, target, conductor,
                            min_v, max_line, max_trafo,
                        )
                        break
                if selected is not None:
                    break
            if selected is not None:
                break
        if selected is None:
            raise RuntimeError(f"No bounded MV design meets constraints for {zone_id}")

        (
            net, counts, groups, feeder_count, feeder_mva, tree_lines,
            open_lines, topology, target, conductor, min_v, max_line, max_trafo,
        ) = selected
        calc_max, error_max, calc_min, error_min = fault_errors(net)
        source = net.ext_grid.iloc[0]
        summary_rows.append({
            "zone_id": zone_id,
            "station_code": station,
            "component_id": component,
            "synchronized_peak_timestamp_utc": peak_meta.get(zone_id, (None,))[0],
            "topology_scenario": topology,
            "source_voltage_setpoint_pu": SOURCE_VM_PU,
            "target_feeder_mva": target,
            "inferred_feeder_count": feeder_count,
            "selected_overhead_conductor_design": conductor,
            "selected_overhead_catalog_id": CONDUCTORS[conductor][0],
            "ptd_count": len(net.load),
            "synchronized_load_mw": float(net.load.p_mw.sum()),
            "synchronized_load_mvar": float(net.load.q_mvar.sum()),
            "candidate_open_line_parts": len(open_lines),
            "maximum_corridor_circuit_count": max(counts.values(), default=1),
            "feeder_mva_min": min(feeder_mva),
            "feeder_mva_max": max(feeder_mva),
            "converged": bool(net.converged),
            "min_voltage_pu": min_v,
            "max_line_loading_percent": max_line,
            "max_transformer_loading_percent": max_trafo,
            "reinforced_ptd_transformers": sum(
                row["capacity_action"] != "PUBLIC_CAPACITY_RETAINED" for row in zone_transformers
            ),
            "within_operating_design_limits": True,
            "public_fault_max_mva": float(source.s_sc_max_mva),
            "calculated_fault_max_mva": calc_max,
            "fault_max_error_percent": error_max,
            "public_fault_min_mva": float(source.s_sc_min_mva),
            "calculated_fault_min_mva": calc_min,
            "fault_min_error_percent": error_min,
            "topology_evidence_status": "OSM_GEOMETRY_WITH_INFERRED_FEEDER_IDENTITIES_AND_SWITCH_STATE",
            "parameter_evidence_status": "PUBLIC_EREDES_STANDARD_WITH_INFERRED_INSTALLED_CONDUCTOR_AND_CIRCUIT_COUNT",
            "load_evidence_status": "SYNCHRONIZED_TOPOLOGY_CONSISTENT_STATION_TOTAL_SCENARIO",
        })
        catalog_id = CONDUCTORS[conductor][0]
        for line_index, line in net.line.iterrows():
            left_mv = float(net.bus.at[int(line.from_bus), "vn_kv"]) > 1.0
            right_mv = float(net.bus.at[int(line.to_bus), "vn_kv"]) > 1.0
            if not (left_mv and right_mv):
                continue
            is_tree = int(line_index) in tree_lines
            circuits = counts.get(int(line_index), 1) if is_tree else 1
            line_rows.append({
                "zone_id": zone_id,
                "station_code": station,
                "component_id": component,
                "line_index": int(line_index),
                "edge_part_id": str(line["name"]),
                "in_service": bool(line.in_service),
                "switching_role": (
                    "INFERRED_NORMALLY_OPEN_POINT" if int(line_index) in open_lines
                    else "INFERRED_FEEDER_PATH" if is_tree else "RETAINED_OSM_MESH_EDGE"
                ),
                "inferred_circuit_count": circuits,
                "geometric_length_km": float(line.length_km),
                "electrical_circuit_km": float(line.length_km) * circuits,
                "selected_catalog_id": catalog_id if is_tree and str(line["type"]) == "ol" else "RETAINED_BASE_PARAMETER",
                "equivalent_r_ohm_per_km": float(line.r_ohm_per_km),
                "equivalent_x_ohm_per_km": float(line.x_ohm_per_km),
                "equivalent_c_nf_per_km": float(line.c_nf_per_km),
                "equivalent_max_i_ka": float(line.max_i_ka),
                "evidence_status": "OSM_GEOMETRY_PUBLIC_SWITCH_AND_CIRCUIT_COUNT_INFERRED",
            })
        for load_index, group_id in groups.items():
            code = str(net.load.at[load_index, "name"]).split(":")[1]
            load_rows.append({
                "zone_id": zone_id,
                "station_code": station,
                "component_id": component,
                "ptd_code": code,
                "inferred_feeder_number": group_id,
                "p_mw": float(net.load.at[load_index, "p_mw"]),
                "q_mvar": float(net.load.at[load_index, "q_mvar"]),
                "evidence_status": "SOURCE_TREE_CONTIGUOUS_SYNCHRONIZED_LOAD_PACKING_INFERRED",
            })
        for row in zone_transformers:
            transformer_rows.append({
                "zone_id": zone_id, "station_code": station, "component_id": component, **row
            })
        if args.save_networks:
            pp.to_json(net, str(args.output / "networks" / f"{zone_id}.json"))
        print(
            f"{position}/{len(zones)} {zone_id} {topology} target={target} "
            f"conductor={conductor} Vmin={min_v:.4f}",
            flush=True,
        )

    outputs = (
        ("zone_operating_design.csv", summary_rows, "scenario.mv_validated_zone_design"),
        ("zone_operating_line_state.csv", line_rows, "scenario.mv_validated_line_state"),
        ("zone_operating_ptd_feeder.csv", load_rows, "scenario.mv_validated_ptd_feeder"),
        ("zone_operating_ptd_transformer_design.csv", transformer_rows, "scenario.mv_validated_ptd_transformer_design"),
    )
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS scenario")
        for filename, data, table in outputs:
            csv_path = args.output / filename
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(data[0]))
                writer.writeheader(); writer.writerows(data)
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_csv_auto(?,header=true)", [str(csv_path)])
        if synchronized_detail_rows:
            detail_path = args.output / "zone_synchronized_peak_ptd_load.csv"
            with detail_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=list(synchronized_detail_rows[0]))
                writer.writeheader(); writer.writerows(synchronized_detail_rows)
            con.execute("""CREATE OR REPLACE TABLE scenario.mv_validated_zone_peak_ptd_load AS
                SELECT * FROM read_csv_auto(?,header=true)""", [str(detail_path)])

    checks = {
        "scope": args.scope,
        "candidate_zones": len(zones),
        "validated_zone_designs": len(summary_rows),
        "converged_zones": sum(bool(row["converged"]) for row in summary_rows),
        "zones_within_voltage_line_transformer_limits": sum(bool(row["within_operating_design_limits"]) for row in summary_rows),
        "zones_with_fault_max_abs_error_within_10_percent": sum(abs(float(row["fault_max_error_percent"])) <= 10 for row in summary_rows),
        "zones_with_fault_min_abs_error_within_10_percent": sum(abs(float(row["fault_min_error_percent"])) <= 10 for row in summary_rows),
        "radial_zone_designs": sum(row["topology_scenario"] == "RADIAL_TREE" for row in summary_rows),
        "meshed_fallback_zone_designs": sum(row["topology_scenario"] == "OSM_MESH_RETAINED" for row in summary_rows),
        "line_state_rows": len(line_rows),
        "ptd_feeder_assignment_rows": len(load_rows),
        "ptd_transformer_design_rows": len(transformer_rows),
        "reinforced_ptd_transformers": sum(
            row["capacity_action"] != "PUBLIC_CAPACITY_RETAINED" for row in transformer_rows
        ),
        "duplicate_zone_line_keys": len(line_rows) - len({(row["zone_id"], row["line_index"]) for row in line_rows}),
        "duplicate_zone_ptd_keys": len(load_rows) - len({(row["zone_id"], row["ptd_code"]) for row in load_rows}),
        "minimum_voltage_pu": min(float(row["min_voltage_pu"]) for row in summary_rows),
        "maximum_line_loading_percent": max(float(row["max_line_loading_percent"]) for row in summary_rows),
        "maximum_transformer_loading_percent": max(float(row["max_transformer_loading_percent"]) for row in summary_rows),
        "maximum_abs_fault_error_percent": max(
            max(abs(float(row["fault_max_error_percent"])), abs(float(row["fault_min_error_percent"])))
            for row in summary_rows
        ),
    }
    errors = []
    for key in (
        "converged_zones", "zones_within_voltage_line_transformer_limits",
        "zones_with_fault_max_abs_error_within_10_percent",
        "zones_with_fault_min_abs_error_within_10_percent",
    ):
        if checks[key] != checks["candidate_zones"]:
            errors.append(f"{key}={checks[key]} expected {checks['candidate_zones']}")
    if checks["duplicate_zone_line_keys"] or checks["duplicate_zone_ptd_keys"]:
        errors.append("Scenario line or PTD keys are not unique")
    validation = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "design_rules": {
            "source_voltage_setpoint_pu": SOURCE_VM_PU,
            "target_feeder_mva_search_order": list(TARGETS),
            "maximum_feeders_per_zone": MAX_FEEDERS,
            "topology_search_order": ["source shortest-path radial tree", "retain OSM mesh only if no radial design passes"],
            "overhead_conductor_search_order": [
                f"{name}: public E-REDES {values[0]} R20 plus inferred X and ampacity"
                for name, values in CONDUCTORS.items()
            ],
            "circuit_equivalent": "R/N, X/N, N*C, N*Imax",
            "ptd_capacity_rule": "retain public capacity unless synchronized apparent load exceeds 80%; then select the smallest repeated public E-REDES standard rating meeting S/0.80",
            "acceptance": "AC convergence, Vmin >= 0.90 pu, line <= 100%, transformer <= 100%, source max/min fault errors <= 10%",
        },
        "evidence_policy": {
            "real": "OSM geometry, public station fault strengths, public PTD capacity, public conductor catalogue, synchronized public aggregate profiles",
            "inferred": "source zone, feeder identity, normally-open points, installed conductor assignment, circuit count, 1.05 pu source setpoint, and required PTD capacity reinforcement",
            "simulated": "zero-sequence ratios and the resulting candidate operating snapshot",
        },
        "limitations": [
            "This is a validated synthetic planning network, not an operator as-operated digital twin.",
            "The selected switching state and circuit identities are reproducible inferences because public feeder diagrams and switch states are unavailable.",
            "A meshed fallback is retained only where every bounded radial candidate fails; its actual switching state remains unknown.",
        ],
        "errors": errors,
    }
    (args.output / "validation.json").write_text(json.dumps(validation, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(validation, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
