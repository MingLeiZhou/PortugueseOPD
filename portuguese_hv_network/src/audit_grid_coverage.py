#!/usr/bin/env python3
"""Reconcile extracted OSM geometry, relation circuit-km, and published context."""
from __future__ import annotations

import json
import re

import pandas as pd

from common import PROJECT, TABLES, VALIDATION, ensure_dirs, parse_osm_voltage_values, read_json, utc_now, write_json


VOLTAGES = {60, 130, 150, 220, 400}


def voltages_at_or_above_60(value: object) -> list[int]:
    values: set[int] = set()
    for raw in re.findall(r"\d+(?:[.,]\d+)?", str(value)):
        number = float(raw.replace(",", "."))
        if number < 1000:
            continue
        voltage = int(round(number / 1000.0))
        if voltage >= 60:
            values.add(voltage)
    return sorted(values)


def main() -> None:
    ensure_dirs()
    config = read_json(PROJECT / "config" / "model_config.json")
    ways = pd.read_csv(TABLES / "osm_power_ways.csv", low_memory=False)
    memberships = pd.read_csv(TABLES / "osm_circuit_way_membership.csv", low_memory=False)
    relations = pd.read_csv(TABLES / "osm_power_relations.csv", low_memory=False)
    retained = pd.read_csv(TABLES / "lines_topology.csv", low_memory=False)
    blocked = pd.read_csv(TABLES / "lines_blocked.csv", low_memory=False)

    # Preserve every >=60 kV OSM way in the inventory. Direct way tags and
    # relation-inherited voltages are unioned by (way_id, voltage), so a way is
    # counted once as physical geometry even if it belongs to several circuits.
    direct_way_voltage = ways[ways["power"].isin(["line", "minor_line", "cable"])][
        ["way_id", "length_km", "voltage"]
    ].copy()
    direct_way_voltage["voltage_kv"] = direct_way_voltage["voltage"].map(voltages_at_or_above_60)
    direct_way_voltage = direct_way_voltage.explode("voltage_kv").dropna(subset=["voltage_kv"])
    direct_way_voltage["voltage_source"] = "DIRECT_WAY_TAG"

    relation_way_voltage = memberships[["way_id", "circuit_voltage", "member_role"]].drop_duplicates().merge(
        ways[["way_id", "length_km", "power"]], on="way_id", how="left"
    )
    relation_way_voltage = relation_way_voltage[
        relation_way_voltage["power"].isin(["line", "minor_line", "cable"])
        | (relation_way_voltage["power"].fillna("").eq("") & relation_way_voltage["member_role"].fillna("").isin(["", "line"]))
    ]
    relation_way_voltage["voltage_kv"] = relation_way_voltage["circuit_voltage"].map(voltages_at_or_above_60)
    relation_way_voltage = relation_way_voltage.explode("voltage_kv").dropna(subset=["voltage_kv"])
    relation_way_voltage["voltage_source"] = "CIRCUIT_RELATION"
    physical_way_voltage = pd.concat(
        [direct_way_voltage[["way_id", "length_km", "voltage_kv", "voltage_source"]],
         relation_way_voltage[["way_id", "length_km", "voltage_kv", "voltage_source"]]],
        ignore_index=True,
    ).sort_values("voltage_source").drop_duplicates(["way_id", "voltage_kv"], keep="last")
    physical_way_voltage["voltage_kv"] = physical_way_voltage["voltage_kv"].astype(int)

    circuit_ways = memberships.merge(ways[["way_id", "length_km", "power"]], on="way_id", how="left")
    circuit_ways = circuit_ways[
        circuit_ways["power"].isin(["line", "minor_line", "cable"])
        | (circuit_ways["power"].fillna("").eq("") & circuit_ways["member_role"].fillna("").isin(["", "line"]))
    ]
    circuit_ways = circuit_ways.drop_duplicates(["circuit_id", "way_id"])
    circuit_ways["voltage_kv"] = circuit_ways["circuit_voltage"].map(
        lambda value: parse_osm_voltage_values(value, VOLTAGES)
    )
    circuit_ways = circuit_ways.explode("voltage_kv")
    circuit_ways = circuit_ways[circuit_ways["voltage_kv"].notna()].copy()
    circuit_ways["voltage_kv"] = circuit_ways["voltage_kv"].astype(int)

    circuit_relations = relations[relations["power"] == "circuit"].copy()
    circuit_relations["voltage_kv"] = circuit_relations["voltage"].map(
        lambda value: parse_osm_voltage_values(value, VOLTAGES)
    )
    circuit_relations = circuit_relations.explode("voltage_kv")
    circuit_relations = circuit_relations[circuit_relations["voltage_kv"].notna()].copy()
    circuit_relations["voltage_kv"] = circuit_relations["voltage_kv"].astype(int)

    wiki = config["osm_portugal_wiki_reference"]
    ren = {int(key): float(value) for key, value in config["ren_2025_reference_line_km"].items()}
    rows: list[dict[str, object]] = []
    for voltage in sorted(VOLTAGES):
        source = "E-REDES" if voltage in {60, 130} else "OpenStreetMap"
        retained_km = float(retained.loc[(retained["voltage_kv"] == voltage) & (retained["source"] == source), "length_km"].sum())
        blocked_km = float(blocked.loc[(blocked["voltage_kv"] == voltage) & (blocked["source"] == source), "length_km"].sum())
        osm_physical_km = float(physical_way_voltage.loc[physical_way_voltage["voltage_kv"] == voltage, "length_km"].sum())
        circuit_km = float(circuit_ways.loc[circuit_ways["voltage_kv"] == voltage, "length_km"].sum())
        mapped_reference = float(wiki["mapped_line_km"][str(voltage)])
        expected_reference = float(wiki["expected_line_km"][str(voltage)])
        rows.append({
            "voltage_kv": voltage,
            "topology_primary_source": source,
            "retained_topology_corridor_km": retained_km,
            "blocked_after_clustering_km": blocked_km,
            "osm_extracted_physical_corridor_km": osm_physical_km,
            "osm_relation_circuit_km": circuit_km,
            "osm_circuit_relations": int((circuit_relations["voltage_kv"] == voltage).sum()),
            "osm_wiki_mapped_km_2026_07_14": mapped_reference,
            "osm_relation_to_wiki_ratio": circuit_km / mapped_reference if mapped_reference else None,
            "osm_wiki_expected_or_operator_km": expected_reference,
            "ren_2025_context_km": ren.get(voltage, ""),
            "interpretation": (
                "E-REDES geometry is primary; OSM circuit-km is a contextual cross-check"
                if voltage in {60, 130}
                else "OSM way geometry is primary; relation circuit-km counts shared corridors once per circuit"
            ),
        })
    result = pd.DataFrame(rows)
    result.to_csv(VALIDATION / "voltage_coverage_reconciliation.csv", index=False)
    inventory_rows: list[dict[str, object]] = []
    inventory_levels = sorted(set(config.get("osm_inventory_voltage_levels_kv", [])) | set(physical_way_voltage["voltage_kv"]))
    for voltage in inventory_levels:
        subset = physical_way_voltage[physical_way_voltage["voltage_kv"] == voltage]
        relation_subset = circuit_ways[circuit_ways["voltage_kv"] == voltage]
        inventory_rows.append({
            "voltage_kv": voltage,
            "physical_way_count": int(subset["way_id"].nunique()),
            "physical_corridor_km": float(subset["length_km"].sum()),
            "circuit_relation_count": int(circuit_relations.loc[circuit_relations["voltage_kv"] == voltage, "relation_id"].nunique()),
            "relation_circuit_km": float(relation_subset["length_km"].sum()),
            "model_disposition": (
                "EREDES_PRIMARY_OSM_CROSSCHECK" if voltage in {60, 130}
                else "OSM_PRIMARY" if voltage in {150, 220, 400}
                else "PRESERVED_FOR_BORDER_AND_SCOPE_REVIEW"
            ),
        })
    inventory = pd.DataFrame(inventory_rows)
    inventory.to_csv(VALIDATION / "osm_60plus_voltage_inventory.csv", index=False)
    summary = {
        "generated_at": utc_now(),
        "geofabrik_extract": read_json(TABLES / "osm_power_extract_summary.json"),
        "wiki_reference_date": wiki["snapshot_date"], "wiki_reference_url": wiki["url"],
        "important_distinction": "Physical corridor-km counts each OSM way once; relation circuit-km counts a shared corridor once for each circuit relation. REN/OSM published totals must be compared using the matching definition.",
        "rows": result.to_dict("records"),
        "all_osm_voltage_levels_at_or_above_60_kv": inventory.to_dict("records"),
    }
    write_json(VALIDATION / "voltage_coverage_reconciliation.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
