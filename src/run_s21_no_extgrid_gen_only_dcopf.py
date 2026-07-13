"""S21 diagnostic scenario: remove ext_grid from supply role and keep only internal gen plus explicit import proxy generators."""

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
OUT = ROOT / "data" / "processed" / "dcopf_s21_no_extgrid"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SCENARIO_ID = "S21_NO_EXTGRID_GEN_ONLY_DCOPF"


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
    if len(net.ext_grid):
        net.ext_grid.drop(net.ext_grid.index, inplace=True)

    gen_count = 0
    gen_pmax = 0.0
    import_count = 0
    import_pmax = 0.0
    for _, row in usable.iterrows():
        if pd.isna(row.get("assigned_bus_index")) or pd.isna(row.get("pmax_mw_proxy")):
            continue
        bus = int(row["assigned_bus_index"])
        pmax = float(row["pmax_mw_proxy"])
        dispatch_class = str(row.get("dispatch_proxy_class", ""))
        if dispatch_class == "dispatchable_proxy":
            cost = float(row.get("marginal_cost_eur_per_mwh", 100.0))
            gen_count += 1
            gen_pmax += pmax
        else:
            cost = float(row.get("marginal_cost_eur_per_mwh", 300.0))
            import_count += 1
            import_pmax += pmax
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

    # Promote the largest internal generator proxy to slack/reference role.
    if len(net.gen):
        pmax_series = pd.to_numeric(net.gen["max_p_mw"], errors="coerce").fillna(0.0)
        slack_idx = int(pmax_series.idxmax())
        net.gen.loc[slack_idx, "slack"] = True

    return {
        "dispatchable_gen_count": gen_count,
        "dispatchable_gen_pmax_mw": gen_pmax,
        "import_proxy_count": import_count,
        "import_proxy_pmax_mw": import_pmax,
        "slack_gen_index": int(pmax_series.idxmax()) if len(net.gen) else "",
        "slack_gen_name": str(net.gen.loc[int(pmax_series.idxmax()), "name"]) if len(net.gen) else "",
    }


def set_load_scale(net, scale: float) -> float:
    if len(net.load):
        net.load["scaling"] = scale
        net.load["controllable"] = False
        return float((pd.to_numeric(net.load.loc[net.load["in_service"], "p_mw"], errors="coerce") * scale).sum())
    return 0.0


def gen_metrics(net) -> pd.DataFrame:
    gen = net.gen.copy().reset_index(names="gen_index") if len(net.gen) else pd.DataFrame()
    res = net.res_gen.copy().reset_index(names="gen_index") if len(net.res_gen) else pd.DataFrame()
    return gen.merge(res, on="gen_index", how="left", suffixes=("", "_res")) if len(gen) and len(res) else gen


def line_metrics(net) -> pd.DataFrame:
    line = net.line.copy().reset_index(names="line_index") if len(net.line) else pd.DataFrame()
    res = net.res_line.copy().reset_index(names="line_index") if len(net.res_line) else pd.DataFrame()
    out = line.merge(res, on="line_index", how="left", suffixes=("", "_res")) if len(line) and len(res) else line
    return out.sort_values("loading_percent", ascending=False) if len(out) and "loading_percent" in out.columns else out


def run_case(base_net, usable: pd.DataFrame, load_scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    supply_summary = attach_generators(net, usable)
    effective_load = set_load_scale(net, load_scale)

    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "load_scale": load_scale,
        "effective_load_p_mw": effective_load,
        **supply_summary,
        "converged": False,
        "error_type": "",
        "error": "",
        "objective_value": "",
        "total_gen_dispatch_mw": "",
        "max_line_loading_percent": "",
        "max_line_name": "",
        "publication_allowed": False,
        "diagnostic_only": True,
    }
    try:
        pp.rundcopp(net, verbose=False)
        row["converged"] = bool(net.OPF_converged)
        row["objective_value"] = float(net.res_cost)
        row["total_gen_dispatch_mw"] = float(net.res_gen["p_mw"].sum()) if len(net.res_gen) else 0.0
        row["max_line_loading_percent"] = float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0
        row["max_line_name"] = str(net.line.loc[int(net.res_line["loading_percent"].idxmax()), "name"]) if len(net.res_line) else ""
    except Exception as exc:
        row["error_type"] = type(exc).__name__
        row["error"] = str(exc)
    return row


def markdown_table(df: pd.DataFrame, max_rows: int = 120) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary: dict[str, Any], results: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 54 S21 No-ExtGrid Gen-Only DC OPF",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: remove ext_grid from the supply role entirely, designate one internal generator proxy as slack/reference, and test whether the proxy fleet can support any meaningful internal DC OPF dispatch.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Attempts",
        "",
        markdown_table(results, 120),
    ]
    (REPORTS / "54_s21_no_extgrid_gen_only_dcopf.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_net = load_net()
    usable = load_inputs()
    scales = [0.10, 0.20, 0.30]
    rows = [run_case(base_net, usable, scale) for scale in scales]
    results = pd.DataFrame(rows)
    results.to_csv(OUT / "s21_no_extgrid_dcopf_attempts.csv", index=False)
    converged = results[results["converged"] == True].copy()
    best = {}
    if len(converged):
        best = converged.sort_values(["load_scale", "total_gen_dispatch_mw"], ascending=[False, False]).iloc[0].to_dict()
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_id": SCENARIO_ID,
        "attempt_count": int(len(results)),
        "converged_count": int(results["converged"].sum()),
        "best_load_scale": best.get("load_scale", ""),
        "best_total_gen_dispatch_mw": best.get("total_gen_dispatch_mw", ""),
        "best_max_line_loading_percent": best.get("max_line_loading_percent", ""),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s21_no_extgrid_dcopf_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, results)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
