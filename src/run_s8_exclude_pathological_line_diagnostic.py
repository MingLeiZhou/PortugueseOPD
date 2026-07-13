"""S8 diagnostic scenario: exclude pathological slack-adjacent line ATPL_00003.

This does not claim the line is invalid in the real Portuguese grid. It creates a
controlled diagnostic branch to test whether excluding the identified pathology
candidate restores ACPF convergence over a load-scale frontier.
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
OUT = ROOT / "data" / "processed" / "acpf_s8_pathology_exclusion"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}
SCENARIO_ID = "S8_EXCLUDE_PATHOLOGICAL_SLACK_ADJACENT_LINE_DIAGNOSTIC"
EXCLUDED_LINE_NAME = "ATPL_00003"


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S8 diagnostic scenario.")
    return net, opts


def apply_s8(net) -> dict[str, Any]:
    mask = net.line["name"].astype(str) == EXCLUDED_LINE_NAME
    excluded_count = int(mask.sum())
    excluded_rows = net.line.loc[mask].copy().reset_index(names="line_index")
    net.line.loc[mask, "in_service"] = False
    opts = getattr(net, "user_pf_options", {}) or {}
    opts.update(
        {
            "scenario_id": SCENARIO_ID,
            "publication_allowed": False,
            "operator_grade_ready": False,
            "opf_ready": False,
            "provenance_policy": "diagnostic_pathology_exclusion",
            "pathology_exclusion_line": EXCLUDED_LINE_NAME,
            "pathology_exclusion_reason": "ATPL_00003 identified as slack-adjacent line causing S5 low-load overvoltage/loading pathology; exclusion is diagnostic only.",
        }
    )
    net.user_pf_options = opts
    return {"excluded_count": excluded_count, "excluded_lines": excluded_rows.to_dict(orient="records")}


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


def run_case(base_net, scale: float, init: str = "dc") -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    set_load(net, scale)
    p_mw, q_mvar = effective_load(net)
    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "attempt_name": f"s8_scale_{scale:.3f}_{init}",
        "load_scale": scale,
        "init": init,
        "effective_p_mw": p_mw,
        "effective_q_mvar": q_mvar,
        "excluded_line_name": EXCLUDED_LINE_NAME,
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
            init=init,
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


def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(attempts: pd.DataFrame, provenance: dict[str, Any], summary: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    converged = attempts[attempts["converged"] == True].copy()
    text = [
        "# 28 S8 Pathological Line Exclusion Diagnostic",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: controlled diagnostic exclusion of `ATPL_00003`. This is not a claim that the real line should be removed; it tests whether this topology/parameter pathology candidate blocks S5 convergence.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Exclusion Provenance",
        "",
        markdown_table(pd.DataFrame([{"excluded_line_name": EXCLUDED_LINE_NAME, "excluded_count": provenance.get("excluded_count", 0), "reason": "S5 targeted sensitivity showed stable convergence through 10% load only when this slack-adjacent line was disabled.", "publication_allowed": False}])),
        "",
        "## Converged Attempts",
        "",
        markdown_table(converged, 80),
        "",
        "## All Attempts",
        "",
        markdown_table(attempts, 120),
        "",
        "## Interpretation",
        "",
        "If S8 converges at higher load scales than S5, the next model-building step should not simply delete the line permanently. Instead, inspect `ATPL_00003` source geometry, endpoint assignment, asset type, length, circuit count, and whether it duplicates or misconnects the slack-side facility. Keep this scenario diagnostic-only until manually validated.",
    ]
    (REPORTS / "28_s8_pathological_line_exclusion_diagnostic.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net, opts = load_net_checked()
    provenance = apply_s8(net)
    pp.to_json(net, str(OUT / "s8_exclude_atpl_00003_pandapower_net.json"))
    pd.DataFrame(provenance["excluded_lines"]).to_csv(OUT / "s8_excluded_lines.csv", index=False)

    scales = [0.02, 0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.00]
    rows = [run_case(net, scale, init="dc") for scale in scales]
    attempts = pd.DataFrame(rows)
    attempts.to_csv(OUT / "s8_acpf_frontier_attempts.csv", index=False)
    converged = attempts[attempts["converged"] == True].copy()
    last_converged = converged.iloc[-1].to_dict() if len(converged) else {}
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_readiness_status": opts.get("readiness_status"),
        "base_scenario_id": opts.get("scenario_id"),
        "scenario_id": SCENARIO_ID,
        "excluded_line_name": EXCLUDED_LINE_NAME,
        "excluded_count": provenance["excluded_count"],
        "attempt_count": int(len(attempts)),
        "converged_count": int(attempts["converged"].sum()),
        "max_converged_load_scale": last_converged.get("load_scale", ""),
        "max_converged_effective_p_mw": last_converged.get("effective_p_mw", ""),
        "max_converged_min_vm_pu": last_converged.get("min_vm_pu", ""),
        "max_converged_max_vm_pu": last_converged.get("max_vm_pu", ""),
        "max_converged_max_line_loading_percent": last_converged.get("max_line_loading_percent", ""),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s8_acpf_frontier_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(attempts, provenance, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
