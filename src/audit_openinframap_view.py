#!/usr/bin/env python3
"""Audit what an OpenInfraMap view can contribute to electrical topology.

OpenInfraMap renders OpenStreetMap data.  This audit uses the project's saved
OSM snapshot and the already extracted voltage-specific node/segment graph so
that the visual inspection can be reproduced.  Shared OSM nodes are reported
as geometry adjacency only; they are never promoted to operator-verified
terminals.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RAW_OSM = ROOT / "portuguese_hv_network/data/raw/osm/portugal_power_osm.json"
NODES = ROOT / "output/distribution_topology/osm_distribution_nodes.csv"
SEGMENTS = ROOT / "output/distribution_topology/osm_distribution_segments.csv"
OUT = ROOT / "output/all_voltage/openinframap_guimaraes_topology_audit.json"


def in_box(lat: float, lon: float, bbox: tuple[float, float, float, float]) -> bool:
    south, north, west, east = bbox
    return south <= lat <= north and west <= lon <= east


def raw_equipment(path: Path, bbox: tuple[float, float, float, float]) -> dict:
    south, north, west, east = bbox
    program = f'''[
      .elements[] |
      select(
        (.type=="node" and .lat>={south} and .lat<={north}
          and .lon>={west} and .lon<={east} and .tags.power!=null)
        or
        (.type=="way" and
          (.tags.power=="substation" or .tags.power=="transformer"
           or .tags.power=="switch" or .tags.power=="circuit_breaker"
           or .tags.power=="disconnector") and
          any(.geometry[]?; .lat>={south} and .lat<={north}
                            and .lon>={west} and .lon<={east}))
      ) |
      {{type,id,power:.tags.power,voltage:(.tags.voltage//""),nodes:(.nodes//[])}}
    ]'''
    proc = subprocess.run(
        ["jq", "-c", program, str(path)], check=True, capture_output=True, text=True
    )
    objects = json.loads(proc.stdout)
    counts = Counter((item["type"], item["power"], item["voltage"]) for item in objects)
    transformer_ids = {item["id"] for item in objects if item["type"] == "node" and item["power"] == "transformer"}

    reference_program = f'''[
      .elements[] |
      select(.type=="way" and .nodes!=null and
             any(.nodes[]; . as $n | {json.dumps(sorted(transformer_ids))} | index($n))) |
      {{id,power:.tags.power,voltage:(.tags.voltage//""),
        transformer_nodes:[.nodes[] | . as $n |
          select({json.dumps(sorted(transformer_ids))} | index($n))]}}
    ]'''
    refs: list[dict] = []
    if transformer_ids:
        proc = subprocess.run(
            ["jq", "-c", reference_program, str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        refs = json.loads(proc.stdout)
    return {
        "object_counts": [
            {"osm_type": key[0], "power": key[1], "voltage": key[2] or None, "count": value}
            for key, value in sorted(counts.items())
        ],
        "transformer_node_count": len(transformer_ids),
        "transformer_nodes_referenced_by_line_way": len(
            {node for row in refs if row["power"] in {"line", "minor_line", "cable"} for node in row["transformer_nodes"]}
        ),
        "transformer_line_references": refs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lat", type=float, default=41.43978)
    parser.add_argument("--lon", type=float, default=-8.30989)
    parser.add_argument("--lat-span", type=float, default=0.04)
    parser.add_argument("--lon-span", type=float, default=0.06)
    parser.add_argument("--raw-osm", type=Path, default=RAW_OSM)
    parser.add_argument("--nodes", type=Path, default=NODES)
    parser.add_argument("--segments", type=Path, default=SEGMENTS)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    bbox = (args.lat - args.lat_span, args.lat + args.lat_span,
            args.lon - args.lon_span, args.lon + args.lon_span)

    nodes: dict[str, dict] = {}
    voltage_nodes: Counter[str] = Counter()
    with args.nodes.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if in_box(float(row["lat"]), float(row["lon"]), bbox):
                nodes[row["node_id"]] = row
                voltage_nodes[row["voltage_kv"]] += 1

    degree: Counter[str] = Counter()
    voltage_segments: Counter[str] = Counter()
    ways: dict[str, set[str]] = defaultdict(set)
    components: set[str] = set()
    with args.segments.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row["from_node"] not in nodes and row["to_node"] not in nodes:
                continue
            degree[row["from_node"]] += 1
            degree[row["to_node"]] += 1
            voltage_segments[row["voltage_kv"]] += 1
            ways[row["voltage_kv"]].add(row["osm_way_id"])
            components.add(row["component_id"])

    equipment = raw_equipment(args.raw_osm, bbox)
    switch_count = sum(
        row["count"] for row in equipment["object_counts"]
        if row["power"] in {"switch", "circuit_breaker", "disconnector"}
    )
    lv_segments = sum(count for voltage, count in voltage_segments.items() if float(voltage) <= 1.0)
    mv_segments = sum(count for voltage, count in voltage_segments.items() if 1.0 < float(voltage) < 60.0)
    report = {
        "result": "PASS",
        "source": {
            "viewer_url": f"https://openinframap.org/#12.91/{args.lat}/{args.lon}",
            "data_model": "OpenStreetMap rendered by OpenInfraMap",
            "saved_osm_snapshot": str(args.raw_osm.relative_to(ROOT)),
        },
        "bbox": {"south": bbox[0], "north": bbox[1], "west": bbox[2], "east": bbox[3]},
        "geometry": {
            "node_count": len(nodes),
            "segment_count_touching_bbox": sum(voltage_segments.values()),
            "component_count_touching_bbox": len(components),
            "node_counts_by_voltage_kv": dict(sorted(voltage_nodes.items(), key=lambda item: float(item[0]))),
            "segment_counts_by_voltage_kv": dict(sorted(voltage_segments.items(), key=lambda item: float(item[0]))),
            "way_counts_by_voltage_kv": {key: len(value) for key, value in sorted(ways.items(), key=lambda item: float(item[0]))},
            "shared_nodes_degree_gt_2": sum(value > 2 for node, value in degree.items() if node in nodes),
            "dead_end_nodes_degree_1": sum(value == 1 for node, value in degree.items() if node in nodes),
            "maximum_geometry_degree": max((value for node, value in degree.items() if node in nodes), default=0),
        },
        "equipment": equipment,
        "capability_assessment": {
            "medium_voltage_geometry_present": mv_segments > 0,
            "low_voltage_geometry_present_in_view": lv_segments > 0,
            "shared_osm_node_adjacency_present": any(value > 2 for node, value in degree.items() if node in nodes),
            "explicit_switchgear_objects_present": switch_count > 0,
            "operator_verified_terminal_connectivity_present": False,
            "usable_as": "OSM_GEOMETRY_AND_SHARED_NODE_TOPOLOGY_CANDIDATE",
            "not_usable_as": "VERIFIED_ELECTRICAL_TERMINAL_OR_SWITCH_STATE_MODEL",
        },
        "interpretation_rules": [
            "A shared OSM node proves mapped geometry adjacency, not an operator-verified energized terminal.",
            "A crossing without a shared OSM node is not connected in the extracted candidate graph.",
            "Transformer or substation proximity does not create a terminal link.",
            "Missing low-voltage geometry means synthetic or separately sourced LV topology remains necessary.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
