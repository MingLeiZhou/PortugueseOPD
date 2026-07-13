"""Validate Portuguese ACPF load allocation against topology and transformer capacity.

This script writes diagnostics and suggestions only. It does not overwrite the
base ACPF-ready load table.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
READY = ROOT / "data" / "processed" / "acpf_ready"
OUT = ROOT / "data" / "processed" / "load_validation"
REPORTS = ROOT / "reports"

HIGH_LOAD_TO_SN = 0.8
DEEP_LOAD_DISTANCE = 12
CAP_TARGET_RATIO = 0.75


def read(name: str) -> pd.DataFrame:
    return pd.read_csv(READY / name)


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def build_graph(line: pd.DataFrame, trafo: pd.DataFrame) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for _, row in line[line["in_service"]].iterrows():
        a, b = str(row["from_bus"]), str(row["to_bus"])
        graph[a].add(b)
        graph[b].add(a)
    for _, row in trafo[trafo["in_service"]].iterrows():
        a, b = str(row["hv_bus"]), str(row["lv_bus"])
        graph[a].add(b)
        graph[b].add(a)
    return graph


def distances(graph: dict[str, set[str]], source: str) -> dict[str, int]:
    dist = {source: 0}
    q: deque[str] = deque([source])
    while q:
        cur = q.popleft()
        for nxt in graph.get(cur, set()):
            if nxt not in dist:
                dist[nxt] = dist[cur] + 1
                q.append(nxt)
    return dist


def component_ids(graph: dict[str, set[str]]) -> dict[str, int]:
    comp: dict[str, int] = {}
    idx = 0
    for node in graph:
        if node in comp:
            continue
        q: deque[str] = deque([node])
        comp[node] = idx
        while q:
            cur = q.popleft()
            for nxt in graph[cur]:
                if nxt not in comp:
                    comp[nxt] = idx
                    q.append(nxt)
        idx += 1
    return comp


def load_validation(bus: pd.DataFrame, line: pd.DataFrame, trafo: pd.DataFrame, load: pd.DataFrame, ext: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    graph = build_graph(line, trafo)
    comp = component_ids(graph)
    selected_ext = ext[ext["in_service"]]
    slack = str(selected_ext.iloc[0]["bus_id"]) if len(selected_ext) else ""
    dist = distances(graph, slack) if slack else {}

    active_load = load[load["in_service"]].copy()
    active_trafo = trafo[trafo["in_service"]].copy()
    trafo_by_lv = active_trafo.set_index("lv_bus", drop=False).to_dict("index") if len(active_trafo) else {}
    trafo_by_fac = active_trafo.groupby("facility_code")["sn_mva"].sum().to_dict() if len(active_trafo) else {}

    rows: list[dict[str, Any]] = []
    for _, row in active_load.iterrows():
        bus_id = str(row["bus_id"])
        facility = str(row.get("facility_code", ""))
        p = float(row.get("p_mw") or 0.0)
        q = float(row.get("q_mvar") or 0.0)
        s = math.sqrt(p * p + q * q)
        local_trafo = trafo_by_lv.get(bus_id, {})
        sn = float(local_trafo.get("sn_mva") or trafo_by_fac.get(facility, 0.0) or 0.0)
        ratio = s / sn if sn else math.nan
        distance = dist.get(bus_id)
        flags = []
        if distance is None:
            flags.append("UNREACHABLE_FROM_SLACK")
        elif distance > DEEP_LOAD_DISTANCE:
            flags.append("DEEP_LOAD_PATH")
        if sn and ratio > HIGH_LOAD_TO_SN:
            flags.append("HIGH_LOAD_TO_TRANSFORMER_CAPACITY")
        if not sn:
            flags.append("NO_LOCAL_TRANSFORMER_CAPACITY_MATCH")
        rows.append(
            {
                "load_id": row.get("load_id"),
                "bus_id": bus_id,
                "facility_code": facility,
                "p_mw": p,
                "q_mvar": q,
                "s_mva_proxy": s,
                "distance_to_slack_edges": distance if distance is not None else "",
                "component_id": comp.get(bus_id, ""),
                "local_trafo_id": local_trafo.get("trafo_id", ""),
                "local_trafo_sn_mva": sn,
                "load_to_trafo_sn_ratio": ratio if not math.isnan(ratio) else "",
                "validation_flags": ";".join(flags),
                "load_status": "CHECK" if flags else "OK_DIAGNOSTIC",
                "source_status": row.get("p_status", ""),
                "q_status": row.get("q_status", ""),
                "publication_allowed": False,
            }
        )
    load_df = pd.DataFrame(rows)

    if load_df.empty:
        transformer_df = pd.DataFrame()
    else:
        transformer_df = (
            load_df.groupby(["facility_code", "local_trafo_id", "local_trafo_sn_mva"], dropna=False)
            .agg(load_count=("load_id", "count"), p_mw=("p_mw", "sum"), q_mvar=("q_mvar", "sum"), s_mva_proxy=("s_mva_proxy", "sum"), max_distance_to_slack=("distance_to_slack_edges", "max"))
            .reset_index()
        )
        transformer_df["load_to_trafo_sn_ratio"] = transformer_df["s_mva_proxy"] / transformer_df["local_trafo_sn_mva"].replace({0: math.nan})
        transformer_df["validation_status"] = transformer_df["load_to_trafo_sn_ratio"].apply(lambda x: "CHECK_HIGH_LOAD" if pd.notna(x) and x > HIGH_LOAD_TO_SN else "OK_DIAGNOSTIC")

    if load_df.empty:
        depth_df = pd.DataFrame()
    else:
        tmp = load_df.copy()
        tmp["depth_bucket"] = pd.cut(pd.to_numeric(tmp["distance_to_slack_edges"], errors="coerce"), bins=[-1, 3, 6, 9, 12, 99], labels=["0-3", "4-6", "7-9", "10-12", "13+"])
        depth_df = tmp.groupby("depth_bucket", dropna=False).agg(load_count=("load_id", "count"), p_mw=("p_mw", "sum"), s_mva_proxy=("s_mva_proxy", "sum")).reset_index()

    suggestions: list[dict[str, Any]] = []
    for _, row in load_df.iterrows():
        ratio = pd.to_numeric(pd.Series([row.get("load_to_trafo_sn_ratio")]), errors="coerce").iloc[0]
        suggested_scale = 1.0
        action = "keep"
        if pd.notna(ratio) and ratio > CAP_TARGET_RATIO and row.get("local_trafo_sn_mva"):
            suggested_scale = min(suggested_scale, CAP_TARGET_RATIO / ratio)
            action = "cap_to_transformer_capacity_scenario"
        distance = pd.to_numeric(pd.Series([row.get("distance_to_slack_edges")]), errors="coerce").iloc[0]
        if pd.notna(distance) and distance > DEEP_LOAD_DISTANCE:
            suggested_scale = min(suggested_scale, 0.5)
            action = "deep_path_sensitivity_scale"
        if action != "keep":
            suggestions.append(
                {
                    "load_id": row["load_id"],
                    "bus_id": row["bus_id"],
                    "current_p_mw": row["p_mw"],
                    "suggested_scale": round(float(suggested_scale), 4),
                    "suggested_p_mw": round(float(row["p_mw"] * suggested_scale), 6),
                    "action": action,
                    "scenario_status": "DIAGNOSTIC_SUGGESTION_ONLY",
                    "publication_allowed": False,
                }
            )
    suggestions_df = pd.DataFrame(suggestions)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_load_count": int(len(load_df)),
        "total_p_mw": float(load_df["p_mw"].sum()) if len(load_df) else 0.0,
        "total_q_mvar": float(load_df["q_mvar"].sum()) if len(load_df) else 0.0,
        "flagged_load_count": int(load_df["validation_flags"].astype(str).ne("").sum()) if len(load_df) else 0,
        "high_transformer_load_count": int((pd.to_numeric(transformer_df.get("load_to_trafo_sn_ratio", pd.Series(dtype=float)), errors="coerce") > HIGH_LOAD_TO_SN).sum()) if len(transformer_df) else 0,
        "deep_load_count": int((pd.to_numeric(load_df.get("distance_to_slack_edges", pd.Series(dtype=float)), errors="coerce") > DEEP_LOAD_DISTANCE).sum()) if len(load_df) else 0,
        "suggestion_count": int(len(suggestions_df)),
        "status": "DIAGNOSTIC_DONE",
        "publication_allowed": False,
    }
    return load_df, transformer_df, depth_df, suggestions_df, summary


def markdown_table(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(load_df: pd.DataFrame, transformer_df: pd.DataFrame, depth_df: pd.DataFrame, suggestions_df: pd.DataFrame, summary: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    flagged = load_df[load_df["validation_flags"].astype(str).ne("")].sort_values("s_mva_proxy", ascending=False) if len(load_df) else pd.DataFrame()
    text = [
        "# 17 Load Allocation Validation",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: diagnostic validation only. This report does not overwrite ACPF-ready loads and does not claim measured hourly load correctness.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Flagged Loads",
        "",
        markdown_table(flagged, 25),
        "",
        "## Transformer Load Validation",
        "",
        markdown_table(transformer_df.sort_values("load_to_trafo_sn_ratio", ascending=False) if len(transformer_df) else transformer_df, 20),
        "",
        "## Load By Depth Bucket",
        "",
        markdown_table(depth_df, 10),
        "",
        "## Diagnostic Reallocation Suggestions",
        "",
        markdown_table(suggestions_df, 25),
    ]
    (REPORTS / "17_load_allocation_validation.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    bus = read("pt_bus_table_acpf.csv")
    line = read("pt_line_table_acpf.csv")
    trafo = read("pt_trafo_table_acpf.csv")
    load = read("pt_load_table_acpf.csv")
    ext = read("pt_ext_grid_table_acpf.csv")
    load_df, transformer_df, depth_df, suggestions_df, summary = load_validation(bus, line, trafo, load, ext)
    load_df.to_csv(OUT / "pt_load_allocation_validation.csv", index=False)
    transformer_df.to_csv(OUT / "pt_transformer_load_validation.csv", index=False)
    depth_df.to_csv(OUT / "pt_load_depth_summary.csv", index=False)
    suggestions_df.to_csv(OUT / "pt_load_reallocation_suggestions.csv", index=False)
    (OUT / "pt_load_validation_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(load_df, transformer_df, depth_df, suggestions_df, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
