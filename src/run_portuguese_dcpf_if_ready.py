"""Run a fail-closed diagnostic DC power flow for the Portuguese model."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pandapower as pp

ROOT = Path(__file__).resolve().parents[1]
READY = ROOT / "data" / "processed" / "acpf_ready"
OUT = ROOT / "data" / "processed" / "dcpf_results"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_RUN_STATUS = {"AC_PF_BENCHMARK_PLUMBING_READY", "AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY", "AC_PF_SCENARIO_READY", "SOURCE_BACKED_READY"}


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_RUN_STATUS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for diagnostic DC PF.")
    return net, opts


def line_metrics(net) -> pd.DataFrame:
    if not len(net.line):
        return pd.DataFrame()
    line = net.line.copy().reset_index(names="line_index")
    res = net.res_line.copy().reset_index(names="line_index")
    merged = line.merge(res, on="line_index", how="left", suffixes=("", "_res"))
    keep = ["line_index", "name", "from_bus", "to_bus", "length_km", "r_ohm_per_km", "x_ohm_per_km", "max_i_ka", "loading_percent", "p_from_mw", "p_to_mw", "pl_mw"]
    return merged[[c for c in keep if c in merged.columns]].sort_values("loading_percent", ascending=False)


def bus_metrics(net) -> pd.DataFrame:
    if not len(net.bus):
        return pd.DataFrame()
    bus = net.bus.copy().reset_index(names="bus_index")
    res = net.res_bus.copy().reset_index(names="bus_index")
    merged = bus.merge(res, on="bus_index", how="left", suffixes=("", "_res"))
    keep = ["bus_index", "name", "vn_kv", "in_service", "va_degree", "p_mw"]
    return merged[[c for c in keep if c in merged.columns]].sort_values("p_mw", ascending=False)


def write_report(summary: dict[str, Any], line_df: pd.DataFrame, bus_df: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)

    def markdown_table(df: pd.DataFrame, max_rows: int = 40) -> str:
        if df.empty:
            return "_No rows._\n"
        view = df.head(max_rows)
        cols = list(view.columns)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in view.iterrows():
            lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
        return "\n".join(lines) + "\n"

    text = [
        "# 44 Portuguese Diagnostic DC Power Flow",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: diagnostic DC PF only. This does not imply OPF-readiness or publication-grade AC model validity.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Top Loaded Lines",
        "",
        markdown_table(line_df, 40),
        "",
        "## Bus Power Metrics",
        "",
        markdown_table(bus_df, 40),
    ]
    (REPORTS / "44_portuguese_dcpf_diagnostic.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net, opts = load_net_checked()
    summary: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_status": opts.get("readiness_status"),
        "scenario_id": opts.get("scenario_id"),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "converged": False,
        "error_type": "",
        "error": "",
    }
    try:
        pp.rundcpp(net, calculate_voltage_angles=True)
        summary["converged"] = bool(net.converged)
        summary["active_bus_count"] = int(net.bus["in_service"].sum()) if "in_service" in net.bus.columns else int(len(net.bus))
        summary["active_line_count"] = int(net.line["in_service"].sum()) if "in_service" in net.line.columns else int(len(net.line))
        summary["active_load_count"] = int(net.load["in_service"].sum()) if "in_service" in net.load.columns else int(len(net.load))
        summary["max_line_loading_percent"] = float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0
        summary["max_line_name"] = str(net.line.loc[int(net.res_line["loading_percent"].idxmax()), "name"]) if len(net.res_line) else ""
        summary["slack_bus_name"] = str(net.bus.loc[int(net.ext_grid.loc[net.ext_grid["in_service"], "bus"].iloc[0]), "name"]) if len(net.ext_grid[net.ext_grid["in_service"]]) else ""
        summary["total_load_p_mw"] = float(net.load.loc[net.load["in_service"], "p_mw"].sum()) if len(net.load) else 0.0
        summary["total_line_losses_mw"] = float(net.res_line["pl_mw"].sum()) if len(net.res_line) else 0.0
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)

    line_df = line_metrics(net) if summary["converged"] else pd.DataFrame()
    bus_df = bus_metrics(net) if summary["converged"] else pd.DataFrame()
    if len(line_df):
        line_df.to_csv(OUT / "pt_dcpf_line_results.csv", index=False)
    if len(bus_df):
        bus_df.to_csv(OUT / "pt_dcpf_bus_results.csv", index=False)
    (OUT / "pt_dcpf_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, line_df, bus_df)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
