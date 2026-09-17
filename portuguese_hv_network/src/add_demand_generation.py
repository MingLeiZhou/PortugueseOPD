#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
import unicodedata
from typing import Any

import pandas as pd
import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

from common import PROJECT, RAW, TABLES, ensure_dirs, haversine_m, normalize_name, parse_mw, parse_osm_voltage_values, point_in_geojson_geometry, read_json, utc_now, write_json


PDIRT_DIRECT_CLIENT_BUS_OVERRIDES = {
    "petrogal": "BUS:OSM:way:163214308:150",
    "repsol sines": "BUS:OSM:way:163214308:150",
    "siderurgia maia": "BUS:OSM:way:151109816:220",
    "sakthi maia": "BUS:OSM:way:151109816:220",
    "lusosider": "BUS:OSM:way:163226996:150",
    "neves corvo": "BUS:OSM:way:163204230:150",
}

# Public grid-development records distinguish the two Frades plants: the
# older 2x97 MW Frades I units remain on the 150 kV Frades installation,
# whereas the 2x390 MW Frades II units evacuate through two 400 kV circuits to
# Vieira do Minho.  A capacity-only nearest-facility rule otherwise assigns all
# four colocated OSM units to the closer 150 kV substation.
FRADES_GENERATOR_BUS_OVERRIDES = {
    "OSM:node:12188389776": "BUS:OSM:relation:19040600:400",
    "OSM:node:12188389777": "BUS:OSM:relation:19040600:400",
    "OSM:node:12188389778": "BUS:OSM:way:151077578:150",
    "OSM:node:12188389779": "BUS:OSM:way:151077578:150",
}
FRADES_OVERRIDE_STATUS = "PUBLIC_EVIDENCE_FRADES_I_150KV_FRADES_II_400KV_SPLIT"


def pde_key(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\b(subestacao|central|termoelectrica|hidroeletrica|fotovoltaica|de|da|do|das|dos)\b", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def pdirt_residual_load_rows(
    buses: pd.DataFrame,
    connected_buses: set[str],
    residual_mw: float,
    timestamp: str,
) -> pd.DataFrame:
    reference_path = TABLES / "pdirt_annex12_2025_pde_loads.csv"
    if not reference_path.exists():
        raise FileNotFoundError(f"Missing {reference_path}; run extract_pdirt_annex12.py first")
    reference = pd.read_csv(reference_path)
    reference = reference[reference["season"].eq("WINTER")].copy()
    reference["reference_p_mw"] = pd.to_numeric(reference["peak_p_mw"], errors="coerce")
    reference["reference_q_mvar"] = pd.to_numeric(reference["peak_q_mvar"], errors="coerce")
    candidates = buses[
        buses["bus_id"].astype(str).isin(connected_buses)
        & buses["facility_name"].notna()
        & buses["voltage_kv"].ge(150)
    ].copy()
    candidates["pde_key"] = candidates["facility_name"].map(pde_key)
    candidates["facility_penalty"] = candidates["facility_name"].astype(str).str.contains(
        "Central|Parque|Tração", case=False, regex=True
    ).astype(int)
    available_bus_ids = set(buses["bus_id"].astype(str))
    rows: list[dict[str, object]] = []
    for row in reference.to_dict("records"):
        key = pde_key(row["pde_name"])
        bus_id = PDIRT_DIRECT_CLIENT_BUS_OVERRIDES.get(key, "")
        status = "CURATED_DIRECT_CLIENT_TO_PUBLIC_FACILITY"
        if bus_id not in available_bus_ids:
            exact = candidates[candidates["pde_key"].eq(key)]
            if exact.empty:
                continue
            selected = exact.sort_values(["facility_penalty", "voltage_kv", "bus_id"]).iloc[0]
            bus_id = str(selected["bus_id"])
            status = "NORMALIZED_EXACT_PUBLIC_FACILITY_NAME_LOWEST_RNT_VOLTAGE"
        if float(row["reference_p_mw"]) <= 0.0:
            continue
        rows.append({**row, "bus_id": bus_id, "mapping_status": status})
    mapped = pd.DataFrame(rows)
    denominator = float(mapped["reference_p_mw"].sum())
    mapped["p_mw"] = residual_mw * mapped["reference_p_mw"] / denominator
    mapped["q_mvar"] = mapped["p_mw"] * mapped["reference_q_mvar"] / mapped["reference_p_mw"]
    mapped["power_factor"] = mapped["p_mw"] / (mapped["p_mw"] ** 2 + mapped["q_mvar"] ** 2) ** 0.5
    mapped["load_id"] = [f"LOAD:PDIRT-PDE:{index:03d}" for index in range(len(mapped))]
    mapped["substation_name"] = mapped["pde_name"]
    mapped["facility_code"] = "PDIRT_PDE"
    mapped["timestamp"] = timestamp
    mapped["source_status"] = "REN_MINUS_EREDES_ALLOCATED_BY_PDIRT_ANNEX12_PDE_WEIGHT"
    mapped["reactive_power_status"] = "PDIRT_ANNEX12_WINTER_PEAK_Q_TO_P_PROXY"
    mapped["network_connection_status"] = "CONNECTED_TO_LINE_OR_TRANSFORMER"
    mapped["in_service_scenario"] = True
    mapped["observed_p_mw"] = 0.0
    mapped["ren_residual_p_mw"] = mapped["p_mw"]
    mapped[["pde_name", "bus_id", "mapping_status", "reference_p_mw", "reference_q_mvar", "p_mw", "q_mvar"]].to_csv(
        TABLES / "pdirt_pde_base_scenario_mapping.csv", index=False
    )
    return mapped


def utilization_midpoint(value: Any) -> float:
    text = str(value).strip()
    if text == "+100%":
        return 1.05
    match = __import__("re").search(r"(\d+)%-([0-9]+)%", text)
    return (float(match.group(1)) + float(match.group(2))) / 200.0 if match else 0.0


