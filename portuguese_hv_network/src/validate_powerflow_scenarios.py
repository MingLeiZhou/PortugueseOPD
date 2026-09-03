#!/usr/bin/env python3
"""Run a transparent AC power-flow scaling sweep on the built candidate model."""
from __future__ import annotations

import argparse
import json

import pandapower as pp
import pandas as pd

from common import MODEL, POWER_FLOW, ensure_dirs, utc_now, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scales", default="0.50,0.75,1.00,1.10,1.20")
    args = parser.parse_args()
    ensure_dirs()
    model_path = MODEL / "portuguese_hv_candidate.json"
    rows: list[dict[str, object]] = []
    for scale in [float(value) for value in args.scales.split(",")]:
        net = pp.from_json(model_path)
        for table_name in ("load", "sgen", "gen"):
            table = getattr(net, table_name)
            if not table.empty:
                table["scaling"] = scale
        result: dict[str, object] = {"scaling": scale, "converged": False}
        try:
            pp.runpp(
                net, algorithm="nr", init="dc", calculate_voltage_angles=True,
                max_iteration=50, tolerance_mva=1e-6, enforce_q_lims=True, numba=False,
            )
            result.update({
                "converged": bool(net.converged),
                "vm_pu_min": float(net.res_bus.vm_pu.min()), "vm_pu_max": float(net.res_bus.vm_pu.max()),
                "line_loading_percent_max": float(net.res_line.loading_percent.max()),
                "transformer_loading_percent_max": float(net.res_trafo.loading_percent.max()),
                "load_p_mw": float(net.res_load.p_mw.sum()),
                "generation_p_mw": float(net.res_sgen.p_mw.sum()) + float(net.res_gen.p_mw.sum()),
                "generator_q_mvar": float(net.res_gen.q_mvar.sum()),
                "q_limit_violations": int(((net.res_gen.q_mvar > net.gen.max_q_mvar + 1e-6) | (net.res_gen.q_mvar < net.gen.min_q_mvar - 1e-6)).sum()),
            })
            result["within_declared_screening_limits"] = bool(
                result["vm_pu_min"] >= 0.90 and result["vm_pu_max"] <= 1.10
                and result["line_loading_percent_max"] <= 100.0
                and result["transformer_loading_percent_max"] <= 100.0
                and result["q_limit_violations"] == 0
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
        "important_scope": "The 1.00 case is the primary full-scale public-data-calibrated case; the other rows are sensitivity/stress tests. PV control and reactive limits are declared assumptions.",
    }
    write_json(POWER_FLOW / "power_flow_scaling_sweep_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(frame.to_string(index=False))


if __name__ == "__main__":
    main()
