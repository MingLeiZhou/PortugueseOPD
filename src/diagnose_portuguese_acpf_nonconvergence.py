"""Static diagnostics for the non-converged Portuguese ACPF plumbing model."""

from __future__ import annotations

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
DIAG = ROOT / "data" / "processed" / "acpf_diagnostics"


def read(name: str) -> pd.DataFrame:
    return pd.read_csv(READY / name)


def write(name: str, df: pd.DataFrame) -> None:
    DIAG.mkdir(parents=True, exist_ok=True)
    df.to_csv(DIAG / name, index=False)


def numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def build_graph(lines: pd.DataFrame, trafos: pd.DataFrame | None = None) -> dict[str, set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for _, row in lines[lines["in_service"]].iterrows():
        a, b = str(row["from_bus"]), str(row["to_bus"])
        graph[a].add(b)
        graph[b].add(a)
    if trafos is not None:
        for _, row in trafos[trafos["in_service"]].iterrows():
            a, b = str(row["hv_bus"]), str(row["lv_bus"])
            graph[a].add(b)
            graph[b].add(a)
    return graph


def distances(graph: dict[str, set[str]], source: str) -> dict[str, int]:
    dist = {source: 0}
    q: deque[str] = deque([source])
    while q:
        node = q.popleft()
        for nxt in graph.get(node, set()):
            if nxt not in dist:
                dist[nxt] = dist[node] + 1
                q.append(nxt)
    return dist


def connected_components(graph: dict[str, set[str]]) -> list[set[str]]:
    seen: set[str] = set()
    comps: list[set[str]] = []
    for node in graph:
        if node in seen:
            continue
        comp = set()
        q = deque([node])
        seen.add(node)
        while q:
            cur = q.popleft()
            comp.add(cur)
            for nxt in graph.get(cur, set()):
                if nxt not in seen:
                    seen.add(nxt)
                    q.append(nxt)
        comps.append(comp)
    return sorted(comps, key=len, reverse=True)


def component_diagnostics(bus: pd.DataFrame, line: pd.DataFrame, trafo: pd.DataFrame, load: pd.DataFrame, ext: pd.DataFrame) -> pd.DataFrame:
    active_bus = bus[bus["in_service"]].copy()
    active_line = line[line["in_service"]].copy()
    active_trafo = trafo[trafo["in_service"]].copy()
    active_load = load[load["in_service"]].copy()
    active_ext = ext[ext["in_service"]].copy()
    graph = build_graph(active_line, active_trafo)
    comps = connected_components(graph)
    slack = str(active_ext.iloc[0]["bus_id"]) if len(active_ext) else ""
    dist = distances(graph, slack) if slack else {}
    active_load["distance_to_slack_edges"] = active_load["bus_id"].map(dist)
    bus_load = active_load.groupby("bus_id")["p_mw"].sum().sort_values(ascending=False)
    bus_degree = {node: len(nei) for node, nei in graph.items()}

    rows: list[dict[str, Any]] = [
        {"metric": "active_buses", "value": len(active_bus), "details": ""},
        {"metric": "active_lines", "value": len(active_line), "details": ""},
        {"metric": "active_transformers", "value": len(active_trafo), "details": ""},
        {"metric": "active_loads", "value": len(active_load), "details": ""},
        {"metric": "active_ext_grids", "value": len(active_ext), "details": ""},
        {"metric": "connected_components_with_trafo_edges", "value": len(comps), "details": [len(c) for c in comps[:10]]},
        {"metric": "all_loads_connected_to_slack", "value": bool(active_load["bus_id"].isin(dist).all()), "details": f"unreachable_loads={int((~active_load['bus_id'].isin(dist)).sum())}"},
        {"metric": "total_p_load_mw", "value": float(active_load["p_mw"].sum()), "details": ""},
        {"metric": "total_q_load_mvar", "value": float(active_load["q_mvar"].sum()), "details": ""},
        {"metric": "total_transformer_capacity_mva", "value": float(active_trafo["sn_mva"].sum()), "details": ""},
        {"metric": "total_line_length_km", "value": float(active_line["length_km"].sum()), "details": ""},
        {"metric": "longest_line_km", "value": float(active_line["length_km"].max()), "details": active_line.sort_values("length_km", ascending=False).head(5)[["line_id", "length_km", "asset_type"]].to_dict("records")},
        {"metric": "max_bus_load_mw", "value": float(bus_load.iloc[0]) if len(bus_load) else 0.0, "details": bus_load.head(10).to_dict()},
        {"metric": "max_graph_degree", "value": max(bus_degree.values()) if bus_degree else 0, "details": dict(sorted(bus_degree.items(), key=lambda x: x[1], reverse=True)[:10])},
        {"metric": "max_load_distance_to_slack_edges", "value": float(active_load["distance_to_slack_edges"].max()), "details": active_load.sort_values("distance_to_slack_edges", ascending=False).head(10)[["load_id", "bus_id", "p_mw", "distance_to_slack_edges"]].to_dict("records")},
    ]
    return pd.DataFrame(rows)


def power_balance_checks(line: pd.DataFrame, trafo: pd.DataFrame, load: pd.DataFrame, ext: pd.DataFrame) -> pd.DataFrame:
    active_line = line[line["in_service"]].copy()
    active_trafo = trafo[trafo["in_service"]].copy()
    active_load = load[load["in_service"]].copy()
    graph = build_graph(active_line, active_trafo)
    slack = str(ext[ext["in_service"]].iloc[0]["bus_id"])
    dist = distances(graph, slack)
    active_load["distance_to_slack_edges"] = active_load["bus_id"].map(dist)

    total_p = float(active_load["p_mw"].sum())
    total_q = float(active_load["q_mvar"].sum())
    total_s_load = float((active_load["p_mw"] ** 2 + active_load["q_mvar"] ** 2).pow(0.5).sum())
    total_trafo = float(active_trafo["sn_mva"].sum())
    total_line_thermal = float(active_line["thermal_limit_mva"].sum())

    trafo_load_proxy = active_load.groupby("facility_code").agg(load_p_mw=("p_mw", "sum"), load_q_mvar=("q_mvar", "sum")).reset_index()
    trafo_proxy = active_trafo[["facility_code", "sn_mva", "trafo_id"]].merge(trafo_load_proxy, on="facility_code", how="left").fillna({"load_p_mw": 0.0, "load_q_mvar": 0.0})
    trafo_proxy["load_s_mva"] = (trafo_proxy["load_p_mw"] ** 2 + trafo_proxy["load_q_mvar"] ** 2) ** 0.5
    trafo_proxy["load_to_sn_ratio"] = trafo_proxy["load_s_mva"] / trafo_proxy["sn_mva"]

    rows = [
        {"metric": "total_p_load_mw", "value": total_p, "flag": "", "details": ""},
        {"metric": "total_q_load_mvar", "value": total_q, "flag": "", "details": ""},
        {"metric": "total_s_load_mva_proxy", "value": total_s_load, "flag": "", "details": ""},
        {"metric": "total_transformer_mva", "value": total_trafo, "flag": "", "details": ""},
        {"metric": "load_to_transformer_capacity_ratio", "value": total_s_load / total_trafo if total_trafo else math.nan, "flag": "HIGH" if total_trafo and total_s_load / total_trafo > 0.8 else "", "details": ""},
        {"metric": "total_line_thermal_mva", "value": total_line_thermal, "flag": "", "details": "thermal limits are benchmark/scenario values"},
        {"metric": "transformers_over_80pct_proxy", "value": int((trafo_proxy["load_to_sn_ratio"] > 0.8).sum()), "flag": "CHECK", "details": trafo_proxy.sort_values("load_to_sn_ratio", ascending=False).head(10).to_dict("records")},
        {"metric": "loads_beyond_20_edges_from_slack", "value": int((active_load["distance_to_slack_edges"] > 20).sum()), "flag": "CHECK", "details": active_load.sort_values("distance_to_slack_edges", ascending=False).head(10)[["load_id", "bus_id", "p_mw", "distance_to_slack_edges"]].to_dict("records")},
        {"metric": "largest_loads", "value": "", "flag": "CHECK", "details": active_load.sort_values("p_mw", ascending=False).head(10)[["load_id", "bus_id", "p_mw", "q_mvar", "distance_to_slack_edges"]].to_dict("records")},
    ]
    return pd.DataFrame(rows)


def impedance_checks(line: pd.DataFrame) -> pd.DataFrame:
    active = line[line["in_service"]].copy()
    active["total_r_ohm"] = numeric(active["r_ohm_per_km"]) * numeric(active["length_km"])
    active["total_x_ohm"] = numeric(active["x_ohm_per_km"]) * numeric(active["length_km"])
    active["z_base_ohm"] = numeric(active["voltage_kv"]) ** 2 / 100.0
    active["r_pu"] = active["total_r_ohm"] / active["z_base_ohm"]
    active["x_pu"] = active["total_x_ohm"] / active["z_base_ohm"]
    active["r_x_ratio"] = numeric(active["r_ohm_per_km"]) / numeric(active["x_ohm_per_km"])
    active["flag_near_zero_impedance"] = (active["r_pu"].abs() + active["x_pu"].abs()) < 1e-5
    active["flag_large_impedance"] = (active["r_pu"].abs() + active["x_pu"].abs()) > 1.0
    active["flag_unusual_r_x_ratio"] = (active["r_x_ratio"] < 0.05) | (active["r_x_ratio"] > 2.0)
    active["flag_long_benchmark_line"] = (active["length_km"] > 20) & active["parameter_source_status"].astype(str).str.contains("BENCHMARK|SCENARIO", regex=True)
    active["flag_mixed_proxy"] = active["asset_type"].eq("mixed")
    active["flag_extreme_capacitance"] = (numeric(active["c_nf_per_km"]) <= 0) | (numeric(active["c_nf_per_km"]) > 1000)
    cols = [
        "line_id",
        "from_bus",
        "to_bus",
        "length_km",
        "asset_type",
        "r_ohm_per_km",
        "x_ohm_per_km",
        "c_nf_per_km",
        "total_r_ohm",
        "total_x_ohm",
        "r_pu",
        "x_pu",
        "r_x_ratio",
        "parameter_source_status",
        "scenario_id",
        "flag_near_zero_impedance",
        "flag_large_impedance",
        "flag_unusual_r_x_ratio",
        "flag_long_benchmark_line",
        "flag_mixed_proxy",
        "flag_extreme_capacitance",
    ]
    return active[cols].sort_values(["flag_large_impedance", "length_km"], ascending=[False, False])


def transformer_checks(trafo: pd.DataFrame, load: pd.DataFrame) -> pd.DataFrame:
    active = trafo[trafo["in_service"]].copy()
    load_by_fac = load[load["in_service"]].groupby("facility_code").agg(load_p_mw=("p_mw", "sum"), load_q_mvar=("q_mvar", "sum")).reset_index()
    active = active.merge(load_by_fac, on="facility_code", how="left").fillna({"load_p_mw": 0.0, "load_q_mvar": 0.0})
    active["derived_x_percent"] = (numeric(active["vk_percent"]) ** 2 - numeric(active["vkr_percent"]) ** 2).clip(lower=0).pow(0.5)
    active["x_r_ratio"] = active["derived_x_percent"] / numeric(active["vkr_percent"])
    active["load_s_mva_proxy"] = (active["load_p_mw"] ** 2 + active["load_q_mvar"] ** 2) ** 0.5
    active["load_to_sn_ratio_proxy"] = active["load_s_mva_proxy"] / numeric(active["sn_mva"])
    active["flag_vkr_too_small"] = numeric(active["vkr_percent"]) < 0.05
    active["flag_vkr_too_large"] = numeric(active["vkr_percent"]) > 5
    active["flag_invalid_x_percent"] = active["derived_x_percent"].isna() | (active["derived_x_percent"] <= 0)
    active["flag_high_load_to_sn"] = active["load_to_sn_ratio_proxy"] > 0.8
    active["flag_voltage_ratio_mismatch"] = (numeric(active["hv_kv"]) <= numeric(active["lv_kv"]))
    active["flag_neutral_tap_assumption"] = numeric(active["tap_pos"]).fillna(0).eq(0)
    cols = [
        "trafo_id",
        "hv_bus",
        "lv_bus",
        "facility_code",
        "sn_mva",
        "vk_percent",
        "vkr_percent",
        "derived_x_percent",
        "x_r_ratio",
        "tap_pos",
        "tap_side",
        "matched_lut_rated_mva",
        "uk_value_status",
        "load_s_mva_proxy",
        "load_to_sn_ratio_proxy",
        "flag_vkr_too_small",
        "flag_vkr_too_large",
        "flag_invalid_x_percent",
        "flag_high_load_to_sn",
        "flag_voltage_ratio_mismatch",
        "flag_neutral_tap_assumption",
    ]
    return active[cols].sort_values("load_to_sn_ratio_proxy", ascending=False)


def main() -> None:
    DIAG.mkdir(parents=True, exist_ok=True)
    bus = read("pt_bus_table_acpf.csv")
    line = read("pt_line_table_acpf.csv")
    trafo = read("pt_trafo_table_acpf.csv")
    load = read("pt_load_table_acpf.csv")
    ext = read("pt_ext_grid_table_acpf.csv")

    component = component_diagnostics(bus, line, trafo, load, ext)
    balance = power_balance_checks(line, trafo, load, ext)
    impedance = impedance_checks(line)
    transformers = transformer_checks(trafo, load)

    write("pt_acpf_component_diagnostics.csv", component)
    write("pt_acpf_power_balance_checks.csv", balance)
    write("pt_acpf_impedance_sanity_checks.csv", impedance)
    write("pt_acpf_transformer_sanity_checks.csv", transformers)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "active_counts": component.to_dict("records"),
        "impedance_flags": {
            "near_zero": int(impedance["flag_near_zero_impedance"].sum()),
            "large_impedance": int(impedance["flag_large_impedance"].sum()),
            "unusual_r_x_ratio": int(impedance["flag_unusual_r_x_ratio"].sum()),
            "long_benchmark_line": int(impedance["flag_long_benchmark_line"].sum()),
            "mixed_proxy": int(impedance["flag_mixed_proxy"].sum()),
            "extreme_capacitance": int(impedance["flag_extreme_capacitance"].sum()),
        },
        "transformer_flags": {
            "vkr_too_small": int(transformers["flag_vkr_too_small"].sum()),
            "vkr_too_large": int(transformers["flag_vkr_too_large"].sum()),
            "invalid_x_percent": int(transformers["flag_invalid_x_percent"].sum()),
            "high_load_to_sn": int(transformers["flag_high_load_to_sn"].sum()),
            "voltage_ratio_mismatch": int(transformers["flag_voltage_ratio_mismatch"].sum()),
            "neutral_tap_assumption": int(transformers["flag_neutral_tap_assumption"].sum()),
        },
    }
    (DIAG / "pt_acpf_static_diagnostics_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
