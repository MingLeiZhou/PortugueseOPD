import json
import math
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/private/tmp/portugueseopd_matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd

import config
from utils import ensure_directories, load_dataset_frame, markdown_table, normalize_name, utc_now, write_json, write_text


THRESHOLDS_M = [100, 250, 500, 1000]
SUSPICIOUS_DISTANCE_M = 500


@dataclass
class LocalMetricProjection:
    """Small-distance metric projection centered on the dataset centroid."""

    lon0: float
    lat0: float
    radius_m: float = 6_371_008.8

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        x = self.radius_m * math.radians(lon - self.lon0) * math.cos(math.radians(self.lat0))
        y = self.radius_m * math.radians(lat - self.lat0)
        return x, y


def read_csv_auto(path: Path) -> pd.DataFrame:
    first_line = path.open("r", encoding="utf-8-sig", errors="replace").readline()
    sep = ";" if first_line.count(";") > first_line.count(",") else ","
    return pd.read_csv(path, sep=sep, encoding="utf-8-sig", low_memory=False)


def parse_lat_lon(value: Any) -> tuple[float, float] | None:
    if pd.isna(value):
        return None
    text = str(value)
    nums = [float(x) for x in text.replace("[", "").replace("]", "").split(",") if x.strip()]
    if len(nums) < 2:
        return None
    a, b = nums[0], nums[1]
    if abs(a) <= 90 and abs(b) <= 180:
        return a, b
    return b, a


def load_at_substations() -> pd.DataFrame:
    substations = read_csv_auto(config.RAW_DIR / "se-at_2025.csv")
    coords = substations["coordenadas"].apply(parse_lat_lon)
    substations["lat"] = coords.apply(lambda value: value[0] if value else np.nan)
    substations["lon"] = coords.apply(lambda value: value[1] if value else np.nan)
    substations = substations.dropna(subset=["lat", "lon"]).copy()
    substations["bus_id"] = substations["codigo"].astype(str)
    substations["bus_name"] = substations["instalacao"].astype(str)
    return enrich_substations(substations)


def aggregate_first_numeric(df: pd.DataFrame, key: str) -> pd.DataFrame:
    if df.empty or key not in df.columns:
        return pd.DataFrame(columns=[key])
    aggregations = {}
    for column in df.columns:
        if column == key:
            continue
        numeric = pd.to_numeric(df[column], errors="coerce")
        if numeric.notna().any():
            aggregations[column] = "max"
        else:
            aggregations[column] = "first"
    return df.groupby(key, dropna=False).agg(aggregations).reset_index()


def enrich_substations(substations: pd.DataFrame) -> pd.DataFrame:
    enriched = substations.copy()

    carga = read_csv_auto(config.RAW_DIR / "carga-na-subestacao.csv")
    carga_agg = aggregate_first_numeric(carga, "codigo_da_instalacao")
    keep = [
        "codigo_da_instalacao",
        "tensao",
        "carga_natural",
        "potencia_instalada",
        "potencia_garantida",
        "disponibilidade",
    ]
    carga_agg = carga_agg[[column for column in keep if column in carga_agg.columns]]
    enriched = enriched.merge(
        carga_agg,
        left_on="bus_id",
        right_on="codigo_da_instalacao",
        how="left",
    )

    caracteristicas = read_csv_auto(config.RAW_DIR / "caracteristicas-da-rede.csv")
    caracteristicas_agg = aggregate_first_numeric(caracteristicas, "codigo_da_instalacao")
    keep = [
        "codigo_da_instalacao",
        "relacao_de_transformacao_at_mt",
        "potencia_de_curto_circuito_maxima_at",
        "potencia_de_curto_circuito_maxima_mt",
        "potencia_de_curto_circuito_minima_at",
        "potencia_de_curto_circuito_minima_mt",
    ]
    caracteristicas_agg = caracteristicas_agg[
        [column for column in keep if column in caracteristicas_agg.columns]
    ].rename(columns={column: f"caracteristicas_{column}" for column in keep if column != "codigo_da_instalacao"})
    enriched = enriched.merge(
        caracteristicas_agg,
        left_on="bus_id",
        right_on="codigo_da_instalacao",
        how="left",
        suffixes=("", "_caracteristicas"),
    )

    capacidade = read_csv_auto(config.RAW_DIR / "capacidade-rececao-rnd.csv")
    capacidade_agg = aggregate_first_numeric(capacidade, "codigo")
    keep = [
        "codigo",
        "capacidade_de_recepcao_at_mva_rari",
        "capacidade_de_recepcao_mt_at_mva_rari",
        "potencia_de_ligacao_ligado_mva_rari",
        "potencia_de_ligacao_comprometido_mva_rari",
    ]
    capacidade_agg = capacidade_agg[
        [column for column in keep if column in capacidade_agg.columns]
    ].rename(columns={column: f"capacidade_{column}" for column in keep if column != "codigo"})
    enriched = enriched.merge(
        capacidade_agg,
        left_on="bus_id",
        right_on="codigo",
        how="left",
        suffixes=("", "_capacidade"),
    )

    return enriched


