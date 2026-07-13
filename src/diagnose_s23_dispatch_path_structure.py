"""Diagnose dispatch concentration and transfer-path structure for internal DC OPF."""

from __future__ import annotations

import copy
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import pandapower as pp

ROOT = Path(__file__).resolve().parents[1]
BACKBONE = ROOT / "data" / "processed" / "acpf_s16_backbone_core_depth6"
DISPATCH = ROOT / "data" / "processed" / "generator_dispatch_proxies"
COSTS = ROOT / "data" / "processed" / "generator_costs"
OUT = ROOT / "data" / "processed" / "dcopf_s23_dispatch_path"
REPORTS = ROOT / "reports"
NET_PATH = BACKBONE / "s16_backbone_core_depth6_net.json"
PROXY_PATH = DISPATCH / "pt_generator_dispatch_proxy_table.csv"
COST_PATH = COSTS / "pt_generator_cost_scenarios.csv"
SCENARIO_ID = "S23_DISPATCH_PATH_STRUCTURE_DIAGNOSIS"
LOAD_SCALE = 0.3


def load_net() -> Any:
    return pp.from_json(NET_PATH)


def load_inputs() -> pd.DataFrame:
    proxies = pd.read_csv(PROXY_PATH)
    costs = pd.read_csv(COST_PATH)
    usable = proxies[proxies["dispatch_proxy_class"].isin(["dispatchable_proxy", "import_interface_proxy"])].copy()
    usable = usable.merge(costs[["candidate_id", "marginal_cost_eur_per_mwh"]], on="candidate_id", how="left")
    return usable


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


def set_load_scale(net, scale: float) -> None:
    if len(net.load):
        net.load["scaling"] = scale
        net.load["controllable"] = False


def run_case() -> Any:
    net = load_net()
    usable = load_inputs()
    attach_generators(net, usable)
    set_load_scale(net, LOAD_SCALE)
    pp.rundcopp(net, verbose=False)
    return net


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


def shortest_path(graph: dict[int, set[int]], src: int, dst: int) -> list[int]:
    if src == dst:
        return [src]
    q: deque[int] = deque([src])
    parent = {src: None}
    while q:
        cur = q.popleft()
        for nxt in graph.get(cur, set()):
            if nxt not in parent:
                parent[nxt] = cur
                if nxt == dst:
                    path = [dst]
                    while parent[path[-1]] is not None:
                        path.append(parent[path[-1]])
                    return list(reversed(path))
                q.append(nxt)
    return []


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    net = run_case()

    gen = net.gen.copy().reset_index(names="gen_index")
    res_gen = net.res_gen.copy().reset_index(names="gen_index") if len(net.res_gen) else pd.DataFrame()
    gen_df = gen.merge(res_gen, on="gen_index", how="left", suffixes=("", "_res")) if len(res_gen) else gen
    gen_df = gen_df.sort_values("p_mw_res", ascending=False)

    load = net.load.copy().reset_index(names="load_index")
    active_load = load[load["in_service"]].copy() if len(load) else pd.DataFrame()
    active_load["effective_p_mw"] = pd.to_numeric(active_load["p_mw"], errors="coerce") * pd.to_numeric(active_load["scaling"], errors="coerce").fillna(1.0) if len(active_load) else pd.Series(dtype=float)
    active_load = active_load.sort_values("effective_p_mw", ascending=False)

    line = net.line.copy().reset_index(names="line_index")
    res_line = net.res_line.copy().reset_index(names="line_index") if len(net.res_line) else pd.DataFrame()
    line_df = line.merge(res_line, on="line_index", how="left", suffixes=("", "_res")) if len(res_line) else line
    line_df = line_df.sort_values("loading_percent", ascending=False)

    graph = build_graph(net)
    top_gen = gen_df.head(5).copy()
    top_loads = active_load.head(10).copy()

    path_rows = []
    for _, g in top_gen.iterrows():
        gbus = int(g["bus"])
        gname = str(g.get("name", ""))
        for _, l in top_loads.iterrows():
            lbus = int(l["bus"])
            lname = str(l.get("name", ""))
            path = shortest_path(graph, gbus, lbus)
            path_rows.append(
                {
                    "generator_name": gname,
                    "generator_bus": gbus,
                    "generator_dispatch_mw": float(g.get("p_mw_res", 0.0)),
                    "load_name": lname,
                    "load_bus": lbus,
                    "load_effective_p_mw": float(l.get("effective_p_mw", 0.0)),
                    "path_length_buses": len(path),
                    "path_bus_sequence": " -> ".join(str(x) for x in path),
                }
            )
    path_df = pd.DataFrame(path_rows)

    top_gen.to_csv(OUT / "s23_top_dispatch_generators.csv", index=False)
    top_loads.to_csv(OUT / "s23_top_loads.csv", index=False)
    line_df.to_csv(OUT / "s23_line_results.csv", index=False)
    path_df.to_csv(OUT / "s23_generator_to_load_paths.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "load_scale": LOAD_SCALE,
        "total_gen_dispatch_mw": float(gen_df["p_mw_res"].sum()) if len(gen_df) else 0.0,
        "top_generator_name": str(gen_df.iloc[0]["name"]) if len(gen_df) else "",
        "top_generator_dispatch_mw": float(gen_df.iloc[0]["p_mw_res"]) if len(gen_df) else 0.0,
        "worst_line_name": str(line_df.iloc[0]["name"]) if len(line_df) else "",
        "worst_line_loading_percent": float(line_df.iloc[0]["loading_percent"]) if len(line_df) else 0.0,
        "top_load_name": str(top_loads.iloc[0]["name"]) if len(top_loads) else "",
        "top_load_effective_p_mw": float(top_loads.iloc[0]["effective_p_mw"]) if len(top_loads) else 0.0,
        "publication_allowed": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s23_dispatch_path_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    def markdown_table(df: pd.DataFrame, max_rows: int = 80) -> str:
        if df.empty:
            return "_No rows._\n"
        view = df.head(max_rows)
        cols = list(view.columns)
        lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
        for _, row in view.iterrows():
            lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
        return "\n".join(lines) + "\n"

    text = [
        "# 58 S23 Dispatch Path Structure Diagnosis",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: characterize which generators dispatch, which loads dominate, and which network corridors bind first under the internal DC OPF configuration.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Top Dispatch Generators",
        "",
        markdown_table(top_gen, 20),
        "",
        "## Top Loads",
        "",
        markdown_table(top_loads, 20),
        "",
        "## Top Loaded Lines",
        "",
        markdown_table(line_df[[c for c in ["line_index", "name", "from_bus", "to_bus", "loading_percent", "p_from_mw", "p_to_mw"] if c in line_df.columns]], 40),
        "",
        "## Generator-to-Load Path Skeletons",
        "",
        markdown_table(path_df, 80),
        "",
        "## Interpretation",
        "",
        "The next remediation step should focus on the dominant dispatch corridor linking the top-dispatch generator region to the highest-load buses. If the same mixed-family or short bridge lines recur across many generator-load paths, they should be treated as OPF-grade transfer bottlenecks rather than isolated PF anomalies.",
    ]
    (REPORTS / "58_s23_dispatch_path_structure_diagnosis.md").write_text("\n".join(text), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
