"""S15 diagnostic scenario: active-subnetwork reduction/expansion frontier around slack 542."""

from __future__ import annotations

import copy
import json
import math
import os
import traceback
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

import pandas as pd
import pandapower as pp

READY = ROOT / "data" / "processed" / "acpf_ready"
LOAD_VALIDATION = ROOT / "data" / "processed" / "load_validation"
OUT = ROOT / "data" / "processed" / "acpf_s15_active_subnetwork_frontier"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}
SCENARIO_ID = "S15_ACTIVE_SUBNETWORK_REDUCTION_FRONTIER"
SLACK_BUS = 542
SUGGESTION_PATH = LOAD_VALIDATION / "pt_load_reallocation_suggestions.csv"


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S15 diagnostic scenario.")
    return net, opts


def load_suggestions() -> pd.DataFrame:
    if not SUGGESTION_PATH.exists():
        raise RuntimeError(f"Missing load reallocation suggestion file: {SUGGESTION_PATH}")
    return pd.read_csv(SUGGESTION_PATH)


def set_slack(net) -> None:
    if SLACK_BUS not in net.bus.index:
        raise RuntimeError(f"Slack bus {SLACK_BUS} is not present in net.bus")
    if len(net.ext_grid):
        net.ext_grid["in_service"] = False
    pp.create_ext_grid(net, bus=SLACK_BUS, vm_pu=1.0, va_degree=0.0, name=f"S15_ext_grid_{SLACK_BUS}")


def apply_suggestions(net, suggestions: pd.DataFrame) -> dict[str, Any]:
    if not len(net.load):
        return {"modified_load_count": 0, "total_p_before": 0.0, "total_p_after": 0.0}
    load = net.load.copy()
    before = float(pd.to_numeric(load["p_mw"], errors="coerce").fillna(0.0).sum())
    modified = 0
    for _, row in suggestions.iterrows():
        load_id = str(row.get("load_id", ""))
        scale = float(row.get("suggested_scale", 1.0))
        mask = load["name"].astype(str) == load_id
        if mask.any():
            load.loc[mask, "p_mw"] = pd.to_numeric(load.loc[mask, "p_mw"], errors="coerce") * scale
            load.loc[mask, "q_mvar"] = pd.to_numeric(load.loc[mask, "q_mvar"], errors="coerce") * scale
            modified += int(mask.sum())
    after = float(pd.to_numeric(load["p_mw"], errors="coerce").fillna(0.0).sum())
    net.load = load
    return {"modified_load_count": modified, "total_p_before": before, "total_p_after": after}


def set_pf(net, pf: float = 0.95) -> None:
    if len(net.load):
        net.load["q_mvar"] = net.load["p_mw"] * math.tan(math.acos(pf))


def build_graph(net) -> dict[int, set[int]]:
    graph: dict[int, set[int]] = defaultdict(set)
    if len(net.line):
        for _, row in net.line[net.line.in_service].iterrows():
            a, b = int(row["from_bus"]), int(row["to_bus"])
            graph[a].add(b)
            graph[b].add(a)
    if len(net.trafo):
        for _, row in net.trafo[net.trafo.in_service].iterrows():
            a, b = int(row["hv_bus"]), int(row["lv_bus"])
            graph[a].add(b)
            graph[b].add(a)
    return graph


def distances(graph: dict[int, set[int]], source: int) -> dict[int, int]:
    dist = {source: 0}
    q: deque[int] = deque([source])
    while q:
        cur = q.popleft()
        for nxt in graph.get(cur, set()):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    return dist


def apply_depth_cut(net, max_depth: int) -> dict[str, Any]:
    graph = build_graph(net)
    dist = distances(graph, SLACK_BUS)
    keep_buses = {bus for bus, d in dist.items() if d <= max_depth}
    if len(net.bus):
        net.bus["in_service"] = net.bus.index.to_series().astype(int).isin(keep_buses)
    if len(net.line):
        net.line["in_service"] = net.line["from_bus"].astype(int).isin(keep_buses) & net.line["to_bus"].astype(int).isin(keep_buses)
    if len(net.trafo):
        net.trafo["in_service"] = net.trafo["hv_bus"].astype(int).isin(keep_buses) & net.trafo["lv_bus"].astype(int).isin(keep_buses)
    if len(net.load):
        net.load["in_service"] = net.load["bus"].astype(int).isin(keep_buses)
    if len(net.ext_grid):
        net.ext_grid["in_service"] = net.ext_grid["bus"].astype(int).isin(keep_buses)
    return {
        "max_depth": max_depth,
        "kept_bus_count": len(keep_buses),
        "max_distance_present": max(dist.values()) if dist else 0,
    }


def set_scale(net, scale: float) -> None:
    if len(net.load):
        net.load["scaling"] = scale


def effective_load(net) -> tuple[float, float]:
    if not len(net.load):
        return 0.0, 0.0
    active = net.load[net.load.in_service].copy() if "in_service" in net.load.columns else net.load.copy()
    scaling = pd.to_numeric(active.get("scaling", 1.0), errors="coerce").fillna(1.0)
    p = float((pd.to_numeric(active["p_mw"], errors="coerce").fillna(0.0) * scaling).sum())
    q = float((pd.to_numeric(active["q_mvar"], errors="coerce").fillna(0.0) * scaling).sum())
    return p, q


