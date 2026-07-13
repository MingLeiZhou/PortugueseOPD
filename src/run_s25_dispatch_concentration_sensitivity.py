"""S25 diagnostic scenario: dispatch concentration sensitivity for internal DC OPF."""

from __future__ import annotations

import copy
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
OUT = ROOT / "data" / "processed" / "dcopf_s25_dispatch_concentration"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SCENARIO_ID = "S25_DISPATCH_CONCENTRATION_SENSITIVITY"
TARGET_GEN = "GENCAND_capacidade-rececao-rnd_1107P5728100_potencia_de_ligacao_ligado_mva_rari"
LOAD_SCALE = 0.3


def load_net() -> Any:
    return pp.from_json(NET_PATH)


def load_inputs() -> pd.DataFrame:
    proxies = pd.read_csv(PROXY_PATH)
    costs = pd.read_csv(COST_PATH)
    usable = proxies[proxies["dispatch_proxy_class"].isin(["dispatchable_proxy", "import_interface_proxy"])].copy()
    usable = usable.merge(costs[["candidate_id", "marginal_cost_eur_per_mwh"]], on="candidate_id", how="left")
    return usable


def attach_generators(net, usable: pd.DataFrame, variant: dict[str, Any]) -> None:
    if len(net.gen):
        net.gen.drop(net.gen.index, inplace=True)
    if len(net.poly_cost):
        net.poly_cost.drop(net.poly_cost.index, inplace=True)
    if len(net.ext_grid):
        net.ext_grid.drop(net.ext_grid.index, inplace=True)

    for _, row in usable.iterrows():
        if pd.isna(row.get("assigned_bus_index")) or pd.isna(row.get("pmax_mw_proxy")):
            continue
        bus = int(row["assigned_bus_index"])
        pmax = float(row["pmax_mw_proxy"])
        name = str(row.get("candidate_id"))
        if name == TARGET_GEN:
            if variant["variant_id"] == "remove_top_gen":
                pmax = 0.0
            elif variant["variant_id"] == "cap_top_gen_50pct":
                pmax = 0.5 * pmax
            elif variant["variant_id"] == "penalize_top_gen":
                pass
        cost = float(row.get("marginal_cost_eur_per_mwh", 100.0))
        if name == TARGET_GEN and variant["variant_id"] == "penalize_top_gen":
            cost = cost * 5.0
        gen_idx = pp.create_gen(
            net,
            bus=bus,
            p_mw=0.0,
            vm_pu=1.0,
            min_p_mw=0.0,
            max_p_mw=max(0.0, pmax),
            min_q_mvar=-0.1 * max(1.0, pmax),
            max_q_mvar=0.1 * max(1.0, pmax),
            controllable=True,
            slack=False,
            name=name,
        )
        pp.create_poly_cost(net, gen_idx, "gen", cp1_eur_per_mw=cost)

    if len(net.gen):
        slack_idx = int(pd.to_numeric(net.gen["max_p_mw"], errors="coerce").fillna(0.0).idxmax())
        net.gen.loc[slack_idx, "slack"] = True


def set_load_scale(net, scale: float) -> float:
    if len(net.load):
        net.load["scaling"] = scale
        net.load["controllable"] = False
        return float((pd.to_numeric(net.load.loc[net.load["in_service"], "p_mw"], errors="coerce") * scale).sum())
    return 0.0


def gen_metrics(net) -> pd.DataFrame:
    gen = net.gen.copy().reset_index(names="gen_index")
    res = net.res_gen.copy().reset_index(names="gen_index") if len(net.res_gen) else pd.DataFrame()
    return gen.merge(res, on="gen_index", how="left", suffixes=("", "_res")) if len(gen) and len(res) else gen


def line_metrics(net) -> pd.DataFrame:
    line = net.line.copy().reset_index(names="line_index")
    res = net.res_line.copy().reset_index(names="line_index") if len(net.res_line) else pd.DataFrame()
    out = line.merge(res, on="line_index", how="left", suffixes=("", "_res")) if len(line) and len(res) else line
    return out.sort_values("loading_percent", ascending=False) if len(out) and "loading_percent" in out.columns else out


