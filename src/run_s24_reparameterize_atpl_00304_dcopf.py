"""S24 diagnostic scenario: targeted ATPL_00304 remediation under internal DC OPF."""

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
OUT = ROOT / "data" / "processed" / "dcopf_s24_atpl_00304_remediation"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SCENARIO_ID = "S24_REPARAMETERIZE_ATPL_00304_DCOPF"
LINE_NAME = "ATPL_00304"
OH_WEIGHT = 0.75
CB_WEIGHT = 0.25
LOAD_SCALE = 0.3


def load_net() -> Any:
    return pp.from_json(NET_PATH)


def load_inputs() -> pd.DataFrame:
    proxies = pd.read_csv(PROXY_PATH)
    costs = pd.read_csv(COST_PATH)
    usable = proxies[proxies["dispatch_proxy_class"].isin(["dispatchable_proxy", "import_interface_proxy"])].copy()
    usable = usable.merge(costs[["candidate_id", "marginal_cost_eur_per_mwh"]], on="candidate_id", how="left")
    return usable


def infer_reference_lines(net) -> tuple[pd.Series, pd.Series, int]:
    line = net.line.copy()
    target = line[line["name"].astype(str) == LINE_NAME]
    if target.empty:
        raise RuntimeError(f"Line {LINE_NAME} not found")
    idx = int(target.index[0])
    cable_pool = line[(line.index != idx) & (pd.to_numeric(line["c_nf_per_km"], errors="coerce") >= 250)].copy()
    overhead_pool = line[(line.index != idx) & (pd.to_numeric(line["c_nf_per_km"], errors="coerce") < 50)].copy()
    cable = cable_pool.sort_values("length_km", ascending=False).iloc[0]
    overhead = overhead_pool.sort_values("length_km", ascending=False).iloc[0]
    return overhead, cable, idx


def remediate_line(net) -> dict[str, Any]:
    overhead, cable, idx = infer_reference_lines(net)
    old = net.line.loc[idx].copy()
    net.line.loc[idx, "r_ohm_per_km"] = OH_WEIGHT * float(overhead["r_ohm_per_km"]) + CB_WEIGHT * float(cable["r_ohm_per_km"])
    net.line.loc[idx, "x_ohm_per_km"] = OH_WEIGHT * float(overhead["x_ohm_per_km"]) + CB_WEIGHT * float(cable["x_ohm_per_km"])
    net.line.loc[idx, "c_nf_per_km"] = OH_WEIGHT * float(overhead["c_nf_per_km"]) + CB_WEIGHT * float(cable["c_nf_per_km"])
    net.line.loc[idx, "max_i_ka"] = min(float(overhead["max_i_ka"]), float(cable["max_i_ka"]))
    return {
        "line_index": idx,
        "old_r_ohm_per_km": float(old["r_ohm_per_km"]),
        "old_x_ohm_per_km": float(old["x_ohm_per_km"]),
        "old_c_nf_per_km": float(old["c_nf_per_km"]),
        "new_r_ohm_per_km": float(net.line.loc[idx, "r_ohm_per_km"]),
        "new_x_ohm_per_km": float(net.line.loc[idx, "x_ohm_per_km"]),
        "new_c_nf_per_km": float(net.line.loc[idx, "c_nf_per_km"]),
        "new_max_i_ka": float(net.line.loc[idx, "max_i_ka"]),
        "overhead_reference_line": str(overhead["name"]),
        "cable_reference_line": str(cable["name"]),
    }


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


def set_load_scale(net, scale: float) -> float:
    if len(net.load):
        net.load["scaling"] = scale
        net.load["controllable"] = False
        return float((pd.to_numeric(net.load.loc[net.load["in_service"], "p_mw"], errors="coerce") * scale).sum())
    return 0.0


