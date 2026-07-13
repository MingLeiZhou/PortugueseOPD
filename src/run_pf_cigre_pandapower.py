"""Run an AC power-flow reference case on pandapower's CIGRE MV network.

This benchmark intentionally does not use E-REDES data. It exports result
tables and plots that define the future workflow contract for the Portuguese
model once parameters are available.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

import matplotlib.pyplot as plt
import pandas as pd
import pandapower as pp
import pandapower.networks as pn


RESULTS = ROOT / "data" / "benchmark_results"
FIGURES = ROOT / "figures"


def with_index(df: pd.DataFrame, index_name: str) -> pd.DataFrame:
    out = df.copy()
    out.insert(0, index_name, out.index)
    return out


def write_table(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def combine(element: pd.DataFrame, result: pd.DataFrame, index_name: str) -> pd.DataFrame:
    base = with_index(element, index_name)
    res = with_index(result.add_prefix("res_"), index_name)
    return base.merge(res, on=index_name, how="left")


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    net = pn.create_cigre_network_mv(with_der="pv_wind")
    pp.runpp(net, algorithm="nr", init="auto", calculate_voltage_angles=True, numba=False)

    bus_results = combine(net.bus, net.res_bus, "bus")
    line_results = combine(net.line, net.res_line, "line")
    trafo_results = combine(net.trafo, net.res_trafo, "trafo")
    load_results = combine(net.load, net.res_load, "load")
    sgen_results = combine(net.sgen, net.res_sgen, "sgen")
    ext_grid_results = combine(net.ext_grid, net.res_ext_grid, "ext_grid")

    write_table(RESULTS / "cigre_bus_results.csv", bus_results)
    write_table(RESULTS / "cigre_line_results.csv", line_results)
    write_table(RESULTS / "cigre_trafo_results.csv", trafo_results)
    write_table(RESULTS / "cigre_load_results.csv", load_results)
    write_table(RESULTS / "cigre_sgen_results.csv", sgen_results)
    write_table(RESULTS / "cigre_ext_grid_results.csv", ext_grid_results)

    losses = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "benchmark": "pandapower.create_cigre_network_mv(with_der='pv_wind')",
        "pandapower_version": pp.__version__,
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
        "max_line_loading_percent": float(net.res_line["loading_percent"].max()),
        "max_trafo_loading_percent": float(net.res_trafo["loading_percent"].max()),
        "required_input_tables": ["bus", "line", "trafo", "load", "sgen", "ext_grid"],
    }
    (RESULTS / "cigre_power_losses.json").write_text(json.dumps(losses, indent=2), encoding="utf-8")

    sorted_bus = bus_results.sort_values(["vn_kv", "bus"]).reset_index(drop=True)
    plt.figure(figsize=(10, 5))
    plt.plot(sorted_bus.index, sorted_bus["res_vm_pu"], marker="o", linewidth=1.5)
    plt.axhline(1.05, color="tab:red", linestyle="--", linewidth=0.8)
    plt.axhline(0.95, color="tab:red", linestyle="--", linewidth=0.8)
    plt.title("CIGRE MV AC Power Flow: Bus Voltage Profile")
    plt.xlabel("Bus order by voltage level/index")
    plt.ylabel("Voltage magnitude [p.u.]")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(FIGURES / "benchmark_voltage_profile.png", dpi=160)
    plt.close()

    sorted_line = line_results.sort_values("res_loading_percent", ascending=False).reset_index(drop=True)
    plt.figure(figsize=(10, 5))
    plt.bar(sorted_line.index, sorted_line["res_loading_percent"], color="tab:blue")
    plt.title("CIGRE MV AC Power Flow: Line Loading")
    plt.xlabel("Line order by loading")
    plt.ylabel("Loading [%]")
    plt.grid(True, axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(FIGURES / "benchmark_line_loading.png", dpi=160)
    plt.close()

    print(json.dumps(losses, indent=2))


if __name__ == "__main__":
    main()
