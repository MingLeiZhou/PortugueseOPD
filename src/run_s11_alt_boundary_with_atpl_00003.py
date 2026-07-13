"""S11 diagnostic scenario: keep ATPL_00003 and vary slack-side boundary representation."""

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
OUT = ROOT / "data" / "processed" / "acpf_s11_alt_boundary"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}
SCENARIO_ID = "S11_ALT_BOUNDARY_WITH_ATPL_00003"
SLACK_CANDIDATES = [383, 385, 407, 409, 411, 517, 521, 542]


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S11 diagnostic scenario.")
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


def set_slack(net, slack_bus: int) -> None:
    if slack_bus not in net.bus.index:
        raise RuntimeError(f"Slack bus {slack_bus} is not present in net.bus")
    if len(net.ext_grid):
        net.ext_grid["in_service"] = False
    pp.create_ext_grid(net, bus=slack_bus, vm_pu=1.0, va_degree=0.0, name=f"S11_ext_grid_{slack_bus}")


def run_case(base_net, slack_bus: int, scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    set_load(net, scale)
    set_slack(net, slack_bus)
    p_mw, q_mvar = effective_load(net)
    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "slack_bus": slack_bus,
        "slack_bus_name": str(net.bus.loc[slack_bus, "name"]),
        "attempt_name": f"s11_slack_{slack_bus}_scale_{scale:.3f}",
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


def write_report(summary: dict[str, Any], results: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    by_slack = results.groupby(["slack_bus", "slack_bus_name"])["converged"].agg(attempts="count", converged="sum").reset_index()
    converged = results[results["converged"] == True].copy()
    if len(converged):
        best = converged.assign(max_vm_abs_dev=(pd.to_numeric(converged["max_vm_pu"], errors="coerce") - 1.0).abs()).sort_values(["load_scale", "max_vm_abs_dev", "max_line_loading_percent"], ascending=[False, True, True])
    else:
        best = converged
    text = [
        "# 34 S11 Alternate Boundary With ATPL_00003",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: keep ATPL_00003 in service and vary slack-side boundary representation to test whether non-convergence is driven by line existence or by its coupling to the chosen slack/boundary model.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Convergence By Slack",
        "",
        markdown_table(by_slack, 80),
        "",
        "## Best Converged Attempts",
        "",
        markdown_table(best, 80),
        "",
        "## All Attempts",
        "",
        markdown_table(results, 160),
    ]
    (REPORTS / "34_s11_alt_boundary_with_atpl_00003.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net, opts = load_net_checked()
    scales = [0.02, 0.05, 0.10]
    rows = [run_case(net, slack_bus, scale) for slack_bus in SLACK_CANDIDATES for scale in scales]
    results = pd.DataFrame(rows)
    results.to_csv(OUT / "s11_alt_boundary_attempts.csv", index=False)
    converged = results[results["converged"] == True].copy()
    best = {}
    if len(converged):
        tmp = converged.assign(max_vm_abs_dev=(pd.to_numeric(converged["max_vm_pu"], errors="coerce") - 1.0).abs())
        best = tmp.sort_values(["load_scale", "max_vm_abs_dev", "max_line_loading_percent"], ascending=[False, True, True]).iloc[0].to_dict()
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_readiness_status": opts.get("readiness_status"),
        "scenario_id": SCENARIO_ID,
        "slack_candidate_count": len(SLACK_CANDIDATES),
        "attempt_count": int(len(results)),
        "converged_count": int(results["converged"].sum()),
        "best_case_slack_bus": best.get("slack_bus", ""),
        "best_case_slack_bus_name": best.get("slack_bus_name", ""),
        "best_case_load_scale": best.get("load_scale", ""),
        "best_case_max_vm_pu": best.get("max_vm_pu", ""),
        "best_case_max_line_loading_percent": best.get("max_line_loading_percent", ""),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s11_alt_boundary_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, results)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
