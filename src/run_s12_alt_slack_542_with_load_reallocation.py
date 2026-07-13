"""S12 diagnostic scenario: keep ATPL_00003, set slack to bus 542, and apply load reallocation suggestions."""

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
LOAD_VALIDATION = ROOT / "data" / "processed" / "load_validation"
OUT = ROOT / "data" / "processed" / "acpf_s12_alt_slack_542_load_reallocation"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}
SCENARIO_ID = "S12_ALT_SLACK_542_WITH_LOAD_REALLOCATION"
SLACK_BUS = 542
SUGGESTION_PATH = LOAD_VALIDATION / "pt_load_reallocation_suggestions.csv"


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S12 diagnostic scenario.")
    return net, opts


def load_suggestions() -> pd.DataFrame:
    if not SUGGESTION_PATH.exists():
        raise RuntimeError(f"Missing load reallocation suggestion file: {SUGGESTION_PATH}")
    return pd.read_csv(SUGGESTION_PATH)


def set_slack(net) -> None:
    if SLACK_BUS not in net.bus.index:
        raise RuntimeError(f"Slack bus {SLACK_BUS} is not present in net.bus")
    if len(net.ext_grid):
        net.ext_grid["in_service"] = False
    pp.create_ext_grid(net, bus=SLACK_BUS, vm_pu=1.0, va_degree=0.0, name=f"S12_ext_grid_{SLACK_BUS}")


def apply_suggestions(net, suggestions: pd.DataFrame) -> dict[str, Any]:
    if not len(net.load):
        return {"modified_load_count": 0, "total_p_before": 0.0, "total_p_after": 0.0}
    load = net.load.copy()
    before = float(pd.to_numeric(load["p_mw"], errors="coerce").fillna(0.0).sum())
    modified = 0
    action_counts: dict[str, int] = {}
    for _, row in suggestions.iterrows():
        load_id = str(row.get("load_id", ""))
        scale = float(row.get("suggested_scale", 1.0))
        action = str(row.get("action", ""))
        mask = load["name"].astype(str) == load_id
        if mask.any():
            load.loc[mask, "p_mw"] = pd.to_numeric(load.loc[mask, "p_mw"], errors="coerce") * scale
            load.loc[mask, "q_mvar"] = pd.to_numeric(load.loc[mask, "q_mvar"], errors="coerce") * scale
            modified += int(mask.sum())
            action_counts[action] = action_counts.get(action, 0) + int(mask.sum())
    after = float(pd.to_numeric(load["p_mw"], errors="coerce").fillna(0.0).sum())
    net.load = load
    return {
        "modified_load_count": modified,
        "total_p_before": before,
        "total_p_after": after,
        "action_counts": action_counts,
    }


def set_pf(net, pf: float = 0.95) -> None:
    if len(net.load):
        net.load["q_mvar"] = net.load["p_mw"] * math.tan(math.acos(pf))


def set_scale(net, scale: float) -> None:
    if len(net.load):
        net.load["scaling"] = scale


def effective_load(net) -> tuple[float, float]:
    if not len(net.load):
        return 0.0, 0.0
    active = net.load[net.load.in_service].copy() if "in_service" in net.load.columns else net.load.copy()
    scaling = pd.to_numeric(active.get("scaling", 1.0), errors="coerce").fillna(1.0)
    p = float((pd.to_numeric(active["p_mw"], errors="coerce").fillna(0.0) * scaling).sum())
    q = float((pd.to_numeric(active["q_mvar"], errors="coerce").fillna(0.0) * scaling).sum())
    return p, q


def prepare_net(base_net, suggestions: pd.DataFrame) -> tuple[Any, dict[str, Any]]:
    net = copy.deepcopy(base_net)
    set_slack(net)
    suggestion_summary = apply_suggestions(net, suggestions)
    set_pf(net, 0.95)
    opts = getattr(net, "user_pf_options", {}) or {}
    opts.update(
        {
            "scenario_id": SCENARIO_ID,
            "publication_allowed": False,
            "operator_grade_ready": False,
            "opf_ready": False,
            "provenance_policy": "diagnostic_alt_slack_542_with_load_reallocation",
            "load_reallocation_summary": suggestion_summary,
        }
    )
    net.user_pf_options = opts
    return net, suggestion_summary