def iter_geometry_parts(geometry: dict[str, Any]) -> list[list[list[float]]]:
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if geom_type == "LineString":
        return [coords]
    if geom_type == "MultiLineString":
        return [part for part in coords if part]
    return []


def flatten_parts(parts: list[list[list[float]]]) -> list[list[float]]:
    flattened = []
    for part in parts:
        flattened.extend(part)
    return flattened


def projected_length_m(parts: list[list[list[float]]], projection: LocalMetricProjection) -> float:
    total = 0.0
    for part in parts:
        if len(part) < 2:
            continue
        points = [projection.xy(lon, lat) for lon, lat in part]
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            total += math.hypot(x2 - x1, y2 - y1)
    return total


def load_at_lines() -> tuple[pd.DataFrame, list[dict[str, Any]]]:
    path = config.RAW_DIR / "rede-at-teste.geojson"
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    shapes = []
    for idx, feature in enumerate(data["features"]):
        properties = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        parts = iter_geometry_parts(geometry)
        flattened = flatten_parts(parts)
        if not flattened:
            continue
        start_lon, start_lat = flattened[0][:2]
        end_lon, end_lat = flattened[-1][:2]
        source_line_id = str(properties.get("id") or idx)
        line_id = f"{idx:05d}_{source_line_id}"
        rows.append(
            {
                "line_id": line_id,
                "source_line_id": source_line_id,
                "source_index": idx,
                "geometry_type": geometry.get("type"),
                "geometry_part_count": len(parts),
                "start_lon": start_lon,
                "start_lat": start_lat,
                "end_lon": end_lon,
                "end_lat": end_lat,
                "tipo": properties.get("tipo"),
                "tensao_de": properties.get("tensao_de"),
                "situacao": properties.get("situacao"),
                "linha_mt_i": properties.get("linha_mt_i"),
                "codigo_da": properties.get("codigo_da"),
                "con_code": properties.get("con_code"),
                "dis_code": properties.get("dis_code"),
                "con_name": properties.get("con_name"),
                "dis_name": properties.get("dis_name"),
            }
        )
        shapes.append({"line_id": line_id, "parts": parts})
    return pd.DataFrame(rows), shapes


def build_projection(lines: pd.DataFrame, substations: pd.DataFrame) -> LocalMetricProjection:
    lons = pd.concat([lines["start_lon"], lines["end_lon"], substations["lon"]]).astype(float)
    lats = pd.concat([lines["start_lat"], lines["end_lat"], substations["lat"]]).astype(float)
    return LocalMetricProjection(lon0=float(lons.mean()), lat0=float(lats.mean()))


def project_lines_and_substations(
    lines: pd.DataFrame,
    shapes: list[dict[str, Any]],
    substations: pd.DataFrame,
    projection: LocalMetricProjection,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, list[list[tuple[float, float]]]]]:
    lines = lines.copy()
    substations = substations.copy()

    for prefix in ["start", "end"]:
        xy = lines.apply(
            lambda row: projection.xy(float(row[f"{prefix}_lon"]), float(row[f"{prefix}_lat"])),
            axis=1,
        )
        lines[f"{prefix}_x_m"] = xy.apply(lambda value: value[0])
        lines[f"{prefix}_y_m"] = xy.apply(lambda value: value[1])

    station_xy = substations.apply(lambda row: projection.xy(float(row["lon"]), float(row["lat"])), axis=1)
    substations["x_m"] = station_xy.apply(lambda value: value[0])
    substations["y_m"] = station_xy.apply(lambda value: value[1])

    projected_shapes = {}
    length_by_id = {}
    for shape in shapes:
        projected_parts = []
        for part in shape["parts"]:
            projected_parts.append([projection.xy(float(lon), float(lat)) for lon, lat, *_ in part])
        projected_shapes[shape["line_id"]] = projected_parts
        length_by_id[shape["line_id"]] = projected_length_m(shape["parts"], projection)
    lines["geometry_length_m"] = lines["line_id"].map(length_by_id)
    return lines, substations, projected_shapes


