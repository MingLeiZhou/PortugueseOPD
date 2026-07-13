"""Progressive ACPF diagnostic frontier for the Portuguese plumbing net.

Converged cases from this script are diagnostic only unless upstream readiness is
source-backed and validated. The script preserves fail-closed readiness checks.
"""

from __future__ import annotations

import copy
import json
import math
import os
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

import pandas as pd
import pandapower as pp

READY = ROOT / "data" / "processed" / "acpf_ready"
OUT = ROOT / "data" / "processed" / "acpf_progressive"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_RUN_STATUS = {"AC_PF_BENCHMARK_PLUMBING_READY", "AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY", "AC_PF_SCENARIO_READY", "SOURCE_BACKED_READY"}


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_RUN_STATUS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for progressive AC PF diagnostics.")
    return net, opts


def scale_load(net, factor: float) -> None:
    if len(net.load):
        net.load["scaling"] = factor


def set_power_factor(net, pf: float | None) -> None:
    if not len(net.load):
        return
    if pf is None:
        net.load["q_mvar"] = 0.0
    else:
        net.load["q_mvar"] = net.load["p_mw"] * math.tan(math.acos(pf))


def set_voltage_bounds(net, vmin: float, vmax: float) -> None:
    if "min_vm_pu" in net.bus.columns:
        net.bus["min_vm_pu"] = vmin
    if "max_vm_pu" in net.bus.columns:
        net.bus["max_vm_pu"] = vmax


def set_transformer_xr(net, xr: float) -> None:
    if not len(net.trafo):
        return
    vk = pd.to_numeric(net.trafo["vk_percent"], errors="coerce")
    net.trafo["vkr_percent"] = vk / math.sqrt(1.0 + xr**2)


def multiply_trafo_capacity(net, factor: float) -> None:
    if len(net.trafo):
        net.trafo["sn_mva"] = pd.to_numeric(net.trafo["sn_mva"], errors="coerce") * factor


def add_simple_shunt_proxy(net, q_mvar_per_load_bus: float = 2.0) -> int:
    if not len(net.load):
        return 0
    added = 0
    for bus in sorted(set(int(x) for x in net.load.loc[net.load.in_service, "bus"])):
        pp.create_shunt(net, bus=bus, q_mvar=-abs(q_mvar_per_load_bus), p_mw=0.0, name=f"diagnostic_shunt_{bus}")
        added += 1
    return added


def effective_load(net) -> tuple[float, float]:
    if not len(net.load):
        return 0.0, 0.0
    active = net.load[net.load.in_service].copy()
    scaling = pd.to_numeric(active.get("scaling", 1.0), errors="coerce").fillna(1.0)
    p = float((pd.to_numeric(active["p_mw"], errors="coerce").fillna(0.0) * scaling).sum())
    q = float((pd.to_numeric(active["q_mvar"], errors="coerce").fillna(0.0) * scaling).sum())
    return p, q


def run_attempt(base_net, attempt: dict[str, Any]) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    scale_load(net, float(attempt.get("load_scale", 1.0)))
    if "pf" in attempt:
        set_power_factor(net, attempt["pf"])
    if "vmin" in attempt and "vmax" in attempt:
        set_voltage_bounds(net, float(attempt["vmin"]), float(attempt["vmax"]))
    if "trafo_xr" in attempt:
        set_transformer_xr(net, float(attempt["trafo_xr"]))
    if "trafo_capacity_multiplier" in attempt:
        multiply_trafo_capacity(net, float(attempt["trafo_capacity_multiplier"]))
    shunts_added = 0
    if attempt.get("shunt_proxy"):
        shunts_added = add_simple_shunt_proxy(net)

    settings = {
        "algorithm": attempt.get("algorithm", "nr"),
        "init": attempt.get("init", "flat"),
        "calculate_voltage_angles": True,
        "enforce_q_lims": bool(attempt.get("enforce_q_lims", False)),
        "numba": False,
        "max_iteration": int(attempt.get("max_iteration", 80)),
        "tolerance_mva": float(attempt.get("tolerance_mva", 1e-6)),
    }
    p, q = effective_load(net)
    row = {
        **attempt,
        "diagnostic_only": True,
        "publication_allowed": False,
        "effective_p_mw": p,
        "effective_q_mvar": q,
        "shunts_added": shunts_added,
        "converged": False,
        "error_type": "",
        "error": "",
        "min_vm_pu": "",
        "max_vm_pu": "",
        "max_line_loading_percent": "",
        "max_trafo_loading_percent": "",
        "total_line_losses_mw": "",
        "total_trafo_losses_mw": "",
    }
    try:
        pp.runpp(net, **settings)
        row["converged"] = bool(net.converged)
        if net.converged:
            row.update(
                {
                    "min_vm_pu": float(net.res_bus["vm_pu"].min()),
                    "max_vm_pu": float(net.res_bus["vm_pu"].max()),
                    "max_line_loading_percent": float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0,
                    "max_trafo_loading_percent": float(net.res_trafo["loading_percent"].max()) if len(net.res_trafo) else 0.0,
                    "total_line_losses_mw": float(net.res_line["pl_mw"].sum()) if len(net.res_line) else 0.0,
                    "total_trafo_losses_mw": float(net.res_trafo["pl_mw"].sum()) if len(net.res_trafo) else 0.0,
                }
            )
    except Exception as exc:
        row["error_type"] = type(exc).__name__
        row["error"] = str(exc)
        row["traceback"] = traceback.format_exc(limit=3)
    return row


