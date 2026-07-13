"""S17 diagnostic scenario: reparameterize ATPL_00075 using validated length shares."""

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
OUT = ROOT / "data" / "processed" / "acpf_s17_reparameterize_atpl_00075"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}
SCENARIO_ID = "S17_REPARAMETERIZE_ATPL_00075_DIAGNOSTIC"
SLACK_BUS = 542
SUGGESTION_PATH = LOAD_VALIDATION / "pt_load_reallocation_suggestions.csv"
LINE_NAME = "ATPL_00075"
OH_WEIGHT = 0.742
CB_WEIGHT = 0.258


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S17 diagnostic scenario.")
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
    pp.create_ext_grid(net, bus=SLACK_BUS, vm_pu=1.0, va_degree=0.0, name=f"S17_ext_grid_{SLACK_BUS}")


def apply_suggestions(net, suggestions: pd.DataFrame) -> dict[str, Any]:
    if not len(net.load):
        return {"modified_load_count": 0, "total_p_before": 0.0, "total_p_after": 0.0}
    load = net.load.copy()
    before = float(pd.to_numeric(load["p_mw"], errors="coerce").fillna(0.0).sum())
    modified = 0
    for _, row in suggestions.iterrows():
        load_id = str(row.get("load_id", ""))
        scale = float(row.get("suggested_scale", 1.0))
        mask = load["name"].astype(str) == load_id
        if mask.any():
            load.loc[mask, "p_mw"] = pd.to_numeric(load.loc[mask, "p_mw"], errors="coerce") * scale
            load.loc[mask, "q_mvar"] = pd.to_numeric(load.loc[mask, "q_mvar"], errors="coerce") * scale
            modified += int(mask.sum())
    after = float(pd.to_numeric(load["p_mw"], errors="coerce").fillna(0.0).sum())
    net.load = load
    return {"modified_load_count": modified, "total_p_before": before, "total_p_after": after}


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


def infer_reference_lines(net) -> tuple[pd.Series, pd.Series, int]:
    line = net.line.copy()
    target = line[line["name"].astype(str) == LINE_NAME]
    if target.empty:
        raise RuntimeError(f"Line {LINE_NAME} not found")
    idx = int(target.index[0])
    cable_pool = line[(line.index != idx) & (pd.to_numeric(line["c_nf_per_km"], errors="coerce") >= 250)].copy()
    overhead_pool = line[(line.index != idx) & (pd.to_numeric(line["c_nf_per_km"], errors="coerce") < 50)].copy()
    if cable_pool.empty or overhead_pool.empty:
        raise RuntimeError("Could not infer cable/overhead pools from existing line table.")
    cable = cable_pool.sort_values("length_km", ascending=False).iloc[0]
    overhead = overhead_pool.sort_values("length_km", ascending=False).iloc[0]
    return overhead, cable, idx


def prepare_net(base_net, suggestions: pd.DataFrame) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    net = copy.deepcopy(base_net)
    set_slack(net)
    suggestion_summary = apply_suggestions(net, suggestions)
    set_pf(net, 0.95)
    overhead, cable, idx = infer_reference_lines(net)
    old = net.line.loc[idx].copy()

    weighted_r = OH_WEIGHT * float(overhead["r_ohm_per_km"]) + CB_WEIGHT * float(cable["r_ohm_per_km"])
    weighted_x = OH_WEIGHT * float(overhead["x_ohm_per_km"]) + CB_WEIGHT * float(cable["x_ohm_per_km"])
    weighted_c = OH_WEIGHT * float(overhead["c_nf_per_km"]) + CB_WEIGHT * float(cable["c_nf_per_km"])
    conservative_i = min(float(overhead["max_i_ka"]), float(cable["max_i_ka"]))
    weighted_i = OH_WEIGHT * float(overhead["max_i_ka"]) + CB_WEIGHT * float(cable["max_i_ka"])

    net.line.loc[idx, "r_ohm_per_km"] = weighted_r
    net.line.loc[idx, "x_ohm_per_km"] = weighted_x
    net.line.loc[idx, "c_nf_per_km"] = weighted_c

    opts = getattr(net, "user_pf_options", {}) or {}
    opts.update(
        {
            "scenario_id": SCENARIO_ID,
            "publication_allowed": False,
            "operator_grade_ready": False,
            "opf_ready": False,
            "provenance_policy": "diagnostic_reparameterize_atpl_00075",
            "atpl_00075_reparameterization": {
                "overhead_weight": OH_WEIGHT,
                "cable_weight": CB_WEIGHT,
                "weighted_r_ohm_per_km": weighted_r,
                "weighted_x_ohm_per_km": weighted_x,
                "weighted_c_nf_per_km": weighted_c,
                "conservative_i_ka": conservative_i,
                "weighted_i_ka": weighted_i,
                "reason": "Manual validation report 42 found ATPL_00075 is a real corridor with overhead ~74.2% and underground ~25.8%; replace 50/50 mixed proxy with length-weighted proxy.",
                "overhead_reference_line": str(overhead["name"]),
                "cable_reference_line": str(cable["name"]),
            },
        }
    )
    net.user_pf_options = opts

    param_summary = {
        "line_index": idx,
        "old_r_ohm_per_km": float(old["r_ohm_per_km"]),
        "old_x_ohm_per_km": float(old["x_ohm_per_km"]),
        "old_c_nf_per_km": float(old["c_nf_per_km"]),
        "weighted_r_ohm_per_km": weighted_r,
        "weighted_x_ohm_per_km": weighted_x,
        "weighted_c_nf_per_km": weighted_c,
        "conservative_i_ka": conservative_i,
        "weighted_i_ka": weighted_i,
        "overhead_reference_line": str(overhead["name"]),
        "cable_reference_line": str(cable["name"]),
    }
    return net, suggestion_summary, param_summary