def nearest_substations(lines: pd.DataFrame, substations: pd.DataFrame) -> pd.DataFrame:
    station_xy = substations[["x_m", "y_m"]].to_numpy(dtype=float)
    station_codes = substations["bus_id"].astype(str).to_numpy()
    station_names = substations["bus_name"].astype(str).to_numpy()
    endpoint_rows = []
    for _, line in lines.iterrows():
        for endpoint in ["start", "end"]:
            point = np.array([line[f"{endpoint}_x_m"], line[f"{endpoint}_y_m"]], dtype=float)
            distances = np.sqrt(np.sum((station_xy - point) ** 2, axis=1))
            nearest_idx = int(np.argmin(distances))
            row = {
                "line_id": line["line_id"],
                "endpoint": endpoint,
                "endpoint_lon": line[f"{endpoint}_lon"],
                "endpoint_lat": line[f"{endpoint}_lat"],
                "endpoint_x_m": line[f"{endpoint}_x_m"],
                "endpoint_y_m": line[f"{endpoint}_y_m"],
                "nearest_bus": station_codes[nearest_idx],
                "nearest_bus_name": station_names[nearest_idx],
                "nearest_distance_m": float(distances[nearest_idx]),
                "voltage": line["tensao_de"],
                "type": line["tipo"],
            }
            for threshold in THRESHOLDS_M:
                row[f"matched_{threshold}m"] = row["nearest_distance_m"] <= threshold
            endpoint_rows.append(row)
    return pd.DataFrame(endpoint_rows)


def threshold_summary(endpoint_matches: pd.DataFrame, total_lines: int) -> list[dict[str, Any]]:
    rows = []
    for threshold in THRESHOLDS_M:
        matched_column = f"matched_{threshold}m"
        pivot = endpoint_matches.pivot(index="line_id", columns="endpoint", values=matched_column)
        nearest_bus = endpoint_matches.pivot(index="line_id", columns="endpoint", values="nearest_bus")
        distance = endpoint_matches.pivot(index="line_id", columns="endpoint", values="nearest_distance_m")
        both = pivot["start"] & pivot["end"]
        one = pivot["start"] ^ pivot["end"]
        none = ~(pivot["start"] | pivot["end"])
        self_loops = both & (nearest_bus["start"] == nearest_bus["end"])
        different_bus = both & (nearest_bus["start"] != nearest_bus["end"])
        max_distance = pd.concat([distance["start"], distance["end"]], axis=1).max(axis=1)
        suspicious = different_bus & (max_distance > SUSPICIOUS_DISTANCE_M)
        clean_inter_substation = different_bus & ~suspicious
        matched_endpoint_count = int(endpoint_matches[matched_column].sum())
        matched_buses = endpoint_matches.loc[endpoint_matches[matched_column], "nearest_bus"]
        rows.append(
            {
                "threshold_m": threshold,
                "endpoint_match_rate_pct": round(100 * matched_endpoint_count / (2 * total_lines), 4),
                "lines_both_endpoints_matched": int(both.sum()),
                "lines_one_endpoint_matched": int(one.sum()),
                "lines_no_endpoints_matched": int(none.sum()),
                "self_loops": int(self_loops.sum()),
                "clean_inter_substation_lines": int(clean_inter_substation.sum()),
                "suspicious_long_distance_matches": int(suspicious.sum()),
                "unique_substations_used": int(matched_buses.nunique()),
            }
        )
    return rows


def choose_threshold(summary_rows: list[dict[str, Any]]) -> int:
    # Prefer the smallest threshold that maximizes clean distinct-bus matches.
    best_row = None
    best_score = -1e9
    for row in summary_rows:
        score = (
            1000 * row["clean_inter_substation_lines"]
            - 500 * row["suspicious_long_distance_matches"]
            - row["threshold_m"]
        )
        if score > best_score:
            best_row = row
            best_score = score
    return int(best_row["threshold_m"])


