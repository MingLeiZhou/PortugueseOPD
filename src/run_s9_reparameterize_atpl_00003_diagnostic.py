"""S9 diagnostic scenario: keep ATPL_00003 but reparameterize it by validated length weights.

Manual validation found ATPL_00003 is a real corridor with only ~0.66% underground
segment and ~99.34% overhead segment. This scenario replaces the crude 50/50 mixed
proxy on ATPL_00003 with a length-weighted electrical parameter mix.
"""

from __future__ import annotations

import copy
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

import pandas as pd
import pandapower as pp

READY = ROOT / "data" / "processed" / "acpf_ready"
OUT = ROOT / "data" / "processed" / "acpf_s9_reparameterized_atpl_00003"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}
SCENARIO_ID = "S9_REPARAMETERIZE_ATPL_00003_DIAGNOSTIC"
LINE_NAME = "ATPL_00003"
OH_WEIGHT = 0.9934
CB_WEIGHT = 0.0066


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S9 diagnostic scenario.")
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


def classify_lines(net) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    line = net.line.copy()
    named = line[line["name"].astype(str) == LINE_NAME].copy()
    if named.empty:
        raise RuntimeError(f"Line {LINE_NAME} not found in net.line")
    target_idx = named.index[0]
    target = named.iloc[0]

    overhead_pool = line[(line.index != target_idx) & (line["c_nf_per_km"] < 50)].copy()
    cable_pool = line[(line.index != target_idx) & (line["c_nf_per_km"] >= 250)].copy()
    if overhead_pool.empty or cable_pool.empty:
        raise RuntimeError("Could not infer overhead/cable parameter pools from existing net lines.")
    return target.to_frame().T, overhead_pool, cable_pool


def apply_s9(net) -> dict[str, Any]:
    target_df, overhead_pool, cable_pool = classify_lines(net)
    idx = target_df.index[0]
    overhead = overhead_pool.sort_values("length_km", ascending=False).iloc[0]
    cable = cable_pool.sort_values("length_km", ascending=False).iloc[0]

    def weighted(col: str) -> float:
        return OH_WEIGHT * float(overhead[col]) + CB_WEIGHT * float(cable[col])

    net.line.loc[idx, "r_ohm_per_km"] = weighted("r_ohm_per_km")
    net.line.loc[idx, "x_ohm_per_km"] = weighted("x_ohm_per_km")
    net.line.loc[idx, "c_nf_per_km"] = weighted("c_nf_per_km")
    net.line.loc[idx, "max_i_ka"] = min(float(overhead["max_i_ka"]), float(cable["max_i_ka"]))

    opts = getattr(net, "user_pf_options", {}) or {}
    opts.update(
        {
            "scenario_id": SCENARIO_ID,
            "publication_allowed": False,
            "operator_grade_ready": False,
            "opf_ready": False,
            "provenance_policy": "diagnostic_reparameterized_atpl_00003",
            "atpl_00003_reparameterization": {
                "overhead_weight": OH_WEIGHT,
                "cable_weight": CB_WEIGHT,
                "reason": "Manual validation report 31 found underground segment ~0.66% and overhead segment ~99.34%; replace 50/50 mixed proxy with length-weighted proxy.",
                "source_overhead_line": str(overhead["name"]),
                "source_cable_line": str(cable["name"]),
            },
        }
    )
    net.user_pf_options = opts

    return {
        "line_name": LINE_NAME,
        "line_index": int(idx),
        "overhead_weight": OH_WEIGHT,
        "cable_weight": CB_WEIGHT,
        "old_r_ohm_per_km": float(target_df.iloc[0]["r_ohm_per_km"]),
        "old_x_ohm_per_km": float(target_df.iloc[0]["x_ohm_per_km"]),
        "old_c_nf_per_km": float(target_df.iloc[0]["c_nf_per_km"]),
        "new_r_ohm_per_km": float(net.line.loc[idx, "r_ohm_per_km"]),
        "new_x_ohm_per_km": float(net.line.loc[idx, "x_ohm_per_km"]),
        "new_c_nf_per_km": float(net.line.loc[idx, "c_nf_per_km"]),
        "new_max_i_ka": float(net.line.loc[idx, "max_i_ka"]),
        "overhead_reference_line": str(overhead["name"]),
        "cable_reference_line": str(cable["name"]),
    }


def run_case(base_net, scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    set_load(net, scale)
    p_mw, q_mvar = effective_load(net)
    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "attempt_name": f"s9_scale_{scale:.3f}_dc",
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


def write_report(summary: dict[str, Any], param_change: dict[str, Any], attempts: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 32 S9 Reparameterize ATPL_00003 Diagnostic",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: keep `ATPL_00003` in service but replace the crude 50/50 mixed proxy with a manual-validation-backed length-weighted mixed electrical proxy. Diagnostic only.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Parameter Change",
        "",
        markdown_table(pd.DataFrame([param_change])),
        "",
        "## Frontier Attempts",
        "",
        markdown_table(attempts, 80),
        "",
        "## Interpretation",
        "",
        "If S9 recovers stable convergence without excluding the line, then `ATPL_00003` should remain in topology but must not use the 50/50 mixed proxy. The next step would be to upstream this weighted mixed treatment or segment split into scenario generation.",
    ]
    (REPORTS / "32_s9_reparameterize_atpl_00003_diagnostic.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net, opts = load_net_checked()
    param_change = apply_s9(net)
    pp.to_json(net, str(OUT / "s9_reparameterized_atpl_00003_pandapower_net.json"))
    pd.DataFrame([param_change]).to_csv(OUT / "s9_atpl_00003_parameter_change.csv", index=False)

    scales = [0.02, 0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.00]
    rows = [run_case(net, scale) for scale in scales]
    attempts = pd.DataFrame(rows)
    attempts.to_csv(OUT / "s9_acpf_frontier_attempts.csv", index=False)
    converged = attempts[attempts["converged"] == True].copy()
    last_converged = converged.iloc[-1].to_dict() if len(converged) else {}
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_readiness_status": opts.get("readiness_status"),
        "scenario_id": SCENARIO_ID,
        "line_name": LINE_NAME,
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
    (OUT / "s9_acpf_frontier_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, param_change, attempts)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
