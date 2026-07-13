"""Controlled AC PF ablation diagnostics for the Portuguese plumbing net.

These runs are diagnostics only. Converged cases are not Portuguese PF results
and must not be interpreted as validation.
"""

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
DIAG = ROOT / "data" / "processed" / "acpf_diagnostics"
NET_PATH = READY / "pt_acpf_pandapower_net.json"


def run_case(net, test_group: str, test_name: str, **runpp_kwargs: Any) -> dict[str, Any]:
    settings = {
        "algorithm": runpp_kwargs.pop("algorithm", "nr"),
        "init": runpp_kwargs.pop("init", "flat"),
        "calculate_voltage_angles": runpp_kwargs.pop("calculate_voltage_angles", True),
        "enforce_q_lims": runpp_kwargs.pop("enforce_q_lims", False),
        "numba": False,
        "max_iteration": runpp_kwargs.pop("max_iteration", 50),
        "tolerance_mva": runpp_kwargs.pop("tolerance_mva", 1e-6),
    }
    settings.update(runpp_kwargs)
    active_load = net.load.loc[net.load.in_service].copy() if len(net.load) else pd.DataFrame()
    if len(active_load):
        scaling = pd.to_numeric(active_load.get("scaling", 1.0), errors="coerce").fillna(1.0)
        effective_p_mw = float((pd.to_numeric(active_load["p_mw"], errors="coerce").fillna(0.0) * scaling).sum())
        effective_q_mvar = float((pd.to_numeric(active_load["q_mvar"], errors="coerce").fillna(0.0) * scaling).sum())
    else:
        effective_p_mw = 0.0
        effective_q_mvar = 0.0

    row = {
        "test_group": test_group,
        "test_name": test_name,
        "diagnostic_only": True,
        "algorithm": settings.get("algorithm"),
        "init": settings.get("init"),
        "converged": False,
        "iterations": "",
        "min_vm_pu": "",
        "max_vm_pu": "",
        "max_line_loading_percent": "",
        "max_trafo_loading_percent": "",
        "total_line_losses_mw": "",
        "total_trafo_losses_mw": "",
        "total_load_p_mw": effective_p_mw,
        "total_load_q_mvar": effective_q_mvar,
        "error_type": "",
        "error": "",
    }
    try:
        pp.runpp(net, **settings)
        row["converged"] = bool(net.converged)
        if net.converged:
            row.update(
                {
                    "iterations": getattr(net, "_ppc", {}).get("iterations", ""),
                    "min_vm_pu": float(net.res_bus["vm_pu"].min()),
                    "max_vm_pu": float(net.res_bus["vm_pu"].max()),
                    "max_line_loading_percent": float(net.res_line["loading_percent"].max()) if len(net.res_line) else 0.0,
                    "max_trafo_loading_percent": float(net.res_trafo["loading_percent"].max()) if len(net.res_trafo) else 0.0,
                    "total_line_losses_mw": float(net.res_line["pl_mw"].sum()) if len(net.res_line) else 0.0,
                    "total_trafo_losses_mw": float(net.res_trafo["pl_mw"].sum()) if len(net.res_trafo) else 0.0,
                }
            )
    except Exception as exc:
        row["error_type"] = type(exc).__name__
        row["error"] = str(exc)
    return row


def clone_base():
    return pp.from_json(NET_PATH)


def scale_load(net, factor: float) -> None:
    net.load["scaling"] = factor


def set_power_factor(net, pf: float | None) -> None:
    if pf is None:
        net.load["q_mvar"] = 0.0
        return
    q = net.load["p_mw"] * math.tan(math.acos(pf))
    net.load["q_mvar"] = q


def set_transformer_xr(net, xr: float) -> None:
    vk = pd.to_numeric(net.trafo["vk_percent"], errors="coerce")
    net.trafo["vkr_percent"] = vk / math.sqrt(1.0 + xr**2)


def multiply_trafo_capacity(net, factor: float) -> None:
    net.trafo["sn_mva"] = net.trafo["sn_mva"] * factor


