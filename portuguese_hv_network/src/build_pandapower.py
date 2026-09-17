#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict

import networkx as nx
import pandas as pd
import pandapower as pp

from common import MODEL, POWER_FLOW, PROJECT, RAW, TABLES, ensure_dirs, point_in_geojson_geometry, read_json, utc_now, write_json


def read_optional(path, columns: list[str]) -> pd.DataFrame:
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=columns)
    return pd.read_csv(path, low_memory=False)


def topology_graph(buses: pd.DataFrame, lines: pd.DataFrame, transformers: pd.DataFrame) -> nx.Graph:
    graph = nx.Graph()
    graph.add_nodes_from(buses["bus_id"].astype(str))
    active_lines = lines[lines["in_service"].astype(str).str.lower().isin({"true", "1"})] if "in_service" in lines else lines
    graph.add_edges_from(active_lines[["from_bus", "to_bus"]].astype(str).itertuples(index=False, name=None))
    if not transformers.empty:
        graph.add_edges_from(transformers[["hv_bus", "lv_bus"]].astype(str).itertuples(index=False, name=None))
    return graph


def cross_border_boundary_buses(lines: pd.DataFrame) -> set[str]:
    audit_path = PROJECT / "outputs" / "validation" / "cross_border_circuit_audit.csv"
    if not audit_path.exists() or "osm_circuit_ids" not in lines:
        return set()
    audit = pd.read_csv(audit_path, low_memory=False)
    ids = set(audit.loc[audit["classification"] == "DOCUMENTED_OR_LIKELY_CROSS_BORDER", "circuit_id"].astype(int))
    candidates: set[str] = set()
    for circuit_id in ids:
        subset = lines[
            lines["osm_circuit_ids"].fillna("").astype(str).map(
                lambda value: circuit_id in {int(item) for item in re.findall(r"\d+", value)}
            )
        ]
        if subset.empty:
            continue
        graph = nx.Graph()
        graph.add_edges_from(subset[["from_bus", "to_bus"]].astype(str).itertuples(index=False, name=None))
        candidates.update(str(node) for node, degree in graph.degree if degree == 1)
    return candidates


def regularize_cross_border_boundary(
    net: pp.pandapowerNet, config: dict[str, object], preliminary_net_import_mw: float
) -> dict[str, object]:
    """Convert equal-angle multi-slack buses to one reference plus fixed equivalents."""
    circuits_by_bus = {
        str(row["bus_id"]): int(row.get("circuit_count", 1))
        for row in config.get("cross_border_interconnections", [])
    }
    records = []
    for index, row in net.ext_grid.iterrows():
        bus = int(row["bus"])
        bus_id = str(net.bus.loc[bus, "bus_id"])
        records.append({
            "index": int(index), "bus": bus, "bus_id": bus_id, "name": str(row["name"]),
            "weight": float(net.bus.loc[bus, "vn_kv"]) * circuits_by_bus.get(bus_id, 1),
        })
    if len(records) <= 1:
        return {"boundary_mode": "SINGLE_REFERENCE_ALREADY_PRESENT"}
    total_weight = sum(float(row["weight"]) for row in records)
    reference = max(records, key=lambda row: (float(row["weight"]), row["bus_id"]))
    for row in records:
        if row["index"] == reference["index"]:
            continue
        p_mw = preliminary_net_import_mw * float(row["weight"]) / total_weight
        index = pp.create_sgen(net, row["bus"], p_mw=p_mw, q_mvar=0.0,
                               name=f"BOUNDARY_EQ:{row['name']}", type="cross_border_aggregate_equivalent")
        net.sgen.loc[index, "source_status"] = "MODEL_DERIVED_TOTAL_CAPACITY_WEIGHTED_BOUNDARY_EQUIVALENT"
    net.ext_grid.drop(index=[row["index"] for row in records if row["index"] != reference["index"]], inplace=True)
    net.ext_grid.loc[reference["index"], "source_status"] = "SINGLE_ANGLE_REFERENCE_FOR_AGGREGATE_BOUNDARY_EQUIVALENT"
    return {
        "boundary_mode": "SINGLE_REFERENCE_PLUS_MODEL_DERIVED_CAPACITY_WEIGHTED_INJECTIONS",
        "boundary_reference_bus_id": reference["bus_id"],
        "preliminary_equal_angle_net_import_mw": preliminary_net_import_mw,
        "boundary_observation_used_as_input": False,
    }


