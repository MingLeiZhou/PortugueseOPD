"""S26 diagnostic scenario: expand internal dispatchable proxy fleet for DC OPF diversification."""

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
OUT = ROOT / "data" / "processed" / "dcopf_s26_supply_diversification"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SCENARIO_ID = "S26_SUPPLY_DIVERSIFICATION_DIAGNOSTIC"
LOAD_SCALE = 0.3


def load_net() -> Any:
    return pp.from_json(NET_PATH)


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    proxies = pd.read_csv(PROXY_PATH)
    costs = pd.read_csv(COST_PATH)
    return proxies, costs


def expand_dispatchable_fleet(proxies: pd.DataFrame) -> pd.DataFrame:
    df = proxies.copy()
    promote_mask = (
        (df["dispatch_proxy_class"] == "capacity_context_only")
        & df["assignment_status"].eq("ASSIGNED_TO_BACKBONE_BUS")
        & pd.to_numeric(df["assigned_bus_voltage_kv"], errors="coerce").eq(60.0)
        & pd.to_numeric(df["capacity_mva_or_mw"], errors="coerce").fillna(0) > 0.2
    )
    df.loc[promote_mask, "dispatch_proxy_class"] = "dispatchable_proxy_diversified"
    df.loc[promote_mask, "dispatch_reason"] = "promoted_from_capacity_context_for_supply_diversification_diagnostic"
    df.loc[promote_mask, "pmax_mw_proxy"] = pd.to_numeric(df.loc[promote_mask, "capacity_mva_or_mw"], errors="coerce")
    df.loc[promote_mask, "pmin_mw_proxy"] = 0.0
    return df


def attach_generators(net, proxies: pd.DataFrame, costs: pd.DataFrame) -> dict[str, Any]:
    if len(net.gen):
        net.gen.drop(net.gen.index, inplace=True)
    if len(net.poly_cost):
        net.poly_cost.drop(net.poly_cost.index, inplace=True)
    if len(net.ext_grid):
        net.ext_grid.drop(net.ext_grid.index, inplace=True)

    costs_map = costs.set_index("candidate_id") if len(costs) else pd.DataFrame()
    added = 0
    import_count = 0
    total_pmax = 0.0
    promoted_count = 0

    usable = proxies[proxies["dispatch_proxy_class"].isin(["dispatchable_proxy", "dispatchable_proxy_diversified", "import_interface_proxy"])].copy()
    for _, row in usable.iterrows():
        if pd.isna(row.get("assigned_bus_index")) or pd.isna(row.get("pmax_mw_proxy")):
            continue
        bus = int(row["assigned_bus_index"])
        pmax = float(row["pmax_mw_proxy"])
        cls = str(row.get("dispatch_proxy_class", ""))
        if cls == "import_interface_proxy":
            import_count += 1
            cost = 120.0
        else:
            if cls == "dispatchable_proxy_diversified":
                promoted_count += 1
            cost = 100.0
        if len(costs_map) and row["candidate_id"] in costs_map.index:
            cost = float(costs_map.loc[row["candidate_id"], "marginal_cost_eur_per_mwh"])
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
        added += 1
        total_pmax += pmax

    if len(net.gen):
        slack_idx = int(pd.to_numeric(net.gen["max_p_mw"], errors="coerce").fillna(0.0).idxmax())
        net.gen.loc[slack_idx, "slack"] = True

    return {
        "added_gen_count": added,
        "import_proxy_count": import_count,
        "promoted_dispatchable_count": promoted_count,
        "total_pmax_mw": total_pmax,
    }


def set_load_scale(net, scale: float) -> float:
    if len(net.load):
        net.load["scaling"] = scale
        net.load["controllable"] = False
        return float((pd.to_numeric(net.load.loc[net.load["in_service"], "p_mw"], errors="coerce") * scale).sum())
    return 0.0


def run_case() -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    net = load_net()
    proxies, costs = load_inputs()
    diversified = expand_dispatchable_fleet(proxies)
    attach_summary = attach_generators(net, diversified, costs)
    effective_load = set_load_scale(net, LOAD_SCALE)

    summary = {
        "scenario_id": SCENARIO_ID,
        "effective_load_p_mw": effective_load,
        **attach_summary,
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
        gen = net.gen.copy().reset_index(names="gen_index")
        res_gen = net.res_gen.copy().reset_index(names="gen_index") if len(net.res_gen) else pd.DataFrame()
        gen_df = gen.merge(res_gen, on="gen_index", how="left", suffixes=("", "_res")) if len(res_gen) else gen
        gen_df = gen_df.sort_values("p_mw_res", ascending=False) if len(gen_df) else gen_df

        line = net.line.copy().reset_index(names="line_index")
        res_line = net.res_line.copy().reset_index(names="line_index") if len(net.res_line) else pd.DataFrame()
        line_df = line.merge(res_line, on="line_index", how="left", suffixes=("", "_res")) if len(res_line) else line
        line_df = line_df.sort_values("loading_percent", ascending=False) if len(line_df) else line_df

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


def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary: dict[str, Any], gen_df: pd.DataFrame, line_df: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 61 S26 Supply Diversification Diagnostic",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: expand the internal dispatchable proxy fleet by promoting selected high-confidence 60 kV capacity-context rows and test whether dispatch concentration and congestion improve.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary])),
        "",
        "## Generator Dispatch",
        "",
        markdown_table(gen_df, 80),
        "",
        "## Top Loaded Lines",
        "",
        markdown_table(line_df[[c for c in ["line_index", "name", "loading_percent", "p_from_mw", "p_to_mw"] if c in line_df.columns]], 80),
    ]
    (REPORTS / "61_s26_supply_diversification_diagnostic.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    summary, gen_df, line_df = run_case()
    if len(gen_df):
        gen_df.to_csv(OUT / "s26_gen_results.csv", index=False)
    if len(line_df):
        line_df.to_csv(OUT / "s26_line_results.csv", index=False)
    (OUT / "s26_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, gen_df, line_df)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
