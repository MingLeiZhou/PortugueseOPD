"""Diagnose S5 low-load ACPF numerical/electrical pathologies.

Runs the converged S5 low-load cases and writes ranked tables for overloaded
lines, high-voltage buses, and charging/length proxies. Diagnostic-only.
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
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S5 pathology diagnostics.")
    return net, opts


def set_pf(net, pf: float) -> None:
    if len(net.load):
        net.load["q_mvar"] = net.load["p_mw"] * math.tan(math.acos(pf))


def run_case(base_net, name: str, load_scale: float, init: str) -> tuple[Any, dict[str, Any]]:
    net = copy.deepcopy(base_net)
    if len(net.load):
        net.load["scaling"] = load_scale
    set_pf(net, 0.95)
    settings = {
        "algorithm": "nr",
        "init": init,
        "calculate_voltage_angles": True,
        "enforce_q_lims": False,
        "numba": False,
        "max_iteration": 120,
        "tolerance_mva": 1e-6,
    }
    pp.runpp(net, **settings)
    active = net.load[net.load.in_service].copy() if len(net.load) else pd.DataFrame()
    scaling = pd.to_numeric(active.get("scaling", 1.0), errors="coerce").fillna(1.0) if len(active) else pd.Series(dtype=float)
    summary = {
        "case_name": name,
        "load_scale": load_scale,
        "init": init,
        "converged": bool(net.converged),
        "effective_p_mw": float((pd.to_numeric(active.get("p_mw", pd.Series(dtype=float)), errors="coerce").fillna(0.0) * scaling).sum()) if len(active) else 0.0,
        "effective_q_mvar": float((pd.to_numeric(active.get("q_mvar", pd.Series(dtype=float)), errors="coerce").fillna(0.0) * scaling).sum()) if len(active) else 0.0,
        "min_vm_pu": float(net.res_bus["vm_pu"].min()) if len(net.res_bus) else "",
        "max_vm_pu": float(net.res_bus["vm_pu"].max()) if len(net.res_bus) else "",
        "max_line_loading_percent": float(net.res_line["loading_percent"].max()) if len(net.res_line) else "",
        "max_trafo_loading_percent": float(net.res_trafo["loading_percent"].max()) if len(net.res_trafo) else "",
    }
    return net, summary


def line_diagnostics(net, case_name: str) -> pd.DataFrame:
    line = net.line.copy().reset_index(names="line_index")
    res = net.res_line.copy().reset_index(names="line_index")
    merged = line.merge(res, on="line_index", how="left", suffixes=("", "_res"))
    for col in ["length_km", "c_nf_per_km", "max_i_ka", "loading_percent", "q_from_mvar", "q_to_mvar", "p_from_mw", "p_to_mw"]:
        if col in merged:
            merged[col] = pd.to_numeric(merged[col], errors="coerce")
    merged["case_name"] = case_name
    merged["charging_proxy_nf"] = merged["length_km"].fillna(0.0) * merged["c_nf_per_km"].fillna(0.0)
    merged["abs_q_total_mvar"] = merged.get("q_from_mvar", 0.0).abs().fillna(0.0) + merged.get("q_to_mvar", 0.0).abs().fillna(0.0)
    merged["abs_p_total_mw"] = merged.get("p_from_mw", 0.0).abs().fillna(0.0) + merged.get("p_to_mw", 0.0).abs().fillna(0.0)
    keep = [
        "case_name",
        "line_index",
        "name",
        "from_bus",
        "to_bus",
        "length_km",
        "r_ohm_per_km",
        "x_ohm_per_km",
        "c_nf_per_km",
        "max_i_ka",
        "loading_percent",
        "p_from_mw",
        "q_from_mvar",
        "p_to_mw",
        "q_to_mvar",
        "pl_mw",
        "ql_mvar",
        "charging_proxy_nf",
        "abs_q_total_mvar",
        "abs_p_total_mw",
    ]
    return merged[[c for c in keep if c in merged.columns]].copy()


def bus_diagnostics(net, case_name: str) -> pd.DataFrame:
    bus = net.bus.copy().reset_index(names="bus_index")
    res = net.res_bus.copy().reset_index(names="bus_index")
    merged = bus.merge(res, on="bus_index", how="left", suffixes=("", "_res"))
    merged["case_name"] = case_name
    keep = ["case_name", "bus_index", "name", "vn_kv", "in_service", "vm_pu", "va_degree", "p_mw", "q_mvar"]
    return merged[[c for c in keep if c in merged.columns]].copy()


def markdown_table(df: pd.DataFrame, max_rows: int = 30) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary_df: pd.DataFrame, top_lines: pd.DataFrame, top_charging: pd.DataFrame, top_buses: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 25 S5 ACPF Pathology Diagnosis",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "Scope: diagnose converged low-load S5 diagnostic cases. These findings are not publication-grade validation.",
        "",
        "## Case Summary",
        "",
        markdown_table(summary_df),
        "",
        "## Top Line Loading / Flow Pathologies",
        "",
        markdown_table(top_lines, 40),
        "",
        "## Top Charging Proxy Lines",
        "",
        markdown_table(top_charging, 40),
        "",
        "## Highest Voltage Buses",
        "",
        markdown_table(top_buses, 40),
        "",
        "## Interpretation",
        "",
        "The zero-load case converges but shows near-zero minimum voltage and very high line loading, while 2-3% load cases can converge only with DC initialization and show voltage magnitudes far above normal limits. This points to numerical/electrical pathology in line charging, branch length/capacitance/current combinations, or topology around high-voltage buses rather than simple full-load stress.",
    ]
    (REPORTS / "25_s5_acpf_pathology_diagnosis.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_net, opts = load_net_checked()
    cases = [
        ("zero_load_flat", 0.0, "flat"),
        ("low_load_002_dc", 0.02, "dc"),
        ("low_load_003_dc", 0.03, "dc"),
    ]
    summaries: list[dict[str, Any]] = []
    line_frames: list[pd.DataFrame] = []
    bus_frames: list[pd.DataFrame] = []
    for name, scale, init in cases:
        net, summary = run_case(base_net, name, scale, init)
        summaries.append(summary)
        line_frames.append(line_diagnostics(net, name))
        bus_frames.append(bus_diagnostics(net, name))

    summary_df = pd.DataFrame(summaries)
    lines = pd.concat(line_frames, ignore_index=True)
    buses = pd.concat(bus_frames, ignore_index=True)

    top_lines = lines.sort_values("loading_percent", ascending=False).head(50)
    top_charging = lines.sort_values(["charging_proxy_nf", "abs_q_total_mvar"], ascending=False).head(50)
    top_buses = buses.sort_values("vm_pu", ascending=False).head(50)

    summary_df.to_csv(OUT / "s5_pathology_case_summary.csv", index=False)
    top_lines.to_csv(OUT / "s5_top_line_pathologies.csv", index=False)
    top_charging.to_csv(OUT / "s5_top_charging_proxy_lines.csv", index=False)
    top_buses.to_csv(OUT / "s5_top_voltage_buses.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_status": opts.get("readiness_status"),
        "scenario_id": opts.get("scenario_id"),
        "cases_run": len(cases),
        "case_names": [name for name, _, _ in cases],
        "max_observed_line_loading_percent": float(lines["loading_percent"].max()),
        "max_observed_vm_pu": float(buses["vm_pu"].max()),
        "min_observed_vm_pu": float(buses["vm_pu"].min()),
        "publication_allowed": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s5_pathology_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(summary_df, top_lines, top_charging, top_buses)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
