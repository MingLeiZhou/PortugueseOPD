"""Diagnose the first Portuguese DC OPF baseline results."""

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
DCOPF = ROOT / "data" / "processed" / "dcopf_results"
OUT = ROOT / "data" / "processed" / "dcopf_diagnostics"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SUMMARY_PATH = DCOPF / "pt_dcopf_summary.json"


def load_net() -> Any:
    if not NET_PATH.exists():
        raise RuntimeError(f"Missing backbone net: {NET_PATH}")
    return pp.from_json(NET_PATH)


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    proxies = pd.read_csv(PROXY_PATH) if PROXY_PATH.exists() else pd.DataFrame()
    costs = pd.read_csv(COST_PATH) if COST_PATH.exists() else pd.DataFrame()
    return proxies, costs


def rebuild_run(net, proxies: pd.DataFrame, costs: pd.DataFrame) -> Any:
    usable = proxies[proxies["dispatch_proxy_class"].isin(["dispatchable_proxy", "import_interface_proxy"])].copy()
    usable = usable.merge(costs[["candidate_id", "marginal_cost_eur_per_mwh"]], on="candidate_id", how="left")

    if len(net.gen):
        net.gen.drop(net.gen.index, inplace=True)
    if len(net.poly_cost):
        net.poly_cost.drop(net.poly_cost.index, inplace=True)

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

    pp.rundcopp(net, verbose=False)
    return net


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

    net = load_net()
    proxies, costs = load_inputs()
    result_net = rebuild_run(net, proxies, costs)

    gen = result_net.gen.copy().reset_index(names="gen_index")
    res_gen = result_net.res_gen.copy().reset_index(names="gen_index") if len(result_net.res_gen) else pd.DataFrame()
    gen_df = gen.merge(res_gen, on="gen_index", how="left", suffixes=("", "_res")) if len(res_gen) else gen

    ext = result_net.ext_grid.copy().reset_index(names="ext_grid_index")
    res_ext = result_net.res_ext_grid.copy().reset_index(names="ext_grid_index") if len(result_net.res_ext_grid) else pd.DataFrame()
    ext_df = ext.merge(res_ext, on="ext_grid_index", how="left", suffixes=("", "_res")) if len(res_ext) else ext

    line = result_net.line.copy().reset_index(names="line_index")
    res_line = result_net.res_line.copy().reset_index(names="line_index") if len(result_net.res_line) else pd.DataFrame()
    line_df = line.merge(res_line, on="line_index", how="left", suffixes=("", "_res")) if len(res_line) else line
    line_df = line_df.sort_values("loading_percent", ascending=False) if "loading_percent" in line_df.columns else line_df

    total_pmax = float(pd.to_numeric(gen_df.get("max_p_mw", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if len(gen_df) else 0.0
    total_dispatch = float(pd.to_numeric(gen_df.get("p_mw_res", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if len(gen_df) else 0.0
    ext_dispatch = float(pd.to_numeric(ext_df.get("p_mw_res", pd.Series(dtype=float)), errors="coerce").fillna(0.0).sum()) if len(ext_df) else 0.0
    total_load = float(result_net.load.loc[result_net.load["in_service"], "p_mw"].sum()) if len(result_net.load) else 0.0

    gen_df.to_csv(OUT / "pt_dcopf_gen_diagnostics.csv", index=False)
    ext_df.to_csv(OUT / "pt_dcopf_ext_grid_diagnostics.csv", index=False)
    line_df.to_csv(OUT / "pt_dcopf_line_diagnostics.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "opf_converged": bool(result_net.OPF_converged),
        "gen_count": int(len(gen_df)),
        "ext_grid_count": int(len(ext_df)),
        "total_generator_pmax_mw": total_pmax,
        "total_generator_dispatch_mw": total_dispatch,
        "total_ext_grid_dispatch_mw": ext_dispatch,
        "total_load_p_mw": total_load,
        "dispatch_ratio_gen_to_load": (total_dispatch / total_load) if total_load else 0.0,
        "dispatch_ratio_ext_to_load": (ext_dispatch / total_load) if total_load else 0.0,
        "max_line_loading_percent": float(line_df["loading_percent"].max()) if len(line_df) and "loading_percent" in line_df.columns else 0.0,
        "max_line_name": str(line_df.iloc[0]["name"]) if len(line_df) else "",
        "publication_allowed": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "pt_dcopf_diagnostic_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    interpretation = []
    if summary["dispatch_ratio_gen_to_load"] < 0.1:
        interpretation.append("Generator proxy fleet contributes little relative to load; OPF is likely being balanced almost entirely through the ext_grid/import side.")
    if summary["max_line_loading_percent"] > 100:
        interpretation.append("The current backbone still has severe congestion, so even a converged DC OPF is not yet a credible operational benchmark.")
    if total_pmax < total_load:
        interpretation.append("Total proxy generation capacity is well below load, so the model cannot represent an internally balanced dispatch without strong import dependence.")

    text = [
        "# 51 Portuguese DC OPF Diagnostic Interpretation",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: explain the first diagnostic DC OPF baseline behavior, especially why generator dispatch is negligible and how ext_grid/import and congestion dominate the solution.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Generator Dispatch",
        "",
        markdown_table(gen_df, 80),
        "",
        "## Ext Grid Dispatch",
        "",
        markdown_table(ext_df, 20),
        "",
        "## Top Loaded Lines",
        "",
        markdown_table(line_df[[c for c in ["line_index", "name", "from_bus", "to_bus", "loading_percent", "p_from_mw", "p_to_mw"] if c in line_df.columns]], 40),
        "",
        "## Interpretation",
        "",
    ]
    if interpretation:
        text.extend([f"- {item}" for item in interpretation])
    else:
        text.append("- No major diagnostic interpretation was triggered.")

    (REPORTS / "51_portuguese_dcopf_diagnostic_interpretation.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
