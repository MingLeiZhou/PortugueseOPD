#!/usr/bin/env python3
"""Build and solve one timestamped PT60 case from public-data inputs.

This module provides the shared single-case engine used by the archived replay
and public-input interfaces. Experiment schedules and result aggregation live
in their respective runners.
"""
from __future__ import annotations

import io
import json
import math
import os
import re
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pandapower as pp
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from build_pandapower import infer_discrete_tap_positions
from common import PROJECT, read_json, utc_now, write_json
from time_alignment import utc_interval_start, ren_source_index, eredes_source_label, source_time_metadata, SOURCE_ZONE


ROOT = PROJECT.parent
RELEASE = ROOT / "data" / "releases" / "PT60-v2.0.0"
MODEL_INPUT = PROJECT / "outputs" / "model" / "portuguese_hv_candidate.json"
GENERATOR_INPUT = PROJECT / "outputs" / "tables" / "generators.csv"
OUTPUT = PROJECT / "outputs" / "temporal_validation"
RAW_REN = PROJECT / "data" / "raw" / "ren"
RAW_WEATHER = PROJECT / "data" / "raw" / "weather"
RAW_EREDES = PROJECT / "data" / "raw" / "eredes" / "temporal_validation"
RAW_CROSS_BORDER = PROJECT / "data" / "raw" / "cross_border"
REN_URL = "https://servicebus.ren.pt/datahubapi/electricity/ElectricityProductionBreakdownDaily"
REN_INSTALLED_CAPACITY_URL = "https://servicebus.ren.pt/datahubapi/electricity/ElectricityInstalledPowerMonthly"
WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
EREDES_API_BASE = "https://e-redes.opendatasoft.com/api/explore/v2.1/catalog/datasets"
EREDES_LOAD_PARTITIONS = [
    "diagrama_carga_subestacao_01_a_07",
    "diagrama_carga_subestacao_08_a_10",
    "diagrama_carga_subestacao_11_a_12",
    "diagrama_carga_subestacao_13_a_15",
    "diagrama_carga_subestacao_16_a_18",
]
REN_RNT_BALANCE_CSV = "https://datahub.ren.pt/service/download/csv/2609"
ESIOS_API_BASE = "https://api.esios.ree.es/indicators"
ENTSOE_API_URL = "https://web-api.tp.entsoe.eu/api"
PT_BIDDING_ZONE = "10YPT-REN------W"
ES_BIDDING_ZONE = "10YES-REE------0"

GENERATION_GROUPS = {
    "Hydro": {"hydro"},
    "Solar": {"solar"},
    "Wind": {"wind"},
    "Natural Gas": {"gas", "gas;oil", "oil;gas"},
    "Other Thermal": {"oil", "diesel", "geothermal"},
    "Biomass": {"biomass", "biogas", "biomass;gas", "waste"},
    "Wave": {"wave"},
    "Battery Injection": {"battery"},
}

WEATHER_POINTS = {
    "Porto": (41.1579, -8.6291),
    "Lisbon": (38.7223, -9.1393),
    "Faro": (37.0194, -7.9304),
}

UNMAPPED_RESIDUAL_BUS_ID = "BUS:OSM:way:131715746:400"
PDIRT_ANNEX12_TABLE = PROJECT / "outputs" / "tables" / "pdirt_annex12_2025_pde_loads.csv"


