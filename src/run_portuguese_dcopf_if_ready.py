"""Run a first fail-closed diagnostic DC OPF baseline on the S16 backbone core."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pandapower as pp

ROOT = Path(__file__).resolve().parents[1]
BACKBONE = ROOT / "data" / "processed" / "acpf_s16_backbone_core_depth6"
DISPATCH = ROOT / "data" / "processed" / "generator_dispatch_proxies"
COSTS = ROOT / "data" / "processed" / "generator_costs"
OUT = ROOT / "data" / "processed" / "dcopf_results"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
SUMMARY_PATH = BACKBONE / "s16_backbone_summary.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
ALLOWED_SCENARIO_ID = {"S16_BACKBONE_DIAGNOSTIC_CORE_DEPTH6"}


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: backbone net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    scenario = opts.get("scenario_id")
    if scenario not in ALLOWED_SCENARIO_ID:
        raise RuntimeError(f"Fail-closed: scenario {scenario!r} is not allowed for first diagnostic DC OPF baseline.")
    return net, opts


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    proxies = pd.read_csv(PROXY_PATH) if PROXY_PATH.exists() else pd.DataFrame()
    costs = pd.read_csv(COST_PATH) if COST_PATH.exists() else pd.DataFrame()
    if proxies.empty or costs.empty:
        raise RuntimeError("Missing generator dispatch proxy or cost scenario inputs.")
    usable = proxies[proxies["dispatch_proxy_class"].isin(["dispatchable_proxy", "import_interface_proxy"])].copy()
    usable = usable.merge(costs[["candidate_id", "cost_class", "marginal_cost_eur_per_mwh", "must_run", "curtailable"]], on="candidate_id", how="left")
    return usable, costs


def attach_generators(net, usable: pd.DataFrame) -> dict[str, Any]:
    if len(net.gen):
        net.gen.drop(net.gen.index, inplace=True)
    if len(net.poly_cost):
        net.poly_cost.drop(net.poly_cost.index, inplace=True)

    added = 0
    total_pmax = 0.0
    for _, row in usable.iterrows():
        if pd.isna(row.get("assigned_bus_index")) or pd.isna(row.get("pmax_mw_proxy")):
            continue
        bus = int(row["assigned_bus_index"])
        pmax = float(row["pmax_mw_proxy"])
        pmin = float(row.get("pmin_mw_proxy") or 0.0)
        gen_idx = pp.create_gen(
            net,
            bus=bus,
            p_mw=max(0.0, min(0.3 * pmax, pmax)),
            vm_pu=1.0,
            min_p_mw=pmin,
            max_p_mw=pmax,
            min_q_mvar=-0.1 * max(1.0, pmax),
            max_q_mvar=0.1 * max(1.0, pmax),
            controllable=True,
            name=str(row.get("candidate_id")),
        )
        pp.create_poly_cost(net, gen_idx, "gen", cp1_eur_per_mw=float(row.get("marginal_cost_eur_per_mwh", 100.0)))
        added += 1
        total_pmax += pmax
    return {"added_gen_count": added, "total_pmax_mw": total_pmax}


def line_metrics(net) -> pd.DataFrame:
    if not len(net.line):
        return pd.DataFrame()
    line = net.line.copy().reset_index(names="line_index")
    res = net.res_line.copy().reset_index(names="line_index") if len(net.res_line) else pd.DataFrame()
    merged = line.merge(res, on="line_index", how="left", suffixes=("", "_res")) if len(res) else line
    keep = ["line_index", "name", "from_bus", "to_bus", "length_km", "loading_percent", "p_from_mw", "p_to_mw"]
    return merged[[c for c in keep if c in merged.columns]].sort_values("loading_percent", ascending=False)


def gen_metrics(net) -> pd.DataFrame:
    if not len(net.gen):
        return pd.DataFrame()
    gen = net.gen.copy().reset_index(names="gen_index")
    res = net.res_gen.copy().reset_index(names="gen_index") if len(net.res_gen) else pd.DataFrame()
    merged = gen.merge(res, on="gen_index", how="left", suffixes=("", "_res")) if len(res) else gen
    keep = ["gen_index", "name", "bus", "p_mw", "min_p_mw", "max_p_mw", "res_p_mw", "res_q_mvar"]
    return merged[[c for c in keep if c in merged.columns]].sort_values("max_p_mw", ascending=False)


def write_report(summary: dict[str, Any], gen_df: pd.DataFrame, line_df: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)

    def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
        if df.empty:
            return "_No rows._\n"
        view = df.head(max_rows)
        cols = list(view.columns)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in view.iterrows():
            lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
        return "\n".join(lines) + "\n"

    text = [
        "# 50 Portuguese Diagnostic DC OPF Baseline",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: first fail-closed diagnostic DC OPF attempt on the S16 backbone core using semantic dispatch proxies and diagnostic cost assumptions. This is not a publication-grade OPF model.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Generator Dispatch Results",
        "",
        markdown_table(gen_df, 80),
        "",
        "## Top Loaded Lines",
        "",
        markdown_table(line_df, 80),
    ]
    (REPORTS / "50_portuguese_dcopf_diagnostic.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net, opts = load_net_checked()
    usable, _ = load_inputs()
    attach_summary = attach_generators(net, usable)

    summary: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_id": opts.get("scenario_id"),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "converged": False,
        "error_type": "",
        "error": "",
        **attach_summary,
    }

    try:
        pp.rundcopp(net, verbose=False)
        summary["converged"] = bool(net.OPF_converged)
        summary["objective_value"] = float(net.res_cost)
        summary["active_bus_count"] = int(net.bus["in_service"].sum()) if "in_service" in net.bus.columns else int(len(net.bus))
        summary["gen_count"] = int(len(net.gen))
        summary["max_line_loading_percent"] = float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0
        summary["max_line_name"] = str(net.line.loc[int(net.res_line["loading_percent"].idxmax()), "name"]) if len(net.res_line) else ""
        summary["total_gen_dispatch_mw"] = float(net.res_gen["p_mw"].sum()) if len(net.res_gen) else 0.0
        summary["total_load_p_mw"] = float(net.load.loc[net.load["in_service"], "p_mw"].sum()) if len(net.load) else 0.0
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)

    gen_df = gen_metrics(net) if summary["converged"] else pd.DataFrame()
    line_df = line_metrics(net) if summary["converged"] else pd.DataFrame()
    if len(gen_df):
        gen_df.to_csv(OUT / "pt_dcopf_gen_results.csv", index=False)
    if len(line_df):
        line_df.to_csv(OUT / "pt_dcopf_line_results.csv", index=False)
    (OUT / "pt_dcopf_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, gen_df, line_df)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
