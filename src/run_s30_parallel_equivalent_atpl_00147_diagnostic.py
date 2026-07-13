"""S30 diagnostic scenario: parallel-equivalent treatment for ATPL_00147 on top of S29 path strengthening."""

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
OUT = ROOT / "data" / "processed" / "dcopf_s30_atpl_00147_parallel_equivalent"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SCENARIO_ID = "S30_PARALLEL_EQUIVALENT_ATPL_00147_DIAGNOSTIC"
PRIMARY_LINE = "ATPL_00304"
TARGET_LINE = "ATPL_00147"
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
    df.loc[promote_mask, "pmax_mw_proxy"] = pd.to_numeric(df.loc[promote_mask, "capacity_mva_or_mw"], errors="coerce")
    df.loc[promote_mask, "pmin_mw_proxy"] = 0.0
    return df


def attach_generators(net, proxies: pd.DataFrame, costs: pd.DataFrame) -> None:
    if len(net.gen):
        net.gen.drop(net.gen.index, inplace=True)
    if len(net.poly_cost):
        net.poly_cost.drop(net.poly_cost.index, inplace=True)
    if len(net.ext_grid):
        net.ext_grid.drop(net.ext_grid.index, inplace=True)

    costs_map = costs.set_index("candidate_id") if len(costs) else pd.DataFrame()
    usable = proxies[
        proxies["dispatch_proxy_class"].isin(
            ["dispatchable_proxy", "dispatchable_proxy_diversified", "import_interface_proxy"]
        )
    ].copy()
    for _, row in usable.iterrows():
        if pd.isna(row.get("assigned_bus_index")) or pd.isna(row.get("pmax_mw_proxy")):
            continue
        bus = int(row["assigned_bus_index"])
        pmax = float(row["pmax_mw_proxy"])
        cost = 100.0
        if str(row.get("dispatch_proxy_class")) == "import_interface_proxy":
            cost = 120.0
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

    if len(net.gen):
        slack_idx = int(pd.to_numeric(net.gen["max_p_mw"], errors="coerce").fillna(0.0).idxmax())
        net.gen.loc[slack_idx, "slack"] = True


def set_load_scale(net, scale: float) -> float:
    if len(net.load):
        net.load["scaling"] = scale
        net.load["controllable"] = False
        return float((pd.to_numeric(net.load.loc[net.load["in_service"], "p_mw"], errors="coerce") * scale).sum())
    return 0.0


def line_index(net, line_name: str) -> int:
    target = net.line[net.line["name"].astype(str) == line_name]
    if target.empty:
        raise RuntimeError(f"Line {line_name} not found")
    return int(target.index[0])


def apply_s29_foundation(net) -> dict[str, Any]:
    idx = line_index(net, PRIMARY_LINE)
    old = net.line.loc[idx].copy()
    net.line.loc[idx, "max_i_ka"] = pd.to_numeric(net.line.loc[idx, "max_i_ka"], errors="coerce") * 2.0
    net.line.loc[idx, "x_ohm_per_km"] = pd.to_numeric(net.line.loc[idx, "x_ohm_per_km"], errors="coerce") * 0.5
    return {
        "foundation_line_index": idx,
        "foundation_line_name": PRIMARY_LINE,
        "foundation_old_x_ohm_per_km": float(old["x_ohm_per_km"]),
        "foundation_old_max_i_ka": float(old["max_i_ka"]),
        "foundation_new_x_ohm_per_km": float(net.line.loc[idx, "x_ohm_per_km"]),
        "foundation_new_max_i_ka": float(net.line.loc[idx, "max_i_ka"]),
    }


def apply_variant(net, variant_id: str) -> dict[str, Any]:
    foundation = apply_s29_foundation(net)
    idx = line_index(net, TARGET_LINE)
    old = net.line.loc[idx].copy()
    out = {
        **foundation,
        "line_index": idx,
        "line_name": TARGET_LINE,
        "old_r_ohm_per_km": float(old["r_ohm_per_km"]),
        "old_x_ohm_per_km": float(old["x_ohm_per_km"]),
        "old_c_nf_per_km": float(old["c_nf_per_km"]),
        "old_max_i_ka": float(old["max_i_ka"]),
    }
    if variant_id == "s29_foundation_only":
        return out
    if variant_id == "parallel_equivalent_2x":
        net.line.loc[idx, "max_i_ka"] = pd.to_numeric(net.line.loc[idx, "max_i_ka"], errors="coerce") * 2.0
        net.line.loc[idx, "x_ohm_per_km"] = pd.to_numeric(net.line.loc[idx, "x_ohm_per_km"], errors="coerce") * 0.5
    elif variant_id == "parallel_equivalent_4x":
        net.line.loc[idx, "max_i_ka"] = pd.to_numeric(net.line.loc[idx, "max_i_ka"], errors="coerce") * 4.0
        net.line.loc[idx, "x_ohm_per_km"] = pd.to_numeric(net.line.loc[idx, "x_ohm_per_km"], errors="coerce") * 0.35
    elif variant_id == "parallel_equivalent_6x":
        net.line.loc[idx, "max_i_ka"] = pd.to_numeric(net.line.loc[idx, "max_i_ka"], errors="coerce") * 6.0
        net.line.loc[idx, "x_ohm_per_km"] = pd.to_numeric(net.line.loc[idx, "x_ohm_per_km"], errors="coerce") * 0.25
    else:
        raise ValueError(f"Unknown variant {variant_id}")
    out.update(
        {
            "new_r_ohm_per_km": float(net.line.loc[idx, "r_ohm_per_km"]),
            "new_x_ohm_per_km": float(net.line.loc[idx, "x_ohm_per_km"]),
            "new_c_nf_per_km": float(net.line.loc[idx, "c_nf_per_km"]),
            "new_max_i_ka": float(net.line.loc[idx, "max_i_ka"]),
        }
    )
    return out


