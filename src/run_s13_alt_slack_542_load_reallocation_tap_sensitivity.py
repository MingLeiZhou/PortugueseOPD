"""S13 diagnostic scenario: S12 base with transformer/tap/Q support sensitivities."""

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
OUT = ROOT / "data" / "processed" / "acpf_s13_alt_slack_542_tap_sensitivity"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}
SCENARIO_ID = "S13_ALT_SLACK_542_LOAD_REALLOCATION_TAP_SENSITIVITY"
SLACK_BUS = 542
SUGGESTION_PATH = LOAD_VALIDATION / "pt_load_reallocation_suggestions.csv"


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S13 diagnostic scenario.")
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
    pp.create_ext_grid(net, bus=SLACK_BUS, vm_pu=1.0, va_degree=0.0, name=f"S13_ext_grid_{SLACK_BUS}")


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


def add_shunt_support(net, q_mvar_per_load_bus: float = 2.0) -> int:
    if not len(net.load):
        return 0
    added = 0
    buses = sorted(set(int(x) for x in net.load.loc[net.load.in_service, "bus"]))
    for bus in buses:
        pp.create_shunt(net, bus=bus, q_mvar=-abs(q_mvar_per_load_bus), p_mw=0.0, name=f"s13_shunt_{bus}")
        added += 1
    return added


def apply_variant(net, variant: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"shunts_added": 0}
    if "tap_pos" in variant and len(net.trafo):
        if "tap_pos" in net.trafo.columns:
            net.trafo["tap_pos"] = variant["tap_pos"]
        out["tap_pos"] = variant["tap_pos"]
    if "trafo_xr" in variant and len(net.trafo):
        vk = pd.to_numeric(net.trafo["vk_percent"], errors="coerce")
        xr = float(variant["trafo_xr"])
        net.trafo["vkr_percent"] = vk / math.sqrt(1.0 + xr**2)
        out["trafo_xr"] = xr
    if variant.get("q_support"):
        out["shunts_added"] = add_shunt_support(net, float(variant.get("shunt_q_mvar_per_bus", 2.0)))
    return out


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
            "provenance_policy": "diagnostic_alt_slack_542_load_reallocation_tap_sensitivity",
            "load_reallocation_summary": suggestion_summary,
        }
    )
    net.user_pf_options = opts
    return net, suggestion_summary


def run_case(base_net, variant: dict[str, Any], scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    set_scale(net, scale)
    variant_effects = apply_variant(net, variant)
    p_mw, q_mvar = effective_load(net)
    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "variant_id": variant["variant_id"],
        "variant_detail": variant["detail"],
        "slack_bus": SLACK_BUS,
        "load_scale": scale,
        "effective_p_mw": p_mw,
        "effective_q_mvar": q_mvar,
        "tap_pos": variant_effects.get("tap_pos", ""),
        "trafo_xr": variant_effects.get("trafo_xr", ""),
        "shunts_added": variant_effects.get("shunts_added", 0),
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


def variant_plan() -> list[dict[str, Any]]:
    return [
        {"variant_id": "baseline", "detail": "S12 baseline with slack 542 and load reallocation"},
        {"variant_id": "tap_plus_2", "detail": "Set all transformer tap positions to +2", "tap_pos": 2},
        {"variant_id": "tap_plus_4", "detail": "Set all transformer tap positions to +4", "tap_pos": 4},
        {"variant_id": "xr_10", "detail": "Set transformer X/R to 10", "trafo_xr": 10},
        {"variant_id": "xr_40", "detail": "Set transformer X/R to 40", "trafo_xr": 40},
        {"variant_id": "tap_plus_2_shunt", "detail": "Tap +2 plus shunt Q support", "tap_pos": 2, "q_support": True, "shunt_q_mvar_per_bus": 2.0},
        {"variant_id": "shunt_only", "detail": "Shunt Q support only", "q_support": True, "shunt_q_mvar_per_bus": 2.0},
    ]


def markdown_table(df: pd.DataFrame, max_rows: int = 120) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary: dict[str, Any], suggestion_summary: dict[str, Any], results: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    by_variant = results.groupby("variant_id")["converged"].agg(attempts="count", converged="sum").reset_index()
    converged = results[results["converged"] == True].copy()
    if len(converged):
        best = converged.assign(score=(pd.to_numeric(converged["min_vm_pu"], errors="coerce") - 0.95).abs() + (pd.to_numeric(converged["max_vm_pu"], errors="coerce") - 1.0).abs()).sort_values(["load_scale", "score"], ascending=[False, True])
    else:
        best = converged
    text = [
        "# 36 S13 Alt Slack 542 Load Reallocation Tap Sensitivity",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: build on S12 by testing transformer tap bias, transformer X/R sensitivity, and shunt/Q support. Diagnostic only.",
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
        "## Convergence By Variant",
        "",
        markdown_table(by_variant, 40),
        "",
        "## Best Converged Attempts",
        "",
        markdown_table(best, 80),
        "",
        "## All Attempts",
        "",
        markdown_table(results, 200),
    ]
    (REPORTS / "36_s13_alt_slack_542_load_reallocation_tap_sensitivity.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_net, opts = load_net_checked()
    suggestions = load_suggestions()
    net, suggestion_summary = prepare_net(base_net, suggestions)
    scales = [0.05, 0.10, 0.20]
    variants = variant_plan()
    rows = [run_case(net, variant, scale) for variant in variants for scale in scales]
    results = pd.DataFrame(rows)
    results.to_csv(OUT / "s13_tap_sensitivity_attempts.csv", index=False)
    converged = results[results["converged"] == True].copy()
    best = {}
    if len(converged):
        tmp = converged.assign(score=(pd.to_numeric(converged["min_vm_pu"], errors="coerce") - 0.95).abs() + (pd.to_numeric(converged["max_vm_pu"], errors="coerce") - 1.0).abs())
        best = tmp.sort_values(["load_scale", "score"], ascending=[False, True]).iloc[0].to_dict()
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_readiness_status": opts.get("readiness_status"),
        "scenario_id": SCENARIO_ID,
        "slack_bus": SLACK_BUS,
        "variant_count": len(variants),
        "attempt_count": int(len(results)),
        "converged_count": int(results["converged"].sum()),
        "best_variant_id": best.get("variant_id", ""),
        "best_variant_load_scale": best.get("load_scale", ""),
        "best_variant_min_vm_pu": best.get("min_vm_pu", ""),
        "best_variant_max_vm_pu": best.get("max_vm_pu", ""),
        "best_variant_max_line_loading_percent": best.get("max_line_loading_percent", ""),
        "modified_load_count": suggestion_summary.get("modified_load_count", 0),
        "total_p_before": suggestion_summary.get("total_p_before", 0.0),
        "total_p_after": suggestion_summary.get("total_p_after", 0.0),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s13_tap_sensitivity_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, suggestion_summary, results)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