def candidate_line_branches(
    lines: pd.DataFrame,
    endpoint_matches: pd.DataFrame,
    threshold_m: int,
) -> pd.DataFrame:
    match_col = f"matched_{threshold_m}m"
    starts = endpoint_matches[endpoint_matches["endpoint"] == "start"].set_index("line_id")
    ends = endpoint_matches[endpoint_matches["endpoint"] == "end"].set_index("line_id")
    rows = []
    for _, line in lines.iterrows():
        line_id = line["line_id"]
        start = starts.loc[line_id]
        end = ends.loc[line_id]
        start_matched = bool(start[match_col])
        end_matched = bool(end[match_col])
        from_bus = start["nearest_bus"] if start_matched else ""
        to_bus = end["nearest_bus"] if end_matched else ""
        from_name = start["nearest_bus_name"] if start_matched else ""
        to_name = end["nearest_bus_name"] if end_matched else ""
        max_distance = max(float(start["nearest_distance_m"]), float(end["nearest_distance_m"]))
        if start_matched and end_matched:
            if from_bus == to_bus:
                classification = "self-loop"
            elif max_distance > SUSPICIOUS_DISTANCE_M:
                classification = "suspicious long-distance match"
            else:
                classification = "inter-substation"
        elif start_matched or end_matched:
            classification = "dangling"
        else:
            classification = "unmatched"
        canonical = sorted([from_bus, to_bus]) if from_bus and to_bus else ["", ""]
        rows.append(
            {
                "line_id": line_id,
                "threshold_m": threshold_m,
                "from_bus": from_bus,
                "from_bus_name": from_name,
                "to_bus": to_bus,
                "to_bus_name": to_name,
                "canonical_from_bus": canonical[0],
                "canonical_to_bus": canonical[1],
                "classification": classification,
                "start_distance_m": round(float(start["nearest_distance_m"]), 3),
                "end_distance_m": round(float(end["nearest_distance_m"]), 3),
                "max_endpoint_distance_m": round(max_distance, 3),
                "geometry_length_m": round(float(line["geometry_length_m"]), 3),
                "voltage": line["tensao_de"],
                "type": line["tipo"],
                "situation": line["situacao"],
                "geometry_type": line["geometry_type"],
                "geometry_part_count": int(line["geometry_part_count"]),
                "source_line_id": line["source_line_id"],
                "line_code": line["codigo_da"],
                "municipality": line["con_name"],
                "district": line["dis_name"],
                "r": "MISSING_NOT_ESTIMATED",
                "x": "MISSING_NOT_ESTIMATED",
                "b": "MISSING_NOT_ESTIMATED",
                "thermal_limit": "MISSING_NOT_ESTIMATED",
                "transformer_impedance": "MISSING_NOT_ESTIMATED",
                "tap_settings": "MISSING_NOT_ESTIMATED",
            }
        )
    candidates = pd.DataFrame(rows)
    merge_keys = ["canonical_from_bus", "canonical_to_bus", "voltage", "type"]
    valid = candidates["classification"].isin(["inter-substation", "suspicious long-distance match"])
    merged_ids = {}
    for idx, key_values in enumerate(
        candidates.loc[valid, merge_keys].drop_duplicates().itertuples(index=False, name=None),
        start=1,
    ):
        merged_ids[key_values] = f"ATBR_{idx:05d}"
    candidates["merged_branch_id"] = candidates.apply(
        lambda row: merged_ids.get(tuple(row[key] for key in merge_keys), ""), axis=1
    )
    counts = candidates.loc[valid].groupby("merged_branch_id")["line_id"].transform("count")
    candidates.loc[valid, "merged_segment_count"] = counts
    candidates["merged_segment_count"] = candidates["merged_segment_count"].fillna(0).astype(int)
    return candidates


