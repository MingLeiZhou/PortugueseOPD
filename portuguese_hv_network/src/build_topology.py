#!/usr/bin/env python3
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from common import PROJECT, RAW, TABLES, element_center, ensure_dirs, haversine_m, normalize_name, parse_osm_voltage_values, parse_voltage_values, polyline_length_km, read_json, utc_now, write_json


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        a, b = self.find(left), self.find(right)
        if a != b:
            self.parent[b] = a


def metric_xy(points: list[tuple[float, float]]) -> np.ndarray:
    try:
        from pyproj import Transformer

        transformer = Transformer.from_crs("EPSG:4326", "EPSG:3763", always_xy=True)
        return np.array([transformer.transform(lon, lat) for lon, lat in points], dtype=float)
    except ImportError:
        # Deterministic fallback for environments not yet provisioned with
        # pyproj. Validation records which implementation was used.
        mean_lat = np.deg2rad(np.mean([lat for _, lat in points]) if points else 39.5)
        return np.array([(lon * 111320.0 * np.cos(mean_lat), lat * 110574.0) for lon, lat in points], dtype=float)


def metric_backend() -> str:
    try:
        import pyproj  # noqa: F401
        return "EPSG:3763 (ETRS89 / Portugal TM06) via pyproj"
    except ImportError:
        return "declared equirectangular fallback because pyproj is unavailable"


def geometry_parts(geometry: dict[str, Any]) -> list[list[list[float]]]:
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "LineString":
        return [coords]
    if kind == "MultiLineString":
        return coords
    return []


