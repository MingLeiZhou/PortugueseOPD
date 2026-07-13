"""Run Portuguese AC PF only if the fail-closed readiness status allows it."""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

import matplotlib.pyplot as plt
import pandas as pd
import pandapower as pp


READY = ROOT / "data" / "processed" / "acpf_ready"
RESULTS = ROOT / "data" / "processed" / "acpf_results"
FIGURES = ROOT / "figures"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_RUN_STATUS = {"AC_PF_BENCHMARK_PLUMBING_READY", "AC_PF_SCENARIO_READY", "AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY", "SOURCE_BACKED_READY"}


def with_index(df: pd.DataFrame, name: str) -> pd.DataFrame:
    out = df.copy()
    out.insert(0, name, out.index)
    return out


def combine(element: pd.DataFrame, result: pd.DataFrame, name: str) -> pd.DataFrame:
    return with_index(element, name).merge(with_index(result.add_prefix("res_"), name), on=name, how="left")


def save_results(net, summary: dict) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)
    combine(net.bus, net.res_bus, "bus").to_csv(RESULTS / "pt_acpf_bus_results.csv", index=False)
    combine(net.line, net.res_line, "line").to_csv(RESULTS / "pt_acpf_line_results.csv", index=False)
    combine(net.trafo, net.res_trafo, "trafo").to_csv(RESULTS / "pt_acpf_trafo_results.csv", index=False)
    combine(net.ext_grid, net.res_ext_grid, "ext_grid").to_csv(RESULTS / "pt_acpf_ext_grid_results.csv", index=False)
    combine(net.load, net.res_load, "load").to_csv(RESULTS / "pt_acpf_load_results.csv", index=False)
    (RESULTS / "pt_acpf_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    bus_res = combine(net.bus, net.res_bus, "bus")
    active_bus = bus_res[bus_res["in_service"]].copy()
    plt.figure(figsize=(10, 5))
    active_bus.sort_values("res_vm_pu")["res_vm_pu"].reset_index(drop=True).plot(marker="o", linewidth=1)
    plt.axhline(0.95, color="tab:red", linestyle="--", linewidth=0.8)
    plt.axhline(1.05, color="tab:red", linestyle="--", linewidth=0.8)
    plt.title("Portuguese ACPF Scenario: Voltage Profile")
    plt.xlabel("Active bus order by voltage")
    plt.ylabel("Voltage [p.u.]")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(FIGURES / "pt_acpf_voltage_profile.png", dpi=160)
    plt.close()

    line_res = combine(net.line, net.res_line, "line")
    plt.figure(figsize=(10, 5))
    line_res.sort_values("res_loading_percent", ascending=False)["res_loading_percent"].reset_index(drop=True).plot(kind="bar")
    plt.title("Portuguese ACPF Scenario: Line Loading")
    plt.xlabel("Active line order by loading")
    plt.ylabel("Loading [%]")
    plt.tight_layout()
    plt.savefig(FIGURES / "pt_acpf_line_loading.png", dpi=160)
    plt.close()

    trafo_res = combine(net.trafo, net.res_trafo, "trafo")
    plt.figure(figsize=(10, 5))
    trafo_res.sort_values("res_loading_percent", ascending=False)["res_loading_percent"].reset_index(drop=True).plot(kind="bar")
    plt.title("Portuguese ACPF Scenario: Transformer Loading")
    plt.xlabel("Active transformer order by loading")
    plt.ylabel("Loading [%]")
    plt.tight_layout()
    plt.savefig(FIGURES / "pt_acpf_trafo_loading.png", dpi=160)
    plt.close()


def main() -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_RUN_STATUS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for AC PF run.")

    solver_settings = {
        "algorithm": "nr",
        "init": "flat",
        "calculate_voltage_angles": True,
        "enforce_q_lims": False,
        "numba": False,
        "max_iteration": 50,
        "tolerance_mva": 1e-6,
    }
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_status": readiness,
        "scenario_id": opts.get("scenario_id"),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "provenance_policy": opts.get("provenance_policy"),
        "solver_settings": solver_settings,
        "converged": False,
        "warnings": [],
    }
    try:
        pp.runpp(net, **solver_settings)
        summary["converged"] = bool(net.converged)
        summary.update(
            {
                "bus_count": int(len(net.bus)),
                "active_bus_count": int(net.bus["in_service"].sum()),
                "line_count": int(len(net.line)),
                "trafo_count": int(len(net.trafo)),
                "load_count": int(len(net.load)),
                "ext_grid_count": int(len(net.ext_grid)),
                "min_vm_pu": float(net.res_bus["vm_pu"].min()),
                "max_vm_pu": float(net.res_bus["vm_pu"].max()),
                "voltage_violation_count": int(((net.res_bus["vm_pu"] < net.bus["min_vm_pu"]) | (net.res_bus["vm_pu"] > net.bus["max_vm_pu"])).sum()),
                "max_line_loading_percent": float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0,
                "max_trafo_loading_percent": float(net.res_trafo["loading_percent"].max()) if len(net.res_trafo) else 0.0,
                "total_line_losses_mw": float(net.res_line["pl_mw"].sum()) if len(net.res_line) else 0.0,
                "total_trafo_losses_mw": float(net.res_trafo["pl_mw"].sum()) if len(net.res_trafo) else 0.0,
                "total_load_p_mw": float(net.res_load["p_mw"].sum()) if len(net.res_load) else 0.0,
                "total_load_q_mvar": float(net.res_load["q_mvar"].sum()) if len(net.res_load) else 0.0,
                "total_ext_grid_p_mw": float(net.res_ext_grid["p_mw"].sum()) if len(net.res_ext_grid) else 0.0,
                "total_ext_grid_q_mvar": float(net.res_ext_grid["q_mvar"].sum()) if len(net.res_ext_grid) else 0.0,
                "islands_or_components_not_energized": "inactive buses/branches were excluded upstream by scenario component selection",
            }
        )
        save_results(net, summary)
    except Exception as exc:
        summary.update(
            {
                "converged": False,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=5),
                "likely_causes": [
                    "benchmark-only line impedances may be numerically unsuitable",
                    "single-slack component selection may exclude or overload equivalent transformers",
                    "load and Q scenarios may be too heavy for the assumed topology",
                    "tap/slack assumptions may be inadequate",
                ],
            }
        )
        (RESULTS / "pt_acpf_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