def ptd_bus_weights(buses: pd.DataFrame) -> pd.DataFrame:
    path = RAW / "eredes" / "postos-transformacao-distribuicao.csv"
    bus_rows = buses[
        (buses["voltage_kv"] == 60)
        & (buses["source"] == "E-REDES")
        & (buses["facility_type"] == "substation")
    ].copy()
    if not path.exists() or bus_rows.empty:
        return pd.DataFrame(columns=["bus_id", "ptd_count", "ptd_installed_mva", "ptd_utilized_mw_weight", "nearest_distance_km"])
    ptd = pd.read_csv(path, sep=";", low_memory=False)
    coordinates = ptd["coordenadas_geo"].astype(str).str.extract(r"^\s*([-0-9.]+)\s*,\s*([-0-9.]+)\s*$")
    ptd["lat"] = pd.to_numeric(coordinates[0], errors="coerce")
    ptd["lon"] = pd.to_numeric(coordinates[1], errors="coerce")
    ptd["installed_mva"] = pd.to_numeric(ptd["potencia_transformacao_kva"], errors="coerce").fillna(0.0) / 1000.0
    ptd["utilization_fraction"] = ptd["nivel_utilizacao"].map(utilization_midpoint)
    ptd["weight_mw"] = ptd["installed_mva"] * ptd["utilization_fraction"]
    ptd = ptd.dropna(subset=["lat", "lon"])

    def xyz(frame: pd.DataFrame) -> np.ndarray:
        lat = np.radians(frame["lat"].astype(float).to_numpy())
        lon = np.radians(frame["lon"].astype(float).to_numpy())
        return np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))

    station_points = bus_rows.rename(columns={"lat": "lat", "lon": "lon"})
    tree = cKDTree(xyz(station_points))
    chord, index = tree.query(xyz(ptd), k=1)
    angular = 2.0 * np.arcsin(np.minimum(1.0, chord / 2.0))
    ptd["nearest_distance_km"] = angular * 6371.0088
    ptd["bus_id"] = station_points.iloc[index]["bus_id"].astype(str).to_numpy()
    grouped = ptd.groupby("bus_id", as_index=False).agg(
        ptd_count=("cod_instalacao", "size"),
        ptd_installed_mva=("installed_mva", "sum"),
        ptd_utilized_mw_weight=("weight_mw", "sum"),
        nearest_distance_km=("nearest_distance_km", "max"),
    )
    grouped.to_csv(TABLES / "ptd_load_allocation_weights.csv", index=False)
    return grouped


