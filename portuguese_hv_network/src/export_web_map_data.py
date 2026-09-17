#!/usr/bin/env python3
"""Export compact GeoJSON used by the interactive Portuguese grid map."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from common import POWER_FLOW, PROJECT, RAW, TABLES, VALIDATION, element_center, parse_osm_voltage_values, read_json, utc_now, write_json


WEB_DATA = PROJECT / "site" / "public" / "data"
WEB_VOLTAGES = {60, 130, 150, 220, 400}


def clean(value: object) -> object:
    if value is None or pd.isna(value):
        return None
    if hasattr(value, "item"):
        value = value.item()
    return value


def feature(geometry: dict[str, object], properties: dict[str, object]) -> dict[str, object]:
    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {key: clean(value) for key, value in properties.items()},
    }


def stitch_rings(segments: list[tuple[list[int], list[list[float]]]]) -> list[list[list[float]]]:
    """Join OSM multipolygon outer-way fragments by shared endpoint IDs."""
    remaining = [(nodes[:], coords[:]) for nodes, coords in segments if len(nodes) >= 2]
    rings: list[list[list[float]]] = []
    while remaining:
        nodes, coords = remaining.pop(0)
        changed = True
        while changed and nodes[0] != nodes[-1]:
            changed = False
            for index, (other_nodes, other_coords) in enumerate(remaining):
                if nodes[-1] == other_nodes[0]:
                    nodes.extend(other_nodes[1:]); coords.extend(other_coords[1:])
                elif nodes[-1] == other_nodes[-1]:
                    nodes.extend(reversed(other_nodes[:-1])); coords.extend(reversed(other_coords[:-1]))
                elif nodes[0] == other_nodes[-1]:
                    nodes = other_nodes[:-1] + nodes; coords = other_coords[:-1] + coords
                elif nodes[0] == other_nodes[0]:
                    nodes = list(reversed(other_nodes[1:])) + nodes; coords = list(reversed(other_coords[1:])) + coords
                else:
                    continue
                remaining.pop(index); changed = True; break
        if len(coords) >= 4 and nodes[0] == nodes[-1]:
            rings.append(coords)
    return rings


def osm_detail_collections() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    """Export high-zoom OSM substation footprints, equipment, and line supports."""
    data = read_json(RAW / "osm" / "portugal_power_osm.json")
    elements = data.get("elements", [])
    ways = {int(item["id"]): item for item in elements if item.get("type") == "way"}
    hv_line_nodes: set[int] = set()
    for item in elements:
        tags = item.get("tags") or {}
        if item.get("type") == "way" and tags.get("power") in {"line", "minor_line", "cable"} and parse_osm_voltage_values(tags.get("voltage"), WEB_VOLTAGES):
            hv_line_nodes.update(int(value) for value in item.get("nodes") or [])

    area_features: list[dict[str, object]] = []
    equipment_features: list[dict[str, object]] = []
    support_features: list[dict[str, object]] = []
    for item in elements:
        tags = item.get("tags") or {}
        power = tags.get("power", "")
        item_type = item.get("type")
        osm_id = int(item.get("id", 0))
        voltages = parse_osm_voltage_values(tags.get("voltage"), WEB_VOLTAGES)
        base_properties = {
            "id": f"OSM:{item_type}:{osm_id}", "source": "OpenStreetMap",
            "source_status": "DIRECT_OSM_GEOMETRY", "object_type": "osm_power_detail",
            "power": power, "name": tags.get("name") or tags.get("ref"),
            "operator": tags.get("operator"), "voltage": tags.get("voltage"),
            "voltage_kv": max(voltages) if voltages else None,
            "rating": tags.get("rating"), "substation": tags.get("substation"),
        }
        if power == "substation" and voltages and item_type == "way":
            coords = [[float(point["lon"]), float(point["lat"])] for point in item.get("geometry") or []]
            if len(coords) >= 4 and coords[0] == coords[-1]:
                area_features.append(feature({"type": "Polygon", "coordinates": [coords]}, base_properties))
        elif power == "substation" and voltages and item_type == "relation":
            segments = []
            for member in item.get("members") or []:
                if member.get("type") != "way" or member.get("role", "") == "inner":
                    continue
                member_way = ways.get(int(member["ref"]))
                if not member_way:
                    continue
                segments.append((
                    [int(value) for value in member_way.get("nodes") or []],
                    [[float(point["lon"]), float(point["lat"])] for point in member_way.get("geometry") or []],
                ))
            for ring_index, ring in enumerate(stitch_rings(segments)):
                props = dict(base_properties); props["id"] = f"OSM:relation:{osm_id}:outer:{ring_index}"
                area_features.append(feature({"type": "Polygon", "coordinates": [ring]}, props))
        if power in {"transformer", "switch", "compensator", "converter", "portal"}:
            center = element_center(item)
            if center:
                equipment_features.append(feature({"type": "Point", "coordinates": [center[0], center[1]]}, base_properties))
        if power in {"tower", "pole"} and item_type == "node" and osm_id in hv_line_nodes:
            support_features.append(feature(
                {"type": "Point", "coordinates": [float(item["lon"]), float(item["lat"])]},
                {
                    "id": f"OSM:node:{osm_id}", "power": power,
                    "source": "OpenStreetMap", "source_status": "DIRECT_OSM_GEOMETRY",
                    "object_type": "line_support",
                },
            ))
    return (
        {"type": "FeatureCollection", "features": area_features},
        {"type": "FeatureCollection", "features": equipment_features},
        {"type": "FeatureCollection", "features": support_features},
    )


def main() -> None:
    tables = TABLES
    power_flow = POWER_FLOW
    WEB_DATA.mkdir(parents=True, exist_ok=True)

    buses = pd.read_csv(tables / "buses.csv")
    bus_results = pd.read_csv(power_flow / "bus_results.csv").rename(columns={"Unnamed: 0": "model_index"})
    if len(buses) != len(bus_results):
        raise RuntimeError("Bus result rows do not match the topology bus table")
    buses = pd.concat([buses.reset_index(drop=True), bus_results.reset_index(drop=True)], axis=1)

    lines = pd.read_csv(tables / "lines.csv")
    line_results = pd.read_csv(power_flow / "line_results.csv").rename(columns={"Unnamed: 0": "model_index"})
    if len(lines) != len(line_results):
        raise RuntimeError("Line result rows do not match the topology line table")
    lines = pd.concat([lines.reset_index(drop=True), line_results.reset_index(drop=True)], axis=1)

    transformers = pd.read_csv(tables / "transformers_topology.csv")
    transformer_results = pd.read_csv(power_flow / "transformer_results.csv").rename(columns={"Unnamed: 0": "model_index"})
    if len(transformers) != len(transformer_results):
        raise RuntimeError("Transformer result rows do not match the topology transformer table")
    transformers = pd.concat([transformers.reset_index(drop=True), transformer_results.reset_index(drop=True)], axis=1)

    facilities = pd.read_csv(tables / "facilities.csv")
    generators = pd.read_csv(tables / "generators.csv")
    generator_results_path = power_flow / "generator_operating_points.csv"
    if generator_results_path.exists():
        generator_results = pd.read_csv(generator_results_path)[["generator_id", "p_mw", "q_mvar", "q_status"]]
        generators = generators.drop(columns=["p_mw", "q_mvar"], errors="ignore").merge(generator_results, on="generator_id", how="left")

    bus_coordinates = buses.set_index("bus_id")[["lon", "lat"]].to_dict("index")
    bus_features = []
    for row in buses.to_dict("records"):
        bus_features.append(
            feature(
                {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
                {
                    "id": row["bus_id"], "voltage_kv": row["voltage_kv"],
                    "name": row.get("facility_name"), "facility_type": row.get("facility_type"),
                    "source": row.get("source"), "source_status": row.get("source_status"),
                    "vm_pu": row.get("vm_pu"), "va_degree": row.get("va_degree"),
                    "p_mw": row.get("p_mw"), "q_mvar": row.get("q_mvar"),
                },
            )
        )

    line_features = []
    for row in lines.to_dict("records"):
        coordinates = json.loads(str(row["geometry_json"]))
        loading = row.get("loading_percent")
        flow_mw = max(abs(float(row.get("p_from_mw") or 0.0)), abs(float(row.get("p_to_mw") or 0.0)))
        flow_mvar = max(abs(float(row.get("q_from_mvar") or 0.0)), abs(float(row.get("q_to_mvar") or 0.0)))
        flow_measure = max(flow_mw, flow_mvar)
        if loading is None or pd.isna(loading):
            scenario_flow_status = "NOT_ENERGIZED_IN_ACTIVE_COMPONENT"
        elif flow_measure < 1e-9:
            scenario_flow_status = "ZERO_FLOW_UNDER_CURRENT_SCENARIO"
        elif flow_measure < 0.01:
            scenario_flow_status = "BELOW_0_01_MW"
        else:
            scenario_flow_status = "ACTIVE_FLOW"
        line_features.append(
            feature(
                {"type": "LineString", "coordinates": coordinates},
                {
                    "id": row["line_id"], "name": row.get("name"), "object_type": "line",
                    "voltage_kv": row["voltage_kv"], "source": row.get("source"),
                    "source_status": row.get("source_status"),
                    "parameter_status": row.get("parameter_status"),
                    "from_bus": row["from_bus"], "to_bus": row["to_bus"],
                    "length_km": row.get("length_km"), "parallel": row.get("parallel"),
                    "loading_percent": row.get("loading_percent"),
                    "scenario_flow_status": scenario_flow_status,
                    "p_from_mw": row.get("p_from_mw"), "p_to_mw": row.get("p_to_mw"),
                    "q_from_mvar": row.get("q_from_mvar"), "q_to_mvar": row.get("q_to_mvar"),
                    "pl_mw": row.get("pl_mw"), "ql_mvar": row.get("ql_mvar"), "i_ka": row.get("i_ka"),
                    "max_i_ka": row.get("max_i_ka"),
                },
            )
        )

    transformer_features = []
    for row in transformers.to_dict("records"):
        hv = bus_coordinates[str(row["hv_bus"])]
        lv = bus_coordinates[str(row["lv_bus"])]
        transformer_features.append(
            feature(
                {
                    "type": "Point",
                    "coordinates": [(float(hv["lon"]) + float(lv["lon"])) / 2, (float(hv["lat"]) + float(lv["lat"])) / 2],
                },
                {
                    "id": row["transformer_id"], "hv_kv": row["hv_kv"], "lv_kv": row["lv_kv"],
                    "sn_mva": row["sn_mva"], "source_status": row["source_status"],
                    "parameter_status": row["parameter_status"],
                    "loading_percent": row.get("loading_percent"),
                    "p_hv_mw": row.get("p_hv_mw"), "p_lv_mw": row.get("p_lv_mw"),
                },
            )
        )

    facility_features = []
    for row in facilities.to_dict("records"):
        facility_features.append(
            feature(
                {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
                {
                    "id": row["facility_id"], "name": row.get("name"),
                    "facility_type": row.get("facility_type"), "voltage_kv": row.get("voltage_kv"),
                    "code": row.get("code"), "source": row.get("source"),
                    "source_status": row.get("source_status"), "object_type": "facility",
                },
            )
        )

    generator_features = []
    visible_generators = generators[
        generators["source_status"] != "OUTSIDE_PORTUGAL_CONTINENTAL_MODEL_SCOPE"
    ]
    for row in visible_generators.to_dict("records"):
        tags = json.loads(str(row.get("tags_json") or "{}"))
        generator_features.append(
            feature(
                {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
                {
                    "id": row["generator_id"], "source_id": row.get("source_id"),
                    "name": row.get("name"), "generation_source": row.get("generation_source"),
                    "nameplate_mw": row.get("nameplate_mw"), "operator": tags.get("operator"),
                    "plant_method": tags.get("plant:method") or tags.get("generator:method"),
                    "plant_type": tags.get("plant:type") or tags.get("generator:type"),
                    "bus_id": row.get("bus_id"), "bus_voltage_kv": row.get("bus_voltage_kv"),
                    "match_distance_m": row.get("match_distance_m"), "p_mw": row.get("p_mw"),
                    "q_mvar": row.get("q_mvar"), "source": "OpenStreetMap",
                    "source_status": row.get("source_status"), "dispatch_status": row.get("dispatch_status"),
                    "object_type": "generation_asset",
                },
            )
        )

    boundary_ids = set(pd.read_csv(tables / "scenario_boundaries.csv")["bus_id"].astype(str))
    boundary_features = [item for item in bus_features if str(item["properties"]["id"]) in boundary_ids]
    substation_areas, power_equipment, line_supports = osm_detail_collections()

    collections = {
        "lines.geojson": {"type": "FeatureCollection", "features": line_features},
        "buses.geojson": {"type": "FeatureCollection", "features": bus_features},
        "transformers.geojson": {"type": "FeatureCollection", "features": transformer_features},
        "facilities.geojson": {"type": "FeatureCollection", "features": facility_features},
        "generators.geojson": {"type": "FeatureCollection", "features": generator_features},
        "substation_areas.geojson": substation_areas,
        "power_equipment.geojson": power_equipment,
        "line_supports.geojson": line_supports,
        "boundaries.geojson": {"type": "FeatureCollection", "features": boundary_features},
    }
    for filename, collection in collections.items():
        if filename in {"line_supports.geojson", "power_equipment.geojson", "substation_areas.geojson"}:
            (WEB_DATA / filename).write_text(
                json.dumps(collection, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
            )
        else:
            write_json(WEB_DATA / filename, collection)

    pf = read_json(power_flow / "power_flow_summary.json")
    operating_case = read_json(power_flow / "full_scale_operating_case.json")
    scenario_sweep = pd.read_csv(power_flow / "power_flow_scaling_sweep.csv")
    validation = read_json(VALIDATION / "validation_report.json")
    summary = {
        "generated_at": utc_now(),
        "scenario_scaling": pf["scenario_scaling"],
        "converged": pf["converged"],
        "buses": len(bus_features), "lines": len(line_features),
        "transformers": len(transformer_features),
        "facilities": len(facility_features),
        "substations": int((facilities["facility_type"] == "substation").sum()),
        "switching_stations": int((facilities["facility_type"] == "switching_station").sum()),
        "generation_assets": len(generator_features),
        "osm_substation_areas": len(substation_areas["features"]),
        "osm_power_equipment": len(power_equipment["features"]),
        "osm_hv_line_supports": len(line_supports["features"]),
        "generation_sources": {
            str(key): int(value)
            for key, value in visible_generators["generation_source"].value_counts().sort_index().items()
        },
        "vm_pu_min": pf["vm_pu_min"], "vm_pu_max": pf["vm_pu_max"],
        "line_loading_percent_max": pf["line_loading_percent_max"],
        "trafo_loading_percent_max": pf["trafo_loading_percent_max"],
        "calibration_timestamp_utc": operating_case.get("timestamp_utc"),
        "overloaded_line_rows": operating_case.get("overloaded_line_rows"),
        "overloaded_transformer_rows": operating_case.get("overloaded_transformer_rows"),
        "total_load_p_mw": pf["total_load_p_mw"],
        "total_generation_p_mw": pf["total_generation_p_mw"],
        "total_ext_grid_p_mw": pf["total_ext_grid_p_mw"],
        "losses_p_mw": pf["losses_p_mw"],
        "scenario_sweep": scenario_sweep.where(pd.notna(scenario_sweep), None).to_dict("records"),
        "validation_status": validation["overall_status"],
        "important_scope": "Full-scale public-data-informed study case at the declared timestamp with observed net import held out. Asset-level dispatch, reactive power, unobserved equipment parameters and displayed branch flows remain evidence-ranked estimates or solved values rather than operator telemetry.",
    }
    write_json(WEB_DATA / "summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