def set_slack_to_bus(net, bus_idx: int) -> None:
    net.ext_grid.at[net.ext_grid.index[0], "bus"] = bus_idx


def active_graph(net, include_trafo: bool = True) -> dict[int, set[int]]:
    graph: dict[int, set[int]] = defaultdict(set)
    for _, row in net.line[net.line.in_service].iterrows():
        a, b = int(row.from_bus), int(row.to_bus)
        graph[a].add(b)
        graph[b].add(a)
    if include_trafo:
        for _, row in net.trafo[net.trafo.in_service].iterrows():
            a, b = int(row.hv_bus), int(row.lv_bus)
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


def shortest_path_edges(graph: dict[int, set[int]], source: int, target: int) -> set[tuple[int, int]]:
    parent = {source: None}
    q: deque[int] = deque([source])
    while q and target not in parent:
        cur = q.popleft()
        for nxt in graph.get(cur, set()):
            if nxt not in parent:
                parent[nxt] = cur
                q.append(nxt)
    edges: set[tuple[int, int]] = set()
    if target not in parent:
        return edges
    cur = target
    while parent[cur] is not None:
        a, b = cur, parent[cur]
        edges.add(tuple(sorted((a, b))))
        cur = parent[cur]
    return edges


def restrict_to_buses(net, buses: set[int]) -> None:
    net.bus["in_service"] = net.bus.index.isin(buses)
    net.line["in_service"] = net.line.apply(lambda r: int(r.from_bus) in buses and int(r.to_bus) in buses, axis=1)
    net.trafo["in_service"] = net.trafo.apply(lambda r: int(r.hv_bus) in buses and int(r.lv_bus) in buses, axis=1)
    net.load["in_service"] = net.load["bus"].isin(buses)
    net.ext_grid["in_service"] = net.ext_grid["bus"].isin(buses)


def minimal_model_cases() -> list[dict[str, Any]]:
    base = clone_base()
    slack = int(base.ext_grid.iloc[0].bus)
    graph = active_graph(base, include_trafo=True)
    dist = distances(graph, slack)
    load_buses = sorted(set(int(x) for x in base.load.loc[base.load.in_service, "bus"] if int(x) in dist), key=lambda b: dist[b])
    cases = []

    if load_buses:
        target = load_buses[0]
        path_edges = shortest_path_edges(graph, slack, target)
        buses = {slack, target}
        for a, b in path_edges:
            buses.add(a)
            buses.add(b)
        net = clone_base()
        restrict_to_buses(net, buses)
        net.load["in_service"] = net.load.index == net.load[net.load.bus == target].index[0]
        cases.append({"name": "M1_slack_nearest_load_path", "net": net})

    top5 = set(load_buses[:5])
    if top5:
        buses = {slack}
        for target in top5:
            buses.add(target)
            for a, b in shortest_path_edges(graph, slack, target):
                buses.add(a)
                buses.add(b)
        net = clone_base()
        restrict_to_buses(net, buses)
        net.load["in_service"] = net.load["bus"].isin(top5)
        cases.append({"name": "M2_slack_5_nearest_load_paths", "net": net})

    if dist:
        farthest = max((b for b in dist if b in set(base.bus.index)), key=lambda b: dist[b])
        buses = {slack, farthest}
        for a, b in shortest_path_edges(graph, slack, farthest):
            buses.add(a)
            buses.add(b)
        net = clone_base()
        restrict_to_buses(net, buses)
        cases.append({"name": "M3_largest_radial_depth_path", "net": net})

    net = clone_base()
    net.trafo["in_service"] = False
    net.load["in_service"] = False
    cases.append({"name": "M4_active_component_without_transformers_or_loads", "net": net})

    net = clone_base()
    net.load["in_service"] = False
    cases.append({"name": "M5_active_component_without_loads", "net": net})
    return cases


