"""Complete minimum fields for a Portuguese AC PF scenario table set.

This step creates scenario-labelled ACPF tables only. It does not build a
pandapower net and does not run a solver.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections import Counter, defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
READY = ROOT / "data" / "processed" / "acpf_ready"
SCHEMA = ROOT / "data" / "processed" / "pandapower_schema"
RESULTS = ROOT / "data" / "processed" / "acpf_results"
REPORTS = ROOT / "reports"
FIGURES = ROOT / "figures"
MIXED_POLICY_PATH = ROOT / "data" / "processed" / "mixed_corridor_policy_table.csv"

DEFAULT_SCENARIO_ID = "S_ACPF_BENCHMARK_PLUMBING_BASE"
DEFAULT_READINESS_STATUS = "AC_PF_BENCHMARK_PLUMBING_READY"
BEST_AVAILABLE_SCENARIO_ID = "S5_BEST_AVAILABLE_MULTILINGUAL_DIAGNOSTIC"
BEST_AVAILABLE_READINESS_STATUS = "AC_PF_BEST_AVAILABLE_DIAGNOSTIC_READY"


def read_table(name: str) -> pd.DataFrame:
    return pd.read_csv(SCHEMA / name)


def write_table(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def yes_no(value: bool) -> str:
    return "PASS" if value else "FAIL"


def thermal_mva(voltage_kv: float, current_ka: float) -> float:
    return math.sqrt(3.0) * voltage_kv * current_ka


def q_from_pf(p_mw: float, pf: float) -> float:
    return p_mw * math.tan(math.acos(pf))


def parse_nf(value: Any) -> float | None:
    if pd.isna(value):
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", str(value))
    return float(match.group(0)) if match else None


def connected_components(lines: pd.DataFrame) -> list[set[str]]:
    graph: dict[str, set[str]] = defaultdict(set)
    for _, row in lines.iterrows():
        a = str(row["from_bus"])
        b = str(row["to_bus"])
        graph[a].add(b)
        graph[b].add(a)
    seen: set[str] = set()
    comps: list[set[str]] = []
    for node in graph:
        if node in seen:
            continue
        comp: set[str] = set()
        queue: deque[str] = deque([node])
        seen.add(node)
        while queue:
            current = queue.popleft()
            comp.add(current)
            for nxt in graph[current]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        comps.append(comp)
    return sorted(comps, key=len, reverse=True)


def choose_energized_component(lines: pd.DataFrame, ext_grid: pd.DataFrame) -> tuple[set[str], dict[str, Any]]:
    comps = connected_components(lines)
    ext_buses = set(ext_grid["bus_id"].dropna().astype(str))
    for rank, comp in enumerate(comps, start=1):
        candidates = sorted(comp.intersection(ext_buses))
        if candidates:
            return comp, {"component_rank": rank, "component_bus_count": len(comp), "ext_grid_candidates_in_component": len(candidates)}
    return comps[0] if comps else set(), {"component_rank": 1, "component_bus_count": len(comps[0]) if comps else 0, "ext_grid_candidates_in_component": 0}


def select_slack(ext_grid: pd.DataFrame, energized_60kv_buses: set[str]) -> tuple[str | None, pd.DataFrame]:
    ext = ext_grid.copy()
    ext["in_energized_component"] = ext["bus_id"].astype(str).isin(energized_60kv_buses)
    ext["s_sc_max_mva_numeric"] = numeric(ext["s_sc_max_mva"])
    candidates = ext[ext["in_energized_component"]].copy()
    if candidates.empty:
        ext["selected_slack"] = False
        return None, ext
    candidates = candidates.sort_values(["s_sc_max_mva_numeric", "facility_code"], ascending=[False, True])
    selected = str(candidates.iloc[0]["ext_grid_id"])
    ext["selected_slack"] = ext["ext_grid_id"].astype(str) == selected
    return selected, ext


def load_overhead_base() -> dict[str, Any]:
    df = pd.read_csv(ROOT / "data" / "processed" / "step3b_overhead_line_parameter_candidates.csv")
    pp = df[(df["source_id"] == "SRC_PANDAPOWER_STD") & df["r_ohm_per_km"].notna()].copy()
    # Use a middle 110 kV European standard type as a benchmark plumbing value.
    row = pp.sort_values("cross_section_mm2").iloc[len(pp) // 2]
    c_nf = parse_nf(row["capacitance_or_b"])
    return {
        "asset": "overhead",
        "r_ohm_per_km": float(row["r_ohm_per_km"]),
        "x_ohm_per_km": float(row["x_ohm_per_km"]),
        "c_nf_per_km": float(c_nf),
        "max_i_ka": float(row["rated_current_a"]) / 1000.0,
        "source_id": "SRC_PANDAPOWER_STD",
        "source_confidence": "low",
        "source_status": "BENCHMARK_ONLY",
        "assumption_note": f"Benchmark-only overhead parameters from {row['conductor_type']}; not Portugal-specific and not publication-ready.",
    }


def load_cable_base(use_best_available: bool = False) -> dict[str, Any]:
    cable = pd.read_csv(ROOT / "data" / "processed" / "step3b_cable_parameter_candidates.csv")
    pp = cable[(cable["source_id"] == "SRC_PANDAPOWER_STD") & cable["r_ohm_per_km"].notna()].copy()
    pp_row = pp.sort_values("cross_section_mm2").iloc[len(pp) // 2]
    eredes = cable[
        (cable["source_id"] == "SRC_EREDES_DMAC33281")
        & (cable["cross_section_mm2"] == 630)
        & (cable["installation_type"] == "buried_soil_1_circuit_hot")
    ].iloc[0]
    if use_best_available:
        diagnostic_path = ROOT / "data" / "processed" / "step3b_best_available_cable_diagnostic_table.csv"
        if diagnostic_path.exists():
            diagnostic = pd.read_csv(diagnostic_path)
            selected = diagnostic[diagnostic["cross_section_mm2"] == 630].copy()
            if selected.empty and len(diagnostic):
                selected = diagnostic.sort_values("cross_section_mm2").iloc[[len(diagnostic) // 2]]
            if len(selected):
                row = selected.iloc[0]
                return {
                    "asset": "cable",
                    "r_ohm_per_km": float(row["r_ohm_per_km"]),
                    "x_ohm_per_km": float(row["x_ohm_per_km"]),
                    "c_nf_per_km": float(row["c_nf_per_km"]),
                    "max_i_ka": float(row["rated_current_a"]) / 1000.0,
                    "source_id": str(row.get("source_ids", "")),
                    "source_confidence": "best_available_multilingual_diagnostic_merged_section_bucket",
                    "source_status": "BEST_AVAILABLE_CABLE_DIAGNOSTIC_MERGED",
                    "assumption_note": f"Cable R/X/C/current selected from merged best-available cable diagnostic table, section={row.get('cross_section_mm2')} mm2. Current voltage={row.get('nominal_voltage_kv_for_current')}; impedance voltage={row.get('nominal_voltage_kv_for_impedance')}. Diagnostic only; not publication-ready.",
                }
    return {
        "asset": "cable",
        "r_ohm_per_km": float(pp_row["r_ohm_per_km"]),
        "x_ohm_per_km": float(pp_row["x_ohm_per_km"]),
        "c_nf_per_km": float(parse_nf(pp_row["capacitance_per_km"])),
        "max_i_ka": float(eredes["rated_current_a"]) / 1000.0,
        "source_id": "SRC_PANDAPOWER_STD;SRC_EREDES_DMAC33281",
        "source_confidence": "low_for_rxc_medium_for_current",
        "source_status": "BENCHMARK_ONLY_RXC_AND_SCENARIO_ASSUMED_CURRENT",
        "assumption_note": "R/X/C from pandapower 64/110 kV cable benchmark; current from E-REDES 36/60 kV 630 mm2 buried hot scenario. Branch cable section is unknown.",
    }


def combine_mixed(overhead: dict[str, Any], cable: dict[str, Any]) -> dict[str, Any]:
    return {
        "asset": "mixed",
        "r_ohm_per_km": 0.5 * overhead["r_ohm_per_km"] + 0.5 * cable["r_ohm_per_km"],
        "x_ohm_per_km": 0.5 * overhead["x_ohm_per_km"] + 0.5 * cable["x_ohm_per_km"],
        "c_nf_per_km": 0.5 * overhead["c_nf_per_km"] + 0.5 * cable["c_nf_per_km"],
        "max_i_ka": min(overhead["max_i_ka"], cable["max_i_ka"]),
        "source_id": "SRC_PANDAPOWER_STD;SRC_EREDES_DMAC33281",
        "source_confidence": "low",
        "source_status": "SCENARIO_ASSUMED_FROM_50_50_MIXED_ASSET_PROXY",
        "assumption_note": "Mixed branch uses a 50/50 overhead/cable proxy because segment-level asset lengths are not available.",
    }


def load_mixed_policy() -> pd.DataFrame:
    return pd.read_csv(MIXED_POLICY_PATH) if MIXED_POLICY_PATH.exists() else pd.DataFrame()


def weighted_mixed_params(line_id: str, overhead: dict[str, Any], cable: dict[str, Any], policy: pd.DataFrame) -> dict[str, Any] | None:
    if policy.empty or not line_id:
        return None
    row = policy[policy["line_id"].astype(str) == str(line_id)]
    if row.empty:
        return None
    row = row.iloc[0]
    if str(row.get("policy_class", "")) != "MIXED_WEIGHTED_ALLOWED":
        return None
    oh = pd.to_numeric(pd.Series([row.get("overhead_share")]), errors="coerce").iloc[0]
    cb = pd.to_numeric(pd.Series([row.get("cable_share")]), errors="coerce").iloc[0]
    if pd.isna(oh) or pd.isna(cb):
        return None
    return {
        "asset": "mixed",
        "r_ohm_per_km": oh * overhead["r_ohm_per_km"] + cb * cable["r_ohm_per_km"],
        "x_ohm_per_km": oh * overhead["x_ohm_per_km"] + cb * cable["x_ohm_per_km"],
        "c_nf_per_km": oh * overhead["c_nf_per_km"] + cb * cable["c_nf_per_km"],
        "max_i_ka": min(overhead["max_i_ka"], cable["max_i_ka"]),
        "source_id": "MIXED_CORRIDOR_POLICY_WEIGHTED;SRC_PANDAPOWER_STD;SRC_EREDES_DMAC33281",
        "source_confidence": "manual_review_weighted_diagnostic",
        "source_status": "MIXED_WEIGHTED_ALLOWED",
        "assumption_note": f"Mixed corridor weighted by policy table: overhead_share={oh}, cable_share={cb}. Diagnostic only.",
    }


def complete_lines(line: pd.DataFrame, energized_60kv_buses: set[str], scenario_id: str, use_best_available: bool = False) -> pd.DataFrame:
    overhead = load_overhead_base()
    cable = load_cable_base(use_best_available=use_best_available)
    mixed = combine_mixed(overhead, cable)
    policy = load_mixed_policy()
    by_asset = {"overhead": overhead, "cable": cable, "mixed": mixed}
    out = line.copy()
    rows: list[dict[str, Any]] = []
    for _, row in out.iterrows():
        asset = str(row.get("asset_type") or "unknown")
        line_id = str(row.get("line_id", ""))
        params = weighted_mixed_params(line_id, overhead, cable, policy) if asset == "mixed" else None
        if params is None:
            params = by_asset.get(asset, overhead)
        voltage_kv = float(row["voltage_kv"])
        max_i_ka = params["max_i_ka"]
        completed = row.to_dict()
        completed.update(
            {
                "r_ohm_per_km": params["r_ohm_per_km"],
                "x_ohm_per_km": params["x_ohm_per_km"],
                "c_nf_per_km": params["c_nf_per_km"],
                "b_siemens_per_km": "",
                "max_i_ka": max_i_ka,
                "thermal_limit_mva": thermal_mva(voltage_kv, max_i_ka),
                "circuit_count": 1,
                "circuit_count_status": "SCENARIO_ASSUMED",
                "parameter_source_id": params["source_id"],
                "parameter_source_status": params["source_status"],
                "parameter_confidence": params["source_confidence"],
                "scenario_id": scenario_id,
                "publication_allowed": False,
                "in_service": bool(row["from_bus"] in energized_60kv_buses and row["to_bus"] in energized_60kv_buses),
                "solver_ready": True,
                "acpf_assumption_note": params["assumption_note"],
            }
        )
        for prefix in ["r", "x", "c_b"]:
            completed[f"{prefix}_value_status"] = params["source_status"]
            completed[f"{prefix}_source_id"] = params["source_id"]
            completed[f"{prefix}_source_confidence"] = params["source_confidence"]
            completed[f"{prefix}_scenario_id"] = scenario_id
        completed["max_i_value_status"] = "SCENARIO_ASSUMED" if asset == "cable" else params["source_status"]
        completed["max_i_source_id"] = params["source_id"]
        completed["max_i_source_confidence"] = params["source_confidence"]
        completed["max_i_scenario_id"] = scenario_id
        completed["thermal_limit_value_status"] = completed["max_i_value_status"]
        completed["thermal_limit_source_id"] = params["source_id"]
        completed["thermal_limit_source_confidence"] = params["source_confidence"]
        completed["thermal_limit_scenario_id"] = scenario_id
        rows.append(completed)
    return pd.DataFrame(rows)


def complete_trafos(trafo: pd.DataFrame, energized_60kv_buses: set[str], scenario_id: str) -> pd.DataFrame:
    out = trafo.copy()
    x_over_r = 20.0
    vk = numeric(out["vk_percent"])
    out["vkr_percent"] = vk / math.sqrt(1.0 + x_over_r**2)
    out["r_x_split_status"] = "SCENARIO_ASSUMED_X_OVER_R_20"
    out["pfe_kw"] = 0.0
    out["pfe_status"] = "SCENARIO_ASSUMED_ZERO_NO_LOAD_LOSS"
    out["i0_percent"] = 0.0
    out["i0_status"] = "SCENARIO_ASSUMED_ZERO_MAGNETIZING_CURRENT"
    out["tap_pos"] = 0
    out["tap_position_status"] = "SCENARIO_ASSUMED_NEUTRAL"
    out["unit_count"] = 1
    out["unit_count_status"] = "SCENARIO_ASSUMED_EQUIVALENT_ONE_TRAFO"
    out["parameter_source_status"] = out["uk_value_status"].fillna("SCENARIO_ASSUMED")
    out["scenario_id"] = scenario_id
    out["publication_allowed"] = False
    out["in_service"] = out["hv_bus"].astype(str).isin(energized_60kv_buses)
    out["solver_ready"] = True
    out["acpf_assumption_note"] = (
        "vk_percent from E-REDES uk% candidate table where matched; vkr_percent from X/R=20 scenario; "
        "one equivalent transformer per facility; neutral tap; no-load losses set to zero."
    )
    return out


def complete_loads(load: pd.DataFrame, energized_buses: set[str], scenario_id: str) -> pd.DataFrame:
    base = load[load["load_type"] == "natural_load"].copy()
    pf = 0.95
    base["p_mw"] = numeric(base["p_mw"])
    base["q_mvar"] = base["p_mw"].apply(lambda p: q_from_pf(float(p), pf) if not pd.isna(p) else math.nan)
    base["q_status"] = "SCENARIO_ASSUMED_POWER_FACTOR"
    base["power_factor_assumption"] = pf
    base["scenario_id"] = scenario_id
    base["publication_allowed"] = False
    base["in_service"] = base["bus_id"].astype(str).isin(energized_buses)
    base["solver_ready"] = True
    base["acpf_assumption_note"] = "P uses natural_load summary; Q computed with pf=0.95 lagging base scenario. Hourly readiness is not claimed."
    return base


def complete_buses(bus: pd.DataFrame, energized_buses: set[str], scenario_id: str) -> pd.DataFrame:
    out = bus.copy()
    out["min_vm_pu"] = 0.95
    out["max_vm_pu"] = 1.05
    out["voltage_limit_status"] = "SCENARIO_ASSUMED_0_95_1_05"
    out["in_service"] = out["bus_id"].astype(str).isin(energized_buses)
    out["scenario_id"] = scenario_id
    out["publication_allowed"] = False
    out["solver_ready"] = out["voltage_kv"].notna()
    return out


def complete_ext_grid(ext_grid: pd.DataFrame, energized_60kv_buses: set[str], scenario_id: str) -> pd.DataFrame:
    selected, ext = select_slack(ext_grid, energized_60kv_buses)
    ext["vm_pu"] = 1.0
    ext["va_degree"] = 0.0
    ext["slack_status"] = "SCENARIO_ASSUMED_SELECTED_BY_COMPONENT_AND_SHORT_CIRCUIT_STRENGTH"
    ext["scenario_id"] = scenario_id
    ext["publication_allowed"] = False
    ext["in_service"] = ext["selected_slack"]
    ext["solver_ready"] = ext["selected_slack"]
    ext["notes"] = ext["notes"].fillna("") + " Scenario slack selected only for ACPF plumbing; not a confirmed REN/RNT interface."
    return ext


def complete_sgen(sgen: pd.DataFrame) -> pd.DataFrame:
    out = sgen.copy()
    if "in_service" not in out.columns:
        out["in_service"] = []
    return out


def assumption_register(line: pd.DataFrame, trafo: pd.DataFrame, load: pd.DataFrame, bus: pd.DataFrame, ext: pd.DataFrame, scenario_id: str, use_best_available: bool = False) -> pd.DataFrame:
    rows = [
        {
            "assumption_id": "OH_R_X_C_BENCHMARK",
            "object_type": "line",
            "parameter": "overhead r/x/c/max_i",
            "affected_rows": int((line["asset_type"] == "overhead").sum()),
            "assumption_value": "pandapower 110 kV overhead standard type used as 60 kV benchmark plumbing",
            "unit": "ohm/km; nF/km; kA",
            "reason": "No Portugal-specific 60 kV overhead R/X/B/current LUT found.",
            "source_id": "SRC_PANDAPOWER_STD",
            "source_confidence": "low",
            "scenario_id": scenario_id,
            "publication_allowed": False,
            "risk": "high false-precision risk; not physically validated for Portuguese RND.",
            "validation_needed": "E-REDES/REN conductor families and standard 60 kV line parameters.",
        },
        {
            "assumption_id": "CABLE_RXC_BENCHMARK_CURRENT_SOURCE",
            "object_type": "line",
            "parameter": "cable r/x/c and max_i",
            "affected_rows": int((line["asset_type"] == "cable").sum()),
            "assumption_value": "Merged best-available cable diagnostic table when enabled; otherwise R/X/C from pandapower 64/110 kV benchmark and current from E-REDES 630 mm2 buried hot scenario.",
            "unit": "ohm/km; nF/km; kA",
            "reason": "E-REDES public 36/60 kV cable document has current scenarios but not R/X/capacitance; multilingual same-spec/product evidence fills diagnostic R/X/C buckets.",
            "source_id": "step3b_best_available_cable_diagnostic_table.csv" if use_best_available else "SRC_PANDAPOWER_STD;SRC_EREDES_DMAC33281",
            "source_confidence": "best_available_multilingual_diagnostic" if use_best_available else "low_for_rxc_medium_for_current",
            "scenario_id": scenario_id,
            "publication_allowed": False,
            "risk": "Diagnostic cable section bucket is assumed globally for cable branches; branch-level section and installation remain unknown." if use_best_available else "R/X/C are benchmark-only and cable section is unknown.",
            "validation_needed": "Branch-level cable section/installation plus manufacturer fichas or E-REDES approved cable electrical data.",
        },
        {
            "assumption_id": "MIXED_LINE_50_50_PROXY",
            "object_type": "line",
            "parameter": "mixed branch r/x/c/max_i",
            "affected_rows": int((line["asset_type"] == "mixed").sum()),
            "assumption_value": "50/50 overhead/cable proxy; max_i is minimum of component currents.",
            "unit": "ratio",
            "reason": "Segment-level lengths by asset type are not available in the candidate branch table.",
            "source_id": "SRC_PANDAPOWER_STD;SRC_EREDES_DMAC33281",
            "source_confidence": "low",
            "scenario_id": scenario_id,
            "publication_allowed": False,
            "risk": "Can materially distort impedance and loading of mixed branches.",
            "validation_needed": "Segment-level asset split and lengths.",
        },
        {
            "assumption_id": "CIRCUIT_COUNT_1_ASSUMPTION",
            "object_type": "line",
            "parameter": "circuit_count",
            "affected_rows": int(len(line)),
            "assumption_value": 1,
            "unit": "circuits",
            "reason": "Circuit count is unavailable.",
            "source_id": "",
            "source_confidence": "missing",
            "scenario_id": scenario_id,
            "publication_allowed": False,
            "risk": "Impedance and thermal capacity may be wrong for parallel circuits.",
            "validation_needed": "Official circuit counts or circuit-km by line.",
        },
        {
            "assumption_id": "TRAFO_RX_SPLIT_ASSUMPTION",
            "object_type": "trafo",
            "parameter": "vkr_percent",
            "affected_rows": int(len(trafo)),
            "assumption_value": "X/R=20",
            "unit": "ratio",
            "reason": "E-REDES source gives uk% but not R/X split or load losses.",
            "source_id": "SRC_EREDES_DMAC52140 for vk only",
            "source_confidence": "low_for_vkr",
            "scenario_id": scenario_id,
            "publication_allowed": False,
            "risk": "Losses and voltage drops depend on this split.",
            "validation_needed": "Transformer load losses or typical vkr% by rating.",
        },
        {
            "assumption_id": "TAP_NEUTRAL_ASSUMPTION",
            "object_type": "trafo",
            "parameter": "tap_pos",
            "affected_rows": int(len(trafo)),
            "assumption_value": 0,
            "unit": "tap step",
            "reason": "Tap range/step are source-backed, but actual tap position/control is unavailable.",
            "source_id": "SRC_EREDES_DMAC52140 for tap range/step",
            "source_confidence": "medium",
            "scenario_id": scenario_id,
            "publication_allowed": False,
            "risk": "Voltage profile may be materially affected.",
            "validation_needed": "Public tap-control assumptions or operator data.",
        },
        {
            "assumption_id": "LOAD_PF_095",
            "object_type": "load",
            "parameter": "q_mvar",
            "affected_rows": int(len(load)),
            "assumption_value": "pf=0.95 lagging",
            "unit": "power factor",
            "reason": "Reactive power is absent from open datasets.",
            "source_id": "",
            "source_confidence": "scenario",
            "scenario_id": scenario_id,
            "publication_allowed": False,
            "risk": "Reactive flows and voltages are scenario-dependent.",
            "validation_needed": "Measured Q, power factor, or operator planning assumption.",
        },
        {
            "assumption_id": "SLACK_SELECTED_BY_SCENARIO",
            "object_type": "ext_grid",
            "parameter": "slack bus",
            "affected_rows": int(ext["selected_slack"].sum()) if len(ext) else 0,
            "assumption_value": "highest short-circuit power candidate in selected energized component, vm=1.0 pu, va=0",
            "unit": "selection rule",
            "reason": "No confirmed REN/RNT interface or slack policy exists.",
            "source_id": "at_short_circuit_validation_inputs for context",
            "source_confidence": "low",
            "scenario_id": scenario_id,
            "publication_allowed": False,
            "risk": "Slack placement changes flows and losses.",
            "validation_needed": "Confirmed RNT/RND interface and boundary voltage policy.",
        },
        {
            "assumption_id": "VOLTAGE_LIMIT_095_105",
            "object_type": "bus",
            "parameter": "min_vm_pu/max_vm_pu",
            "affected_rows": int(len(bus)),
            "assumption_value": "0.95/1.05",
            "unit": "p.u.",
            "reason": "No finalized Portuguese voltage-limit scenario was available in previous tables.",
            "source_id": "",
            "source_confidence": "scenario",
            "scenario_id": scenario_id,
            "publication_allowed": False,
            "risk": "Validation thresholds may not match Portuguese operating/planning criteria.",
            "validation_needed": "E-REDES/ERSE voltage criteria for AT/MT model validation.",
        },
    ]
    return pd.DataFrame(rows)


def source_traceability(line: pd.DataFrame, trafo: pd.DataFrame, load: pd.DataFrame, ext: pd.DataFrame, scenario_id: str, readiness_status: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for object_type, df, parameter_cols in [
        ("line", line, ["r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka", "thermal_limit_mva"]),
        ("trafo", trafo, ["vk_percent", "vkr_percent", "tap_pos", "pfe_kw", "i0_percent"]),
        ("load", load, ["p_mw", "q_mvar"]),
        ("ext_grid", ext, ["vm_pu", "va_degree", "s_sc_max_mva", "s_sc_min_mva"]),
    ]:
        for parameter in parameter_cols:
            if parameter not in df.columns:
                continue
            rows.append(
                {
                    "object_type": object_type,
                    "parameter": parameter,
                    "non_null_rows": int(df[parameter].notna().sum()),
                    "total_rows": int(len(df)),
                    "source_ids": ";".join(sorted(set(str(x) for x in df.get("parameter_source_id", df.get("source_id", pd.Series(dtype=str))).dropna().unique()))) if len(df) else "",
                    "scenario_id": scenario_id,
                    "readiness_status": readiness_status,
                    "publication_allowed": False,
                }
            )
    return pd.DataFrame(rows)


def scenario_definitions(scenario_id: str, readiness_status: str, use_best_available: bool = False) -> pd.DataFrame:
    if use_best_available:
        return pd.DataFrame(
            [
                {
                    "scenario_id": BEST_AVAILABLE_SCENARIO_ID,
                    "readiness_label": BEST_AVAILABLE_READINESS_STATUS,
                    "purpose": "Attempt diagnostic AC PF with multilingual best-available cable parameter fills and preserved fail-closed provenance.",
                    "line_parameter_policy": "Overhead remains benchmark-only where needed; cable R/X/C/current use best-available multilingual selected rows when available; mixed lines use 50/50 proxy.",
                    "transformer_policy": "E-REDES uk% plus X/R=20 vkr scenario; one equivalent transformer; neutral taps.",
                    "load_policy": "Natural load P with pf=0.95 lagging Q scenario.",
                    "slack_policy": "One scenario slack in selected line component by short-circuit strength.",
                    "solver_allowed": True,
                    "publication_allowed": False,
                    "claims_allowed": "Diagnostic-only best-available multilingual AC PF; no source-backed readiness, no OPF, no operator-grade claim.",
                }
            ]
        )
    return pd.DataFrame(
        [
            {
                "scenario_id": scenario_id,
                "readiness_label": readiness_status,
                "purpose": "Attempt first Portuguese AC PF plumbing run with all required numeric fields filled.",
                "line_parameter_policy": "Overhead and cable R/X/C use benchmark-only European/pandapower values; cable current uses E-REDES scenario; mixed lines use 50/50 proxy.",
                "transformer_policy": "E-REDES uk% plus X/R=20 vkr scenario; one equivalent transformer; neutral taps.",
                "load_policy": "Natural load P with pf=0.95 lagging Q scenario.",
                "slack_policy": "One scenario slack in selected line component by short-circuit strength.",
                "solver_allowed": True,
                "publication_allowed": False,
                "claims_allowed": "AC PF plumbing/sensitivity only; no source-backed readiness, no OPF, no operator-grade claim.",
            }
        ]
    )


def readiness_gate(bus: pd.DataFrame, line: pd.DataFrame, trafo: pd.DataFrame, load: pd.DataFrame, ext: pd.DataFrame, assumptions: pd.DataFrame, scenario_id: str, readiness_status: str) -> pd.DataFrame:
    bus_ids = set(bus["bus_id"].astype(str))
    active_buses = bus[bus["in_service"]]
    active_lines = line[line["in_service"]]
    active_trafos = trafo[trafo["in_service"]]
    active_loads = load[load["in_service"]]
    selected_ext = ext[ext["in_service"]]

    checks: list[dict[str, Any]] = []

    def add(category: str, check: str, critical: bool, passed: bool, details: str) -> None:
        checks.append(
            {
                "category": category,
                "check_name": check,
                "critical": critical,
                "status": yes_no(passed),
                "details": details,
                "readiness_status": readiness_status,
                "scenario_id": scenario_id,
            }
        )

    add("bus", "bus_ids_unique", True, bus["bus_id"].is_unique, f"bus_rows={len(bus)}")
    add("bus", "active_buses_have_vn_kv", True, active_buses["voltage_kv"].notna().all(), f"active_buses={len(active_buses)}")
    add("bus", "voltage_limits_present", True, active_buses[["min_vm_pu", "max_vm_pu"]].notna().all().all(), "scenario 0.95/1.05")

    add("line", "active_line_buses_valid", True, active_lines["from_bus"].isin(bus_ids).all() and active_lines["to_bus"].isin(bus_ids).all(), f"active_lines={len(active_lines)}")
    add("line", "no_active_self_loops", True, not (active_lines["from_bus"].astype(str) == active_lines["to_bus"].astype(str)).any(), "")
    add("line", "active_line_length_positive", True, (numeric(active_lines["length_km"]) > 0).all(), "")
    for col in ["r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km"]:
        add("line", f"{col}_numeric", True, numeric(active_lines[col]).notna().all(), f"missing={int(numeric(active_lines[col]).isna().sum())}")
    add("line", "max_i_numeric_or_allowed", False, numeric(active_lines["max_i_ka"]).notna().all(), "Needed for loading interpretation; non-critical for PF equations.")

    add("trafo", "active_trafo_buses_valid", True, active_trafos["hv_bus"].isin(bus_ids).all() and active_trafos["lv_bus"].isin(bus_ids).all(), f"active_trafos={len(active_trafos)}")
    for col in ["sn_mva", "hv_kv", "lv_kv", "vk_percent", "vkr_percent"]:
        add("trafo", f"{col}_numeric", True, numeric(active_trafos[col]).notna().all(), f"missing={int(numeric(active_trafos[col]).isna().sum())}")
    add("trafo", "tap_fields_numeric_or_unused", True, active_trafos[["tap_neutral", "tap_min", "tap_max", "tap_step_percent", "tap_pos"]].apply(numeric).notna().all().all(), "")

    add("load", "active_load_bus_valid", True, active_loads["bus_id"].isin(bus_ids).all(), f"active_loads={len(active_loads)}")
    add("load", "p_mw_numeric", True, numeric(active_loads["p_mw"]).notna().all(), "")
    add("load", "q_mvar_numeric", True, numeric(active_loads["q_mvar"]).notna().all(), "")

    add("ext_grid", "one_selected_slack", True, len(selected_ext) == 1, f"selected_ext_grid={len(selected_ext)}")
    add("ext_grid", "slack_bus_valid", True, selected_ext["bus_id"].isin(bus_ids).all() if len(selected_ext) else False, "")
    add("ext_grid", "vm_va_numeric", True, numeric(selected_ext["vm_pu"]).notna().all() and numeric(selected_ext["va_degree"]).notna().all() if len(selected_ext) else False, "")

    add("network", "component_has_slack_and_load", True, len(selected_ext) == 1 and len(active_loads) > 0, f"active_loads={len(active_loads)}")
    add("network", "assumptions_registered", True, len(assumptions) >= 1, f"assumptions={len(assumptions)}")

    critical_pass = all(c["status"] == "PASS" for c in checks if c["critical"])
    checks.append(
        {
            "category": "overall",
            "check_name": "AC_PF_RUN_ALLOWED",
            "critical": True,
            "status": yes_no(critical_pass),
            "details": "Allowed only as benchmark plumbing test; not AC_PF_SCENARIO_READY or SOURCE_BACKED_READY.",
            "readiness_status": readiness_status if critical_pass else "NOT_READY",
            "scenario_id": scenario_id,
        }
    )
    return pd.DataFrame(checks)


def main() -> None:
    READY.mkdir(parents=True, exist_ok=True)
    RESULTS.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    bus0 = read_table("pt_bus_table_candidate.csv")
    line0 = read_table("pt_line_table_candidate.csv")
    trafo0 = read_table("pt_trafo_table_candidate.csv")
    load0 = read_table("pt_load_table_candidate.csv")
    sgen0 = read_table("pt_sgen_table_candidate.csv")
    ext0 = read_table("pt_ext_grid_table_candidate.csv")

    use_best_available = True
    scenario_id = BEST_AVAILABLE_SCENARIO_ID if use_best_available else DEFAULT_SCENARIO_ID
    readiness_status = BEST_AVAILABLE_READINESS_STATUS if use_best_available else DEFAULT_READINESS_STATUS

    energized_60kv, component_info = choose_energized_component(line0, ext0)
    line = complete_lines(line0, energized_60kv, scenario_id=scenario_id, use_best_available=use_best_available)
    trafo = complete_trafos(trafo0, energized_60kv, scenario_id=scenario_id)

    energized_buses = set(energized_60kv)
    energized_buses.update(trafo.loc[trafo["in_service"], "lv_bus"].astype(str))
    bus = complete_buses(bus0, energized_buses, scenario_id=scenario_id)
    load = complete_loads(load0, energized_buses, scenario_id=scenario_id)
    sgen = complete_sgen(sgen0)
    ext_grid = complete_ext_grid(ext0, energized_60kv, scenario_id=scenario_id)

    assumptions = assumption_register(line, trafo, load, bus, ext_grid, scenario_id=scenario_id, use_best_available=use_best_available)
    scenarios = scenario_definitions(scenario_id=scenario_id, readiness_status=readiness_status, use_best_available=use_best_available)
    traceability = source_traceability(line, trafo, load, ext_grid, scenario_id=scenario_id, readiness_status=readiness_status)
    gate = readiness_gate(bus, line, trafo, load, ext_grid, assumptions, scenario_id=scenario_id, readiness_status=readiness_status)

    write_table(READY / "pt_bus_table_acpf.csv", bus)
    write_table(READY / "pt_line_table_acpf.csv", line)
    write_table(READY / "pt_trafo_table_acpf.csv", trafo)
    write_table(READY / "pt_load_table_acpf.csv", load)
    write_table(READY / "pt_sgen_table_acpf.csv", sgen)
    write_table(READY / "pt_ext_grid_table_acpf.csv", ext_grid)
    write_table(READY / "pt_acpf_assumption_register.csv", assumptions)
    write_table(READY / "pt_acpf_readiness_gate.csv", gate)
    write_table(READY / "pt_acpf_scenario_definitions.csv", scenarios)
    write_table(READY / "pt_acpf_source_traceability.csv", traceability)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "readiness_status": readiness_status if (gate.query("check_name == 'AC_PF_RUN_ALLOWED'")["status"].iloc[0] == "PASS") else "NOT_READY",
        "scenario_id": scenario_id,
        "component_selection": component_info,
        "row_counts": {
            "bus": int(len(bus)),
            "active_bus": int(bus["in_service"].sum()),
            "line": int(len(line)),
            "active_line": int(line["in_service"].sum()),
            "trafo": int(len(trafo)),
            "active_trafo": int(trafo["in_service"].sum()),
            "load": int(len(load)),
            "active_load": int(load["in_service"].sum()),
            "sgen": int(len(sgen)),
            "ext_grid": int(len(ext_grid)),
            "selected_ext_grid": int(ext_grid["selected_slack"].sum()),
        },
        "line_parameter_status": line["parameter_source_status"].value_counts(dropna=False).to_dict(),
        "trafo_parameter_status": trafo["parameter_source_status"].value_counts(dropna=False).to_dict(),
        "critical_gate_pass": bool((gate[gate["critical"]]["status"] == "PASS").all()),
        "ac_pf_run_allowed": bool((gate.query("check_name == 'AC_PF_RUN_ALLOWED'")["status"].iloc[0] == "PASS")),
        "run_policy": "best_available_multilingual_diagnostic_only" if use_best_available else "benchmark_plumbing_test_only_no_publication_claims",
    }
    (READY / "pt_acpf_completion_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
