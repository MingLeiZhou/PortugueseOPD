"""Build Portuguese pandapower-schema candidate tables without running solvers.

Step 3C + Step 4A dry run:
- constructs bus/line/trafo/load/sgen/ext_grid-shaped tables;
- attaches parameter source/status/confidence metadata;
- defines sensitivity scenarios;
- runs non-solver quality checks only.

This script must not call pandapower.runpp or pandapower.runopp.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "processed" / "pandapower_schema"
REPORTS = ROOT / "reports"
FIGURES = ROOT / "figures"

import os

os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".cache" / "matplotlib"))

import matplotlib.pyplot as plt
import pandas as pd


MISSING = "MISSING"
NOT_SOLVER_READY = False


def read_csv(path: str | Path, **kwargs: Any) -> pd.DataFrame:
    return pd.read_csv(ROOT / path, **kwargs)


def write_csv(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def normalize_code(value: Any) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip()


def parse_voltage_label(value: Any) -> float | None:
    if pd.isna(value):
        return None
    text = str(value).replace(",", ".")
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def parse_ratio_voltages(value: Any) -> tuple[float | None, float | None]:
    if pd.isna(value):
        return None, None
    nums = [float(x.replace(",", ".")) for x in re.findall(r"\d+(?:[,.]\d+)?", str(value))]
    if len(nums) >= 2:
        return nums[0], nums[1]
    if len(nums) == 1:
        return None, nums[0]
    return None, None


def parse_geojson_points(path: Path, facility_type: str, source_dataset: str) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    data = json.loads(path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    for feature in data.get("features", []):
        props = feature.get("properties") or {}
        geom = feature.get("geometry") or {}
        coords = geom.get("coordinates") or [None, None]
        lon, lat = (coords + [None, None])[:2] if isinstance(coords, list) else (None, None)
        rows.append(
            {
                "facility_code": normalize_code(props.get("codigo")),
                "facility_name": props.get("instalacao"),
                "facility_type": facility_type,
                "district": props.get("distrito"),
                "municipality": props.get("concelho"),
                "zone": props.get("nut3"),
                "longitude": lon,
                "latitude": lat,
                "source_dataset": source_dataset,
            }
        )
    return pd.DataFrame(rows)


def build_facility_base(branches: pd.DataFrame, transformer_availability: pd.DataFrame) -> pd.DataFrame:
    facility_parts: list[pd.DataFrame] = []
    for side in ["from", "to"]:
        part = branches[
            [
                f"{side}_facility_code",
                f"{side}_facility_name",
                f"{side}_facility_type",
            ]
        ].copy()
        part.columns = ["facility_code", "facility_name", "facility_type"]
        part["in_interfacility_topology"] = True
        facility_parts.append(part)

    trans = transformer_availability[["substation_code", "facility_name", "facility_type"]].copy()
    trans.columns = ["facility_code", "facility_name", "facility_type"]
    trans["in_interfacility_topology"] = False
    facility_parts.append(trans)

    facilities = pd.concat(facility_parts, ignore_index=True)
    facilities["facility_code"] = facilities["facility_code"].map(normalize_code)
    facilities = (
        facilities.sort_values("in_interfacility_topology", ascending=False)
        .drop_duplicates("facility_code", keep="first")
        .reset_index(drop=True)
    )
    return facilities


def build_bus_table(branches: pd.DataFrame, transformer_availability: pd.DataFrame) -> pd.DataFrame:
    facilities = build_facility_base(branches, transformer_availability)

    coords = pd.concat(
        [
            parse_geojson_points(ROOT / "data/raw/se-at_2025.geojson", "SE_AT", "se-at_2025"),
            parse_geojson_points(ROOT / "data/raw/pc-at_2025.geojson", "PC_AT", "pc-at_2025"),
            parse_geojson_points(ROOT / "data/raw/se-mt_2025.geojson", "SE_MT", "se-mt_2025"),
        ],
        ignore_index=True,
    )
    coords["facility_code"] = coords["facility_code"].map(normalize_code)
    coords = coords.drop_duplicates("facility_code", keep="first")

    trans = transformer_availability.copy()
    trans["facility_code"] = trans["substation_code"].map(normalize_code)
    trans_cols = [
        "facility_code",
        "transformation_ratio",
        "high_voltage_kv",
        "low_voltage_kv",
        "source_district",
        "capacity_municipality",
    ]
    trans_small = trans[[c for c in trans_cols if c in trans.columns]].drop_duplicates("facility_code")

    base = facilities.merge(coords, on=["facility_code", "facility_name", "facility_type"], how="left")
    if base["latitude"].isna().any():
        coord_any = coords.drop(columns=[c for c in ["facility_name", "facility_type"] if c in coords.columns])
        base = base.drop(columns=[c for c in ["district", "municipality", "zone", "longitude", "latitude", "source_dataset"] if c in base.columns])
        base = base.merge(coord_any, on="facility_code", how="left")
    base = base.merge(trans_small, on="facility_code", how="left")

    rows: list[dict[str, Any]] = []
    for _, row in base.iterrows():
        code = normalize_code(row.get("facility_code"))
        ftype = row.get("facility_type")
        hv = row.get("high_voltage_kv")
        lv = row.get("low_voltage_kv")
        ratio = row.get("transformation_ratio")
        if pd.isna(hv) or hv is None:
            hv = 60.0 if ftype in {"SE_AT", "PC_AT"} else None
        if pd.isna(lv):
            lv = None

        role = "AT_substation" if ftype == "SE_AT" else "posto_de_corte" if ftype == "PC_AT" else "unknown"
        source_dataset = row.get("source_dataset")
        if pd.isna(source_dataset):
            source_dataset = "at_transformer_parameter_availability"

        common = {
            "facility_code": code,
            "facility_name": row.get("facility_name"),
            "facility_type": ftype,
            "zone": row.get("zone"),
            "municipality": row.get("municipality") if not pd.isna(row.get("municipality")) else row.get("capacity_municipality"),
            "district": row.get("district") if not pd.isna(row.get("district")) else row.get("source_district"),
            "latitude": row.get("latitude"),
            "longitude": row.get("longitude"),
            "source_dataset": source_dataset,
            "bus_status": "candidate",
            "solver_ready": NOT_SOLVER_READY,
        }

        rows.append(
            {
                **common,
                "bus_id": f"{code}_60",
                "voltage_kv": hv,
                "topology_role": role,
                "voltage_level_status": "direct" if not pd.isna(hv) and hv else "missing",
                "notes": "AT-side candidate bus. Not solver-ready until full electrical parameters, slack, and voltage-control assumptions are available.",
            }
        )
        if ftype == "SE_AT" and lv is not None and not pd.isna(lv):
            rows.append(
                {
                    **common,
                    "bus_id": f"{code}_{int(lv) if float(lv).is_integer() else lv}",
                    "voltage_kv": lv,
                    "topology_role": "transformer_node",
                    "voltage_level_status": "derived",
                    "notes": f"MT-side candidate bus derived from transformation_ratio={ratio}. Not solver-ready until transformer unit and R/X/tap assumptions are resolved.",
                }
            )

    bus = pd.DataFrame(rows)
    bus = bus.drop_duplicates("bus_id").sort_values(["facility_code", "voltage_kv"], ascending=[True, False]).reset_index(drop=True)
    return bus


def choose_parallel_group(row: pd.Series) -> str:
    key = row.get("duplicate_branch_key")
    if pd.isna(key) or not str(key).strip():
        key = f"{row.get('from_bus')}__{row.get('to_bus')}__{row.get('voltage')}__{row.get('status')}"
    return "PG_" + re.sub(r"[^A-Za-z0-9]+", "_", str(key)).strip("_")


def build_line_table(line_inputs: pd.DataFrame, branches: pd.DataFrame) -> pd.DataFrame:
    merged = line_inputs.merge(
        branches[
            [
                "branch_id",
                "from_facility_code",
                "to_facility_code",
                "from_facility_type",
                "to_facility_type",
                "confidence_score",
                "classification",
            ]
        ],
        on="branch_id",
        how="left",
        suffixes=("", "_branch"),
    )
    rows: list[dict[str, Any]] = []
    for _, row in merged.iterrows():
        asset_type = row.get("asset_type") if not pd.isna(row.get("asset_type")) else "unknown"
        voltage_kv = row.get("voltage_kv")
        line_id = row.get("branch_id")
        status = row.get("status")
        length_km = row.get("total_length_km")
        assumption_notes = []
        if asset_type == "cable":
            max_i_status = "SCENARIO_ASSUMED"
            max_i_source = "SRC_EREDES_DMAC33281"
            max_i_conf = "medium"
            thermal_status = "SCENARIO_ASSUMED"
            thermal_source = "SRC_EREDES_DMAC33281"
            thermal_conf = "medium"
            scenario_id = "S1_cable_transformer_source_backed"
            assumption_notes.append("Cable current/thermal bands exist from E-REDES 36/60 kV scenarios, but branch cable section and installation condition are unknown.")
        elif asset_type == "mixed":
            max_i_status = thermal_status = "MISSING"
            max_i_source = thermal_source = ""
            max_i_conf = thermal_conf = "missing"
            scenario_id = "S0_missing_only"
            assumption_notes.append("Mixed overhead/cable branch is not split by segment-level electrical length; keep non-solver-ready.")
        elif asset_type == "overhead":
            max_i_status = thermal_status = "MISSING"
            max_i_source = thermal_source = ""
            max_i_conf = thermal_conf = "missing"
            scenario_id = "S0_missing_only"
            assumption_notes.append("No complete Portuguese 60 kV overhead current/thermal LUT is defensibly sourced.")
        else:
            max_i_status = thermal_status = "MISSING"
            max_i_source = thermal_source = ""
            max_i_conf = thermal_conf = "missing"
            scenario_id = "S0_missing_only"
            assumption_notes.append("Asset type unknown.")

        r_status = x_status = c_status = "MISSING"
        r_source = x_source = c_source = ""
        r_conf = x_conf = c_conf = "missing"
        if asset_type in {"overhead", "cable"}:
            assumption_notes.append("R/X/B are intentionally missing unless a future scenario explicitly selects source-backed or benchmark-only values.")

        self_loop = normalize_code(row.get("from_bus")) == normalize_code(row.get("to_bus"))
        solver_ready = False
        rows.append(
            {
                "line_id": line_id,
                "from_bus": f"{normalize_code(row.get('from_bus'))}_60",
                "to_bus": f"{normalize_code(row.get('to_bus'))}_60",
                "from_facility_code": normalize_code(row.get("from_bus")),
                "to_facility_code": normalize_code(row.get("to_bus")),
                "voltage_kv": voltage_kv,
                "length_km": length_km,
                "asset_type": asset_type,
                "status": status,
                "number_of_original_segments": row.get("number_of_original_segments"),
                "topology_confidence_score": row.get("topology_confidence_score", row.get("confidence_score")),
                "parallel_group_id": choose_parallel_group(row),
                "r_ohm_per_km": "",
                "r_value_status": r_status,
                "r_source_id": r_source,
                "r_source_confidence": r_conf,
                "r_scenario_id": scenario_id,
                "r_assumption_note": "Missing: no final Portuguese R LUT selected.",
                "x_ohm_per_km": "",
                "x_value_status": x_status,
                "x_source_id": x_source,
                "x_source_confidence": x_conf,
                "x_scenario_id": scenario_id,
                "x_assumption_note": "Missing: no final Portuguese X LUT selected.",
                "c_nf_per_km": "",
                "b_siemens_per_km": "",
                "c_b_value_status": c_status,
                "c_b_source_id": c_source,
                "c_b_source_confidence": c_conf,
                "c_b_scenario_id": scenario_id,
                "c_b_assumption_note": "Missing: no final Portuguese B/capacitance LUT selected.",
                "max_i_ka": "",
                "max_i_value_status": max_i_status,
                "max_i_source_id": max_i_source,
                "max_i_source_confidence": max_i_conf,
                "max_i_scenario_id": scenario_id,
                "max_i_assumption_note": "Scenario band available only for E-REDES 36/60 kV cables." if asset_type == "cable" else "Missing.",
                "thermal_limit_mva": "",
                "thermal_limit_value_status": thermal_status,
                "thermal_limit_source_id": thermal_source,
                "thermal_limit_source_confidence": thermal_conf,
                "thermal_limit_scenario_id": scenario_id,
                "thermal_limit_assumption_note": "Derived only inside scenario from selected cable current band." if asset_type == "cable" else "Missing.",
                "self_loop": self_loop,
                "solver_ready": solver_ready,
                "notes": " ".join(assumption_notes),
            }
        )
    return pd.DataFrame(rows)


def nearest_transformer_rating(installed_power: Any, candidates: pd.DataFrame) -> tuple[float | None, float | None, str]:
    if pd.isna(installed_power):
        return None, None, "missing_installed_power"
    ratings = candidates[["rated_mva", "short_circuit_impedance_percent"]].dropna()
    if ratings.empty:
        return None, None, "missing_candidate_lut"
    val = float(installed_power)
    exact = ratings[ratings["rated_mva"].round(6) == round(val, 6)]
    if not exact.empty:
        r = exact.iloc[0]
        return float(r["rated_mva"]), float(r["short_circuit_impedance_percent"]), "exact_installed_power_match"
    idx = (ratings["rated_mva"] - val).abs().idxmin()
    r = ratings.loc[idx]
    return float(r["rated_mva"]), float(r["short_circuit_impedance_percent"]), "nearest_rating_scenario_only_installed_power_is_not_unit_rating"


def build_trafo_table(bus: pd.DataFrame, transformer_availability: pd.DataFrame, trafo_candidates: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    trans = transformer_availability.copy()
    trans = trans[trans["transformation_ratio"].notna()].copy()
    trans["facility_code"] = trans["substation_code"].map(normalize_code)
    available_bus_ids = set(bus["bus_id"])
    for _, row in trans.iterrows():
        code = normalize_code(row.get("facility_code"))
        hv = row.get("high_voltage_kv")
        lv = row.get("low_voltage_kv")
        if pd.isna(hv) or pd.isna(lv):
            parsed_hv, parsed_lv = parse_ratio_voltages(row.get("transformation_ratio"))
            hv = hv if not pd.isna(hv) else parsed_hv
            lv = lv if not pd.isna(lv) else parsed_lv
        if pd.isna(hv) or pd.isna(lv):
            continue
        hv_bus = f"{code}_{int(hv) if float(hv).is_integer() else hv}"
        lv_bus = f"{code}_{int(lv) if float(lv).is_integer() else lv}"
        if hv_bus not in available_bus_ids or lv_bus not in available_bus_ids:
            continue
        sn_mva = row.get("potencia_instalada")
        matched_rating, uk, match_status = nearest_transformer_rating(sn_mva, trafo_candidates)
        uk_status = "SOURCE_BACKED" if match_status == "exact_installed_power_match" else "SCENARIO_ASSUMED" if uk is not None else "MISSING"
        confidence = "high" if uk_status == "SOURCE_BACKED" else "medium" if uk_status == "SCENARIO_ASSUMED" else "missing"
        rows.append(
            {
                "trafo_id": f"TR_{code}_{int(hv)}_{int(lv)}",
                "hv_bus": hv_bus,
                "lv_bus": lv_bus,
                "facility_code": code,
                "facility_name": row.get("facility_name"),
                "hv_kv": hv,
                "lv_kv": lv,
                "sn_mva": sn_mva,
                "transformation_ratio": row.get("transformation_ratio"),
                "matched_lut_rated_mva": matched_rating,
                "uk_percent": uk if uk is not None else "",
                "vk_percent": uk if uk is not None else "",
                "vkr_percent": "",
                "uk_value_status": uk_status,
                "r_x_split_status": "MISSING_OR_ASSUMED",
                "tap_side": "hv",
                "tap_neutral": 0,
                "tap_min": -11,
                "tap_max": 11,
                "tap_step_percent": 1.5,
                "tap_range_value_status": "SOURCE_BACKED",
                "tap_position_status": "MISSING_ACTUAL_POSITION",
                "unit_count_status": "MISSING",
                "source_id": "SRC_EREDES_DMAC52140" if uk is not None else "",
                "confidence": confidence,
                "solver_ready": False,
                "notes": f"E-REDES 60 kV/MT uk% candidate match status: {match_status}. Installed power is not confirmed as individual transformer unit rating; vkr/RX split and actual tap position are missing.",
            }
        )
    return pd.DataFrame(rows).drop_duplicates("trafo_id").reset_index(drop=True)


def build_load_table(bus: pd.DataFrame, transformer_availability: pd.DataFrame) -> pd.DataFrame:
    bus_codes = set(bus["facility_code"].map(normalize_code))
    rows: list[dict[str, Any]] = []
    for _, row in transformer_availability.iterrows():
        code = normalize_code(row.get("substation_code"))
        if code not in bus_codes:
            continue
        lv = row.get("low_voltage_kv")
        bus_id = f"{code}_{int(lv) if not pd.isna(lv) and float(lv).is_integer() else lv}" if not pd.isna(lv) else f"{code}_60"
        if bus_id not in set(bus["bus_id"]):
            bus_id = f"{code}_60"
        for load_type, col, status in [
            ("natural_load", "carga_natural_mean", "direct"),
            ("guaranteed_power", "potencia_garantida_mean", "direct"),
        ]:
            p_mw = row.get(col)
            if pd.isna(p_mw):
                continue
            rows.append(
                {
                    "load_id": f"LD_{code}_{load_type}",
                    "bus_id": bus_id,
                    "facility_code": code,
                    "facility_name": row.get("facility_name"),
                    "p_mw": p_mw,
                    "q_mvar": "",
                    "load_source": "at_transformer_parameter_availability joined from carga-na-subestacao",
                    "load_type": load_type,
                    "timestamp": "",
                    "season": "inverno_verao_mean",
                    "p_status": status,
                    "q_status": "missing",
                    "power_factor_assumption": "",
                    "confidence": "medium",
                    "solver_ready": False,
                    "notes": "P is substation-level summary value. Q is not available; no reactive power was invented. Hourly load readiness is not claimed.",
                }
            )
    return pd.DataFrame(rows).drop_duplicates("load_id").reset_index(drop=True)


def build_sgen_table() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "sgen_id",
            "bus_id",
            "facility_code",
            "p_mw",
            "q_mvar",
            "source",
            "generation_type",
            "p_status",
            "q_status",
            "confidence",
            "solver_ready",
            "notes",
        ]
    )


def build_ext_grid_table(bus: pd.DataFrame, short_circuit: pd.DataFrame) -> pd.DataFrame:
    bus_codes = set(bus["facility_code"].map(normalize_code))
    rows: list[dict[str, Any]] = []
    sc = short_circuit.copy()
    sc["facility_code"] = sc["substation_code"].map(normalize_code)
    sc = sc[sc["facility_code"].isin(bus_codes)]
    for _, row in sc.iterrows():
        smax = row.get("short_circuit_power_max_at_mva")
        smin = row.get("short_circuit_power_min_at_mva")
        branch_count = row.get("candidate_graph_branch_count")
        if pd.isna(branch_count) or branch_count < 3:
            continue
        rows.append(
            {
                "ext_grid_id": f"EG_CAND_{normalize_code(row.get('facility_code'))}",
                "bus_id": f"{normalize_code(row.get('facility_code'))}_60",
                "facility_code": normalize_code(row.get("facility_code")),
                "candidate_reason": "High-degree AT graph node with available AT short-circuit data; candidate only, not confirmed REN/RNT interface.",
                "connection_to_REN_or_RNT_status": "unconfirmed",
                "vm_pu": "",
                "va_degree": "",
                "s_sc_max_mva": smax if not pd.isna(smax) else "",
                "s_sc_min_mva": smin if not pd.isna(smin) else "",
                "rx_ratio_status": "missing",
                "confidence": "low",
                "solver_ready": False,
                "notes": "Short-circuit power is context/validation only. No Thevenin equivalent or final slack bus selected.",
            }
        )
    return pd.DataFrame(rows).drop_duplicates("facility_code").reset_index(drop=True)


def build_scenarios() -> pd.DataFrame:
    rows = [
        {
            "scenario_id": "S0_missing_only",
            "purpose": "Represent current direct/source-backed schema with missing unsourced electrical values.",
            "included_assets": "Topology, bus candidates, line metadata, transformer/load inventory.",
            "allowed_sources": "Direct E-REDES topology and metadata only.",
            "prohibited_claims": "No solver readiness, no impedance completeness, no OPF readiness.",
            "solver_allowed": False,
            "publication_allowed": "yes",
            "notes": "Baseline audit scenario.",
        },
        {
            "scenario_id": "S1_cable_transformer_source_backed",
            "purpose": "Attach E-REDES cable ampacity bands and transformer uk% candidates where applicable.",
            "included_assets": "Cable current/thermal bands; transformer uk% and tap range/step.",
            "allowed_sources": "SRC_EREDES_DMAC33281; SRC_EREDES_DMAC52140.",
            "prohibited_claims": "No final branch parameters; no overhead completion; no PF/OPF.",
            "solver_allowed": False,
            "publication_allowed": "yes with caveats",
            "notes": "Requires explicit cable section/installation and transformer unit-rating assumptions.",
        },
        {
            "scenario_id": "S2_low_confidence_benchmark_plumbing",
            "purpose": "Use benchmark-only values solely to test code plumbing outside solver runs.",
            "included_assets": "pandapower benchmark standard types if explicitly selected later.",
            "allowed_sources": "SRC_PANDAPOWER_STD only as benchmark-only.",
            "prohibited_claims": "Not publishable as Portuguese physical model; no validation claim.",
            "solver_allowed": False,
            "publication_allowed": "no",
            "notes": "Useful for table-shape tests only.",
        },
        {
            "scenario_id": "S3_sensitivity_band_candidate",
            "purpose": "Define low/medium/high sensitivity bands where source ranges exist.",
            "included_assets": "Cable ampacity bands; possible transformer uk% rating bands; benchmark-only rows if flagged.",
            "allowed_sources": "Direct/source-backed rows first; benchmark rows only if clearly separated.",
            "prohibited_claims": "No final parameter selection, no operational model, no OPF readiness.",
            "solver_allowed": False,
            "publication_allowed": "yes with caveats",
            "notes": "Future sensitivity design; not executed here.",
        },
    ]
    return pd.DataFrame(rows)


def build_status_matrix(line: pd.DataFrame, trafo: pd.DataFrame, load: pd.DataFrame, sgen: pd.DataFrame, ext_grid: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    def add(object_type: str, parameter: str, status: str, source: str, confidence: str, count_ready: int, count_total: int, notes: str) -> None:
        rows.append(
            {
                "object_type": object_type,
                "parameter": parameter,
                "status": status,
                "source_id": source,
                "confidence": confidence,
                "ready_or_available_rows": count_ready,
                "total_rows": count_total,
                "coverage_percent": round(100 * count_ready / count_total, 4) if count_total else 0.0,
                "notes": notes,
            }
        )

    add("bus", "bus_id/facility/voltage", "PARTIALLY_READY", "E-REDES topology/facility data", "medium", 0, 0, "Bus schema exists but solver_ready remains false.")
    for param, col in [
        ("line r_ohm_per_km", "r_value_status"),
        ("line x_ohm_per_km", "x_value_status"),
        ("line c_nf_per_km / b", "c_b_value_status"),
        ("line max_i_ka", "max_i_value_status"),
        ("line thermal_limit_mva", "thermal_limit_value_status"),
    ]:
        counts = line[col].value_counts()
        source_backed = int((line[col].isin(["SOURCE_BACKED", "DIRECT", "DERIVED"])).sum())
        scenario = int((line[col] == "SCENARIO_ASSUMED").sum())
        available = source_backed + scenario
        if source_backed:
            status = "PARTIALLY_READY"
            confidence = "medium"
        elif scenario:
            status = "SCENARIO_ONLY"
            confidence = "medium"
        else:
            status = "MISSING"
            confidence = "missing"
        add("line", param, status, "", confidence, available, len(line), f"Status distribution: {counts.to_dict()}")
    add("trafo", "uk/vk_percent", "SCENARIO_ONLY", "SRC_EREDES_DMAC52140", "medium", int(trafo["uk_percent"].astype(str).str.len().gt(0).sum()) if len(trafo) else 0, len(trafo), "uk% is source-backed where rating match is exact, scenario-only where installed power is used as proxy.")
    add("trafo", "vkr_percent / R/X split", "MISSING", "", "missing", 0, len(trafo), "No vkr_percent or load-loss-based R/X split was fabricated.")
    add("trafo", "tap range/step", "PARTIALLY_READY", "SRC_EREDES_DMAC52140", "high", len(trafo), len(trafo), "Range/step source-backed; actual tap position/control missing.")
    add("load", "p_mw", "PARTIALLY_READY", "carga-na-subestacao via availability table", "medium", len(load), len(load), "Summary P values only; hourly join not claimed.")
    add("load", "q_mvar", "MISSING", "", "missing", 0, len(load), "Reactive load not available and not invented.")
    add("sgen", "actual generation dispatch", "MISSING", "", "missing", 0, len(sgen), "Reception capacity is not actual generation.")
    add("ext_grid", "slack bus and Thevenin equivalent", "MISSING", "short-circuit data context only", "low", 0, len(ext_grid), "No final slack selected; rx ratio missing.")
    return pd.DataFrame(rows)


def build_readiness_matrix() -> pd.DataFrame:
    rows = [
        ("bus", "name/vn_kv/index", True, True, "partially_ready", "multi-voltage representation and voltage limits not fully validated", "Validate busbar/node inventory and voltage levels."),
        ("line", "from_bus/to_bus/length_km", True, True, "ready", "", "Keep topology QA and no self-loop checks."),
        ("line", "r_ohm_per_km", True, True, "missing", "No final Portuguese 60 kV line R LUT selected.", "Request E-REDES/REN standard overhead/cable parameters."),
        ("line", "x_ohm_per_km", True, True, "missing", "No final Portuguese 60 kV line X LUT selected.", "Request E-REDES/REN standard overhead/cable parameters."),
        ("line", "c_nf_per_km", True, True, "missing", "Cable/line capacitance not available for final use.", "Request manufacturer fichas or operator standards."),
        ("line", "max_i_ka", False, True, "scenario_only", "Cable ampacity bands exist; overhead current missing; branch cable section unknown.", "Get branch-level conductor/cable section and installation condition."),
        ("trafo", "hv_bus/lv_bus/sn_mva", True, True, "partially_ready", "Installed power exists but unit count/rating not confirmed.", "Request transformer unit count or station inventory."),
        ("trafo", "vk_percent", True, True, "scenario_only", "E-REDES uk% table exists, but rating match often uses installed power proxy.", "Confirm unit rating mapping."),
        ("trafo", "vkr_percent", True, True, "missing", "R/X split missing.", "Request load losses or typical R/X split."),
        ("trafo", "tap fields", False, True, "partially_ready", "Range/step known; actual position/control missing.", "Request tap-control assumptions."),
        ("load", "p_mw", True, True, "partially_ready", "Substation summary loads available; hourly mapping not validated.", "Join and validate hourly diagrams."),
        ("load", "q_mvar", True, True, "missing", "Reactive power absent.", "Request Q or state power-factor scenario separately."),
        ("sgen", "p_mw/q_mvar", False, True, "missing", "No actual generation dispatch available.", "Use DGEG/REN generation data later; do not use hosting capacity as dispatch."),
        ("ext_grid", "slack bus/vm_pu/va_degree", True, True, "missing", "No confirmed REN/RNT interface/slack assumption.", "Identify interface substations and define slack policy."),
        ("poly_cost / OPF costs", "cost function", False, True, "missing", "No OPF cost or penalty function.", "Add market/generator costs or explicit penalty objective later."),
    ]
    return pd.DataFrame(
        rows,
        columns=[
            "table",
            "field",
            "required_for_basic_ac_pf",
            "required_for_opf",
            "current_status",
            "blocking_issue",
            "recommended_next_action",
        ],
    )


def quality_checks(bus: pd.DataFrame, line: pd.DataFrame, trafo: pd.DataFrame, load: pd.DataFrame, sgen: pd.DataFrame, ext_grid: pd.DataFrame) -> dict[str, Any]:
    bus_ids = set(bus["bus_id"])
    checks = {
        "bus_rows": int(len(bus)),
        "bus_id_unique": bool(bus["bus_id"].is_unique),
        "line_rows": int(len(line)),
        "line_from_bus_missing_count": int((~line["from_bus"].isin(bus_ids)).sum()),
        "line_to_bus_missing_count": int((~line["to_bus"].isin(bus_ids)).sum()),
        "line_self_loop_count": int(line["self_loop"].sum()),
        "line_nonpositive_length_count": int((pd.to_numeric(line["length_km"], errors="coerce") <= 0).sum()),
        "line_asset_type_distribution": line["asset_type"].value_counts(dropna=False).to_dict(),
        "line_r_missing_count": int((line["r_value_status"] == "MISSING").sum()),
        "line_x_missing_count": int((line["x_value_status"] == "MISSING").sum()),
        "line_c_b_missing_count": int((line["c_b_value_status"] == "MISSING").sum()),
        "line_max_i_missing_count": int((line["max_i_value_status"] == "MISSING").sum()),
        "line_max_i_scenario_count": int((line["max_i_value_status"] == "SCENARIO_ASSUMED").sum()),
        "line_source_confidence_distribution": Counter(line[["r_source_confidence", "x_source_confidence", "c_b_source_confidence", "max_i_source_confidence"]].to_numpy().ravel()).copy(),
        "trafo_rows": int(len(trafo)),
        "trafo_hv_bus_missing_count": int((~trafo["hv_bus"].isin(bus_ids)).sum()) if len(trafo) else 0,
        "trafo_lv_bus_missing_count": int((~trafo["lv_bus"].isin(bus_ids)).sum()) if len(trafo) else 0,
        "trafo_vkr_missing_count": int((trafo["vkr_percent"].astype(str).str.len() == 0).sum()) if len(trafo) else 0,
        "load_rows": int(len(load)),
        "load_bus_missing_count": int((~load["bus_id"].isin(bus_ids)).sum()) if len(load) else 0,
        "load_q_missing_count": int((load["q_status"] == "missing").sum()) if len(load) else 0,
        "sgen_rows": int(len(sgen)),
        "ext_grid_candidate_count": int(len(ext_grid)),
        "solver_ready_rows": {
            "bus": int(bus["solver_ready"].sum()) if len(bus) else 0,
            "line": int(line["solver_ready"].sum()) if len(line) else 0,
            "trafo": int(trafo["solver_ready"].sum()) if len(trafo) else 0,
            "load": int(load["solver_ready"].sum()) if len(load) else 0,
            "sgen": int(sgen["solver_ready"].sum()) if len(sgen) else 0,
            "ext_grid": int(ext_grid["solver_ready"].sum()) if len(ext_grid) else 0,
        },
    }
    checks["line_source_confidence_distribution"] = dict(checks["line_source_confidence_distribution"])
    return checks


def plot_figures(line: pd.DataFrame, status_matrix: pd.DataFrame) -> None:
    FIGURES.mkdir(parents=True, exist_ok=True)
    asset_counts = line["asset_type"].value_counts()
    plt.figure(figsize=(7, 4))
    asset_counts.plot(kind="bar", color=["#4C78A8", "#F58518", "#54A24B", "#B279A2"][: len(asset_counts)])
    plt.title("Portuguese Candidate AT Branch Asset Types")
    plt.xlabel("Asset type")
    plt.ylabel("Branch count")
    plt.tight_layout()
    plt.savefig(FIGURES / "pt_asset_type_distribution.png", dpi=160)
    plt.close()

    conf = Counter(line[["r_source_confidence", "x_source_confidence", "c_b_source_confidence", "max_i_source_confidence", "thermal_limit_source_confidence"]].to_numpy().ravel())
    plt.figure(figsize=(7, 4))
    pd.Series(conf).sort_index().plot(kind="bar", color="#4C78A8")
    plt.title("Candidate Branch Parameter Source Confidence")
    plt.xlabel("Confidence")
    plt.ylabel("Field count across branch parameters")
    plt.tight_layout()
    plt.savefig(FIGURES / "pt_branch_confidence_distribution.png", dpi=160)
    plt.close()

    coverage = status_matrix[status_matrix["total_rows"] > 0].copy()
    coverage["missing_percent"] = 100 - coverage["coverage_percent"]
    plt.figure(figsize=(9, 5))
    plt.barh(coverage["object_type"] + ": " + coverage["parameter"], coverage["coverage_percent"], color="#54A24B", label="available/source-backed")
    plt.barh(coverage["object_type"] + ": " + coverage["parameter"], coverage["missing_percent"], left=coverage["coverage_percent"], color="#E45756", label="missing/scenario")
    plt.xlabel("Coverage [%]")
    plt.title("Parameter Coverage Status")
    plt.legend()
    plt.tight_layout()
    plt.savefig(FIGURES / "pt_schema_parameter_coverage.png", dpi=160)
    plt.close()


def markdown_table(df: pd.DataFrame, columns: list[str], max_rows: int | None = None) -> str:
    view = df.loc[:, columns].copy()
    if max_rows is not None:
        view = view.head(max_rows)
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    lines = [header, sep]
    for _, row in view.iterrows():
        vals = []
        for c in columns:
            val = row[c]
            if pd.isna(val):
                val = ""
            vals.append(str(val).replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines)


def write_report(
    bus: pd.DataFrame,
    line: pd.DataFrame,
    trafo: pd.DataFrame,
    load: pd.DataFrame,
    sgen: pd.DataFrame,
    ext_grid: pd.DataFrame,
    status_matrix: pd.DataFrame,
    readiness: pd.DataFrame,
    scenarios: pd.DataFrame,
    checks: dict[str, Any],
) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    asset_counts = line["asset_type"].value_counts().to_dict()
    value_status = {
        "line_r": line["r_value_status"].value_counts().to_dict(),
        "line_x": line["x_value_status"].value_counts().to_dict(),
        "line_c_b": line["c_b_value_status"].value_counts().to_dict(),
        "line_max_i": line["max_i_value_status"].value_counts().to_dict(),
        "line_thermal": line["thermal_limit_value_status"].value_counts().to_dict(),
        "trafo_uk": trafo["uk_value_status"].value_counts().to_dict() if len(trafo) else {},
    }
    report = f"""# 12 Portuguese Parameterization And Pandapower Schema