def infer_discrete_tap_positions(net: pp.pandapowerNet, target_low: float, target_high: float, distributed_slack: bool = False) -> pd.DataFrame:
    """Apply a declared steady-state OLTC proxy and retain an audit ledger."""
    initial = net.trafo["tap_pos"].copy()
    for _ in range(16):
        changed = False
        for index, row in net.trafo.iterrows():
            if not bool(row.get("in_service", True)):
                continue
            lv_bus = int(row["lv_bus"])
            voltage = float(net.res_bus.loc[lv_bus, "vm_pu"])
            position = int(row["tap_pos"])
            minimum = int(row["tap_min"])
            maximum = int(row["tap_max"])
            # All generated transformers use an HV-side tap. A lower tap ratio
            # raises the secondary voltage; a higher ratio lowers it.
            if voltage < target_low and position > minimum:
                net.trafo.loc[index, "tap_pos"] = position - 1
                changed = True
            elif voltage > target_high and position < maximum:
                net.trafo.loc[index, "tap_pos"] = position + 1
                changed = True
        if not changed:
            break
        pp.runpp(net, algorithm="nr", init="results", calculate_voltage_angles=True, max_iteration=50, tolerance_mva=1e-6, enforce_q_lims=True, numba=False, distributed_slack=distributed_slack)
    ledger = net.trafo[["name", "hv_bus", "lv_bus", "tap_pos", "tap_min", "tap_max", "tap_step_percent"]].copy()
    ledger["initial_tap_pos"] = initial
    ledger["final_lv_vm_pu"] = ledger["lv_bus"].map(net.res_bus["vm_pu"])
    ledger["tap_position_status"] = "INFERRED_DISCRETE_VOLTAGE_CONTROL_NOT_OPERATOR_OBSERVED"
    return ledger


