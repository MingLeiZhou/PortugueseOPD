#!/usr/bin/env python3
"""Audit relation duplication, interconnectors, and electrical components."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

import networkx as nx
import pandas as pd

from common import PROJECT, RAW, TABLES, VALIDATION, ensure_dirs, normalize_name, parse_osm_voltage_values, point_in_geojson_geometry, read_json, utc_now, write_json


MODEL_VOLTAGES = {60, 130, 150, 220, 400}
INTERCONNECTOR_TERMS = {
    "cartelle", "brovales", "cedillo", "aldeadavila", "saucelle",
    "alcantara", "conchas",
}


def line_memberships() -> pd.DataFrame:
    memberships = pd.read_csv(TABLES / "osm_circuit_way_membership.csv", low_memory=False)
    ways = pd.read_csv(TABLES / "osm_power_ways.csv", low_memory=False)[
        ["way_id", "power", "name", "operator", "length_km"]
    ]
    frame = memberships.merge(ways, on="way_id", how="left")
    return frame[
        frame["power"].isin(["line", "minor_line", "cable"])
        | (frame["power"].fillna("").eq("") & frame["member_role"].fillna("").isin(["", "line"]))
    ].copy()


def duplicate_audit(memberships: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows: list[dict[str, object]] = []
    signatures: dict[tuple[int, ...], list[int]] = defaultdict(list)
    for circuit_id, group in memberships.groupby("circuit_id"):
        way_ids = [int(value) for value in group["way_id"]]
        signature = tuple(sorted(set(way_ids)))
        signatures[signature].append(int(circuit_id))
        repeated = sum(count - 1 for count in Counter(way_ids).values() if count > 1)
        first = group.iloc[0]
        rows.append({
            "circuit_id": int(circuit_id), "voltage": first.get("circuit_voltage", ""),
            "name": first.get("circuit_name", ""), "ref": first.get("circuit_ref", ""),
            "line_way_memberships": len(way_ids), "unique_line_ways": len(signature),
            "repeated_way_memberships": repeated,
            "shared_membership_group": "", "shared_membership_interpretation": "",
        })
    duplicate_group = 0
    for signature, ids in signatures.items():
        if signature and len(ids) > 1:
            duplicate_group += 1
            group_rows = [row for row in rows if row["circuit_id"] in ids]
            refs = {normalize_name(row.get("ref", "")) for row in group_rows if pd.notna(row.get("ref")) and normalize_name(row.get("ref", ""))}
            names = {normalize_name(row.get("name", "")) for row in group_rows if pd.notna(row.get("name")) and normalize_name(row.get("name", ""))}
            interpretation = (
                "CONFIRMED_DUPLICATE_RELATION_SAME_MEMBERS_AND_IDENTITY" if len(refs) <= 1 and len(names) <= 1
                else "PARALLEL_CIRCUITS_SHARED_GEOMETRY"
            )
            for row in group_rows:
                row["shared_membership_group"] = f"SHARED_SET_{duplicate_group:03d}"
                row["shared_membership_interpretation"] = interpretation
    shared_rows: list[dict[str, object]] = []
    for way_id, group in memberships.groupby("way_id"):
        ids = sorted(set(group["circuit_id"].astype(int)))
        if len(ids) < 2:
            continue
        shared_rows.append({
            "way_id": int(way_id), "circuit_count": len(ids),
            "circuit_ids": ";".join(map(str, ids)),
            "voltage_values": ";".join(sorted(set(group["circuit_voltage"].dropna().astype(str)))),
            "way_name": group.iloc[0].get("name", ""), "operator": group.iloc[0].get("operator", ""),
            "length_km": group.iloc[0].get("length_km", ""),
            "interpretation": "SHARED_PHYSICAL_CORRIDOR_OR_PARALLEL_CIRCUIT_REVIEW",
        })
    return pd.DataFrame(rows), pd.DataFrame(shared_rows)


def cross_border_audit(memberships: pd.DataFrame) -> pd.DataFrame:
    relations = pd.read_csv(TABLES / "osm_power_relations.csv", low_memory=False)
    relations = relations[relations["power"] == "circuit"].copy()
    line_context = memberships.groupby("circuit_id", as_index=False).agg(
        line_way_count=("way_id", "nunique"), relation_line_km=("length_km", "sum"),
        way_operators=("operator", lambda values: ";".join(sorted(set(str(v) for v in values.dropna() if str(v).strip())))),
        way_names=("name", lambda values: ";".join(sorted(set(str(v) for v in values.dropna() if str(v).strip())))),
    )
    country_path = RAW / "reference" / "portugal_gisco_2024.geojson"
    country_geometry = read_json(country_path)["features"][0]["geometry"] if country_path.exists() else None
    geometry_counts: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    topology_lines = pd.read_csv(TABLES / "lines_topology.csv", low_memory=False)
    osm_lines = topology_lines[topology_lines["source"] == "OpenStreetMap"]
    for line in osm_lines.to_dict("records"):
        ids = [int(value) for value in re.findall(r"\d+", str(line.get("osm_circuit_ids", "")))]
        points = json.loads(line["geometry_json"])
        inside = sum(
            point_in_geojson_geometry(float(point[0]), float(point[1]), country_geometry)
            for point in points
        ) if country_geometry is not None else len(points)
        for circuit_id in ids:
            geometry_counts[circuit_id][0] += len(points)
            geometry_counts[circuit_id][1] += inside
    rows: list[dict[str, object]] = []
    for row in relations.merge(line_context, left_on="relation_id", right_on="circuit_id", how="left").to_dict("records"):
        voltage = parse_osm_voltage_values(row.get("voltage"), MODEL_VOLTAGES)
        if not voltage:
            continue
        primary_text = normalize_name(" ".join(str(row.get(key, "")) for key in ("name", "ref", "operator")))
        operator_text = normalize_name(" ".join(str(row.get(key, "")) for key in ("operator", "way_operators")))
        terms = sorted(term for term in INTERCONNECTOR_TERMS if re.search(rf"\b{re.escape(term)}\b", primary_text))
        spanish_operator = any(term in operator_text for term in ("red electrica", "ree", "espana", "spain"))
        ren_operator = " ren " in f" {operator_text} " or "rede nacional de transporte" in operator_text
        vertex_total, vertex_inside = geometry_counts[int(row["relation_id"])]
        if vertex_total and vertex_inside == 0:
            geographic_status = "OUTSIDE_PORTUGAL"
        elif vertex_total and vertex_inside < vertex_total:
            geographic_status = "CROSSES_PORTUGAL_BOUNDARY"
        elif vertex_total:
            geographic_status = "WITHIN_PORTUGAL"
        else:
            geographic_status = "NO_MODEL_GEOMETRY"
        # The 1:1M GISCO boundary is an audit aid, not a sole classifier: its
        # generalized coastline can make domestic coastal circuits appear to
        # cross the polygon.  A border label or Spanish operator evidence is
        # therefore required for automatic cross-border classification.
        if terms or spanish_operator:
            classification = "DOCUMENTED_OR_LIKELY_CROSS_BORDER"
        elif geographic_status == "OUTSIDE_PORTUGAL":
            classification = "SPANISH_SIDE_OUTSIDE_PORTUGAL"
        elif ren_operator:
            classification = "PORTUGUESE_RNT"
        else:
            classification = "OPERATOR_OR_BORDER_STATUS_UNRESOLVED"
        rows.append({
            "circuit_id": int(row["relation_id"]), "voltage_kv": ";".join(map(str, voltage)),
            "name": row.get("name", ""), "ref": row.get("ref", ""), "operator": row.get("operator", ""),
            "way_operators": row.get("way_operators", ""), "line_way_count": row.get("line_way_count", 0),
            "relation_line_km": row.get("relation_line_km", 0.0), "matched_interconnector_terms": ";".join(terms),
            "geometry_vertex_count": vertex_total, "geometry_vertices_inside_portugal": vertex_inside,
            "geographic_status": geographic_status,
            "classification": classification,
            "model_treatment": (
                "PRESERVE_AS_BOUNDARY_CANDIDATE_NOT_DOMESTIC_LOAD_BUS" if classification == "DOCUMENTED_OR_LIKELY_CROSS_BORDER"
                else "EXCLUDE_FROM_DOMESTIC_CORE" if classification == "SPANISH_SIDE_OUTSIDE_PORTUGAL" else "PRESERVE"
            ),
        })
    return pd.DataFrame(rows)


def component_audit() -> pd.DataFrame:
    buses = pd.read_csv(TABLES / "buses.csv", low_memory=False)
    lines = pd.read_csv(TABLES / "lines.csv", low_memory=False)
    transformers = pd.read_csv(TABLES / "transformers_topology.csv", low_memory=False)
    loads = pd.read_csv(TABLES / "loads.csv", low_memory=False)
    generators = pd.read_csv(TABLES / "generators.csv", low_memory=False)
    graph = nx.Graph()
    graph.add_nodes_from(buses["bus_id"].astype(str))
    graph.add_edges_from(lines[["from_bus", "to_bus"]].astype(str).itertuples(index=False, name=None))
    graph.add_edges_from(transformers[["hv_bus", "lv_bus"]].astype(str).itertuples(index=False, name=None))
    components = sorted(nx.connected_components(graph), key=len, reverse=True)
    component_of = {str(bus): index for index, component in enumerate(components) for bus in component}
    buses["component_id"] = buses["bus_id"].astype(str).map(component_of)
    loads["component_id"] = loads["bus_id"].astype(str).map(component_of)
    generators["component_id"] = generators["bus_id"].fillna("").astype(str).map(component_of)
    rows: list[dict[str, object]] = []
    for component_id, component in enumerate(components):
        bus_rows = buses[buses["component_id"] == component_id]
        load_rows = loads[loads["component_id"] == component_id]
        generator_rows = generators[generators["component_id"] == component_id]
        active_loads = load_rows[load_rows["in_service_scenario"].astype(str).str.lower().isin(["true", "1"])]
        assigned_generators = generator_rows[generator_rows["bus_id"].fillna("").astype(str) != ""]
        rows.append({
            "component_id": component_id, "bus_count": len(component),
            "line_count": int(lines["from_bus"].astype(str).map(component_of).eq(component_id).sum()),
            "transformer_count": int(transformers["hv_bus"].astype(str).map(component_of).eq(component_id).sum()),
            "voltage_levels_kv": ";".join(map(str, sorted(bus_rows["voltage_kv"].astype(int).unique()))),
            "active_load_count": len(active_loads), "active_load_mw": float(active_loads["p_mw"].sum()),
            "assigned_generator_count": len(assigned_generators), "assigned_nameplate_mw": float(assigned_generators["nameplate_mw"].sum()),
            "scenario_active": bool(len(active_loads) or len(assigned_generators)),
            "centroid_lon": float(bus_rows["lon"].mean()), "centroid_lat": float(bus_rows["lat"].mean()),
            "example_facilities": " | ".join(bus_rows["facility_name"].dropna().astype(str).loc[lambda s: s != ""].drop_duplicates().head(5)),
        })
    return pd.DataFrame(rows)


def transformer_reconciliation() -> pd.DataFrame:
    transformers = pd.read_csv(TABLES / "transformers_topology.csv", low_memory=False)
    direct = transformers[transformers["source_status"] == "DIRECT_OSM_TRANSFORMER_TAG"]
    official_2024 = {
        (400, 220): 8100.0, (400, 150): 6440.0, (220, 150): 830.0,
        (400, 60): 5270.0, (220, 60): 13071.0, (150, 60): 6558.0, (150, 130): 140.0,
    }
    rows: list[dict[str, object]] = []
    for pair, official_mva in official_2024.items():
        subset = direct[(direct["hv_kv"] == pair[0]) & (direct["lv_kv"] == pair[1])]
        observed = float(subset["sn_mva"].sum())
        rows.append({
            "hv_kv": pair[0], "lv_kv": pair[1], "direct_osm_transformer_units": len(subset),
            "direct_osm_nameplate_mva": observed, "ren_2024_in_service_total_mva": official_mva,
            "difference_mva": observed - official_mva,
            "ratio_to_ren": observed / official_mva if official_mva else None,
            "interpretation": "AGGREGATE_CONCORDANCE_ONLY_NOT_UNIT_LEVEL_OPERATOR_CONFIRMATION",
            "ren_source": "REN Relatorio da Qualidade de Servico RNT 2024, Quadro I",
        })
    return pd.DataFrame(rows)


def main() -> None:
    ensure_dirs()
    memberships = line_memberships()
    circuits, shared = duplicate_audit(memberships)
    cross_border = cross_border_audit(memberships)
    components = component_audit()
    transformers = transformer_reconciliation()
    circuits.to_csv(VALIDATION / "osm_circuit_duplicate_audit.csv", index=False)
    shared.to_csv(VALIDATION / "osm_shared_way_circuit_audit.csv", index=False)
    cross_border.to_csv(VALIDATION / "cross_border_circuit_audit.csv", index=False)
    components.to_csv(VALIDATION / "network_component_audit.csv", index=False)
    transformers.to_csv(VALIDATION / "transformer_capacity_reconciliation.csv", index=False)
    summary = {
        "generated_at": utc_now(),
        "circuit_relations_audited": len(circuits),
        "relations_with_repeated_line_members": int((circuits["repeated_way_memberships"] > 0).sum()),
        "relations_sharing_an_exact_way_set": int((circuits["shared_membership_group"] != "").sum()),
        "confirmed_duplicate_relations": int((circuits["shared_membership_interpretation"] == "CONFIRMED_DUPLICATE_RELATION_SAME_MEMBERS_AND_IDENTITY").sum()),
        "parallel_relations_sharing_geometry": int((circuits["shared_membership_interpretation"] == "PARALLEL_CIRCUITS_SHARED_GEOMETRY").sum()),
        "physical_ways_shared_by_relations": len(shared),
        "likely_cross_border_relations": int((cross_border["classification"] == "DOCUMENTED_OR_LIKELY_CROSS_BORDER").sum()),
        "topological_components": len(components),
        "scenario_active_components": int(components["scenario_active"].sum()),
        "direct_osm_transformer_units": int(transformers["direct_osm_transformer_units"].sum()),
        "transformer_reconciliation_note": "REN totals validate aggregate voltage-pair capacity only; OSM remains the unit-level tag source.",
    }
    write_json(VALIDATION / "network_evidence_audit_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