def slack_candidates() -> list[tuple[str, int]]:
    ext = pd.read_csv(READY / "pt_ext_grid_table_acpf.csv")
    bus_name_to_idx = {str(row["name"]): int(idx) for idx, row in clone_base().bus.iterrows()}
    candidates = ext[ext["in_energized_component"]].copy()
    candidates["s_sc_max_mva_numeric"] = pd.to_numeric(candidates["s_sc_max_mva"], errors="coerce")
    selected: list[tuple[str, int]] = []
    for label, df in [
        ("highest_short_circuit", candidates.sort_values("s_sc_max_mva_numeric", ascending=False)),
        ("lowest_short_circuit", candidates.sort_values("s_sc_max_mva_numeric", ascending=True)),
    ]:
        if len(df):
            bus_id = str(df.iloc[0]["bus_id"])
            if bus_id in bus_name_to_idx:
                selected.append((label + "_" + bus_id, bus_name_to_idx[bus_id]))
    # Add current selected slack and first few alternatives.
    for _, row in candidates.sort_values("facility_code").head(5).iterrows():
        bus_id = str(row["bus_id"])
        if bus_id in bus_name_to_idx:
            selected.append(("candidate_" + bus_id, bus_name_to_idx[bus_id]))
    dedup: list[tuple[str, int]] = []
    seen: set[int] = set()
    for name, idx in selected:
        if idx not in seen:
            dedup.append((name, idx))
            seen.add(idx)
    return dedup


def main() -> None:
    DIAG.mkdir(parents=True, exist_ok=True)

    load_rows = []
    for factor in [0.05, 0.10, 0.20, 0.30, 0.50, 0.75, 1.00]:
        net = clone_base()
        scale_load(net, factor)
        load_rows.append(run_case(net, "load_scaling", f"p_scale_{factor:.2f}"))

    for pf in [1.00, 0.98, 0.95, 0.90, None]:
        for scale in [1.0, 0.5]:
            net = clone_base()
            scale_load(net, scale)
            set_power_factor(net, pf)
            name = "q_zero" if pf is None else f"pf_{pf:.2f}"
            load_rows.append(run_case(net, "q_sensitivity", f"{name}_p_scale_{scale:.2f}"))
    load_df = pd.DataFrame(load_rows)
    load_df.to_csv(DIAG / "pt_acpf_load_sensitivity_results.csv", index=False)

    trafo_rows = []
    net = clone_base()
    trafo_rows.append(run_case(net, "trafo", "existing_transformer_settings"))
    for xr in [10, 20, 40]:
        net = clone_base()
        set_transformer_xr(net, xr)
        trafo_rows.append(run_case(net, "trafo_xr", f"x_over_r_{xr}"))
    for mult in [1.0, 1.5, 2.0]:
        net = clone_base()
        multiply_trafo_capacity(net, mult)
        trafo_rows.append(run_case(net, "trafo_capacity", f"capacity_multiplier_{mult:.1f}"))
    net = clone_base()
    net.trafo["tap_pos"] = 0
    trafo_rows.append(run_case(net, "trafo_tap", "all_taps_neutral"))
    net = clone_base()
    for col in ["tap_side", "tap_neutral", "tap_min", "tap_max", "tap_step_percent", "tap_step_degree", "tap_pos"]:
        if col in net.trafo.columns:
            net.trafo[col] = None
    if "oltc" in net.trafo.columns:
        net.trafo["oltc"] = False
    trafo_rows.append(run_case(net, "trafo_tap", "tap_fields_disabled"))
    pd.DataFrame(trafo_rows).to_csv(DIAG / "pt_acpf_transformer_ablation_results.csv", index=False)

    slack_rows = []
    for name, bus_idx in slack_candidates():
        net = clone_base()
        set_slack_to_bus(net, bus_idx)
        slack_rows.append(run_case(net, "slack", name))
    slack_df = pd.DataFrame(slack_rows)
    slack_df.to_csv(DIAG / "pt_acpf_slack_sensitivity_results.csv", index=False)

    algo_rows = []
    low_load_converged = load_df[load_df["converged"] == True]
    for algo in ["nr", "iwamoto_nr", "bfsw", "gs"]:
        for init in ["flat", "dc"]:
            net = clone_base()
            algo_rows.append(run_case(net, "algorithm", f"{algo}_{init}", algorithm=algo, init=init, max_iteration=100))
    if len(low_load_converged):
        net = clone_base()
        scale_load(net, float(low_load_converged.iloc[0]["test_name"].split("_")[-1]))
        first = run_case(net, "algorithm_seed", "low_load_seed", algorithm="nr", init="flat")
        algo_rows.append(first)
    algo_df = pd.DataFrame(algo_rows)
    algo_df.to_csv(DIAG / "pt_acpf_algorithm_sensitivity_results.csv", index=False)

    minimal_rows = []
    for case in minimal_model_cases():
        minimal_rows.append(run_case(case["net"], "minimal_model", case["name"], algorithm="nr", init="flat", max_iteration=100))
    minimal_df = pd.DataFrame(minimal_rows)

    ablation = pd.concat(
        [
            load_df,
            pd.DataFrame(trafo_rows),
            slack_df,
            algo_df,
            minimal_df,
        ],
        ignore_index=True,
    )
    ablation.to_csv(DIAG / "pt_acpf_ablation_summary.csv", index=False)

    hypotheses = build_hypotheses(load_df, pd.DataFrame(trafo_rows), slack_df, algo_df, minimal_df)
    (DIAG / "pt_acpf_failure_hypotheses.json").write_text(json.dumps(hypotheses, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"rows": len(ablation), "any_converged": bool(ablation["converged"].any()), "converged_count": int(ablation["converged"].sum())}, indent=2))