Generated: {datetime.now(timezone.utc).isoformat()}

Scope: Step 3C + Step 4A dry run only. This creates Portuguese candidate input tables shaped for future pandapower construction. It does not run AC power flow, OPF, or cascading simulation; it does not assign final Portuguese electrical parameters.

## Created Tables

| table | rows | solver-ready rows |
| --- | ---: | ---: |
| bus | {len(bus)} | {int(bus['solver_ready'].sum()) if len(bus) else 0} |
| line | {len(line)} | {int(line['solver_ready'].sum()) if len(line) else 0} |
| trafo | {len(trafo)} | {int(trafo['solver_ready'].sum()) if len(trafo) else 0} |
| load | {len(load)} | {int(load['solver_ready'].sum()) if len(load) else 0} |
| sgen | {len(sgen)} | 0 |
| ext_grid | {len(ext_grid)} | {int(ext_grid['solver_ready'].sum()) if len(ext_grid) else 0} |

Outputs are under `data/processed/pandapower_schema/`.

## Parameter Status

Candidate AT lines: {len(line)}. Asset type distribution: `{asset_counts}`.

Value-status summary: `{value_status}`.

{markdown_table(status_matrix, ['object_type', 'parameter', 'status', 'source_id', 'confidence', 'ready_or_available_rows', 'total_rows', 'coverage_percent'], max_rows=30)}