def attempt_plan() -> list[dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    attempts.append({"level": "L0", "attempt_name": "base_nr_flat", "algorithm": "nr", "init": "flat", "load_scale": 1.0, "vmin": 0.95, "vmax": 1.05})
    for init in ["dc", "flat"]:
        for algo in ["iwamoto_nr", "nr"]:
            attempts.append({"level": "L1", "attempt_name": f"{algo}_{init}", "algorithm": algo, "init": init, "load_scale": 1.0, "vmin": 0.95, "vmax": 1.05, "max_iteration": 120})
    for scale in [0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.00]:
        attempts.append({"level": "L2", "attempt_name": f"load_scale_{scale:.2f}", "algorithm": "nr", "init": "flat", "load_scale": scale, "vmin": 0.95, "vmax": 1.05, "max_iteration": 120})
    for bounds in [(0.90, 1.10), (0.85, 1.15)]:
        for scale in [0.5, 1.0]:
            attempts.append({"level": "L3", "attempt_name": f"voltage_{bounds[0]}_{bounds[1]}_scale_{scale}", "algorithm": "nr", "init": "flat", "load_scale": scale, "vmin": bounds[0], "vmax": bounds[1], "max_iteration": 120})
    for pf in [1.0, 0.98, 0.95, 0.90, None]:
        label = "q_zero" if pf is None else f"pf_{pf:.2f}"
        for scale in [0.5, 1.0]:
            attempts.append({"level": "L4", "attempt_name": f"{label}_scale_{scale}", "algorithm": "nr", "init": "flat", "load_scale": scale, "pf": pf, "vmin": 0.90, "vmax": 1.10, "max_iteration": 120})
    for xr in [10, 20, 40]:
        attempts.append({"level": "L5", "attempt_name": f"trafo_xr_{xr}", "algorithm": "nr", "init": "flat", "load_scale": 1.0, "trafo_xr": xr, "vmin": 0.90, "vmax": 1.10, "max_iteration": 120})
    for mult in [1.5, 2.0]:
        attempts.append({"level": "L5", "attempt_name": f"trafo_capacity_{mult}", "algorithm": "nr", "init": "flat", "load_scale": 1.0, "trafo_capacity_multiplier": mult, "vmin": 0.90, "vmax": 1.10, "max_iteration": 120})
    for scale in [0.5, 1.0]:
        attempts.append({"level": "L5", "attempt_name": f"simple_shunt_proxy_scale_{scale}", "algorithm": "nr", "init": "flat", "load_scale": scale, "vmin": 0.90, "vmax": 1.10, "shunt_proxy": True, "max_iteration": 120})
    return attempts


def markdown_table(df: pd.DataFrame, max_rows: int = 30) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(attempts: pd.DataFrame, first: dict[str, Any], summary: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    by_level = attempts.groupby("level")["converged"].agg(attempts="count", converged="sum").reset_index()
    converged = attempts[attempts["converged"] == True]
    text = [
        "# 18 Progressive ACPF Diagnostics",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: diagnostic frontier only. Converged cases are not publication-grade Portuguese PF results unless all upstream data are source-backed and validated.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Attempts By Level",
        "",
        markdown_table(by_level),
        "",
        "## First Diagnostic Convergence",
        "",
        markdown_table(pd.DataFrame([first]) if first else pd.DataFrame()),
        "",
        "## Converged Attempts",
        "",
        markdown_table(converged[["level", "attempt_name", "effective_p_mw", "effective_q_mvar", "min_vm_pu", "max_vm_pu", "max_line_loading_percent", "max_trafo_loading_percent"]] if len(converged) else converged, 30),
    ]
    (REPORTS / "18_progressive_acpf.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net, opts = load_net_checked()
    rows = [run_attempt(net, attempt) for attempt in attempt_plan()]
    attempts = pd.DataFrame(rows)
    attempts.to_csv(OUT / "pt_acpf_progressive_attempts.csv", index=False)
    converged = attempts[attempts["converged"] == True].copy()
    first = converged.iloc[0].to_dict() if len(converged) else {}
    (OUT / "pt_acpf_progressive_first_converged.json").write_text(json.dumps(first, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_status": opts.get("readiness_status"),
        "scenario_id": opts.get("scenario_id"),
        "attempt_count": int(len(attempts)),
        "converged_count": int(attempts["converged"].sum()),
        "first_converged_attempt": first.get("attempt_name", ""),
        "first_converged_level": first.get("level", ""),
        "first_converged_effective_p_mw": first.get("effective_p_mw", ""),
        "status": "DIAGNOSTIC_DONE" if len(attempts) else "NOT_RUN",
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
    }
    (OUT / "pt_acpf_progressive_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(attempts, first, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
