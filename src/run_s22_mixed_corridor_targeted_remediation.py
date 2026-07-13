"""S22 diagnostic scenario: family-level remediation for key mixed corridors."""

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
OUT = ROOT / "data" / "processed" / "s22_mixed_corridor_remediation"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SCENARIO_ID = "S22_MIXED_CORRIDOR_TARGETED_REMEDIATION"

# Validated / inferred mixed-family weights.
WEIGHTS = {
    "ATPL_00003": {"overhead": 0.9934, "cable": 0.0066},
    "ATPL_00075": {"overhead": 0.7420, "cable": 0.2580},
    # No manual validation yet; use an overhead-dominant heuristic pending review.
    "ATPL_00304": {"overhead": 0.75, "cable": 0.25},
}


def load_net() -> Any:
    if not NET_PATH.exists():
        raise RuntimeError(f"Missing backbone net: {NET_PATH}")
    return pp.from_json(NET_PATH)


def load_proxy_inputs() -> pd.DataFrame:
    proxies = pd.read_csv(PROXY_PATH) if PROXY_PATH.exists() else pd.DataFrame()
    costs = pd.read_csv(COST_PATH) if COST_PATH.exists() else pd.DataFrame()
    usable = proxies[proxies["dispatch_proxy_class"].isin(["dispatchable_proxy", "import_interface_proxy"])].copy() if len(proxies) else pd.DataFrame()
    usable = usable.merge(costs[["candidate_id", "marginal_cost_eur_per_mwh"]], on="candidate_id", how="left") if len(usable) and len(costs) else usable
    return usable


def infer_reference_lines(net) -> tuple[pd.Series, pd.Series]:
    line = net.line.copy()
    cable_pool = line[pd.to_numeric(line["c_nf_per_km"], errors="coerce") >= 250].copy()
    overhead_pool = line[pd.to_numeric(line["c_nf_per_km"], errors="coerce") < 50].copy()
    if cable_pool.empty or overhead_pool.empty:
        raise RuntimeError("Could not infer cable/overhead pools from backbone line table.")
    cable = cable_pool.sort_values("length_km", ascending=False).iloc[0]
    overhead = overhead_pool.sort_values("length_km", ascending=False).iloc[0]
    return overhead, cable


def remediate_mixed_corridors(net) -> pd.DataFrame:
    overhead, cable = infer_reference_lines(net)
    records = []
    for line_name, weights in WEIGHTS.items():
        mask = net.line["name"].astype(str) == line_name
        if not mask.any():
            continue
        idx = int(net.line[mask].index[0])
        old = net.line.loc[idx].copy()
        oh_w = float(weights["overhead"])
        cb_w = float(weights["cable"])
        net.line.loc[idx, "r_ohm_per_km"] = oh_w * float(overhead["r_ohm_per_km"]) + cb_w * float(cable["r_ohm_per_km"])
        net.line.loc[idx, "x_ohm_per_km"] = oh_w * float(overhead["x_ohm_per_km"]) + cb_w * float(cable["x_ohm_per_km"])
        net.line.loc[idx, "c_nf_per_km"] = oh_w * float(overhead["c_nf_per_km"]) + cb_w * float(cable["c_nf_per_km"])
        net.line.loc[idx, "max_i_ka"] = min(float(overhead["max_i_ka"]), float(cable["max_i_ka"]))
        records.append(
            {
                "line_name": line_name,
                "line_index": idx,
                "overhead_weight": oh_w,
                "cable_weight": cb_w,
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
        )
    return pd.DataFrame(records)


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


def run_case(base_net, usable: pd.DataFrame, scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    remediation_df = remediate_mixed_corridors(net)
    attach_generators(net, usable)
    effective_load = set_load_scale(net, scale)

    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "load_scale": scale,
        "effective_load_p_mw": effective_load,
        "remediated_line_count": int(len(remediation_df)),
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


def write_report(summary: dict[str, Any], remediation_df: pd.DataFrame, results: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 57 S22 Mixed Corridor Targeted Remediation",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: apply a family-level remediation to critical mixed corridors (`ATPL_00003`, `ATPL_00075`, `ATPL_00304`) and test whether DC OPF bottlenecks become less severe.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Remediated Corridors",
        "",
        markdown_table(remediation_df, 80),
        "",
        "## DC OPF Attempts",
        "",
        markdown_table(results, 80),
    ]
    (REPORTS / "57_s22_mixed_corridor_targeted_remediation.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_net = load_net()
    usable = load_proxy_inputs()
    remediation_df = remediate_mixed_corridors(copy.deepcopy(base_net))
    remediation_df.to_csv(OUT / "s22_mixed_corridor_remediation_summary.csv", index=False)

    scales = [0.10, 0.20, 0.30]
    rows = [run_case(base_net, usable, scale) for scale in scales]
    results = pd.DataFrame(rows)
    results.to_csv(OUT / "s22_dcopf_attempts.csv", index=False)
    converged = results[results["converged"] == True].copy()
    best = {}
    if len(converged):
        best = converged.sort_values(["load_scale", "total_gen_dispatch_mw"], ascending=[False, False]).iloc[0].to_dict()
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scenario_id": SCENARIO_ID,
        "remediated_line_count": int(len(remediation_df)),
        "attempt_count": int(len(results)),
        "converged_count": int(results["converged"].sum()),
        "best_load_scale": best.get("load_scale", ""),
        "best_total_gen_dispatch_mw": best.get("total_gen_dispatch_mw", ""),
        "best_max_line_loading_percent": best.get("max_line_loading_percent", ""),
        "best_max_line_name": best.get("max_line_name", ""),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s22_dcopf_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, remediation_df, results)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