## Scenarios

{markdown_table(scenarios, ['scenario_id', 'purpose', 'allowed_sources', 'solver_allowed', 'publication_allowed'], max_rows=None)}

## Solver Readiness Matrix

{markdown_table(readiness, ['table', 'field', 'current_status', 'blocking_issue', 'recommended_next_action'], max_rows=None)}

## Non-Solver Quality Checks

```json
{json.dumps(checks, indent=2, ensure_ascii=False)}
```

## Answers

1. Can Step 3C and Step 4A be performed together?

Yes. Parameterization dry-run metadata and pandapower-shaped schema construction are tightly coupled: each table row needs source/status/confidence labels before it can be safely converted into a solver object.

2. What candidate pandapower tables were created?

`bus`, `line`, `trafo`, `load`, `sgen`, and `ext_grid` candidate CSVs were created, plus parameter status, scenario, readiness, and summary files.

3. How many objects were produced?

The dry run produced {len(bus)} bus candidates, {len(line)} line candidates, {len(trafo)} transformer candidates, {len(load)} load candidates, {len(sgen)} sgen candidates, and {len(ext_grid)} external-grid candidates.

4. Which fields are source-backed?

Topology fields, 60 kV bus/line voltage, line length, facility identity, and line asset type come from E-REDES/RND processed topology. Transformer uk% candidates and transformer tap range/step are source-backed by `SRC_EREDES_DMAC52140`. Cable current/thermal scenario bands are source-backed by `SRC_EREDES_DMAC33281`, but not selected branch-by-branch.