def merge_candidate_branches(candidates: pd.DataFrame) -> pd.DataFrame:
    valid = candidates["classification"].isin(["inter-substation", "suspicious long-distance match"])
    valid_candidates = candidates.loc[valid & candidates["merged_branch_id"].ne("")].copy()
    if valid_candidates.empty:
        return pd.DataFrame()
    rows = []
    for branch_id, group in valid_candidates.groupby("merged_branch_id"):
        rows.append(
            {
                "branch_id": branch_id,
                "from_bus": group["canonical_from_bus"].iloc[0],
                "to_bus": group["canonical_to_bus"].iloc[0],
                "voltage": group["voltage"].iloc[0],
                "type": group["type"].iloc[0],
                "segment_count": int(len(group)),
                "line_ids": ",".join(group["line_id"].astype(str).tolist()),
                "total_geometry_length_m": round(float(group["geometry_length_m"].sum()), 3),
                "max_endpoint_distance_m": round(float(group["max_endpoint_distance_m"].max()), 3),
                "classification": "suspicious long-distance match"
                if (group["classification"] == "suspicious long-distance match").any()
                else "inter-substation",
                "r": "MISSING_NOT_ESTIMATED",
                "x": "MISSING_NOT_ESTIMATED",
                "b": "MISSING_NOT_ESTIMATED",
                "thermal_limit": "MISSING_NOT_ESTIMATED",
            }
        )
    return pd.DataFrame(rows)


def scalar(value: Any) -> Any:
    if pd.isna(value):
        return ""
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return float(value)
    return value


def build_graph(substations: pd.DataFrame, merged: pd.DataFrame) -> nx.MultiGraph:
    graph = nx.MultiGraph()
    for _, row in substations.iterrows():
        attrs = {
            "label": scalar(row.get("bus_name")),
            "lat": scalar(row.get("lat")),
            "lon": scalar(row.get("lon")),
            "district": scalar(row.get("distrito")),
            "municipality": scalar(row.get("concelho")),
            "voltage_label": scalar(row.get("tensao")),
            "installed_power_mva": scalar(row.get("potencia_instalada")),
            "guaranteed_power_mva": scalar(row.get("potencia_garantida")),
            "r": "MISSING_NOT_ESTIMATED",
            "x": "MISSING_NOT_ESTIMATED",
            "b": "MISSING_NOT_ESTIMATED",
            "thermal_limit": "MISSING_NOT_ESTIMATED",
            "transformer_impedance": "MISSING_NOT_ESTIMATED",
            "tap_settings": "MISSING_NOT_ESTIMATED",
        }
        graph.add_node(str(row["bus_id"]), **attrs)
    for _, row in merged.iterrows():
        if row["classification"] != "inter-substation":
            continue
        graph.add_edge(
            str(row["from_bus"]),
            str(row["to_bus"]),
            key=str(row["branch_id"]),
            branch_id=str(row["branch_id"]),
            voltage=scalar(row["voltage"]),
            type=scalar(row["type"]),
            segment_count=int(row["segment_count"]),
            total_geometry_length_m=float(row["total_geometry_length_m"]),
            max_endpoint_distance_m=float(row["max_endpoint_distance_m"]),
            r="MISSING_NOT_ESTIMATED",
            x="MISSING_NOT_ESTIMATED",
            b="MISSING_NOT_ESTIMATED",
            thermal_limit="MISSING_NOT_ESTIMATED",
        )
    return graph


def graph_statistics(graph: nx.MultiGraph) -> dict[str, Any]:
    simple = nx.Graph()
    simple.add_nodes_from(graph.nodes())
    simple.add_edges_from((u, v) for u, v in graph.edges())
    components = list(nx.connected_components(simple))
    component_sizes = sorted([len(component) for component in components], reverse=True)
    degrees = dict(simple.degree())
    return {
        "number_of_nodes": graph.number_of_nodes(),
        "number_of_edges": graph.number_of_edges(),
        "connected_components": len(components),
        "largest_connected_component_size": component_sizes[0] if component_sizes else 0,
        "isolated_nodes": int(sum(1 for _, degree in simple.degree() if degree == 0)),
        "degree_distribution": dict(sorted(Counter(degrees.values()).items())),
        "component_sizes_top10": component_sizes[:10],
    }


def line_parts_lonlat(shape_by_id: dict[str, list[list[list[float]]]], line_id: str):
    for part in shape_by_id.get(line_id, []):
        yield [point[0] for point in part], [point[1] for point in part]


def plot_base(ax, title: str):
    ax.set_title(title)
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, alpha=0.2)


