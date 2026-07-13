"""Targeted S5 ACPF pathology sensitivities.

Tests alternate slack candidates, disabling ATPL_00003, capacitance caps/removal,
and current-rating multipliers on low-load S5 cases. Diagnostic only.
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
OUT = ROOT / "data" / "processed" / "acpf_failure_frontier"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S5 targeted sensitivity.")
    return net, opts


def set_load(net, scale: float, pf: float = 0.95) -> None:
    if len(net.load):
        net.load["scaling"] = scale
        net.load["q_mvar"] = net.load["p_mw"] * math.tan(math.acos(pf))


def effective_load(net) -> tuple[float, float]:
    if not len(net.load):
        return 0.0, 0.0
    active = net.load[net.load.in_service].copy()
    scaling = pd.to_numeric(active.get("scaling", 1.0), errors="coerce").fillna(1.0)
    p = float((pd.to_numeric(active["p_mw"], errors="coerce").fillna(0.0) * scaling).sum())
    q = float((pd.to_numeric(active["q_mvar"], errors="coerce").fillna(0.0) * scaling).sum())
    return p, q


def apply_sensitivity(net, sensitivity: dict[str, Any]) -> None:
    action = sensitivity["action"]
    if action == "alternate_slack":
        bus = int(sensitivity["slack_bus"])
        if len(net.ext_grid):
            net.ext_grid["in_service"] = False
        pp.create_ext_grid(net, bus=bus, vm_pu=1.0, va_degree=0.0, name=f"diagnostic_alt_slack_{bus}")
    elif action == "disable_line":
        line_name = str(sensitivity["line_name"])
        mask = net.line["name"].astype(str) == line_name
        net.line.loc[mask, "in_service"] = False
    elif action == "cap_c":
        cap = float(sensitivity["c_nf_per_km_cap"])
        net.line["c_nf_per_km"] = pd.to_numeric(net.line["c_nf_per_km"], errors="coerce").clip(upper=cap)
    elif action == "zero_c":
        net.line["c_nf_per_km"] = 0.0
    elif action == "multiply_max_i":
        factor = float(sensitivity["max_i_multiplier"])
        net.line["max_i_ka"] = pd.to_numeric(net.line["max_i_ka"], errors="coerce") * factor
    elif action == "base":
        return
    else:
        raise ValueError(f"Unknown sensitivity action: {action}")


def run_case(base_net, sensitivity: dict[str, Any], load_scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    set_load(net, load_scale)
    apply_sensitivity(net, sensitivity)
    p_mw, q_mvar = effective_load(net)
    row = {
        "sensitivity_id": sensitivity["sensitivity_id"],
        "action": sensitivity["action"],
        "detail": sensitivity.get("detail", ""),
        "load_scale": load_scale,
        "effective_p_mw": p_mw,
        "effective_q_mvar": q_mvar,
        "slack_buses": ";".join(str(int(x)) for x in net.ext_grid.loc[net.ext_grid.in_service, "bus"]) if len(net.ext_grid) else "",
        "converged": False,
        "error_type": "",
        "error": "",
        "min_vm_pu": "",
        "max_vm_pu": "",
        "max_line_loading_percent": "",
        "max_trafo_loading_percent": "",
        "worst_line_name": "",
        "worst_line_index": "",
        "worst_bus_index": "",
        "worst_bus_name": "",
        "publication_allowed": False,
        "diagnostic_only": True,
    }
    settings = {
        "algorithm": "nr",
        "init": "dc",
        "calculate_voltage_angles": True,
        "enforce_q_lims": False,
        "numba": False,
        "max_iteration": 120,
        "tolerance_mva": 1e-6,
    }
    try:
        pp.runpp(net, **settings)
        row["converged"] = bool(net.converged)
        if net.converged:
            max_line_idx = int(net.res_line["loading_percent"].idxmax()) if len(net.res_line) else -1
            max_bus_idx = int(net.res_bus["vm_pu"].idxmax()) if len(net.res_bus) else -1
            row.update(
                {
                    "min_vm_pu": float(net.res_bus["vm_pu"].min()) if len(net.res_bus) else "",
                    "max_vm_pu": float(net.res_bus["vm_pu"].max()) if len(net.res_bus) else "",
                    "max_line_loading_percent": float(net.res_line["loading_percent"].max()) if len(net.res_line) else "",
                    "max_trafo_loading_percent": float(net.res_trafo["loading_percent"].max()) if len(net.res_trafo) else "",
                    "worst_line_name": str(net.line.loc[max_line_idx, "name"]) if max_line_idx >= 0 else "",
                    "worst_line_index": max_line_idx if max_line_idx >= 0 else "",
                    "worst_bus_index": max_bus_idx if max_bus_idx >= 0 else "",
                    "worst_bus_name": str(net.bus.loc[max_bus_idx, "name"]) if max_bus_idx >= 0 else "",
                }
            )
    except Exception as exc:
        row["error_type"] = type(exc).__name__
        row["error"] = str(exc)
        row["traceback"] = traceback.format_exc(limit=3)
    return row


def sensitivity_plan(base_net) -> list[dict[str, Any]]:
    sensitivities: list[dict[str, Any]] = [
        {"sensitivity_id": "base", "action": "base", "detail": "baseline S5 diagnostic net"},
        {"sensitivity_id": "disable_ATPL_00003", "action": "disable_line", "line_name": "ATPL_00003", "detail": "Disable slack-adjacent pathological line"},
        {"sensitivity_id": "cap_c_50", "action": "cap_c", "c_nf_per_km_cap": 50.0, "detail": "Cap all line capacitance to 50 nF/km"},
        {"sensitivity_id": "zero_c", "action": "zero_c", "detail": "Set all line capacitance to zero"},
        {"sensitivity_id": "max_i_5x", "action": "multiply_max_i", "max_i_multiplier": 5.0, "detail": "Increase all line current ratings fivefold"},
    ]
    if len(base_net.ext_grid):
        current = set(int(x) for x in base_net.ext_grid.loc[base_net.ext_grid.in_service, "bus"])
    else:
        current = set()
    candidate_buses = []
    if len(base_net.ext_grid):
        candidate_buses.extend(int(x) for x in base_net.ext_grid["bus"].dropna().astype(int).unique())
    # Add high-voltage local candidates that are in-service and not current slack.
    candidate_buses.extend([385, 407, 409, 411, 517, 521, 542])
    seen: set[int] = set()
    for bus in candidate_buses:
        if bus in seen or bus in current or bus not in base_net.bus.index:
            continue
        seen.add(bus)
        if bool(base_net.bus.loc[bus].get("in_service", True)):
            sensitivities.append({"sensitivity_id": f"alt_slack_{bus}", "action": "alternate_slack", "slack_bus": bus, "detail": f"Use bus {bus} as diagnostic slack"})
    return sensitivities


def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(results: pd.DataFrame, summary: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    by_sensitivity = results.groupby("sensitivity_id")["converged"].agg(attempts="count", converged="sum").reset_index()
    converged = results[results["converged"] == True].copy()
    if len(converged):
        best_voltage = converged.assign(max_vm_abs_dev=(pd.to_numeric(converged["max_vm_pu"], errors="coerce") - 1.0).abs()).sort_values(["max_vm_abs_dev", "max_line_loading_percent"])
    else:
        best_voltage = converged
    cols = ["sensitivity_id", "action", "detail", "load_scale", "effective_p_mw", "effective_q_mvar", "slack_buses", "converged", "error_type", "min_vm_pu", "max_vm_pu", "max_line_loading_percent", "max_trafo_loading_percent", "worst_line_name", "worst_bus_name"]
    text = [
        "# 27 S5 Targeted Pathology Sensitivity",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: targeted diagnostic sensitivities for S5 pathology. This does not validate publication-grade PF readiness.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Convergence By Sensitivity",
        "",
        markdown_table(by_sensitivity, 120),
        "",
        "## Best Voltage-Normalized Converged Cases",
        "",
        markdown_table(best_voltage[cols + ["max_vm_abs_dev"]] if len(best_voltage) else best_voltage, 50),
        "",
        "## All Attempts",
        "",
        markdown_table(results[cols], 200),
    ]
    (REPORTS / "27_s5_targeted_pathology_sensitivity.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net, opts = load_net_checked()
    sensitivities = sensitivity_plan(net)
    load_scales = [0.02, 0.05, 0.10]
    rows = [run_case(net, sensitivity, scale) for sensitivity in sensitivities for scale in load_scales]
    results = pd.DataFrame(rows)
    results.to_csv(OUT / "s5_targeted_pathology_sensitivity_attempts.csv", index=False)
    converged = results[results["converged"] == True].copy()
    best = {}
    if len(converged):
        tmp = converged.assign(max_vm_abs_dev=(pd.to_numeric(converged["max_vm_pu"], errors="coerce") - 1.0).abs())
        best = tmp.sort_values(["max_vm_abs_dev", "max_line_loading_percent"]).iloc[0].to_dict()
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_status": opts.get("readiness_status"),
        "scenario_id": opts.get("scenario_id"),
        "sensitivity_count": len(sensitivities),
        "attempt_count": int(len(results)),
        "converged_count": int(results["converged"].sum()),
        "best_case_sensitivity_id": best.get("sensitivity_id", ""),
        "best_case_load_scale": best.get("load_scale", ""),
        "best_case_max_vm_pu": best.get("max_vm_pu", ""),
        "best_case_max_line_loading_percent": best.get("max_line_loading_percent", ""),
        "publication_allowed": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s5_targeted_pathology_sensitivity_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(results, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
