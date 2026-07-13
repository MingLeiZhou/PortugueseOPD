"""Trace local S5 pathology subgraphs around bad lines and high-voltage buses."""

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
OUT = ROOT / "data" / "processed" / "acpf_failure_frontier"
REPORTS = ROOT / "reports"
NET_PATH = READY / "pt_acpf_pandapower_net.json"
ALLOWED_READINESS = {"AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"}
SEED_BUSES = {383, 385, 409, 411, 412, 421, 422, 445, 446, 453, 454, 455, 456}
SEED_LINE_NAMES = {"ATPL_00003"}


def load_net_checked():
    if not NET_PATH.exists():
        raise RuntimeError(f"Fail-closed: net file does not exist: {NET_PATH}")
    net = pp.from_json(NET_PATH)
    opts = getattr(net, "user_pf_options", {}) or {}
    readiness = opts.get("readiness_status")
    if readiness not in ALLOWED_READINESS:
        raise RuntimeError(f"Fail-closed: readiness status {readiness!r} is not allowed for S5 local pathology diagnosis.")
    return net, opts


def run_reference_case(base_net):
    net = copy.deepcopy(base_net)
    if len(net.load):
        net.load["scaling"] = 0.02
        net.load["q_mvar"] = net.load["p_mw"] * math.tan(math.acos(0.95))
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
    return net


def graph_adjacency(net) -> dict[int, set[int]]:
    adj: dict[int, set[int]] = defaultdict(set)
    for _, row in net.line[net.line.in_service].iterrows():
        a = int(row["from_bus"])
        b = int(row["to_bus"])
        adj[a].add(b)
        adj[b].add(a)
    for _, row in net.trafo[net.trafo.in_service].iterrows():
        a = int(row["hv_bus"])
        b = int(row["lv_bus"])
        adj[a].add(b)
        adj[b].add(a)
    return adj


def expand_buses(net, depth: int = 2) -> set[int]:
    seeds = set(SEED_BUSES)
    if len(net.line):
        named = net.line[net.line["name"].astype(str).isin(SEED_LINE_NAMES)]
        for _, row in named.iterrows():
            seeds.add(int(row["from_bus"]))
            seeds.add(int(row["to_bus"]))
    adj = graph_adjacency(net)
    seen = set(seeds)
    queue: deque[tuple[int, int]] = deque((bus, 0) for bus in seeds)
    while queue:
        bus, dist = queue.popleft()
        if dist >= depth:
            continue
        for nxt in adj.get(bus, set()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append((nxt, dist + 1))
    return seen


def local_buses(net, bus_set: set[int]) -> pd.DataFrame:
    bus = net.bus.loc[sorted(bus_set)].copy().reset_index(names="bus_index")
    res = net.res_bus.copy().reset_index(names="bus_index")
    out = bus.merge(res, on="bus_index", how="left", suffixes=("", "_res"))
    out["is_seed_bus"] = out["bus_index"].isin(SEED_BUSES)
    out["has_load"] = out["bus_index"].isin(set(net.load.loc[net.load.in_service, "bus"].astype(int))) if len(net.load) else False
    out["has_ext_grid"] = out["bus_index"].isin(set(net.ext_grid.loc[net.ext_grid.in_service, "bus"].astype(int))) if len(net.ext_grid) else False
    keep = ["bus_index", "name", "vn_kv", "in_service", "vm_pu", "va_degree", "p_mw", "q_mvar", "is_seed_bus", "has_load", "has_ext_grid"]
    return out[[c for c in keep if c in out.columns]].sort_values("vm_pu", ascending=False)


def local_lines(net, bus_set: set[int]) -> pd.DataFrame:
    mask = net.line["from_bus"].astype(int).isin(bus_set) | net.line["to_bus"].astype(int).isin(bus_set)
    line = net.line[mask].copy().reset_index(names="line_index")
    res = net.res_line.copy().reset_index(names="line_index")
    out = line.merge(res, on="line_index", how="left", suffixes=("", "_res"))
    out["is_seed_line"] = out["name"].astype(str).isin(SEED_LINE_NAMES)
    for col in ["length_km", "c_nf_per_km", "max_i_ka", "loading_percent", "q_from_mvar", "q_to_mvar"]:
        if col in out:
            out[col] = pd.to_numeric(out[col], errors="coerce")
    out["charging_proxy_nf"] = out["length_km"].fillna(0.0) * out["c_nf_per_km"].fillna(0.0)
    out["abs_q_total_mvar"] = out["q_from_mvar"].abs().fillna(0.0) + out["q_to_mvar"].abs().fillna(0.0)
    keep = ["line_index", "name", "from_bus", "to_bus", "length_km", "r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka", "loading_percent", "p_from_mw", "q_from_mvar", "p_to_mw", "q_to_mvar", "pl_mw", "ql_mvar", "charging_proxy_nf", "abs_q_total_mvar", "is_seed_line", "in_service"]
    return out[[c for c in keep if c in out.columns]].sort_values("loading_percent", ascending=False)


def local_trafos(net, bus_set: set[int]) -> pd.DataFrame:
    mask = net.trafo["hv_bus"].astype(int).isin(bus_set) | net.trafo["lv_bus"].astype(int).isin(bus_set)
    trafo = net.trafo[mask].copy().reset_index(names="trafo_index")
    res = net.res_trafo.copy().reset_index(names="trafo_index")
    out = trafo.merge(res, on="trafo_index", how="left", suffixes=("", "_res"))
    keep = ["trafo_index", "name", "hv_bus", "lv_bus", "sn_mva", "vn_hv_kv", "vn_lv_kv", "vk_percent", "vkr_percent", "tap_pos", "loading_percent", "p_hv_mw", "q_hv_mvar", "p_lv_mw", "q_lv_mvar", "in_service"]
    return out[[c for c in keep if c in out.columns]].sort_values("loading_percent", ascending=False) if len(out) else out


def local_loads(net, bus_set: set[int]) -> pd.DataFrame:
    if not len(net.load):
        return pd.DataFrame()
    load = net.load[net.load["bus"].astype(int).isin(bus_set)].copy().reset_index(names="load_index")
    if "scaling" in load:
        load["effective_p_mw"] = pd.to_numeric(load["p_mw"], errors="coerce") * pd.to_numeric(load["scaling"], errors="coerce").fillna(1.0)
        load["effective_q_mvar"] = pd.to_numeric(load["q_mvar"], errors="coerce") * pd.to_numeric(load["scaling"], errors="coerce").fillna(1.0)
    keep = ["load_index", "name", "bus", "p_mw", "q_mvar", "scaling", "effective_p_mw", "effective_q_mvar", "in_service"]
    return load[[c for c in keep if c in load.columns]].sort_values("effective_p_mw", ascending=False)


def local_ext_grid(net, bus_set: set[int]) -> pd.DataFrame:
    if not len(net.ext_grid):
        return pd.DataFrame()
    ext = net.ext_grid[net.ext_grid["bus"].astype(int).isin(bus_set)].copy().reset_index(names="ext_grid_index")
    keep = ["ext_grid_index", "name", "bus", "vm_pu", "va_degree", "in_service"]
    return ext[[c for c in keep if c in ext.columns]]


def markdown_table(df: pd.DataFrame, max_rows: int = 40) -> str:
    if df.empty:
        return "_No rows._\n"
    view = df.head(max_rows)
    cols = list(view.columns)
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join(["---"] * len(cols)) + " |"]
    for _, row in view.iterrows():
        lines.append("| " + " | ".join(str(row.get(c, "")).replace("|", "\\|") for c in cols) + " |")
    return "\n".join(lines) + "\n"


