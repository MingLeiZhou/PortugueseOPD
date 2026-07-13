"""Create S16 backbone diagnostic core from the S15 depth-6 frontier choice."""

from __future__ import annotations

import copy
import json
import math
import os
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
OUT = ROOT / "data" / "processed" / "acpf_s16_backbone_core_depth6"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}
SCENARIO_ID = "S16_BACKBONE_DIAGNOSTIC_CORE_DEPTH6"
SLACK_BUS = 542
MAX_DEPTH = 6
SUGGESTION_PATH = LOAD_VALIDATION / "pt_load_reallocation_suggestions.csv"


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S16 backbone scenario.")
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
    pp.create_ext_grid(net, bus=SLACK_BUS, vm_pu=1.0, va_degree=0.0, name=f"S16_ext_grid_{SLACK_BUS}")


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
    return {"keep_buses": sorted(keep_buses), "distance_map": dist}


def effective_load(net, scale: float = 1.0) -> tuple[float, float]:
    if not len(net.load):
        return 0.0, 0.0
    active = net.load[net.load.in_service].copy() if "in_service" in net.load.columns else net.load.copy()
    p = float((pd.to_numeric(active["p_mw"], errors="coerce").fillna(0.0) * scale).sum())
    q = float((pd.to_numeric(active["q_mvar"], errors="coerce").fillna(0.0) * scale).sum())
    return p, q


def prepare_net(base_net, suggestions: pd.DataFrame) -> tuple[Any, dict[str, Any], dict[str, Any]]:
    net = copy.deepcopy(base_net)
    set_slack(net)
    suggestion_summary = apply_suggestions(net, suggestions)
    set_pf(net, 0.95)
    depth_info = apply_depth_cut(net, MAX_DEPTH)
    opts = getattr(net, "user_pf_options", {}) or {}
    opts.update(
        {
            "scenario_id": SCENARIO_ID,
            "publication_allowed": False,
            "operator_grade_ready": False,
            "opf_ready": False,
            "provenance_policy": "diagnostic_backbone_core_depth6",
            "load_reallocation_summary": suggestion_summary,
            "backbone_depth": MAX_DEPTH,
        }
    )
    net.user_pf_options = opts
    return net, suggestion_summary, depth_info


def run_reference_pf(net, scale: float = 0.5) -> dict[str, Any]:
    work = copy.deepcopy(net)
    if len(work.load):
        work.load["scaling"] = scale
    pp.runpp(
        work,
        algorithm="nr",
        init="dc",
        calculate_voltage_angles=True,
        enforce_q_lims=False,
        numba=False,
        max_iteration=120,
        tolerance_mva=1e-6,
    )
    max_line_idx = int(work.res_line["loading_percent"].idxmax()) if len(work.res_line) else -1
    max_bus_idx = int(work.res_bus["vm_pu"].idxmax()) if len(work.res_bus) else -1
    p_mw, q_mvar = effective_load(work, scale=scale)
    return {
        "reference_scale": scale,
        "effective_p_mw": p_mw,
        "effective_q_mvar": q_mvar,
        "min_vm_pu": float(work.res_bus["vm_pu"].min()) if len(work.res_bus) else "",
        "max_vm_pu": float(work.res_bus["vm_pu"].max()) if len(work.res_bus) else "",
        "max_line_loading_percent": float(work.res_line["loading_percent"].max()) if len(work.res_line) else "",
        "max_trafo_loading_percent": float(work.res_trafo["loading_percent"].max()) if len(work.res_trafo) else "",
        "worst_line_name": str(work.line.loc[max_line_idx, "name"]) if max_line_idx >= 0 else "",
        "worst_bus_name": str(work.bus.loc[max_bus_idx, "name"]) if max_bus_idx >= 0 else "",
    }


def markdown_table(df: pd.DataFrame, max_rows: int = 120) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary: dict[str, Any], suggestion_summary: dict[str, Any], depth_info: dict[str, Any], reference: dict[str, Any], bus_df: pd.DataFrame, line_df: pd.DataFrame, load_df: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 39 S16 Backbone Diagnostic Core Depth 6",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        f"Scenario: `{SCENARIO_ID}`",
        "",
        "Scope: formalize the best current backbone diagnostic core selected from S15: slack 542, load reallocation enabled, active subnetwork limited to graph depth 6 from the slack bus.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Load Reallocation Summary",
        "",
        markdown_table(pd.DataFrame([suggestion_summary])),
        "",
        "## Reference Power Flow at 50% Scale",
        "",
        markdown_table(pd.DataFrame([reference])),
        "",
        "## Backbone Buses",
        "",
        markdown_table(bus_df, 120),
        "",
        "## Backbone Lines",
        "",
        markdown_table(line_df, 120),
        "",
        "## Backbone Loads",
        "",
        markdown_table(load_df, 120),
    ]
    (REPORTS / "39_s16_backbone_diagnostic_core_depth6.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_net, opts = load_net_checked()
    suggestions = load_suggestions()
    net, suggestion_summary, depth_info = prepare_net(base_net, suggestions)
    pp.to_json(net, str(OUT / "s16_backbone_core_depth6_net.json"))

    bus_df = net.bus[net.bus.in_service].copy().reset_index(names="bus_index") if len(net.bus) else pd.DataFrame()
    line_df = net.line[net.line.in_service].copy().reset_index(names="line_index") if len(net.line) else pd.DataFrame()
    load_df = net.load[net.load.in_service].copy().reset_index(names="load_index") if len(net.load) else pd.DataFrame()
    bus_df.to_csv(OUT / "s16_backbone_buses.csv", index=False)
    line_df.to_csv(OUT / "s16_backbone_lines.csv", index=False)
    load_df.to_csv(OUT / "s16_backbone_loads.csv", index=False)

    reference = run_reference_pf(net, scale=0.5)
    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "base_readiness_status": opts.get("readiness_status"),
        "scenario_id": SCENARIO_ID,
        "slack_bus": SLACK_BUS,
        "max_depth": MAX_DEPTH,
        "kept_bus_count": int(len(bus_df)),
        "kept_line_count": int(len(line_df)),
        "kept_load_count": int(len(load_df)),
        "reference_scale": reference["reference_scale"],
        "reference_effective_p_mw": reference["effective_p_mw"],
        "reference_min_vm_pu": reference["min_vm_pu"],
        "reference_max_vm_pu": reference["max_vm_pu"],
        "reference_max_line_loading_percent": reference["max_line_loading_percent"],
        "reference_worst_line_name": reference["worst_line_name"],
        "publication_allowed": False,
        "operator_grade_ready": False,
        "opf_ready": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s16_backbone_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    write_report(summary, suggestion_summary, depth_info, reference, bus_df, line_df, load_df)
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