def build_hypotheses(load_df: pd.DataFrame, trafo_df: pd.DataFrame, slack_df: pd.DataFrame, algo_df: pd.DataFrame, minimal_df: pd.DataFrame) -> list[dict[str, Any]]:
    any_low_load = bool(load_df[(load_df["test_group"] == "load_scaling") & (load_df["converged"] == True)].any().any())
    min_converged_scale = None
    conv_load = load_df[(load_df["test_group"] == "load_scaling") & (load_df["converged"] == True)]
    if len(conv_load):
        min_converged_scale = conv_load["test_name"].iloc[0]
    q_conv = int(load_df[(load_df["test_group"] == "q_sensitivity") & (load_df["converged"] == True)].shape[0])
    trafo_conv = int(trafo_df[trafo_df["converged"] == True].shape[0])
    slack_conv = int(slack_df[slack_df["converged"] == True].shape[0])
    algo_conv = int(algo_df[algo_df["converged"] == True].shape[0])
    minimal_failed = minimal_df[minimal_df["converged"] != True]["test_name"].astype(str).tolist()

    return [
        {
            "hypothesis": "H1 benchmark-only line impedance unsuitable",
            "rank": 2,
            "evidence_for": "Base PF failed; line R/X/C are not Portugal-specific and include mixed proxies.",
            "evidence_against": "If minimal no-load/network tests converge, pure admittance is not the only issue.",
            "diagnostic_tests_supporting": ["impedance_sanity", "load_scaling", "minimal_models"],
            "recommended_fix": "Source Portuguese 60 kV overhead/cable R/X/B; split mixed lines by segment length.",
            "eredes_ren_data_needed": True,
        },
        {
            "hypothesis": "H2 transformer equivalent model unsuitable",
            "rank": 1 if trafo_conv == 0 else 3,
            "evidence_for": f"Transformer ablations converged count={trafo_conv}; vkr uses X/R scenario and unit count is unknown.",
            "evidence_against": "If low-load or no-load cases converge, transformers may be secondary to loading/topology.",
            "diagnostic_tests_supporting": ["transformer_ablation", "transformer_sanity"],
            "recommended_fix": "Request unit counts, actual ratings, load losses/vkr, tap policy.",
            "eredes_ren_data_needed": True,
        },
        {
            "hypothesis": "H3 load/Q scenario too heavy",
            "rank": 1 if any_low_load else 4,
            "evidence_for": f"Load-scaling convergence first occurred at {min_converged_scale}; Q converged cases={q_conv}.",
            "evidence_against": "If no 5% load case converges, load magnitude is not sufficient explanation.",
            "diagnostic_tests_supporting": ["load_scaling", "q_sensitivity"],
            "recommended_fix": "Validate hourly P, measured Q/power factor, and component-level load allocation.",
            "eredes_ren_data_needed": True,
        },
        {
            "hypothesis": "H4 slack selected at weak or unrealistic location",
            "rank": 2 if slack_conv > 0 else 5,
            "evidence_for": f"Slack sensitivity converged cases={slack_conv}; current slack is scenario-selected, not confirmed RNT/RND interface.",
            "evidence_against": "If no alternative slack converges, slack is not the only cause.",
            "diagnostic_tests_supporting": ["slack_sensitivity"],
            "recommended_fix": "Confirm REN/RNT interface buses and boundary voltage assumptions.",
            "eredes_ren_data_needed": True,
        },
        {
            "hypothesis": "H5 topology connectivity still physically wrong",
            "rank": 2,
            "evidence_for": "Only one component is energized and topology was reconstructed from fragmented open geometries.",
            "evidence_against": "Readiness gates confirm graph connectivity for active loads, but not physical correctness.",
            "diagnostic_tests_supporting": ["component_diagnostics", "minimal_models"],
            "recommended_fix": "Validate branch terminals, switching stations, and transformer-node mapping with operator topology.",
            "eredes_ren_data_needed": True,
        },
        {
            "hypothesis": "H6 mixed line proxy creates bad branches",
            "rank": 3,
            "evidence_for": "Active model contains mixed lines using a 50/50 overhead/cable proxy, including one of the longest active branches.",
            "evidence_against": "Static impedance checks found no near-zero, large-impedance, or unusual R/X flags.",
            "diagnostic_tests_supporting": ["impedance_sanity", "minimal_models"],
            "recommended_fix": "Recover segment-level overhead/cable lengths or split mixed circuits before assigning parameters.",
            "eredes_ren_data_needed": True,
        },
        {
            "hypothesis": "H7 voltage-level / transformer-bus mapping errors",
            "rank": 3,
            "evidence_for": "PF failure begins when loaded transformer paths are included; transformer buses are constructed from open-data voltage labels.",
            "evidence_against": "Static transformer checks found no hv<=lv voltage-ratio mismatch.",
            "diagnostic_tests_supporting": ["transformer_sanity", "minimal_models"],
            "recommended_fix": "Validate AT/MT bus mapping, transformer terminal assignment, and voltage levels against operator records.",
            "eredes_ren_data_needed": True,
        },
        {
            "hypothesis": "H8 isolated or weakly connected load pockets",
            "rank": 2,
            "evidence_for": f"All loads are graph-connected, but deep load paths exist and minimal failed cases={minimal_failed}.",
            "evidence_against": "There are no orphan active loads and no disconnected energized load islands in the active graph.",
            "diagnostic_tests_supporting": ["component_diagnostics", "power_balance", "minimal_models"],
            "recommended_fix": "Check whether deep radial paths are missing parallel feeds, switching-state links, or normally closed ties.",
            "eredes_ren_data_needed": True,
        },
        {
            "hypothesis": "H9 numerical algorithm issue only",
            "rank": 9 if algo_conv == 0 else 6,
            "evidence_for": f"Algorithm sensitivity converged cases={algo_conv}.",
            "evidence_against": "Data and assumptions are weak; algorithm-only explanation is unlikely.",
            "diagnostic_tests_supporting": ["algorithm_sensitivity"],
            "recommended_fix": "Do not rely on algorithm changes as validation; fix data/model first.",
            "eredes_ren_data_needed": False,
        },
    ]


if __name__ == "__main__":
    main()
