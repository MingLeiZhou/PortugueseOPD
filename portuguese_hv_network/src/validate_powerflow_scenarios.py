#!/usr/bin/env python3
"""Run a transparent AC power-flow scaling sweep on the built candidate model."""
from __future__ import annotations

import argparse
import json

import pandapower as pp
import pandas as pd

from build_pandapower import infer_discrete_tap_positions, regularize_cross_border_boundary
from common import MODEL, POWER_FLOW, PROJECT, ensure_dirs, read_json, utc_now, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scales", default="0.50,0.75,1.00,1.10,1.20")
    args = parser.parse_args()
    ensure_dirs()
    model_path = MODEL / "portuguese_hv_candidate.json"
    config = read_json(PROJECT / "config" / "model_config.json")
    rows: list[dict[str, object]] = []
    for scale in [float(value) for value in args.scales.split(",")]:
        net = pp.from_json(model_path)
        if not net.load.empty:
            net.load["scaling"] = scale
        for table_name in ("sgen", "gen"):
            table = getattr(net, table_name)
            if table.empty:
                continue
            residual = table["name"].fillna("").astype(str).str.startswith("UNMAPPED_RESIDUAL:")
            physical = ~residual
            nameplate = pd.to_numeric(table.get("nameplate_mw"), errors="coerce")
            table.loc[physical, "p_mw"] = (table.loc[physical, "p_mw"] * scale).clip(
                upper=nameplate.loc[physical]
            )
            table.loc[residual, "p_mw"] = table.loc[residual, "p_mw"] * scale
            table["scaling"] = 1.0
        generator_capacity_violations = int(
            sum(
                (
                    ~getattr(net, table_name)["name"].fillna("").astype(str).str.startswith("UNMAPPED_RESIDUAL:")
                    & getattr(net, table_name)["p_mw"].gt(pd.to_numeric(getattr(net, table_name).get("nameplate_mw"), errors="coerce") + 1e-8)
                ).sum()
                for table_name in ("sgen", "gen") if not getattr(net, table_name).empty
            )
        )
        result: dict[str, object] = {"scaling": scale, "converged": False}
        try:
            pp.runpp(
                net, algorithm="nr", init="dc", calculate_voltage_angles=True,
                max_iteration=50, tolerance_mva=1e-6, enforce_q_lims=True, numba=False,
            )
            preliminary_net_import_mw = float(net.res_ext_grid.p_mw.sum())
            boundary_summary = regularize_cross_border_boundary(net, config, preliminary_net_import_mw)
            pp.runpp(net, algorithm="nr", init="results", calculate_voltage_angles=True,
                     max_iteration=50, tolerance_mva=1e-6, enforce_q_lims=True, numba=False)
            target_low, target_high = map(float, config.get("power_flow_tap_target_band_pu", [0.985, 1.015]))
            infer_discrete_tap_positions(net, target_low, target_high)
            boundary_sgen_mask = net.sgen.get("type", pd.Series(index=net.sgen.index, dtype=object)).eq("cross_border_aggregate_equivalent")
            result.update({
                **boundary_summary,
                "converged": bool(net.converged),
                "vm_pu_min": float(net.res_bus.vm_pu.min()), "vm_pu_max": float(net.res_bus.vm_pu.max()),
                "line_loading_percent_max": float(net.res_line.loading_percent.max()),
                "transformer_loading_percent_max": float(net.res_trafo.loading_percent.max()),
                "load_p_mw": float(net.res_load.p_mw.sum()),
                "generation_p_mw": float(net.res_sgen.loc[~boundary_sgen_mask, "p_mw"].sum()) + float(net.res_gen.p_mw.sum()),
                "generator_q_mvar": float(net.res_gen.q_mvar.sum()),
                "generator_capacity_violations": generator_capacity_violations,
                "q_limit_violations": int(((net.res_gen.q_mvar > net.gen.max_q_mvar + 1e-6) | (net.res_gen.q_mvar < net.gen.min_q_mvar - 1e-6)).sum()),
            })
            result["within_declared_screening_limits"] = bool(
                result["vm_pu_min"] >= 0.90 and result["vm_pu_max"] <= 1.10
                and result["line_loading_percent_max"] <= 100.0
                and result["transformer_loading_percent_max"] <= 100.0
                and result["q_limit_violations"] == 0
                and result["generator_capacity_violations"] == 0
            )
        except Exception as exc:
            result["solver_error"] = f"{type(exc).__name__}: {exc}"
            result["within_declared_screening_limits"] = False
        rows.append(result)
    frame = pd.DataFrame(rows)
    frame.to_csv(POWER_FLOW / "power_flow_scaling_sweep.csv", index=False)
    passing = frame[frame["within_declared_screening_limits"]]
    summary = {
        "generated_at": utc_now(), "model": str(model_path), "scenarios": len(frame),
        "converged_scenarios": int(frame["converged"].sum()),
        "screening_pass_scenarios": int(frame["within_declared_screening_limits"].sum()),
        "largest_tested_passing_scale": float(passing["scaling"].max()) if not passing.empty else None,
        "important_scope": "The 1.00 case is the primary full-scale public-data-informed case; the other rows are sensitivity/stress tests. Physical generator injections are capped at public nameplate capacity, while any explicitly unmapped residual remains a non-asset proxy. PV control and reactive limits are declared assumptions.",
    }
    write_json(POWER_FLOW / "power_flow_scaling_sweep_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
