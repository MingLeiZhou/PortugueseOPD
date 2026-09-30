#!/usr/bin/env python3
"""Build an OSM-node distribution graph and audit PTD/source connections.

This does not promote spatial matches to verified electrical terminals. It
preserves mapped OSM node adjacency, separates voltage classes, and reports
which candidate components have an E-REDES 60/MV source nearby.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

import duckdb
import numpy as np
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
OSM = ROOT / "portuguese_hv_network/data/raw/osm/portugal_power_osm.json"
PTD = ROOT / "output/feasibility/ptd_national_export.json"
DB = ROOT / "data/releases/SimPT60-2026.09.21-r1/bundle/inputs.duckdb"
CHARACTERISTICS = ROOT / "portuguese_hv_network/data/raw/eredes/caracteristicas-da-rede.csv"
OUT = ROOT / "output/distribution_topology"
MV_VOLTAGES = {6000, 10000, 15000, 20000, 30000}
VOLTAGES = MV_VOLTAGES | {400}


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 12742.0 * math.asin(min(1.0, math.sqrt(a)))


def xy_km(lat: float, lon: float) -> tuple[float, float]:
    return lon * 111.32 * math.cos(math.radians(39.5)), lat * 111.32


class DSU:
    def __init__(self) -> None:
        self.parent: dict[tuple[int, int], tuple[int, int]] = {}
        self.size: dict[tuple[int, int], int] = {}

    def add(self, key: tuple[int, int]) -> None:
        if key not in self.parent:
            self.parent[key] = key
            self.size[key] = 1

    def find(self, key: tuple[int, int]) -> tuple[int, int]:
        parent = self.parent
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return
        if self.size[ra] < self.size[rb]:
            ra, rb = rb, ra
        self.parent[rb] = ra
        self.size[ra] += self.size[rb]


def write_csv(path: Path, fields: list[str], records: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(records)


def parse_osm(path: Path) -> tuple[DSU, dict[tuple[int, int], tuple[float, float]], list[tuple], Counter]:
    expression = (
        '.elements[] | select(.type=="way" and '
        '(.tags.power=="minor_line" or .tags.power=="cable" or .tags.power=="line") and '
        '(.tags.voltage=="400" or .tags.voltage=="6000" or .tags.voltage=="10000" or '
        '.tags.voltage=="15000" or .tags.voltage=="20000" or .tags.voltage=="30000")) | '
        '{id, voltage:.tags.voltage, power:.tags.power, nodes, geometry}'
    )
    proc = subprocess.Popen(["jq", "-c", expression, str(path)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    dsu = DSU()
    coords: dict[tuple[int, int], tuple[float, float]] = {}
    edges: list[tuple] = []
    ways: Counter = Counter()
    assert proc.stdout is not None
    for line in proc.stdout:
        way = json.loads(line)
        voltage = int(way["voltage"])
        nodes, geo = way["nodes"], way["geometry"]
        if voltage not in VOLTAGES or len(nodes) != len(geo) or len(nodes) < 2:
            continue
        ways[voltage] += 1
        for node, point in zip(nodes, geo):
            key = (voltage, int(node))
            dsu.add(key)
            coords[key] = (float(point["lat"]), float(point["lon"]))
        for a, b, pa, pb in zip(nodes, nodes[1:], geo, geo[1:]):
            ka, kb = (voltage, int(a)), (voltage, int(b))
            if ka == kb:
                continue
            dsu.union(ka, kb)
            length = distance_km(pa["lat"], pa["lon"], pb["lat"], pb["lon"])
            edges.append((voltage, int(a), int(b), int(way["id"]), way["power"], length))
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError(f"OSM extraction failed: {stderr[:500]}")
    return dsu, coords, edges, ways


def station_rows(path: Path, db: Path) -> list[dict]:
    with duckdb.connect(str(db), read_only=True) as con:
        buses = {str(code): (bid, float(lat), float(lon)) for bid, code, lat, lon in con.execute(
            """SELECT bus_id, facility_code, lat, lon FROM grid.buses
               WHERE voltage_kv=60 AND facility_code IS NOT NULL
                 AND lat IS NOT NULL AND lon IS NOT NULL""").fetchall()}
    records: list[dict] = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle, delimiter=";"):
            code = row["codigo_da_instalacao"]
            if code not in buses:
                continue
            nums = [int(x) for x in re.findall(r"\d+", row["relacao_de_transformacao_at_mt"] or "")]
            mv = next((x for x in reversed(nums) if x * 1000 in MV_VOLTAGES), None)
            if mv is None:
                continue
            bid, lat, lon = buses[code]
            max_ssc = float(row["potencia_de_curto_circuito_maxima_mt"] or 0)
            min_ssc = float(row["potencia_de_curto_circuito_minima_mt"] or 0)
            records.append({"station_code": code, "hv_bus_id": bid, "lat": lat, "lon": lon,
                            "mv_voltage_v": mv * 1000,
                            "installed_mva_public": row["potencia_instalada"],
                            "short_circuit_max_mv_mva_public": max_ssc,
                            "short_circuit_min_mv_mva_public": min_ssc,
                            "thevenin_z_magnitude_min_ohm": round(mv * mv / max_ssc, 6) if max_ssc > 0 else "",
                            "thevenin_z_magnitude_max_ohm": round(mv * mv / min_ssc, 6) if min_ssc > 0 else "",
                            "thevenin_status": "DERIVED_MAGNITUDE_ONLY_RX_SPLIT_UNKNOWN" if max_ssc > 0 and min_ssc > 0 else "INSUFFICIENT_PUBLIC_SSC",
                            "neutral_regime_public": row["regime_de_neutro_mt"]})
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--osm", type=Path, default=OSM)
    parser.add_argument("--ptd", type=Path, default=PTD)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--characteristics", type=Path, default=CHARACTERISTICS)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    dsu, coords, edges, way_counts = parse_osm(args.osm)
    roots = {dsu.find(key) for key in coords}
    component_ids = {root: f"OSMCOMP:{root[0]}:{root[1]}" for root in sorted(roots)}
    by_voltage: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for key in coords:
        by_voltage[key[0]].append(key)
    trees = {v: cKDTree(np.asarray([xy_km(*coords[key]) for key in keys]))
             for v, keys in by_voltage.items()}
    mv_keys = [key for voltage, keys in by_voltage.items() if voltage in MV_VOLTAGES for key in keys]
    mv_tree = cKDTree(np.asarray([xy_km(*coords[key]) for key in mv_keys]))

    stations = station_rows(args.characteristics, args.database)
    anchored_roots: set[tuple[int, int]] = set()
    for station in stations:
        voltage = station["mv_voltage_v"]
        if voltage not in trees:
            station.update(nearest_osm_node="", node_distance_km="", component_id="", anchor_status="NO_OSM_VOLTAGE_CLASS")
            continue
        dist, index = trees[voltage].query(xy_km(station["lat"], station["lon"]))
        key = by_voltage[voltage][int(index)]
        root = dsu.find(key)
        station.update(nearest_osm_node=key[1], node_distance_km=round(float(dist), 4),
                       component_id=component_ids[root],
                       anchor_status="NEAR_STATION_CANDIDATE" if dist <= 0.5 else "DISTANT_STATION_CANDIDATE")
        if dist <= 0.5:
            anchored_roots.add(root)

    ptds = json.loads(args.ptd.read_text(encoding="utf-8"))
    if isinstance(ptds, dict):
        ptds = ptds["results"]
    ptd_rows: list[dict] = []
    for ptd in ptds:
        loc = ptd.get("coordenadas_geo")
        if not loc:
            continue
        dist, index = mv_tree.query(xy_km(loc["lat"], loc["lon"]))
        key = mv_keys[int(index)]
        root = dsu.find(key)
        status = "NEAR_ANCHORED_COMPONENT" if dist <= 0.2 and root in anchored_roots else (
            "NEAR_UNANCHORED_COMPONENT" if dist <= 0.2 else "SPATIAL_PROXY")
        ptd_rows.append({"ptd_code": ptd["cod_instalacao"], "capacity_kva_public": ptd["potencia_transformacao_kva"],
                         "customer_count_public": ptd.get("num_clientes", ""), "mv_voltage_kv_candidate": key[0] / 1000,
                         "osm_node_id_candidate": key[1], "component_id": component_ids[root],
                         "distance_to_osm_node_km": round(float(dist), 4),
                         "component_has_near_60kv_station": root in anchored_roots,
                         "connection_status": status})

    node_fields = ["node_id", "voltage_kv", "lat", "lon", "component_id", "source_status"]
    with (args.output / "osm_distribution_nodes.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=node_fields)
        writer.writeheader()
        for key, (lat, lon) in coords.items():
            writer.writerow(dict(node_id=f"OSM:{key[0]}:{key[1]}", voltage_kv=key[0] / 1000,
                                 lat=lat, lon=lon, component_id=component_ids[dsu.find(key)],
                                 source_status="OSM_SHARED_NODE_TOPOLOGY"))
    edge_fields = ["edge_id", "from_node", "to_node", "voltage_kv", "length_km", "osm_way_id", "power_tag", "component_id", "source_status"]
    with (args.output / "osm_distribution_segments.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=edge_fields)
        writer.writeheader()
        for index, (voltage, a, b, way_id, power, length) in enumerate(edges, start=1):
            writer.writerow(dict(edge_id=f"OSMSEG:{index:07d}", from_node=f"OSM:{voltage}:{a}",
                                 to_node=f"OSM:{voltage}:{b}", voltage_kv=voltage / 1000,
                                 length_km=round(length, 6), osm_way_id=way_id, power_tag=power,
                                 component_id=component_ids[dsu.find((voltage, a))],
                                 source_status="OSM_SHARED_NODE_TOPOLOGY"))
    write_csv(args.output / "station_mv_candidates.csv", list(stations[0]), stations)
    write_csv(args.output / "ptd_osm_candidates.csv", list(ptd_rows[0]), ptd_rows)
    component_ptds = Counter(row["component_id"] for row in ptd_rows)
    component_stations = Counter(row["component_id"] for row in stations if row.get("anchor_status") == "NEAR_STATION_CANDIDATE")
    components = [{"component_id": component_ids[root], "voltage_kv": root[0] / 1000,
                   "node_count": dsu.size[root], "ptd_candidate_count": component_ptds[component_ids[root]],
                   "near_station_count": component_stations[component_ids[root]],
                   "status": "SOURCE_CANDIDATE" if root in anchored_roots else "NO_NEAR_SOURCE_CANDIDATE"}
                  for root in sorted(roots)]
    write_csv(args.output / "osm_components.csv", list(components[0]), components)
    with duckdb.connect() as con:
        for name in ("osm_distribution_nodes", "osm_distribution_segments", "osm_components", "ptd_osm_candidates"):
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_csv_auto(?, all_varchar=true)",
                        [str(args.output / f"{name}.csv")])
        export_checks = {
            "duplicate_node_ids": con.execute("SELECT count(*)-count(DISTINCT node_id) FROM osm_distribution_nodes").fetchone()[0],
            "duplicate_segment_ids": con.execute("SELECT count(*)-count(DISTINCT edge_id) FROM osm_distribution_segments").fetchone()[0],
            "orphan_segment_endpoints": con.execute("""SELECT count(*) FROM osm_distribution_segments e
                LEFT JOIN osm_distribution_nodes a ON e.from_node=a.node_id
                LEFT JOIN osm_distribution_nodes b ON e.to_node=b.node_id
                WHERE a.node_id IS NULL OR b.node_id IS NULL""").fetchone()[0],
            "orphan_ptd_components": con.execute("""SELECT count(*) FROM ptd_osm_candidates p
                LEFT JOIN osm_components c ON p.component_id=c.component_id WHERE c.component_id IS NULL""").fetchone()[0],
            "nonpositive_segment_lengths": con.execute("SELECT count(*) FROM osm_distribution_segments WHERE TRY_CAST(length_km AS DOUBLE)<=0").fetchone()[0],
        }
    counts = Counter(row["connection_status"] for row in ptd_rows)
    errors = [f"{name}: {value}" for name, value in export_checks.items()
              if value and name != "nonpositive_segment_lengths"]
    audit = {"result": "PASS" if not errors else "FAIL",
             "scope": "OSM node adjacency and spatial source/PTD candidates; no operator-verified terminal join",
             "osm_way_counts": {str(k / 1000): v for k, v in sorted(way_counts.items())},
             "osm_node_count": len(coords), "osm_segment_count": len(edges), "component_count": len(roots),
             "components_with_near_station": len(anchored_roots), "station_voltage_records": len(stations),
             "station_near_osm_node_count": sum(s["anchor_status"] == "NEAR_STATION_CANDIDATE" for s in stations),
             "ptd_total": len(ptds), "ptd_mapped": len(ptd_rows),
             "ptd_connection_status": dict(counts),
             "ptds_on_near_source_components": sum(row["component_has_near_60kv_station"] for row in ptd_rows),
             "station_fault_equivalents_with_numeric_range": sum(s["thevenin_status"] == "DERIVED_MAGNITUDE_ONLY_RX_SPLIT_UNKNOWN" for s in stations),
             "export_checks": export_checks, "validation_errors": errors,
             "unresolved": ["Spatial PTD-to-OSM-node link is unverified", "Station 60/MV transformer terminal link is unverified",
                            "OSM node adjacency may omit private/unmapped conductor sections"]}
    (args.output / "validation.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