5. Which fields are scenario-only?

Cable current and thermal limits are scenario-only because branch-level cable section and installation condition are unknown. Transformer uk% is source-backed only when a candidate unit rating can be matched; otherwise it is scenario-only because installed substation power is not confirmed as individual transformer rating.

6. Which fields remain missing?

Line R/X/B, overhead current/thermal limits, cable R/X/capacitance, circuit count, transformer R/X split/vkr%, transformer unit count, actual tap position/control mode, reactive load, real generation dispatch, slack-bus selection, Thevenin R/X, voltage-control assumptions, and OPF costs remain missing.

7. Are any rows solver-ready?

No. All candidate rows keep `solver_ready=false` because solver-blocking electrical fields and system assumptions are still unresolved.

8. Why is the Portuguese model still not allowed to run AC PF / OPF?

The branch and transformer schemas are structurally closer to pandapower, but core electrical values are incomplete or scenario-only. Running PF/OPF now would require selecting unsourced line impedances, overhead ratings, reactive load assumptions, transformer R/X split, slack model, tap controls, and OPF costs.

9. What exact gaps remain before the first safe AC PF test?

Minimum gaps: complete line R/X/B by asset class, defensible current/thermal limits or explicit loading caveats, transformer R/X split, transformer unit/rating mapping, reactive load or stated power-factor scenario, slack/interface selection, voltage limits, and tap position/control assumptions.

