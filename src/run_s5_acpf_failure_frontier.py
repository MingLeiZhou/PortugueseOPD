"""Focused low-load ACPF failure frontier for S5 diagnostic readiness.

This script tests whether the S5 best-available multilingual diagnostic net can
converge at very small load scales. It is diagnostic-only and preserves the
fail-closed readiness check.
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
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S5 failure frontier.")
    return net, opts


def set_power_factor(net, pf: float | None) -> None:
    if not len(net.load):
        return
    if pf is None:
        net.load["q_mvar"] = 0.0
    else:
        net.load["q_mvar"] = net.load["p_mw"] * math.tan(math.acos(pf))


def effective_load(net) -> tuple[float, float]:
    if not len(net.load):
        return 0.0, 0.0
    active = net.load[net.load.in_service].copy()
    scaling = pd.to_numeric(active.get("scaling", 1.0), errors="coerce").fillna(1.0)
    p = float((pd.to_numeric(active["p_mw"], errors="coerce").fillna(0.0) * scaling).sum())
    q = float((pd.to_numeric(active["q_mvar"], errors="coerce").fillna(0.0) * scaling).sum())
    return p, q


def net_counts(net) -> dict[str, int]:
    return {
        "bus_count": int(len(net.bus)),
        "line_count": int(len(net.line)),
        "trafo_count": int(len(net.trafo)),
        "load_count": int(len(net.load)),
        "ext_grid_count": int(len(net.ext_grid)),
        "active_bus_count": int(net.bus["in_service"].sum()) if "in_service" in net.bus else int(len(net.bus)),
        "active_line_count": int(net.line["in_service"].sum()) if "in_service" in net.line else int(len(net.line)),
        "active_trafo_count": int(net.trafo["in_service"].sum()) if "in_service" in net.trafo else int(len(net.trafo)),
        "active_load_count": int(net.load["in_service"].sum()) if "in_service" in net.load else int(len(net.load)),
    }


def run_attempt(base_net, attempt: dict[str, Any]) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    if len(net.load):
        net.load["scaling"] = float(attempt["load_scale"])
    if "pf" in attempt:
        set_power_factor(net, attempt["pf"])

    p_mw, q_mvar = effective_load(net)
    row = {
        **attempt,
        **net_counts(net),
        "effective_p_mw": p_mw,
        "effective_q_mvar": q_mvar,
        "selected_slack_bus": int(net.ext_grid.iloc[0]["bus"]) if len(net.ext_grid) else "",
        "converged": False,
        "error_type": "",
        "error": "",
        "min_vm_pu": "",
        "max_vm_pu": "",
        "max_line_loading_percent": "",
        "max_trafo_loading_percent": "",
        "total_line_losses_mw": "",
        "total_trafo_losses_mw": "",
        "publication_allowed": False,
        "diagnostic_only": True,
    }
    settings = {
        "algorithm": attempt["algorithm"],
        "init": attempt["init"],
        "calculate_voltage_angles": True,
        "enforce_q_lims": False,
        "numba": False,
        "max_iteration": int(attempt.get("max_iteration", 120)),
        "tolerance_mva": 1e-6,
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
    scales = [0.0, 0.005, 0.01, 0.02, 0.03, 0.05, 0.075, 0.10]
    attempts: list[dict[str, Any]] = []
    for scale in scales:
        attempts.append({"level": "DENSE_LOW_LOAD", "attempt_name": f"nr_flat_scale_{scale:.3f}", "algorithm": "nr", "init": "flat", "load_scale": scale, "pf": 0.95, "max_iteration": 120})
    for scale in scales:
        attempts.append({"level": "DENSE_LOW_LOAD_DC_INIT", "attempt_name": f"nr_dc_scale_{scale:.3f}", "algorithm": "nr", "init": "dc", "load_scale": scale, "pf": 0.95, "max_iteration": 120})
    for scale in scales:
        attempts.append({"level": "DENSE_Q_ZERO", "attempt_name": f"nr_flat_q_zero_scale_{scale:.3f}", "algorithm": "nr", "init": "flat", "load_scale": scale, "pf": None, "max_iteration": 120})
    return attempts


def markdown_table(df: pd.DataFrame, max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(attempts: pd.DataFrame, summary: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    by_level = attempts.groupby("level")["converged"].agg(attempts="count", converged="sum").reset_index()
    by_scale = attempts.groupby("load_scale")["converged"].agg(attempts="count", converged="sum").reset_index()
    cols = ["level", "attempt_name", "algorithm", "init", "load_scale", "pf", "effective_p_mw", "effective_q_mvar", "converged", "error_type", "error", "min_vm_pu", "max_vm_pu", "max_line_loading_percent", "max_trafo_loading_percent"]
    text = [
        "# 24 S5 Dense ACPF Failure Frontier",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: focused low-load diagnostic frontier for `S5_BEST_AVAILABLE_MULTILINGUAL_DIAGNOSTIC`. This is not publication-grade PF validation.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Attempts By Level",
        "",
        markdown_table(by_level),
        "",
        "## Attempts By Load Scale",
        "",
        markdown_table(by_scale),
        "",
        "## Attempt Details",
        "",
        markdown_table(attempts[cols], max_rows=80),
    ]
    (REPORTS / "24_s5_acpf_failure_frontier.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net, opts = load_net_checked()
    rows = [run_attempt(net, attempt) for attempt in attempt_plan()]
    attempts = pd.DataFrame(rows)
    attempts.to_csv(OUT / "s5_acpf_dense_failure_frontier_attempts.csv", index=False)
    converged = attempts[attempts["converged"] == True].copy()
    first = converged.iloc[0].to_dict() if len(converged) else {}
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_status": opts.get("readiness_status"),
        "scenario_id": opts.get("scenario_id"),
        "attempt_count": int(len(attempts)),
        "converged_count": int(attempts["converged"].sum()),
        "first_converged_attempt": first.get("attempt_name", ""),
        "first_converged_load_scale": first.get("load_scale", ""),
        "first_converged_effective_p_mw": first.get("effective_p_mw", ""),
        "zero_load_converged": bool(attempts[(attempts["load_scale"] == 0.0) & (attempts["converged"] == True)].shape[0]),
        "lowest_tested_nonzero_load_scale": 0.005,
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s5_acpf_dense_failure_frontier_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "s5_acpf_dense_failure_frontier_first_converged.json").write_text(json.dumps(first, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(attempts, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
