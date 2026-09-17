#!/usr/bin/env python3
"""Export the full-scale solved case with explicit evidence grades."""
from __future__ import annotations

import json

import pandas as pd

from common import POWER_FLOW, PROJECT, TABLES, ensure_dirs, read_json, utc_now, write_json


def main() -> None:
    ensure_dirs()
    config = read_json(PROJECT / "config" / "model_config.json")
    summary = read_json(POWER_FLOW / "power_flow_summary.json")
    if not summary.get("converged") or float(summary.get("scenario_scaling", 0)) != 1.0:
        raise RuntimeError("Operating-case export requires a converged full-scale (1.0) solution")

    lines = pd.read_csv(TABLES / "lines.csv", low_memory=False)
    line_results = pd.read_csv(POWER_FLOW / "line_results.csv").drop(columns=["Unnamed: 0"], errors="ignore")
    line_output = pd.concat([lines.reset_index(drop=True), line_results.reset_index(drop=True)], axis=1)
    line_output["flow_result_status"] = "AC_POWER_FLOW_SOLUTION_NOT_MEASURED_TELEMETRY"
    line_output["thermal_limit_status"] = line_output["max_i_status"].map({
        "DIRECT_PDIRD_MINIMUM_SUMMER_NOMINAL_CURRENT": "SOURCE_BACKED_STATIC_SUMMER_NOMINAL_CURRENT",
        "DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT": "SOURCE_BACKED_STATIC_WINTER_NOMINAL_CURRENT",
        "DIRECT_PUBLIC_CORRIDOR_SUMMER_NOMINAL_CURRENT": "SOURCE_BACKED_STATIC_CORRIDOR_SUMMER_NOMINAL_CURRENT",
        "DIRECT_PUBLIC_CORRIDOR_WINTER_NOMINAL_CURRENT": "SOURCE_BACKED_STATIC_CORRIDOR_WINTER_NOMINAL_CURRENT",
        "ENGINEERING_PROXY": "VOLTAGE_CLASS_STATIC_RATING_PROXY",
    }).fillna("UNDOCUMENTED_STATIC_RATING_PROXY")
    line_output["overloaded_in_case"] = line_output["loading_percent"].fillna(0.0) > 100.0
    line_output.to_csv(POWER_FLOW / "line_operating_results.csv", index=False)

    transformers = pd.read_csv(TABLES / "transformers_topology.csv", low_memory=False)
    transformer_results = pd.read_csv(POWER_FLOW / "transformer_results.csv").drop(columns=["Unnamed: 0"], errors="ignore")
    transformer_output = pd.concat([transformers.reset_index(drop=True), transformer_results.reset_index(drop=True)], axis=1)
    taps_path = POWER_FLOW / "inferred_transformer_tap_positions.csv"
    if taps_path.exists():
        taps = pd.read_csv(taps_path).drop(columns=["Unnamed: 0"], errors="ignore")
        taps = taps.rename(columns={"name": "transformer_id", "tap_pos": "solved_tap_pos"})
        transformer_output = transformer_output.merge(
            taps[["transformer_id", "solved_tap_pos", "initial_tap_pos", "final_lv_vm_pu", "tap_position_status"]],
            on="transformer_id", how="left",
        )
    transformer_output["flow_result_status"] = "AC_POWER_FLOW_SOLUTION_NOT_MEASURED_TELEMETRY"
    transformer_output.to_csv(POWER_FLOW / "transformer_operating_results.csv", index=False)

    loads = pd.read_csv(TABLES / "loads.csv", low_memory=False)
    loads["operating_point_status"] = loads.apply(
        lambda row: "DIRECT_EREDES_P_WITH_POWER_FACTOR_DERIVED_Q" if row["observed_p_mw"] > 0
        else "REN_RESIDUAL_PTD_SPATIAL_ALLOCATION_WITH_POWER_FACTOR_DERIVED_Q", axis=1,
    )
    loads.to_csv(POWER_FLOW / "load_operating_points.csv", index=False)

    evidence_rows = [
        {"quantity": "line_r_ohm_per_km", "observed_or_source_backed_rows": int((lines.r_status != "ENGINEERING_PROXY").sum()), "estimated_rows": int((lines.r_status == "ENGINEERING_PROXY").sum()), "interpretation": "PDIRD conductor-derived where matched; otherwise voltage/asset-class proxy"},
        {"quantity": "line_x_ohm_per_km", "observed_or_source_backed_rows": 0, "estimated_rows": len(lines), "interpretation": "Engineering proxy; no equipment-level public reactance series"},
        {"quantity": "line_c_nf_per_km", "observed_or_source_backed_rows": 0, "estimated_rows": len(lines), "interpretation": "Engineering proxy; no equipment-level public capacitance series"},
        {"quantity": "line_thermal_limit", "observed_or_source_backed_rows": int(lines.max_i_status.isin(["DIRECT_PDIRD_MINIMUM_SUMMER_NOMINAL_CURRENT", "DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT", "DIRECT_PUBLIC_CORRIDOR_SUMMER_NOMINAL_CURRENT", "DIRECT_PUBLIC_CORRIDOR_WINTER_NOMINAL_CURRENT"]).sum()), "estimated_rows": int((~lines.max_i_status.isin(["DIRECT_PDIRD_MINIMUM_SUMMER_NOMINAL_CURRENT", "DIRECT_PDIRD_MINIMUM_WINTER_NOMINAL_CURRENT", "DIRECT_PUBLIC_CORRIDOR_SUMMER_NOMINAL_CURRENT", "DIRECT_PUBLIC_CORRIDOR_WINTER_NOMINAL_CURRENT"])).sum()), "interpretation": "Static seasonal nominal current where PDIRD/RARI-backed; not real-time dynamic line rating"},
        {"quantity": "line_service_state", "observed_or_source_backed_rows": int(lines.operational_status.isin(["Em exploração", "Desligado/Reserva"]).sum()), "estimated_rows": int((~lines.operational_status.isin(["Em exploração", "Desligado/Reserva"])).sum()), "interpretation": "E-REDES asset status or explicit steady-state assumption; not individual breaker telemetry"},
        {"quantity": "generator_p_mw", "observed_or_source_backed_rows": 0, "estimated_rows": int(summary["generator_assets_assigned"]), "interpretation": "REN source totals are observed; allocation to public assets is proportional to nameplate"},
        {"quantity": "generator_q_mvar", "observed_or_source_backed_rows": 0, "estimated_rows": int(summary["generator_assets_assigned"]), "interpretation": "Solved PV-bus response or fixed-PQ assumption; not unit telemetry"},
        {"quantity": "load_p_mw", "observed_or_source_backed_rows": int((loads.source_status == "DIRECT_EREDES_ACTIVE_POWER").sum()), "estimated_rows": int((loads.source_status == "REN_SYSTEM_RESIDUAL_PTD_SPATIAL_ALLOCATION").sum()), "interpretation": "E-REDES substation observations plus PTD-weighted residual to match REN consumption"},
        {"quantity": "load_q_mvar", "observed_or_source_backed_rows": 0, "estimated_rows": len(loads), "interpretation": f"Derived using fixed power factor {config['load_power_factor']}"},
        {"quantity": "transformer_tap_pos", "observed_or_source_backed_rows": 0, "estimated_rows": len(transformers), "interpretation": "Discrete voltage-control inference within declared tap range"},
        {"quantity": "line_p_q_flow", "observed_or_source_backed_rows": 0, "estimated_rows": int(line_output.loading_percent.notna().sum()), "interpretation": "Solved AC power-flow result; not measured branch telemetry"},
    ]
    evidence = pd.DataFrame(evidence_rows)
    evidence.to_csv(POWER_FLOW / "operating_quantity_evidence.csv", index=False)

    report = {
        "generated_at": utc_now(),
        "case": "FULL_SCALE_PUBLIC_DATA_INFORMED_HELDOUT_IMPORT",
        "timestamp_utc": config["calibration_timestamp_utc"],
        "converged": True,
        "load_p_mw": summary["total_load_p_mw"],
        "generation_p_mw": summary["total_generation_p_mw"],
        "external_balance_and_losses_mw": summary["total_ext_grid_p_mw"],
        "losses_p_mw": summary["losses_p_mw"],
        "voltage_range_pu": [summary["vm_pu_min"], summary["vm_pu_max"]],
        "maximum_line_loading_percent": summary["line_loading_percent_max"],
        "maximum_transformer_loading_percent": summary["trafo_loading_percent_max"],
        "overloaded_line_rows": int(line_output["overloaded_in_case"].sum()),
        "overloaded_transformer_rows": int((transformer_output.loading_percent > 100.0).sum()),
        "interpretation": "A converged, full-scale public-data-informed study case with observed net import held out. Constraint violations are model diagnostics, not claims about the operator's real-time network state.",
    }
    write_json(POWER_FLOW / "full_scale_operating_case.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
