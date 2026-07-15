import argparse
import html
import json
import math
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

import networkx as nx
import numpy as np
import pandas as pd

import config
from metric_projection import PortugalTM06Projection
from utils import ensure_directories, markdown_table, utc_now, write_json, write_text


FACILITY_BUFFERS_M = [50, 100, 250, 500]
ENDPOINT_SNAP_THRESHOLDS_M = [0.5, 1, 5, 10, 25, 50]
MERGE_MODES = ["geometry-only", "voltage-aware", "voltage-status-aware"]
STATUS_ACTIVE_KEYWORDS = ("explor", "servico", "serviço")


class UnionFind:
    def __init__(self, items: list[str]):
        self.parent = {item: item for item in items}
        self.rank = {item: 0 for item in items}

    def find(self, item: str) -> str:
        parent = self.parent[item]
        if parent != item:
            self.parent[item] = self.find(parent)
        return self.parent[item]

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.rank[ra] < self.rank[rb]:
            self.parent[ra] = rb
        elif self.rank[ra] > self.rank[rb]:
            self.parent[rb] = ra
        else:
            self.parent[rb] = ra
            self.rank[ra] += 1


def normalize_text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def normalize_voltage(value: Any) -> str:
    text = normalize_text(value).lower().replace(" ", "")
    return text


def normalize_status(value: Any) -> str:
    return normalize_text(value).lower()


def status_is_active(value: Any) -> bool:
    text = normalize_status(value)
    return any(keyword in text for keyword in STATUS_ACTIVE_KEYWORDS)


def voltage_compatible(a: Any, b: Any) -> bool:
    va, vb = normalize_voltage(a), normalize_voltage(b)
    return not va or not vb or va == vb


def status_compatible(a: Any, b: Any) -> bool:
    sa, sb = normalize_status(a), normalize_status(b)
    return not sa or not sb or sa == sb or (status_is_active(sa) and status_is_active(sb))


def circle_polygon(projection: Any, x: float, y: float, radius_m: float, n=32):
    coords = []
    for i in range(n + 1):
        angle = 2 * math.pi * i / n
        lon, lat = projection.lonlat(x + radius_m * math.cos(angle), y + radius_m * math.sin(angle))
        coords.append([lon, lat])
    return coords


def geojson_feature(geometry: dict[str, Any], properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "Feature", "geometry": geometry, "properties": properties}


def write_geojson(path: Path, features: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"type": "FeatureCollection", "features": features}
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read_csv_auto(path: Path) -> pd.DataFrame:
    first_line = path.open("r", encoding="utf-8-sig", errors="replace").readline()
    sep = ";" if first_line.count(";") > first_line.count(",") else ","
    return pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)


def parse_lonlat_from_point_geometry(geometry: dict[str, Any]) -> tuple[float, float] | None:
    if not geometry or geometry.get("type") != "Point":
        return None
    coords = geometry.get("coordinates") or []
    if len(coords) < 2:
        return None
    return float(coords[0]), float(coords[1])


def geometry_parts(geometry: dict[str, Any]) -> list[list[list[float]]]:
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if geom_type == "LineString":
        return [coords] if len(coords) >= 2 else []
    if geom_type == "MultiLineString":
        return [part for part in coords if len(part) >= 2]
    return []


def flatten_parts(parts: list[list[list[float]]]) -> list[list[float]]:
    flattened = []
    for part in parts:
        flattened.extend(part)
    return flattened


def projected_length(parts: list[list[list[float]]], projection: Any) -> float:
    total = 0.0
    for part in parts:
        points = [projection.xy(float(lon), float(lat)) for lon, lat, *_ in part]
        for a, b in zip(points, points[1:]):
            total += math.hypot(b[0] - a[0], b[1] - a[1])
    return total


def load_at_lines() -> tuple[pd.DataFrame, dict[str, list[list[list[float]]]], dict[str, Any]]:
    source_path = config.RAW_DIR / "rede-at-teste.geojson"
    data = json.loads(source_path.read_text(encoding="utf-8"))
    rows = []
    shapes: dict[str, list[list[list[float]]]] = {}
    invalid = 0
    repaired = 0
    for idx, feature in enumerate(data.get("features", [])):
        props = feature.get("properties") or {}
        raw_geometry = feature.get("geometry") or {}
        raw_part_count = 0
        if raw_geometry.get("type") == "LineString":
            raw_part_count = 1
        elif raw_geometry.get("type") == "MultiLineString":
            raw_part_count = len(raw_geometry.get("coordinates") or [])
        parts = geometry_parts(raw_geometry)
        if raw_part_count and len(parts) < raw_part_count:
            repaired += 1
        if not parts:
            invalid += 1
            continue
        flattened = flatten_parts(parts)
        source_line_id = str(props.get("id") or idx)
        line_id = f"ATSEG_{idx:05d}_{source_line_id}"
        shapes[line_id] = parts
        rows.append(
            {
                "line_id": line_id,
                "source_line_id": source_line_id,
                "feature_index": idx,
                "geometry_type": raw_geometry.get("type"),
                "geometry_part_count": len(parts),
                "start_lon": float(flattened[0][0]),
                "start_lat": float(flattened[0][1]),
                "end_lon": float(flattened[-1][0]),
                "end_lat": float(flattened[-1][1]),
                "voltage": props.get("tensao_de"),
                "type": props.get("tipo"),
                "status": props.get("situacao"),
                "line_code": props.get("codigo_da"),
                "linha_mt_i": props.get("linha_mt_i"),
                "municipality_code": props.get("con_code"),
                "district_code": props.get("dis_code"),
                "municipality": props.get("con_name"),
                "district": props.get("dis_name"),
            }
        )
    lines = pd.DataFrame(rows)
    report = {
        "raw_feature_count": len(data.get("features", [])),
        "valid_line_geometries": int(len(lines)),
        "invalid_or_dropped_geometries": int(invalid),
        "simple_repaired_geometries": int(repaired),
        "source_path": str(source_path),
    }
    return lines, shapes, report


def load_facility_file(path: Path, source_dataset: str, facility_type: str) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    seen = set()
    for idx, feature in enumerate(data.get("features", [])):
        props = feature.get("properties") or {}
        point = parse_lonlat_from_point_geometry(feature.get("geometry") or {})
        if point is None:
            continue
        code = str(props.get("codigo") or props.get("cod_instalacao") or f"{facility_type}_{idx}")
        name = str(props.get("instalacao") or props.get("name") or props.get("nome") or code)
        key = (facility_type, code, round(point[0], 8), round(point[1], 8))
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "facility_uid": f"{facility_type}:{code}",
                "facility_code": code,
                "facility_name": name,
                "facility_type": facility_type,
                "source_dataset": source_dataset,
                "lon": point[0],
                "lat": point[1],
                "voltage": props.get("tensao") or props.get("tension") or "",
                "district": props.get("distrito") or props.get("district") or props.get("dis_name") or "",
                "municipality": props.get("concelho") or props.get("county") or props.get("con_name") or "",
            }
        )
    return rows


def load_facilities() -> pd.DataFrame:
    rows = []
    facility_sources = [
        ("se-at_2025.geojson", "se-at_2025", "SE_AT"),
        ("pc-at_2025.geojson", "pc-at_2025", "PC_AT"),
        ("se-mt_2025.geojson", "se-mt_2025", "SE_MT"),
        ("pc-mt_2025.geojson", "pc-mt_2025", "PC_MT"),
    ]
    for filename, dataset, facility_type in facility_sources:
        path = config.RAW_DIR / filename
        if path.exists():
            rows.extend(load_facility_file(path, dataset, facility_type))
    facilities = pd.DataFrame(rows)
    return facilities.drop_duplicates(subset=["facility_uid", "lon", "lat"]).reset_index(drop=True)


def build_projection(lines: pd.DataFrame, facilities: pd.DataFrame) -> PortugalTM06Projection:
    """Return the formal metric CRS used by PT60-Candidate v1.0.2+."""

    return PortugalTM06Projection()