10. What should be requested from E-REDES / REN?

Request standard 60 kV overhead conductor families and R/X/B/current ratings; 36/60 kV cable R/X/capacitance/current by approved cable type and installation condition; branch circuit counts; transformer unit counts, ratings, load losses/R/X split, tap-control assumptions; confirmed REN/RNT interface substations; aggregate circuit-km/rating statistics; and citable permission for typical ranges.

11. What should be the next step?

Step 4B should validate hourly load joins and reactive-power assumptions separately, then Step 4C can create a non-executed pandapower net object builder that fails closed when required parameters remain missing. The first solver run should wait until the readiness matrix no longer has RED blockers for basic AC PF.

## Final Conclusion

GREEN:

- Pandapower-shaped candidate tables now exist for bus, line, transformer, load, sgen, and external-grid objects.
- Topology, facility codes, 60 kV line voltage, line length, line asset type, transformer voltage ratios, installed power, short-circuit context, cable current bands, transformer uk%, and tap range/step are preserved with source/status labels.

YELLOW:

- Cable current/thermal limits and transformer uk% can support sensitivity scenarios, but not final branch-level solver values.
- Load P values exist as substation summaries; hourly load and Q are not ready.
- External-grid candidates can be listed from graph/short-circuit context, but no slack bus is confirmed.