def loads(buses: pd.DataFrame, config: dict[str, Any], connected_buses: set[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    power_factor = float(config["load_power_factor"])
    snapshot = pd.read_csv(RAW / "eredes" / "load_snapshot.csv", low_memory=False)
    code_field = "codigo_subestacao"
    energy_field = "energia"
    snapshot[code_field] = snapshot[code_field].astype(str)
    snapshot[energy_field] = pd.to_numeric(snapshot[energy_field], errors="coerce")
    grouped = snapshot.groupby(code_field, as_index=False).agg(
        substation_name=("subestacao", "first"), timestamp=("datahora", "first"), energy_kwh=(energy_field, "sum")
    )
    # E-REDES codes also occur as OSM `ref` tags. The official E-REDES bus is
    # the primary join target so the same measurement cannot be duplicated by
    # a concordant OSM facility representation.
    bus_lookup = buses[
        (buses["voltage_kv"] == 60)
        & (buses["source"] == "E-REDES")
        & buses["facility_code"].notna()
    ].copy()
    bus_lookup["facility_code"] = bus_lookup["facility_code"].astype(str)
    bus_lookup = bus_lookup.sort_values("bus_id").drop_duplicates("facility_code")
    merged = grouped.merge(bus_lookup[["bus_id", "facility_code"]], left_on=code_field, right_on="facility_code", how="left")
    tan_phi = math.tan(math.acos(power_factor))
    matched = merged[merged["bus_id"].notna()].copy()
    matched["p_mw"] = matched["energy_kwh"] / 250.0
    matched["q_mvar"] = matched["p_mw"] * tan_phi
    matched["load_id"] = [f"LOAD:{index:05d}" for index in range(len(matched))]
    matched["source_status"] = "DIRECT_EREDES_ACTIVE_POWER"
    matched["reactive_power_status"] = "SCENARIO_ASSUMPTION_FIXED_POWER_FACTOR"
    matched["power_factor"] = power_factor
    matched["network_connection_status"] = matched["bus_id"].astype(str).map(
        lambda value: "CONNECTED_TO_LINE_OR_TRANSFORMER" if value in connected_buses else "UNCONNECTED_FACILITY"
    )
    matched["in_service_scenario"] = matched["network_connection_status"] == "CONNECTED_TO_LINE_OR_TRANSFORMER"
    matched["observed_p_mw"] = matched["p_mw"]
    matched["ren_residual_p_mw"] = 0.0

    ren_path = RAW / "ren" / "dispatch_calibration.json"
    weights = ptd_bus_weights(buses)
    load_mode = config.get("load_completion_mode", "EREDES_OBSERVED_PLUS_REN_RESIDUAL_PTD_WEIGHTED")
    if load_mode == "EREDES_OBSERVED_PLUS_PDIRT_PDE_WEIGHTED_RNT_RESIDUAL" and ren_path.exists():
        ren = read_json(ren_path)
        residual = max(0.0, float(ren["consumption_mw"]) - float(matched["p_mw"].sum()))
        mapped_pdirt = pdirt_residual_load_rows(
            buses, connected_buses, residual, str(ren["calibration_timestamp_utc"])
        )
        matched = pd.concat([matched, mapped_pdirt], ignore_index=True, sort=False)
    elif load_mode == "RNT_INDUSTRIAL_PLUS_EREDES_OBSERVED" and ren_path.exists():
        ren = read_json(ren_path)
        residual = max(0.0, float(ren["consumption_mw"]) - float(matched["p_mw"].sum()))
        rnt_allocations = [
            ("BUS:OSM:way:144622215:400", 500.0, "Sines 400kV Bulk Industrial Hub"),
            ("BUS:OSM:way:129055610:400", 450.0, "Palmela 400kV Setúbal Industrial Hub"),
            ("BUS:OSM:way:144554435:400", 300.0, "Lavos 400kV Figueira da Foz Industrial"),
            ("BUS:OSM:way:151109816:220", 350.0, "Maia 220kV Siderurgia Heavy Industry"),
            ("BUS:OSM:way:131715746:400", 250.0, "Rio Maior 400kV Central Transmission Hub"),
            ("BUS:OSM:way:163214308:150", 216.25, "Sines 150kV Petrochemical Complex"),
        ]
        rnt_rows = []
        for idx, (bus_id, p_target, desc) in enumerate(rnt_allocations):
            p_val = p_target * (residual / 2066.25)
            rnt_rows.append({
                "bus_id": bus_id,
                "p_mw": p_val,
                "q_mvar": p_val * tan_phi,
                "power_factor": power_factor,
                "load_id": f"LOAD:RNT-INDUSTRIAL:{idx+1:02d}",
                "substation_name": desc,
                "facility_code": "RNT_INDUSTRIAL",
                "timestamp": str(ren["calibration_timestamp_utc"]),
                "source_status": "RNT_DIRECT_TRANSMISSION_INDUSTRIAL_ALLOCATION",
                "reactive_power_status": "SCENARIO_ASSUMPTION_FIXED_POWER_FACTOR",
                "network_connection_status": "CONNECTED_TO_LINE_OR_TRANSFORMER" if bus_id in connected_buses else "UNCONNECTED_FACILITY",
                "in_service_scenario": True,
                "observed_p_mw": 0.0,
                "ren_residual_p_mw": p_val,
            })
        matched = pd.concat([matched, pd.DataFrame(rnt_rows)], ignore_index=True, sort=False)
    elif load_mode not in {"EREDES_OBSERVED_ONLY", "RNT_INDUSTRIAL_PLUS_EREDES_OBSERVED", "EREDES_OBSERVED_PLUS_PDIRT_PDE_WEIGHTED_RNT_RESIDUAL"} and ren_path.exists() and not weights.empty:
        ren = read_json(ren_path)
        residual = max(0.0, float(ren["consumption_mw"]) - float(matched["p_mw"].sum()))
        positive = weights[
            (weights["ptd_utilized_mw_weight"] > 0)
            & weights["bus_id"].astype(str).isin(connected_buses)
        ].copy()
        if residual > 0 and not positive.empty:
            facility = buses.set_index("bus_id")
            positive["facility_code"] = positive["bus_id"].map(facility["facility_code"])
            observed_by_bus = matched.groupby("bus_id")["p_mw"].sum()
            positive["observed_at_bus_mw"] = positive["bus_id"].map(observed_by_bus).fillna(0.0)
            characteristics = pd.read_csv(RAW / "eredes" / "caracteristicas-da-rede.csv", sep=";", low_memory=False)
            characteristics.columns = [str(column).lstrip("\ufeff") for column in characteristics.columns]
            installed = characteristics.sort_values("ano").drop_duplicates("codigo_da_instalacao", keep="last").set_index("codigo_da_instalacao")["potencia_instalada"]
            positive["eredes_installed_mva"] = pd.to_numeric(positive["facility_code"].map(installed), errors="coerce")
            positive["allocation_headroom_mw"] = (
                positive["eredes_installed_mva"].fillna(positive["ptd_installed_mva"])
                - positive["observed_at_bus_mw"]
            ).clip(lower=0.0)
            positive["p_mw"] = 0.0
            remaining = residual
            for _ in range(20):
                room = (positive["allocation_headroom_mw"] - positive["p_mw"]).clip(lower=0.0)
                active = room > 1e-9
                if remaining <= 1e-6 or not active.any():
                    break
                active_weights = positive.loc[active, "ptd_utilized_mw_weight"].clip(lower=1e-9)
                proposed = remaining * active_weights / active_weights.sum()
                addition = np.minimum(proposed.to_numpy(), room.loc[active].to_numpy())
                positive.loc[active, "p_mw"] += addition
                remaining -= float(addition.sum())
            if remaining > 1e-3:
                raise RuntimeError(f"REN residual load exceeds documented E-REDES/PTD capacity headroom by {remaining:.3f} MW")
            positive["q_mvar"] = positive["p_mw"] * tan_phi
            positive["power_factor"] = power_factor
            positive["load_id"] = [f"LOAD:REN-RESIDUAL:{index:04d}" for index in range(len(positive))]
            positive["substation_name"] = positive["bus_id"].map(facility["facility_name"])
            positive["timestamp"] = str(ren["calibration_timestamp_utc"])
            positive["source_status"] = "REN_SYSTEM_RESIDUAL_PTD_SPATIAL_ALLOCATION"
            positive["reactive_power_status"] = "SCENARIO_ASSUMPTION_FIXED_POWER_FACTOR"
            positive["network_connection_status"] = positive["bus_id"].astype(str).map(
                lambda value: "CONNECTED_TO_LINE_OR_TRANSFORMER" if value in connected_buses else "UNCONNECTED_FACILITY"
            )
            positive["in_service_scenario"] = positive["network_connection_status"] == "CONNECTED_TO_LINE_OR_TRANSFORMER"
            positive["observed_p_mw"] = 0.0
            positive["ren_residual_p_mw"] = positive["p_mw"]
            matched = pd.concat([matched, positive], ignore_index=True, sort=False)
    columns = ["load_id", "bus_id", "p_mw", "q_mvar", "observed_p_mw", "ren_residual_p_mw", "power_factor", "substation_name", "facility_code", "timestamp", "source_status", "reactive_power_status", "network_connection_status", "in_service_scenario"]
    return matched[columns], merged[merged["bus_id"].isna()].copy()


def output_mw(tags: dict[str, Any]) -> float | None:
    for field in ("plant:output:electricity", "generator:output:electricity", "output:electricity", "output"):
        value = parse_mw(tags.get(field))
        if value is not None:
            return value
    return None


def _geometry_center(geometry: dict[str, Any]) -> tuple[float, float] | None:
    points: list[list[float]] = []

    def collect(value: Any) -> None:
        if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
            points.append(value)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    collect(geometry.get("coordinates", []))
    if not points:
        return None
    return (
        sum(float(point[0]) for point in points) / len(points),
        sum(float(point[1]) for point in points) / len(points),
    )


def _nearest_generation_bus(
    lon: float,
    lat: float,
    capacity_mw: float,
    buses: pd.DataFrame,
    eligible_bus_ids: set[str],
    maximum_distance_m: float,
) -> tuple[str, float, int | str, str]:
    eligible = buses[buses["bus_id"].astype(str).isin(eligible_bus_ids)].copy()
    minimum_connection_kv = 150 if capacity_mw >= 100.0 else 60
    eligible = eligible[eligible["voltage_kv"] >= minimum_connection_kv]
    facilities = eligible[eligible["facility_type"].isin(["substation", "switching_station"])]
    search = facilities if not facilities.empty else eligible
    if search.empty:
        return "", float("inf"), "", f"DGEG_NEAREST_FACILITY_MIN_{minimum_connection_kv}KV"
    distances = search.apply(
        lambda row: haversine_m((lon, lat), (float(row["lon"]), float(row["lat"]))),
        axis=1,
    )
    index = distances.idxmin()
    distance = float(distances.loc[index])
    if distance > maximum_distance_m:
        return "", distance, "", f"DGEG_NEAREST_FACILITY_MIN_{minimum_connection_kv}KV"
    return (
        str(search.loc[index, "bus_id"]),
        distance,
        int(search.loc[index, "voltage_kv"]),
        f"DGEG_NEAREST_FACILITY_MIN_{minimum_connection_kv}KV",
    )


def _dgeg_date_iso(value: Any) -> str:
    parsed = pd.to_datetime(pd.to_numeric(pd.Series([value]), errors="coerce"), unit="ms", utc=True).iloc[0]
    return "" if pd.isna(parsed) else parsed.isoformat()


def augment_with_dgeg_generation(
    osm: pd.DataFrame,
    buses: pd.DataFrame,
    config: dict[str, Any],
    eligible_bus_ids: set[str],
) -> pd.DataFrame:
    """Use official DGEG assets where they materially improve OSM coverage.

    DGEG wind records are turbine polygons and are aggregated by licensed wind
    farm, sub-park and commissioning date.  They replace the incomplete OSM
    wind inventory.  DGEG solar records are less complete, so only licensed,
    commissioned facilities more than 3 km from any OSM solar asset are added.
    """
    dgeg_dir = RAW / "dgeg"
    wind_path = dgeg_dir / "generation_CE.geojson"
    solar_path = dgeg_dir / "generation_CS.geojson"
    if not wind_path.exists() or not solar_path.exists():
        return osm

    def frame_from_geojson(path: Any) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        for feature in read_json(path).get("features", []):
            center = _geometry_center(feature.get("geometry") or {})
            if center is None:
                continue
            row = dict(feature.get("properties") or {})
            row["lon"], row["lat"] = center
            rows.append(row)
        return pd.DataFrame(rows)

    wind = frame_from_geojson(wind_path)
    solar = frame_from_geojson(solar_path)
    audit_rows: list[dict[str, Any]] = []
    additions: list[dict[str, Any]] = []
    maximum_distance = float(config["generation_bus_match_m"])
    inventory_cutoff_ms = pd.Timestamp(
        config.get("generation_inventory_cutoff_utc", config["calibration_timestamp_utc"])
    ).timestamp() * 1000.0

    wind["capacity_mw"] = pd.to_numeric(wind["potencia_geradorkw"], errors="coerce") / 1000.0
    wind["commissioned_ms"] = pd.to_numeric(wind["data_exploracao"], errors="coerce")
    wind = wind[
        wind["lic_exploracao"].eq("LicExploracao")
        & (wind["commissioned_ms"].isna() | wind["commissioned_ms"].le(inventory_cutoff_ms))
        & wind["capacity_mw"].gt(0.0)
    ].copy()
    wind_groups = wind.groupby(
        ["processo", "nome", "subparque", "data_exploracao"], dropna=False, as_index=False
    ).agg(
        capacity_mw=("capacity_mw", "sum"),
        lon=("lon", "mean"),
        lat=("lat", "mean"),
        turbine_count=("objectid", "size"),
    )
    removed_osm_wind = osm[osm["generation_source"].fillna("").astype(str).str.lower().eq("wind")]
    osm_wind_connections = removed_osm_wind[removed_osm_wind["bus_id"].fillna("").astype(str).ne("")].copy()
    osm = osm.drop(index=removed_osm_wind.index)
    for row in wind_groups.to_dict("records"):
        # DGEG does not publish the electrical connection bus.  Transfer the
        # inferred OSM bus assignment of the nearest wind asset instead of
        # connecting a whole wind farm to the geometrically nearest E-REDES
        # substation.  The latter created false radial 60 kV overloads.  A
        # conservative 20 km limit leaves genuinely unsupported farms
        # unassigned rather than inventing a connection.
        connection_distances = osm_wind_connections.apply(
            lambda candidate: haversine_m(
                (float(row["lon"]), float(row["lat"])),
                (float(candidate["lon"]), float(candidate["lat"])),
            ),
            axis=1,
        )
        if not connection_distances.empty and float(connection_distances.min()) <= 3000.0:
            connection = osm_wind_connections.loc[connection_distances.idxmin()]
            bus_id = str(connection["bus_id"])
            distance = float(connection_distances.min())
            voltage = int(connection["bus_voltage_kv"])
            rule = "DGEG_CAPACITY_NEAREST_OSM_WIND_CONNECTION_MAX_3KM"
        else:
            # Without a close public connection clue, avoid injecting an
            # entire wind farm into a radial 60 kV substation.  Force the
            # uncertain transfer to the nearest transmission-level facility.
            bus_id, distance, voltage, rule = _nearest_generation_bus(
                float(row["lon"]), float(row["lat"]), max(100.0, float(row["capacity_mw"])),
                buses, eligible_bus_ids, maximum_distance,
            )
            rule = (
                "DGEG_WIND_NO_CLOSE_OSM_CONNECTION_NEAREST_MIN_150KV"
                if bus_id else "UNASSIGNED_NO_OSM_OR_150KV_CONNECTION_WITHIN_20KM"
            )
        source_id = f"DGEG:CE:{int(row['processo'])}:{str(row.get('subparque') or 'NA')}:{str(row.get('data_exploracao') or 'NA')}"
        additions.append({
            "source_id": source_id,
            "osm_power_type": "generator_group",
            "name": " / ".join(part for part in [str(row.get("nome") or ""), str(row.get("subparque") or "")] if part),
            "normalized_name": normalize_name(row.get("nome", "")),
            "generation_source": "wind",
            "nameplate_mw": float(row["capacity_mw"]),
            "bus_id": bus_id,
            "match_distance_m": distance,
            "p_mw": 0.0,
            "q_mvar": 0.0,
            "dispatch_fraction": 0.0,
            "source_status": "DGEG_LICENSED_ASSET_INFERRED_OSM_BUS_TRANSFER" if bus_id else "UNASSIGNED_DGEG_LICENSED_ASSET",
            "dispatch_status": "NOT_YET_DISPATCHED",
            "lon": float(row["lon"]),
            "lat": float(row["lat"]),
            "bus_voltage_kv": voltage,
            "bus_assignment_rule": rule,
            "connection_evidence_status": "INFERRED_GEOGRAPHIC_TRANSFER_NOT_CONFIRMED_CONNECTION" if bus_id else "UNMAPPED",
            "tags_json": json.dumps({"dgeg_layer": "CE", "processo": row["processo"], "subparque": row.get("subparque"), "turbine_count": row["turbine_count"]}, ensure_ascii=False, sort_keys=True),
            "hierarchy_dedup_status": "DGEG_WIND_FARM_DATE_AGGREGATE",
            "available_from_utc": _dgeg_date_iso(row.get("data_exploracao")),
            "asset_evidence_source": "DGEG_CE_ARCGIS",
        })
    audit_rows.append({
        "action": "REPLACE_OSM_WIND_WITH_DGEG_LICENSED_FARMS",
        "removed_asset_count": len(removed_osm_wind),
        "removed_nameplate_mw": float(removed_osm_wind["nameplate_mw"].sum()),
        "added_asset_count": len(wind_groups),
        "added_nameplate_mw": float(wind_groups["capacity_mw"].sum()),
        "evidence_status": "OFFICIAL_DGEG_LICENSED_ASSET_GEOMETRY",
    })

    solar["capacity_mw"] = pd.to_numeric(solar["potencia_instaladakva"], errors="coerce") / 1000.0
    solar["commissioned_ms"] = pd.to_numeric(solar["data_exploracao"], errors="coerce")
    solar = solar[
        solar["lic_exploracao"].eq("LicExploracao")
        & (solar["commissioned_ms"].isna() | solar["commissioned_ms"].le(inventory_cutoff_ms))
        & solar["subtipo_instalacao"].fillna("").astype(str).str.startswith(("Solar", "UPAC"))
        & solar["capacity_mw"].gt(0.0)
    ].copy()
    solar_groups = solar.groupby(
        ["processo", "nome", "data_exploracao"], dropna=False, as_index=False
    ).agg(capacity_mw=("capacity_mw", "max"), lon=("lon", "mean"), lat=("lat", "mean"), polygon_count=("objectid", "size"))
    osm_solar = osm[osm["generation_source"].fillna("").astype(str).str.lower().eq("solar")]
    added_solar_count = 0
    added_solar_mw = 0.0
    for row in solar_groups.to_dict("records"):
        distances = osm_solar.apply(
            lambda candidate: haversine_m(
                (float(row["lon"]), float(row["lat"])),
                (float(candidate["lon"]), float(candidate["lat"])),
            ),
            axis=1,
        )
        name_match = osm_solar["normalized_name"].fillna("").eq(normalize_name(row.get("nome", ""))).any()
        nearest_osm_m = float(distances.min()) if not distances.empty else float("inf")
        if name_match or nearest_osm_m <= 3000.0:
            continue
        bus_id, distance, voltage, rule = _nearest_generation_bus(
            float(row["lon"]), float(row["lat"]), float(row["capacity_mw"]),
            buses, eligible_bus_ids, maximum_distance,
        )
        additions.append({
            "source_id": f"DGEG:CS:{int(row['processo'])}:{str(row.get('data_exploracao') or 'NA')}",
            "osm_power_type": "plant",
            "name": str(row.get("nome") or ""),
            "normalized_name": normalize_name(row.get("nome", "")),
            "generation_source": "solar",
            "nameplate_mw": float(row["capacity_mw"]),
            "bus_id": bus_id,
            "match_distance_m": distance,
            "p_mw": 0.0,
            "q_mvar": 0.0,
            "dispatch_fraction": 0.0,
            "source_status": "DIRECT_DGEG_LICENSED_ASSET_INFERRED_BUS" if bus_id else "UNASSIGNED_DGEG_LICENSED_ASSET",
            "dispatch_status": "NOT_YET_DISPATCHED",
            "lon": float(row["lon"]),
            "lat": float(row["lat"]),
            "bus_voltage_kv": voltage,
            "bus_assignment_rule": rule,
            "tags_json": json.dumps({"dgeg_layer": "CS", "processo": row["processo"], "polygon_count": row["polygon_count"], "capacity_basis": "potencia_instaladakva_as_unity_pf_proxy"}, ensure_ascii=False, sort_keys=True),
            "hierarchy_dedup_status": "DGEG_SOLAR_UNIQUE_TO_OSM_3KM_SCREEN",
            "available_from_utc": _dgeg_date_iso(row.get("data_exploracao")),
            "asset_evidence_source": "DGEG_CS_ARCGIS",
        })
        added_solar_count += 1
        added_solar_mw += float(row["capacity_mw"])
    audit_rows.append({
        "action": "ADD_DGEG_LICENSED_SOLAR_UNIQUE_BEYOND_3KM_OR_NAME_MATCH",
        "removed_asset_count": 0,
        "removed_nameplate_mw": 0.0,
        "added_asset_count": added_solar_count,
        "added_nameplate_mw": added_solar_mw,
        "evidence_status": "DGEG_KVA_USED_AS_UNITY_PF_NAMEPLATE_PROXY",
    })
    # DGEG reports the Casal da Cortiça storage demonstrator as 12 MVA / 24
    # MWh, connected in June 2025.  Public material does not disclose its
    # breaker or MW rating.  We therefore use 12 MW as an explicit unity-power-
    # factor upper-bound proxy and reuse the public connection already mapped
    # for the co-located named Casal da Cortiça solar facility.
    casal_solar = osm[osm["normalized_name"].eq("central fotovoltaica de casal da cortica")]
    if not casal_solar.empty:
        connection = casal_solar.sort_values(["match_distance_m", "bus_id"]).iloc[0]
        additions.append({
            "source_id": "DGEG:NOTICE:CASAL_DA_CORTICA_BESS:2025-06",
            "osm_power_type": "storage",
            "name": "Bateria de Casal da Cortiça",
            "normalized_name": "bateria de casal da cortica",
            "generation_source": "battery",
            "nameplate_mw": 12.0,
            "bus_id": str(connection["bus_id"]),
            "match_distance_m": float(connection["match_distance_m"]),
            "p_mw": 0.0,
            "q_mvar": 0.0,
            "dispatch_fraction": 0.0,
            "source_status": "DIRECT_DGEG_12MVA_24MWH_BESS_INFERRED_COLOCATED_CONNECTION",
            "dispatch_status": "NOT_YET_DISPATCHED",
            "lon": float(connection["lon"]),
            "lat": float(connection["lat"]),
            "bus_voltage_kv": connection["bus_voltage_kv"],
            "bus_assignment_rule": "COLOCATED_NAMED_CASAL_DA_CORTICA_SOLAR_PUBLIC_CONNECTION",
            "tags_json": json.dumps({
                "apparent_power_mva": 12.0,
                "energy_mwh": 24.0,
                "mw_basis": "12 MVA used as unity-power-factor MW upper-bound proxy",
                "source_url": "https://www.dgeg.gov.pt/pt/areas-setoriais/energia/energia-eletrica/atividades-eventos/",
            }, ensure_ascii=False, sort_keys=True),
            "hierarchy_dedup_status": "CURATED_PUBLIC_STORAGE_ASSET",
            "available_from_utc": "2025-06-01T00:00:00+00:00",
            "asset_evidence_source": "DGEG_CASAL_DA_CORTICA_STORAGE_NOTICE",
        })
        audit_rows.append({
            "action": "ADD_CASAL_DA_CORTICA_BESS",
            "removed_asset_count": 0,
            "removed_nameplate_mw": 0.0,
            "added_asset_count": 1,
            "added_nameplate_mw": 12.0,
            "evidence_status": "DGEG_12MVA_24MWH; MW_AND_CONNECTION_ARE_EXPLICIT_PROXIES",
        })
    pd.DataFrame(audit_rows).to_csv(TABLES / "generation_dgeg_integration_audit.csv", index=False)
    result = pd.concat([osm, pd.DataFrame(additions)], ignore_index=True, sort=False)
    result["generator_id"] = [f"GEN:{index:05d}" for index in range(len(result))]
    return result


def generation_candidates(buses: pd.DataFrame, config: dict[str, Any], eligible_bus_ids: set[str]) -> pd.DataFrame:
    elements = read_json(RAW / "osm" / "generation_elements.json")
    boundary_path = RAW / "reference" / "portugal_gisco_2024.geojson"
    country_geometry = read_json(boundary_path)["features"][0]["geometry"] if boundary_path.exists() else None
    allowed = set(config["voltage_levels_kv"])
    bus_voltage_lookup = buses.set_index("bus_id")["voltage_kv"].to_dict()
    candidates: list[dict[str, Any]] = []
    for element in elements:
        tags = element.get("tags") or {}
        capacity = output_mw(tags)
        if capacity is None or capacity <= 0:
            continue
        declared = parse_osm_voltage_values(tags.get("voltage"), allowed)
        network_buses = buses[buses["bus_id"].astype(str).isin(eligible_bus_ids)].copy()
        if declared:
            eligible = network_buses[network_buses["voltage_kv"].isin(declared)].copy()
            assignment_rule = "DECLARED_OSM_VOLTAGE_NEAREST_NETWORK_BUS"
        else:
            # Generator terminal voltages are commonly absent from OSM or are
            # below the model scope. Avoid injecting utility-scale plants into
            # the nearest 60 kV line endpoint when a nearby transmission-level
            # connection facility is available.
            minimum_connection_kv = 150 if capacity >= 100.0 else 60
            eligible = network_buses[network_buses["voltage_kv"] >= minimum_connection_kv].copy()
            assignment_rule = f"CAPACITY_CLASS_NEAREST_FACILITY_MIN_{minimum_connection_kv}KV"
        best_bus = ""
        best_distance = float("inf")
        mainland = config.get("continental_portugal_bounds", [-9.6, -6.0, 36.8, 42.2])
        in_model_scope = mainland[0] <= float(element["lon"]) <= mainland[1] and mainland[2] <= float(element["lat"]) <= mainland[3]
        if country_geometry is not None:
            in_model_scope = in_model_scope and point_in_geojson_geometry(float(element["lon"]), float(element["lat"]), country_geometry)
        if in_model_scope and not eligible.empty:
            # Prefer an actual substation/switching-station representation if
            # one lies within the admissible search radius. A line endpoint is
            # used only when no facility satisfies the same voltage rule.
            facility_eligible = eligible[eligible["facility_type"].isin(["substation", "switching_station"])]
            search = facility_eligible if not facility_eligible.empty else eligible
            distances = search.apply(
                lambda row: haversine_m((float(element["lon"]), float(element["lat"])), (float(row["lon"]), float(row["lat"]))), axis=1
            )
            index = distances.idxmin()
            best_distance = float(distances.loc[index])
            if best_distance <= float(config["generation_bus_match_m"]):
                best_bus = str(search.loc[index, "bus_id"])
        source_id = str(element["source_id"])
        override_bus = FRADES_GENERATOR_BUS_OVERRIDES.get(source_id)
        if override_bus and override_bus in set(network_buses["bus_id"].astype(str)):
            selected_bus = network_buses.loc[network_buses["bus_id"].astype(str).eq(override_bus)].iloc[0]
            best_bus = override_bus
            best_distance = haversine_m(
                (float(element["lon"]), float(element["lat"])),
                (float(selected_bus["lon"]), float(selected_bus["lat"])),
            )
            assignment_rule = FRADES_OVERRIDE_STATUS
        candidates.append(
            {
                "generator_id": f"GEN:{len(candidates):05d}", "source_id": source_id,
                "osm_power_type": tags.get("power", ""),
                "name": tags.get("name", tags.get("ref", "")), "normalized_name": normalize_name(tags.get("name", "")),
                "generation_source": tags.get("plant:source", tags.get("generator:source", tags.get("source", "unknown"))),
                "nameplate_mw": capacity, "bus_id": best_bus, "match_distance_m": best_distance if best_distance < float("inf") else "",
                "p_mw": capacity * float(config["generator_dispatch_fraction"]) if best_bus else 0.0,
                "q_mvar": 0.0, "dispatch_fraction": float(config["generator_dispatch_fraction"]),
                "source_status": FRADES_OVERRIDE_STATUS if override_bus and best_bus == override_bus else ("DIRECT_OSM_ASSET_INFERRED_BUS" if best_bus else ("OUTSIDE_PORTUGAL_CONTINENTAL_MODEL_SCOPE" if not in_model_scope else "UNASSIGNED_OSM_ASSET")),
                "dispatch_status": "SCENARIO_ASSUMPTION_NAMEPLATE_FRACTION", "lon": element["lon"], "lat": element["lat"],
                "bus_voltage_kv": int(bus_voltage_lookup[best_bus]) if best_bus else "",
                "bus_assignment_rule": assignment_rule,
                "tags_json": json.dumps(tags, ensure_ascii=False, sort_keys=True),
            }
        )
    frame = pd.DataFrame(candidates)
    if frame.empty:
        return frame
    # OSM commonly represents one facility twice: a power=plant object with
    # total nameplate and nearby power=generator objects with unit ratings.
    # When the nearby unit sum explains the plant rating, retain the units and
    # remove the aggregate plant row from electrical dispatch accounting.
    plants = frame[frame["osm_power_type"] == "plant"]
    units = frame[frame["osm_power_type"] == "generator"]
    # Assign each generator unit to only its nearest colocated compatible plant
    # before comparing capacities.  A tight radius avoids treating separate
    # hydro stations on the same river cascade as parent/child objects.
    units_by_plant: dict[int, list[int]] = {int(index): [] for index in plants.index}
    for unit_index, unit in units.iterrows():
        same_source_plants = plants[
            plants["generation_source"].fillna("").astype(str).str.lower()
            == str(unit["generation_source"]).lower()
        ]
        best_plant_index: int | None = None
        best_plant_distance = float("inf")
        for plant_index, plant in same_source_plants.iterrows():
            distance = haversine_m(
                (float(plant["lon"]), float(plant["lat"])),
                (float(unit["lon"]), float(unit["lat"])),
            )
            if distance <= 500.0 and distance < best_plant_distance:
                best_plant_index = int(plant_index)
                best_plant_distance = distance
        if best_plant_index is not None:
            units_by_plant[best_plant_index].append(int(unit_index))

    duplicate_plants: list[int] = []
    duplicate_units: list[int] = []
    for plant_index, plant in plants.iterrows():
        assigned_units = units.loc[units_by_plant[int(plant_index)]] if units_by_plant[int(plant_index)] else units.iloc[0:0]
        unit_capacity = float(assigned_units["nameplate_mw"].sum())
        plant_capacity = float(plant["nameplate_mw"])
        if assigned_units.empty or plant_capacity <= 0:
            continue
        # Never dispatch both an aggregate plant rating and its colocated unit
        # ratings.  Retain the more complete representation: units when their
        # published sum is at least the aggregate, otherwise the plant total.
        if unit_capacity >= plant_capacity:
            duplicate_plants.append(int(plant_index))
        else:
            duplicate_units.extend(int(index) for index in assigned_units.index)
    frame["hierarchy_dedup_status"] = "RETAINED_DISPATCH_OBJECT"
    frame.loc[duplicate_plants, "hierarchy_dedup_status"] = "AGGREGATE_PLANT_DUPLICATED_BY_NEARBY_GENERATOR_UNITS"
    frame.loc[duplicate_units, "hierarchy_dedup_status"] = "GENERATOR_UNIT_DUPLICATED_BY_NEARBY_AGGREGATE_PLANT"
    excluded = sorted(set(duplicate_plants + duplicate_units))
    frame.loc[excluded].to_csv(TABLES / "generation_hierarchy_dedup_ledger.csv", index=False)
    frame = frame.drop(index=excluded)
    # Remove obvious duplicate representations of the same *named* asset at
    # the same location, retaining the largest published capacity.  Anonymous
    # power=generator objects must remain distinct: multi-unit stations such
    # as Gouvaes (4 x 220 MW) place several unnamed machines within the same
    # rounded coordinate cell.  The previous unconditional spatial key
    # collapsed those real units into one and understated national nameplate
    # capacity by several gigawatts.
    frame["spatial_key"] = frame.apply(
        lambda row: (
            f"{round(float(row.lon), 3)}:{round(float(row.lat), 3)}:{row.normalized_name}"
            if str(row.normalized_name).strip()
            else f"UNNAMED_UNIQUE:{row.source_id}"
        ),
        axis=1,
    )
    frame = (
        frame.sort_values(["spatial_key", "nameplate_mw"], ascending=[True, False])
        .drop_duplicates("spatial_key")
        .drop(columns="spatial_key")
    )
    frame["generator_id"] = [f"GEN:{index:05d}" for index in range(len(frame))]
    return augment_with_dgeg_generation(frame, buses, config, eligible_bus_ids)


def balance_generation_scenario(
    generators: pd.DataFrame, load_rows: pd.DataFrame, lines: pd.DataFrame,
    transformers: pd.DataFrame, supply_share: float,
) -> pd.DataFrame:
    if generators.empty:
        return generators
    graph = nx.Graph()
    graph.add_edges_from(lines[["from_bus", "to_bus"]].astype(str).itertuples(index=False, name=None))
    if not transformers.empty:
        graph.add_edges_from(transformers[["hv_bus", "lv_bus"]].astype(str).itertuples(index=False, name=None))
    component_of: dict[str, int] = {}
    for component_id, component in enumerate(nx.connected_components(graph)):
        for bus_id in component:
            component_of[str(bus_id)] = component_id
    output = generators.copy()
    output["component_id"] = output["bus_id"].fillna("").astype(str).map(component_of)
    output["p_mw"] = 0.0
    output["dispatch_fraction"] = 0.0
    active_loads = load_rows[load_rows["in_service_scenario"]].copy()
    active_loads["component_id"] = active_loads["bus_id"].astype(str).map(component_of)
    load_by_component = active_loads.groupby("component_id")["p_mw"].sum().to_dict()
    for component_id, group in output[output["component_id"].notna()].groupby("component_id"):
        capacity = float(group["nameplate_mw"].sum())
        target = float(load_by_component.get(component_id, 0.0)) * supply_share
        fraction = min(1.0, target / capacity) if capacity > 0 else 0.0
        output.loc[group.index, "dispatch_fraction"] = fraction
        output.loc[group.index, "p_mw"] = output.loc[group.index, "nameplate_mw"] * fraction
    output["dispatch_status"] = "SCENARIO_ASSUMPTION_COMPONENT_LOAD_BALANCED_NAMEPLATE_SHARE"
    scenario_config = read_json(PROJECT / "config" / "model_config.json")
    pv_min_mw = float(scenario_config.get("power_flow_pv_generator_min_nameplate_mw", 100.0))
    pv_min_kv = float(scenario_config.get("power_flow_pv_generator_min_bus_kv", 150))
    output["voltage_control_mode"] = output.apply(
        lambda row: "PV_VOLTAGE_CONTROL_SCENARIO" if (
            str(row.get("bus_id", ""))
            and str(row.get("generation_source", "")).lower() != "battery"
            and float(row.get("nameplate_mw", 0.0)) >= pv_min_mw
            and float(row.get("bus_voltage_kv", 0.0) or 0.0) >= pv_min_kv
        ) else "FIXED_PQ_SCENARIO",
        axis=1,
    )
    return output


def ren_dispatch_generation(generators: pd.DataFrame) -> pd.DataFrame:
    path = RAW / "ren" / "dispatch_calibration.json"
    if generators.empty or not path.exists():
        return generators
    ren = read_json(path)
    targets = ren["generation_by_source_mw"]
    groups = {
        "Hydro": {"hydro"},
        "Solar": {"solar"},
        "Wind": {"wind"},
        "Natural Gas": {"gas", "gas;oil", "oil;gas"},
        "Other Thermal": {"oil", "diesel", "geothermal"},
        # REN reports waste-to-energy together with renewable thermal output;
        # keeping OSM `waste` under Other Thermal overstated that 25 MW bucket
        # while understating the official Biomass aggregate.
        "Biomass": {"biomass", "biogas", "biomass;gas", "waste"},
        "Wave": {"wave"},
        "Battery Injection": {"battery"},
    }
    output = generators.copy()
    output["p_mw"] = 0.0
    output["dispatch_fraction"] = 0.0
    output["dispatch_target_source"] = "UNMAPPED_REN_SOURCE"
    scenario_config = read_json(PROJECT / "config" / "model_config.json")
    commissioned = pd.to_datetime(output["available_from_utc"], utc=True, errors="coerce")
    calibration_timestamp = pd.Timestamp(scenario_config["calibration_timestamp_utc"])
    assigned = (
        output["bus_id"].fillna("").astype(str).ne("")
        & (commissioned.isna() | commissioned.le(calibration_timestamp))
    )
    is_merit_order = scenario_config.get("generation_dispatch_mode") == "MERIT_ORDER_REGIONAL_HYDRO_CCGT"
    audit_rows: list[dict[str, object]] = []

    def allocate_with_cap(mask: pd.Series, target_mw: float) -> float:
        capacity = output.loc[mask, "nameplate_mw"].fillna(0.0).clip(lower=0.0)
        headroom = (capacity - output.loc[mask, "p_mw"]).clip(lower=0.0)
        available_mw = float(headroom.sum())
        assigned_mw = min(float(target_mw), available_mw)
        if available_mw > 0.0 and assigned_mw > 0.0:
            output.loc[mask, "p_mw"] += headroom * assigned_mw / available_mw
        return float(target_mw) - assigned_mw

    for ren_name, source_names in groups.items():
        mask = assigned & output["generation_source"].fillna("").astype(str).str.lower().isin(source_names)
        capacity = float(output.loc[mask, "nameplate_mw"].sum())
        target = float(targets.get(ren_name, 0.0))
        output.loc[mask, "dispatch_target_source"] = ren_name
        remaining = target
        if is_merit_order and ren_name == "Hydro":
            large_mask = mask & (output["nameplate_mw"] >= 100.0)
            small_mask = mask & ~large_mask
            cap_large = float(output.loc[large_mask, "nameplate_mw"].sum())
            p_large = min(target, cap_large * 0.82)
            allocate_with_cap(large_mask, p_large)
            output.loc[large_mask, "dispatch_target_source"] = "Hydro (Large Peaking)"
            remaining = target - float(output.loc[mask, "p_mw"].sum())
            remaining = allocate_with_cap(small_mask, remaining)
            output.loc[small_mask, "dispatch_target_source"] = "Hydro (Small Run-of-River)"
            if remaining > 1e-9:
                remaining = allocate_with_cap(large_mask, remaining)
        elif is_merit_order and ren_name == "Natural Gas":
            large_mask = mask & (output["nameplate_mw"] >= 150.0)
            small_mask = mask & ~large_mask
            cap_large = float(output.loc[large_mask, "nameplate_mw"].sum())
            p_large = min(target, cap_large * 0.40)
            allocate_with_cap(large_mask, p_large)
            output.loc[large_mask, "dispatch_target_source"] = "Natural Gas (Large CCGT)"
            remaining = target - float(output.loc[mask, "p_mw"].sum())
            remaining = allocate_with_cap(small_mask, remaining)
            output.loc[small_mask, "dispatch_target_source"] = "Natural Gas (Small Cogen/Peaker)"
            if remaining > 1e-9:
                remaining = allocate_with_cap(large_mask, remaining)
        else:
            remaining = allocate_with_cap(mask, target)
        mapped_input = float(output.loc[mask, "p_mw"].sum())
        residual = max(0.0, target - mapped_input)
        audit_rows.append({
            "generation_source": ren_name,
            "ren_target_mw": target,
            "mapped_asset_count": int(mask.sum()),
            "mapped_nameplate_capacity_mw": capacity,
            "mapped_asset_input_mw": mapped_input,
            "unmapped_residual_mw": residual,
            "residual_status": "UNMAPPED_NATIONAL_RESIDUAL_PROXY" if residual > 1e-9 else "NONE",
        })
    positive_capacity = output["nameplate_mw"].fillna(0.0).gt(0.0)
    output.loc[positive_capacity, "dispatch_fraction"] = (
        output.loc[positive_capacity, "p_mw"] / output.loc[positive_capacity, "nameplate_mw"]
    )
    violations = assigned & (
        output["p_mw"].lt(-1e-9)
        | output["p_mw"].gt(output["nameplate_mw"].fillna(0.0) + 1e-8)
    )
    if violations.any():
        raise AssertionError(f"Generator capacity allocation failed for {int(violations.sum())} mapped assets")
    audit = pd.DataFrame(audit_rows)
    audit.to_csv(TABLES / "generation_dispatch_audit.csv", index=False)
    audit.loc[audit["unmapped_residual_mw"].gt(1e-9)].to_csv(
        TABLES / "generation_unmapped_residuals.csv", index=False
    )
    output["dispatch_status"] = "CAPACITY_CONSTRAINED_MERIT_ORDER_AND_REN_SOURCE_TOTALS" if is_merit_order else "CAPACITY_CONSTRAINED_REN_SOURCE_TOTAL_PROPORTIONAL_TO_NAMEPLATE"
    # Voltage control is a bus-level capability. Multiple public generator
    # objects connected to one bus are therefore assessed by their combined
    # nameplate rather than an arbitrary per-object threshold.
    assigned_capacity_by_bus = output.loc[assigned].groupby("bus_id")["nameplate_mw"].sum()
    output["bus_generation_nameplate_mw"] = output["bus_id"].map(assigned_capacity_by_bus).fillna(0.0)
    scenario_config = read_json(PROJECT / "config" / "model_config.json")
    pv_min_mw = float(scenario_config.get("power_flow_pv_generator_min_nameplate_mw", 20.0))
    pv_min_kv = float(scenario_config.get("power_flow_pv_generator_min_bus_kv", 60.0))
    output["voltage_control_mode"] = output.apply(
        lambda row: "PV_VOLTAGE_CONTROL_SCENARIO" if (
            str(row.get("bus_id", ""))
            and str(row.get("generation_source", "")).lower() != "battery"
            and float(row.get("bus_generation_nameplate_mw", 0.0)) >= pv_min_mw
            and float(row.get("bus_voltage_kv", 0.0) or 0.0) >= pv_min_kv
        ) else "FIXED_PQ_SCENARIO",
        axis=1,
    )
    return output


def main() -> None:
    ensure_dirs()
    config = read_json(PROJECT / "config" / "model_config.json")
    buses = pd.read_csv(TABLES / "buses.csv")
    lines = pd.read_csv(TABLES / "lines_topology.csv")
    transformers = pd.read_csv(TABLES / "transformers_topology.csv")
    connected_buses = set(lines["from_bus"].astype(str)) | set(lines["to_bus"].astype(str))
    if not transformers.empty:
        connected_buses |= set(transformers["hv_bus"].astype(str)) | set(transformers["lv_bus"].astype(str))
    load_rows, unmatched = loads(buses, config, connected_buses)
    graph = nx.Graph()
    graph.add_edges_from(lines[["from_bus", "to_bus"]].astype(str).itertuples(index=False, name=None))
    if not transformers.empty:
        graph.add_edges_from(transformers[["hv_bus", "lv_bus"]].astype(str).itertuples(index=False, name=None))
    component_of = {str(bus): component_id for component_id, component in enumerate(nx.connected_components(graph)) for bus in component}
    active_load_components = {
        component_of[str(bus)] for bus in load_rows.loc[load_rows["in_service_scenario"], "bus_id"].astype(str)
        if str(bus) in component_of
    }
    eligible_generation_buses = {
        str(bus) for bus in connected_buses
        if str(bus) in component_of and component_of[str(bus)] in active_load_components
    }
    generators = generation_candidates(buses, config, eligible_generation_buses)
    if config.get("generation_dispatch_mode") in {"REN_SOURCE_TOTALS_PROPORTIONAL_TO_OSM_NAMEPLATE", "MERIT_ORDER_REGIONAL_HYDRO_CCGT"}:
        generators = ren_dispatch_generation(generators)
    else:
        generators = balance_generation_scenario(
            generators, load_rows, lines, transformers,
            float(config["generation_supply_share_by_active_component"]),
        )
    load_rows.to_csv(TABLES / "loads.csv", index=False)
    unmatched.to_csv(TABLES / "loads_unmatched.csv", index=False)
    generators.to_csv(TABLES / "generators.csv", index=False)
    summary = {
        "generated_at": utc_now(), "loads": len(load_rows), "unmatched_load_records": len(unmatched),
        "total_load_p_mw": float(load_rows["p_mw"].sum()), "total_load_q_mvar": float(load_rows["q_mvar"].sum()),
        "scenario_connected_loads": int(load_rows["in_service_scenario"].sum()),
        "scenario_connected_load_p_mw": float(load_rows.loc[load_rows["in_service_scenario"], "p_mw"].sum()),
        "generation_candidates": len(generators),
        "assigned_generation_candidates": int((generators["bus_id"].astype(str) != "").sum()) if not generators.empty else 0,
        "total_assigned_scenario_generation_mw": float(generators.loc[generators["bus_id"].astype(str) != "", "p_mw"].sum()) if not generators.empty else 0.0,
    }
    write_json(TABLES / "demand_generation_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