def save_maps(
    raw_shapes: list[dict[str, Any]],
    substations: pd.DataFrame,
    candidates: pd.DataFrame,
    merged: pd.DataFrame,
    graph: nx.MultiGraph,
) -> dict[str, str]:
    shape_by_id = {shape["line_id"]: shape["parts"] for shape in raw_shapes}
    map_paths = {}

    def save_current(name):
        path = config.REPORTS_DIR / name
        plt.tight_layout()
        plt.savefig(path, dpi=200)
        plt.close()
        map_paths[name] = str(path)

    plt.figure(figsize=(9, 10))
    ax = plt.gca()
    for shape in raw_shapes:
        for part in shape["parts"]:
            ax.plot([p[0] for p in part], [p[1] for p in part], color="#9aa0a6", linewidth=0.35, alpha=0.55)
    ax.scatter(substations["lon"], substations["lat"], s=9, color="#d62728", label="AT substations", zorder=4)
    plot_base(ax, "All RND AT lines and AT substations")
    ax.legend(loc="lower left")
    save_current("06_at_map_all_lines_substations.png")

    plt.figure(figsize=(9, 10))
    ax = plt.gca()
    matched = candidates[candidates["classification"].isin(["inter-substation", "suspicious long-distance match"])]
    for line_id in matched["line_id"]:
        for part in shape_by_id.get(str(line_id), []):
            ax.plot([p[0] for p in part], [p[1] for p in part], color="#1f77b4", linewidth=0.5, alpha=0.7)
    used = set(matched["from_bus"]) | set(matched["to_bus"])
    used_substations = substations[substations["bus_id"].isin(used)]
    ax.scatter(used_substations["lon"], used_substations["lat"], s=10, color="#2ca02c", label="Used substations", zorder=4)
    plot_base(ax, "Matched AT line candidates")
    ax.legend(loc="lower left")
    save_current("06_at_map_matched_lines.png")

    plt.figure(figsize=(9, 10))
    ax = plt.gca()
    colors = {
        "dangling": "#ff7f0e",
        "unmatched": "#d62728",
        "self-loop": "#9467bd",
        "suspicious long-distance match": "#111111",
    }
    for classification, color in colors.items():
        subset = candidates[candidates["classification"] == classification]
        for line_id in subset["line_id"]:
            for part in shape_by_id.get(str(line_id), []):
                ax.plot([p[0] for p in part], [p[1] for p in part], color=color, linewidth=0.6, alpha=0.75)
        if not subset.empty:
            ax.plot([], [], color=color, label=f"{classification}: {len(subset)}")
    ax.scatter(substations["lon"], substations["lat"], s=6, color="#555555", alpha=0.6, zorder=4)
    plot_base(ax, "Unmatched, dangling, self-loop, and suspicious AT lines")
    ax.legend(loc="lower left", fontsize=8)
    save_current("06_at_map_problem_lines.png")

    simple = nx.Graph()
    simple.add_nodes_from(graph.nodes())
    simple.add_edges_from((u, v) for u, v in graph.edges())
    components = list(nx.connected_components(simple))
    largest = max(components, key=len) if components else set()
    lcc_edges = []
    for _, row in merged.iterrows():
        if row["classification"] != "inter-substation":
            continue
        if row["from_bus"] in largest and row["to_bus"] in largest:
            lcc_edges.extend(str(line_id) for line_id in row["line_ids"].split(","))
    plt.figure(figsize=(9, 10))
    ax = plt.gca()
    for line_id in lcc_edges:
        for part in shape_by_id.get(str(line_id), []):
            ax.plot([p[0] for p in part], [p[1] for p in part], color="#0b5cad", linewidth=0.55, alpha=0.7)
    lcc_substations = substations[substations["bus_id"].isin(largest)]
    ax.scatter(lcc_substations["lon"], lcc_substations["lat"], s=10, color="#2ca02c", label="Largest component substations", zorder=4)
    plot_base(ax, "Largest connected component")
    ax.legend(loc="lower left")
    save_current("06_at_map_largest_connected_component.png")
    return map_paths