RED:

- No row is solver-ready.
- Line R/X/B, overhead ratings, cable R/X/capacitance, circuit counts, transformer R/X split, unit counts, actual tap settings/control, Q loads, generation dispatch, slack definition, voltage-control assumptions, and OPF costs still block AC PF and OPF.
"""
    (REPORTS / "12_portuguese_parameterization_and_pandapower_schema.md").write_text(report, encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    FIGURES.mkdir(parents=True, exist_ok=True)

    branches = read_csv("data/processed/at_interfacility_candidate_branches.csv")
    line_inputs = read_csv("data/processed/at_line_parameter_inputs.csv")
    trafo_candidates = read_csv("data/processed/step3b_transformer_parameter_candidates.csv")
    transformer_availability = read_csv("data/processed/at_transformer_parameter_availability.csv")
    short_circuit = read_csv("data/processed/at_short_circuit_validation_inputs.csv")

    bus = build_bus_table(branches, transformer_availability)
    line = build_line_table(line_inputs, branches)
    trafo = build_trafo_table(bus, transformer_availability, trafo_candidates)
    load = build_load_table(bus, transformer_availability)
    sgen = build_sgen_table()
    ext_grid = build_ext_grid_table(bus, short_circuit)
    scenarios = build_scenarios()
    status_matrix = build_status_matrix(line, trafo, load, sgen, ext_grid)
    readiness = build_readiness_matrix()
    checks = quality_checks(bus, line, trafo, load, sgen, ext_grid)

    write_csv(OUT / "pt_bus_table_candidate.csv", bus)
    write_csv(OUT / "pt_line_table_candidate.csv", line)
    write_csv(OUT / "pt_trafo_table_candidate.csv", trafo)
    write_csv(OUT / "pt_load_table_candidate.csv", load)
    write_csv(OUT / "pt_sgen_table_candidate.csv", sgen)
    write_csv(OUT / "pt_ext_grid_table_candidate.csv", ext_grid)
    write_csv(OUT / "pt_parameter_status_matrix.csv", status_matrix)
    write_csv(OUT / "pt_solver_readiness_matrix.csv", readiness)
    write_csv(OUT / "pt_parameterization_scenarios.csv", scenarios)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "Step 3C + Step 4A dry run only; no solver executed",
        "row_counts": {
            "bus": int(len(bus)),
            "line": int(len(line)),
            "trafo": int(len(trafo)),
            "load": int(len(load)),
            "sgen": int(len(sgen)),
            "ext_grid": int(len(ext_grid)),
        },
        "quality_checks": checks,
        "solver_ready": False,
        "solver_run_performed": False,
        "reason_not_solver_ready": [
            "line R/X/B missing",
            "overhead current/thermal limits missing",
            "cable R/X/capacitance missing",
            "circuit counts missing",
            "transformer R/X split missing",
            "reactive load missing",
            "slack/interface model missing",
            "tap position/control missing",
            "OPF costs missing",
        ],
    }
    (OUT / "pt_schema_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    plot_figures(line, status_matrix)
    write_report(bus, line, trafo, load, sgen, ext_grid, status_matrix, readiness, scenarios, checks)

    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