def run_case(base_net, variant: str, scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    set_scale(net, scale)
    if len(net.line):
        target_idx = int(net.line[net.line["name"].astype(str) == LINE_NAME].index[0])
        i_cfg = getattr(net, "user_pf_options", {}).get("atpl_00075_reparameterization", {})
        if variant == "conservative_i":
            net.line.loc[target_idx, "max_i_ka"] = float(i_cfg.get("conservative_i_ka"))
        elif variant == "weighted_i":
            net.line.loc[target_idx, "max_i_ka"] = float(i_cfg.get("weighted_i_ka"))
        else:
            raise ValueError(f"Unknown variant {variant}")
    p_mw, q_mvar = effective_load(net)
    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "variant_id": variant,
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


def write_report(summary: dict[str, Any], suggestion_summary: dict[str, Any], param_summary: dict[str, Any], results: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    by_variant = results.groupby("variant_id")["converged"].agg(attempts="count", converged="sum").reset_index()
    text = [
        "# 43 S17 Reparameterize ATPL_00075 Diagnostic",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: keep the depth-6 backbone setup and replace the 50/50 proxy on ATPL_00075 with a manual-validation-backed length-weighted mixed parameterization. Diagnostic only.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Load Reallocation Summary",
        "",
        markdown_table(pd.DataFrame([suggestion_summary])),
        "",
        "## Parameter Summary",
        "",
        markdown_table(pd.DataFrame([param_summary])),
        "",
        "## Convergence By Variant",
        "",
        markdown_table(by_variant),
        "",
        "## All Attempts",
        "",
        markdown_table(results, 120),
    ]
    (REPORTS / "43_s17_reparameterize_atpl_00075_diagnostic.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_net, opts = load_net_checked()
    suggestions = load_suggestions()
    net, suggestion_summary, param_summary = prepare_net(base_net, suggestions)
    pp.to_json(net, str(OUT / "s17_reparameterized_atpl_00075_net.json"))
    pd.DataFrame([param_summary]).to_csv(OUT / "s17_atpl_00075_parameter_summary.csv", index=False)

    variants = ["conservative_i", "weighted_i"]
    scales = [0.10, 0.20, 0.30, 0.50]
    rows = [run_case(net, variant, scale) for variant in variants for scale in scales]
    results = pd.DataFrame(rows)
    results.to_csv(OUT / "s17_acpf_frontier_attempts.csv", index=False)

    converged = results[results["converged"] == True].copy()
    best = {}
    if len(converged):
        best = converged.sort_values(["load_scale", "min_vm_pu", "max_line_loading_percent"], ascending=[False, False, True]).iloc[0].to_dict()
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_readiness_status": opts.get("readiness_status"),
        "scenario_id": SCENARIO_ID,
        "variant_count": len(variants),
        "attempt_count": int(len(results)),
        "converged_count": int(results["converged"].sum()),
        "best_variant_id": best.get("variant_id", ""),
        "best_load_scale": best.get("load_scale", ""),
        "best_min_vm_pu": best.get("min_vm_pu", ""),
        "best_max_vm_pu": best.get("max_vm_pu", ""),
        "best_max_line_loading_percent": best.get("max_line_loading_percent", ""),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s17_acpf_frontier_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, suggestion_summary, param_summary, results)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