def run_case(base_net, scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    set_scale(net, scale)
    p_mw, q_mvar = effective_load(net)
    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "attempt_name": f"s12_scale_{scale:.3f}_dc",
        "slack_bus": SLACK_BUS,
        "load_scale": scale,
        "effective_p_mw": p_mw,
        "effective_q_mvar": q_mvar,
        "converged": False,
        "error_type": "",
        "error": "",
        "min_vm_pu": "",
        "max_vm_pu": "",
        "max_line_loading_percent": "",
        "max_trafo_loading_percent": "",
        "worst_line_name": "",
        "worst_bus_name": "",
        "publication_allowed": False,
        "diagnostic_only": True,
    }
    try:
        pp.runpp(
            net,
            algorithm="nr",
            init="dc",
            calculate_voltage_angles=True,
            enforce_q_lims=False,
            numba=False,
            max_iteration=120,
            tolerance_mva=1e-6,
        )
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
                    "worst_bus_name": str(net.bus.loc[max_bus_idx, "name"]) if max_bus_idx >= 0 else "",
                }
            )
    except Exception as exc:
        row["error_type"] = type(exc).__name__
        row["error"] = str(exc)
        row["traceback"] = traceback.format_exc(limit=3)
    return row


def markdown_table(df: pd.DataFrame, max_rows: int = 120) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary: dict[str, Any], suggestion_summary: dict[str, Any], attempts: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 35 S12 Alt Slack 542 With Load Reallocation",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: keep ATPL_00003 in service, move slack to bus 542, and apply load reallocation suggestions from load validation diagnostics. Diagnostic only.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Load Reallocation Summary",
        "",
        markdown_table(pd.DataFrame([{
            'modified_load_count': suggestion_summary.get('modified_load_count', 0),
            'total_p_before': suggestion_summary.get('total_p_before', 0.0),
            'total_p_after': suggestion_summary.get('total_p_after', 0.0),
            'action_counts': suggestion_summary.get('action_counts', {}),
        }])),
        "",
        "## Frontier Attempts",
        "",
        markdown_table(attempts, 120),
    ]
    (REPORTS / "35_s12_alt_slack_542_with_load_reallocation.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_net, opts = load_net_checked()
    suggestions = load_suggestions()
    net, suggestion_summary = prepare_net(base_net, suggestions)
    pp.to_json(net, str(OUT / "s12_alt_slack_542_reallocated_net.json"))

    scales = [0.02, 0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.00]
    rows = [run_case(net, scale) for scale in scales]
    attempts = pd.DataFrame(rows)
    attempts.to_csv(OUT / "s12_acpf_frontier_attempts.csv", index=False)
    converged = attempts[attempts["converged"] == True].copy()
    last_converged = converged.iloc[-1].to_dict() if len(converged) else {}
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_readiness_status": opts.get("readiness_status"),
        "scenario_id": SCENARIO_ID,
        "slack_bus": SLACK_BUS,
        "attempt_count": int(len(attempts)),
        "converged_count": int(attempts["converged"].sum()),
        "max_converged_load_scale": last_converged.get("load_scale", ""),
        "max_converged_effective_p_mw": last_converged.get("effective_p_mw", ""),
        "max_converged_min_vm_pu": last_converged.get("min_vm_pu", ""),
        "max_converged_max_vm_pu": last_converged.get("max_vm_pu", ""),
        "max_converged_max_line_loading_percent": last_converged.get("max_line_loading_percent", ""),
        "modified_load_count": suggestion_summary.get("modified_load_count", 0),
        "total_p_before": suggestion_summary.get("total_p_before", 0.0),
        "total_p_after": suggestion_summary.get("total_p_after", 0.0),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s12_acpf_frontier_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, suggestion_summary, attempts)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