def build(run_power_flow: bool) -> tuple[pp.pandapowerNet, dict[str, object]]:
    config = read_json(PROJECT / "config" / "model_config.json")
    scenario_scaling = float(config.get("power_flow_scenario_scaling", 1.0))
    use_proxy_shunt = bool(config.get("power_flow_use_proxy_shunt_capacitance", True))
    buses = pd.read_csv(TABLES / "buses.csv", low_memory=False)
    lines = pd.read_csv(TABLES / "lines.csv", low_memory=False)
    transformers = read_optional(TABLES / "transformers_topology.csv", ["transformer_id", "hv_bus", "lv_bus"])
    loads = read_optional(TABLES / "loads.csv", ["load_id", "bus_id", "p_mw", "q_mvar"])
    generators = read_optional(TABLES / "generators.csv", ["generator_id", "bus_id", "p_mw", "q_mvar"])
    generation_residuals = read_optional(
        TABLES / "generation_unmapped_residuals.csv",
        ["generation_source", "ren_target_mw", "mapped_asset_input_mw", "unmapped_residual_mw", "residual_status"],
    )
    net = pp.create_empty_network(name=str(config["model_name"]), sn_mva=100.0, f_hz=50.0)
    bus_index: dict[str, int] = {}
    for row in buses.to_dict("records"):
        index = pp.create_bus(net, vn_kv=float(row["voltage_kv"]), name=str(row["bus_id"]), geodata=(float(row["lon"]), float(row["lat"])))
        bus_index[str(row["bus_id"])] = index
        for column in ("bus_id", "facility_id", "facility_name", "facility_code", "facility_type", "source", "source_status"):
            net.bus.loc[index, column] = row.get(column, "")
    for row in lines.to_dict("records"):
        index = pp.create_line_from_parameters(
            net, bus_index[str(row["from_bus"])], bus_index[str(row["to_bus"])], max(float(row["length_km"]), 0.001),
            float(row["r_ohm_per_km"]), float(row["x_ohm_per_km"]),
            float(row["c_nf_per_km"]) if use_proxy_shunt else 0.0, float(row["max_i_ka"]),
            name=str(row["line_id"]), parallel=int(row["parallel"]), df=float(row["df"]),
            in_service=str(row.get("in_service", True)).lower() in {"true", "1"},
        )
        for column in ("line_id", "source_line_id", "source", "source_status", "parameter_status", "asset_type", "voltage_kv", "switch_state_status"):
            net.line.loc[index, column] = row.get(column, "")
        net.line.loc[index, "source_c_nf_per_km"] = row.get("c_nf_per_km", "")
        net.line.loc[index, "shunt_capacitance_status"] = "VOLTAGE_CLASS_PROXY_ENABLED" if use_proxy_shunt else "PROXY_DISABLED_IN_DEMONSTRATION_CASE"
    for row in transformers.to_dict("records"):
        index = pp.create_transformer_from_parameters(
            net=net, hv_bus=bus_index[str(row["hv_bus"])], lv_bus=bus_index[str(row["lv_bus"])], sn_mva=float(row["sn_mva"]),
            vn_hv_kv=float(row["hv_kv"]), vn_lv_kv=float(row["lv_kv"]),
            vk_percent=float(row["vk_percent"]), vkr_percent=float(row["vkr_percent"]),
            pfe_kw=float(row["pfe_kw"]), i0_percent=float(row["i0_percent"]), shift_degree=float(row["shift_degree"]), name=str(row["transformer_id"]),
            tap_side=str(row["tap_side"]), tap_neutral=int(row["tap_neutral"]), tap_min=int(row["tap_min"]),
            tap_max=int(row["tap_max"]), tap_step_percent=float(row["tap_step_percent"]), tap_pos=int(row["tap_pos"]),
            parallel=int(row.get("parallel", 1)),
        )
        for column in (
            "transformer_id", "source_status", "source_id", "parameter_status",
            "asset_override_source_url", "asset_override_effective_date",
        ):
            net.trafo.loc[index, column] = row.get(column, "")
    scenario_loads = loads[
        loads["in_service_scenario"].astype(str).str.lower().isin({"true", "1"})
    ] if "in_service_scenario" in loads else loads
    for row in scenario_loads.to_dict("records"):
        index = pp.create_load(
            net, bus_index[str(row["bus_id"])], p_mw=float(row["p_mw"]), q_mvar=float(row["q_mvar"]),
            scaling=scenario_scaling, name=str(row["load_id"]),
        )
        net.load.loc[index, "source_status"] = row.get("source_status", "")
        net.load.loc[index, "reactive_power_status"] = row.get("reactive_power_status", "")
    enable_shunts = bool(config.get("power_flow_enable_substation_shunts", True))
    target_pf = float(config.get("power_flow_substation_target_pf", 0.98))
    target_tan = math.tan(math.acos(target_pf))
    if enable_shunts and not scenario_loads.empty:
        load_by_bus_p = scenario_loads.groupby("bus_id")["p_mw"].sum()
        load_by_bus_q = scenario_loads.groupby("bus_id")["q_mvar"].sum()
        for bus_id_str, bus_p in load_by_bus_p.items():
            if str(bus_id_str) in bus_index:
                bus_q = float(load_by_bus_q.get(bus_id_str, 0.0))
                q_target = bus_p * target_tan
                q_excess = max(0.0, bus_q - q_target)
                if q_excess > 0.1:
                    comp_mvar = min(q_excess, 15.0)
                    s_idx = pp.create_shunt(
                        net, bus_index[str(bus_id_str)], q_mvar=-comp_mvar, p_mw=0.0,
                        name=f"SHUNT:{bus_id_str}", in_service=True,
                    )
                    net.shunt.loc[s_idx, "source_status"] = "ERSE_TARGET_POWER_FACTOR_SUBSTATION_COMPENSATION"
    for reactor in config.get("configured_400kv_shunt_reactors", []):
        bus_id_str = str(reactor["bus_id"])
        if bus_id_str in bus_index:
            r_idx = pp.create_shunt(
                net, bus_index[bus_id_str], q_mvar=float(reactor["q_mvar"]), p_mw=0.0,
                name=f"REACTOR:{reactor['name']}", in_service=True,
            )
            net.shunt.loc[r_idx, "source_status"] = "CONFIGURED_400KV_SHUNT_REACTOR_PROXY"
    assigned_generators = generators[generators["bus_id"].fillna("").astype(str) != ""].copy() if not generators.empty else generators
    pv_mask = assigned_generators.get("voltage_control_mode", pd.Series(index=assigned_generators.index, dtype=str)).eq("PV_VOLTAGE_CONTROL_SCENARIO")
    pq_generators = assigned_generators[~pv_mask]
    pv_generators = assigned_generators[pv_mask]
    for row in pq_generators.to_dict("records"):
        index = pp.create_sgen(
            net, bus_index[str(row["bus_id"])], p_mw=float(row["p_mw"]), q_mvar=float(row["q_mvar"]),
            scaling=scenario_scaling, name=str(row["generator_id"]),
        )
        net.sgen.loc[index, "source_status"] = row.get("source_status", "")
        net.sgen.loc[index, "dispatch_status"] = row.get("dispatch_status", "")
        net.sgen.loc[index, "nameplate_mw"] = row.get("nameplate_mw", "")
    use_dist_slack = bool(config.get("power_flow_use_distributed_slack", False))
    q_fraction = float(config.get("power_flow_generator_q_limit_fraction", 0.5))
    for bus_id, group in pv_generators.groupby("bus_id"):
        p_mw = float(group["p_mw"].sum())
        nameplate_mw = float(group["nameplate_mw"].sum())
        slack_w = nameplate_mw if use_dist_slack and nameplate_mw >= 50.0 else 0.0
        index = pp.create_gen(
            net, bus_index[str(bus_id)], p_mw=p_mw, vm_pu=1.0,
            min_q_mvar=-q_fraction * nameplate_mw, max_q_mvar=q_fraction * nameplate_mw,
            scaling=scenario_scaling, name=f"PV_GROUP:{bus_id}",
            slack_weight=slack_w,
        )
        net.gen.loc[index, "source_status"] = "AGGREGATED_OSM_ASSETS_INFERRED_BUS"
        net.gen.loc[index, "dispatch_status"] = "SCENARIO_PV_VOLTAGE_CONTROL_WITH_ASSUMED_Q_LIMITS"
        net.gen.loc[index, "nameplate_mw"] = nameplate_mw
        net.gen.loc[index, "source_asset_count"] = len(group)
    residual_bus_id = str(config.get("unmapped_generation_residual_bus_id", "BUS:OSM:way:131715746:400"))
    if not generation_residuals.empty and float(generation_residuals["unmapped_residual_mw"].sum()) > 1e-9:
        if residual_bus_id not in bus_index:
            raise KeyError(f"Configured unmapped-generation residual bus does not exist: {residual_bus_id}")
        for residual in generation_residuals.to_dict("records"):
            residual_mw = float(residual.get("unmapped_residual_mw", 0.0))
            if residual_mw <= 1e-9:
                continue
            source_name = str(residual["generation_source"])
            index = pp.create_sgen(
                net,
                bus_index[residual_bus_id],
                p_mw=residual_mw,
                q_mvar=0.0,
                scaling=scenario_scaling,
                name=f"UNMAPPED_RESIDUAL:{source_name}",
                type="unmapped_generation_residual",
            )
            net.sgen.loc[index, "source_status"] = "UNMAPPED_NATIONAL_RESIDUAL_PROXY"
            net.sgen.loc[index, "dispatch_status"] = "REN_SOURCE_TOTAL_MINUS_MAPPED_NAMEPLATE_CAPACITY"
            net.sgen.loc[index, "nameplate_mw"] = float("nan")
            net.sgen.loc[index, "generation_source"] = source_name
    graph = topology_graph(buses, lines, transformers)
    main_component = max(nx.connected_components(graph), key=len)
    for b_idx in net.bus.index:
        bus_id_val = str(net.bus.loc[b_idx, "bus_id"])
        if bus_id_val not in main_component:
            net.bus.loc[b_idx, "in_service"] = False
    active_buses = set(scenario_loads["bus_id"].dropna().astype(str)) | set(assigned_generators["bus_id"].dropna().astype(str))
    evidenced_boundary_buses = cross_border_boundary_buses(lines)
    country_path = RAW / "reference" / "portugal_gisco_2024.geojson"
    country_geometry = read_json(country_path)["features"][0]["geometry"] if country_path.exists() else None
    boundary_rows: list[dict[str, object]] = []
    cb_interconnections = config.get("cross_border_interconnections", [])
    if cb_interconnections:
        for idx, interconn in enumerate(cb_interconnections):
            b_id = str(interconn["bus_id"])
            if b_id in bus_index:
                boundary_basis = "PHYSICAL_CROSS_BORDER_EXTERNAL_BUS"
                g_idx = pp.create_ext_grid(
                    net, bus_index[b_id], vm_pu=1.0, va_degree=0.0,
                    name=f"EXT_GRID:{interconn.get('name', b_id)}"
                )
                net.ext_grid.loc[g_idx, "source_status"] = boundary_basis
                boundary_rows.append({
                    "component_index": 0, "bus_id": b_id,
                    "voltage_kv": interconn.get("voltage_kv", 400),
                    "interconnector_name": interconn.get("name", ""),
                    "circuit_count": interconn.get("circuit_count", 1),
                    "snapshot_timestamp_utc": config.get("cross_border_snapshot", {}).get("timestamp_utc", ""),
                    "boundary_basis": boundary_basis,
                })
    else:
        for component_index, component in enumerate(nx.connected_components(graph)):
            if not component.intersection(active_buses):
                continue
            evidenced = component.intersection(evidenced_boundary_buses)
            choices = buses[buses["bus_id"].astype(str).isin(evidenced or component)].copy()
            if evidenced and country_geometry is not None:
                choices["outside_portugal"] = choices.apply(
                    lambda row: not point_in_geojson_geometry(float(row["lon"]), float(row["lat"]), country_geometry), axis=1
                )
                choices = choices.sort_values(["outside_portugal", "voltage_kv", "bus_id"], ascending=[False, False, True])
            else:
                choices = choices.sort_values(["voltage_kv", "bus_id"], ascending=[False, True])
            selected = choices.iloc[0]
            index = pp.create_ext_grid(net, bus_index[str(selected["bus_id"])], vm_pu=1.0, va_degree=0.0, name=f"SCENARIO_BOUNDARY:{component_index}")
            basis = "CROSS_BORDER_EXTERNAL_ENDPOINT" if evidenced else "HIGHEST_VOLTAGE_COMPONENT_FALLBACK"
            net.ext_grid.loc[index, "source_status"] = f"SCENARIO_BOUNDARY_{basis}"
            boundary_rows.append({"component_index": component_index, "bus_id": selected["bus_id"], "voltage_kv": selected["voltage_kv"], "component_bus_count": len(component), "boundary_basis": basis})
    pd.DataFrame(boundary_rows).to_csv(TABLES / "scenario_boundaries.csv", index=False)
    pp.to_json(net, MODEL / "portuguese_hv_candidate.json")
    summary: dict[str, object] = {
        "generated_at": utc_now(), "buses": len(net.bus), "lines": len(net.line), "transformers": len(net.trafo),
        "loads": len(net.load), "fixed_pq_generator_assets": len(net.sgen), "voltage_controlled_generator_buses": len(net.gen),
        "generator_assets_assigned": len(assigned_generators), "scenario_boundaries": len(net.ext_grid),
        "unmapped_generation_residual_rows": int(generation_residuals["unmapped_residual_mw"].gt(1e-9).sum()) if not generation_residuals.empty else 0,
        "unmapped_generation_residual_mw": float(generation_residuals["unmapped_residual_mw"].sum()) if not generation_residuals.empty else 0.0,
        "topological_components": 1 if net.bus.in_service.all() == False else nx.number_connected_components(graph),
        "power_flow_attempted": run_power_flow,
        "scenario_scaling": scenario_scaling,
        "line_shunt_capacitance_mode": "voltage-class proxy enabled" if use_proxy_shunt else "proxy retained in source table but disabled in solved demonstration case",
        "scenario_note": config.get("power_flow_scenario_note", ""),
    }
    if run_power_flow:
        try:
            pp.runpp(net, algorithm="nr", init="dc", calculate_voltage_angles=True, max_iteration=50, tolerance_mva=1e-6, enforce_q_lims=True, numba=False, distributed_slack=use_dist_slack)
            preliminary_net_import_mw = float(net.res_ext_grid.p_mw.sum())
            boundary_summary = regularize_cross_border_boundary(net, config, preliminary_net_import_mw)
            pp.runpp(net, algorithm="nr", init="results", calculate_voltage_angles=True, max_iteration=50, tolerance_mva=1e-6, enforce_q_lims=True, numba=False, distributed_slack=use_dist_slack)
            tap_ledger = pd.DataFrame()
            if bool(config.get("power_flow_infer_tap_positions", False)) and len(net.trafo):
                target_low, target_high = map(float, config.get("power_flow_tap_target_band_pu", [0.985, 1.015]))
                tap_ledger = infer_discrete_tap_positions(net, target_low, target_high, distributed_slack=use_dist_slack)
                tap_ledger.to_csv(POWER_FLOW / "inferred_transformer_tap_positions.csv", index=True)
            boundary_sgen_mask = net.sgen.get("type", pd.Series(index=net.sgen.index, dtype=object)).eq("cross_border_aggregate_equivalent")
            physical_sgen_mw = float(net.res_sgen.loc[~boundary_sgen_mask, "p_mw"].sum()) if len(net.res_sgen) else 0.0
            boundary_equivalent_mw = float(net.res_sgen.loc[boundary_sgen_mask, "p_mw"].sum()) if len(net.res_sgen) else 0.0
            summary.update(
                {
                    **boundary_summary,
                    "converged": bool(net.converged),
                    "vm_pu_min": float(net.res_bus.vm_pu.min()), "vm_pu_max": float(net.res_bus.vm_pu.max()),
                    "line_loading_percent_max": float(net.res_line.loading_percent.max()) if len(net.res_line) else 0.0,
                    "trafo_loading_percent_max": float(net.res_trafo.loading_percent.max()) if len(net.res_trafo) else 0.0,
                    "total_load_p_mw": float(net.res_load.p_mw.sum()),
                    "total_generation_p_mw": physical_sgen_mw + (float(net.res_gen.p_mw.sum()) if len(net.res_gen) else 0.0),
                    "total_generator_q_mvar": float(net.res_gen.q_mvar.sum()) if len(net.res_gen) else 0.0,
                    "total_ext_grid_p_mw": float(net.res_ext_grid.p_mw.sum()) + boundary_equivalent_mw,
                    "boundary_equivalent_fixed_injection_mw": boundary_equivalent_mw,
                    "losses_p_mw": float(net.res_line.pl_mw.sum()) + (float(net.res_trafo.pl_mw.sum()) if len(net.res_trafo) else 0.0),
                    "transformers_with_inferred_non_neutral_tap": int((net.trafo.tap_pos != net.trafo.tap_neutral).sum()),
                }
            )
            net.res_bus.to_csv(POWER_FLOW / "bus_results.csv", index=True)
            net.res_line.to_csv(POWER_FLOW / "line_results.csv", index=True)
            net.res_trafo.to_csv(POWER_FLOW / "transformer_results.csv", index=True)
            net.res_gen.to_csv(POWER_FLOW / "voltage_controlled_generator_results.csv", index=True)
            generator_operating_rows: list[dict[str, object]] = []
            pv_q_by_bus = {
                str(net.bus.loc[int(row.bus), "bus_id"]): float(net.res_gen.loc[index, "q_mvar"])
                for index, row in net.gen.iterrows()
            }
            pv_nameplate_by_bus = pv_generators.groupby("bus_id")["nameplate_mw"].sum().to_dict() if not pv_generators.empty else {}
            for row in assigned_generators.to_dict("records"):
                bus_id = str(row["bus_id"])
                if row.get("voltage_control_mode") == "PV_VOLTAGE_CONTROL_SCENARIO":
                    denominator = float(pv_nameplate_by_bus.get(bus_id, 0.0))
                    q_mvar = float(pv_q_by_bus.get(bus_id, 0.0)) * float(row["nameplate_mw"]) / denominator if denominator > 0 else 0.0
                    q_status = "SOLVED_BUS_Q_ALLOCATED_BY_ASSET_NAMEPLATE"
                else:
                    q_mvar = float(row.get("q_mvar", 0.0))
                    q_status = "FIXED_PQ_SCENARIO_ASSUMPTION"
                generator_operating_rows.append({
                    "generator_id": row["generator_id"], "source_id": row.get("source_id", ""),
                    "name": row.get("name", ""), "generation_source": row.get("generation_source", ""),
                    "bus_id": bus_id, "p_mw": float(row["p_mw"]) * scenario_scaling,
                    "q_mvar": q_mvar, "p_status": row.get("dispatch_status", ""),
                    "q_status": q_status, "operating_point_status": "PUBLIC_TOTAL_CALIBRATED_SCENARIO_NOT_UNIT_TELEMETRY",
                })
            for residual in generation_residuals.to_dict("records"):
                residual_mw = float(residual.get("unmapped_residual_mw", 0.0))
                if residual_mw <= 1e-9:
                    continue
                source_name = str(residual["generation_source"])
                generator_operating_rows.append({
                    "generator_id": f"UNMAPPED_RESIDUAL:{source_name}",
                    "source_id": "REN_SOURCE_TOTAL_RESIDUAL",
                    "name": f"Unmapped {source_name} residual",
                    "generation_source": source_name,
                    "bus_id": residual_bus_id,
                    "p_mw": residual_mw * scenario_scaling,
                    "q_mvar": 0.0,
                    "p_status": "UNMAPPED_NATIONAL_RESIDUAL_PROXY",
                    "q_status": "ZERO_REACTIVE_POWER_PROXY",
                    "operating_point_status": "AGGREGATE_RESIDUAL_NOT_A_PHYSICAL_GENERATOR",
                })
            pd.DataFrame(generator_operating_rows).to_csv(POWER_FLOW / "generator_operating_points.csv", index=False)
            pp.to_json(net, MODEL / "portuguese_hv_candidate_solved.json")
        except Exception as exc:
            summary.update({"converged": False, "solver_error": f"{type(exc).__name__}: {exc}"})
    write_json(POWER_FLOW / "power_flow_summary.json", summary)
    return net, summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-run", action="store_true")
    args = parser.parse_args()
    ensure_dirs()
    _, summary = build(not args.no_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