def build_report(
    summary: dict[str, Any],
    threshold_rows: list[dict[str, Any]],
    class_counts: dict[str, int],
    merged: pd.DataFrame,
    map_paths: dict[str, str],
):
    best = summary["selected_threshold_m"]
    total_lines = summary["input"]["at_line_count"]
    full_bus_lines = class_counts.get("inter-substation", 0) + class_counts.get(
        "suspicious long-distance match", 0
    )
    full_bus_pct = round(100 * full_bus_lines / total_lines, 3)
    failure_rows = [
        {"Failure mode": key, "Lines": value, "Share %": round(100 * value / total_lines, 3)}
        for key, value in sorted(class_counts.items())
        if key != "inter-substation"
    ]
    graph_stats = summary["graph_statistics"]
    text = [
        "# 06 AT Topology Reconstruction Feasibility",
        "",
        f"Generated: {utc_now()}",
        "",
        "Scope: Step 2A only. This reconstructs candidate AT topology from RND AT line geometries and E-REDES AT substation coordinates. Electrical parameters remain explicitly missing; no power flow, OPF, or ML model is run.",
        "",
        "## Inputs",
        "",
        f"- AT lines: `data/raw/rede-at-teste.geojson` ({total_lines} line features)",
        f"- AT substations: `data/raw/se-at_2025.csv` ({summary['input']['at_substation_rows']} rows; {summary['input']['unique_at_substation_codes']} unique substation codes with coordinates)",
        "- Node enrichment sources loaded for identifiers/capacity context only: `carga-na-subestacao.csv`, `caracteristicas-da-rede.csv`, `capacidade-rececao-rnd.csv`",
        "",
        "## Metric Geometry Handling",
        "",
        "Coordinates were converted from lon/lat into a local equirectangular metric projection centered on the AT line/substation centroid. This avoids unavailable GIS dependencies while preserving meter-scale nearest-neighbor distances for 100-1000 m snapping tests.",
        "",
        f"- Projection center longitude: {summary['projection']['lon0']:.6f}",
        f"- Projection center latitude: {summary['projection']['lat0']:.6f}",
        "- Units: meters",
        "",
        "## Snapping Threshold Test",
        "",
        markdown_table(
            threshold_rows,
            [
                "threshold_m",
                "endpoint_match_rate_pct",
                "lines_both_endpoints_matched",
                "lines_one_endpoint_matched",
                "lines_no_endpoints_matched",
                "self_loops",
                "clean_inter_substation_lines",
                "suspicious_long_distance_matches",
                "unique_substations_used",
            ],
        ),
        "",
        f"Selected threshold: **{best} m**. The selection uses the smallest threshold that maximizes clean distinct-substation matches before 1000 m introduces long-distance suspicious matches.",
        "",
        "## Candidate Line Classification",
        "",
        markdown_table(
            [{"Classification": key, "Lines": value, "Share %": round(100 * value / total_lines, 3)} for key, value in sorted(class_counts.items())],
            ["Classification", "Lines", "Share %"],
        ),
        "",
        f"AT lines receiving both `from_bus` and `to_bus`: **{full_bus_lines}/{total_lines} ({full_bus_pct}%)**.",
        "",
        "## Merged Branches",
        "",
        f"Line segments were merged by canonical `(from_bus, to_bus, voltage, type)`. Merged branch count: **{len(merged)}**.",
        "",
        "Electrical branch parameters are not estimated and are stored as `MISSING_NOT_ESTIMATED`: `r`, `x`, `b`, `thermal_limit`, transformer impedance, and tap settings.",
        "",
        "## Graph Statistics",
        "",
        markdown_table(
            [
                {"Metric": "nodes", "Value": graph_stats["number_of_nodes"]},
                {"Metric": "edges", "Value": graph_stats["number_of_edges"]},
                {"Metric": "connected_components", "Value": graph_stats["connected_components"]},
                {
                    "Metric": "largest_connected_component_size",
                    "Value": graph_stats["largest_connected_component_size"],
                },
                {"Metric": "isolated_nodes", "Value": graph_stats["isolated_nodes"]},
                {"Metric": "degree_distribution", "Value": graph_stats["degree_distribution"]},
            ],
            ["Metric", "Value"],
        ),
        "",
        "## Main Failure Modes",
        "",
        markdown_table(failure_rows, ["Failure mode", "Lines", "Share %"]),
        "",
        "The dominant failure modes are caused by RND line geometries that terminate away from the nearest listed AT substation, geometries that represent intra-station or very short local assets, and line segments whose endpoints snap to the same substation.",
        "",
        "## Maps",
        "",
        f"![All AT lines and substations]({Path(map_paths['06_at_map_all_lines_substations.png']).name})",
        "",
        f"![Matched AT lines]({Path(map_paths['06_at_map_matched_lines.png']).name})",
        "",
        f"![Problem AT lines]({Path(map_paths['06_at_map_problem_lines.png']).name})",
        "",
        f"![Largest connected component]({Path(map_paths['06_at_map_largest_connected_component.png']).name})",
        "",
        "## Final Answers",
        "",
        f"1. Can AT line geometries be converted into a reliable bus-branch topology? **Not directly.** The geometry and substation coordinates are technically usable, but endpoint snapping alone recovers too small a connected AT graph to be considered reliable.",
        f"2. Which snapping threshold is best? **{best} m** for this run.",
        f"3. What percentage of AT lines can receive `from_bus` and `to_bus`? **{full_bus_pct}%** at {best} m.",
        "4. Main failure modes: dangling endpoints, unmatched endpoints, self-loops, and long-distance snaps that should be reviewed before use.",
        "5. Is the result good enough for Step 2B? **Not as a reliable line-level topology input.** Step 2B can proceed only for transformer/multi-voltage node inventory work; topology-dependent reconstruction needs an additional line-segment connectivity pass before relying on these branches.",
    ]
    write_text(config.REPORTS_DIR / "06_at_topology_reconstruction.md", "\n".join(text))


