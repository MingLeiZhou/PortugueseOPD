"""S10 diagnostic scenario: split ATPL_00003 into two branches with an intermediate bus.

This is a representation diagnostic, not a claim about the exact real switching
layout. It tests whether replacing the single lumped slack-adjacent branch with a
split equivalent improves solvability while preserving the corridor.
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
OUT = ROOT / "data" / "processed" / "acpf_s10_split_atpl_00003"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}
SCENARIO_ID = "S10_SPLIT_ATPL_00003_DIAGNOSTIC"
LINE_NAME = "ATPL_00003"
TOTAL_LENGTH_KM = 16.484278
CABLE_LENGTH_KM = 0.110
OVERHEAD_LENGTH_KM = TOTAL_LENGTH_KM - CABLE_LENGTH_KM


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S10 diagnostic scenario.")
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


def apply_s10(net) -> dict[str, Any]:
    overhead, cable, idx = infer_reference_lines(net)
    original = net.line.loc[idx].copy()

    from_bus = int(original["from_bus"])
    to_bus = int(original["to_bus"])
    new_bus_name = f"{LINE_NAME}_SPLIT_JUNCTION"
    new_bus = pp.create_bus(
        net,
        vn_kv=float(net.bus.loc[from_bus, "vn_kv"]),
        name=new_bus_name,
        zone="diagnostic_split",
        in_service=True,
        max_vm_pu=float(net.bus.loc[from_bus, "max_vm_pu"]) if "max_vm_pu" in net.bus.columns else 1.05,
        min_vm_pu=float(net.bus.loc[from_bus, "min_vm_pu"]) if "min_vm_pu" in net.bus.columns else 0.95,
    )
    net.line.loc[idx, "in_service"] = False

    pp.create_line_from_parameters(
        net,
        from_bus=from_bus,
        to_bus=new_bus,
        length_km=OVERHEAD_LENGTH_KM,
        r_ohm_per_km=float(overhead["r_ohm_per_km"]),
        x_ohm_per_km=float(overhead["x_ohm_per_km"]),
        c_nf_per_km=float(overhead["c_nf_per_km"]),
        max_i_ka=float(overhead["max_i_ka"]),
        name=f"{LINE_NAME}_OH_SPLIT",
        type="ol",
    )
    pp.create_line_from_parameters(
        net,
        from_bus=new_bus,
        to_bus=to_bus,
        length_km=CABLE_LENGTH_KM,
        r_ohm_per_km=float(cable["r_ohm_per_km"]),
        x_ohm_per_km=float(cable["x_ohm_per_km"]),
        c_nf_per_km=float(cable["c_nf_per_km"]),
        max_i_ka=float(cable["max_i_ka"]),
        name=f"{LINE_NAME}_CB_SPLIT",
        type="cs",
    )

    opts = getattr(net, "user_pf_options", {}) or {}
    opts.update(
        {
            "scenario_id": SCENARIO_ID,
            "publication_allowed": False,
            "operator_grade_ready": False,
            "opf_ready": False,
            "provenance_policy": "diagnostic_split_atpl_00003",
            "atpl_00003_split": {
                "overhead_length_km": OVERHEAD_LENGTH_KM,
                "cable_length_km": CABLE_LENGTH_KM,
                "reason": "Manual validation report 31 found ATPL_00003 is a real corridor with only ~0.110 km underground segment and ~16.374 km overhead segment; replace one lumped branch with split diagnostic representation.",
                "overhead_reference_line": str(overhead["name"]),
                "cable_reference_line": str(cable["name"]),
            },
        }
    )
    net.user_pf_options = opts

    return {
        "excluded_original_line_name": LINE_NAME,
        "excluded_original_line_index": idx,
        "split_bus_index": int(new_bus),
        "split_bus_name": new_bus_name,
        "overhead_length_km": OVERHEAD_LENGTH_KM,
        "cable_length_km": CABLE_LENGTH_KM,
        "overhead_reference_line": str(overhead["name"]),
        "cable_reference_line": str(cable["name"]),
    }


def run_case(base_net, scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    set_load(net, scale)
    p_mw, q_mvar = effective_load(net)
    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "attempt_name": f"s10_scale_{scale:.3f}_dc",
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


def write_report(summary: dict[str, Any], split_info: dict[str, Any], attempts: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 33 S10 Split ATPL_00003 Diagnostic",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: replace the single lumped `ATPL_00003` branch with a diagnostic split representation that preserves the corridor but separates the validated short cable portion from the dominant overhead portion.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Split Definition",
        "",
        markdown_table(pd.DataFrame([split_info])),
        "",
        "## Frontier Attempts",
        "",
        markdown_table(attempts, 80),
        "",
        "## Interpretation",
        "",
        "If S10 converges at materially higher load than S5 and S9, the evidence favors changing the representation of ATPL_00003 rather than excluding it entirely. If S10 still fails, then the unresolved issue is likely not just branch lumping but how this corridor interacts with the slack-side boundary representation.",
    ]
    (REPORTS / "33_s10_split_atpl_00003_diagnostic.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net, opts = load_net_checked()
    split_info = apply_s10(net)
    pp.to_json(net, str(OUT / "s10_split_atpl_00003_pandapower_net.json"))
    pd.DataFrame([split_info]).to_csv(OUT / "s10_split_definition.csv", index=False)

    scales = [0.02, 0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.00]
    rows = [run_case(net, scale) for scale in scales]
    attempts = pd.DataFrame(rows)
    attempts.to_csv(OUT / "s10_acpf_frontier_attempts.csv", index=False)
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
    (OUT / "s10_acpf_frontier_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, split_info, attempts)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
