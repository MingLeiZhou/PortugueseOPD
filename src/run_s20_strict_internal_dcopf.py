"""S20 diagnostic scenario: strict internal DC OPF with constrained import."""

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
OUT = ROOT / "data" / "processed" / "dcopf_s20_strict_internal"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SCENARIO_ID = "S20_STRICT_INTERNAL_DCOPF"


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


def attach_generators(net, usable: pd.DataFrame, import_limit_factor: float, import_cost: float) -> dict[str, Any]:
    if len(net.gen):
        net.gen.drop(net.gen.index, inplace=True)
    if len(net.poly_cost):
        net.poly_cost.drop(net.poly_cost.index, inplace=True)

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
            eff_pmax = pmax
            cost = float(row.get("marginal_cost_eur_per_mwh", 100.0))
            gen_count += 1
            gen_pmax += eff_pmax
        else:
            eff_pmax = pmax * import_limit_factor
            cost = import_cost
            import_count += 1
            import_pmax += eff_pmax
        gen_idx = pp.create_gen(
            net,
            bus=bus,
            p_mw=0.0,
            vm_pu=1.0,
            min_p_mw=0.0,
            max_p_mw=max(0.0, eff_pmax),
            min_q_mvar=-0.1 * max(1.0, eff_pmax),
            max_q_mvar=0.1 * max(1.0, eff_pmax),
            controllable=True,
            name=str(row.get("candidate_id")),
        )
        pp.create_poly_cost(net, gen_idx, "gen", cp1_eur_per_mw=cost)

    return {
        "dispatchable_gen_count": gen_count,
        "dispatchable_gen_pmax_mw": gen_pmax,
        "import_proxy_count": import_count,
        "import_proxy_pmax_mw": import_pmax,
        "import_limit_factor": import_limit_factor,
        "import_cost_eur_per_mwh": import_cost,
    }


def set_load_scale(net, scale: float) -> None:
    if len(net.load):
        net.load["scaling"] = scale
        net.load["controllable"] = False


def effective_load(net) -> float:
    if not len(net.load):
        return 0.0
    active = net.load[net.load["in_service"]].copy() if "in_service" in net.load.columns else net.load.copy()
    scaling = pd.to_numeric(active.get("scaling", 1.0), errors="coerce").fillna(1.0)
    return float((pd.to_numeric(active["p_mw"], errors="coerce").fillna(0.0) * scaling).sum())


def run_case(base_net, usable: pd.DataFrame, variant: dict[str, Any], load_scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    attach_summary = attach_generators(net, usable, float(variant["import_limit_factor"]), float(variant["import_cost"] ))
    set_load_scale(net, load_scale)
    if len(net.ext_grid):
        net.ext_grid["controllable"] = False

    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "variant_id": variant["variant_id"],
        "variant_detail": variant["detail"],
        "load_scale": load_scale,
        **attach_summary,
        "effective_load_p_mw": effective_load(net),
        "converged": False,
        "error_type": "",
        "error": "",
        "objective_value": "",
        "total_gen_dispatch_mw": "",
        "total_ext_grid_dispatch_mw": "",
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
        row["total_ext_grid_dispatch_mw"] = float(net.res_ext_grid["p_mw"].sum()) if len(net.res_ext_grid) else 0.0
        row["max_line_loading_percent"] = float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0
        row["max_line_name"] = str(net.line.loc[int(net.res_line["loading_percent"].idxmax()), "name"]) if len(net.res_line) else ""
    except Exception as exc:
        row["error_type"] = type(exc).__name__
        row["error"] = str(exc)
    return row


def variant_plan() -> list[dict[str, Any]]:
    return [
        {"variant_id": "no_import", "detail": "Set import proxy pmax to zero", "import_limit_factor": 0.0, "import_cost": 500.0},
        {"variant_id": "limited_import_10pct", "detail": "Limit import proxy to 10% of nominal pmax", "import_limit_factor": 0.1, "import_cost": 300.0},
        {"variant_id": "limited_import_20pct", "detail": "Limit import proxy to 20% of nominal pmax", "import_limit_factor": 0.2, "import_cost": 200.0},
        {"variant_id": "penalty_import", "detail": "Keep full import pmax but assign strong penalty cost", "import_limit_factor": 1.0, "import_cost": 500.0},
    ]


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
        "# 79 S20 Strict Internal DC OPF",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: test whether the current backbone has any meaningful internal dispatch capability when import/interface supply is limited or penalized.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## All Attempts",
        "",
        markdown_table(results, 120),
    ]
    (REPORTS / "79_s20_strict_internal_dcopf.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_net = load_net()
    usable = load_inputs()
    variants = variant_plan()
    load_scales = [0.10, 0.20, 0.30]
    rows = [run_case(base_net, usable, variant, scale) for variant in variants for scale in load_scales]
    results = pd.DataFrame(rows)
    results.to_csv(OUT / "s20_strict_internal_dcopf_attempts.csv", index=False)

    converged = results[results["converged"] == True].copy()
    best = {}
    if len(converged):
        best = converged.sort_values(["load_scale", "total_gen_dispatch_mw"], ascending=[False, False]).iloc[0].to_dict()
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_id": SCENARIO_ID,
        "variant_count": len(variants),
        "attempt_count": int(len(results)),
        "converged_count": int(results["converged"].sum()),
        "best_variant_id": best.get("variant_id", ""),
        "best_load_scale": best.get("load_scale", ""),
        "best_total_gen_dispatch_mw": best.get("total_gen_dispatch_mw", ""),
        "best_total_ext_grid_dispatch_mw": best.get("total_ext_grid_dispatch_mw", ""),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s20_strict_internal_dcopf_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, results)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