def _public_data_session() -> requests.Session:
    """Return a GET session resilient to transient public-API interruptions."""
    retry = Retry(
        total=5,
        connect=5,
        read=3,
        status=5,
        backoff_factor=0.75,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
        respect_retry_after_header=True,
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.mount("http://", HTTPAdapter(max_retries=retry))
    return session


PUBLIC_DATA_SESSION = _public_data_session()

# The load-diagram dataset uses the substation (S) code while the independent
# capacity dataset uses the colocated delivery-point (P) code for these three
# facilities.  The names and geographic prefixes agree.  Keeping this as an
# explicit ledger avoids a fuzzy join and makes future source changes visible.
EREDES_CAPACITY_CODE_ALIASES = {
    "1107S5901900": "1107P5728100",  # Fanhões
    "1512S5031200": "1512P5903900",  # Sado
    "1808S5009500": "1808P5020500",  # Mortágua
}


def _pde_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\b(subestacao|central|termoelectrica|hidroeletrica|fotovoltaica|de|da|do|das|dos)\b", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


PDIRT_DIRECT_CLIENT_BUS_OVERRIDES = {
    "petrogal": "BUS:OSM:way:163214308:150",
    "repsol sines": "BUS:OSM:way:163214308:150",
    "siderurgia maia": "BUS:OSM:way:151109816:220",
    "sakthi maia": "BUS:OSM:way:151109816:220",
    "lusosider": "BUS:OSM:way:163226996:150",
    "neves corvo": "BUS:OSM:way:163204230:150",
}

# Public asset/bus inventory of reversible hydro stations represented in PT60.
# REN publishes aggregate pumping only; allocation within this set is therefore
# proportional to public generator nameplate and remains an explicit proxy.
PUMPED_STORAGE_BUS_IDS = {
    "BUS:OSM:way:151077578:150",  # Frades / Venda Nova complex
    "BUS:OSM:way:767739005:400",  # Gouvães
    "BUS:OSM:way:135457228:400",  # Alto Lindoso
    "BUS:OSM:relation:19651625:220",  # Aguieira
    "BUS:OSM:way:648169311:400",  # Alqueva II
    "BUS:OSM:way:771616113:220",  # Baixo Sabor upstream stage
}


def get_json(url: str, params: dict[str, object], cache: Path, refresh: bool) -> Any:
    if cache.exists() and not refresh:
        return read_json(cache)
    response = PUBLIC_DATA_SESSION.get(url, params=params, headers={"User-Agent": "PT60 temporal validation/0.1"}, timeout=120)
    response.raise_for_status()
    value = response.json()
    write_json(cache, value)
    return value


def eredes_load_snapshot(timestamp: pd.Timestamp, refresh: bool) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Download and cache one E-REDES quarter-hour substation snapshot."""
    timestamp = utc_interval_start(timestamp)
    source_date, source_hour = eredes_source_label(timestamp)
    cache = RAW_EREDES / "aligned_v2" / f"load_{timestamp.strftime('%Y-%m-%d_%H%M')}.json"
    if cache.exists() and not refresh:
        payload = read_json(cache)
        return pd.DataFrame(payload["records"]), payload["metadata"]
    records: list[dict[str, Any]] = []
    partition_counts: dict[str, int] = {}
    where = f"data=date'{source_date}' and hora='{source_hour}'"
    for dataset in EREDES_LOAD_PARTITIONS:
        offset = 0
        partition_records: list[dict[str, Any]] = []
        while True:
            response = PUBLIC_DATA_SESSION.get(
                f"{EREDES_API_BASE}/{dataset}/records",
                params={"where": where, "limit": 100, "offset": offset},
                headers={"User-Agent": "PT60 public-evidence validation/0.2"},
                timeout=120,
            )
            response.raise_for_status()
            result = response.json()
            batch = result.get("results", [])
            partition_records.extend(batch)
            offset += len(batch)
            if not batch or offset >= int(result.get("total_count", len(partition_records))):
                break
        partition_counts[dataset] = len(partition_records)
        for row in partition_records:
            row["dataset_partition"] = dataset
        records.extend(partition_records)
    if not records:
        raise RuntimeError(f"No E-REDES load records found for {timestamp.isoformat()}")
    metadata = {
        **source_time_metadata(timestamp),
        "source": "E-REDES Open Data substation load diagram",
        "timestamp_utc": timestamp.isoformat(),
        "interval_minutes": 15,
        "conversion": "p_mw = energia_kwh / 0.25 h / 1000 = energia_kwh / 250",
        "partition_counts": partition_counts,
        "record_count": len(records),
    }
    write_json(cache, {"metadata": metadata, "records": records})
    return pd.DataFrame(records), metadata


def eredes_substation_capacity(refresh: bool) -> pd.DataFrame:
    cache = RAW_EREDES / "substation_capacity_2025.json"
    if cache.exists() and not refresh:
        return pd.DataFrame(read_json(cache)["records"])
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = requests.get(
            f"{EREDES_API_BASE}/carga-na-subestacao/records",
            params={"limit": 100, "offset": offset},
            headers={"User-Agent": "PT60 public-evidence validation/0.2"},
            timeout=120,
        )
        response.raise_for_status()
        payload = response.json()
        batch = payload.get("results", [])
        records.extend(batch)
        offset += len(batch)
        if not batch or offset >= int(payload.get("total_count", len(records))):
            break
    write_json(cache, {"source": "E-REDES carga-na-subestacao", "records": records})
    return pd.DataFrame(records)


def ren_rnt_loss_benchmark(date: str, refresh: bool) -> dict[str, Any]:
    """Read the official monthly RNT physical-balance loss percentage."""
    cache = RAW_REN / f"rnt_balance_{date}.csv"
    if cache.exists() and not refresh:
        raw = cache.read_bytes()
    else:
        response = requests.get(
            REN_RNT_BALANCE_CSV,
            params={"startDateString": date, "endDateString": date, "culture": "en-GB"},
            headers={"User-Agent": "PT60 public-evidence validation/0.2"},
            timeout=120,
        )
        response.raise_for_status()
        raw = response.content
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(raw)
    table = pd.read_csv(io.BytesIO(raw), sep=";", encoding="utf-8-sig")
    first_column = table.columns[0]
    loss_rows = table[table[first_column].astype(str).str.strip().eq("LOSSES [%]")]
    if len(loss_rows) != 1:
        raise RuntimeError(f"REN RNT loss row missing or ambiguous for {date}")
    monthly_column = table.columns[2]
    value = float(loss_rows.iloc[0][monthly_column])
    return {
        "benchmark_date": date,
        "loss_percent": value,
        "source_url": f"{REN_RNT_BALANCE_CSV}?startDateString={date}&endDateString={date}&culture=en-GB",
        "scope": "REN National Transmission Network monthly physical balance",
    }


def _mapped_eredes_bus_lookup(net: pp.pandapowerNet) -> dict[str, int]:
    buses = net.bus[
        net.bus["source"].fillna("").eq("E-REDES")
        & net.bus["facility_code"].notna()
        & net.bus["in_service"].fillna(False)
    ].copy()
    buses = buses.sort_values(["facility_code", "vn_kv"], ascending=[True, True]).drop_duplicates("facility_code")
    return {str(row.facility_code): int(index) for index, row in buses.iterrows()}


def pdirt_residual_reference(
    net: pp.pandapowerNet, case: dict[str, Any], national_load_mw: float
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Map Annex 12 PdE rows and choose the closest published load regime."""
    if not PDIRT_ANNEX12_TABLE.exists():
        raise FileNotFoundError(f"Missing {PDIRT_ANNEX12_TABLE}; run extract_pdirt_annex12.py")
    # Prefer an explicit experimental-season label.  The timestamp fallback
    # avoids case-name heuristics.  May--September follows the two-season
    # convention used by the PDIRT Annex 12 tables for this public benchmark.
    season = str(case.get("pdirt_reference_season", "")).upper()
    if not season:
        month = pd.Timestamp(case["timestamp_utc"]).month
        season = "SUMMER" if 5 <= month <= 9 else "WINTER"
    if season not in {"SUMMER", "WINTER"}:
        raise ValueError(f"Unsupported PDIRT reference season: {season}")
    table = pd.read_csv(PDIRT_ANNEX12_TABLE)
    table = table[table["season"].eq(season)].copy()
    regimes = {
        "peak": float(table["peak_p_mw"].sum()),
        "intermediate": float(table["intermediate_p_mw"].sum()),
        "valley": float(table["valley_p_mw"].sum()),
    }
    regime = min(regimes, key=lambda name: abs(regimes[name] - national_load_mw))
    table["reference_p_mw"] = pd.to_numeric(table[f"{regime}_p_mw"], errors="coerce")
    table["reference_q_mvar"] = pd.to_numeric(table[f"{regime}_q_mvar"], errors="coerce")
    bus_by_id = {str(row.bus_id): int(index) for index, row in net.bus.iterrows()}
    candidates = net.bus[
        net.bus["in_service"].fillna(False)
        & net.bus["facility_name"].notna()
        & net.bus["vn_kv"].astype(float).ge(150.0)
    ].copy()
    candidates["pde_key"] = candidates["facility_name"].map(_pde_key)
    candidates["facility_penalty"] = candidates["facility_name"].astype(str).str.contains(
        "Central|Parque|Tração", case=False, regex=True
    ).astype(int)
    bus_indices: list[float] = []
    mapping_statuses: list[str] = []
    for row in table.to_dict("records"):
        key = _pde_key(row["pde_name"])
        override_id = PDIRT_DIRECT_CLIENT_BUS_OVERRIDES.get(key)
        if override_id in bus_by_id:
            bus_indices.append(float(bus_by_id[override_id]))
            mapping_statuses.append("CURATED_DIRECT_CLIENT_TO_PUBLIC_FACILITY")
            continue
        exact = candidates[candidates["pde_key"].eq(key)].copy()
        if exact.empty:
            bus_indices.append(float("nan"))
            mapping_statuses.append("UNMAPPED_PDIRT_PDE")
            continue
        # The delivery is placed at the lowest represented voltage of the
        # named RNT facility.  This is deterministic, but remains a bus-level
        # mapping assumption rather than a measured breaker assignment.
        selected = exact.sort_values(["facility_penalty", "vn_kv", "bus_id"]).iloc[0]
        bus_indices.append(float(selected.name))
        mapping_statuses.append("NORMALIZED_EXACT_PUBLIC_FACILITY_NAME_LOWEST_VOLTAGE")
    table["bus_index"] = bus_indices
    table["mapping_status"] = mapping_statuses
    table["mapped_to_pt60"] = table["bus_index"].notna()
    mapped = table["mapped_to_pt60"] & table["reference_p_mw"].gt(0.0)
    denominator = float(table.loc[mapped, "reference_p_mw"].sum())
    table["allocation_weight"] = 0.0
    table.loc[mapped, "allocation_weight"] = table.loc[mapped, "reference_p_mw"] / denominator
    return table, {
        "pdirt_reference_season": season,
        "pdirt_reference_regime": regime.upper(),
        "pdirt_reference_national_total_mw": regimes[regime],
        "pdirt_reference_mapped_pde_count": int(mapped.sum()),
        "pdirt_reference_unmapped_pde_count": int((~table["mapped_to_pt60"]).sum()),
        "pdirt_reference_mapped_weight_basis_mw": denominator,
        "pdirt_reference_source": "PDIRT 2025-2034 Annex 12, pages 323-324",
    }


def aggregate_facility_loads(snapshot: pd.DataFrame) -> pd.DataFrame:
    """Preserve missing observations; a numeric zero is a valid measurement."""
    snapshot = snapshot.copy()
    snapshot["codigo_subestacao"] = snapshot["codigo_subestacao"].astype(str)
    snapshot["energia"] = pd.to_numeric(snapshot["energia"], errors="raise")
    if not snapshot["energia"].dropna().map(math.isfinite).all():
        raise ValueError("Non-finite E-REDES energy observation")
    aggregation = {
        "substation_name": ("subestacao", "first"),
        "energy_kwh": ("energia", lambda values: values.sum(min_count=1)),
        "source_record_count": ("energia", "size"),
        "valid_observation_count": ("energia", "count"),
    }
    if "q_mvar" in snapshot:
        snapshot["q_mvar"] = pd.to_numeric(snapshot["q_mvar"], errors="raise")
        aggregation["supplied_q_mvar"] = ("q_mvar", lambda values: values.sum(min_count=1))
    grouped = snapshot.groupby("codigo_subestacao", as_index=False).agg(**aggregation)
    grouped["missing_observation_count"] = grouped.source_record_count - grouped.valid_observation_count
    grouped["observation_status"] = "OBSERVED"
    grouped.loc[grouped.missing_observation_count.gt(0), "observation_status"] = "PARTIAL_OBSERVATION"
    grouped.loc[grouped.valid_observation_count.eq(0), "observation_status"] = "MISSING"
    grouped["raw_p_mw"] = grouped.energy_kwh / 250.0
    return grouped


def residual_allocation_weights(net, reference, mode="PDIRT"):
    """Same eligible PDEs and residual P/Q totals under every spatial rule."""
    table = reference.copy()
    eligible = table.allocation_weight.gt(0)
    baseline = table.allocation_weight.copy()
    ratios = table.reference_q_mvar / table.reference_p_mw.replace(0, float("nan"))
    q_total_per_p = float((baseline * ratios).sum())
    table["capacity_weight_fallback"] = False
    if mode == "UNIFORM_PDE":
        table.loc[eligible, "allocation_weight"] = 1.0 / int(eligible.sum())
    elif mode == "CAPACITY_PDE":
        capacity = {}
        for bus in table.loc[eligible, "bus_index"].unique():
            capacity[bus] = float(net.trafo.loc[net.trafo.in_service & (net.trafo.hv_bus.eq(bus) | net.trafo.lv_bus.eq(bus)), "sn_mva"].sum())
        positive = pd.Series(capacity); fallback = float(positive[positive.gt(0)].median())
        if not math.isfinite(fallback):
            raise ValueError("No transformer capacity available for spatial comparison")
        counts = table.loc[eligible, "bus_index"].value_counts()
        basis = table.loc[eligible, "bus_index"].map(lambda bus: (capacity[bus] or fallback) / counts[bus])
        table.loc[eligible, "allocation_weight"] = basis / basis.sum()
        table.loc[eligible, "capacity_weight_fallback"] = table.loc[eligible, "bus_index"].map(capacity).eq(0)
    elif mode != "PDIRT":
        raise ValueError(f"Unknown residual spatial allocation: {mode}")
    q_weights = table.allocation_weight * ratios.fillna(0)
    if abs(q_weights.sum()) > 1e-12:
        q_weights *= q_total_per_p / q_weights.sum()
    elif abs(q_total_per_p) > 1e-12:
        raise ValueError("Cannot preserve total residual reactive demand")
    table["q_mvar_per_residual_mw"] = q_weights
    return table


def apply_load_profile(
    net: pp.pandapowerNet,
    case: dict[str, Any],
    observation: dict[str, Any],
    generators: pd.DataFrame,
    refresh: bool,
    snapshot_override: pd.DataFrame | None = None,
    storage_load_override: pd.DataFrame | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Replace the frozen load table with synchronized or season-matched E-REDES evidence."""
    profile_timestamp = pd.Timestamp(case["load_profile_timestamp_utc"])
    if snapshot_override is None:
        snapshot, metadata = eredes_load_snapshot(profile_timestamp, refresh)
    else:
        snapshot = snapshot_override.copy()
        metadata = {
            "source": "caller-supplied public substation load table",
            "timestamp_utc": profile_timestamp.isoformat(),
            "record_count": len(snapshot),
        }
    grouped = aggregate_facility_loads(snapshot)
    mode = str(case["load_profile_mode"])
    if mode == "SYNCHRONIZED_EREDES" or mode.startswith("SUPPLIED_PUBLIC_DATA"):
        profile_scale = 1.0
        reference_ren_load_mw = float(observation["load_mw"])
    else:
        reference_observation = ren_observation(profile_timestamp, refresh)
        reference_ren_load_mw = float(reference_observation["load_mw"])
        profile_scale = float(observation["load_mw"]) / reference_ren_load_mw
    grouped["applied_p_mw"] = grouped["raw_p_mw"].fillna(0.0) * profile_scale

    bus_lookup = _mapped_eredes_bus_lookup(net)
    grouped["bus_index"] = grouped["codigo_subestacao"].map(bus_lookup)
    grouped["mapped_to_pt60"] = grouped["bus_index"].notna()
    target_pf = 0.97
    tan_phi = math.tan(math.acos(target_pf))
    grouped["applied_q_mvar"] = (
        grouped["supplied_q_mvar"].fillna(grouped["raw_p_mw"].fillna(0.0) * tan_phi) * profile_scale
        if "supplied_q_mvar" in grouped.columns
        else grouped["applied_p_mw"] * tan_phi
    )
    net.load.drop(net.load.index, inplace=True)
    load_compensation = net.shunt["name"].fillna("").astype(str).str.startswith("SHUNT:")
    net.shunt.drop(net.shunt.index[load_compensation], inplace=True)

    audit_rows: list[dict[str, Any]] = []
    for row in grouped.to_dict("records"):
        mapped = bool(row["mapped_to_pt60"])
        bus_index = int(row["bus_index"]) if mapped else int(net.bus.index[net.bus["bus_id"].eq(UNMAPPED_RESIDUAL_BUS_ID)][0])
        status = (
            "DIRECT_SYNCHRONIZED_EREDES_ACTIVE_POWER"
            if mode == "SYNCHRONIZED_EREDES" and mapped
            else "CALLER_SUPPLIED_SEASONAL_PROXY_ACTIVE_POWER"
            if mode == "SUPPLIED_PUBLIC_DATA_SEASONAL_PROXY" and mapped
            else "CALLER_SUPPLIED_PUBLIC_ACTIVE_POWER"
            if mode.startswith("SUPPLIED_PUBLIC_DATA") and mapped
            else "SEASON_MATCHED_EREDES_ACTIVE_POWER_PROXY"
            if mapped
            else "UNMAPPED_EREDES_LOAD_RESIDUAL_PROXY"
        )
        if row["observation_status"] == "MISSING":
            status = "MISSING_OBSERVATION_NO_LOCAL_INJECTION"
        p_mw = float(row["applied_p_mw"])
        index = pp.create_load(
            net,
            bus_index,
            p_mw=p_mw,
            q_mvar=float(row["applied_q_mvar"]),
            name=f"LOAD:EREDES:{row['codigo_subestacao']}:{profile_timestamp.strftime('%Y%m%d%H%M')}",
            in_service=True,
        )
        net.load.loc[index, "observation_status"] = row["observation_status"]
        net.load.loc[index, "raw_observed_p_mw"] = row["raw_p_mw"]
        net.load.loc[index, "source_status"] = status
        net.load.loc[index, "reactive_power_status"] = (
            "CALLER_SUPPLIED_PUBLIC_REACTIVE_POWER"
            if "supplied_q_mvar" in grouped.columns
            else "SCENARIO_ASSUMPTION_FIXED_POWER_FACTOR"
        )
        audit_rows.append({
            "case_id": case["case_id"],
            "profile_timestamp_utc": profile_timestamp.isoformat(),
            "facility_code": row["codigo_subestacao"],
            "substation_name": row["substation_name"],
            "raw_p_mw": float(row["raw_p_mw"]),
            **{key: row[key] for key in ("observation_status", "source_record_count", "valid_observation_count", "missing_observation_count")},
            "profile_scale": profile_scale,
            "applied_p_mw": p_mw,
            "applied_q_mvar": float(row["applied_q_mvar"]),
            "mapped_to_pt60": mapped,
            "bus_id": str(net.bus.loc[bus_index, "bus_id"]),
            "source_status": status,
        })

    evidenced_load_mw = float(grouped["applied_p_mw"].sum())
    national_residual_mw = float(observation["load_mw"]) - evidenced_load_mw
    if national_residual_mw < -1e-6:
        raise RuntimeError(
            f"E-REDES profile exceeds REN national load by {-national_residual_mw:.3f} MW for {case['case_id']}"
        )
    pdirt, pdirt_summary = pdirt_residual_reference(net, case, float(observation["load_mw"]))
    spatial_mode = str(case.get("residual_allocation_mode", "PDIRT"))
    pdirt = residual_allocation_weights(net, pdirt, spatial_mode)
    pdirt_summary["residual_allocation_mode"] = spatial_mode
    pdirt_summary["capacity_weight_fallback_pde_count"] = int(pdirt.capacity_weight_fallback.sum())
    pdirt_summary["residual_total_q_mvar"] = national_residual_mw * float(pdirt.q_mvar_per_residual_mw.sum())
    if national_residual_mw > 1e-9:
        mapped_reference = pdirt[pdirt["allocation_weight"].gt(0.0)].copy()
        for row in mapped_reference.to_dict("records"):
            p_mw = national_residual_mw * float(row["allocation_weight"])
            q_to_p = float(row["q_mvar_per_residual_mw"]) / float(row["allocation_weight"])
            index = pp.create_load(
                net,
                int(row["bus_index"]),
                p_mw=p_mw,
                q_mvar=p_mw * q_to_p,
                name=f"LOAD:PDIRT-PDE-RESIDUAL:{case['case_id']}:{row['pde_name']}",
                in_service=True,
            )
            net.load.loc[index, "source_status"] = f"REN_MINUS_EREDES_ALLOCATED_BY_{spatial_mode}_WEIGHT"
            net.load.loc[index, "reactive_power_status"] = "PDIRT_ANNEX12_SEASON_REGIME_Q_TO_P_PROXY"
            audit_rows.append({
                "case_id": case["case_id"],
                "profile_timestamp_utc": profile_timestamp.isoformat(),
                "facility_code": "",
                "substation_name": row["pde_name"],
                "raw_p_mw": None,
                "profile_scale": None,
                "applied_p_mw": p_mw,
                "applied_q_mvar": p_mw * q_to_p,
                "mapped_to_pt60": True,
                "bus_id": str(net.bus.loc[int(row["bus_index"]), "bus_id"]),
                "source_status": f"REN_MINUS_EREDES_ALLOCATED_BY_{spatial_mode}_WEIGHT",
                "pdirt_mapping_status": row["mapping_status"],
                "pdirt_reference_p_mw": row["reference_p_mw"],
                "pdirt_reference_q_mvar": row["reference_q_mvar"],
                "pdirt_allocation_weight": row["allocation_weight"],
                "residual_allocation_mode": spatial_mode,
                "capacity_weight_fallback": row["capacity_weight_fallback"],
            })
    for row in pdirt[~pdirt["mapped_to_pt60"]].to_dict("records"):
        audit_rows.append({
            "case_id": case["case_id"],
            "profile_timestamp_utc": profile_timestamp.isoformat(),
            "facility_code": "",
            "substation_name": row["pde_name"],
            "raw_p_mw": None,
            "profile_scale": None,
            "applied_p_mw": 0.0,
            "applied_q_mvar": 0.0,
            "mapped_to_pt60": False,
            "bus_id": "",
            "source_status": "UNMAPPED_PDIRT_ANNEX12_PDE_REFERENCE_NOT_ALLOCATED",
            "pdirt_mapping_status": row["mapping_status"],
            "pdirt_reference_p_mw": row["reference_p_mw"],
            "pdirt_reference_q_mvar": row["reference_q_mvar"],
            "pdirt_allocation_weight": 0.0,
        })

    net_bus_by_id = {str(row.bus_id): int(index) for index, row in net.bus.iterrows()}
    available = available_asset_mask(generators, pd.Timestamp(case["timestamp_utc"]))
    assigned = generators["bus_id"].fillna("").astype(str).ne("") & available

    def add_storage_loads(
        target_mw: float,
        candidates: pd.DataFrame,
        prefix: str,
        mapped_status: str,
    ) -> tuple[float, float]:
        capacity_by_bus = candidates.groupby("bus_id")["nameplate_mw"].sum().clip(lower=0.0)
        mapped_total = 0.0
        supplied_by_bus = pd.Series(dtype=float)
        if storage_load_override is not None and not storage_load_override.empty:
            supplied = storage_load_override[
                storage_load_override["storage_type"].astype(str).str.upper().eq(prefix)
            ].copy()
            if not supplied.empty:
                supplied["p_mw"] = pd.to_numeric(supplied["p_mw"], errors="raise")
                supplied_by_bus = supplied.groupby("bus_id")["p_mw"].sum()
                unknown = sorted(set(supplied_by_bus.index.astype(str)) - set(capacity_by_bus.index.astype(str)))
                if unknown:
                    raise ValueError(f"storage_loads.csv {prefix} uses unsupported bus IDs: {unknown[:10]}")
                if (supplied_by_bus < -1e-9).any() or (supplied_by_bus > capacity_by_bus.reindex(supplied_by_bus.index) + 1e-8).any():
                    raise ValueError(f"storage_loads.csv {prefix} violates non-negative public asset capacity")
                if float(supplied_by_bus.sum()) > float(target_mw) + 1e-6:
                    raise ValueError(f"storage_loads.csv {prefix} exceeds the national public total")
                for bus_id, p_mw in supplied_by_bus.items():
                    index = pp.create_load(net, net_bus_by_id[str(bus_id)], p_mw=float(p_mw), q_mvar=0.0,
                                           name=f"LOAD:{prefix}:SUPPLIED:{case['case_id']}:{bus_id}", in_service=True)
                    net.load.loc[index, "source_status"] = "CALLER_SUPPLIED_PUBLIC_STORAGE_LOAD"
                    net.load.loc[index, "reactive_power_status"] = "ZERO_Q_STORAGE_CONSUMPTION_PROXY"
                    mapped_total += float(p_mw)
                    audit_rows.append({
                        "case_id": case["case_id"], "profile_timestamp_utc": profile_timestamp.isoformat(),
                        "facility_code": "", "substation_name": prefix, "raw_p_mw": float(p_mw),
                        "profile_scale": 1.0, "applied_p_mw": float(p_mw), "applied_q_mvar": 0.0,
                        "mapped_to_pt60": True, "bus_id": str(bus_id),
                        "source_status": "CALLER_SUPPLIED_PUBLIC_STORAGE_LOAD",
                    })
        headroom = capacity_by_bus.copy()
        if not supplied_by_bus.empty:
            headroom.loc[supplied_by_bus.index] -= supplied_by_bus
        remaining_target = max(0.0, float(target_mw) - mapped_total)
        allocated_target = min(remaining_target, float(headroom.sum()))
        if allocated_target > 0.0 and float(headroom.sum()) > 0.0:
            for bus_id, capacity_mw in headroom[headroom.gt(0.0)].items():
                p_mw = allocated_target * float(capacity_mw) / float(headroom.sum())
                index = pp.create_load(net, net_bus_by_id[str(bus_id)], p_mw=p_mw, q_mvar=0.0,
                                       name=f"LOAD:{prefix}:{case['case_id']}:{bus_id}", in_service=True)
                net.load.loc[index, "source_status"] = mapped_status
                net.load.loc[index, "reactive_power_status"] = "ZERO_Q_STORAGE_CONSUMPTION_PROXY"
                mapped_total += p_mw
                audit_rows.append({
                    "case_id": case["case_id"], "profile_timestamp_utc": profile_timestamp.isoformat(),
                    "facility_code": "", "substation_name": prefix, "raw_p_mw": None,
                    "profile_scale": None, "applied_p_mw": p_mw, "applied_q_mvar": 0.0,
                    "mapped_to_pt60": True, "bus_id": str(bus_id), "source_status": mapped_status,
                })
        residual_mw = max(0.0, float(target_mw) - mapped_total)
        if residual_mw > 1e-9:
            index = pp.create_load(net, net_bus_by_id[UNMAPPED_RESIDUAL_BUS_ID], p_mw=residual_mw, q_mvar=0.0,
                                   name=f"LOAD:{prefix}:UNMAPPED:{case['case_id']}", in_service=True)
            net.load.loc[index, "source_status"] = f"UNMAPPED_{prefix}_CONSUMPTION_RESIDUAL_PROXY"
            net.load.loc[index, "reactive_power_status"] = "ZERO_Q_STORAGE_CONSUMPTION_PROXY"
            audit_rows.append({
                "case_id": case["case_id"], "profile_timestamp_utc": profile_timestamp.isoformat(),
                "facility_code": "", "substation_name": f"Unmapped {prefix}", "raw_p_mw": None,
                "profile_scale": None, "applied_p_mw": residual_mw, "applied_q_mvar": 0.0,
                "mapped_to_pt60": False, "bus_id": UNMAPPED_RESIDUAL_BUS_ID,
                "source_status": f"UNMAPPED_{prefix}_CONSUMPTION_RESIDUAL_PROXY",
            })
        return mapped_total, residual_mw

    pumping_candidates = generators[
        assigned & generators["generation_source"].eq("hydro")
        & generators["bus_id"].astype(str).isin(PUMPED_STORAGE_BUS_IDS)
    ].copy()
    mapped_pumping_mw, pumping_residual_mw = add_storage_loads(
        float(observation["pumping_mw"]), pumping_candidates, "PUMPING",
        "REN_AGGREGATE_PUMPING_ALLOCATED_TO_PUBLIC_REVERSIBLE_HYDRO_BY_NAMEPLATE",
    )
    battery_candidates = generators[
        assigned & generators["generation_source"].eq("battery")
    ].copy()
    mapped_battery_consumption_mw, battery_consumption_residual_mw = add_storage_loads(
        float(observation["battery_consumption_mw"]), battery_candidates, "BATTERY_CHARGING",
        "REN_AGGREGATE_BATTERY_CONSUMPTION_ALLOCATED_TO_PUBLIC_BESS_BY_NAMEPLATE",
    )

    load_by_bus_p = net.load.groupby("bus")["p_mw"].sum()
    load_by_bus_q = net.load.groupby("bus")["q_mvar"].sum()
    target_tan = math.tan(math.acos(0.98))
    for bus_index, bus_p in load_by_bus_p.items():
        q_excess = max(0.0, float(load_by_bus_q.get(bus_index, 0.0)) - float(bus_p) * target_tan)
        if q_excess > 0.1:
            shunt_index = pp.create_shunt(
                net,
                int(bus_index),
                q_mvar=-min(q_excess, 15.0),
                p_mw=0.0,
                name=f"SHUNT:{net.bus.loc[int(bus_index), 'bus_id']}",
                in_service=True,
            )
            net.shunt.loc[shunt_index, "source_status"] = "ERSE_TARGET_POWER_FACTOR_SUBSTATION_COMPENSATION"

    matched = grouped["mapped_to_pt60"]
    summary = {
        "load_profile_mode": mode,
        "load_profile_timestamp_utc": profile_timestamp.isoformat(),
        "load_profile_source_record_count": int(metadata["record_count"]),
        "load_profile_valid_observation_count": int(grouped.valid_observation_count.sum()),
        "load_profile_missing_observation_count": int(grouped.missing_observation_count.sum()),
        "load_profile_missing_facility_count": int(grouped.observation_status.eq("MISSING").sum()),
        "load_profile_observed_zero_facility_count": int(grouped.raw_p_mw.eq(0).sum()),
        "load_profile_unique_substation_count": int(len(grouped)),
        "load_profile_matched_substation_count": int(matched.sum()),
        "load_profile_unmatched_substation_count": int((~matched).sum()),
        "load_profile_unmatched_mw": float(grouped.loc[~matched, "applied_p_mw"].sum()),
        "load_profile_negative_substation_count": int(grouped["applied_p_mw"].lt(0.0).sum()),
        "load_profile_negative_mw": float(grouped.loc[grouped["applied_p_mw"].lt(0.0), "applied_p_mw"].sum()),
        "load_profile_scale": profile_scale,
        "load_profile_reference_ren_load_mw": reference_ren_load_mw,
        "eredes_profile_load_mw": evidenced_load_mw,
        "eredes_profile_fraction_of_national_load": evidenced_load_mw / float(observation["load_mw"]),
        "ren_minus_eredes_rnt_load_proxy_mw": national_residual_mw,
        "ren_pumping_load_mw": float(observation["pumping_mw"]),
        "mapped_pumping_load_mw": mapped_pumping_mw,
        "unmapped_pumping_load_residual_mw": pumping_residual_mw,
        "ren_battery_consumption_mw": float(observation["battery_consumption_mw"]),
        "mapped_battery_consumption_mw": mapped_battery_consumption_mw,
        "unmapped_battery_consumption_residual_mw": battery_consumption_residual_mw,
        **pdirt_summary,
        "total_modeled_load_mw": float(net.load["p_mw"].sum()),
    }
    if not math.isclose(summary["total_modeled_load_mw"], float(observation["load_plus_storage_mw"]), abs_tol=0.11):
        raise AssertionError(f"Load profile does not reconcile to REN total for {case['case_id']}")
    return summary, audit_rows


def ren_observation(timestamp: pd.Timestamp, refresh: bool) -> dict[str, Any]:
    timestamp = utc_interval_start(timestamp)
    date = timestamp.tz_convert(SOURCE_ZONE).strftime("%Y-%m-%d")
    payload = get_json(
        REN_URL,
        # REN's OSB gateway currently returns OSB-382512 for some requests
        # when ``culture`` precedes ``date`` in the query string, although
        # both parameters are documented as required.  Preserve the working
        # order explicitly; it does not change the requested observation.
        {"date": date, "culture": "en-US"},
        RAW_REN / f"dispatch_{date}.json",
        refresh,
    )
    _, index = ren_source_index(payload, timestamp)
    values = {str(item["name"]): float(item["data"][index]) for item in payload["series"]}
    generation = {name: values.get(name, 0.0) for name in GENERATION_GROUPS}
    return {
        **source_time_metadata(timestamp),
        "source_url": f"{REN_URL}?date={date}&culture=en-US",
        "load_mw": values["Consumption"],
        "load_plus_storage_mw": values["Consumption + Storage"],
        "pumping_mw": values.get("Pumping", 0.0),
        "battery_consumption_mw": values.get("Consumption of Batteries", 0.0),
        "import_mw": values.get("Import", 0.0),
        "export_mw": values.get("Export", 0.0),
        "net_import_mw": values.get("Import", 0.0) - values.get("Export", 0.0),
        "generation_by_source_mw": generation,
        "generation_total_mw": sum(generation.values()),
    }


def ren_installed_capacity(benchmark: str, refresh: bool) -> dict[str, float]:
    year, month = benchmark.split("-")
    payload = get_json(
        REN_INSTALLED_CAPACITY_URL,
        {"culture": "en-US", "year": year, "month": month},
        RAW_REN / f"installed_capacity_{benchmark}.json",
        refresh,
    )
    if not isinstance(payload, list):
        raise RuntimeError(f"REN installed-capacity response is unavailable for {benchmark}: {payload}")
    values = {
        str(row["type"]): float(row["monthly_Accumulation"])
        for row in payload
        if row.get("monthly_Accumulation") is not None
    }
    mapping = {
        "Hydro": "HYDRO",
        "Solar": "SOLAR",
        "Wind": "WIND",
        "Natural Gas": "NATURAL_GAS",
        "Other Thermal": "OTHER_THERMAL",
        "Biomass": "BIOMASS",
        "Wave": "WAVE",
    }
    return {source: values.get(ren_type, 0.0) for source, ren_type in mapping.items()}


def weather_observation(timestamp: pd.Timestamp, refresh: bool) -> dict[str, Any]:
    date = timestamp.strftime("%Y-%m-%d")
    hour = int((timestamp + pd.Timedelta(minutes=30)).floor("h").hour)
    records: list[dict[str, object]] = []
    for name, (lat, lon) in WEATHER_POINTS.items():
        payload = get_json(
            WEATHER_URL,
            {
                "latitude": lat,
                "longitude": lon,
                "start_date": date,
                "end_date": date,
                "hourly": "temperature_2m,wind_speed_10m,shortwave_radiation",
                "timezone": "UTC",
            },
            RAW_WEATHER / f"open_meteo_{name.lower()}_{date}.json",
            refresh,
        )
        records.append({
            "location": name,
            "temperature_2m_c": float(payload["hourly"]["temperature_2m"][hour]),
            "wind_speed_10m_kmh": float(payload["hourly"]["wind_speed_10m"][hour]),
            "shortwave_radiation_w_m2": float(payload["hourly"]["shortwave_radiation"][hour]),
        })
    return {
        "source": "Open-Meteo historical/reanalysis API; contextual weather, not line-level DLR telemetry",
        "sample_hour_utc": f"{date}T{hour:02d}:00:00+00:00",
        "locations": records,
        "mean_temperature_2m_c": sum(float(row["temperature_2m_c"]) for row in records) / len(records),
        "mean_wind_speed_10m_kmh": sum(float(row["wind_speed_10m_kmh"]) for row in records) / len(records),
    }


def _allocate_with_cap(
    output: pd.DataFrame,
    mask: pd.Series,
    target_mw: float,
) -> float:
    """Allocate proportionally without exceeding mapped nameplate capacity."""
    capacity = output.loc[mask, "nameplate_mw"].fillna(0.0).clip(lower=0.0)
    headroom = (capacity - output.loc[mask, "scenario_p_mw"]).clip(lower=0.0)
    available_mw = float(headroom.sum())
    assigned_mw = min(float(target_mw), available_mw)
    if available_mw > 0.0 and assigned_mw > 0.0:
        output.loc[mask, "scenario_p_mw"] += headroom * assigned_mw / available_mw
    return float(target_mw) - assigned_mw


def available_asset_mask(generators: pd.DataFrame, timestamp: pd.Timestamp) -> pd.Series:
    commissioned = pd.to_datetime(generators["available_from_utc"], utc=True, errors="coerce")
    return commissioned.isna() | commissioned.le(timestamp)


def allocate_generation(
    generators: pd.DataFrame,
    targets: dict[str, float],
    timestamp: pd.Timestamp,
    asset_dispatch: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, list[dict[str, object]]]:
    output = generators.copy()
    output["scenario_p_mw"] = 0.0
    output["snapshot_asset_available"] = available_asset_mask(output, timestamp)
    assigned = output["bus_id"].fillna("").astype(str).ne("") & output["snapshot_asset_available"]
    output["dispatch_input_status"] = "ALLOCATED_FROM_PUBLIC_SOURCE_TOTAL"
    output["dispatch_mode"] = "ALLOCATED"
    output["supplied_p_mw"] = float("nan")
    fixed = pd.Series(False, index=output.index)
    if asset_dispatch is not None and not asset_dispatch.empty:
        required = {"generator_id", "p_mw"}
        missing = required - set(asset_dispatch.columns)
        if missing:
            raise ValueError(f"generator_dispatch.csv missing columns: {sorted(missing)}")
        supplied = asset_dispatch.copy()
        supplied["generator_id"] = supplied["generator_id"].astype(str)
        supplied["p_mw"] = pd.to_numeric(supplied["p_mw"], errors="raise")
        if not supplied["p_mw"].map(math.isfinite).all():
            raise ValueError("Supplied dispatch must be finite")
        supplied["dispatch_mode"] = supplied.get("dispatch_mode", pd.Series("FIXED", index=supplied.index)).fillna("FIXED")
        if not supplied["dispatch_mode"].isin(["FIXED", "SEED"]).all():
            raise ValueError("dispatch_mode must be FIXED or SEED")
        if supplied["generator_id"].duplicated().any():
            raise ValueError("generator_dispatch.csv contains duplicate generator_id values")
        unknown = sorted(set(supplied["generator_id"]) - set(output["generator_id"].astype(str)))
        if unknown:
            raise ValueError(f"Unknown generator_id values: {unknown[:10]}")
        supplied_by_id = supplied.set_index("generator_id")["p_mw"]
        matched = output["generator_id"].astype(str).isin(supplied_by_id.index)
        output.loc[matched, "scenario_p_mw"] = output.loc[matched, "generator_id"].astype(str).map(supplied_by_id)
        invalid = matched & (
            ~assigned
            | output["scenario_p_mw"].lt(-1e-9)
            | output["scenario_p_mw"].gt(output["nameplate_mw"].fillna(0.0) + 1e-8)
        )
        if invalid.any():
            ids = output.loc[invalid, "generator_id"].astype(str).tolist()[:10]
            raise ValueError(f"Supplied dispatch violates availability, mapping, or nameplate limits: {ids}")
        output.loc[matched, "supplied_p_mw"] = output.loc[matched, "scenario_p_mw"]
        output.loc[matched, "dispatch_mode"] = output.loc[matched, "generator_id"].astype(str).map(supplied.set_index("generator_id")["dispatch_mode"])
        fixed = output.dispatch_mode.eq("FIXED")
        output.loc[matched, "dispatch_input_status"] = "CALLER_SUPPLIED_ALLOCATION_SEED"
        output.loc[fixed, "dispatch_input_status"] = "CALLER_SUPPLIED_FIXED_UNIT_DISPATCH"
    residual_rows: list[dict[str, object]] = []
    for ren_name, source_names in GENERATION_GROUPS.items():
        mask = assigned & output["generation_source"].fillna("").astype(str).str.lower().isin(source_names)
        target = float(targets.get(ren_name, 0.0))
        seeded = float(output.loc[mask, "scenario_p_mw"].sum())
        if seeded > target + 1e-6:
            raise ValueError(
                f"Supplied unit dispatch for {ren_name} ({seeded:.3f} MW) exceeds "
                f"the public source total ({target:.3f} MW)"
            )
        remaining_target = max(0.0, target - seeded)
        adjustable = mask & ~fixed
        if ren_name in {"Hydro", "Natural Gas"}:
            threshold = 100.0 if ren_name == "Hydro" else 150.0
            utilization = 0.82 if ren_name == "Hydro" else 0.40
            large = adjustable & output["nameplate_mw"].ge(threshold)
            small = adjustable & ~large
            large_capacity = float(output.loc[large, "nameplate_mw"].sum())
            preferred_large_target = min(remaining_target, max(0.0, large_capacity * utilization - float(output.loc[large, "scenario_p_mw"].sum())))
            remaining = _allocate_with_cap(output, large, preferred_large_target)
            if remaining > 1e-9:
                raise AssertionError(f"Unexpected preferred-allocation residual for {ren_name}: {remaining}")
            remaining = target - float(output.loc[mask, "scenario_p_mw"].sum())
            remaining = _allocate_with_cap(output, small, remaining)
            if remaining > 1e-9:
                large_headroom = large & output["scenario_p_mw"].lt(output["nameplate_mw"] - 1e-9)
                remaining = _allocate_with_cap(output, large_headroom, remaining)
        else:
            remaining = _allocate_with_cap(output, adjustable, remaining_target)
        mapped_capacity = float(output.loc[mask, "nameplate_mw"].fillna(0.0).clip(lower=0.0).sum())
        mapped_input = float(output.loc[mask, "scenario_p_mw"].sum())
        residual = max(0.0, target - mapped_input)
        residual_rows.append({
            "generation_source": ren_name,
            "ren_target_mw": target,
            "mapped_asset_count": int(mask.sum()),
            "mapped_nameplate_capacity_mw": mapped_capacity,
            "mapped_asset_input_mw": mapped_input,
            "unmapped_residual_mw": residual,
            "residual_bus_id": UNMAPPED_RESIDUAL_BUS_ID if residual > 1e-9 else "",
            "residual_status": "UNMAPPED_NATIONAL_RESIDUAL_PROXY" if residual > 1e-9 else "NONE",
        })
    violations = assigned & output["scenario_p_mw"].gt(output["nameplate_mw"].fillna(0.0) + 1e-8)
    if violations.any():
        ids = output.loc[violations, "generator_id"].astype(str).tolist()[:10]
        raise AssertionError(f"Capacity-constrained allocation failed for: {ids}")
    output["allocation_increment_mw"] = output.scenario_p_mw - output.supplied_p_mw.fillna(0.0)
    if not output.loc[fixed, "allocation_increment_mw"].eq(0.0).all():
        raise AssertionError("Fixed dispatch changed")
    return output, residual_rows


def apply_generation_inputs(
    net: pp.pandapowerNet,
    generators: pd.DataFrame,
    observation: dict[str, Any],
    timestamp: pd.Timestamp,
    asset_dispatch: pd.DataFrame | None = None,
    legacy_bus_aggregation: bool = False,
) -> tuple[dict[str, float], pd.DataFrame, list[dict[str, object]]]:
    allocated, residual_rows = allocate_generation(
        generators, observation["generation_by_source_mw"], timestamp, asset_dispatch=asset_dispatch
    )
    p_by_id = allocated.set_index("generator_id")["scenario_p_mw"].to_dict()
    for index, row in net.sgen.iterrows():
        net.sgen.loc[index, "p_mw"] = float(p_by_id.get(str(row["name"]), 0.0))
        net.sgen.loc[index, "scaling"] = 1.0
    p_by_bus = allocated.groupby("bus_id")["scenario_p_mw"].sum().to_dict()
    standalone_by_bus = net.sgen.loc[net.sgen.in_service].groupby("bus")["p_mw"].sum()
    for index, row in net.gen.iterrows():
        bus_id = str(net.bus.loc[int(row["bus"]), "bus_id"])
        standalone = 0.0 if legacy_bus_aggregation else float(standalone_by_bus.get(int(row["bus"]), 0.0))
        net.gen.loc[index, "p_mw"] = float(p_by_bus.get(bus_id, 0.0)) - standalone
        net.gen.loc[index, "scaling"] = 1.0
    residual_bus_matches = net.bus.index[net.bus["bus_id"].eq(UNMAPPED_RESIDUAL_BUS_ID)]
    if len(residual_bus_matches) != 1 or not bool(net.bus.loc[int(residual_bus_matches[0]), "in_service"]):
        raise AssertionError(f"Unmapped residual bus is unavailable: {UNMAPPED_RESIDUAL_BUS_ID}")
    residual_bus = int(residual_bus_matches[0])
    for residual in residual_rows:
        residual_mw = float(residual["unmapped_residual_mw"])
        if residual_mw <= 1e-9:
            continue
        index = pp.create_sgen(
            net,
            residual_bus,
            p_mw=residual_mw,
            q_mvar=0.0,
            name=f"UNMAPPED_RESIDUAL:{residual['generation_source']}",
            type="unmapped_generation_residual",
        )
        net.sgen.loc[index, "generator_id"] = f"UNMAPPED_RESIDUAL:{residual['generation_source']}"
        net.sgen.loc[index, "generation_source"] = residual["generation_source"]
        net.sgen.loc[index, "source_status"] = "UNMAPPED_NATIONAL_RESIDUAL_PROXY"
        net.sgen.loc[index, "dispatch_status"] = "REN_SOURCE_TOTAL_MINUS_MAPPED_NAMEPLATE_CAPACITY"
    mapped_generation = float(allocated.loc[allocated.bus_id.fillna("").astype(str).ne(""), "scenario_p_mw"].sum())
    residual_generation = sum(float(row["unmapped_residual_mw"]) for row in residual_rows)
    summary = {
        "allocated_generation_mw": mapped_generation,
        "mapped_asset_generation_mw": mapped_generation,
        "unmapped_generation_residual_mw": residual_generation,
        "total_modeled_generation_mw": mapped_generation + residual_generation,
        "generator_capacity_violations": 0,
    }
    allocated["pandapower_element"] = "NONE"
    allocated["pandapower_index"] = pd.NA
    sgen_by_id = {str(row["name"]): i for i, row in net.sgen.loc[net.sgen.in_service].iterrows()}
    gen_by_bus = {str(net.bus.loc[int(row["bus"]), "bus_id"]): i for i, row in net.gen.loc[net.gen.in_service].iterrows()}
    for i, row in allocated.iterrows():
        if not bool(row["snapshot_asset_available"]):
            continue
        if str(row.generator_id) in sgen_by_id:
            allocated.loc[i, ["pandapower_element", "pandapower_index"]] = ["sgen", sgen_by_id[str(row.generator_id)]]
        elif str(row.bus_id) in gen_by_bus:
            allocated.loc[i, ["pandapower_element", "pandapower_index"]] = ["gen", gen_by_bus[str(row.bus_id)]]
    summary["fixed_dispatch_asset_count"] = int(allocated.dispatch_mode.eq("FIXED").sum())
    summary["seed_dispatch_asset_count"] = int(allocated.dispatch_mode.eq("SEED").sum())
    return summary, allocated, residual_rows


def apply_topology_and_rating(net: pp.pandapowerNet, case: dict[str, Any]) -> None:
    factor = float(case["rating_factor_relative_to_static_summer"])
    net.line["max_i_ka"] = net.line["max_i_ka"] / 1.15 * factor
    new_line_ids = {"LINE:009732", "LINE:009814"}
    new_lines = net.line["line_id"].astype(str).isin(new_line_ids)
    net.line.loc[new_lines, "in_service"] = bool(case["new_interconnector_in_service"])
    if case["new_interconnector_in_service"]:
        endpoint_indices = set(net.line.loc[new_lines, "from_bus"].astype(int)) | set(net.line.loc[new_lines, "to_bus"].astype(int))
        net.bus.loc[list(endpoint_indices), "in_service"] = True
        boundary_bus = int(net.bus.index[net.bus["bus_id"].eq("BUS:JUNCTION:400:04027")][0])
        pp.create_ext_grid(
            net,
            boundary_bus,
            vm_pu=1.0,
            va_degree=0.0,
            name="EXT_GRID:Ponte de Lima - Fontefría (Minho-Galicia)",
        )


def apply_line_parameter_overrides(
    net: pp.pandapowerNet, overrides: pd.DataFrame | None, seasonal_factor: float
) -> None:
    """Apply source-backed line values supplied in the public input package."""
    if overrides is None or overrides.empty:
        return
    if "line_id" not in overrides or overrides["line_id"].astype(str).duplicated().any():
        raise ValueError("line_overrides.csv requires unique line_id values")
    line_index = net.line.reset_index().set_index(net.line["line_id"].astype(str))["index"].to_dict()
    unknown = sorted(set(overrides["line_id"].astype(str)) - set(line_index))
    if unknown:
        raise ValueError(f"line_overrides.csv contains unknown line IDs: {unknown[:10]}")
    column_map = {
        "r_ohm_per_km": "r_ohm_per_km",
        "x_ohm_per_km": "x_ohm_per_km",
        "c_nf_per_km": "c_nf_per_km",
    }
    for row in overrides.to_dict("records"):
        index = int(line_index[str(row["line_id"])])
        changed = False
        for source, destination in column_map.items():
            if source in row and pd.notna(row[source]):
                value = float(row[source])
                if value < 0.0:
                    raise ValueError(f"{source} must be non-negative for {row['line_id']}")
                net.line.loc[index, destination] = value
                changed = True
        if "static_summer_max_i_ka" in row and pd.notna(row["static_summer_max_i_ka"]):
            value = float(row["static_summer_max_i_ka"])
            if value <= 0.0:
                raise ValueError(f"static_summer_max_i_ka must be positive for {row['line_id']}")
            net.line.loc[index, "max_i_ka"] = value * seasonal_factor
            changed = True
        if not changed:
            raise ValueError(f"No parameter value supplied for {row['line_id']}")
        net.line.loc[index, "parameter_status"] = "CALLER_SUPPLIED_SOURCE_BACKED_OVERRIDE"
        net.line.loc[index, "parameter_source_url"] = str(row.get("source_url", ""))


def regularize_cross_border_boundary(
    net: pp.pandapowerNet,
    preliminary_net_import_mw: float,
    allocation_mode: str = "VOLTAGE_X_CIRCUITS",
) -> dict[str, Any]:
    """Replace equal-angle multi-slack buses with an aggregate equivalent.

    The preliminary total is produced by the model itself, not taken from REN.
    Its spatial distribution is then assigned by voltage-times-circuit-count
    weights.  One ext_grid remains as the angle reference and absorbs only the
    nonlinear loss mismatch.  This removes artificial fixed-angle loop flows
    while keeping the aggregate interconnector observation held out.
    """
    if len(net.ext_grid) <= 1:
        return {
            "boundary_mode": "SINGLE_REFERENCE_ALREADY_PRESENT",
            "preliminary_equal_angle_net_import_mw": preliminary_net_import_mw,
            "boundary_equivalent_fixed_injection_mw": 0.0,
        }
    config = read_json(PROJECT / "config" / "model_config.json")
    circuits_by_bus = {
        str(row["bus_id"]): int(row.get("circuit_count", 1))
        for row in config.get("cross_border_interconnections", [])
    }
    allocation_mode = str(allocation_mode).upper()
    if allocation_mode not in {"VOLTAGE_X_CIRCUITS", "VOLTAGE", "UNIFORM"}:
        raise ValueError(f"Unsupported boundary allocation mode: {allocation_mode}")
    records: list[dict[str, Any]] = []
    for index, row in net.ext_grid.iterrows():
        bus_index = int(row["bus"])
        bus_id = str(net.bus.loc[bus_index, "bus_id"])
        voltage_kv = float(net.bus.loc[bus_index, "vn_kv"])
        circuit_count = circuits_by_bus.get(bus_id, 1)
        if allocation_mode == "VOLTAGE_X_CIRCUITS":
            weight_basis = voltage_kv * circuit_count
            weight_label = "BUS_VOLTAGE_KV_TIMES_PUBLIC_CIRCUIT_COUNT"
        elif allocation_mode == "VOLTAGE":
            weight_basis = voltage_kv
            weight_label = "BUS_VOLTAGE_KV"
        else:
            weight_basis = 1.0
            weight_label = "UNIFORM_ACROSS_PUBLIC_BOUNDARY_BUSES"
        records.append({
            "index": int(index),
            "bus": bus_index,
            "bus_id": bus_id,
            "name": str(row["name"]),
            "weight_basis": weight_basis,
        })
    total_weight = sum(float(row["weight_basis"]) for row in records)
    reference = max(records, key=lambda row: (float(row["weight_basis"]), row["bus_id"]))
    fixed_total = 0.0
    for row in records:
        if row["index"] == reference["index"]:
            continue
        share = float(row["weight_basis"]) / total_weight
        p_mw = preliminary_net_import_mw * share
        index = pp.create_sgen(
            net,
            int(row["bus"]),
            p_mw=p_mw,
            q_mvar=0.0,
            name=f"BOUNDARY_EQ:{row['name']}",
            type="cross_border_aggregate_equivalent",
        )
        net.sgen.loc[index, "source_status"] = "MODEL_DERIVED_TOTAL_CAPACITY_WEIGHTED_BOUNDARY_EQUIVALENT"
        net.sgen.loc[index, "boundary_bus_id"] = row["bus_id"]
        fixed_total += p_mw
    net.ext_grid.drop(index=[row["index"] for row in records if row["index"] != reference["index"]], inplace=True)
    net.ext_grid.loc[reference["index"], "source_status"] = "SINGLE_ANGLE_REFERENCE_FOR_AGGREGATE_BOUNDARY_EQUIVALENT"
    return {
        "boundary_mode": "SINGLE_REFERENCE_PLUS_MODEL_DERIVED_CAPACITY_WEIGHTED_INJECTIONS",
        "boundary_reference_bus_id": reference["bus_id"],
        "preliminary_equal_angle_net_import_mw": preliminary_net_import_mw,
        "boundary_equivalent_fixed_injection_mw": fixed_total,
        "boundary_weight_basis": weight_label,
        "boundary_observation_used_as_input": False,
    }


def _esios_indicator_value(indicator_id: int, timestamp: pd.Timestamp, refresh: bool) -> tuple[float | None, str, str]:
    token = os.environ.get("ESIOS_API_TOKEN", "").strip()
    source_url = f"{ESIOS_API_BASE}/{indicator_id}"
    if not token:
        return None, "TOKEN_NOT_CONFIGURED", source_url
    cache = RAW_CROSS_BORDER / f"esios_{indicator_id}_{timestamp.strftime('%Y-%m-%d_%H%M')}.json"
    try:
        if cache.exists() and not refresh:
            payload = read_json(cache)
        else:
            response = requests.get(
                source_url,
                params={
                    "start_date": timestamp.isoformat(),
                    "end_date": (timestamp + pd.Timedelta(minutes=15)).isoformat(),
                },
                headers={
                    "Accept": "application/json; application/vnd.esios-api-v1+json",
                    "Content-Type": "application/json",
                    "x-api-key": token,
                    "User-Agent": "PT60 public-evidence validation/0.2",
                },
                timeout=120,
            )
            response.raise_for_status()
            payload = response.json()
            write_json(cache, payload)
        values = payload.get("indicator", {}).get("values", [])
        candidates = []
        for row in values:
            dt_value = row.get("datetime_utc") or row.get("datetime")
            if dt_value is None or row.get("value") is None:
                continue
            candidates.append((abs(pd.Timestamp(dt_value) - timestamp), float(row["value"])))
        if not candidates:
            return None, "NO_VALUE_FOR_TIMESTAMP", source_url
        return min(candidates, key=lambda item: item[0])[1], "AVAILABLE", source_url
    except Exception as exc:
        return None, f"FETCH_ERROR:{type(exc).__name__}", source_url


def _duration_minutes(value: str) -> int:
    if value == "PT15M":
        return 15
    if value == "PT30M":
        return 30
    if value == "PT60M":
        return 60
    raise ValueError(f"Unsupported ENTSO-E resolution: {value}")


def _entsoe_physical_flow(
    out_domain: str,
    in_domain: str,
    timestamp: pd.Timestamp,
    direction_label: str,
    refresh: bool,
) -> tuple[float | None, str, str]:
    token = os.environ.get("ENTSOE_SECURITY_TOKEN", "").strip()
    if not token:
        return None, "TOKEN_NOT_CONFIGURED", ENTSOE_API_URL
    cache = RAW_CROSS_BORDER / f"entsoe_{direction_label}_{timestamp.strftime('%Y-%m-%d_%H%M')}.xml"
    try:
        if cache.exists() and not refresh:
            raw = cache.read_bytes()
        else:
            start = timestamp.floor("h")
            end = start + pd.Timedelta(hours=2)
            response = requests.get(
                ENTSOE_API_URL,
                params={
                    "securityToken": token,
                    "documentType": "A11",
                    "out_Domain": out_domain,
                    "in_Domain": in_domain,
                    "periodStart": start.strftime("%Y%m%d%H%M"),
                    "periodEnd": end.strftime("%Y%m%d%H%M"),
                },
                headers={"User-Agent": "PT60 public-evidence validation/0.2"},
                timeout=120,
            )
            response.raise_for_status()
            raw = response.content
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(raw)
        root = ET.fromstring(raw)
        if root.tag.endswith("Acknowledgement_MarketDocument"):
            reason = next((item.text for item in root.iter() if item.tag.endswith("text") and item.text), "no data")
            return None, f"NO_DATA:{reason}", ENTSOE_API_URL
        candidates: list[tuple[pd.Timedelta, float]] = []
        for period in [item for item in root.iter() if item.tag.endswith("Period")]:
            interval = next((item for item in period.iter() if item.tag.endswith("timeInterval")), None)
            start_text = next((item.text for item in interval or [] if item.tag.endswith("start")), None)
            resolution = next((item.text for item in period if item.tag.endswith("resolution")), None)
            if start_text is None or resolution is None:
                continue
            period_start = pd.Timestamp(start_text)
            minutes = _duration_minutes(resolution)
            for point in [item for item in period if item.tag.endswith("Point")]:
                position_text = next((item.text for item in point if item.tag.endswith("position")), None)
                quantity_text = next((item.text for item in point if item.tag.endswith("quantity")), None)
                if position_text is None or quantity_text is None:
                    continue
                point_time = period_start + pd.Timedelta(minutes=minutes * (int(position_text) - 1))
                candidates.append((abs(point_time - timestamp), float(quantity_text)))
        if not candidates:
            return None, "NO_VALUE_FOR_TIMESTAMP", ENTSOE_API_URL
        return min(candidates, key=lambda item: item[0])[1], "AVAILABLE", ENTSOE_API_URL
    except Exception as exc:
        return None, f"FETCH_ERROR:{type(exc).__name__}", ENTSOE_API_URL


def cross_border_evidence(
    case_id: str,
    timestamp: pd.Timestamp,
    ren: dict[str, Any],
    model_net_import_mw: float,
    refresh: bool,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [{
        "case_id": case_id,
        "timestamp_utc": timestamp.isoformat(),
        "source": "PT60_ACPF",
        "import_mw": max(0.0, model_net_import_mw),
        "export_mw": max(0.0, -model_net_import_mw),
        "net_import_mw": model_net_import_mw,
        "availability_status": "AVAILABLE",
        "independent_of_ren": False,
        "source_url": "local solved AC model",
    }, {
        "case_id": case_id,
        "timestamp_utc": timestamp.isoformat(),
        "source": "REN_DATAHUB",
        "import_mw": float(ren["import_mw"]),
        "export_mw": float(ren["export_mw"]),
        "net_import_mw": float(ren["net_import_mw"]),
        "availability_status": "AVAILABLE_HELD_OUT",
        "independent_of_ren": False,
        "source_url": ren["source_url"],
    }]
    esios_spain_import, status_557, url_557 = _esios_indicator_value(557, timestamp, refresh)
    esios_spain_export, status_561, url_561 = _esios_indicator_value(561, timestamp, refresh)
    esios_available = esios_spain_import is not None and esios_spain_export is not None
    rows.append({
        "case_id": case_id,
        "timestamp_utc": timestamp.isoformat(),
        "source": "REE_ESIOS_557_561",
        "import_mw": esios_spain_export if esios_available else None,
        "export_mw": esios_spain_import if esios_available else None,
        "net_import_mw": esios_spain_export - esios_spain_import if esios_available else None,
        "availability_status": "AVAILABLE" if esios_available else f"557={status_557};561={status_561}",
        "independent_of_ren": True,
        "source_url": f"{url_557};{url_561}",
    })
    entsoe_import, import_status, entsoe_url = _entsoe_physical_flow(
        ES_BIDDING_ZONE, PT_BIDDING_ZONE, timestamp, "ES_to_PT", refresh
    )
    entsoe_export, export_status, _ = _entsoe_physical_flow(
        PT_BIDDING_ZONE, ES_BIDDING_ZONE, timestamp, "PT_to_ES", refresh
    )
    entsoe_available = entsoe_import is not None and entsoe_export is not None
    rows.append({
        "case_id": case_id,
        "timestamp_utc": timestamp.isoformat(),
        "source": "ENTSOE_A11_PHYSICAL_FLOW",
        "import_mw": entsoe_import,
        "export_mw": entsoe_export,
        "net_import_mw": entsoe_import - entsoe_export if entsoe_available else None,
        "availability_status": "AVAILABLE" if entsoe_available else f"ES_TO_PT={import_status};PT_TO_ES={export_status}",
        "independent_of_ren": True,
        "source_url": entsoe_url,
    })
    for row in rows:
        value = row.get("net_import_mw")
        row["error_vs_ren_mw"] = float(value) - float(ren["net_import_mw"]) if value is not None else None
        row["absolute_error_vs_ren_mw"] = abs(float(row["error_vs_ren_mw"])) if row["error_vs_ren_mw"] is not None else None
    return rows


def run_case(
    case: dict[str, Any],
    generators: pd.DataFrame,
    refresh: bool,
    save_solved: bool = True,
    *,
    observation_override: dict[str, Any] | None = None,
    load_snapshot_override: pd.DataFrame | None = None,
    weather_override: dict[str, Any] | None = None,
    rnt_loss_override: dict[str, Any] | None = None,
    generator_dispatch_override: pd.DataFrame | None = None,
    storage_load_override: pd.DataFrame | None = None,
    line_parameter_overrides: pd.DataFrame | None = None,
    independent_cross_border_override: dict[str, Any] | None = None,
    model_input: Path = MODEL_INPUT,
    output_dir: Path = OUTPUT,
    diagnostic_line_ids: tuple[str, ...] = (),
) -> tuple[
    dict[str, Any],
    list[dict[str, object]],
    list[dict[str, object]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    timestamp = pd.Timestamp(case["timestamp_utc"])
    observation = observation_override or ren_observation(timestamp, refresh)
    weather = weather_override or weather_observation(timestamp, refresh)
    net = pp.from_json(model_input)
    apply_topology_and_rating(net, case)
    if net.get("pt60_snapshot_controls", False):
        from rebuild_snapshot_controls import rebuild_controls
        rebuild_controls(net, generators, timestamp)
    if case.get("exclude_unknown_asset_dates", False):
        generators = generators.copy()
        generators.loc[generators.available_from_utc.isna(), "available_from_utc"] = "2100-01-01T00:00:00Z"
        if net.get("pt60_snapshot_controls", False):
            rebuild_controls(net, generators, timestamp)
    apply_line_parameter_overrides(
        net, line_parameter_overrides, float(case["rating_factor_relative_to_static_summer"])
    )
    load_summary, load_audit_rows = apply_load_profile(
        net, case, observation, generators, refresh,
        snapshot_override=load_snapshot_override,
        storage_load_override=storage_load_override,
    )
    allocation, allocated, residual_rows = apply_generation_inputs(
        net, generators, observation, timestamp, asset_dispatch=generator_dispatch_override,
        legacy_bus_aggregation=bool(case.get("legacy_bus_aggregation", False)),
    )
    if case.get("disable_load_compensation", False):
        mask = net.shunt.name.fillna("").str.startswith("SHUNT:")
        net.shunt.loc[mask, "in_service"] = False
    if not math.isclose(
        float(allocation["total_modeled_generation_mw"]),
        float(observation["generation_total_mw"]),
        abs_tol=1e-6,
    ):
        raise AssertionError("Mapped assets plus explicit residuals do not preserve the REN generation total")
    active_generation = sum(
        float((table.loc[table.in_service, "p_mw"] * table.loc[table.in_service, "scaling"]).sum())
        for table in (net.gen, net.sgen)
    )
    if not case.get("legacy_bus_aggregation", False) and not math.isclose(active_generation, float(observation["generation_total_mw"]), abs_tol=1e-5):
        raise AssertionError("Active pandapower injections do not equal the allocated generation total")
    pp.runpp(net, algorithm="nr", init="dc", calculate_voltage_angles=True, max_iteration=50, tolerance_mva=1e-6, enforce_q_lims=True, numba=False)
    preliminary_net_import_mw = float(net.res_ext_grid.p_mw.sum())
    boundary_summary = regularize_cross_border_boundary(
        net,
        preliminary_net_import_mw,
        str(case.get("boundary_allocation_mode", "VOLTAGE_X_CIRCUITS")),
    )
    pp.runpp(net, algorithm="nr", init="results", calculate_voltage_angles=True, max_iteration=50, tolerance_mva=1e-6, enforce_q_lims=True, numba=False)
    infer_discrete_tap_positions(net, 0.985, 1.015)
    boundary_sgen_mask = net.sgen.get("type", pd.Series(index=net.sgen.index, dtype=object)).eq("cross_border_aggregate_equivalent")
    boundary_injections = pd.concat([
        net.res_ext_grid["p_mw"].astype(float),
        net.res_sgen.loc[boundary_sgen_mask, "p_mw"].astype(float),
    ], ignore_index=True)
    model_net_import = float(boundary_injections.sum())
    boundary_import_mw = float(boundary_injections.clip(lower=0.0).sum())
    boundary_export_mw = float((-boundary_injections.clip(upper=0.0)).sum())
    boundary_gross_exchange_mw = boundary_import_mw + boundary_export_mw
    boundary_gross_to_abs_net_ratio = boundary_gross_exchange_mw / max(abs(model_net_import), 1e-9)
    observed_net_import = float(observation["net_import_mw"])
    observed_system_load_mw = float(observation["load_plus_storage_mw"])
    observed_balance_residual = float(observation["generation_total_mw"]) + observed_net_import - observed_system_load_mw
    rnt_loss = rnt_loss_override or ren_rnt_loss_benchmark(str(case["rnt_loss_benchmark_date"]), refresh)
    active_line_results = net.res_line.loc[net.line.in_service.fillna(False)].copy()
    lines_over_100 = int(active_line_results["loading_percent"].gt(100.0).sum())
    line_voltage_kv = net.line["from_bus"].astype(int).map(net.bus["vn_kv"].astype(float))
    rnt_line_mask = net.line.in_service.fillna(False) & line_voltage_kv.ge(150.0)
    rnd_at_line_mask = net.line.in_service.fillna(False) & line_voltage_kv.lt(150.0)
    rnt_line_losses_mw = float(net.res_line.loc[rnt_line_mask, "pl_mw"].sum())
    rnd_at_line_losses_mw = float(net.res_line.loc[rnd_at_line_mask, "pl_mw"].sum())
    transformer_losses_mw = float(net.res_trafo["pl_mw"].sum())
    rnt_scope_losses_mw = rnt_line_losses_mw + transformer_losses_mw
    total_pt60_losses_mw = rnt_scope_losses_mw + rnd_at_line_losses_mw
    observed_load_mw = observed_system_load_mw
    result = {
        **case,
        "generated_at": utc_now(),
        "converged": bool(net.converged),
        "active_generation_injection_error_mw": active_generation - float(observation["generation_total_mw"]),
        "active_buses": int(net.bus.in_service.sum()),
        "active_lines": int(net.line.in_service.sum()),
        "load_spatial_status": case["load_profile_mode"],
        "node_level_validation_status": (
            "PARTIAL_SYNCHRONIZED_EREDES_VALIDATION_RNT_RESIDUAL_REMAINS_PROXY"
            if case["load_profile_mode"] == "SYNCHRONIZED_EREDES"
            else "CALLER_SUPPLIED_PUBLIC_LOAD_MAPPING_RNT_RESIDUAL_REMAINS_PROXY"
            if str(case["load_profile_mode"]).startswith("SUPPLIED_PUBLIC_DATA")
            else "SEASON_MATCHED_PROXY_NOT_SYNCHRONIZED_NODE_VALIDATION"
        ),
        **load_summary,
        **allocation,
        "observed_consumption_mw": float(observation["load_mw"]),
        "observed_load_mw": observed_system_load_mw,
        "observed_system_load_including_storage_mw": observed_system_load_mw,
        "observed_generation_total_mw": float(observation["generation_total_mw"]),
        "observed_net_import_mw": observed_net_import,
        "observed_generation_plus_import_minus_load_mw": observed_balance_residual,
        "model_net_import_mw": model_net_import,
        "model_boundary_gross_import_mw": boundary_import_mw,
        "model_boundary_gross_export_mw": boundary_export_mw,
        "model_boundary_gross_exchange_mw": boundary_gross_exchange_mw,
        "model_boundary_gross_to_abs_net_ratio": boundary_gross_to_abs_net_ratio,
        **boundary_summary,
        "net_import_error_mw": model_net_import - observed_net_import,
        "net_import_absolute_error_mw": abs(model_net_import - observed_net_import),
        "net_import_error_percent_of_load": 100.0 * (model_net_import - observed_net_import) / observed_system_load_mw,
        "model_total_pt60_losses_mw": total_pt60_losses_mw,
        "model_total_pt60_losses_percent_of_load": 100.0 * total_pt60_losses_mw / observed_load_mw,
        "model_rnt_line_losses_mw": rnt_line_losses_mw,
        "model_rnd_at_line_losses_mw": rnd_at_line_losses_mw,
        "model_transformer_losses_mw": transformer_losses_mw,
        "model_rnt_scope_losses_mw": rnt_scope_losses_mw,
        "model_rnt_scope_losses_percent_of_load": 100.0 * rnt_scope_losses_mw / observed_load_mw,
        # Backward-compatible aliases now explicitly point to the complete
        # PT60 model scope, not the REN RNT comparison scope.
        "model_losses_mw": total_pt60_losses_mw,
        "model_losses_percent_of_load": 100.0 * total_pt60_losses_mw / observed_load_mw,
        "ren_rnt_monthly_loss_percent": float(rnt_loss["loss_percent"]),
        "rnt_scope_loss_percent_error_vs_ren_rnt_monthly": 100.0 * rnt_scope_losses_mw / observed_load_mw - float(rnt_loss["loss_percent"]),
        "loss_percent_error_vs_ren_rnt_monthly": 100.0 * rnt_scope_losses_mw / observed_load_mw - float(rnt_loss["loss_percent"]),
        "vm_pu_min": float(net.res_bus.vm_pu.min()),
        "vm_pu_max": float(net.res_bus.vm_pu.max()),
        "maximum_line_loading_percent": float(net.res_line.loading_percent.max()),
        "lines_over_100_percent": lines_over_100,
        "maximum_transformer_loading_percent": float(net.res_trafo.loading_percent.max()),
        "weather": weather,
        "ren_observation": observation,
        "validation_design": "LOAD_AND_SOURCE_DISPATCH_INPUTS; AGGREGATE_NET_IMPORT_HELD_OUT",
    }
    if case.get("spatial_result_path"):
        path = Path(case["spatial_result_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, bus_id=net.bus.bus_id.astype(str).to_numpy(dtype=str),
            vm_pu=net.res_bus.vm_pu.to_numpy(), line_id=net.line.line_id.astype(str).to_numpy(dtype=str),
            loading_percent=net.res_line.loading_percent.to_numpy())
    if save_solved:
        output_dir.mkdir(parents=True, exist_ok=True)
        pp.to_json(net, output_dir / f"{case['case_id']}_solved.json")
        allocated.to_csv(output_dir / "asset_allocation.csv", index=False)
    hotspot_rows: list[dict[str, object]] = []
    top_indices = list(active_line_results.sort_values("loading_percent", ascending=False).head(10).index)
    selected_indices = list(net.line.index[net.line.line_id.isin(diagnostic_line_ids) & net.line.in_service])
    for line_index in dict.fromkeys(top_indices + selected_indices):
        line = net.line.loc[line_index]
        from_bus = net.bus.loc[int(line["from_bus"])]
        to_bus = net.bus.loc[int(line["to_bus"])]
        hotspot_rows.append({
            "case_id": case["case_id"],
            "line_id": line["line_id"],
            "source_line_id": line["source_line_id"],
            "voltage_kv": float(from_bus["vn_kv"]),
            "from_bus_id": from_bus["bus_id"],
            "from_facility_name": from_bus.get("facility_name"),
            "to_bus_id": to_bus["bus_id"],
            "to_facility_name": to_bus.get("facility_name"),
            "max_i_ka": float(line["max_i_ka"]),
            "parameter_status": line["parameter_status"],
            "loading_percent": float(net.res_line.loc[line_index, "loading_percent"]),
            "p_from_mw": float(net.res_line.loc[line_index, "p_from_mw"]),
            "i_ka": float(net.res_line.loc[line_index, "i_ka"]),
        })
    source_rows = []
    residual_by_source = {str(row["generation_source"]): row for row in residual_rows}
    for source, observed in observation["generation_by_source_mw"].items():
        source_names = GENERATION_GROUPS[source]
        mask = generators["generation_source"].fillna("").astype(str).str.lower().isin(source_names) & generators["bus_id"].fillna("").astype(str).ne("")
        residual = residual_by_source[source]
        mapped_input = float(allocated.loc[mask, "scenario_p_mw"].sum())
        residual_mw = float(residual["unmapped_residual_mw"])
        source_rows.append({
            "case_id": case["case_id"],
            "generation_source": source,
            "ren_observed_mw": observed,
            "mapped_nameplate_capacity_mw": residual["mapped_nameplate_capacity_mw"],
            "mapped_asset_input_mw": mapped_input,
            "unmapped_residual_mw": residual_mw,
            "model_total_input_mw": mapped_input + residual_mw,
            "capacity_violation_count": 0,
            "residual_bus_id": residual["residual_bus_id"],
            "residual_status": residual["residual_status"],
        })
    boundary_rows: list[dict[str, Any]] = []
    for index, row in net.ext_grid.iterrows():
        boundary_rows.append({
            "case_id": case["case_id"],
            "timestamp_utc": timestamp.isoformat(),
            "boundary_name": row["name"],
            "bus_id": net.bus.loc[int(row["bus"]), "bus_id"],
            "p_into_pt60_mw": float(net.res_ext_grid.loc[index, "p_mw"]),
            "q_into_pt60_mvar": float(net.res_ext_grid.loc[index, "q_mvar"]),
            "measurement_status": "MODEL_DERIVED_NOT_OBSERVED_PER_CIRCUIT",
        })
    for index, row in net.sgen.loc[boundary_sgen_mask].iterrows():
        boundary_rows.append({
            "case_id": case["case_id"],
            "timestamp_utc": timestamp.isoformat(),
            "boundary_name": row["name"],
            "bus_id": net.bus.loc[int(row["bus"]), "bus_id"],
            "p_into_pt60_mw": float(net.res_sgen.loc[index, "p_mw"]),
            "q_into_pt60_mvar": float(net.res_sgen.loc[index, "q_mvar"]),
            "measurement_status": "MODEL_DERIVED_AGGREGATE_CAPACITY_WEIGHTED_NOT_OBSERVED_PER_CIRCUIT",
        })
    cross_border_rows = [] if case.get("skip_optional_external_acquisition", False) else cross_border_evidence(case["case_id"], timestamp, observation, model_net_import, refresh)
    if independent_cross_border_override is not None:
        independent_value = float(independent_cross_border_override["net_import_mw"])
        cross_border_rows.append({
            "case_id": case["case_id"],
            "timestamp_utc": timestamp.isoformat(),
            "source": str(independent_cross_border_override.get("source", "CALLER_SUPPLIED_INDEPENDENT_PUBLIC_SOURCE")),
            "import_mw": max(0.0, independent_value),
            "export_mw": max(0.0, -independent_value),
            "net_import_mw": independent_value,
            "availability_status": "AVAILABLE",
            "independent_of_ren": True,
            "source_url": str(independent_cross_border_override["source_url"]),
            "error_vs_ren_mw": independent_value - float(observation["net_import_mw"]),
            "absolute_error_vs_ren_mw": abs(independent_value - float(observation["net_import_mw"])),
        })
    loss_row = {
        "case_id": case["case_id"],
        "timestamp_utc": timestamp.isoformat(),
        "model_total_pt60_loss_mw": result["model_total_pt60_losses_mw"],
        "model_total_pt60_loss_percent_of_load": result["model_total_pt60_losses_percent_of_load"],
        "model_rnt_line_loss_mw": result["model_rnt_line_losses_mw"],
        "model_rnd_at_line_loss_mw": result["model_rnd_at_line_losses_mw"],
        "model_transformer_loss_mw": result["model_transformer_losses_mw"],
        "model_rnt_scope_loss_mw": result["model_rnt_scope_losses_mw"],
        "model_rnt_scope_loss_percent_of_load": result["model_rnt_scope_losses_percent_of_load"],
        "ren_rnt_benchmark_date": rnt_loss["benchmark_date"],
        "ren_rnt_monthly_loss_percent": rnt_loss["loss_percent"],
        "error_percentage_points": result["loss_percent_error_vs_ren_rnt_monthly"],
        "comparison_status": "RNT_LINES_150KV_PLUS_AND_TRANSFORMERS_VS_MONTHLY_RNT_BALANCE; RND_60_130KV_EXCLUDED; NOT_SNAPSHOT_TELEMETRY",
        "source_url": rnt_loss["source_url"],
    }
    return result, source_rows, hotspot_rows, load_audit_rows, cross_border_rows, boundary_rows, loss_row
