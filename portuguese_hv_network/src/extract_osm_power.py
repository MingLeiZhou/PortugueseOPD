#!/usr/bin/env python3
"""Extract a relation-preserving Portugal power inventory from Geofabrik PBF.

The output deliberately keeps OSM ways as geometry and records
``power=circuit``/``power=line_section`` relations as electrical identity.
It is not an operational-state model; it is a reproducible source snapshot for
the topology builder and the coverage audit.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import osmium
import pandas as pd

from common import RAW, TABLES, ensure_dirs, polyline_length_km, sha256, utc_now, write_json


PBF = RAW / "osm" / "portugal-latest.osm.pbf"
OUTPUT = RAW / "osm" / "portugal_power_osm.json"
LINE_POWERS = {"line", "minor_line", "cable"}
POINT_POWERS = {
    "substation", "plant", "generator", "transformer", "switch", "busbar",
    "bay", "portal", "tower", "pole", "compensator", "converter",
}
RELATION_POWERS = {"circuit", "line_section"}
KEPT_POWERS = LINE_POWERS | POINT_POWERS | RELATION_POWERS


def tags_dict(tags: Any) -> dict[str, str]:
    return {tag.k: tag.v for tag in tags}


def member_type(value: str) -> str:
    return {"n": "node", "w": "way", "r": "relation"}.get(value, value)


class RelationIndex(osmium.SimpleHandler):
    def __init__(self) -> None:
        super().__init__()
        self.relations: dict[int, dict[str, Any]] = {}
        self.member_way_ids: set[int] = set()

    def relation(self, relation: Any) -> None:
        if len(relation.tags) == 0:
            return
        tags = tags_dict(relation.tags)
        if tags.get("power") not in KEPT_POWERS:
            return
        members = [
            {"type": member_type(member.type), "ref": int(member.ref), "role": member.role or ""}
            for member in relation.members
        ]
        self.relations[int(relation.id)] = {
            "type": "relation", "id": int(relation.id), "tags": tags, "members": members,
        }
        self.member_way_ids.update(member["ref"] for member in members if member["type"] == "way")


class PowerInventory(osmium.SimpleHandler):
    def __init__(self, member_way_ids: set[int]) -> None:
        super().__init__()
        self.member_way_ids = member_way_ids
        self.elements: dict[tuple[str, int], dict[str, Any]] = {}
        self.factory = osmium.geom.GeoJSONFactory()

    def node(self, node: Any) -> None:
        if len(node.tags) == 0 or node.tags.get("power") not in KEPT_POWERS:
            return
        tags = tags_dict(node.tags)
        if not node.location.valid():
            return
        self.elements[("node", int(node.id))] = {
            "type": "node", "id": int(node.id), "lat": node.location.lat,
            "lon": node.location.lon, "tags": tags,
        }

    def way(self, way: Any) -> None:
        if len(way.tags) == 0 and int(way.id) not in self.member_way_ids:
            return
        tags = tags_dict(way.tags)
        if tags.get("power") not in KEPT_POWERS and int(way.id) not in self.member_way_ids:
            return
        geometry: list[dict[str, float]] = []
        node_ids: list[int] = []
        for node in way.nodes:
            if not node.location.valid():
                return
            geometry.append({"lat": node.location.lat, "lon": node.location.lon})
            node_ids.append(int(node.ref))
        self.elements[("way", int(way.id))] = {
            "type": "way", "id": int(way.id), "nodes": node_ids,
            "geometry": geometry, "tags": tags,
        }

    def relation(self, relation: Any) -> None:
        if len(relation.tags) == 0 or relation.tags.get("power") not in KEPT_POWERS:
            return
        tags = tags_dict(relation.tags)
        self.elements[("relation", int(relation.id))] = {
            "type": "relation", "id": int(relation.id), "tags": tags,
            "members": [
                {"type": member_type(member.type), "ref": int(member.ref), "role": member.role or ""}
                for member in relation.members
            ],
        }

def relation_tables(elements: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    relations = {
        int(element["id"]): element
        for element in elements
        if element["type"] == "relation" and (element.get("tags") or {}).get("power") in RELATION_POWERS
    }
    relation_rows: list[dict[str, Any]] = []
    member_rows: list[dict[str, Any]] = []
    for relation_id, relation in relations.items():
        tags = relation.get("tags") or {}
        counts = Counter(member["type"] for member in relation.get("members", []))
        relation_rows.append({
            "relation_id": relation_id, "power": tags.get("power", ""),
            "name": tags.get("name", ""), "ref": tags.get("ref", ""),
            "voltage": tags.get("voltage", ""), "operator": tags.get("operator", ""),
            "circuits": tags.get("circuits", ""), "member_nodes": counts["node"],
            "member_ways": counts["way"], "member_relations": counts["relation"],
        })
        for position, member in enumerate(relation.get("members", [])):
            member_rows.append({
                "relation_id": relation_id, "relation_power": tags.get("power", ""),
                "member_position": position, "member_type": member["type"],
                "member_ref": member["ref"], "member_role": member.get("role", ""),
            })

    membership_rows: list[dict[str, Any]] = []
    for circuit_id, circuit in relations.items():
        if (circuit.get("tags") or {}).get("power") != "circuit":
            continue
        circuit_tags = circuit.get("tags") or {}
        seen_relations: set[int] = set()

        def visit(relation_id: int, line_section_id: int | None, path: list[int]) -> None:
            if relation_id in seen_relations:
                return
            seen_relations.add(relation_id)
            relation = relations.get(relation_id)
            if relation is None:
                return
            relation_power = (relation.get("tags") or {}).get("power")
            current_section = relation_id if relation_power == "line_section" else line_section_id
            for member in relation.get("members", []):
                if member["type"] == "way":
                    membership_rows.append({
                        "circuit_id": circuit_id,
                        "circuit_ref": circuit_tags.get("ref", ""),
                        "circuit_name": circuit_tags.get("name", ""),
                        "circuit_voltage": circuit_tags.get("voltage", ""),
                        "line_section_id": current_section or "",
                        "way_id": member["ref"], "member_role": member.get("role", ""),
                        "membership_path": ">".join(map(str, path + [member["ref"]])),
                    })
                elif member["type"] == "relation":
                    visit(int(member["ref"]), current_section, path + [int(member["ref"])])

        visit(circuit_id, None, [circuit_id])
    return pd.DataFrame(relation_rows), pd.DataFrame(member_rows), pd.DataFrame(membership_rows)


def way_inventory(elements: list[dict[str, Any]], memberships: pd.DataFrame) -> pd.DataFrame:
    circuit_count = memberships.groupby("way_id")["circuit_id"].nunique().to_dict() if not memberships.empty else {}
    rows: list[dict[str, Any]] = []
    for element in elements:
        if element["type"] != "way":
            continue
        tags = element.get("tags") or {}
        geometry = [[point["lon"], point["lat"]] for point in element.get("geometry", [])]
        rows.append({
            "way_id": int(element["id"]), "power": tags.get("power", ""),
            "voltage": tags.get("voltage", ""), "name": tags.get("name", ""),
            "ref": tags.get("ref", ""), "operator": tags.get("operator", ""),
            "circuits_tag": tags.get("circuits", ""),
            "circuit_relation_count": int(circuit_count.get(int(element["id"]), 0)),
            "length_km": polyline_length_km(geometry) if len(geometry) >= 2 else 0.0,
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=PBF)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    ensure_dirs()
    if not args.input.exists():
        raise FileNotFoundError(args.input)

    relation_index = RelationIndex()
    relation_index.apply_file(args.input)
    inventory = PowerInventory(relation_index.member_way_ids)
    inventory.apply_file(args.input, locations=True, idx="flex_mem")
    # The first pass guarantees relation membership is retained even if the
    # inventory callback encounters an unusual relation representation.
    for relation_id, relation in relation_index.relations.items():
        inventory.elements[("relation", relation_id)] = relation
    # Add a deterministic geometry centre to power multipolygon relations from
    # their member-way coordinates. This avoids a costly all-area assembly pass.
    way_elements = {
        int(element["id"]): element
        for (kind, _), element in inventory.elements.items() if kind == "way"
    }
    for relation_id, relation in relation_index.relations.items():
        if (relation.get("tags") or {}).get("power") not in POINT_POWERS:
            continue
        coordinates = [
            (float(point["lon"]), float(point["lat"]))
            for member in relation.get("members", []) if member["type"] == "way"
            for point in way_elements.get(int(member["ref"]), {}).get("geometry", [])
        ]
        if coordinates:
            relation["center"] = {
                "lon": sum(point[0] for point in coordinates) / len(coordinates),
                "lat": sum(point[1] for point in coordinates) / len(coordinates),
            }
    elements = sorted(inventory.elements.values(), key=lambda value: (value["type"], int(value["id"])))

    relations, members, memberships = relation_tables(elements)
    ways = way_inventory(elements, memberships)
    relations.to_csv(TABLES / "osm_power_relations.csv", index=False)
    members.to_csv(TABLES / "osm_relation_members.csv", index=False)
    memberships.to_csv(TABLES / "osm_circuit_way_membership.csv", index=False)
    ways.to_csv(TABLES / "osm_power_ways.csv", index=False)
    metadata = {
        "generated_at": utc_now(), "source_path": str(args.input),
        "source_bytes": args.input.stat().st_size, "source_sha256": sha256(args.input),
        "elements": len(elements), "power_counts": Counter((element.get("tags") or {}).get("power", "") for element in elements),
        "circuit_relations": int((relations["power"] == "circuit").sum()) if not relations.empty else 0,
        "line_section_relations": int((relations["power"] == "line_section").sum()) if not relations.empty else 0,
        "circuit_way_memberships": len(memberships),
    }
    write_json(args.output, {"version": 0.6, "generator": "extract_osm_power.py", "metadata": metadata, "elements": elements})
    write_json(TABLES / "osm_power_extract_summary.json", metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
