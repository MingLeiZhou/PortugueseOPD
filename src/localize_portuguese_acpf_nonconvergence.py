"""Localize Portuguese ACPF non-convergence by controlled topology ablations.

The experiments are diagnostic only. A converged ablation is not treated as an
electrically validated result, and convergence caused by disconnecting load is
reported separately from convergence that preserves the supplied component.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import os
import warnings
from collections import deque
from itertools import combinations
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/portugueseopd-matplotlib")

import networkx as nx
import numpy as np
import pandas as pd
import pandapower as pp

import config
from utils import ensure_directories, utc_now, write_json, write_text


ROOT = config.ROOT_DIR
NET_PATH = config.PROCESSED_DIR / "acpf_ready" / "pt_acpf_pandapower_net.json"
OUT = config.PROCESSED_DIR / "acpf_localization"
REPORT = config.REPORTS_DIR / "99_acpf_nonconvergence_localization.md"


def active(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def active_graph(net: Any) -> nx.Graph:
    graph = nx.Graph()
    active_buses = {int(index) for index, row in net.bus.iterrows() if active(row.get("in_service", True))}
    graph.add_nodes_from(active_buses)
    for _, row in net.line.iterrows():
        if active(row.get("in_service", True)) and int(row.from_bus) in active_buses and int(row.to_bus) in active_buses:
            graph.add_edge(int(row.from_bus), int(row.to_bus), element_type="line")
    for _, row in net.trafo.iterrows():
        if active(row.get("in_service", True)) and int(row.hv_bus) in active_buses and int(row.lv_bus) in active_buses:
            graph.add_edge(int(row.hv_bus), int(row.lv_bus), element_type="trafo")
    return graph


def slack_bus(net: Any) -> int:
    ext = net.ext_grid[net.ext_grid["in_service"].map(active)]
    if len(ext) != 1:
        raise RuntimeError(f"Fail-closed: expected exactly one active ext_grid, found {len(ext)}")
    return int(ext.iloc[0].bus)


def supplied_context(net: Any) -> dict[str, float | int]:
    graph = active_graph(net)
    slack = slack_bus(net)
    supplied = nx.node_connected_component(graph, slack) if slack in graph else {slack}
    loads = net.load[net.load["in_service"].map(active)].copy()
    loads["p_mw"] = pd.to_numeric(loads["p_mw"], errors="coerce").fillna(0.0)
    loads["q_mvar"] = pd.to_numeric(loads["q_mvar"], errors="coerce").fillna(0.0)
    supplied_mask = loads["bus"].astype(int).isin(supplied)
    return {
        "active_component_buses": int(len(supplied)),
        "active_components": int(nx.number_connected_components(graph)) if len(graph) else 0,
        "supplied_load_count": int(supplied_mask.sum()),
        "unsupplied_load_count": int((~supplied_mask).sum()),
        "supplied_p_mw": float(loads.loc[supplied_mask, "p_mw"].sum()),
        "unsupplied_p_mw": float(loads.loc[~supplied_mask, "p_mw"].sum()),
        "supplied_q_mvar": float(loads.loc[supplied_mask, "q_mvar"].sum()),
        "unsupplied_q_mvar": float(loads.loc[~supplied_mask, "q_mvar"].sum()),
    }


def scale_load(net: Any, factor: float) -> None:
    net.load.loc[:, "p_mw"] = pd.to_numeric(net.load["p_mw"], errors="coerce") * factor
    net.load.loc[:, "q_mvar"] = pd.to_numeric(net.load["q_mvar"], errors="coerce") * factor


def run_case(net: Any, experiment: str, case_id: str, load_scale: float, **metadata: Any) -> dict[str, Any]:
    scale_load(net, load_scale)
    context = supplied_context(net)
    active_total_p = context["supplied_p_mw"] + context["unsupplied_p_mw"]
    row: dict[str, Any] = {
        "experiment": experiment,
        "case_id": case_id,
        "load_scale": load_scale,
        **metadata,
        **context,
        "unsupplied_load_fraction": (
            float(context["unsupplied_p_mw"]) / float(active_total_p) if active_total_p else 0.0
        ),
        "converged": False,
        "iterations": math.nan,
        "min_vm_pu": math.nan,
        "max_vm_pu": math.nan,
        "max_line_loading_percent": math.nan,
        "max_trafo_loading_percent": math.nan,
        "solver_error": "",
        "warning_count": 0,
    }
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            pp.runpp(
                net,
                algorithm="nr",
                init="flat",
                calculate_voltage_angles=True,
                max_iteration=50,
                tolerance_mva=1e-6,
                enforce_q_lims=True,
                check_connectivity=True,
                numba=False,
            )
        row["warning_count"] = len(caught)
        row["converged"] = bool(net.converged)
        if net.converged:
            ppc = getattr(net, "_ppc", {}) or {}
            row["iterations"] = ppc.get("iterations", math.nan)
            row["min_vm_pu"] = float(net.res_bus["vm_pu"].dropna().min()) if len(net.res_bus) else math.nan
            row["max_vm_pu"] = float(net.res_bus["vm_pu"].dropna().max()) if len(net.res_bus) else math.nan
            row["max_line_loading_percent"] = float(net.res_line["loading_percent"].dropna().max()) if len(net.res_line) else math.nan
            row["max_trafo_loading_percent"] = float(net.res_trafo["loading_percent"].dropna().max()) if len(net.res_trafo) else math.nan
    except Exception as exc:  # pandapower exposes solver failures through several exception types
        row["solver_error"] = f"{type(exc).__name__}: {exc}"
    if row["converged"]:
        if row["unsupplied_load_fraction"] > 0.05:
            row["interpretation"] = "CONVERGED_AFTER_MATERIAL_LOAD_DISCONNECTION"
        elif row["unsupplied_load_fraction"] > 0:
            row["interpretation"] = "CONVERGED_WITH_SMALL_LOAD_DISCONNECTION"
        else:
            row["interpretation"] = "CONVERGED_WITH_LOAD_PRESERVED"
    else:
        row["interpretation"] = "NON_CONVERGED"
    return row


def graph_distances(graph: nx.Graph, source: int) -> dict[int, int]:
    return {int(node): int(distance) for node, distance in nx.single_source_shortest_path_length(graph, source).items()}


def element_depths(net: Any, distances: dict[int, int]) -> tuple[dict[int, int], dict[int, int]]:
    line_depth = {
        int(index): max(distances.get(int(row.from_bus), 10**6), distances.get(int(row.to_bus), 10**6))
        for index, row in net.line.iterrows()
    }
    trafo_depth = {
        int(index): max(distances.get(int(row.hv_bus), 10**6), distances.get(int(row.lv_bus), 10**6))
        for index, row in net.trafo.iterrows()
    }
    return line_depth, trafo_depth


def base_structure(net: Any) -> tuple[nx.Graph, dict[int, int], set[tuple[int, int]]]:
    graph = active_graph(net)
    slack = slack_bus(net)
    distances = graph_distances(graph, slack)
    bridges = {tuple(sorted((int(left), int(right)))) for left, right in nx.bridges(graph)}
    return graph, distances, bridges


def leave_one_line_out(base: Any, scales: list[float], distances: dict[int, int], bridges: set[tuple[int, int]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    active_indices = [int(index) for index, row in base.line.iterrows() if active(row.get("in_service", True))]
    for scale in scales:
        rows.append(run_case(copy.deepcopy(base), "line_ablation", "BASE", scale, element_index=-1, element_name="BASE"))
        for index in active_indices:
            net = copy.deepcopy(base)
            source = int(net.line.at[index, "from_bus"])
            target = int(net.line.at[index, "to_bus"])
            name = str(net.line.at[index, "name"])
            net.line.at[index, "in_service"] = False
            rows.append(
                run_case(
                    net,
                    "line_ablation",
                    f"disable_line_{index}",
                    scale,
                    element_index=index,
                    element_name=name,
                    from_bus=source,
                    to_bus=target,
                    is_graph_bridge=tuple(sorted((source, target))) in bridges,
                    slack_depth=max(distances.get(source, -1), distances.get(target, -1)),
                    length_km=float(pd.to_numeric(base.line.at[index, "length_km"], errors="coerce")),
                )
            )
    return pd.DataFrame(rows)


def leave_one_trafo_out(base: Any, scales: list[float], distances: dict[int, int], bridges: set[tuple[int, int]]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    active_indices = [int(index) for index, row in base.trafo.iterrows() if active(row.get("in_service", True))]
    for scale in scales:
        for index in active_indices:
            net = copy.deepcopy(base)
            source = int(net.trafo.at[index, "hv_bus"])
            target = int(net.trafo.at[index, "lv_bus"])
            name = str(net.trafo.at[index, "name"])
            net.trafo.at[index, "in_service"] = False
            rows.append(
                run_case(
                    net,
                    "trafo_ablation",
                    f"disable_trafo_{index}",
                    scale,
                    element_index=index,
                    element_name=name,
                    hv_bus=source,
                    lv_bus=target,
                    is_graph_bridge=tuple(sorted((source, target))) in bridges,
                    slack_depth=max(distances.get(source, -1), distances.get(target, -1)),
                    sn_mva=float(pd.to_numeric(base.trafo.at[index, "sn_mva"], errors="coerce")),
                )
            )
    return pd.DataFrame(rows)


def depth_frontier(base: Any, scales: list[float], distances: dict[int, int]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    active_bus_indices = {int(index) for index, row in base.bus.iterrows() if active(row.get("in_service", True))}
    line_depth, trafo_depth = element_depths(base, distances)
    max_depth = max(distances.values())
    for scale in scales:
        for depth in range(max_depth + 1):
            net = copy.deepcopy(base)
            retained = {bus for bus in active_bus_indices if distances.get(bus, 10**6) <= depth}
            net.bus.loc[:, "in_service"] = [int(index) in retained for index in net.bus.index]
            net.line.loc[:, "in_service"] = [
                active(base.line.at[index, "in_service"])
                and int(row.from_bus) in retained
                and int(row.to_bus) in retained
                for index, row in net.line.iterrows()
            ]
            net.trafo.loc[:, "in_service"] = [
                active(base.trafo.at[index, "in_service"])
                and int(row.hv_bus) in retained
                and int(row.lv_bus) in retained
                for index, row in net.trafo.iterrows()
            ]
            net.load.loc[:, "in_service"] = [
                active(base.load.at[index, "in_service"]) and int(row.bus) in retained
                for index, row in net.load.iterrows()
            ]
            net.ext_grid.loc[:, "in_service"] = [
                active(base.ext_grid.at[index, "in_service"]) and int(row.bus) in retained
                for index, row in net.ext_grid.iterrows()
            ]
            frontier_lines = [str(base.line.at[index, "name"]) for index, value in line_depth.items() if value == depth]
            frontier_trafos = [str(base.trafo.at[index, "name"]) for index, value in trafo_depth.items() if value == depth]
            rows.append(
                run_case(
                    net,
                    "depth_frontier",
                    f"depth_{depth}",
                    scale,
                    depth=depth,
                    retained_buses=len(retained),
                    active_lines=int(net.line["in_service"].map(active).sum()),
                    active_trafos=int(net.trafo["in_service"].map(active).sum()),
                    frontier_line_names="|".join(frontier_lines),
                    frontier_trafo_names="|".join(frontier_trafos),
                )
            )
    return pd.DataFrame(rows)


def configure_depth_net(base: Any, distances: dict[int, int], depth: int) -> Any:
    net = copy.deepcopy(base)
    active_bus_indices = {int(index) for index, row in base.bus.iterrows() if active(row.get("in_service", True))}
    retained = {bus for bus in active_bus_indices if distances.get(bus, 10**6) <= depth}
    net.bus.loc[:, "in_service"] = [int(index) in retained for index in net.bus.index]
    net.line.loc[:, "in_service"] = [
        active(base.line.at[index, "in_service"])
        and int(row.from_bus) in retained
        and int(row.to_bus) in retained
        for index, row in net.line.iterrows()
    ]
    net.trafo.loc[:, "in_service"] = [
        active(base.trafo.at[index, "in_service"])
        and int(row.hv_bus) in retained
        and int(row.lv_bus) in retained
        for index, row in net.trafo.iterrows()
    ]
    net.load.loc[:, "in_service"] = [
        active(base.load.at[index, "in_service"]) and int(row.bus) in retained
        for index, row in net.load.iterrows()
    ]
    net.ext_grid.loc[:, "in_service"] = [
        active(base.ext_grid.at[index, "in_service"]) and int(row.bus) in retained
        for index, row in net.ext_grid.iterrows()
    ]
    return net


def localize_failure_frontiers(
    base: Any,
    distances: dict[int, int],
    depth_results: pd.DataFrame,
) -> pd.DataFrame:
    line_depth, trafo_depth = element_depths(base, distances)
    rows: list[dict[str, Any]] = []
    for scale, group in depth_results.groupby("load_scale", sort=True):
        ordered = group.sort_values("depth")
        failed = ordered[~ordered["converged"].astype(bool)]
        if failed.empty:
            continue
        failure = failed.iloc[0]
        depth = int(failure["depth"])
        previous = ordered[ordered["depth"] == depth - 1]
        if previous.empty or not bool(previous.iloc[0]["converged"]):
            continue
        frontier_lines = sorted(index for index, value in line_depth.items() if value == depth)
        frontier_trafos = sorted(index for index, value in trafo_depth.items() if value == depth)

        current = configure_depth_net(base, distances, depth)
        for index in frontier_lines:
            net = copy.deepcopy(current)
            net.line.at[index, "in_service"] = False
            rows.append(
                run_case(
                    net,
                    "frontier_disable_one",
                    f"depth_{depth}_disable_line_{index}",
                    float(scale),
                    failure_depth=depth,
                    element_type="line",
                    element_index=index,
                    element_name=str(base.line.at[index, "name"]),
                    combination_size=1,
                )
            )
        for index in frontier_trafos:
            net = copy.deepcopy(current)
            net.trafo.at[index, "in_service"] = False
            rows.append(
                run_case(
                    net,
                    "frontier_disable_one",
                    f"depth_{depth}_disable_trafo_{index}",
                    float(scale),
                    failure_depth=depth,
                    element_type="trafo",
                    element_index=index,
                    element_name=str(base.trafo.at[index, "name"]),
                    combination_size=1,
                )
            )

        previous_net = configure_depth_net(base, distances, depth - 1)
        current_bus_mask = current.bus["in_service"].map(active)
        previous_net.bus.loc[:, "in_service"] = current_bus_mask
        previous_net.load.loc[:, "in_service"] = current.load["in_service"].map(active)
        for index in frontier_lines:
            net = copy.deepcopy(previous_net)
            net.line.at[index, "in_service"] = True
            rows.append(
                run_case(
                    net,
                    "frontier_add_one",
                    f"depth_{depth}_add_line_{index}",
                    float(scale),
                    failure_depth=depth,
                    element_type="line",
                    element_index=index,
                    element_name=str(base.line.at[index, "name"]),
                    combination_size=1,
                )
            )
        for index in frontier_trafos:
            net = copy.deepcopy(previous_net)
            net.trafo.at[index, "in_service"] = True
            rows.append(
                run_case(
                    net,
                    "frontier_add_one",
                    f"depth_{depth}_add_trafo_{index}",
                    float(scale),
                    failure_depth=depth,
                    element_type="trafo",
                    element_index=index,
                    element_name=str(base.trafo.at[index, "name"]),
                    combination_size=1,
                )
            )

        added_scaled_p = float(failure["supplied_p_mw"]) - float(previous.iloc[0]["supplied_p_mw"])
        single_disable = [row for row in rows if row["experiment"] == "frontier_disable_one" and row["failure_depth"] == depth and row["load_scale"] == float(scale)]
        any_single_fix = any(bool(row["converged"]) and float(row["unsupplied_load_fraction"]) <= 0.05 for row in single_disable)
        if not any_single_fix and abs(added_scaled_p) < 1e-9 and 2 <= len(frontier_lines) <= 20:
            for left, right in combinations(frontier_lines, 2):
                net = copy.deepcopy(current)
                net.line.at[left, "in_service"] = False
                net.line.at[right, "in_service"] = False
                rows.append(
                    run_case(
                        net,
                        "frontier_disable_pair",
                        f"depth_{depth}_disable_lines_{left}_{right}",
                        float(scale),
                        failure_depth=depth,
                        element_type="line_pair",
                        element_index=f"{left}|{right}",
                        element_name=f"{base.line.at[left, 'name']}|{base.line.at[right, 'name']}",
                        combination_size=2,
                    )
                )
    return pd.DataFrame(rows)


def rank_localization(line_results: pd.DataFrame, trafo_results: pd.DataFrame) -> pd.DataFrame:
    candidates = pd.concat(
        [
            line_results[line_results["element_index"] >= 0].assign(element_type="line"),
            trafo_results.assign(element_type="trafo"),
        ],
        ignore_index=True,
        sort=False,
    )
    candidates["preserves_95pct_load"] = candidates["unsupplied_load_fraction"] <= 0.05
    candidates["localization_signal"] = np.select(
        [
            candidates["converged"].astype(bool) & candidates["preserves_95pct_load"],
            candidates["converged"].astype(bool),
        ],
        ["CONVERGENCE_PRESERVING_LOAD", "CONVERGENCE_BY_LOAD_DISCONNECTION"],
        default="NO_SINGLE_ELEMENT_FIX",
    )
    candidates["diagnostic_score"] = (
        candidates["converged"].astype(int) * 100.0
        + candidates["preserves_95pct_load"].astype(int) * 25.0
        - pd.to_numeric(candidates["unsupplied_load_fraction"], errors="coerce").fillna(1.0) * 100.0
        - pd.to_numeric(candidates["load_scale"], errors="coerce").rsub(1.0).abs() * 5.0
    )
    return candidates.sort_values(
        ["diagnostic_score", "load_scale", "element_type", "element_name"],
        ascending=[False, False, True, True],
        kind="mergesort",
    )


def first_failure_by_scale(depth: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for scale, group in depth.groupby("load_scale", sort=True):
        ordered = group.sort_values("depth")
        failed = ordered[~ordered["converged"].astype(bool)]
        first = failed.iloc[0] if len(failed) else None
        rows.append(
            {
                "load_scale": float(scale),
                "first_nonconverged_depth": int(first["depth"]) if first is not None else None,
                "frontier_line_names": str(first["frontier_line_names"]) if first is not None else "",
                "frontier_trafo_names": str(first["frontier_trafo_names"]) if first is not None else "",
                "supplied_p_mw_at_failure": float(first["supplied_p_mw"]) if first is not None else None,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ablation-load-scales", default="0.1,1.0")
    parser.add_argument("--depth-load-scales", default="0.05,0.1,1.0")
    args = parser.parse_args()
    ablation_scales = [float(value) for value in args.ablation_load_scales.split(",")]
    depth_scales = [float(value) for value in args.depth_load_scales.split(",")]

    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: missing pandapower net: {NET_PATH}")
    ensure_directories()
    OUT.mkdir(parents=True, exist_ok=True)
    base = pp.from_json(NET_PATH)
    graph, distances, bridges = base_structure(base)
    lines = leave_one_line_out(base, ablation_scales, distances, bridges)
    trafos = leave_one_trafo_out(base, ablation_scales, distances, bridges)
    depths = depth_frontier(base, depth_scales, distances)
    frontier = localize_failure_frontiers(base, distances, depths)
    ranked = rank_localization(lines, trafos)

    lines.to_csv(OUT / "pt_acpf_leave_one_line_out.csv", index=False)
    trafos.to_csv(OUT / "pt_acpf_leave_one_transformer_out.csv", index=False)
    depths.to_csv(OUT / "pt_acpf_depth_frontier.csv", index=False)
    frontier.to_csv(OUT / "pt_acpf_failure_frontier_localization.csv", index=False)
    ranked.to_csv(OUT / "pt_acpf_localization_ranked.csv", index=False)

    preserved = ranked[ranked["localization_signal"] == "CONVERGENCE_PRESERVING_LOAD"]
    disconnected = ranked[ranked["localization_signal"] == "CONVERGENCE_BY_LOAD_DISCONNECTION"]
    depth_failures = first_failure_by_scale(depths)
    frontier_preserved = frontier[
        frontier["converged"].astype(bool) & (frontier["unsupplied_load_fraction"] <= 0.05)
    ].copy() if len(frontier) else frontier.copy()
    frontier_counts = (
        frontier.groupby(["load_scale", "failure_depth", "experiment"])["converged"]
        .agg(attempts="count", converged="sum")
        .reset_index()
        .to_dict("records")
        if len(frontier)
        else []
    )
    low_scale_disable_sets = []
    for scale in (0.05, 0.1):
        values = set(
            frontier[
                (frontier["load_scale"] == scale)
                & (frontier["experiment"] == "frontier_disable_one")
                & frontier["converged"].astype(bool)
                & (frontier["unsupplied_load_fraction"] <= 0.05)
            ]["element_name"].astype(str)
        )
        low_scale_disable_sets.append(values)
    stable_low_scale_relief_lines = sorted(set.intersection(*low_scale_disable_sets)) if all(low_scale_disable_sets) else []
    summary = {
        "generated_at": utc_now(),
        "status": "DIAGNOSTIC_ONLY",
        "active_buses": int(len(graph)),
        "active_lines": int(base.line["in_service"].map(active).sum()),
        "active_trafos": int(base.trafo["in_service"].map(active).sum()),
        "maximum_slack_depth": int(max(distances.values())),
        "line_ablation_runs": int(len(lines)),
        "transformer_ablation_runs": int(len(trafos)),
        "depth_frontier_runs": int(len(depths)),
        "failure_frontier_localization_runs": int(len(frontier)),
        "converged_load_preserving_single_element_ablations": int(len(preserved)),
        "converged_by_load_disconnection_ablations": int(len(disconnected)),
        "first_depth_failures": depth_failures,
        "failure_frontier_run_counts": frontier_counts,
        "stable_low_scale_relief_lines": stable_low_scale_relief_lines,
        "load_preserving_failure_frontier_fixes": frontier_preserved[
            ["experiment", "load_scale", "failure_depth", "element_type", "element_name", "unsupplied_load_fraction"]
        ].to_dict("records") if len(frontier_preserved) else [],
        "top_load_preserving_candidates": preserved.head(10)[
            ["element_type", "element_name", "load_scale", "unsupplied_load_fraction", "slack_depth"]
        ].to_dict("records"),
        "claim_boundary": "Ablation convergence localizes numerical/topological sensitivity but does not validate Portuguese electrical parameters or topology.",
    }
    write_json(OUT / "pt_acpf_localization_summary.json", summary)

    text = [
        "# 99 ACPF Non-Convergence Localization",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Status: `DIAGNOSTIC_ONLY`",
        "",
        "## Scope",
        "",
        "The full benchmark-plumbing net is tested with leave-one-line-out, leave-one-transformer-out, and slack-depth frontier ablations. Every run records unsupplied load before solving. Convergence caused by disconnecting more than 5% of active load is not counted as a load-preserving localization signal.",
        "",
        "## Counts",
        "",
        f"- active graph: {summary['active_buses']} buses, {summary['active_lines']} lines, {summary['active_trafos']} transformers",
        f"- line ablation runs: {summary['line_ablation_runs']}",
        f"- transformer ablation runs: {summary['transformer_ablation_runs']}",
        f"- depth-frontier runs: {summary['depth_frontier_runs']}",
        f"- failure-frontier localization runs: {summary['failure_frontier_localization_runs']}",
        f"- converged single-element ablations preserving at least 95% of load: {summary['converged_load_preserving_single_element_ablations']}",
        f"- converged ablations explained by material load disconnection: {summary['converged_by_load_disconnection_ablations']}",
        "",
        "## First failing depth",
        "",
        "```json",
        json.dumps(depth_failures, indent=2),
        "```",
        "",
        "## Failure-frontier localization",
        "",
        "```json",
        json.dumps(frontier_counts, indent=2),
        "```",
        "",
        "Lines whose removal restored both 5% and 10% depth-15 cases without disconnecting load:",
        "",
        "`" + "|".join(stable_low_scale_relief_lines) + "`",
        "",
        "## Interpretation boundary",
        "",
        summary["claim_boundary"],
    ]
    write_text(REPORT, "\n".join(text) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
