"""S19 diagnostic scenario: align DC PF/DC OPF problem formulation for interpretability."""

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
OUT = ROOT / "data" / "processed" / "dcopf_s19_model_sanity"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SCENARIO_ID = "S19_DCPF_DCOPF_MODEL_SANITY_ALIGNMENT"


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


def attach_generators(net, usable: pd.DataFrame) -> dict[str, Any]:
    if len(net.gen):
        net.gen.drop(net.gen.index, inplace=True)
    if len(net.poly_cost):
        net.poly_cost.drop(net.poly_cost.index, inplace=True)

    added_gen = 0
    total_gen_pmax = 0.0
    for _, row in usable[usable["dispatch_proxy_class"] == "dispatchable_proxy"].iterrows():
        if pd.isna(row.get("assigned_bus_index")) or pd.isna(row.get("pmax_mw_proxy")):
            continue
        bus = int(row["assigned_bus_index"])
        pmax = float(row["pmax_mw_proxy"])
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
            name=str(row.get("candidate_id")),
        )
        pp.create_poly_cost(net, gen_idx, "gen", cp1_eur_per_mw=float(row.get("marginal_cost_eur_per_mwh", 100.0)))
        added_gen += 1
        total_gen_pmax += pmax

    added_import = 0
    total_import_pmax = 0.0
    for _, row in usable[usable["dispatch_proxy_class"] == "import_interface_proxy"].iterrows():
        if pd.isna(row.get("assigned_bus_index")) or pd.isna(row.get("pmax_mw_proxy")):
            continue
        bus = int(row["assigned_bus_index"])
        pmax = float(row["pmax_mw_proxy"])
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
            name=str(row.get("candidate_id")),
        )
        pp.create_poly_cost(net, gen_idx, "gen", cp1_eur_per_mw=float(row.get("marginal_cost_eur_per_mwh", 120.0)))
        added_import += 1
        total_import_pmax += pmax

    return {
        "added_gen_count": added_gen,
        "total_gen_pmax_mw": total_gen_pmax,
        "added_import_proxy_count": added_import,
        "total_import_pmax_mw": total_import_pmax,
    }


def align_problem(net, reserve_factor: float = 0.9) -> dict[str, Any]:
    if len(net.ext_grid):
        net.ext_grid["controllable"] = False
    if len(net.load):
        net.load["controllable"] = False
        total_load = float(pd.to_numeric(net.load.loc[net.load["in_service"], "p_mw"], errors="coerce").fillna(0.0).sum())
    else:
        total_load = 0.0
    total_supply = float(pd.to_numeric(net.gen["max_p_mw"], errors="coerce").fillna(0.0).sum()) if len(net.gen) else 0.0
    target = total_supply * reserve_factor
    scale = min(1.0, target / total_load) if total_load else 0.0
    if len(net.load):
        net.load["scaling"] = scale
    return {"base_total_load_p_mw": total_load, "total_proxy_supply_mw": total_supply, "target_supply_mw": target, "applied_load_scale": scale}


def effective_load(net) -> float:
    if not len(net.load):
        return 0.0
    active = net.load[net.load["in_service"]].copy()
    scaling = pd.to_numeric(active.get("scaling", 1.0), errors="coerce").fillna(1.0)
    return float((pd.to_numeric(active["p_mw"], errors="coerce").fillna(0.0) * scaling).sum())


def gen_metrics(net) -> pd.DataFrame:
    gen = net.gen.copy().reset_index(names="gen_index") if len(net.gen) else pd.DataFrame()
    res = net.res_gen.copy().reset_index(names="gen_index") if len(net.res_gen) else pd.DataFrame()
    return gen.merge(res, on="gen_index", how="left", suffixes=("", "_res")) if len(gen) and len(res) else gen


def ext_metrics(net) -> pd.DataFrame:
    ext = net.ext_grid.copy().reset_index(names="ext_grid_index") if len(net.ext_grid) else pd.DataFrame()
    res = net.res_ext_grid.copy().reset_index(names="ext_grid_index") if len(net.res_ext_grid) else pd.DataFrame()
    return ext.merge(res, on="ext_grid_index", how="left", suffixes=("", "_res")) if len(ext) and len(res) else ext


def line_metrics(net) -> pd.DataFrame:
    line = net.line.copy().reset_index(names="line_index") if len(net.line) else pd.DataFrame()
    res = net.res_line.copy().reset_index(names="line_index") if len(net.res_line) else pd.DataFrame()
    out = line.merge(res, on="line_index", how="left", suffixes=("", "_res")) if len(line) and len(res) else line
    return out.sort_values("loading_percent", ascending=False) if len(out) and "loading_percent" in out.columns else out


def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary: dict[str, Any], supply_summary: dict[str, Any], scale_summary: dict[str, Any], gen_df: pd.DataFrame, ext_df: pd.DataFrame, line_df: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 53 S19 DC PF / DC OPF Model Sanity Alignment",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: align controllability, ext_grid role, generator role, and load scale so the first DC OPF behaves like a dispatch problem rather than just a solver wiring check.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Supply Summary",
        "",
        markdown_table(pd.DataFrame([supply_summary])),
        "",
        "## Load Scale Summary",
        "",
        markdown_table(pd.DataFrame([scale_summary])),
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
        markdown_table(line_df[[c for c in ["line_index", "name", "loading_percent", "p_from_mw", "p_to_mw"] if c in line_df.columns]], 80),
    ]
    (REPORTS / "53_s19_dcpf_dcopf_model_sanity_alignment.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    net = load_net()
    usable = load_inputs()
    supply_summary = attach_generators(net, usable)
    scale_summary = align_problem(net, reserve_factor=0.9)

    summary: dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_id": SCENARIO_ID,
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        **supply_summary,
        **scale_summary,
        "converged": False,
        "error_type": "",
        "error": "",
    }

    try:
        pp.rundcopp(net, verbose=False)
        summary["converged"] = bool(net.OPF_converged)
        summary["objective_value"] = float(net.res_cost)
        summary["total_gen_dispatch_mw"] = float(net.res_gen["p_mw"].sum()) if len(net.res_gen) else 0.0
        summary["total_ext_grid_dispatch_mw"] = float(net.res_ext_grid["p_mw"].sum()) if len(net.res_ext_grid) else 0.0
        summary["effective_load_p_mw"] = effective_load(net)
        summary["dispatch_ratio_gen_to_load"] = (summary["total_gen_dispatch_mw"] / summary["effective_load_p_mw"]) if summary["effective_load_p_mw"] else 0.0
        summary["dispatch_ratio_ext_to_load"] = (summary["total_ext_grid_dispatch_mw"] / summary["effective_load_p_mw"]) if summary["effective_load_p_mw"] else 0.0
        summary["max_line_loading_percent"] = float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0
        summary["max_line_name"] = str(net.line.loc[int(net.res_line["loading_percent"].idxmax()), "name"]) if len(net.res_line) else ""
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)

    gen_df = gen_metrics(net) if summary["converged"] else pd.DataFrame()
    ext_df = ext_metrics(net) if summary["converged"] else pd.DataFrame()
    line_df = line_metrics(net) if summary["converged"] else pd.DataFrame()

    if len(gen_df):
        gen_df.to_csv(OUT / "s19_dcopf_gen_results.csv", index=False)
    if len(ext_df):
        ext_df.to_csv(OUT / "s19_dcopf_ext_grid_results.csv", index=False)
    if len(line_df):
        line_df.to_csv(OUT / "s19_dcopf_line_results.csv", index=False)
    (OUT / "s19_dcopf_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, supply_summary, scale_summary, gen_df, ext_df, line_df)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