def write_report(summary: dict[str, Any], buses: pd.DataFrame, lines: pd.DataFrame, trafos: pd.DataFrame, loads: pd.DataFrame, ext: pd.DataFrame) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    text = [
        "# 26 S5 Local Pathology Subgraph",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: two-hop local subgraph around `ATPL_00003` and the high-voltage 1106 bus cluster in the converged 2% load / DC-init S5 case. Diagnostic only.",
        "",
        "## Summary",
        "",
        markdown_table(pd.DataFrame([summary]).drop(columns=["generated_at"])),
        "",
        "## Local Buses",
        "",
        markdown_table(buses, 80),
        "",
        "## Local Lines",
        "",
        markdown_table(lines, 100),
        "",
        "## Local Transformers",
        "",
        markdown_table(trafos, 80),
        "",
        "## Local Loads",
        "",
        markdown_table(loads, 80),
        "",
        "## Local Ext Grid",
        "",
        markdown_table(ext, 20),
        "",
        "## Interpretation",
        "",
        "The local subgraph ties the highest-voltage 1106 cluster to multiple high-reactive-flow branches. If no ext_grid appears in this two-hop neighborhood, the overvoltage is not a local slack artifact; it is likely propagated through a weak/reactive-heavy radial area. The next targeted sensitivity should disable or de-rate `ATPL_00003`, cap line capacitance on the highest charging proxy branches, and rerun the dense frontier.",
    ]
    (REPORTS / "26_s5_local_pathology_subgraph.md").write_text("\n".join(text), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    base_net, opts = load_net_checked()
    net = run_reference_case(base_net)
    bus_set = expand_buses(net, depth=2)
    buses = local_buses(net, bus_set)
    lines = local_lines(net, bus_set)
    trafos = local_trafos(net, bus_set)
    loads = local_loads(net, bus_set)
    ext = local_ext_grid(net, bus_set)

    buses.to_csv(OUT / "s5_local_pathology_buses.csv", index=False)
    lines.to_csv(OUT / "s5_local_pathology_lines.csv", index=False)
    trafos.to_csv(OUT / "s5_local_pathology_trafos.csv", index=False)
    loads.to_csv(OUT / "s5_local_pathology_loads.csv", index=False)
    ext.to_csv(OUT / "s5_local_pathology_ext_grid.csv", index=False)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_status": opts.get("readiness_status"),
        "scenario_id": opts.get("scenario_id"),
        "reference_case": "low_load_002_dc",
        "seed_bus_count": len(SEED_BUSES),
        "local_bus_count": int(len(buses)),
        "local_line_count": int(len(lines)),
        "local_trafo_count": int(len(trafos)),
        "local_load_count": int(len(loads)),
        "local_ext_grid_count": int(len(ext)),
        "max_local_vm_pu": float(buses["vm_pu"].max()) if len(buses) else "",
        "min_local_vm_pu": float(buses["vm_pu"].min()) if len(buses) else "",
        "max_local_line_loading_percent": float(lines["loading_percent"].max()) if len(lines) else "",
        "max_local_charging_proxy_nf": float(lines["charging_proxy_nf"].max()) if len(lines) else "",
        "publication_allowed": False,
        "status": "DIAGNOSTIC_DONE",
    }
    (OUT / "s5_local_pathology_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    write_report(summary, buses, lines, trafos, loads, ext)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
