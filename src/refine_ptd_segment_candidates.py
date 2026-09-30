#!/usr/bin/env python3
"""Find nearest mapped MV line segment for every public PTD.

This supplements vertex-only matching. A spatial projection onto an OSM way
is still an unverified candidate connection, never a utility terminal record.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

import duckdb
import numpy as np
from pyproj import Transformer
from shapely import linestrings, points
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
TOPOLOGY = ROOT / "output/distribution_topology"
PTD = ROOT / "output/feasibility/ptd_national_export.json"
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/distribution_topology/ptd_segment_candidates.csv"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--topology", type=Path, default=TOPOLOGY)
    parser.add_argument("--ptd", type=Path, default=PTD)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    forward = Transformer.from_crs("EPSG:4326", "EPSG:3035", always_xy=True)
    inverse = Transformer.from_crs("EPSG:3035", "EPSG:4326", always_xy=True)
    with (args.topology / "osm_distribution_nodes.csv").open(newline="") as handle:
        node_geo = {r["node_id"]: (float(r["lon"]), float(r["lat"])) for r in csv.DictReader(handle)}
    with (args.topology / "osm_distribution_segments.csv").open(newline="") as handle:
        segments = [r for r in csv.DictReader(handle) if float(r["voltage_kv"])>=1]
    node_ids = sorted({r["from_node"] for r in segments} | {r["to_node"] for r in segments})
    lon = np.array([node_geo[n][0] for n in node_ids])
    lat = np.array([node_geo[n][1] for n in node_ids])
    x, y = forward.transform(lon, lat)
    xy = dict(zip(node_ids, zip(x, y)))
    a = np.array([xy[r["from_node"]] for r in segments])
    b = np.array([xy[r["to_node"]] for r in segments])
    graph = STRtree(linestrings(np.stack((a, b), axis=1)))
    ptds = json.loads(args.ptd.read_text(encoding="utf-8"))
    if isinstance(ptds, dict):
        ptds = ptds["results"]
    ptds = [p for p in ptds if p.get("coordenadas_geo")]
    px, py = forward.transform(
        np.array([p["coordenadas_geo"]["lon"] for p in ptds]),
        np.array([p["coordenadas_geo"]["lat"] for p in ptds]))
    point_array = points(px, py)
    selected = np.empty(len(ptds), dtype=np.int64)
    # Batching bounds temporary GEOS allocations on the national input.
    for start in range(0, len(ptds), 5000):
        end = min(start+5000, len(ptds))
        indices = graph.query_nearest(point_array[start:end], all_matches=False)
        if len(indices[0]) != end-start:
            raise RuntimeError("Nearest-segment index did not resolve every PTD")
        selected[start:end][indices[0]] = indices[1]
    va = a[selected]
    vb = b[selected]
    vec = vb-va
    den = np.einsum("ij,ij->i", vec, vec)
    t = np.clip(np.einsum("ij,ij->i", np.stack((px,py),axis=1)-va, vec)/den, 0, 1)
    projected = va+t[:,None]*vec
    distance_km = np.linalg.norm(np.stack((px,py),axis=1)-projected, axis=1)/1000
    projected_lon, projected_lat = inverse.transform(projected[:,0], projected[:,1])
    with (args.topology / "ptd_osm_candidates.csv").open(newline="") as handle:
        vertex_rows = {r["ptd_code"]: r for r in csv.DictReader(handle)}
    with (args.topology / "station_mv_candidates.csv").open(newline="") as handle:
        anchored = {r["component_id"] for r in csv.DictReader(handle)
                    if r["anchor_status"]=="NEAR_STATION_CANDIDATE"}
    fields = ["ptd_code", "osm_segment_id_candidate", "osm_way_id_candidate", "voltage_kv_candidate",
              "component_id", "projected_lon", "projected_lat", "segment_fraction",
              "distance_to_osm_segment_km", "vertex_candidate_component_id",
              "distance_to_osm_node_km", "same_component_as_vertex_candidate",
              "connection_status", "evidence_status"]
    counts = Counter()
    changed_components = 0
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for i, p in enumerate(ptds):
            code = p["cod_instalacao"]
            segment = segments[int(selected[i])]
            vertex = vertex_rows[code]
            same = segment["component_id"] == vertex["component_id"]
            changed_components += not same
            near = distance_km[i] <= 0.2
            status = ("NEAR_ANCHORED_COMPONENT" if segment["component_id"] in anchored else
                      "NEAR_UNANCHORED_COMPONENT") if near else "SPATIAL_PROXY"
            counts[status] += 1
            writer.writerow({"ptd_code": code,
                             "osm_segment_id_candidate": segment["edge_id"],
                             "osm_way_id_candidate": segment["osm_way_id"],
                             "voltage_kv_candidate": segment["voltage_kv"],
                             "component_id": segment["component_id"],
                             "projected_lon": round(float(projected_lon[i]), 8),
                             "projected_lat": round(float(projected_lat[i]), 8),
                             "segment_fraction": round(float(t[i]), 8),
                             "distance_to_osm_segment_km": round(float(distance_km[i]), 6),
                             "vertex_candidate_component_id": vertex["component_id"],
                             "distance_to_osm_node_km": vertex["distance_to_osm_node_km"],
                             "same_component_as_vertex_candidate": same,
                             "connection_status": status,
                             "evidence_status": "OSM_SPATIAL_SEGMENT_PROJECTION_UNVERIFIED"})
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS candidate")
        con.execute("CREATE OR REPLACE TABLE candidate.ptd_segment_candidates AS "
                    "SELECT * FROM read_csv_auto(?, header=true)", [str(args.output)])
        checks = {
            "ptd_rows": con.execute("SELECT count(*) FROM candidate.ptd_segment_candidates").fetchone()[0],
            "unique_ptds": con.execute("SELECT count(DISTINCT ptd_code) FROM candidate.ptd_segment_candidates").fetchone()[0],
            "orphan_segments": con.execute("""SELECT count(*) FROM candidate.ptd_segment_candidates p
                LEFT JOIN candidate.osm_segments s ON p.osm_segment_id_candidate=s.edge_id
                WHERE s.edge_id IS NULL""").fetchone()[0],
            "max_segment_distance_km": con.execute("SELECT max(distance_to_osm_segment_km) FROM candidate.ptd_segment_candidates").fetchone()[0],
            "improved_distance_count": con.execute("""SELECT count(*) FROM candidate.ptd_segment_candidates
                WHERE distance_to_osm_segment_km + 0.001 < distance_to_osm_node_km""").fetchone()[0],
        }
    errors = []
    if checks["ptd_rows"] != len(ptds) or checks["unique_ptds"] != len(ptds) or checks["orphan_segments"]:
        errors.append("PTD coverage or segment foreign key failed")
    report = {"result": "PASS" if not errors else "FAIL", "checks": checks,
              "connection_status": dict(counts), "changed_candidate_component_count": changed_components,
              "errors": errors,
              "scope": "Nearest OSM MV geometry only; PTD and source electrical terminals remain unverified"}
    args.output.with_suffix(".validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
