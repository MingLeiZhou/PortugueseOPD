"""Run DC-OPF and AC-OPF on a small MATPOWER/PGLib-family benchmark case.

This is a solver workflow reference only. It does not use the Portuguese
E-REDES candidate topology and does not imply E-REDES model readiness.
"""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

import pandas as pd
import pandapower as pp
import pandapower.networks as pn


RESULTS = ROOT / "data" / "benchmark_results"


def with_index(df: pd.DataFrame, index_name: str) -> pd.DataFrame:
    out = df.copy()
    out.insert(0, index_name, out.index)
    return out


def combine(element: pd.DataFrame, result: pd.DataFrame, index_name: str) -> pd.DataFrame:
    base = with_index(element, index_name)
    res = with_index(result.add_prefix("res_"), index_name)
    return base.merge(res, on=index_name, how="left")


def write_outputs(prefix: str, net) -> None:
    combine(net.bus, net.res_bus, "bus").to_csv(RESULTS / f"{prefix}_bus_results.csv", index=False)
    combine(net.line, net.res_line, "line").to_csv(RESULTS / f"{prefix}_line_results.csv", index=False)
    combine(net.trafo, net.res_trafo, "trafo").to_csv(RESULTS / f"{prefix}_trafo_results.csv", index=False)
    combine(net.load, net.res_load, "load").to_csv(RESULTS / f"{prefix}_load_results.csv", index=False)
    combine(net.gen, net.res_gen, "gen").to_csv(RESULTS / f"{prefix}_gen_results.csv", index=False)
    combine(net.ext_grid, net.res_ext_grid, "ext_grid").to_csv(RESULTS / f"{prefix}_ext_grid_results.csv", index=False)


def summarize_network(net) -> dict[str, Any]:
    return {
        "bus_count": int(len(net.bus)),
        "line_count": int(len(net.line)),
        "trafo_count": int(len(net.trafo)),
        "load_count": int(len(net.load)),
        "gen_count": int(len(net.gen)),
        "ext_grid_count": int(len(net.ext_grid)),
        "cost_rows": int(len(net.poly_cost)),
        "objective_function": "quadratic/linear generation cost from pandapower poly_cost table",
        "voltage_constraints": "bus min_vm_pu/max_vm_pu",
        "branch_limits": "line max_loading_percent / max_i_ka and transformer loading constraints where defined",
        "generator_constraints": "gen/ext_grid min_p_mw/max_p_mw and min_q_mvar/max_q_mvar",
    }


def run_dc_opf() -> tuple[dict[str, Any], Any | None]:
    net = pn.case14()
    summary = summarize_network(net)
    try:
        pp.rundcopp(net, verbose=False)
        summary.update(
            {
                "solver_track": "DC-OPF",
                "solver_function": "pandapower.rundcopp",
                "status": "CONVERGED" if net.OPF_converged else "NOT_CONVERGED",
                "converged": bool(net.OPF_converged),
                "objective_value": float(net.res_cost),
                "min_bus_vm_pu": float(net.res_bus["vm_pu"].min()),
                "max_bus_vm_pu": float(net.res_bus["vm_pu"].max()),
                "max_line_loading_percent": float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0,
                "max_trafo_loading_percent": float(net.res_trafo["loading_percent"].max()) if len(net.res_trafo) else 0.0,
            }
        )
        return summary, net
    except Exception as exc:  # pragma: no cover - solver availability depends on env
        summary.update(
            {
                "solver_track": "DC-OPF",
                "solver_function": "pandapower.rundcopp",
                "status": "FAILED",
                "converged": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=3),
            }
        )
        return summary, None


def run_ac_opf() -> tuple[dict[str, Any], Any | None]:
    net = pn.case14()
    summary = summarize_network(net)
    try:
        pp.runopp(net, verbose=False, numba=False)
        summary.update(
            {
                "solver_track": "AC-OPF",
                "solver_function": "pandapower.runopp",
                "status": "CONVERGED" if net.OPF_converged else "NOT_CONVERGED",
                "converged": bool(net.OPF_converged),
                "objective_value": float(net.res_cost),
                "min_bus_vm_pu": float(net.res_bus["vm_pu"].min()),
                "max_bus_vm_pu": float(net.res_bus["vm_pu"].max()),
                "max_line_loading_percent": float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0,
                "max_trafo_loading_percent": float(net.res_trafo["loading_percent"].max()) if len(net.res_trafo) else 0.0,
            }
        )
        return summary, net
    except Exception as exc:  # pragma: no cover - solver availability depends on env
        summary.update(
            {
                "solver_track": "AC-OPF",
                "solver_function": "pandapower.runopp",
                "status": "FAILED",
                "converged": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=3),
            }
        )
        return summary, None


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)

    dc_summary, dc_net = run_dc_opf()
    ac_summary, ac_net = run_ac_opf()
    summaries = []
    for summary in [dc_summary, ac_summary]:
        summary["generated_at"] = datetime.now(timezone.utc).isoformat()
        summary["benchmark_case"] = "pandapower.networks.case14 (MATPOWER IEEE 14-bus family)"
        summary["pandapower_version"] = pp.__version__
        summaries.append(summary)

    if dc_net is not None:
        write_outputs("pglib_case14_dc_opf", dc_net)
    if ac_net is not None:
        write_outputs("pglib_case14_ac_opf", ac_net)

    pd.DataFrame(summaries).to_csv(RESULTS / "pglib_opf_results.csv", index=False)
    (RESULTS / "pglib_opf_results.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(json.dumps(summaries, indent=2))


if __name__ == "__main__":
    main()