def add_projected_columns(
    lines: pd.DataFrame,
    facilities: pd.DataFrame,
    shapes: dict[str, list[list[list[float]]]],
    projection: Any,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    lines = lines.copy()
    facilities = facilities.copy()
    for endpoint in ["start", "end"]:
        xy = lines.apply(
            lambda row: projection.xy(float(row[f"{endpoint}_lon"]), float(row[f"{endpoint}_lat"])),
            axis=1,
        )
        lines[f"{endpoint}_x"] = xy.apply(lambda value: value[0])
        lines[f"{endpoint}_y"] = xy.apply(lambda value: value[1])
    facility_xy = facilities.apply(lambda row: projection.xy(float(row["lon"]), float(row["lat"])), axis=1)
    facilities["x"] = facility_xy.apply(lambda value: value[0])
    facilities["y"] = facility_xy.apply(lambda value: value[1])
    lines["length_m"] = lines["line_id"].map(
        {line_id: projected_length(parts, projection) for line_id, parts in shapes.items()}
    )
    return lines, facilities


def node_set_facilities(facilities: pd.DataFrame, node_set: str) -> pd.DataFrame:
    if node_set == "A":
        types = {"SE_AT"}
    elif node_set == "B":
        types = {"SE_AT", "PC_AT"}
    elif node_set == "C":
        types = {"SE_AT", "PC_AT", "SE_MT", "PC_MT"}
    else:
        raise ValueError(node_set)
    return facilities[facilities["facility_type"].isin(types)].copy()


def spatial_bucket(points: list[tuple[float, float, Any]], cell_size: float) -> dict[tuple[int, int], list[tuple[float, float, Any]]]:
    buckets: dict[tuple[int, int], list[tuple[float, float, Any]]] = defaultdict(list)
    if cell_size <= 0:
        cell_size = 1
    for x, y, payload in points:
        buckets[(math.floor(x / cell_size), math.floor(y / cell_size))].append((x, y, payload))
    return buckets


def nearby_payloads(
    buckets: dict[tuple[int, int], list[tuple[float, float, Any]]],
    x: float,
    y: float,
    radius: float,
    cell_size: float,
) -> list[tuple[float, float, Any, float]]:
    cx, cy = math.floor(x / cell_size), math.floor(y / cell_size)
    span = max(1, math.ceil(radius / cell_size))
    matches = []
    for ix in range(cx - span, cx + span + 1):
        for iy in range(cy - span, cy + span + 1):
            for px, py, payload in buckets.get((ix, iy), []):
                dist = math.hypot(px - x, py - y)
                if dist <= radius:
                    matches.append((px, py, payload, dist))
    return matches


def footprint_overlap_summary(facility_set: pd.DataFrame, buffer_m: float) -> dict[str, Any]:
    points = [(row.x, row.y, row.facility_uid) for row in facility_set.itertuples(index=False)]
    buckets = spatial_bucket(points, max(buffer_m * 2, 1))
    overlaps = set()
    nearby_25m = set()
    for x, y, uid in points:
        for _, _, other_uid, dist in nearby_payloads(buckets, x, y, buffer_m * 2, max(buffer_m * 2, 1)):
            if uid >= other_uid:
                continue
            if dist <= buffer_m * 2:
                overlaps.add((uid, other_uid))
            if dist <= 25:
                nearby_25m.add((uid, other_uid))
    return {
        "overlapping_footprint_pairs": len(overlaps),
        "duplicate_or_nearby_facility_pairs_25m": len(nearby_25m),
    }


def build_facility_footprints(
    facilities: pd.DataFrame,
    projection: Any,
) -> tuple[list[dict[str, Any]], pd.DataFrame]:
    features = []
    rows = []
    for node_set in ["A", "B", "C"]:
        facility_set = node_set_facilities(facilities, node_set)
        for buffer_m in FACILITY_BUFFERS_M:
            overlap = footprint_overlap_summary(facility_set, buffer_m)
            for row in facility_set.itertuples(index=False):
                features.append(
                    geojson_feature(
                        {
                            "type": "Polygon",
                            "coordinates": [circle_polygon(projection, float(row.x), float(row.y), buffer_m)],
                        },
                        {
                            "node_set": node_set,
                            "buffer_m": buffer_m,
                            "facility_uid": row.facility_uid,
                            "facility_code": row.facility_code,
                            "facility_name": row.facility_name,
                            "facility_type": row.facility_type,
                            "source_dataset": row.source_dataset,
                        },
                    )
                )
            rows.append(
                {
                    "facility_node_set": node_set,
                    "facility_buffer_m": buffer_m,
                    "facility_count": len(facility_set),
                    **overlap,
                    "spatially_reasonable": overlap["overlapping_footprint_pairs"] < len(facility_set) * 0.5
                    or buffer_m <= 100,
                }
            )
    write_geojson(config.PROCESSED_DIR / "at_facility_footprints.geojson", features)
    return features, pd.DataFrame(rows)


def build_base_endpoints(lines: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for row in lines.itertuples(index=False):
        for role in ["start", "end"]:
            rows.append(
                {
                    "endpoint_id": f"{row.line_id}:{role}",
                    "line_id": row.line_id,
                    "source_line_id": row.source_line_id,
                    "endpoint_role": role,
                    "lon": getattr(row, f"{role}_lon"),
                    "lat": getattr(row, f"{role}_lat"),
                    "x": getattr(row, f"{role}_x"),
                    "y": getattr(row, f"{role}_y"),
                    "voltage": row.voltage,
                    "type": row.type,
                    "status": row.status,
                }
            )
    return pd.DataFrame(rows)


def facility_matcher(facility_set: pd.DataFrame, buffer_m: float):
    points = [(row.x, row.y, row._asdict()) for row in facility_set.itertuples(index=False)]
    buckets = spatial_bucket(points, max(buffer_m, 1))

    def match(x: float, y: float) -> dict[str, Any]:
        matches = nearby_payloads(buckets, x, y, buffer_m, max(buffer_m, 1))
        if not matches:
            return {
                "facility_uid": "",
                "facility_code": "",
                "facility_name": "",
                "facility_type": "",
                "facility_distance_m": np.nan,
                "inside_facility": False,
                "ambiguous_facility_match": False,
                "candidate_facility_count": 0,
            }
        matches.sort(key=lambda item: item[3])
        selected = matches[0][2]
        return {
            "facility_uid": selected["facility_uid"],
            "facility_code": selected["facility_code"],
            "facility_name": selected["facility_name"],
            "facility_type": selected["facility_type"],
            "facility_distance_m": round(float(matches[0][3]), 3),
            "inside_facility": True,
            "ambiguous_facility_match": len(matches) > 1,
            "candidate_facility_count": len(matches),
        }

    return match


def endpoint_facility_membership(base_endpoints: pd.DataFrame, facilities: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    all_rows = []
    summary_rows = []
    total_endpoints = len(base_endpoints)
    for node_set in ["A", "B", "C"]:
        facility_set = node_set_facilities(facilities, node_set)
        for buffer_m in FACILITY_BUFFERS_M:
            matcher = facility_matcher(facility_set, buffer_m)
            rows = []
            for endpoint in base_endpoints.itertuples(index=False):
                match = matcher(float(endpoint.x), float(endpoint.y))
                rows.append(
                    {
                        **endpoint._asdict(),
                        "facility_node_set": node_set,
                        "facility_buffer_m": buffer_m,
                        **match,
                    }
                )
            membership = pd.DataFrame(rows)
            all_rows.append(membership)
            line_pivot = membership.pivot(index="line_id", columns="endpoint_role", values="inside_facility")
            uid_pivot = membership.pivot(index="line_id", columns="endpoint_role", values="facility_uid")
            both = line_pivot["start"] & line_pivot["end"]
            one = line_pivot["start"] ^ line_pivot["end"]
            none = ~(line_pivot["start"] | line_pivot["end"])
            same = both & (uid_pivot["start"] == uid_pivot["end"])
            summary_rows.append(
                {
                    "facility_node_set": node_set,
                    "facility_buffer_m": buffer_m,
                    "endpoints_inside_facility": int(membership["inside_facility"].sum()),
                    "endpoint_facility_match_rate_pct": round(100 * membership["inside_facility"].sum() / total_endpoints, 4),
                    "ambiguous_endpoint_matches": int(membership["ambiguous_facility_match"].sum()),
                    "endpoints_not_in_any_facility": int((~membership["inside_facility"]).sum()),
                    "lines_both_endpoints_in_facilities": int(both.sum()),
                    "lines_one_endpoint_in_facility": int(one.sum()),
                    "lines_no_endpoints_in_facility": int(none.sum()),
                    "lines_both_endpoints_same_facility": int(same.sum()),
                }
            )
    result = pd.concat(all_rows, ignore_index=True)
    result.to_csv(config.PROCESSED_DIR / "at_line_endpoints.csv", index=False)
    return result, pd.DataFrame(summary_rows)


def cluster_endpoints(base_endpoints: pd.DataFrame, threshold_m: float) -> tuple[dict[str, str], pd.DataFrame]:
    endpoints = base_endpoints[["endpoint_id", "x", "y", "voltage", "status"]].to_dict("records")
    uf = UnionFind([row["endpoint_id"] for row in endpoints])
    buckets = spatial_bucket([(row["x"], row["y"], row) for row in endpoints], max(threshold_m, 0.5))
    for row in endpoints:
        for _, _, other, dist in nearby_payloads(buckets, row["x"], row["y"], threshold_m, max(threshold_m, 0.5)):
            if row["endpoint_id"] != other["endpoint_id"]:
                uf.union(row["endpoint_id"], other["endpoint_id"])
    roots = {row["endpoint_id"]: uf.find(row["endpoint_id"]) for row in endpoints}
    root_to_cluster = {root: f"CL_{threshold_m:g}_{idx:05d}" for idx, root in enumerate(sorted(set(roots.values())), start=1)}
    endpoint_to_cluster = {endpoint_id: root_to_cluster[root] for endpoint_id, root in roots.items()}
    rows = []
    for cluster_id in sorted(set(endpoint_to_cluster.values())):
        members = [row for row in endpoints if endpoint_to_cluster[row["endpoint_id"]] == cluster_id]
        voltages = sorted({normalize_voltage(row["voltage"]) for row in members if normalize_voltage(row["voltage"])})
        statuses = sorted({normalize_status(row["status"]) for row in members if normalize_status(row["status"])})
        rows.append(
            {
                "endpoint_snap_threshold_m": threshold_m,
                "endpoint_cluster_id": cluster_id,
                "endpoint_count": len(members),
                "centroid_x": float(np.mean([row["x"] for row in members])),
                "centroid_y": float(np.mean([row["y"] for row in members])),
                "member_endpoint_ids": ",".join(row["endpoint_id"] for row in members),
                "voltage_values": ",".join(voltages),
                "status_values": " | ".join(statuses),
                "mixed_voltage": len(voltages) > 1,
                "mixed_status": len(statuses) > 1,
                "high_degree_cluster": len(members) > 4,
            }
        )
    return endpoint_to_cluster, pd.DataFrame(rows)


def build_endpoint_index(base_endpoints: pd.DataFrame) -> tuple[dict[float, dict[str, str]], pd.DataFrame, pd.DataFrame]:
    mappings = {}
    cluster_frames = []
    assignment_frames = []
    summary_rows = []
    total = len(base_endpoints)
    for threshold in ENDPOINT_SNAP_THRESHOLDS_M:
        mapping, clusters = cluster_endpoints(base_endpoints, threshold)
        mappings[threshold] = mapping
        cluster_frames.append(clusters)
        assignments = base_endpoints.copy()
        assignments["endpoint_snap_threshold_m"] = threshold
        assignments["endpoint_cluster_id"] = assignments["endpoint_id"].map(mapping)
        assignment_frames.append(assignments)
        counts = clusters["endpoint_count"]
        summary_rows.append(
            {
                "endpoint_snap_threshold_m": threshold,
                "total_endpoint_clusters": int(len(clusters)),
                "singleton_clusters": int((counts == 1).sum()),
                "clusters_with_2_endpoints": int((counts == 2).sum()),
                "clusters_with_more_than_2_endpoints": int((counts > 2).sum()),
                "endpoint_clustering_rate_pct": round(100 * (total - (counts == 1).sum()) / total, 4),
                "high_degree_endpoint_clusters": int(clusters["high_degree_cluster"].sum()),
                "mixed_voltage_clusters": int(clusters["mixed_voltage"].sum()),
                "mixed_status_clusters": int(clusters["mixed_status"].sum()),
            }
        )
    endpoint_clusters = pd.concat(cluster_frames, ignore_index=True)
    endpoint_assignments = pd.concat(assignment_frames, ignore_index=True)
    endpoint_assignments.to_csv(config.PROCESSED_DIR / "at_endpoint_index.csv", index=False)
    endpoint_clusters.to_csv(config.PROCESSED_DIR / "at_endpoint_clusters.csv", index=False)
    return mappings, endpoint_clusters, pd.DataFrame(summary_rows)


def compatible_lines(line_a: pd.Series, line_b: pd.Series, mode: str) -> bool:
    if mode == "geometry-only":
        return True
    if not voltage_compatible(line_a["voltage"], line_b["voltage"]):
        return False
    if mode == "voltage-aware":
        return True
    return status_compatible(line_a["status"], line_b["status"])


def union_find_groups(
    lines: pd.DataFrame,
    base_endpoints: pd.DataFrame,
    endpoint_mapping: dict[str, str],
    threshold: float,
    mode: str,
) -> tuple[pd.DataFrame, dict[str, dict[str, Any]]]:
    line_lookup = lines.set_index("line_id")
    endpoint_with_cluster = base_endpoints.copy()
    endpoint_with_cluster["cluster_id"] = endpoint_with_cluster["endpoint_id"].map(endpoint_mapping)
    cluster_to_lines = defaultdict(list)
    for row in endpoint_with_cluster.itertuples(index=False):
        cluster_to_lines[row.cluster_id].append(row.line_id)
    uf = UnionFind(lines["line_id"].astype(str).tolist())
    for cluster_id, line_ids in cluster_to_lines.items():
        unique_lines = sorted(set(line_ids))
        if len(unique_lines) < 2:
            continue
        for a, b in combinations(unique_lines, 2):
            if compatible_lines(line_lookup.loc[a], line_lookup.loc[b], mode):
                uf.union(a, b)
    root_groups = defaultdict(list)
    for line_id in lines["line_id"].astype(str):
        root_groups[uf.find(line_id)].append(line_id)

    group_records = {}
    rows = []
    endpoint_cluster_by_line = endpoint_with_cluster.pivot(index="line_id", columns="endpoint_role", values="cluster_id")
    for idx, line_ids in enumerate(root_groups.values(), start=1):
        group_id = f"UF_{threshold:g}_{mode}_{idx:05d}".replace(".", "p")
        group_lines = line_lookup.loc[line_ids].copy()
        voltages = sorted({normalize_voltage(v) for v in group_lines["voltage"] if normalize_voltage(v)})
        statuses = sorted({normalize_status(v) for v in group_lines["status"] if normalize_status(v)})
        edge_pairs = []
        degree = Counter()
        for line_id in line_ids:
            start_cluster = endpoint_cluster_by_line.loc[line_id, "start"]
            end_cluster = endpoint_cluster_by_line.loc[line_id, "end"]
            edge_pairs.append((start_cluster, end_cluster, line_id))
            if start_cluster != end_cluster:
                degree[start_cluster] += 1
                degree[end_cluster] += 1
        terminal_clusters = sorted([cluster for cluster, deg in degree.items() if deg == 1])
        group_records[group_id] = {
            "group_id": group_id,
            "line_ids": line_ids,
            "edge_pairs": edge_pairs,
            "terminal_clusters": terminal_clusters,
            "voltages": voltages,
            "statuses": statuses,
            "mixed_voltage": len(voltages) > 1,
            "mixed_status": len(statuses) > 1,
        }
        rows.append(
            {
                "endpoint_snap_threshold_m": threshold,
                "merge_mode": mode,
                "union_find_group_id": group_id,
                "segment_count": len(line_ids),
                "source_line_ids": ",".join(group_lines["source_line_id"].astype(str).tolist()),
                "line_ids": ",".join(line_ids),
                "total_length_m": float(group_lines["length_m"].sum()),
                "voltage_values": ",".join(voltages),
                "status_values": " | ".join(statuses),
                "voltage_consistent": len(voltages) <= 1,
                "status_consistent": len(statuses) <= 1,
                "mixed_voltage": len(voltages) > 1,
                "mixed_status": len(statuses) > 1,
                "terminal_count": len(terminal_clusters),
                "terminal_endpoint_cluster_ids": ",".join(terminal_clusters),
            }
        )
    return pd.DataFrame(rows), group_records


def build_all_union_find_groups(
    lines: pd.DataFrame,
    base_endpoints: pd.DataFrame,
    endpoint_mappings: dict[float, dict[str, str]],
) -> tuple[pd.DataFrame, dict[tuple[float, str], dict[str, dict[str, Any]]]]:
    frames = []
    records = {}
    for threshold in ENDPOINT_SNAP_THRESHOLDS_M:
        for mode in MERGE_MODES:
            frame, group_records = union_find_groups(lines, base_endpoints, endpoint_mappings[threshold], threshold, mode)
            frames.append(frame)
            records[(threshold, mode)] = group_records
    all_groups = pd.concat(frames, ignore_index=True)
    all_groups.to_csv(config.PROCESSED_DIR / "at_union_find_groups.csv", index=False)
    return all_groups, records


def endpoint_cluster_centroids(endpoint_index: pd.DataFrame) -> dict[tuple[float, str], tuple[float, float]]:
    result = {}
    for row in endpoint_index.itertuples(index=False):
        result[(float(row.endpoint_snap_threshold_m), row.endpoint_cluster_id)] = (
            float(row.centroid_x),
            float(row.centroid_y),
        )
    return result


def classify_groups_for_strategy(
    groups_df: pd.DataFrame,
    group_records: dict[str, dict[str, Any]],
    lines: pd.DataFrame,
    facilities: pd.DataFrame,
    centroids: dict[tuple[float, str], tuple[float, float]],
    threshold: float,
    mode: str,
    node_set: str,
    buffer_m: float,
) -> pd.DataFrame:
    selected_groups = groups_df[
        (groups_df["endpoint_snap_threshold_m"] == threshold) & (groups_df["merge_mode"] == mode)
    ]
    facility_set = node_set_facilities(facilities, node_set)
    matcher = facility_matcher(facility_set, buffer_m)
    line_lookup = lines.set_index("line_id")
    rows = []
    for group in selected_groups.itertuples(index=False):
        rec = group_records[group.union_find_group_id]
        terminal_details = []
        ambiguous_facility = False
        matched_facilities = []
        for cluster_id in rec["terminal_clusters"]:
            x, y = centroids[(threshold, cluster_id)]
            match = matcher(x, y)
            ambiguous_facility = ambiguous_facility or bool(match["ambiguous_facility_match"])
            if match["inside_facility"]:
                matched_facilities.append(match["facility_uid"])
            terminal_details.append(
                {
                    "endpoint_cluster_id": cluster_id,
                    "x": round(x, 3),
                    "y": round(y, 3),
                    **match,
                }
            )
        terminal_count = len(rec["terminal_clusters"])
        mixed_voltage = bool(group.mixed_voltage)
        mixed_status = bool(group.mixed_status)
        if mixed_voltage or mixed_status or ambiguous_facility:
            classification = "ambiguous"
        elif terminal_count > 2:
            classification = "tap / multi-terminal"
        elif terminal_count == 0:
            classification = "loop"
        elif terminal_count == 2:
            if len(matched_facilities) == 2 and matched_facilities[0] != matched_facilities[1]:
                classification = "inter-facility"
            elif len(matched_facilities) == 2 and matched_facilities[0] == matched_facilities[1]:
                classification = "self-loop"
            elif len(matched_facilities) == 1:
                classification = "single-facility"
            else:
                classification = "isolated"
        else:
            classification = "single-facility" if len(matched_facilities) == 1 else "isolated"

        group_lines = line_lookup.loc[rec["line_ids"]]
        rows.append(
            {
                "facility_node_set": node_set,
                "facility_buffer_m": buffer_m,
                "endpoint_snap_threshold_m": threshold,
                "merge_mode": mode,
                "circuit_id": group.union_find_group_id,
                "classification": classification,
                "terminal_count": terminal_count,
                "terminal_endpoint_cluster_ids": group.terminal_endpoint_cluster_ids,
                "terminal_details_json": json.dumps(terminal_details, ensure_ascii=False),
                "terminal_facility_uids": ",".join(matched_facilities),
                "ambiguous_facility_match": ambiguous_facility,
                "mixed_voltage": mixed_voltage,
                "mixed_status": mixed_status,
                "voltage": rec["voltages"][0] if len(rec["voltages"]) == 1 else "",
                "status": rec["statuses"][0] if len(rec["statuses"]) == 1 else "",
                "voltage_values": group.voltage_values,
                "status_values": group.status_values,
                "segment_count": int(group.segment_count),
                "total_length_m": float(group.total_length_m),
                "total_length_km": round(float(group.total_length_m) / 1000, 6),
                "source_line_ids": group.source_line_ids,
                "line_ids": group.line_ids,
                "geometry_type": "MultiLineString" if int(group.segment_count) > 1 else str(group_lines["geometry_type"].iloc[0]),
                "r": "MISSING_NOT_ESTIMATED",
                "x": "MISSING_NOT_ESTIMATED",
                "b": "MISSING_NOT_ESTIMATED",
                "thermal_limit": "MISSING_NOT_ESTIMATED",
                "transformer_impedance": "MISSING_NOT_ESTIMATED",
                "tap_settings": "MISSING_NOT_ESTIMATED",
            }
        )
    return pd.DataFrame(rows)


def summarize_strategy(classified: pd.DataFrame, lines_count: int) -> dict[str, Any]:
    counts = classified["classification"].value_counts().to_dict()
    graph_stats = graph_stats_from_classified(classified)
    return {
        "raw_line_features": lines_count,
        "merged_circuits": int(len(classified)),
        "reduction_percentage": round(100 * (1 - len(classified) / lines_count), 4),
        "inter_facility_circuits": int(counts.get("inter-facility", 0)),
        "self_loops": int(counts.get("self-loop", 0)),
        "single_facility": int(counts.get("single-facility", 0)),
        "isolated": int(counts.get("isolated", 0)),
        "tap_multi_terminal": int(counts.get("tap / multi-terminal", 0)),
        "ambiguous": int(counts.get("ambiguous", 0)),
        "loop": int(counts.get("loop", 0)),
        "clean_branch_count": int(counts.get("inter-facility", 0)),
        "graph_nodes": graph_stats["number_of_nodes"],
        "graph_edges": graph_stats["number_of_edges"],
        "connected_components": graph_stats["connected_components"],
        "largest_component_size": graph_stats["largest_connected_component_size"],
        "isolated_nodes": graph_stats["isolated_nodes"],
        "mixed_voltage_circuits": int(classified["mixed_voltage"].sum()),
        "mixed_status_circuits": int(classified["mixed_status"].sum()),
        "suspicious_matches": int(classified["ambiguous_facility_match"].sum()),
    }


def graph_stats_from_classified(classified: pd.DataFrame) -> dict[str, Any]:
    graph = nx.MultiGraph()
    inter = classified[classified["classification"] == "inter-facility"]
    for row in inter.itertuples(index=False):
        details = json.loads(row.terminal_details_json)
        if len(details) != 2:
            continue
        a, b = details[0], details[1]
        if not a["facility_uid"] or not b["facility_uid"] or a["facility_uid"] == b["facility_uid"]:
            continue
        graph.add_node(a["facility_uid"], label=a["facility_name"], facility_type=a["facility_type"])
        graph.add_node(b["facility_uid"], label=b["facility_name"], facility_type=b["facility_type"])
        graph.add_edge(
            a["facility_uid"],
            b["facility_uid"],
            key=row.circuit_id,
            circuit_id=row.circuit_id,
            voltage=row.voltage,
            status=row.status,
            total_length_km=float(row.total_length_km),
        )
    simple = nx.Graph()
    simple.add_nodes_from(graph.nodes())
    simple.add_edges_from((u, v) for u, v in graph.edges())
    components = list(nx.connected_components(simple)) if simple.number_of_nodes() else []
    pairs = Counter(tuple(sorted((u, v))) for u, v in graph.edges())
    return {
        "number_of_nodes": int(graph.number_of_nodes()),
        "number_of_edges": int(graph.number_of_edges()),
        "connected_components": int(len(components)),
        "largest_connected_component_size": int(max((len(c) for c in components), default=0)),
        "isolated_nodes": int(sum(1 for _, deg in simple.degree() if deg == 0)),
        "degree_distribution": dict(sorted(Counter(dict(simple.degree()).values()).items())),
        "parallel_edges": int(sum(count - 1 for count in pairs.values() if count > 1)),
        "self_loops": int(nx.number_of_selfloops(graph)),
        "graph_density": float(nx.density(simple)) if simple.number_of_nodes() > 1 else 0.0,
        "component_size_distribution": sorted([len(c) for c in components], reverse=True),
    }


def run_parameter_sweep(
    all_groups: pd.DataFrame,
    group_records_by_strategy: dict[tuple[float, str], dict[str, dict[str, Any]]],
    lines: pd.DataFrame,
    facilities: pd.DataFrame,
    centroids: dict[tuple[float, str], tuple[float, float]],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    strategy_rows = []
    classified_cache: dict[tuple[str, int, float, str], pd.DataFrame] = {}
    for node_set in ["A", "B", "C"]:
        for buffer_m in FACILITY_BUFFERS_M:
            for threshold in ENDPOINT_SNAP_THRESHOLDS_M:
                for mode in MERGE_MODES:
                    classified = classify_groups_for_strategy(
                        all_groups,
                        group_records_by_strategy[(threshold, mode)],
                        lines,
                        facilities,
                        centroids,
                        threshold,
                        mode,
                        node_set,
                        buffer_m,
                    )
                    summary = summarize_strategy(classified, len(lines))
                    reason_parts = []
                    if summary["mixed_voltage_circuits"] == 0:
                        reason_parts.append("no mixed-voltage circuits")
                    if summary["suspicious_matches"] == 0:
                        reason_parts.append("no ambiguous footprint matches")
                    if buffer_m <= 250:
                        reason_parts.append("moderate facility buffer")
                    if threshold <= 25:
                        reason_parts.append("conservative endpoint snapping")
                    score = (
                        summary["clean_branch_count"] * 100
                        + summary["largest_component_size"] * 50
                        - summary["ambiguous"] * 50
                        - summary["mixed_voltage_circuits"] * 100
                        - summary["mixed_status_circuits"] * 10
                        - summary["suspicious_matches"] * 50
                        - (500 if buffer_m == 500 else 0)
                        - (250 if threshold == 50 else 0)
                    )
                    row = {
                        "facility_node_set": node_set,
                        "facility_buffer_m": buffer_m,
                        "endpoint_snap_threshold_m": threshold,
                        "merge_mode": mode,
                        **summary,
                        "strategy_score": round(score, 4),
                        "recommended_yes_no": "no",
                        "reason": "; ".join(reason_parts) if reason_parts else "higher ambiguity or weak graph structure",
                    }
                    strategy_rows.append(row)
                    classified_cache[(node_set, buffer_m, threshold, mode)] = classified
    sweep = pd.DataFrame(strategy_rows)
    best_idx = sweep["strategy_score"].idxmax()
    sweep.loc[best_idx, "recommended_yes_no"] = "yes"
    best_row = sweep.loc[best_idx].to_dict()
    best_key = (
        best_row["facility_node_set"],
        int(best_row["facility_buffer_m"]),
        float(best_row["endpoint_snap_threshold_m"]),
        best_row["merge_mode"],
    )
    best_classified = classified_cache[best_key]
    return sweep, best_classified, best_row


def merged_circuit_geometry(line_ids: list[str], shapes: dict[str, list[list[list[float]]]]) -> dict[str, Any]:
    parts = []
    for line_id in line_ids:
        parts.extend(shapes.get(line_id, []))
    if len(parts) == 1:
        return {"type": "LineString", "coordinates": parts[0]}
    return {"type": "MultiLineString", "coordinates": parts}


def save_selected_geojson_outputs(
    classified: pd.DataFrame,
    shapes: dict[str, list[list[list[float]]]],
) -> None:
    features = []
    for row in classified.itertuples(index=False):
        line_ids = str(row.line_ids).split(",") if row.line_ids else []
        features.append(
            geojson_feature(
                merged_circuit_geometry(line_ids, shapes),
                {
                    "circuit_id": row.circuit_id,
                    "classification": row.classification,
                    "voltage": row.voltage,
                    "status": row.status,
                    "segment_count": int(row.segment_count),
                    "total_length_km": float(row.total_length_km),
                    "mixed_voltage": bool(row.mixed_voltage),
                    "mixed_status": bool(row.mixed_status),
                    "terminal_count": int(row.terminal_count),
                    "source_line_ids": row.source_line_ids,
                },
            )
        )
    write_geojson(config.PROCESSED_DIR / "at_merged_circuits.geojson", features)


def make_candidate_branches(classified: pd.DataFrame, shapes: dict[str, list[list[list[float]]]]) -> pd.DataFrame:
    rows = []
    inter = classified[classified["classification"] == "inter-facility"].copy()
    for idx, row in enumerate(inter.itertuples(index=False), start=1):
        details = json.loads(row.terminal_details_json)
        if len(details) != 2:
            continue
        a, b = details[0], details[1]
        max_terminal_dist = max(float(a.get("facility_distance_m") or 0), float(b.get("facility_distance_m") or 0))
        confidence = max(0.0, min(1.0, 1.0 - max_terminal_dist / max(float(row.facility_buffer_m), 1) * 0.35))
        rows.append(
            {
                "branch_id": f"ATPL_{idx:05d}",
                "circuit_id": row.circuit_id,
                "from_facility_uid": a["facility_uid"],
                "from_facility_code": a["facility_code"],
                "from_facility_name": a["facility_name"],
                "from_facility_type": a["facility_type"],
                "to_facility_uid": b["facility_uid"],
                "to_facility_code": b["facility_code"],
                "to_facility_name": b["facility_name"],
                "to_facility_type": b["facility_type"],
                "voltage": row.voltage,
                "status": row.status,
                "total_length_km": row.total_length_km,
                "number_of_original_segments": row.segment_count,
                "geometry": json.dumps(merged_circuit_geometry(str(row.line_ids).split(","), shapes), ensure_ascii=False),
                "source_line_ids": row.source_line_ids,
                "confidence_score": round(confidence, 4),
                "classification": "inter-facility",
                "r": "MISSING_NOT_ESTIMATED",
                "x": "MISSING_NOT_ESTIMATED",
                "b": "MISSING_NOT_ESTIMATED",
                "thermal_limit": "MISSING_NOT_ESTIMATED",
                "transformer_impedance": "MISSING_NOT_ESTIMATED",
                "tap_settings": "MISSING_NOT_ESTIMATED",
            }
        )
    branches = pd.DataFrame(rows)
    branches.to_csv(config.PROCESSED_DIR / "at_interfacility_candidate_branches.csv", index=False)
    return branches


def build_candidate_graph(branches: pd.DataFrame, selected_facilities: pd.DataFrame) -> tuple[nx.MultiGraph, dict[str, Any]]:
    graph = nx.MultiGraph()
    for row in selected_facilities.itertuples(index=False):
        graph.add_node(
            row.facility_uid,
            facility_code=row.facility_code,
            facility_name=row.facility_name,
            facility_type=row.facility_type,
            source_dataset=row.source_dataset,
            lon=float(row.lon),
            lat=float(row.lat),
        )
    for row in branches.itertuples(index=False):
        graph.add_edge(
            row.from_facility_uid,
            row.to_facility_uid,
            key=row.branch_id,
            branch_id=row.branch_id,
            circuit_id=row.circuit_id,
            voltage=row.voltage,
            status=row.status,
            total_length_km=float(row.total_length_km),
            confidence_score=float(row.confidence_score),
            r="MISSING_NOT_ESTIMATED",
            x="MISSING_NOT_ESTIMATED",
            b="MISSING_NOT_ESTIMATED",
            thermal_limit="MISSING_NOT_ESTIMATED",
        )
    nx.write_graphml(graph, config.PROCESSED_DIR / "at_paper_logic_graph.graphml")
    simple = nx.Graph()
    simple.add_nodes_from(graph.nodes())
    simple.add_edges_from((u, v) for u, v in graph.edges())
    components = list(nx.connected_components(simple)) if simple.number_of_nodes() else []
    pairs = Counter(tuple(sorted((u, v))) for u, v in graph.edges())
    stats = {
        "number_of_graph_nodes": int(graph.number_of_nodes()),
        "number_of_graph_edges": int(graph.number_of_edges()),
        "connected_components": int(len(components)),
        "largest_connected_component_size": int(max((len(c) for c in components), default=0)),
        "isolated_facility_nodes": int(sum(1 for _, degree in simple.degree() if degree == 0)),
        "degree_distribution": dict(sorted(Counter(dict(simple.degree()).values()).items())),
        "parallel_edges": int(sum(count - 1 for count in pairs.values() if count > 1)),
        "self_loops": int(nx.number_of_selfloops(graph)),
        "graph_density": float(nx.density(simple)) if simple.number_of_nodes() > 1 else 0.0,
        "component_size_distribution": sorted([len(c) for c in components], reverse=True),
    }
    return graph, stats


def classification_aggregate(classified: pd.DataFrame) -> pd.DataFrame:
    rows = []
    total = len(classified)
    for classification, group in classified.groupby("classification"):
        rows.append(
            {
                "classification": classification,
                "circuit_count": len(group),
                "percentage_of_all_circuits": round(100 * len(group) / total, 4) if total else 0,
                "total_length_km": round(float(group["total_length_km"].sum()), 6),
                "original_segments_represented": int(group["segment_count"].sum()),
            }
        )
    return pd.DataFrame(rows).sort_values("classification")


def color_for_classification(value: str) -> str:
    return {
        "inter-facility": "#1f77b4",
        "self-loop": "#9467bd",
        "single-facility": "#ff7f0e",
        "isolated": "#d62728",
        "tap / multi-terminal": "#8c564b",
        "ambiguous": "#111111",
        "loop": "#7f7f7f",
    }.get(value, "#555555")


def leaflet_html(title: str, layers: list[dict[str, Any]]) -> str:
    escaped_title = html.escape(title)
    layer_scripts = []
    controls = []
    for idx, layer in enumerate(layers):
        var_name = f"layer{idx}"
        data = json.dumps(layer["geojson"], ensure_ascii=False)
        style = json.dumps(layer.get("style", {}))
        point_style = json.dumps(layer.get("point_style", {}))
        layer_scripts.append(
            f"""
const data{idx} = {data};
const {var_name} = L.geoJSON(data{idx}, {{
  style: function(feature) {{
    if (feature.properties && feature.properties._color) {{
      return {{color: feature.properties._color, weight: feature.properties._weight || 2, fillOpacity: feature.properties._fillOpacity || 0.15}};
    }}
    return {style};
  }},
  pointToLayer: function(feature, latlng) {{
    return L.circleMarker(latlng, Object.assign({{radius: 4}}, {point_style}));
  }},
  onEachFeature: function(feature, layer) {{
    if (feature.properties) {{
      layer.bindPopup(Object.entries(feature.properties).map(([k,v]) => `<b>${{k}}</b>: ${{v}}`).join('<br>'));
    }}
  }}
}}).addTo(map);
"""
        )
        controls.append(f'"{html.escape(layer["name"])}": {var_name}')
    return f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>{escaped_title}</title>
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
  <style>html, body, #map {{ height: 100%; margin: 0; }} .legend {{ background: white; padding: 8px; }}</style>
</head>
<body>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const map = L.map('map').setView([39.6, -8.3], 7);
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{maxZoom: 19, attribution: '&copy; OpenStreetMap'}}).addTo(map);
{''.join(layer_scripts)}
L.control.layers(null, {{{', '.join(controls)}}}).addTo(map);
</script>
</body>
</html>"""


def line_features_for_ids(line_ids: list[str], shapes: dict[str, list[list[list[float]]]], props: dict[str, Any]) -> list[dict[str, Any]]:
    features = []
    for line_id in line_ids:
        if line_id in shapes:
            features.append(geojson_feature(merged_circuit_geometry([line_id], shapes), props | {"line_id": line_id}))
    return features


def create_maps(
    lines: pd.DataFrame,
    facilities: pd.DataFrame,
    shapes: dict[str, list[list[list[float]]]],
    classified: pd.DataFrame,
    branches: pd.DataFrame,
    endpoint_index: pd.DataFrame,
    selected: dict[str, Any],
    graph: nx.MultiGraph,
    projection: Any,
) -> dict[str, str]:
    maps_dir = config.REPORTS_DIR / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)
    node_set = selected["facility_node_set"]
    buffer_m = int(selected["facility_buffer_m"])
    selected_facilities = node_set_facilities(facilities, node_set)
    facility_points = [
        geojson_feature(
            {"type": "Point", "coordinates": [row.lon, row.lat]},
            {
                "facility_uid": row.facility_uid,
                "facility_name": row.facility_name,
                "facility_type": row.facility_type,
                "node_set": node_set,
            },
        )
        for row in selected_facilities.itertuples(index=False)
    ]
    raw_line_subset = []
    for row in lines.head(1500).itertuples(index=False):
        raw_line_subset.extend(line_features_for_ids([row.line_id], shapes, {"voltage": row.voltage, "status": row.status}))
    footprint_features = [
        geojson_feature(
            {"type": "Polygon", "coordinates": [circle_polygon(projection, row.x, row.y, buffer_m)]},
            {
                "facility_uid": row.facility_uid,
                "facility_name": row.facility_name,
                "facility_type": row.facility_type,
                "buffer_m": buffer_m,
            },
        )
        for row in selected_facilities.itertuples(index=False)
    ]

    map_specs = {}
    path = maps_dir / "at_paper_logic_facility_footprints.html"
    path.write_text(
        leaflet_html(
            "AT paper logic facility footprints",
            [
                {"name": "Facility footprints", "geojson": {"type": "FeatureCollection", "features": footprint_features}, "style": {"color": "#2ca02c", "weight": 1, "fillOpacity": 0.08}},
                {"name": "Facilities", "geojson": {"type": "FeatureCollection", "features": facility_points}, "point_style": {"color": "#d62728", "fillColor": "#d62728", "fillOpacity": 0.8}},
            ],
        ),
        encoding="utf-8",
    )
    map_specs[path.name] = str(path)

    path = maps_dir / "at_paper_logic_raw_segments.html"
    path.write_text(
        leaflet_html(
            "Raw AT segments",
            [
                {"name": "Raw AT segments sample", "geojson": {"type": "FeatureCollection", "features": raw_line_subset}, "style": {"color": "#777", "weight": 1, "opacity": 0.65}},
                {"name": "Facilities", "geojson": {"type": "FeatureCollection", "features": facility_points}, "point_style": {"color": "#d62728", "fillColor": "#d62728", "fillOpacity": 0.8}},
            ],
        ),
        encoding="utf-8",
    )
    map_specs[path.name] = str(path)

    cluster_subset = endpoint_index[
        endpoint_index["endpoint_snap_threshold_m"] == float(selected["endpoint_snap_threshold_m"])
    ].copy()
    cluster_features = []
    for row in cluster_subset.itertuples(index=False):
        lon, lat = projection.lonlat(float(row.centroid_x), float(row.centroid_y))
        endpoint_count = int(row.endpoint_count)
        if endpoint_count == 1:
            color = "#aaaaaa"
        elif endpoint_count == 2:
            color = "#1f77b4"
        else:
            color = "#d62728"
        cluster_features.append(
            geojson_feature(
                {"type": "Point", "coordinates": [lon, lat]},
                {
                    "endpoint_cluster_id": row.endpoint_cluster_id,
                    "endpoint_count": endpoint_count,
                    "mixed_voltage": bool(row.mixed_voltage),
                    "mixed_status": bool(row.mixed_status),
                    "_color": color,
                },
            )
        )
    path = maps_dir / "at_paper_logic_endpoint_clusters.html"
    path.write_text(
        leaflet_html(
            "Endpoint clusters",
            [
                {
                    "name": "Endpoint clusters",
                    "geojson": {"type": "FeatureCollection", "features": cluster_features},
                    "point_style": {"color": "#1f77b4", "fillColor": "#1f77b4", "fillOpacity": 0.65, "radius": 3},
                }
            ],
        ),
        encoding="utf-8",
    )
    map_specs[path.name] = str(path)

    circuit_features = []
    for row in classified.itertuples(index=False):
        if row.classification in {"inter-facility", "ambiguous", "tap / multi-terminal", "single-facility", "isolated", "self-loop", "loop"}:
            circuit_features.append(
                geojson_feature(
                    merged_circuit_geometry(str(row.line_ids).split(","), shapes),
                    {
                        "circuit_id": row.circuit_id,
                        "classification": row.classification,
                        "segments": row.segment_count,
                        "length_km": row.total_length_km,
                        "_color": color_for_classification(row.classification),
                        "_weight": 2 if row.classification == "inter-facility" else 1,
                    },
                )
            )
    path = maps_dir / "at_paper_logic_merged_circuits.html"
    path.write_text(
        leaflet_html(
            "Merged AT circuits",
            [{"name": "Merged circuits", "geojson": {"type": "FeatureCollection", "features": circuit_features}, "style": {"color": "#1f77b4", "weight": 1.5}}],
        ),
        encoding="utf-8",
    )
    map_specs[path.name] = str(path)

    path = maps_dir / "at_paper_logic_circuit_classification.html"
    path.write_text(
        leaflet_html(
            "Circuit classification",
            [{"name": "Classified circuits", "geojson": {"type": "FeatureCollection", "features": circuit_features}, "style": {"color": "#1f77b4", "weight": 1.5}}],
        ),
        encoding="utf-8",
    )
    map_specs[path.name] = str(path)

    branch_features = []
    for row in branches.itertuples(index=False):
        branch_features.append(
            geojson_feature(
                json.loads(row.geometry),
                {
                    "branch_id": row.branch_id,
                    "from": row.from_facility_name,
                    "to": row.to_facility_name,
                    "confidence": row.confidence_score,
                    "_color": "#1f77b4",
                    "_weight": 3,
                },
            )
        )
    path = maps_dir / "at_paper_logic_interfacility_branches.html"
    path.write_text(
        leaflet_html(
            "Inter-facility AT branches",
            [
                {"name": "Inter-facility branches", "geojson": {"type": "FeatureCollection", "features": branch_features}, "style": {"color": "#1f77b4", "weight": 3}},
                {"name": "Facilities", "geojson": {"type": "FeatureCollection", "features": facility_points}, "point_style": {"color": "#2ca02c", "fillColor": "#2ca02c", "fillOpacity": 0.8}},
            ],
        ),
        encoding="utf-8",
    )
    map_specs[path.name] = str(path)

    simple = nx.Graph()
    simple.add_nodes_from(graph.nodes())
    simple.add_edges_from((u, v) for u, v in graph.edges())
    components = list(nx.connected_components(simple)) if simple.number_of_nodes() else []
    largest = max(components, key=len) if components else set()
    largest_branch_ids = []
    for edge in graph.edges(keys=True, data=True):
        u, v, _, data = edge
        if u in largest and v in largest:
            largest_branch_ids.append(data.get("branch_id"))
    lcc_features = [feature for feature in branch_features if feature["properties"]["branch_id"] in largest_branch_ids]
    path = maps_dir / "at_paper_logic_largest_component.html"
    path.write_text(
        leaflet_html(
            "Largest connected component",
            [{"name": "Largest component branches", "geojson": {"type": "FeatureCollection", "features": lcc_features}, "style": {"color": "#0b5cad", "weight": 4}}],
        ),
        encoding="utf-8",
    )
    map_specs[path.name] = str(path)
    return map_specs


def build_report(
    validation: dict[str, Any],
    facility_summary: pd.DataFrame,
    endpoint_membership_summary: pd.DataFrame,
    endpoint_index_summary: pd.DataFrame,
    all_groups: pd.DataFrame,
    sweep: pd.DataFrame,
    best: dict[str, Any],
    classified: pd.DataFrame,
    classification_summary: pd.DataFrame,
    graph_stats: dict[str, Any],
    maps: dict[str, str],
) -> None:
    top_strategies = sweep.sort_values("strategy_score", ascending=False).head(12).to_dict("records")
    previous_edges = 51
    previous_lcc = 6
    improvement_edges = graph_stats["number_of_graph_edges"] - previous_edges
    improvement_lcc = graph_stats["largest_connected_component_size"] - previous_lcc
    branch_count = int(best["clean_branch_count"])
    total_circuits = int(best["merged_circuits"])
    text = [
        "# 08 AT Topology Reconstruction - Paper Logic",
        "",
        f"Generated: {utc_now()}",
        "",
        "Scope: Step 2A.2 only. This adapts the paper-style topology reconstruction stage to E-REDES AT geometries: facility footprints, endpoint index, voltage/status-aware union-find line merging, circuit classification, and reliable inter-facility branch extraction. No electrical parameters are estimated.",
        "",
        "Reference paper: [Building Power Grid Models from Open Data: A Complete Pipeline from OpenStreetMap to Optimal Power Flow](https://arxiv.org/abs/2605.04289).",
        "",
        "The referenced paper describes a five-stage open-data pipeline whose topology stage reconstructs bus-branch topology through voltage inference, line merging, and transformer detection. Here only the topology reconstruction logic relevant to line merging and circuit classification is tested, using E-REDES data and preserving missing electrical fields.",
        "",
        "## Input Validation",
        markdown_table(
            [
                {"Metric": "raw AT line features", "Value": validation["raw_feature_count"]},
                {"Metric": "valid line geometries", "Value": validation["valid_line_geometries"]},
                {"Metric": "invalid/dropped geometries", "Value": validation["invalid_or_dropped_geometries"]},
                {"Metric": "simple repaired geometries", "Value": validation["simple_repaired_geometries"]},
                {"Metric": "facility rows loaded", "Value": validation["facility_rows_loaded"]},
                {"Metric": "AT substations", "Value": validation["at_substations"]},
                {"Metric": "other relevant facilities", "Value": validation["other_relevant_facilities"]},
                {"Metric": "metric geometry CRS", "Value": validation["metric_crs"]},
            ],
            ["Metric", "Value"],
        ),
        "",
        "### Total Line Length By Voltage",
        markdown_table(validation["length_by_voltage"], ["voltage", "line_count", "total_length_km"]),
        "",
        "### Total Line Length By Status",
        markdown_table(validation["length_by_status"], ["status", "line_count", "total_length_km"]),
        "",
        "## Facility Footprints",
        markdown_table(
            facility_summary.to_dict("records")[:20],
            [
                "facility_node_set",
                "facility_buffer_m",
                "facility_count",
                "overlapping_footprint_pairs",
                "duplicate_or_nearby_facility_pairs_25m",
                "spatially_reasonable",
            ],
        ),
        "",
        "Node sets: A = AT substations only; B = AT substations + AT switching stations/postos de corte; C = A+B plus MT substations and MT switching stations. PTD distribution transformers were not included in C because they are MV/LV facilities and would be false AT topology nodes.",
        "",
        "## Endpoint Facility Membership",
        markdown_table(
            endpoint_membership_summary.to_dict("records")[:20],
            [
                "facility_node_set",
                "facility_buffer_m",
                "endpoints_inside_facility",
                "endpoint_facility_match_rate_pct",
                "ambiguous_endpoint_matches",
                "lines_both_endpoints_in_facilities",
                "lines_one_endpoint_in_facility",
                "lines_no_endpoints_in_facility",
                "lines_both_endpoints_same_facility",
            ],
        ),
        "",
        "## Endpoint Index",
        markdown_table(
            endpoint_index_summary.to_dict("records"),
            [
                "endpoint_snap_threshold_m",
                "total_endpoint_clusters",
                "singleton_clusters",
                "clusters_with_2_endpoints",
                "clusters_with_more_than_2_endpoints",
                "endpoint_clustering_rate_pct",
                "high_degree_endpoint_clusters",
                "mixed_voltage_clusters",
                "mixed_status_clusters",
            ],
        ),
        "",
        "## Union-Find Sweep",
        markdown_table(
            top_strategies,
            [
                "facility_node_set",
                "facility_buffer_m",
                "endpoint_snap_threshold_m",
                "merge_mode",
                "merged_circuits",
                "reduction_percentage",
                "inter_facility_circuits",
                "clean_branch_count",
                "largest_component_size",
                "ambiguous",
                "mixed_voltage_circuits",
                "suspicious_matches",
                "recommended_yes_no",
                "reason",
            ],
        ),
        "",
        f"Selected strategy: node set **{best['facility_node_set']}**, facility buffer **{int(best['facility_buffer_m'])} m**, endpoint snapping **{best['endpoint_snap_threshold_m']} m**, merge mode **{best['merge_mode']}**.",
        "",
        "## Selected Circuit Classification",
        markdown_table(
            classification_summary.to_dict("records"),
            ["classification", "circuit_count", "percentage_of_all_circuits", "total_length_km", "original_segments_represented"],
        ),
        "",
        "## Candidate Graph",
        markdown_table(
            [
                {"Metric": "graph_nodes", "Value": graph_stats["number_of_graph_nodes"]},
                {"Metric": "graph_edges", "Value": graph_stats["number_of_graph_edges"]},
                {"Metric": "connected_components", "Value": graph_stats["connected_components"]},
                {"Metric": "largest_connected_component_size", "Value": graph_stats["largest_connected_component_size"]},
                {"Metric": "isolated_facility_nodes", "Value": graph_stats["isolated_facility_nodes"]},
                {"Metric": "degree_distribution", "Value": graph_stats["degree_distribution"]},
                {"Metric": "parallel_edges", "Value": graph_stats["parallel_edges"]},
                {"Metric": "self_loops", "Value": graph_stats["self_loops"]},
                {"Metric": "graph_density", "Value": round(graph_stats["graph_density"], 6)},
                {"Metric": "component_size_distribution_top10", "Value": graph_stats["component_size_distribution"][:10]},
            ],
            ["Metric", "Value"],
        ),
        "",
        "## Comparison With Failed Step 2A",
        "",
        f"Direct endpoint-to-substation snapping produced 51 merged edges and largest component size 6. The paper-style method produced {graph_stats['number_of_graph_edges']} graph edges and largest component size {graph_stats['largest_connected_component_size']} under the selected strategy. Edge delta: {improvement_edges}; largest-component delta: {improvement_lcc}.",
        "",
        "## Maps",
        "",
    ]
    for name in maps:
        text.append(f"- `reports/maps/{name}`")
    text.extend(
        [
            "",
            "## Final Answers",
            "",
            f"1. Does paper-style logic improve over direct snapping? {'Yes' if improvement_edges > 0 or improvement_lcc > 0 else 'No'} by graph metrics, but only within the limits of available facility data and geometry fragmentation.",
            f"2. Can RND AT line features be merged into continuous circuits? Yes. Raw features were reduced to {total_circuits} merged circuits under the selected strategy, a {best['reduction_percentage']}% reduction.",
            f"3. Best endpoint snapping threshold: {best['endpoint_snap_threshold_m']} m.",
            f"4. Best facility footprint buffer: {int(best['facility_buffer_m'])} m.",
            f"5. Best facility node set: {best['facility_node_set']}.",
            f"6. Best merge mode: {best['merge_mode']}.",
            f"7. Reliable inter-facility circuits reconstructed: {branch_count}.",
            f"8. Remaining circuit classes are listed in the classification table above; key non-clean classes include self-loops={best['self_loops']}, single-facility={best['single_facility']}, isolated={best['isolated']}, tap/multi-terminal={best['tap_multi_terminal']}, ambiguous={best['ambiguous']}.",
            f"9. Candidate graph connectivity: {graph_stats['number_of_graph_edges']} edges, {graph_stats['connected_components']} components, largest component {graph_stats['largest_connected_component_size']} facilities.",
            "10. Good enough for Step 2B transformer/multi-voltage node inventory: yes, but topology edges remain preliminary.",
            "11. Good enough for Step 3 electrical parameter estimation: no. Reliable topology coverage is still incomplete and electrical fields are missing.",
            "12. Good enough for power flow or OPF: no. Missing line impedance, susceptance, thermal limits, transformer impedance/taps, explicit validated bus terminals, and complete connected topology.",
            "13. Main failure modes: fragmented geometries that do not share endpoints within tested thresholds, loops/self-loops around facilities, single-facility stubs, multi-terminal/tap candidates, and missing intermediate AT facilities.",
            "14. Next technical step: add line-line noding and endpoint-to-line snapping, then use OSM/REN or additional E-REDES facility layers as auxiliary topology references before parameter estimation.",
            "",
            "## Final Conclusion",
            "",
            "### GREEN",
            "- Facility point datasets and AT line geometry are technically readable and can be indexed in a metric coordinate system.",
            "- Union-find merging reduces fragmented line features into larger candidate circuits.",
            "- Inter-facility candidates are explicitly separated from loops, isolated stubs, taps, and ambiguous circuits.",
            "",
            "### YELLOW",
            "- Candidate inter-facility graph is useful for Step 2B inventory and manual/topological validation.",
            "- Facility buffers and endpoint thresholds are usable as sensitivity parameters, but results need map checks.",
            "- Some branches may be real, but they are not yet validated electrical branches.",
            "",
            "### RED",
            "- Not OPF-ready.",
            "- Not ready for electrical parameter estimation.",
            "- Missing line impedances, thermal limits, transformer impedances/taps, and validated terminal connectivity.",
        ]
    )
    write_text(config.REPORTS_DIR / "08_at_topology_paper_logic.md", "\n".join(text))


def main():
    parser = argparse.ArgumentParser(description="Reconstruct the PT60 candidate topology and 216-setting sweep.")
    parser.add_argument("--raw-dir", type=Path, default=config.RAW_DIR, help="Directory containing the E-REDES GeoJSON exports.")
    parser.add_argument("--processed-dir", type=Path, default=config.PROCESSED_DIR, help="Directory for generated data artifacts.")
    parser.add_argument("--reports-dir", type=Path, default=config.REPORTS_DIR, help="Directory for generated reports and maps.")
    args = parser.parse_args()
    config.RAW_DIR = args.raw_dir.resolve()
    config.PROCESSED_DIR = args.processed_dir.resolve()
    config.REPORTS_DIR = args.reports_dir.resolve()

    ensure_directories()
    (config.REPORTS_DIR / "maps").mkdir(parents=True, exist_ok=True)

    lines, shapes, validation = load_at_lines()
    facilities = load_facilities()
    projection = build_projection(lines, facilities)
    lines, facilities = add_projected_columns(lines, facilities, shapes, projection)

    validation["facility_rows_loaded"] = int(len(facilities))
    validation["at_substations"] = int((facilities["facility_type"] == "SE_AT").sum())
    validation["other_relevant_facilities"] = int((facilities["facility_type"] != "SE_AT").sum())
    validation["metric_crs"] = projection.description()
    validation["length_by_voltage"] = (
        lines.groupby("voltage")
        .agg(line_count=("line_id", "count"), total_length_km=("length_m", lambda s: round(float(s.sum()) / 1000, 6)))
        .reset_index()
        .to_dict("records")
    )
    validation["length_by_status"] = (
        lines.groupby("status")
        .agg(line_count=("line_id", "count"), total_length_km=("length_m", lambda s: round(float(s.sum()) / 1000, 6)))
        .reset_index()
        .to_dict("records")
    )

    _, facility_summary = build_facility_footprints(facilities, projection)
    base_endpoints = build_base_endpoints(lines)
    endpoint_membership, endpoint_membership_summary = endpoint_facility_membership(base_endpoints, facilities)
    endpoint_mappings, endpoint_index, endpoint_index_summary = build_endpoint_index(base_endpoints)
    all_groups, group_records_by_strategy = build_all_union_find_groups(lines, base_endpoints, endpoint_mappings)
    centroids = endpoint_cluster_centroids(endpoint_index)
    sweep, best_classified, best = run_parameter_sweep(
        all_groups, group_records_by_strategy, lines, facilities, centroids
    )
    selected_facilities = node_set_facilities(facilities, best["facility_node_set"])

    facility_summary.to_csv(config.PROCESSED_DIR / "at_facility_footprints_summary.csv", index=False)
    endpoint_membership_summary.to_csv(config.PROCESSED_DIR / "at_endpoint_facility_membership_summary.csv", index=False)
    endpoint_index_summary.to_csv(config.PROCESSED_DIR / "at_endpoint_index_summary.csv", index=False)
    sweep.to_csv(config.PROCESSED_DIR / "at_paper_logic_parameter_sweep.csv", index=False)
    best_classified.to_csv(config.PROCESSED_DIR / "at_circuit_classification.csv", index=False)
    save_selected_geojson_outputs(best_classified, shapes)
    branches = make_candidate_branches(best_classified, shapes)
    graph, graph_stats = build_candidate_graph(branches, selected_facilities)
    classification_summary = classification_aggregate(best_classified)
    maps = create_maps(
        lines,
        facilities,
        shapes,
        best_classified,
        branches,
        endpoint_index,
        best,
        graph,
        projection,
    )

    summary = {
        "generated_at": utc_now(),
        "source_paper": {
            "title": "Building Power Grid Models from Open Data: A Complete Pipeline from OpenStreetMap to Optimal Power Flow",
            "arxiv": "2605.04289",
            "url": "https://arxiv.org/abs/2605.04289",
            "topology_stage_note": "Adapts the paper-style topology reconstruction concepts: facility footprints, endpoint index, line merging, voltage-aware compatibility, circuit classification.",
        },
        "validation": validation,
        "selected_strategy": best,
        "classification_summary": classification_summary.to_dict("records"),
        "graph_statistics": graph_stats,
        "previous_step_2a": {
            "merged_edges": 51,
            "largest_component_size": 6,
            "clean_inter_substation_lines": 61,
        },
        "electrical_parameters": {
            "r": "MISSING_NOT_ESTIMATED",
            "x": "MISSING_NOT_ESTIMATED",
            "b": "MISSING_NOT_ESTIMATED",
            "thermal_limit": "MISSING_NOT_ESTIMATED",
            "transformer_impedance": "MISSING_NOT_ESTIMATED",
            "tap_settings": "MISSING_NOT_ESTIMATED",
        },
        "outputs": {
            "facility_footprints": str(config.PROCESSED_DIR / "at_facility_footprints.geojson"),
            "line_endpoints": str(config.PROCESSED_DIR / "at_line_endpoints.csv"),
            "endpoint_index": str(config.PROCESSED_DIR / "at_endpoint_index.csv"),
            "union_find_groups": str(config.PROCESSED_DIR / "at_union_find_groups.csv"),
            "merged_circuits": str(config.PROCESSED_DIR / "at_merged_circuits.geojson"),
            "circuit_classification": str(config.PROCESSED_DIR / "at_circuit_classification.csv"),
            "interfacility_candidate_branches": str(config.PROCESSED_DIR / "at_interfacility_candidate_branches.csv"),
            "graph": str(config.PROCESSED_DIR / "at_paper_logic_graph.graphml"),
            "maps": maps,
        },
    }
    write_json(config.PROCESSED_DIR / "at_paper_logic_summary.json", summary)
    build_report(
        validation,
        facility_summary,
        endpoint_membership_summary,
        endpoint_index_summary,
        all_groups,
        sweep,
        best,
        best_classified,
        classification_summary,
        graph_stats,
        maps,
    )


if __name__ == "__main__":
    main()