def load_eredes(allowed: set[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    lines_data = read_json(RAW / "eredes" / "rede-at-teste.geojson")
    line_rows: list[dict[str, Any]] = []
    for feature in lines_data.get("features", []):
        props = feature.get("properties") or {}
        voltages = parse_voltage_values(props.get("tensao_de"), allowed)
        for part_index, coords in enumerate(geometry_parts(feature.get("geometry") or {})):
            if len(coords) < 2:
                continue
            for voltage in voltages:
                line_rows.append(
                    {
                        "source_line_id": f"EREDES:{props.get('id', props.get('codigo_da', 'unknown'))}:{part_index}:{voltage}",
                        "source": "E-REDES",
                        "source_status": "DIRECT_EREDES",
                        "voltage_kv": voltage,
                        "asset_type": "cable" if "sub" in str(props.get("tipo", "")).lower() else "overhead",
                        "operational_status": props.get("situacao", ""),
                        "name": props.get("codigo_da", ""),
                        "operator": "E-REDES",
                        "circuits": "",
                        "coords": coords,
                        "length_km": polyline_length_km(coords),
                    }
                )
    facilities: list[dict[str, Any]] = []
    for dataset, kind in (("se-at_2025", "substation"), ("pc-at_2025", "switching_station")):
        data = read_json(RAW / "eredes" / f"{dataset}.geojson")
        for feature in data.get("features", []):
            props = feature.get("properties") or {}
            coords = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2:
                continue
            facilities.append(
                {
                    "facility_id": f"EREDES:{props.get('codigo')}",
                    "source": "E-REDES",
                    "source_status": "DIRECT_EREDES",
                    "facility_type": kind,
                    "name": props.get("instalacao", ""),
                    "normalized_name": normalize_name(props.get("instalacao", "")),
                    "code": props.get("codigo", ""),
                    "voltage_kv": 60,
                    "lon": float(coords[0]),
                    "lat": float(coords[1]),
                    "tags_json": "{}",
                }
            )
    return line_rows, facilities


def canonicalize_eredes_facilities(facilities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    """Resolve duplicate/temporary E-REDES point representations audibly."""
    kept: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    substations = [row for row in facilities if row["facility_type"] == "substation" and "movel" not in row["normalized_name"]]
    for row in facilities:
        reason = ""
        canonical = ""
        if row["facility_id"] in seen_ids:
            reason = "DUPLICATE_FACILITY_IDENTIFIER"
        elif "movel" in row["normalized_name"]:
            nearby = sorted(
                ((haversine_m((row["lon"], row["lat"]), (candidate["lon"], candidate["lat"])), candidate) for candidate in substations),
                key=lambda item: item[0],
            )
            if nearby and nearby[0][0] <= 250.0:
                reason = "TEMPORARY_MOBILE_POINT_COLOCATED_WITH_PERMANENT_SUBSTATION"
                canonical = str(nearby[0][1]["facility_id"])
        elif row["facility_type"] == "switching_station":
            name_matches = [candidate for candidate in substations if candidate["normalized_name"] == row["normalized_name"] and row["normalized_name"]]
            if name_matches:
                distance, candidate = min(
                    ((haversine_m((row["lon"], row["lat"]), (candidate["lon"], candidate["lat"])), candidate) for candidate in name_matches),
                    key=lambda item: item[0],
                )
                if distance <= 250.0:
                    reason = "SAME_NAME_COLOCATED_SWITCHING_AND_SUBSTATION_RECORD"
                    canonical = str(candidate["facility_id"])
        if reason:
            ledger.append({
                "excluded_facility_id": row["facility_id"], "excluded_name": row["name"],
                "canonical_facility_id": canonical, "reason": reason,
            })
            continue
        seen_ids.add(str(row["facility_id"]))
        kept.append(row)
    return kept, pd.DataFrame(ledger)


def osm_way_relation_context(data: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """Resolve circuit -> line_section -> way without treating the relations as geometry."""
    relations = {
        int(element["id"]): element
        for element in data.get("elements", [])
        if element.get("type") == "relation"
        and (element.get("tags") or {}).get("power") in {"circuit", "line_section"}
    }
    contexts: dict[int, list[dict[str, Any]]] = defaultdict(list)

    def add_relation_ways(
        relation_id: int,
        circuit_id: int | None,
        circuit_tags: dict[str, Any],
        section_id: int | None,
        section_tags: dict[str, Any],
        visited: set[int],
    ) -> None:
        if relation_id in visited:
            return
        visited.add(relation_id)
        relation = relations.get(relation_id)
        if relation is None:
            return
        tags = relation.get("tags") or {}
        if tags.get("power") == "line_section":
            section_id, section_tags = relation_id, tags
        for member in relation.get("members", []):
            if member.get("type") == "way":
                contexts[int(member["ref"])].append(
                    {
                        "circuit_id": circuit_id,
                        "circuit_tags": circuit_tags,
                        "line_section_id": section_id,
                        "line_section_tags": section_tags,
                        "member_role": member.get("role", ""),
                    }
                )
            elif member.get("type") == "relation":
                add_relation_ways(
                    int(member["ref"]), circuit_id, circuit_tags,
                    section_id, section_tags, visited,
                )

    for relation_id, relation in relations.items():
        tags = relation.get("tags") or {}
        if tags.get("power") == "circuit":
            add_relation_ways(relation_id, relation_id, tags, None, {}, set())
    # Preserve line sections which are not currently members of a circuit.
    covered_sections = {
        int(context["line_section_id"])
        for values in contexts.values() for context in values
        if context.get("line_section_id") not in (None, "")
    }
    for relation_id, relation in relations.items():
        if (relation.get("tags") or {}).get("power") == "line_section" and relation_id not in covered_sections:
            add_relation_ways(relation_id, None, {}, relation_id, relation.get("tags") or {}, set())
    return contexts


def relation_value(contexts: list[dict[str, Any]], key: str) -> str:
    values: list[str] = []
    for context in contexts:
        for tag_set in (context.get("line_section_tags") or {}, context.get("circuit_tags") or {}):
            value = str(tag_set.get(key, "")).strip()
            if value and value not in values:
                values.append(value)
    return ";".join(values)


def duplicate_circuit_canonical_ids(data: dict[str, Any]) -> dict[int, int]:
    """Collapse only relations with identical members and circuit identity."""
    canonical: dict[tuple[object, ...], int] = {}
    mapping: dict[int, int] = {}
    for element in data.get("elements", []):
        tags = element.get("tags") or {}
        if element.get("type") != "relation" or tags.get("power") != "circuit":
            continue
        members = tuple(sorted((member.get("type"), int(member.get("ref")), member.get("role", "")) for member in element.get("members", [])))
        key = (members, normalize_name(tags.get("name", "")), str(tags.get("ref", "")), str(tags.get("voltage", "")), normalize_name(tags.get("operator", "")))
        relation_id = int(element["id"])
        if key in canonical:
            mapping[relation_id] = canonical[key]
        else:
            canonical[key] = relation_id
            mapping[relation_id] = relation_id
    return mapping


def load_osm(allowed: set[int], primary: set[int]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    source_path = RAW / "osm" / "portugal_power_osm.json"
    if not source_path.exists():
        source_path = RAW / "osm" / "portugal_hv_osm.json"
    data = read_json(source_path)
    line_rows: list[dict[str, Any]] = []
    facilities: list[dict[str, Any]] = []
    transformers: list[dict[str, Any]] = []
    generation: list[dict[str, Any]] = []
    relation_context = osm_way_relation_context(data)
    canonical_circuit = duplicate_circuit_canonical_ids(data)
    line_elements: list[tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]], list[int], list[list[float]], list[int]]] = []
    shared_nodes: dict[int, Counter[int]] = defaultdict(Counter)
    for element in data.get("elements", []):
        tags = element.get("tags") or {}
        power = tags.get("power", "")
        contexts = relation_context.get(int(element.get("id", -1)), []) if element.get("type") == "way" else []
        relation_line_contexts = [
            context for context in contexts
            if str(context.get("member_role", "")).lower() in {"", "line"}
        ]
        relation_inherited_line = bool(relation_line_contexts) and power not in {
            "substation", "transformer", "plant", "generator", "switch", "tower", "pole", "portal"
        }
        if (power in {"line", "minor_line", "cable"} or relation_inherited_line) and element.get("type") == "way":
            contexts = relation_line_contexts
            declared_voltage = tags.get("voltage") or relation_value(contexts, "voltage")
            voltages = [value for value in parse_osm_voltage_values(declared_voltage, allowed) if value in primary]
            coords = [[point["lon"], point["lat"]] for point in element.get("geometry") or []]
            node_ids = [int(value) for value in element.get("nodes") or []]
            if len(coords) < 2 or len(node_ids) != len(coords):
                continue
            line_elements.append((element, tags, contexts, voltages, coords, node_ids))
            for voltage in voltages:
                shared_nodes[voltage].update(node_ids)
    # OSM ways can connect at an intermediate shared node. Split only at those
    # explicit OSM junctions (and at way endpoints), not at visual crossings.
    for element, tags, contexts, voltages, coords, node_ids in line_elements:
        element_id = f"OSM:{element.get('type')}:{element.get('id')}"
        circuit_ids = sorted({canonical_circuit.get(int(context["circuit_id"]), int(context["circuit_id"])) for context in contexts if context.get("circuit_id")})
        section_ids = sorted({int(context["line_section_id"]) for context in contexts if context.get("line_section_id")})
        inherited = bool(contexts) and not tags.get("voltage")
        for voltage in voltages:
            breaks = sorted({0, len(node_ids) - 1} | {index for index, node_id in enumerate(node_ids) if shared_nodes[voltage][node_id] > 1})
            for part_index, (start, end) in enumerate(zip(breaks, breaks[1:])):
                part = coords[start : end + 1]
                if len(part) < 2:
                    continue
                line_rows.append(
                    {
                        "source_line_id": f"{element_id}:{voltage}:{part_index}",
                        "source": "OpenStreetMap",
                        "source_status": "DIRECT_OSM_RELATION_CONTEXT" if inherited else "DIRECT_OSM",
                        "voltage_kv": voltage,
                        "asset_type": "cable" if tags.get("power") == "cable" else "overhead",
                        "operational_status": tags.get("construction", tags.get("status", "")) or relation_value(contexts, "status"),
                        "name": tags.get("name", tags.get("ref", "")) or relation_value(contexts, "name") or relation_value(contexts, "ref"),
                        "operator": tags.get("operator", "") or relation_value(contexts, "operator"),
                        "circuits": tags.get("circuits", "") or relation_value(contexts, "circuits") or (str(len(circuit_ids)) if circuit_ids else ""),
                        "osm_way_id": int(element.get("id")),
                        "osm_circuit_ids": ";".join(map(str, circuit_ids)),
                        "osm_line_section_ids": ";".join(map(str, section_ids)),
                        "relation_identity_status": "CIRCUIT_AND_LINE_SECTION" if circuit_ids and section_ids else "LINE_SECTION_ONLY" if section_ids else "WAY_ONLY",
                        "coords": part,
                        "length_km": polyline_length_km(part),
                    }
                )
    for element in data.get("elements", []):
        tags = element.get("tags") or {}
        power = tags.get("power", "")
        if element.get("type") == "area" and element.get("osm_source_type") == "way":
            # The corresponding tagged way is already present and has a usable
            # centre; retain relation-derived areas only.
            continue
        original_id = element.get("osm_original_id", element.get("id"))
        element_id = f"OSM:{element.get('osm_source_type', element.get('type'))}:{original_id}"
        if power == "substation":
            center = element_center(element)
            if center is None:
                continue
            # E-REDES is the canonical 60 kV facility source.  Keeping a
            # second OSM 60 kV bus for the same physical substation disconnects
            # code-joined E-REDES loads from lines snapped to the OSM polygon
            # centre.  OSM 60 kV facilities remain in the raw evidence, while
            # OSM supplies model buses for RNT voltage levels and the sparse
            # 130 kV transition layer.
            voltages = [
                voltage for voltage in parse_osm_voltage_values(tags.get("voltage"), allowed)
                if voltage in primary or voltage == 130
            ]
            for voltage in voltages:
                facilities.append(
                    {
                        "facility_id": element_id,
                        "source": "OpenStreetMap",
                        "source_status": "DIRECT_OSM",
                        "facility_type": "substation",
                        "name": tags.get("name", tags.get("ref", "")),
                        "normalized_name": normalize_name(tags.get("name", tags.get("ref", ""))),
                        "code": tags.get("ref", ""),
                        "voltage_kv": voltage,
                        "lon": center[0],
                        "lat": center[1],
                        "tags_json": json.dumps(tags, ensure_ascii=False, sort_keys=True),
                    }
                )
        elif power == "transformer":
            center = element_center(element)
            if center:
                transformers.append({"source_id": element_id, "lon": center[0], "lat": center[1], "tags": tags})
        elif power in {"plant", "generator"}:
            center = element_center(element)
            if center:
                generation.append({"source_id": element_id, "lon": center[0], "lat": center[1], "tags": tags})
    return line_rows, facilities, transformers, generation


def cluster_endpoint_indexes(line_rows: list[dict[str, Any]], snap_m: float) -> tuple[list[dict[str, Any]], dict[tuple[int, str], int]]:
    endpoint_rows: list[dict[str, Any]] = []
    for line_index, row in enumerate(line_rows):
        for side, coord in (("from", row["coords"][0]), ("to", row["coords"][-1])):
            endpoint_rows.append(
                {"line_index": line_index, "side": side, "voltage_kv": row["voltage_kv"], "lon": float(coord[0]), "lat": float(coord[1])}
            )
    assignment: dict[tuple[int, str], int] = {}
    clusters: list[dict[str, Any]] = []
    for voltage in sorted({int(row["voltage_kv"]) for row in endpoint_rows}):
        subset_indexes = [index for index, row in enumerate(endpoint_rows) if int(row["voltage_kv"]) == voltage]
        points = [(endpoint_rows[index]["lon"], endpoint_rows[index]["lat"]) for index in subset_indexes]
        xy = metric_xy(points)
        uf = UnionFind(len(points))
        for left, right in cKDTree(xy).query_pairs(snap_m):
            uf.union(left, right)
        groups: dict[int, list[int]] = defaultdict(list)
        for local_index in range(len(points)):
            groups[uf.find(local_index)].append(local_index)
        for members in groups.values():
            cluster_index = len(clusters)
            center_lon = float(np.mean([points[index][0] for index in members]))
            center_lat = float(np.mean([points[index][1] for index in members]))
            clusters.append({"cluster_index": cluster_index, "voltage_kv": voltage, "lon": center_lon, "lat": center_lat})
            for local_index in members:
                endpoint = endpoint_rows[subset_indexes[local_index]]
                assignment[(int(endpoint["line_index"]), str(endpoint["side"]))] = cluster_index
    return clusters, assignment


def attach_facilities(
    clusters: list[dict[str, Any]], facilities: list[dict[str, Any]], max_distance_m: float
) -> tuple[list[dict[str, Any]], dict[int, str]]:
    buses: list[dict[str, Any]] = []
    cluster_to_bus: dict[int, str] = {}
    used_facility_voltage: set[tuple[str, int]] = set()
    facilities_by_voltage: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for facility in facilities:
        facilities_by_voltage[int(facility["voltage_kv"])].append(facility)
    for cluster in clusters:
        candidates = facilities_by_voltage.get(int(cluster["voltage_kv"]), [])
        best: dict[str, Any] | None = None
        best_distance = float("inf")
        for facility in candidates:
            distance = haversine_m((cluster["lon"], cluster["lat"]), (facility["lon"], facility["lat"]))
            if distance < best_distance:
                best, best_distance = facility, distance
        if best is not None and best_distance <= max_distance_m:
            bus_id = f"BUS:{best['facility_id']}:{int(cluster['voltage_kv'])}"
            used_facility_voltage.add((str(best["facility_id"]), int(cluster["voltage_kv"])))
            row = {
                "bus_id": bus_id,
                "voltage_kv": int(cluster["voltage_kv"]),
                "lon": best["lon"], "lat": best["lat"],
                "facility_id": best["facility_id"], "facility_name": best["name"], "facility_code": best["code"],
                "facility_type": best["facility_type"], "source": best["source"], "source_status": best["source_status"],
                "endpoint_match_distance_m": best_distance,
            }
        else:
            bus_id = f"BUS:JUNCTION:{int(cluster['voltage_kv'])}:{int(cluster['cluster_index']):05d}"
            row = {
                "bus_id": bus_id, "voltage_kv": int(cluster["voltage_kv"]),
                "lon": cluster["lon"], "lat": cluster["lat"], "facility_id": "", "facility_name": "",
                "facility_code": "", "facility_type": "line_endpoint_cluster", "source": "derived",
                "source_status": "ENDPOINT_CLUSTER_DERIVED", "endpoint_match_distance_m": "",
            }
        cluster_to_bus[int(cluster["cluster_index"])] = bus_id
        if not any(existing["bus_id"] == bus_id for existing in buses):
            buses.append(row)
    for facility in facilities:
        key = (str(facility["facility_id"]), int(facility["voltage_kv"]))
        if key in used_facility_voltage:
            continue
        buses.append(
            {
                "bus_id": f"BUS:{facility['facility_id']}:{int(facility['voltage_kv'])}",
                "voltage_kv": int(facility["voltage_kv"]), "lon": facility["lon"], "lat": facility["lat"],
                "facility_id": facility["facility_id"], "facility_name": facility["name"], "facility_code": facility["code"],
                "facility_type": facility["facility_type"], "source": facility["source"],
                "source_status": facility["source_status"], "endpoint_match_distance_m": "",
            }
        )
    return buses, cluster_to_bus


def main() -> None:
    ensure_dirs()
    config = read_json(PROJECT / "config" / "model_config.json")
    allowed = set(config["voltage_levels_kv"])
    eredes_lines, eredes_facilities = load_eredes(allowed)
    eredes_facilities, facility_ledger = canonicalize_eredes_facilities(eredes_facilities)
    osm_lines, osm_facilities, osm_transformers, osm_generation = load_osm(allowed, set(config["osm_primary_voltage_levels_kv"]))
    lines = eredes_lines + osm_lines
    facilities = eredes_facilities + osm_facilities
    clusters, endpoint_assignment = cluster_endpoint_indexes(lines, float(config["endpoint_snap_m"]))
    buses, cluster_to_bus = attach_facilities(clusters, facilities, float(config["facility_match_m"]))
    output_lines: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for index, row in enumerate(lines):
        from_bus = cluster_to_bus[endpoint_assignment[(index, "from")]]
        to_bus = cluster_to_bus[endpoint_assignment[(index, "to")]]
        record = {key: value for key, value in row.items() if key != "coords"}
        record.update(
            {
                "line_id": f"LINE:{index:06d}", "from_bus": from_bus, "to_bus": to_bus,
                "geometry_json": json.dumps(row["coords"], separators=(",", ":")),
            }
        )
        if from_bus == to_bus or float(row["length_km"]) <= 0:
            record["blocking_reason"] = "SELF_LOOP_AFTER_ENDPOINT_CLUSTERING"
            blocked.append(record)
        else:
            output_lines.append(record)
    pd.DataFrame(buses).drop_duplicates("bus_id").to_csv(TABLES / "buses.csv", index=False)
    pd.DataFrame(output_lines).to_csv(TABLES / "lines_topology.csv", index=False)
    pd.DataFrame(blocked).to_csv(TABLES / "lines_blocked.csv", index=False)
    pd.DataFrame(facilities).to_csv(TABLES / "facilities.csv", index=False)
    facility_ledger.to_csv(TABLES / "facility_canonicalization_ledger.csv", index=False)
    write_json(RAW / "osm" / "transformer_elements.json", osm_transformers)
    write_json(RAW / "osm" / "generation_elements.json", osm_generation)
    summary = {
        "generated_at": utc_now(), "line_inputs": len(lines), "lines_retained": len(output_lines),
        "lines_blocked": len(blocked), "buses": len({row["bus_id"] for row in buses}),
        "facilities": len(facilities), "canonicalized_eredes_facilities": len(facility_ledger),
        "voltage_counts": pd.Series([row["voltage_kv"] for row in output_lines]).value_counts().sort_index().to_dict(),
        "relation_identity_counts": pd.Series([row.get("relation_identity_status", "NOT_APPLICABLE") for row in output_lines]).value_counts().to_dict(),
        "metric_crs": metric_backend(),
    }
    write_json(TABLES / "topology_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
