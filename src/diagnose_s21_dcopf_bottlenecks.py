"""Diagnose bottlenecks in the S21 no-extgrid DC OPF runs."""

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
S21 = ROOT / "data" / "processed" / "dcopf_s21_no_extgrid"
OUT = ROOT / "data" / "processed" / "dcopf_s21_diagnostics"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SUMMARY_PATH = S21 / "s21_no_extgrid_dcopf_summary.json"


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
    usable = usable.merge(costs[["candidate_id", "marginal_cost_eur_per_mwh", "cost_class"]], on="candidate_id", how="left")
    return usable


def attach_generators(net, usable: pd.DataFrame) -> None:
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
        cost = float(row.get("marginal_cost_eur_per_mwh", 100.0))
        gen_idx = pp.create_gen(
            net,
            bus=bus,
            p_mw=0.0,
            vm_pu=1.0,
            min_p_mw=0.0,
            max_p_mw=pmax,
            min_q_mvar=-0.1 * max(1.0, pmax),
            max_q_mvar=0.1 * max(1.0, pmax),
            controllable=True,
            slack=False,
            name=str(row.get("candidate_id")),
        )
        pp.create_poly_cost(net, gen_idx, "gen", cp1_eur_per_mw=cost)

    if len(net.gen):
        slack_idx = int(pd.to_numeric(net.gen["max_p_mw"], errors="coerce").fillna(0.0).idxmax())
        net.gen.loc[slack_idx, "slack"] = True


def set_load_scale(net, scale: float) -> None:
    if len(net.load):
        net.load["scaling"] = scale
        net.load["controllable"] = False


def run_case(scale: float) -> Any:
    net = load_net()
    usable = load_inputs()
    attach_generators(net, usable)
    set_load_scale(net, scale)
    pp.rundcopp(net, verbose=False)
    return net


def line_results(net, scale: float) -> pd.DataFrame:
    line = net.line.copy().reset_index(names="line_index")
    res = net.res_line.copy().reset_index(names="line_index")
    out = line.merge(res, on="line_index", how="left", suffixes=("", "_res"))
    out.insert(0, "load_scale", scale)
    return out.sort_values("loading_percent", ascending=False)


def gen_results(net, scale: float) -> pd.DataFrame:
    gen = net.gen.copy().reset_index(names="gen_index")
    res = net.res_gen.copy().reset_index(names="gen_index")
    out = gen.merge(res, on="gen_index", how="left", suffixes=("", "_res"))
    out.insert(0, "load_scale", scale)
    return out.sort_values("p_mw_res", ascending=False)


def summary_rows(scales: list[float]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    summaries = []
    line_frames = []
    gen_frames = []
    for scale in scales:
        net = run_case(scale)
        line_df = line_results(net, scale)
        gen_df = gen_results(net, scale)
        line_frames.append(line_df)
        gen_frames.append(gen_df)
        summaries.append(
            {
                "load_scale": scale,
                "objective_value": float(net.res_cost),
                "total_gen_dispatch_mw": float(net.res_gen["p_mw"].sum()) if len(net.res_gen) else 0.0,
                "max_line_loading_percent": float(line_df["loading_percent"].max()) if len(line_df) else 0.0,
                "worst_line_name": str(line_df.iloc[0]["name"]) if len(line_df) else "",
                "top_dispatch_gen": str(gen_df.iloc[0]["name"]) if len(gen_df) else "",
                "top_dispatch_mw": float(gen_df.iloc[0]["p_mw_res"]) if len(gen_df) else 0.0,
            }
        )
    return pd.DataFrame(summaries), pd.concat(line_frames, ignore_index=True), pd.concat(gen_frames, ignore_index=True)


def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    scales = [0.10, 0.20, 0.30]
    summary_df, line_df, gen_df = summary_rows(scales)

    top_lines = line_df.groupby("name", as_index=False)["loading_percent"].max().sort_values("loading_percent", ascending=False)
    top_dispatch = gen_df.groupby("name", as_index=False)["p_mw_res"].max().sort_values("p_mw_res", ascending=False)

    summary_df.to_csv(OUT / "s21_dcopf_scale_summary.csv", index=False)
    line_df.to_csv(OUT / "s21_dcopf_line_results_by_scale.csv", index=False)
    gen_df.to_csv(OUT / "s21_dcopf_gen_results_by_scale.csv", index=False)
    top_lines.to_csv(OUT / "s21_dcopf_top_congested_lines.csv", index=False)
    top_dispatch.to_csv(OUT / "s21_dcopf_top_dispatch_generators.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scale_count": len(scales),
        "worst_line_overall": str(top_lines.iloc[0]["name"]) if len(top_lines) else "",
        "worst_line_loading_percent": float(top_lines.iloc[0]["loading_percent"]) if len(top_lines) else 0.0,
        "top_dispatch_generator": str(top_dispatch.iloc[0]["name"]) if len(top_dispatch) else "",
        "top_dispatch_mw": float(top_dispatch.iloc[0]["p_mw_res"]) if len(top_dispatch) else 0.0,
        "publication_allowed": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s21_dcopf_bottleneck_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    text = [
        "# 55 S21 DC OPF Bottleneck Diagnosis",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: explain which lines bind first and which proxy generators actually dispatch in the no-extgrid DC OPF scenario.",
        "",
        "## Scale Summary",
        "",
        markdown_table(summary_df),
        "",
        "## Top Congested Lines",
        "",
        markdown_table(top_lines, 40),
        "",
        "## Top Dispatch Generators",
        "",
        markdown_table(top_dispatch, 40),
        "",
        "## Interpretation",
        "",
        "The internal-only DC OPF is now a real dispatch problem. The next engineering focus should be the corridors that bind first under 20-30% load and the generator buses that dominate dispatch, rather than further semantic generator cleanup.",
    ]
    (REPORTS / "55_s21_dcopf_bottleneck_diagnosis.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