def main():
    ensure_directories()
    lines, raw_shapes = load_at_lines()
    substations = load_at_substations()
    projection = build_projection(lines, substations)
    lines, substations, _ = project_lines_and_substations(lines, raw_shapes, substations, projection)
    endpoint_matches = nearest_substations(lines, substations)
    threshold_rows = threshold_summary(endpoint_matches, len(lines))
    selected_threshold = choose_threshold(threshold_rows)
    candidates = candidate_line_branches(lines, endpoint_matches, selected_threshold)
    merged = merge_candidate_branches(candidates)
    graph = build_graph(substations, merged)
    graph_stats = graph_statistics(graph)

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    endpoint_matches.to_csv(config.PROCESSED_DIR / "at_endpoint_matches.csv", index=False)
    candidates.to_csv(config.PROCESSED_DIR / "at_candidate_branches.csv", index=False)
    merged.to_csv(config.PROCESSED_DIR / "at_merged_candidate_branches.csv", index=False)
    nx.write_graphml(graph, config.PROCESSED_DIR / "at_topology_graph.graphml")

    class_counts = candidates["classification"].value_counts().to_dict()
    maps = save_maps(raw_shapes, substations, candidates, merged, graph)

    summary = {
        "generated_at": utc_now(),
        "scope": "Step 2A AT topology reconstruction feasibility only",
        "input": {
            "at_line_count": int(len(lines)),
            "at_substation_rows": int(len(substations)),
            "unique_at_substation_codes": int(substations["bus_id"].nunique()),
            "duplicate_at_substation_codes": int(len(substations) - substations["bus_id"].nunique()),
        },
        "projection": {
            "method": "local_equirectangular_metric_projection",
            "lon0": projection.lon0,
            "lat0": projection.lat0,
            "units": "meters",
        },
        "threshold_summary": threshold_rows,
        "selected_threshold_m": selected_threshold,
        "classification_counts": class_counts,
        "candidate_branch_file": str(config.PROCESSED_DIR / "at_candidate_branches.csv"),
        "merged_branch_file": str(config.PROCESSED_DIR / "at_merged_candidate_branches.csv"),
        "graph_statistics": graph_stats,
        "electrical_parameters": {
            "r": "MISSING_NOT_ESTIMATED",
            "x": "MISSING_NOT_ESTIMATED",
            "b": "MISSING_NOT_ESTIMATED",
            "thermal_limit": "MISSING_NOT_ESTIMATED",
            "transformer_impedance": "MISSING_NOT_ESTIMATED",
            "tap_settings": "MISSING_NOT_ESTIMATED",
        },
        "maps": maps,
    }
    write_json(config.PROCESSED_DIR / "at_topology_summary.json", summary)
    build_report(summary, threshold_rows, class_counts, merged, maps)


if __name__ == "__main__":
    main()
