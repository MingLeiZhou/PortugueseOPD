#!/usr/bin/env python3
from __future__ import annotations

import json

import networkx as nx
import pandas as pd

from common import POWER_FLOW, PROJECT, RAW, TABLES, VALIDATION, ensure_dirs, read_json, utc_now, write_json


def check(checks: list[dict[str, object]], name: str, passed: bool, measured: object, expected: object, severity: str = "ERROR") -> None:
    checks.append({"check": name, "status": "PASS" if passed else "FAIL", "severity": severity, "measured": measured, "expected": expected})


def main() -> None:
    ensure_dirs()
    config = read_json(PROJECT / "config" / "model_config.json")
    buses = pd.read_csv(TABLES / "buses.csv", low_memory=False)
    lines = pd.read_csv(TABLES / "lines.csv", low_memory=False)
    transformers = pd.read_csv(TABLES / "transformers_topology.csv", low_memory=False)
    loads = pd.read_csv(TABLES / "loads.csv", low_memory=False)
    generators = pd.read_csv(TABLES / "generators.csv", low_memory=False)
    bus_ids = set(buses["bus_id"].astype(str))
    checks: list[dict[str, object]] = []
    check(checks, "unique_bus_ids", buses["bus_id"].is_unique, buses["bus_id"].nunique(), len(buses))
    check(checks, "unique_line_ids", lines["line_id"].is_unique, lines["line_id"].nunique(), len(lines))
    line_endpoints = set(lines["from_bus"].astype(str)) | set(lines["to_bus"].astype(str))
    check(checks, "line_endpoints_exist", line_endpoints <= bus_ids, len(line_endpoints - bus_ids), 0)
    trafo_endpoints = set(transformers["hv_bus"].astype(str)) | set(transformers["lv_bus"].astype(str)) if not transformers.empty else set()
    check(checks, "transformer_endpoints_exist", trafo_endpoints <= bus_ids, len(trafo_endpoints - bus_ids), 0)
    check(checks, "no_line_self_loops", bool((lines["from_bus"] != lines["to_bus"]).all()), int((lines["from_bus"] == lines["to_bus"]).sum()), 0)
    check(checks, "positive_line_lengths", bool((lines["length_km"] > 0).all()), int((lines["length_km"] <= 0).sum()), 0)
    allowed = set(config["voltage_levels_kv"])
    check(checks, "declared_voltage_levels_only", set(buses["voltage_kv"].astype(int)) <= allowed, sorted(set(buses["voltage_kv"].astype(int))), sorted(allowed))
    bus_voltage = buses.set_index("bus_id")["voltage_kv"].astype(int).to_dict()
    mismatch = lines.apply(lambda row: bus_voltage[str(row["from_bus"])] != int(row["voltage_kv"]) or bus_voltage[str(row["to_bus"])] != int(row["voltage_kv"]), axis=1)
    check(checks, "line_bus_voltage_consistency", not bool(mismatch.any()), int(mismatch.sum()), 0)
    if not transformers.empty:
        mismatch_trafo = transformers.apply(lambda row: bus_voltage[str(row["hv_bus"])] != int(row["hv_kv"]) or bus_voltage[str(row["lv_bus"])] != int(row["lv_kv"]), axis=1)
        check(checks, "transformer_bus_voltage_consistency", not bool(mismatch_trafo.any()), int(mismatch_trafo.sum()), 0)
    check(checks, "load_buses_exist", set(loads["bus_id"].astype(str)) <= bus_ids, len(set(loads["bus_id"].astype(str)) - bus_ids), 0)
    assigned_gen = generators[generators["bus_id"].fillna("").astype(str) != ""]
    check(checks, "assigned_generator_buses_exist", set(assigned_gen["bus_id"].astype(str)) <= bus_ids, len(set(assigned_gen["bus_id"].astype(str)) - bus_ids), 0)
    negative_dispatch = assigned_gen["p_mw"].lt(-1e-9)
    capacity_violations = assigned_gen["p_mw"].gt(assigned_gen["nameplate_mw"].fillna(0.0) + 1e-8)
    check(checks, "generator_dispatch_nonnegative", not bool(negative_dispatch.any()), int(negative_dispatch.sum()), 0)
    check(checks, "generator_dispatch_at_most_nameplate", not bool(capacity_violations.any()), int(capacity_violations.sum()), 0)
    dispatch_audit_path = TABLES / "generation_dispatch_audit.csv"
    residual_path = TABLES / "generation_unmapped_residuals.csv"
    dispatch_audit = pd.read_csv(dispatch_audit_path) if dispatch_audit_path.exists() else pd.DataFrame()
    generation_residuals = pd.read_csv(residual_path) if residual_path.exists() else pd.DataFrame()
    audit_error = float(
        (dispatch_audit["ren_target_mw"] - dispatch_audit["mapped_asset_input_mw"] - dispatch_audit["unmapped_residual_mw"]).abs().max()
    ) if not dispatch_audit.empty else float("inf")
    check(checks, "generation_dispatch_audit_reconciles_ren_source_totals", audit_error <= 1e-6, audit_error, "<=1e-6 MW")
    expected_residuals = dispatch_audit.loc[dispatch_audit["unmapped_residual_mw"].gt(1e-9)] if not dispatch_audit.empty else dispatch_audit
    residuals_explicit = (
        len(generation_residuals) == len(expected_residuals)
        and (generation_residuals["residual_status"].eq("UNMAPPED_NATIONAL_RESIDUAL_PROXY").all() if len(generation_residuals) else True)
        and bool(config.get("unmapped_generation_residual_bus_id"))
    )
    check(checks, "unmapped_generation_residuals_explicit", residuals_explicit, len(generation_residuals), len(expected_residuals))
    graph = nx.Graph()
    graph.add_nodes_from(bus_ids)
    active_lines = lines[lines["in_service"].astype(str).str.lower().isin({"true", "1"})] if "in_service" in lines else lines
    graph.add_edges_from(active_lines[["from_bus", "to_bus"]].astype(str).itertuples(index=False, name=None))
    if not transformers.empty:
        graph.add_edges_from(transformers[["hv_bus", "lv_bus"]].astype(str).itertuples(index=False, name=None))
    components = list(nx.connected_components(graph))
    boundaries = pd.read_csv(TABLES / "scenario_boundaries.csv")
    scenario_loads = loads[
        loads["in_service_scenario"].astype(str).str.lower().isin({"true", "1"})
    ] if "in_service_scenario" in loads else loads
    active = set(scenario_loads["bus_id"].astype(str)) | set(assigned_gen["bus_id"].astype(str))
    active_components = sum(bool(component.intersection(active)) for component in components)
    has_multi_interconnectors = bool(config.get("cross_border_interconnections", []))
    expected_boundaries_desc = f">={active_components} (multi-interconnector enabled)" if has_multi_interconnectors else f"{active_components}"
    boundary_condition = (len(boundaries) >= active_components) if has_multi_interconnectors else (len(boundaries) == active_components)
    check(checks, "one_boundary_per_active_component", boundary_condition, len(boundaries), expected_boundaries_desc)
    cross_border_snapshot = config.get("cross_border_snapshot", {})
    if has_multi_interconnectors and cross_border_snapshot:
        expected_bus_count = int(cross_border_snapshot["external_boundary_bus_count"])
        expected_circuit_count = int(cross_border_snapshot["physical_circuit_count"])
        configured_bus_ids = [str(item["bus_id"]) for item in config["cross_border_interconnections"]]
        configured_circuits = sum(int(item.get("circuit_count", 1)) for item in config["cross_border_interconnections"])
        check(checks, "cross_border_external_bus_count", len(boundaries) == expected_bus_count, len(boundaries), expected_bus_count)
        check(checks, "cross_border_boundary_bus_ids_unique", len(configured_bus_ids) == len(set(configured_bus_ids)), len(set(configured_bus_ids)), len(configured_bus_ids))
        check(checks, "cross_border_physical_circuit_count", configured_circuits == expected_circuit_count, configured_circuits, expected_circuit_count)
        check(checks, "cross_border_boundary_bus_ids_exist", set(configured_bus_ids) <= bus_ids, len(set(configured_bus_ids) - bus_ids), 0)
        check(checks, "cross_border_transfer_capacity_not_backfilled", cross_border_snapshot.get("transfer_capacity_limits_mw") is None, cross_border_snapshot.get("transfer_capacity_limits_mw"), None)
    for override in config.get("scenario_line_status_overrides", []):
        selected = lines[
            lines["name"].fillna("").astype(str).eq(str(override["name"]))
            & lines["voltage_kv"].astype(int).eq(int(override["voltage_kv"]))
        ]
        expected_status = bool(override["in_service"])
        actual_ok = not selected.empty and bool((selected["in_service"].astype(str).str.lower().isin({"true", "1"}) == expected_status).all())
        check(checks, f"scenario_line_status_override_{override['name']}_{override['voltage_kv']}kv", actual_ok, f"{len(selected)} rows; in_service={sorted(selected['in_service'].astype(str).unique())}", expected_status)
    direct_line_fraction = float(lines["source_status"].isin(["DIRECT_EREDES", "DIRECT_OSM", "DIRECT_OSM_RELATION_CONTEXT"]).mean()) if len(lines) else 0.0
    proxy_parameter_fraction = float(lines["parameter_status"].astype(str).str.contains("PROXY|PARTIAL", regex=True).mean()) if len(lines) else 0.0
    check(checks, "line_geometry_source_label_complete", direct_line_fraction == 1.0, direct_line_fraction, 1.0)
    check(checks, "parameter_evidence_status_complete", bool(lines["parameter_status"].notna().all()), int(lines["parameter_status"].isna().sum()), 0)
    ren_reference = {int(key): float(value) for key, value in config.get("ren_2025_reference_line_km", {}).items()}
    for voltage, reference_km in sorted(ren_reference.items()):
        observed_km = float(lines.loc[lines["voltage_kv"] == voltage, "length_km"].sum())
        ratio = observed_km / reference_km if reference_km else 0.0
        check(
            checks, f"osm_{voltage}kv_length_vs_ren_2025_context", 0.75 <= ratio <= 1.25,
            f"{observed_km:.1f} km ({ratio:.1%})", f"75--125% of REN contextual total {reference_km:.1f} km", "WARNING",
        )
    power_flow = read_json(POWER_FLOW / "power_flow_summary.json") if (POWER_FLOW / "power_flow_summary.json").exists() else {}
    if power_flow.get("power_flow_attempted"):
        check(checks, "ac_power_flow_converged", bool(power_flow.get("converged")), power_flow.get("converged"), True)
        if power_flow.get("converged"):
            check(checks, "scenario_voltage_range_0.90_to_1.10_pu", float(power_flow["vm_pu_min"]) >= 0.90 and float(power_flow["vm_pu_max"]) <= 1.10, f"{power_flow['vm_pu_min']:.4f}--{power_flow['vm_pu_max']:.4f}", "0.90--1.10", "WARNING")
            check(checks, "scenario_line_loading_at_most_100_percent", float(power_flow["line_loading_percent_max"]) <= 100.0, power_flow["line_loading_percent_max"], "<=100", "WARNING")
            check(checks, "scenario_transformer_loading_at_most_100_percent", float(power_flow["trafo_loading_percent_max"]) <= 100.0, power_flow["trafo_loading_percent_max"], "<=100", "WARNING")
    check(checks, "active_component_boundary_count_review", len(boundaries) <= 10, len(boundaries), "<=10 desired for a nationally integrated scenario", "WARNING")
    frame = pd.DataFrame(checks)
    frame.to_csv(VALIDATION / "validation_checks.csv", index=False)
    errors_pass = bool((frame[frame["severity"] == "ERROR"]["status"] == "PASS").all())
    warnings_pass = bool((frame[frame["severity"] == "WARNING"]["status"] == "PASS").all())
    overall = "PASS" if errors_pass and warnings_pass else "STRUCTURAL_PASS_SCENARIO_WARN" if errors_pass else "FAIL"
    report = {
        "generated_at": utc_now(), "overall_status": overall,
        "checks_passed": int((frame["status"] == "PASS").sum()), "checks_total": len(frame),
        "network": {"buses": len(buses), "lines": len(lines), "transformers": len(transformers), "components": len(components), "active_components": active_components},
        "evidence": {
            "direct_line_geometry_fraction": direct_line_fraction, "proxy_or_partial_line_parameter_fraction": proxy_parameter_fraction,
            "line_parameter_status_counts": lines["parameter_status"].value_counts().to_dict(),
            "transformer_source_status_counts": transformers["source_status"].value_counts().to_dict() if not transformers.empty else {},
            "assigned_load_fraction": len(loads) / max(1, len(loads) + len(pd.read_csv(TABLES / "loads_unmatched.csv"))),
            "scenario_connected_load_fraction": len(scenario_loads) / max(1, len(loads)),
            "assigned_generation_fraction": len(assigned_gen) / max(1, len(generators)),
            "generator_capacity_violation_count": int(capacity_violations.sum()),
            "unmapped_generation_residual_mw": float(generation_residuals["unmapped_residual_mw"].sum()) if not generation_residuals.empty else 0.0,
        },
        "power_flow": power_flow,
    }
    write_json(VALIDATION / "validation_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
