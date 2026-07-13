"""Run an AC power-flow reference case on a SimBench MV network.

This benchmark intentionally does not use E-REDES data. If the optional
simbench dependency is unavailable, the script writes a status file and exits
cleanly so the benchmark suite remains reproducible.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_DEPS = ROOT / ".deps"
if LOCAL_DEPS.exists():
    sys.path.insert(0, str(LOCAL_DEPS))
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

import pandas as pd
import pandapower as pp


RESULTS = ROOT / "data" / "benchmark_results"
SIMBENCH_CODE = "1-MV-rural--0-sw"


def with_index(df: pd.DataFrame, index_name: str) -> pd.DataFrame:
    out = df.copy()
    out.insert(0, index_name, out.index)
    return out


def combine(element: pd.DataFrame, result: pd.DataFrame, index_name: str) -> pd.DataFrame:
    base = with_index(element, index_name)
    res = with_index(result.add_prefix("res_"), index_name)
    return base.merge(res, on=index_name, how="left")


def write_status(status: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "simbench_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")


def write_empty_outputs(status: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([status]).to_csv(RESULTS / "simbench_bus_results.csv", index=False)
    pd.DataFrame([status]).to_csv(RESULTS / "simbench_line_results.csv", index=False)
    pd.DataFrame([status]).to_csv(RESULTS / "simbench_trafo_results.csv", index=False)


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    if importlib.util.find_spec("simbench") is None:
        status = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "benchmark": SIMBENCH_CODE,
            "status": "SKIPPED_DEPENDENCY_MISSING",
            "message": "Install simbench or keep project-local .deps on sys.path.",
        }
        write_status(status)
        write_empty_outputs(status)
        print(json.dumps(status, indent=2))
        return

    import simbench as sb

    net = sb.get_simbench_net(SIMBENCH_CODE)
    pp.runpp(net, algorithm="nr", init="auto", calculate_voltage_angles=True, numba=False)

    bus_results = combine(net.bus, net.res_bus, "bus")
    line_results = combine(net.line, net.res_line, "line")
    trafo_results = combine(net.trafo, net.res_trafo, "trafo")
    load_results = combine(net.load, net.res_load, "load")
    sgen_results = combine(net.sgen, net.res_sgen, "sgen") if len(net.sgen) else pd.DataFrame()
    ext_grid_results = combine(net.ext_grid, net.res_ext_grid, "ext_grid")

    bus_results.to_csv(RESULTS / "simbench_bus_results.csv", index=False)
    line_results.to_csv(RESULTS / "simbench_line_results.csv", index=False)
    trafo_results.to_csv(RESULTS / "simbench_trafo_results.csv", index=False)
    load_results.to_csv(RESULTS / "simbench_load_results.csv", index=False)
    sgen_results.to_csv(RESULTS / "simbench_sgen_results.csv", index=False)
    ext_grid_results.to_csv(RESULTS / "simbench_ext_grid_results.csv", index=False)

    status = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": SIMBENCH_CODE,
        "simbench_version": getattr(sb, "__version__", "unknown"),
        "pandapower_version": pp.__version__,
        "status": "CONVERGED" if net.converged else "NOT_CONVERGED",
        "converged": bool(net.converged),
        "bus_count": int(len(net.bus)),
        "line_count": int(len(net.line)),
        "trafo_count": int(len(net.trafo)),
        "load_count": int(len(net.load)),
        "sgen_count": int(len(net.sgen)),
        "ext_grid_count": int(len(net.ext_grid)),
        "total_load_p_mw": float(net.res_load["p_mw"].sum()) if len(net.res_load) else 0.0,
        "total_sgen_p_mw": float(net.res_sgen["p_mw"].sum()) if len(net.res_sgen) else 0.0,
        "total_ext_grid_p_mw": float(net.res_ext_grid["p_mw"].sum()) if len(net.res_ext_grid) else 0.0,
        "line_pl_mw": float(net.res_line["pl_mw"].sum()) if len(net.res_line) else 0.0,
        "trafo_pl_mw": float(net.res_trafo["pl_mw"].sum()) if len(net.res_trafo) else 0.0,
        "total_losses_mw": float(net.res_line["pl_mw"].sum() + net.res_trafo["pl_mw"].sum()),
        "min_bus_vm_pu": float(net.res_bus["vm_pu"].min()),
        "max_bus_vm_pu": float(net.res_bus["vm_pu"].max()),
        "max_line_loading_percent": float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0,
        "max_trafo_loading_percent": float(net.res_trafo["loading_percent"].max()) if len(net.res_trafo) else 0.0,
        "required_input_tables": ["bus", "line", "trafo", "load", "sgen", "ext_grid"],
    }
    write_status(status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