def run_case(
    proxies: pd.DataFrame, costs: pd.DataFrame, variant_id: str
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    net = load_net()
    diversified = expand_dispatchable_fleet(proxies)
    attach_generators(net, diversified, costs)
    set_load_scale(net, LOAD_SCALE)
    change_df = pd.DataFrame([apply_variant(net, variant_id)])

    summary = {
        "scenario_id": SCENARIO_ID,
        "variant_id": variant_id,
        "effective_load_p_mw": float(
            (pd.to_numeric(net.load.loc[net.load["in_service"], "p_mw"], errors="coerce") * net.load.loc[net.load["in_service"], "scaling"]).sum()
        )
        if len(net.load)
        else 0.0,
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
        return summary, gen_df, line_df, change_df
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)
        return summary, pd.DataFrame(), pd.DataFrame(), change_df


def variant_plan() -> list[str]:
    return ["s29_foundation_only", "parallel_equivalent_2x", "parallel_equivalent_4x", "parallel_equivalent_6x"]


def markdown_table(df: pd.DataFrame, max_rows: int = 100) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary_df: pd.DataFrame, change_df: pd.DataFrame, top_gens_df: pd.DataFrame, top_lines_df: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 77 S30 Parallel Equivalent ATPL_00147 Diagnostic",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: test whether ATPL_00147 should be treated as a downstream transfer-capability bottleneck by applying parallel-equivalent strengthening on top of the successful S29 ATPL_00304 foundation.",
        "",
        "## Summary",
        "",
        markdown_table(summary_df),
        "",
        "## Parameter Changes",
        "",
        markdown_table(change_df, 80),
        "",
        "## Top Dispatch Generators",
        "",
        markdown_table(top_gens_df, 80),
        "",
        "## Top Loaded Lines",
        "",
        markdown_table(top_lines_df, 120),
    ]
    (REPORTS / "77_s30_parallel_equivalent_atpl_00147_diagnostic.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    proxies, costs = load_inputs()
    variants = variant_plan()

    summaries = []
    gen_frames = []
    line_frames = []
    change_frames = []
    for variant in variants:
        summary, gen_df, line_df, change_df = run_case(proxies, costs, variant)
        summaries.append(summary)
        if len(gen_df):
            gen_df = gen_df.copy()
            gen_df.insert(0, "variant_id", variant)
            gen_frames.append(gen_df.head(10))
        if len(line_df):
            line_df = line_df.copy()
            line_df.insert(0, "variant_id", variant)
            line_frames.append(line_df.head(10))
        if len(change_df):
            change_df = change_df.copy()
            change_df.insert(0, "variant_id", variant)
            change_frames.append(change_df)

    summary_df = pd.DataFrame(summaries)
    top_gens_df = pd.concat(gen_frames, ignore_index=True) if gen_frames else pd.DataFrame()
    top_lines_df = pd.concat(line_frames, ignore_index=True) if line_frames else pd.DataFrame()
    change_df = pd.concat(change_frames, ignore_index=True) if change_frames else pd.DataFrame()

    summary_df.to_csv(OUT / "s30_summary.csv", index=False)
    top_gens_df.to_csv(OUT / "s30_top_generators.csv", index=False)
    top_lines_df.to_csv(OUT / "s30_top_lines.csv", index=False)
    change_df.to_csv(OUT / "s30_parameter_changes.csv", index=False)
    (OUT / "s30_summary.json").write_text(json.dumps({"rows": len(summary_df), "status": "DIAGNOSTIC_DONE"}, indent=2), encoding="utf-8")
    write_report(summary_df, change_df, top_gens_df, top_lines_df)
    print(json.dumps({"rows": len(summary_df), "status": "DIAGNOSTIC_DONE"}, indent=2))


if __name__ == "__main__":
    main()