def run_case(remediate: bool) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    net = load_net()
    usable = load_inputs()
    remediation_summary = remediate_line(net) if remediate else {}
    attach_generators(net, usable)
    effective_load = set_load_scale(net, LOAD_SCALE)

    summary = {
        "scenario_id": SCENARIO_ID,
        "remediated": remediate,
        "effective_load_p_mw": effective_load,
        "converged": False,
        "error_type": "",
        "error": "",
        "objective_value": "",
        "total_gen_dispatch_mw": "",
        "max_line_loading_percent": "",
        "max_line_name": "",
    }
    try:
        pp.rundcopp(net, verbose=False)
        summary["converged"] = bool(net.OPF_converged)
        summary["objective_value"] = float(net.res_cost)
        summary["total_gen_dispatch_mw"] = float(net.res_gen["p_mw"].sum()) if len(net.res_gen) else 0.0
        summary["max_line_loading_percent"] = float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0
        summary["max_line_name"] = str(net.line.loc[int(net.res_line["loading_percent"].idxmax()), "name"]) if len(net.res_line) else ""
    except Exception as exc:
        summary["error_type"] = type(exc).__name__
        summary["error"] = str(exc)

    line = net.line.copy().reset_index(names="line_index")
    res_line = net.res_line.copy().reset_index(names="line_index") if len(net.res_line) else pd.DataFrame()
    line_df = line.merge(res_line, on="line_index", how="left", suffixes=("", "_res")) if len(res_line) else line
    line_df = line_df.sort_values("loading_percent", ascending=False) if len(line_df) and "loading_percent" in line_df.columns else line_df

    gen = net.gen.copy().reset_index(names="gen_index")
    res_gen = net.res_gen.copy().reset_index(names="gen_index") if len(net.res_gen) else pd.DataFrame()
    gen_df = gen.merge(res_gen, on="gen_index", how="left", suffixes=("", "_res")) if len(res_gen) else gen
    gen_df = gen_df.sort_values("p_mw_res", ascending=False) if len(gen_df) and "p_mw_res" in gen_df.columns else gen_df

    return {**summary, **({"remediation_summary": remediation_summary} if remediate else {})}, line_df, gen_df


def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary_df: pd.DataFrame, remediation_df: pd.DataFrame, top_lines_df: pd.DataFrame, top_gen_df: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 59 S24 Reparameterize ATPL_00304 DC OPF",
        "",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: compare baseline vs targeted reparameterization of ATPL_00304 under the internal DC OPF configuration.",
        "",
        "## Summary",
        "",
        markdown_table(summary_df),
        "",
        "## Remediation Parameter Change",
        "",
        markdown_table(remediation_df),
        "",
        "## Top Loaded Lines",
        "",
        markdown_table(top_lines_df, 80),
        "",
        "## Top Dispatch Generators",
        "",
        markdown_table(top_gen_df, 80),
    ]
    (REPORTS / "59_s24_reparameterize_atpl_00304_dcopf.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    baseline_summary, baseline_lines, baseline_gens = run_case(remediate=False)
    rem_summary, rem_lines, rem_gens = run_case(remediate=True)

    summary_df = pd.DataFrame([
        {k: v for k, v in baseline_summary.items() if k != "remediation_summary"},
        {k: v for k, v in rem_summary.items() if k != "remediation_summary"},
    ])
    remediation_df = pd.DataFrame([rem_summary.get("remediation_summary", {})]) if rem_summary.get("remediation_summary") else pd.DataFrame()

    baseline_lines.insert(0, "case", "baseline")
    rem_lines.insert(0, "case", "remediated")
    top_lines_df = pd.concat([baseline_lines.head(20), rem_lines.head(20)], ignore_index=True)

    baseline_gens.insert(0, "case", "baseline")
    rem_gens.insert(0, "case", "remediated")
    top_gens_df = pd.concat([baseline_gens.head(20), rem_gens.head(20)], ignore_index=True)

    summary_df.to_csv(OUT / "s24_summary.csv", index=False)
    remediation_df.to_csv(OUT / "s24_remediation_parameters.csv", index=False)
    top_lines_df.to_csv(OUT / "s24_top_line_comparison.csv", index=False)
    top_gens_df.to_csv(OUT / "s24_top_gen_comparison.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "baseline_max_line_name": baseline_summary.get("max_line_name", ""),
        "baseline_max_line_loading_percent": baseline_summary.get("max_line_loading_percent", ""),
        "remediated_max_line_name": rem_summary.get("max_line_name", ""),
        "remediated_max_line_loading_percent": rem_summary.get("max_line_loading_percent", ""),
        "baseline_total_gen_dispatch_mw": baseline_summary.get("total_gen_dispatch_mw", ""),
        "remediated_total_gen_dispatch_mw": rem_summary.get("total_gen_dispatch_mw", ""),
        "publication_allowed": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s24_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary_df, remediation_df, top_lines_df, top_gens_df)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