def prepare_net(base_net, suggestions: pd.DataFrame) -> tuple[Any, dict[str, Any]]:
    net = copy.deepcopy(base_net)
    set_slack(net)
    suggestion_summary = apply_suggestions(net, suggestions)
    set_pf(net, 0.95)
    opts = getattr(net, "user_pf_options", {}) or {}
    opts.update(
        {
            "scenario_id": SCENARIO_ID,
            "publication_allowed": False,
            "operator_grade_ready": False,
            "opf_ready": False,
            "provenance_policy": "diagnostic_active_subnetwork_frontier",
            "load_reallocation_summary": suggestion_summary,
        }
    )
    net.user_pf_options = opts
    return net, suggestion_summary


def run_case(base_net, max_depth: int, scale: float) -> dict[str, Any]:
    net = copy.deepcopy(base_net)
    depth_info = apply_depth_cut(net, max_depth)
    set_scale(net, scale)
    p_mw, q_mvar = effective_load(net)
    row: dict[str, Any] = {
        "scenario_id": SCENARIO_ID,
        "max_depth": max_depth,
        "load_scale": scale,
        "effective_p_mw": p_mw,
        "effective_q_mvar": q_mvar,
        "kept_bus_count": depth_info["kept_bus_count"],
        "max_distance_present": depth_info["max_distance_present"],
        "active_line_count": int(net.line["in_service"].sum()) if len(net.line) else 0,
        "active_trafo_count": int(net.trafo["in_service"].sum()) if len(net.trafo) else 0,
        "active_load_count": int(net.load["in_service"].sum()) if len(net.load) else 0,
        "converged": False,
        "error_type": "",
        "error": "",
        "min_vm_pu": "",
        "max_vm_pu": "",
        "max_line_loading_percent": "",
        "max_trafo_loading_percent": "",
        "worst_line_name": "",
        "worst_bus_name": "",
        "publication_allowed": False,
        "diagnostic_only": True,
    }
    try:
        pp.runpp(
            net,
            algorithm="nr",
            init="dc",
            calculate_voltage_angles=True,
            enforce_q_lims=False,
            numba=False,
            max_iteration=120,
            tolerance_mva=1e-6,
        )
        row["converged"] = bool(net.converged)
        if net.converged:
            max_line_idx = int(net.res_line["loading_percent"].idxmax()) if len(net.res_line) else -1
            max_bus_idx = int(net.res_bus["vm_pu"].idxmax()) if len(net.res_bus) else -1
            row.update(
                {
                    "min_vm_pu": float(net.res_bus["vm_pu"].min()) if len(net.res_bus) else "",
                    "max_vm_pu": float(net.res_bus["vm_pu"].max()) if len(net.res_bus) else "",
                    "max_line_loading_percent": float(net.res_line["loading_percent"].max()) if len(net.res_line) else "",
                    "max_trafo_loading_percent": float(net.res_trafo["loading_percent"].max()) if len(net.res_trafo) else "",
                    "worst_line_name": str(net.line.loc[max_line_idx, "name"]) if max_line_idx >= 0 else "",
                    "worst_bus_name": str(net.bus.loc[max_bus_idx, "name"]) if max_bus_idx >= 0 else "",
                }
            )
    except Exception as exc:
        row["error_type"] = type(exc).__name__
        row["error"] = str(exc)
        row["traceback"] = traceback.format_exc(limit=3)
    return row


def markdown_table(df: pd.DataFrame, max_rows: int = 160) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary: dict[str, Any], suggestion_summary: dict[str, Any], results: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    by_depth = results.groupby("max_depth")["converged"].agg(attempts="count", converged="sum").reset_index()
    converged = results[results["converged"] == True].copy()
    if len(converged):
        best = converged.sort_values(["load_scale", "kept_bus_count", "min_vm_pu"], ascending=[False, False, False])
    else:
        best = converged
    text = [
        "# 38 S15 Active Subnetwork Reduction Frontier",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: build on the slack-542/load-reallocation setup and progressively restrict the active subnetwork by graph depth from the slack bus to identify the strongest solvable core and the expansion depth that reintroduces instability.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Load Reallocation Summary",
        "",
        markdown_table(pd.DataFrame([suggestion_summary])),
        "",
        "## Convergence By Depth",
        "",
        markdown_table(by_depth, 40),
        "",
        "## Best Converged Attempts",
        "",
        markdown_table(best, 120),
        "",
        "## All Attempts",
        "",
        markdown_table(results, 240),
    ]
    (REPORTS / "38_s15_active_subnetwork_reduction_frontier.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_net, opts = load_net_checked()
    suggestions = load_suggestions()
    net, suggestion_summary = prepare_net(base_net, suggestions)
    depths = [2, 3, 4, 5, 6, 8, 10, 12]
    scales = [0.10, 0.20, 0.30, 0.50]
    rows = [run_case(net, depth, scale) for depth in depths for scale in scales]
    results = pd.DataFrame(rows)
    results.to_csv(OUT / "s15_active_subnetwork_frontier_attempts.csv", index=False)
    converged = results[results["converged"] == True].copy()
    best = {}
    if len(converged):
        best = converged.sort_values(["load_scale", "kept_bus_count", "min_vm_pu"], ascending=[False, False, False]).iloc[0].to_dict()
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_readiness_status": opts.get("readiness_status"),
        "scenario_id": SCENARIO_ID,
        "slack_bus": SLACK_BUS,
        "depth_count": len(depths),
        "attempt_count": int(len(results)),
        "converged_count": int(results["converged"].sum()),
        "best_max_depth": best.get("max_depth", ""),
        "best_load_scale": best.get("load_scale", ""),
        "best_kept_bus_count": best.get("kept_bus_count", ""),
        "best_min_vm_pu": best.get("min_vm_pu", ""),
        "best_max_vm_pu": best.get("max_vm_pu", ""),
        "best_max_line_loading_percent": best.get("max_line_loading_percent", ""),
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s15_active_subnetwork_frontier_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, suggestion_summary, results)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