def run_case(usable: pd.DataFrame, variant: dict[str, Any]) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    net = load_net()
    attach_generators(net, usable, variant)
    effective_load = set_load_scale(net, LOAD_SCALE)
    summary = {
        "scenario_id": SCENARIO_ID,
        "variant_id": variant["variant_id"],
        "variant_detail": variant["detail"],
        "effective_load_p_mw": effective_load,
        "converged": False,
        "error_type": "",
        "error": "",
        "objective_value": "",
        "total_gen_dispatch_mw": "",
        "top_dispatch_generator": "",
        "top_dispatch_mw": "",
        "max_line_loading_percent": "",
        "max_line_name": "",
        "publication_allowed": False,
        "diagnostic_only": True,
    }
    try:
        pp.rundcopp(net, verbose=False)
        gen_df = gen_metrics(net)
        line_df = line_metrics(net)
        summary["converged"] = bool(net.OPF_converged)
        summary["objective_value"] = float(net.res_cost)
        summary["total_gen_dispatch_mw"] = float(gen_df["p_mw_res"].sum()) if len(gen_df) else 0.0
        summary["top_dispatch_generator"] = str(gen_df.iloc[0]["name"]) if len(gen_df) else ""
        summary["top_dispatch_mw"] = float(gen_df.iloc[0]["p_mw_res"]) if len(gen_df) else 0.0
        summary["max_line_loading_percent"] = float(line_df.iloc[0]["loading_percent"]) if len(line_df) else 0.0
        summary["max_line_name"] = str(line_df.iloc[0]["name"]) if len(line_df) else ""
        return summary, gen_df, line_df
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
        return summary, pd.DataFrame(), pd.DataFrame()


def variant_plan() -> list[dict[str, Any]]:
    return [
        {"variant_id": "baseline", "detail": "Current internal DC OPF dispatch structure"},
        {"variant_id": "cap_top_gen_50pct", "detail": "Cap top dispatch source to 50% of nominal pmax"},
        {"variant_id": "remove_top_gen", "detail": "Remove top dispatch source entirely"},
        {"variant_id": "penalize_top_gen", "detail": "Raise cost of top dispatch source by 5x"},
    ]


def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary_df: pd.DataFrame, gen_compare: pd.DataFrame, line_compare: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 60 S25 Dispatch Concentration Sensitivity",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: test whether the current internal DC OPF is overly dependent on a single dispatch source (`FANHÕES (PS)`) and observe how congestion and dispatch shift when that source is capped, removed, or penalized.",
        "",
        "## Summary",
        "",
        markdown_table(summary_df),
        "",
        "## Generator Dispatch Comparison",
        "",
        markdown_table(gen_compare, 120),
        "",
        "## Top Loaded Line Comparison",
        "",
        markdown_table(line_compare, 120),
    ]
    (REPORTS / "60_s25_dispatch_concentration_sensitivity.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    usable = load_inputs()
    variants = variant_plan()

    summaries = []
    gen_frames = []
    line_frames = []
    for variant in variants:
        summary, gen_df, line_df = run_case(usable, variant)
        summaries.append(summary)
        if len(gen_df):
            gen_df = gen_df.copy()
            gen_df.insert(0, "variant_id", variant["variant_id"])
            gen_frames.append(gen_df.head(10))
        if len(line_df):
            line_df = line_df.copy()
            line_df.insert(0, "variant_id", variant["variant_id"])
            line_frames.append(line_df.head(10))

    summary_df = pd.DataFrame(summaries)
    gen_compare = pd.concat(gen_frames, ignore_index=True) if gen_frames else pd.DataFrame()
    line_compare = pd.concat(line_frames, ignore_index=True) if line_frames else pd.DataFrame()

    summary_df.to_csv(OUT / "s25_dispatch_concentration_summary.csv", index=False)
    gen_compare.to_csv(OUT / "s25_dispatch_generator_comparison.csv", index=False)
    line_compare.to_csv(OUT / "s25_dispatch_line_comparison.csv", index=False)
    (OUT / "s25_dispatch_concentration_summary.json").write_text(json.dumps({"rows": len(summary_df), "status": "DIAGNOSTIC_DONE"}, indent=2), encoding="utf-8")
    write_report(summary_df, gen_compare, line_compare)
    print(json.dumps({"rows": len(summary_df), "status": "DIAGNOSTIC_DONE"}, indent=2))


if __name__ == "__main__":
    main()
