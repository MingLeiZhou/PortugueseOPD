"""Run a reduced balanced diagnostic DC OPF case on the S16 backbone."""

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
OUT = ROOT / "data" / "processed" / "dcopf_s18_reduced_balanced"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SCENARIO_ID = "S18_DCOPF_REDUCED_BALANCED_CASE"


def load_net() -> Any:
    if not NET_PATH.exists():
        raise RuntimeError(f"Missing backbone net: {NET_PATH}")
    return pp.from_json(NET_PATH)


def load_inputs() -> pd.DataFrame:
    proxies = pd.read_csv(PROXY_PATH) if PROXY_PATH.exists() else pd.DataFrame()
    costs = pd.read_csv(COST_PATH) if COST_PATH.exists() else pd.DataFrame()
    if proxies.empty or costs.empty:
        raise RuntimeError("Missing generator proxy or cost inputs.")
    usable = proxies[proxies["dispatch_proxy_class"].isin(["dispatchable_proxy", "import_interface_proxy"])].copy()
    usable = usable.merge(costs[["candidate_id", "cost_class", "marginal_cost_eur_per_mwh"]], on="candidate_id", how="left")
    return usable


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


def set_load_scale_to_match_supply(net, total_pmax: float, reserve_factor: float = 0.9) -> dict[str, Any]:
    active_load = net.load[net.load["in_service"]].copy() if len(net.load) and "in_service" in net.load.columns else net.load.copy()
    total_load = float(pd.to_numeric(active_load["p_mw"], errors="coerce").fillna(0.0).sum()) if len(active_load) else 0.0
    target = total_pmax * reserve_factor
    scale = min(1.0, target / total_load) if total_load else 0.0
    if len(net.load):
        net.load["scaling"] = scale
    return {"base_total_load_p_mw": total_load, "target_dispatchable_supply_mw": target, "applied_load_scale": scale}


def gen_metrics(net) -> pd.DataFrame:
    gen = net.gen.copy().reset_index(names="gen_index") if len(net.gen) else pd.DataFrame()
    res = net.res_gen.copy().reset_index(names="gen_index") if len(net.res_gen) else pd.DataFrame()
    return gen.merge(res, on="gen_index", how="left", suffixes=("", "_res")) if len(gen) and len(res) else gen


def line_metrics(net) -> pd.DataFrame:
    line = net.line.copy().reset_index(names="line_index") if len(net.line) else pd.DataFrame()
    res = net.res_line.copy().reset_index(names="line_index") if len(net.res_line) else pd.DataFrame()
    return line.merge(res, on="line_index", how="left", suffixes=("", "_res")) if len(line) and len(res) else line


def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary: dict[str, Any], scale_info: dict[str, Any], gen_df: pd.DataFrame, line_df: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 52 S18 Reduced Balanced DC OPF",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: first reduced balanced diagnostic DC OPF on the S16 backbone, with load scaled to roughly match the proxy generation fleet. This is diagnostic only.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Load Scaling Logic",
        "",
        markdown_table(pd.DataFrame([scale_info])),
        "",
        "## Generator Dispatch",
        "",
        markdown_table(gen_df, 80),
        "",
        "## Top Loaded Lines",
        "",
        markdown_table(line_df[[c for c in ["line_index", "name", "loading_percent", "p_from_mw", "p_to_mw"] if c in line_df.columns]], 80),
    ]
    (REPORTS / "52_s18_reduced_balanced_dcopf.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net = load_net()
    usable = load_inputs()
    gen_summary = attach_generators(net, usable)
    scale_info = set_load_scale_to_match_supply(net, gen_summary["total_pmax_mw"], reserve_factor=0.9)

    summary: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_id": SCENARIO_ID,
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        **gen_summary,
        **scale_info,
        "converged": False,
        "error_type": "",
        "error": "",
    }

    try:
        pp.rundcopp(net, verbose=False)
        summary["converged"] = bool(net.OPF_converged)
        summary["objective_value"] = float(net.res_cost)
        summary["total_gen_dispatch_mw"] = float(net.res_gen["p_mw"].sum()) if len(net.res_gen) else 0.0
        summary["total_load_p_mw_effective"] = float((net.load.loc[net.load["in_service"], "p_mw"] * net.load.loc[net.load["in_service"], "scaling"]).sum()) if len(net.load) else 0.0
        summary["max_line_loading_percent"] = float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0
        summary["max_line_name"] = str(net.line.loc[int(net.res_line["loading_percent"].idxmax()), "name"]) if len(net.res_line) else ""
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)

    gen_df = gen_metrics(net) if summary["converged"] else pd.DataFrame()
    line_df = line_metrics(net) if summary["converged"] else pd.DataFrame()
    if len(gen_df):
        gen_df.to_csv(OUT / "s18_dcopf_gen_results.csv", index=False)
    if len(line_df):
        line_df.to_csv(OUT / "s18_dcopf_line_results.csv", index=False)
    (OUT / "s18_dcopf_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, scale_info, gen_df, line_df)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
