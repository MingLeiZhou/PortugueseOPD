#!/usr/bin/env python3
"""Export the normalized all-voltage database as viewport-loadable web-map chunks.

The output deliberately keeps REAL_PUBLIC, INFERRED and SIMULATED objects
separate in feature properties. Compact row arrays are converted to GeoJSON by
the browser only for cells intersecting the current viewport.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Iterable

import duckdb
from shapely.geometry import MultiPoint, mapping


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
DEFAULT_OUTPUT = ROOT / "portuguese_hv_network/site/public/data/all-voltage"
ORIGIN_LON = -10.0
ORIGIN_LAT = 36.0
CELL_DEGREES = 0.25


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def rounded(value: Any, digits: int = 6) -> Any:
    return None if value is None else round(float(value), digits)


def export_chunks(
    connection: duckdb.DuckDBPyConnection,
    output: Path,
    layer: str,
    query: str,
    encode: Callable[[tuple[Any, ...]], list[Any]],
    batch_size: int = 50_000,
) -> dict[str, Any]:
    layer_dir = output / "chunks" / layer
    layer_dir.mkdir(parents=True, exist_ok=True)
    cursor = connection.execute(query)
    chunks: dict[str, dict[str, Any]] = {}
    current_key: str | None = None
    handle = None
    row_count = 0
    first = True

    def close_current() -> None:
        nonlocal handle
        if handle is not None:
            handle.write("]}")
            handle.close()
            handle = None

    try:
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            for row in rows:
                cell_x, cell_y = int(row[0]), int(row[1])
                key = f"{cell_x}_{cell_y}"
                if key != current_key:
                    close_current()
                    current_key = key
                    path = layer_dir / f"{key}.json"
                    handle = path.open("w", encoding="utf-8")
                    handle.write('{"rows":[')
                    first = True
                    chunks[key] = {
                        "url": f"chunks/{layer}/{key}.json",
                        "count": 0,
                        "bbox": [
                            ORIGIN_LON + cell_x * CELL_DEGREES,
                            ORIGIN_LAT + cell_y * CELL_DEGREES,
                            ORIGIN_LON + (cell_x + 1) * CELL_DEGREES,
                            ORIGIN_LAT + (cell_y + 1) * CELL_DEGREES,
                        ],
                    }
                if not first:
                    handle.write(",")
                handle.write(compact_json(encode(row[2:])))
                first = False
                chunks[key]["count"] += 1
                row_count += 1
    finally:
        close_current()

    return {"count": row_count, "chunk_count": len(chunks), "chunks": chunks}


def cell_expression(lon: str, lat: str) -> str:
    return (
        f"CAST(FLOOR(({lon} - ({ORIGIN_LON})) / {CELL_DEGREES}) AS INTEGER), "
        f"CAST(FLOOR(({lat} - ({ORIGIN_LAT})) / {CELL_DEGREES}) AS INTEGER)"
    )


def build_operating_regions(connection: duckdb.DuckDBPyConnection, output: Path) -> int:
    features: list[dict[str, Any]] = []

    large_meta = {
        row[0]: row[1:]
        for row in connection.execute(
            """
            SELECT zone_id, station_code, component_id, ptd_count,
                   synchronized_load_mw, inferred_feeder_count,
                   minimum_voltage_pu, max_line_loading_percent,
                   max_transformer_loading_percent
            FROM model.mv_operating_zone
            """
        ).fetchall()
    }
    large_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for zone_id, lon, lat in connection.execute(
        """
        SELECT d.zone_id, n.lon, n.lat
        FROM candidate.mv_zone_nodes z
        JOIN model.mv_operating_zone d
          ON d.station_code=z.candidate_station_code AND d.component_id=z.component_id
        JOIN candidate.mv_attachment_graph_node n ON n.node_id = z.node_id
        WHERE n.lon IS NOT NULL AND n.lat IS NOT NULL
        ORDER BY 1
        """
    ).fetchall():
        if zone_id in large_meta:
            large_points[zone_id].append((float(lon), float(lat)))

    small_meta = {
        row[0]: row[1:]
        for row in connection.execute(
            """
            SELECT component_id, source_count, ptd_count, synchronized_load_mw,
                   inferred_feeder_count, min_voltage_pu,
                   max_line_loading_percent, max_transformer_loading_percent
            FROM model.osm_component_operating_design
            """
        ).fetchall()
    }
    small_points: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for component_id, lon, lat in connection.execute(
        """
        SELECT n.component_id, n.lon, n.lat
        FROM candidate.mv_attachment_graph_node n
        JOIN model.osm_component_operating_design d USING (component_id)
        WHERE n.lon IS NOT NULL AND n.lat IS NOT NULL
        ORDER BY 1
        """
    ).fetchall():
        small_points[str(component_id)].append((float(lon), float(lat)))

    def geometry(points: Iterable[tuple[float, float]]) -> dict[str, Any]:
        hull = MultiPoint(list(points)).convex_hull
        if hull.geom_type != "Polygon":
            hull = hull.buffer(0.008)
        return mapping(hull)

    for zone_id, points in large_points.items():
        station, component, ptds, load, feeders, min_v, max_line, max_trafo = large_meta[zone_id]
        features.append({
            "type": "Feature",
            "geometry": geometry(points),
            "properties": {
                "id": f"MVZONE:{zone_id}",
                "region_type": "LARGE_SOURCE_ZONE",
                "station_code": station,
                "component_id": component,
                "ptd_count": ptds,
                "load_mw": rounded(load, 3),
                "feeder_count": feeders,
                "minimum_voltage_pu": rounded(min_v, 5),
                "max_line_loading_percent": rounded(max_line, 3),
                "max_transformer_loading_percent": rounded(max_trafo, 3),
                "evidence_status": "INFERRED_OPERATING_SUPPLY_ENVELOPE",
                "truth_class": "INFERRED",
            },
        })
    for component_id, points in small_points.items():
        sources, ptds, load, feeders, min_v, max_line, max_trafo = small_meta[component_id]
        features.append({
            "type": "Feature",
            "geometry": geometry(points),
            "properties": {
                "id": f"OSMREGION:{component_id}",
                "region_type": "BOUNDED_SOURCE_COMPONENT",
                "component_id": component_id,
                "source_count": sources,
                "ptd_count": ptds,
                "load_mw": rounded(load, 3),
                "feeder_count": feeders,
                "minimum_voltage_pu": rounded(min_v, 5),
                "max_line_loading_percent": rounded(max_line, 3),
                "max_transformer_loading_percent": rounded(max_trafo, 3),
                "evidence_status": "INFERRED_OPERATING_SUPPLY_ENVELOPE",
                "truth_class": "INFERRED",
            },
        })

    target = output / "operating-regions.geojson"
    target.write_text(compact_json({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    return len(features)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--skip-poles", action="store_true")
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    manifest: dict[str, Any] = {
        "version": 1,
        "cell_degrees": CELL_DEGREES,
        "origin": [ORIGIN_LON, ORIGIN_LAT],
        "scope": "Portugal public-data-based synthetic all-voltage simulation database",
        "truth_classes": {
            "REAL_PUBLIC": "Frozen public location or geometry",
            "INFERRED": "Deterministic engineering inference",
            "SIMULATED": "Explicit simulation/design object",
        },
        "layers": {},
    }

    with duckdb.connect(str(args.database), read_only=True) as connection:
        midpoint_lon = "(a.lon + b.lon) / 2.0"
        midpoint_lat = "(a.lat + b.lat) / 2.0"
        manifest["layers"]["mv_lines"] = {
            "minzoom": 7.4,
            "truth": "MIXED_PUBLIC_AND_INFERRED",
            **export_chunks(
                connection,
                output,
                "mv_lines",
                f"""
                SELECT {cell_expression(midpoint_lon, midpoint_lat)},
                       e.edge_id, e.voltage_kv, a.lon, a.lat, b.lon, b.lat,
                       CASE e.edge_type WHEN 'OSM_UNCHANGED_SEGMENT' THEN 0
                         WHEN 'OSM_SEGMENT_SPLIT_AT_INFERRED_PTD_TAP' THEN 1 ELSE 2 END,
                       CASE WHEN e.evidence_status = 'OSM_SHARED_NODE_TOPOLOGY' THEN 0 ELSE 1 END
                FROM candidate.mv_attachment_graph_edge e
                JOIN candidate.mv_attachment_graph_node a ON a.node_id=e.from_node
                JOIN candidate.mv_attachment_graph_node b ON b.node_id=e.to_node
                WHERE a.lon IS NOT NULL AND a.lat IS NOT NULL AND b.lon IS NOT NULL AND b.lat IS NOT NULL
                ORDER BY 1,2
                """,
                lambda r: [r[0], rounded(r[1], 3), rounded(r[2]), rounded(r[3]), rounded(r[4]), rounded(r[5]), r[6], r[7]],
            ),
        }

        lv_mid_lon = "(a.lon + b.lon) / 2.0"
        lv_mid_lat = "(a.lat + b.lat) / 2.0"
        manifest["layers"]["lv_lines"] = {
            "minzoom": 10.5,
            "truth": "SIMULATED_WITH_PUBLIC_GEOMETRY_DIRECTION_WHERE_AVAILABLE",
            **export_chunks(
                connection,
                output,
                "lv_lines",
                f"""
                SELECT {cell_expression(lv_mid_lon, lv_mid_lat)},
                       s.route_segment_id, ptd.ptd_code, a.lon, a.lat, b.lon, b.lat,
                       CASE route.installation_scenario WHEN 'OVERHEAD' THEN 0 ELSE 1 END,
                       route.route_basis, route.engineering_confidence_score
                FROM model.route_segment s
                JOIN model.route_point a ON a.route_point_id=s.from_route_point_id
                JOIN model.route_point b ON b.route_point_id=s.to_route_point_id
                JOIN model.route route ON route.route_id=s.route_id
                JOIN candidate.ptd_coordinates_national ptd ON ptd.ptd_code=route.ptd_code
                ORDER BY 1,2
                """,
                lambda r: [r[0], r[1], rounded(r[2]), rounded(r[3]), rounded(r[4]), rounded(r[5]), r[6], r[7], rounded(r[8], 3)],
            ),
        }

        manifest["layers"]["ptds"] = {
            "minzoom": 8.0,
            "truth": "REAL_PUBLIC_LOCATION_WITH_MIXED_ATTRIBUTES",
            **export_chunks(
                connection,
                output,
                "ptds",
                f"""
                WITH pv AS (
                  SELECT container_id, max(rated_mw) der_mw
                  FROM model.resource WHERE resource_type='SOLAR_DER' GROUP BY 1
                )
                SELECT {cell_expression('c.lon', 'c.lat')}, c.ptd_code, c.lon, c.lat,
                       coalesce(t.hv_kv,0.0), a.construction_type, a.customer_count_exact,
                       a.installed_transformer_kva, coalesce(pv.der_mw,0.0),
                       coalesce(a.connected_generation_kw,0.0), c.evidence_status
                FROM candidate.ptd_coordinates_national c
                LEFT JOIN candidate.ptd_public_attributes a USING (ptd_code)
                LEFT JOIN parameter.ptd_transformer_parameters t USING (ptd_code)
                LEFT JOIN pv ON pv.container_id='PTD:' || c.ptd_code
                WHERE c.lon IS NOT NULL AND c.lat IS NOT NULL
                ORDER BY 1,2
                """,
                lambda r: [r[0], rounded(r[1]), rounded(r[2]), rounded(r[3], 3), r[4], r[5], rounded(r[6], 1), rounded(r[7], 4), rounded(r[8], 2), r[9]],
            ),
        }

        manifest["layers"]["stations"] = {
            "minzoom": 5.5,
            "truth": "REAL_PUBLIC_LOCATION_AND_BOUNDARY_DATA",
            **export_chunks(
                connection,
                output,
                "stations",
                f"""
                SELECT {cell_expression('lon', 'lat')}, station_code, lon, lat,
                       mv_voltage_v/1000.0, installed_mva_public,
                       short_circuit_max_mv_mva_public, short_circuit_min_mv_mva_public,
                       anchor_status
                FROM candidate.station_sources
                WHERE lon IS NOT NULL AND lat IS NOT NULL
                ORDER BY 1,2
                """,
                lambda r: [r[0], rounded(r[1]), rounded(r[2]), rounded(r[3], 3), rounded(r[4], 2), rounded(r[5], 2), rounded(r[6], 2), r[7]],
            ),
        }

        manifest["layers"]["switchgear"] = {
            "minzoom": 11.5,
            "truth": "SIMULATED_SWITCHING_ABSTRACTION",
            **export_chunks(
                connection,
                output,
                "switchgear",
                f"""
                SELECT {cell_expression('coalesce(n.lon,c.lon,p.lon)', 'coalesce(n.lat,c.lat,p.lat)')},
                       s.switch_id, coalesce(n.lon,c.lon,p.lon), coalesce(n.lat,c.lat,p.lat),
                       CASE s.switch_kind WHEN 'CIRCUIT_BREAKER' THEN 0 ELSE 1 END,
                       s.rated_voltage_kv, s.normal_open, s.normal_position,
                       s.evidence_status
                FROM model.switching_device s
                JOIN model.connectivity_node n ON n.connectivity_node_id=s.upstream_connectivity_node_id
                LEFT JOIN model.equipment_container c ON c.container_id=n.container_id
                LEFT JOIN candidate.ptd_coordinates_national p
                  ON p.ptd_code=replace(s.controlled_equipment_id,'MVFEEDER:','')
                WHERE coalesce(n.lon,c.lon,p.lon) IS NOT NULL AND coalesce(n.lat,c.lat,p.lat) IS NOT NULL
                ORDER BY 1,2
                """,
                lambda r: [r[0], rounded(r[1]), rounded(r[2]), r[3], rounded(r[4], 3), bool(r[5]), r[6], r[7]],
            ),
        }

        if not args.skip_poles:
            manifest["layers"]["lv_poles"] = {
                "minzoom": 12.5,
                "truth": "REAL_PUBLIC_POINT",
                **export_chunks(
                    connection,
                    output,
                    "lv_poles",
                    f"""
                    SELECT {cell_expression('lon', 'lat')}, source_row_number, lon, lat,
                           municipality_code, evidence_status
                    FROM candidate.lv_public_poles_national
                    WHERE lon IS NOT NULL AND lat IS NOT NULL
                    ORDER BY 1,2
                    """,
                    lambda r: [r[0], rounded(r[1]), rounded(r[2]), r[3], r[4]],
                    batch_size=100_000,
                ),
            }

        manifest["operating_regions"] = {
            "url": "operating-regions.geojson",
            "count": build_operating_regions(connection, output),
            "truth": "INFERRED",
        }
        manifest["database_counts"] = {
            "ptds": connection.execute("SELECT count(*) FROM candidate.ptd_coordinates_national").fetchone()[0],
            "mv_edges": connection.execute("SELECT count(*) FROM candidate.mv_attachment_graph_edge").fetchone()[0],
            "lv_route_segments": connection.execute("SELECT count(*) FROM model.route_segment").fetchone()[0],
            "switches": connection.execute("SELECT count(*) FROM model.switching_device").fetchone()[0],
            "lv_poles": connection.execute("SELECT count(*) FROM candidate.lv_public_poles_national").fetchone()[0],
        }

    (output / "manifest.json").write_text(compact_json(manifest), encoding="utf-8")
    print(json.dumps({
        "result": "PASS",
        "output": str(output),
        "layers": {name: {"count": value["count"], "chunks": value["chunk_count"]} for name, value in manifest["layers"].items()},
        "operating_regions": manifest["operating_regions"]["count"],
    }, indent=2))


if __name__ == "__main__":
    main()
