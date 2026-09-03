#!/usr/bin/env python3
from __future__ import annotations

import json
import math
from typing import Any

import pandas as pd
import networkx as nx
import numpy as np
from scipy.spatial import cKDTree

from common import PROJECT, RAW, TABLES, ensure_dirs, haversine_m, normalize_name, parse_mw, parse_osm_voltage_values, point_in_geojson_geometry, read_json, utc_now, write_json


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
    if load_mode == "RNT_INDUSTRIAL_PLUS_EREDES_OBSERVED" and ren_path.exists():
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
    elif load_mode not in {"EREDES_OBSERVED_ONLY", "RNT_INDUSTRIAL_PLUS_EREDES_OBSERVED"} and ren_path.exists() and not weights.empty:
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
        candidates.append(
            {
                "generator_id": f"GEN:{len(candidates):05d}", "source_id": element["source_id"],
                "osm_power_type": tags.get("power", ""),
                "name": tags.get("name", tags.get("ref", "")), "normalized_name": normalize_name(tags.get("name", "")),
                "generation_source": tags.get("plant:source", tags.get("generator:source", tags.get("source", "unknown"))),
                "nameplate_mw": capacity, "bus_id": best_bus, "match_distance_m": best_distance if best_distance < float("inf") else "",
                "p_mw": capacity * float(config["generator_dispatch_fraction"]) if best_bus else 0.0,
                "q_mvar": 0.0, "dispatch_fraction": float(config["generator_dispatch_fraction"]),
                "source_status": "DIRECT_OSM_ASSET_INFERRED_BUS" if best_bus else ("OUTSIDE_PORTUGAL_CONTINENTAL_MODEL_SCOPE" if not in_model_scope else "UNASSIGNED_OSM_ASSET"),
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
    # Assign each generator unit to only its nearest compatible plant before
    # comparing capacities. This prevents adjacent plants (for example Frades
    # I and II) from both claiming the same set of unit objects.
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
            if distance <= 3000.0 and distance < best_plant_distance:
                best_plant_index = int(plant_index)
                best_plant_distance = distance
        if best_plant_index is not None:
            units_by_plant[best_plant_index].append(int(unit_index))

    duplicate_plants: list[int] = []
    for plant_index, plant in plants.iterrows():
        assigned_units = units.loc[units_by_plant[int(plant_index)]] if units_by_plant[int(plant_index)] else units.iloc[0:0]
        unit_capacity = float(assigned_units["nameplate_mw"].sum())
        plant_capacity = float(plant["nameplate_mw"])
        if plant_capacity > 0 and 0.70 <= unit_capacity / plant_capacity <= 1.30:
            duplicate_plants.append(int(plant_index))
    frame["hierarchy_dedup_status"] = "RETAINED_DISPATCH_OBJECT"
    frame.loc[duplicate_plants, "hierarchy_dedup_status"] = "AGGREGATE_PLANT_DUPLICATED_BY_NEARBY_GENERATOR_UNITS"
    frame.loc[duplicate_plants].to_csv(TABLES / "generation_hierarchy_dedup_ledger.csv", index=False)
    frame = frame.drop(index=duplicate_plants)
    # Remove obvious duplicate representations of the same named asset at the
    # same location, retaining the largest published capacity.
    frame["spatial_key"] = frame.apply(lambda row: f"{round(float(row.lon), 3)}:{round(float(row.lat), 3)}:{row.normalized_name}", axis=1)
    frame = frame.sort_values("nameplate_mw", ascending=False).drop_duplicates("spatial_key").drop(columns="spatial_key")
    frame["generator_id"] = [f"GEN:{index:05d}" for index in range(len(frame))]
    return frame


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
        "Other Thermal": {"oil", "diesel", "waste", "geothermal"},
        "Biomass": {"biomass", "biogas", "biomass;gas"},
        "Wave": {"wave"},
        "Battery Injection": {"battery"},
    }
    output = generators.copy()
    output["p_mw"] = 0.0
    output["dispatch_fraction"] = 0.0
    output["dispatch_target_source"] = "UNMAPPED_REN_SOURCE"
    assigned = output["bus_id"].fillna("").astype(str) != ""
    scenario_config = read_json(PROJECT / "config" / "model_config.json")
    is_merit_order = scenario_config.get("generation_dispatch_mode") == "MERIT_ORDER_REGIONAL_HYDRO_CCGT"
    for ren_name, source_names in groups.items():
        mask = assigned & output["generation_source"].fillna("").astype(str).str.lower().isin(source_names)
        capacity = float(output.loc[mask, "nameplate_mw"].sum())
        target = float(targets.get(ren_name, 0.0))
        if capacity <= 0:
            continue
        if is_merit_order and ren_name == "Hydro":
            large_mask = mask & (output["nameplate_mw"] >= 100.0)
            small_mask = mask & (output["nameplate_mw"] < 100.0)
            cap_large = float(output.loc[large_mask, "nameplate_mw"].sum())
            cap_small = float(output.loc[small_mask, "nameplate_mw"].sum())
            p_large = min(target, cap_large * 0.82)
            p_small = max(0.0, target - p_large)
            frac_large = p_large / cap_large if cap_large > 0 else 0.0
            frac_small = p_small / cap_small if cap_small > 0 else 0.0
            output.loc[large_mask, "dispatch_fraction"] = frac_large
            output.loc[large_mask, "p_mw"] = output.loc[large_mask, "nameplate_mw"] * frac_large
            output.loc[large_mask, "dispatch_target_source"] = "Hydro (Large Peaking)"
            output.loc[small_mask, "dispatch_fraction"] = frac_small
            output.loc[small_mask, "p_mw"] = output.loc[small_mask, "nameplate_mw"] * frac_small
            output.loc[small_mask, "dispatch_target_source"] = "Hydro (Small Run-of-River)"
            continue
        if is_merit_order and ren_name == "Natural Gas":
            large_mask = mask & (output["nameplate_mw"] >= 150.0)
            small_mask = mask & (output["nameplate_mw"] < 150.0)
            cap_large = float(output.loc[large_mask, "nameplate_mw"].sum())
            cap_small = float(output.loc[small_mask, "nameplate_mw"].sum())
            p_large = min(target, cap_large * 0.40)
            p_small = max(0.0, target - p_large)
            frac_large = p_large / cap_large if cap_large > 0 else 0.0
            frac_small = p_small / cap_small if cap_small > 0 else 0.0
            output.loc[large_mask, "dispatch_fraction"] = frac_large
            output.loc[large_mask, "p_mw"] = output.loc[large_mask, "nameplate_mw"] * frac_large
            output.loc[large_mask, "dispatch_target_source"] = "Natural Gas (Large CCGT)"
            output.loc[small_mask, "dispatch_fraction"] = frac_small
            output.loc[small_mask, "p_mw"] = output.loc[small_mask, "nameplate_mw"] * frac_small
            output.loc[small_mask, "dispatch_target_source"] = "Natural Gas (Small Cogen/Peaker)"
            continue
        fraction = target / capacity
        output.loc[mask, "dispatch_fraction"] = fraction
        output.loc[mask, "p_mw"] = output.loc[mask, "nameplate_mw"] * fraction
        output.loc[mask, "dispatch_target_source"] = ren_name
    output["dispatch_status"] = "MERIT_ORDER_AND_REN_SOURCE_TOTALS" if is_merit_order else "REN_SOURCE_TOTAL_PROPORTIONAL_TO_OSM_NAMEPLATE"
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
