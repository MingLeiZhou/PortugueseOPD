#!/usr/bin/env python3
"""Build and solve a small public-data-anchored 60 kV -> MV -> LV pilot.

The E-REDES PTD records are public anchors. MV connections, LV feeders and
customer allocations are explicitly synthetic. The frozen release is read-only.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import subprocess
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

import duckdb
import numpy as np
import pandapower as pp
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/releases/SimPT60-2026.09.21-r1/bundle/inputs.duckdb"
OUT = ROOT / "output/feasibility/lv_pilot"
OSM = ROOT / "portuguese_hv_network/data/raw/osm/portugal_power_osm.json"
SOURCE = "https://e-redes.opendatasoft.com/api/explore/v2.1/catalog/datasets/postos-transformacao-distribuicao/records"
NATIONAL_PTD = ROOT / "output/feasibility/ptd_national_export.json"
NATIONAL_OUT = ROOT / "output/feasibility/all_voltage_national"


def distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def load_ptds(path: Path, fetch: bool, municipality_code: str, limit: int) -> dict:
    if fetch:
        query = urllib.parse.urlencode({"where": f"coddistritoconcelho='{municipality_code}'", "limit": limit})
        request = urllib.request.Request(f"{SOURCE}?{query}", headers={"User-Agent": "SimPT-LV-feasibility/1.0"})
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.load(response)
        census_request = urllib.request.Request(f"{SOURCE}?limit=1", headers={"User-Agent": "SimPT-LV-feasibility/1.0"})
        with urllib.request.urlopen(census_request, timeout=25) as response:
            payload["national_ptd_total_count"] = json.load(response)["total_count"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return payload
    if not path.exists():
        raise FileNotFoundError(f"PTD sample absent: {path}; run once with --fetch")
    return json.loads(path.read_text(encoding="utf-8"))


def utilisation_fraction(value: str) -> float | None:
    match = re.fullmatch(r"\s*(\d+)%\s*-\s*(\d+)%\s*", str(value))
    if not match:
        return None
    lo, hi = map(int, match.groups())
    return (lo + hi) / 200.0 if 0 <= lo <= hi <= 100 else None


def source_buses(db_path: Path) -> list[dict]:
    with duckdb.connect(str(db_path), read_only=True) as con:
        result = con.execute("""SELECT bus_id, lat, lon, facility_code
            FROM grid.buses WHERE voltage_kv=60 AND source='E-REDES'
            AND lat IS NOT NULL AND lon IS NOT NULL AND facility_code IS NOT NULL""")
        cols = [c[0] for c in result.description]
        return [dict(zip(cols, row)) for row in result.fetchall()]


def select_cluster(ptds: list[dict], buses: list[dict], count: int, radius_km: float) -> tuple[dict, list[dict]]:
    candidates = []
    for ptd in ptds:
        loc = ptd.get("coordenadas_geo") or {}
        fraction = utilisation_fraction(ptd.get("nivel_utilizacao", ""))
        kva = ptd.get("potencia_transformacao_kva")
        if "lat" in loc and "lon" in loc and fraction is not None and isinstance(kva, (int, float)) and kva > 0:
            candidates.append({**ptd, "fraction": fraction})
    if not candidates or not buses:
        raise ValueError("No PTD records with coordinates, capacity and utilisation band, or no 60 kV source buses")
    ranked = []
    for bus in buses:
        close = sorted(((distance_km(bus["lat"], bus["lon"], p["coordenadas_geo"]["lat"],
                                     p["coordenadas_geo"]["lon"]), p) for p in candidates), key=lambda x: x[0])
        close = [(d, p) for d, p in close if d <= radius_km]
        if len(close) >= count:
            ranked.append((sum(d for d, _ in close[:count]), bus["bus_id"], bus, close[:count]))
    if not ranked:
        raise ValueError(f"No {count} public PTDs within {radius_km:g} km of one source 60 kV bus")
    _, _, bus, close = min(ranked, key=lambda x: (x[0], x[1]))
    return bus, [{**p, "anchor_distance_km": d} for d, p in close]


def nearby_osm_mv(osm_path: Path, anchor: dict, ptds: list[dict]) -> tuple[float, list[dict], dict]:
    """Read nearby mapped MV ways, keeping OSM identity separate from assumed PTD links."""
    lat, lon = float(anchor["lat"]), float(anchor["lon"])
    span = 0.12
    expression = (
        '.elements[] | select(.type=="way" and '
        '(.tags.power=="minor_line" or .tags.power=="cable" or .tags.power=="line") and '
        f'any(.geometry[]?; .lat>={lat-span} and .lat<={lat+span} and '
        f'.lon>={lon-span} and .lon<={lon+span})) | '
        '{id, power:.tags.power, voltage:.tags.voltage, geometry}'
    )
    result = subprocess.run(["jq", "-c", expression, str(osm_path)], capture_output=True,
                            text=True, check=True)
    ways = []
    for line in result.stdout.splitlines():
        way = json.loads(line)
        voltage = str(way.get("voltage") or "")
        if voltage not in {"6000", "10000", "15000", "20000", "30000"}:
            continue
        ways.append(way)
    if not ways:
        raise ValueError("No nearby OSM MV ways with a usable voltage tag")

    def nearest(point: dict) -> tuple[float, dict]:
        coords = point["coordenadas_geo"] if "coordenadas_geo" in point else point
        return min(((min(distance_km(coords["lat"], coords["lon"], vertex["lat"], vertex["lon"])
                         for vertex in way["geometry"]), way) for way in ways),
                   key=lambda item: (item[0], item[1]["id"]))

    anchor_distance, anchor_way = nearest(anchor)
    mv_kv = int(anchor_way["voltage"]) / 1000.0
    linked = []
    for ptd in ptds:
        gap, way = nearest(ptd)
        linked.append({**ptd, "nearest_osm_mv_way_id": way["id"],
                       "nearest_osm_mv_voltage_kv": int(way["voltage"]) / 1000.0,
                       "nearest_osm_mv_vertex_distance_km": gap})
    evidence = {"nearby_tagged_mv_ways": len(ways), "anchor_nearest_osm_way_id": anchor_way["id"],
                "anchor_nearest_osm_way_distance_km": anchor_distance,
                "chosen_mv_voltage_kv": mv_kv,
                "connection_interpretation": "OSM proximity and voltage only; no verified PTD-to-way terminal link"}
    return mv_kv, linked, evidence


def write_csv(path: Path, records: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)


def build_network(anchor: dict, ptds: list[dict], mv_kv: float) -> tuple[pp.pandapowerNet, dict[str, list[dict]]]:
    net = pp.create_empty_network(f_hz=50, sn_mva=10.0)
    nodes: list[dict] = []
    branches: list[dict] = []
    loads: list[dict] = []

    def add_bus(name: str, voltage: float, status: str, source_id: str) -> int:
        index = pp.create_bus(net, vn_kv=voltage, name=name)
        nodes.append(dict(node_id=name, voltage_kv=voltage, evidence_status=status, source_id=source_id))
        return index

    hv = add_bus(anchor["bus_id"], 60.0, "DIRECT_EXISTING_MODEL", anchor["facility_code"])
    mv = add_bus(f"MV:ROOT:{anchor['facility_code']}", mv_kv, "SYNTHETIC_INTERFACE_OSM_VOLTAGE", anchor["facility_code"])
    pp.create_ext_grid(net, hv, vm_pu=1.0)
    pp.create_transformer_from_parameters(net, hv, mv, sn_mva=20.0, vn_hv_kv=60.0,
                                           vn_lv_kv=mv_kv, vk_percent=10.0, vkr_percent=0.5,
                                           pfe_kw=0.0, i0_percent=0.0, name=f"Synthetic 60/{mv_kv:g} kV bridge")
    branches.append(dict(branch_id="MV_BRIDGE", from_node=anchor["bus_id"],
                         to_node=f"MV:ROOT:{anchor['facility_code']}", kind="transformer",
                         evidence_status="SYNTHETIC_60_20_INTERFACE"))

    for ptd in ptds:
        code = ptd["cod_instalacao"]
        sn_mva = float(ptd["potencia_transformacao_kva"]) / 1000.0
        lv_p = sn_mva * ptd["fraction"] * 0.97
        lv_q = sn_mva * ptd["fraction"] * math.sqrt(1.0 - 0.97 ** 2)
        mv_name, lv_name = f"MV:PTD:{code}", f"LV:PTD:{code}"
        mv_bus = add_bus(mv_name, mv_kv, "SYNTHETIC_MV_CONNECTION", code)
        lv_bus = add_bus(lv_name, 0.4, "DIRECT_PTD_ANCHOR_VOLTAGE_ASSUMED", code)
        km = max(0.05, ptd["anchor_distance_km"] * 1.2)
        pp.create_line_from_parameters(net, mv, mv_bus, length_km=km,
                                        r_ohm_per_km=0.32, x_ohm_per_km=0.35,
                                        c_nf_per_km=10.0, max_i_ka=0.25,
                                        name=f"Synthetic MV feeder {code}")
        branches.append(dict(branch_id=f"MV_LINE:{code}", from_node=f"MV:ROOT:{anchor['facility_code']}",
                             to_node=mv_name, kind="line", evidence_status="SYNTHETIC_MV_TOPOLOGY_AND_PARAMETERS"))
        pp.create_transformer_from_parameters(net, mv_bus, lv_bus, sn_mva=sn_mva,
                                               vn_hv_kv=mv_kv, vn_lv_kv=0.4,
                                               vk_percent=4.0, vkr_percent=1.0,
                                               pfe_kw=0.0, i0_percent=0.0,
                                               name=f"E-REDES PTD capacity {code}")
        branches.append(dict(branch_id=f"PTD:{code}", from_node=mv_name, to_node=lv_name,
                             kind="transformer", evidence_status="PUBLIC_CAPACITY_SYNTHETIC_IMPEDANCE"))
        previous, previous_name = lv_bus, lv_name
        for segment in range(1, 4):
            name = f"LV:{code}:{segment}"
            end = add_bus(name, 0.4, "SYNTHETIC_LV_FEEDER_NODE", code)
            pp.create_line_from_parameters(net, previous, end, length_km=0.05,
                                            r_ohm_per_km=0.50, x_ohm_per_km=0.10,
                                            c_nf_per_km=0.0, max_i_ka=0.25,
                                            name=f"Synthetic LV section {code}:{segment}")
            branches.append(dict(branch_id=f"LV_LINE:{code}:{segment}", from_node=previous_name,
                                 to_node=name, kind="line", evidence_status="SYNTHETIC_LV_TOPOLOGY_AND_PARAMETERS"))
            pp.create_load(net, end, p_mw=lv_p / 3, q_mvar=lv_q / 3,
                           name=f"Aggregated customers {code}:{segment}")
            loads.append(dict(load_id=f"LOAD:{code}:{segment}", node_id=name,
                              p_mw=lv_p / 3, q_mvar=lv_q / 3,
                              public_customer_count=ptd.get("num_clientes"),
                              status="SYNTHETIC_ALLOCATION_FROM_PTD_UTILISATION_BAND"))
            previous, previous_name = end, name
    return net, {"nodes": nodes, "branches": branches, "loads": loads}


def xy_km(lat: float, lon: float) -> tuple[float, float]:
    """Local metric projection for nearest-candidate search, not grid geometry."""
    return lon * 111.32 * math.cos(math.radians(39.5)), lat * 111.32


def scan_national_osm(osm_path: Path, output: Path) -> tuple[np.ndarray, np.ndarray, list[dict], Counter]:
    """Stream OSM power ways through jq; retain all tagged MV and LV way identities."""
    expression = (
        '.elements[] | select(.type=="way" and '
        '(.tags.power=="minor_line" or .tags.power=="cable" or .tags.power=="line") and '
        '(.tags.voltage=="400" or .tags.voltage=="6000" or .tags.voltage=="10000" or '
        '.tags.voltage=="15000" or .tags.voltage=="20000" or .tags.voltage=="30000")) | '
        '{id, power:.tags.power, voltage:.tags.voltage, geometry}'
    )
    proc = subprocess.Popen(["jq", "-c", expression, str(osm_path)], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)
    points: list[tuple[float, float]] = []
    point_way_indexes: list[int] = []
    way_info: list[dict] = []
    voltage_counts: Counter = Counter()
    with (output / "osm_distribution_ways.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["osm_way_id", "power", "voltage_kv", "vertex_count", "length_km", "status"])
        writer.writeheader()
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            way = json.loads(raw_line)
            voltage = int(way["voltage"]) / 1000.0
            geometry = way.get("geometry") or []
            if len(geometry) < 2:
                continue
            length = sum(distance_km(a["lat"], a["lon"], b["lat"], b["lon"])
                         for a, b in zip(geometry, geometry[1:]))
            writer.writerow(dict(osm_way_id=way["id"], power=way["power"], voltage_kv=voltage,
                                 vertex_count=len(geometry), length_km=round(length, 6), status="OSM_MAPPED_GEOMETRY"))
            voltage_counts[voltage] += 1
            if voltage == 0.4:
                continue
            index = len(way_info)
            way_info.append({"osm_way_id": way["id"], "voltage_kv": voltage, "power": way["power"]})
            for vertex in geometry:
                points.append(xy_km(vertex["lat"], vertex["lon"]))
                point_way_indexes.append(index)
    stderr = proc.stderr.read() if proc.stderr else ""
    if proc.wait() != 0:
        raise RuntimeError(f"OSM jq extraction failed: {stderr[:500]}")
    if not points:
        raise ValueError("OSM snapshot has no tagged MV geometry")
    return np.asarray(points, dtype=np.float64), np.asarray(point_way_indexes, dtype=np.int32), way_info, voltage_counts


def national_utilisation(value: str) -> tuple[float, str]:
    if str(value).strip() == "+100%":
        return 1.05, "ASSUMED_105_PERCENT_FROM_OVER_100_BAND"
    fraction = utilisation_fraction(value)
    if fraction is not None:
        return fraction, "MIDPOINT_OF_PUBLIC_PEAK_UTILISATION_BAND"
    return 0.30, "ASSUMED_30_PERCENT_WHEN_PUBLIC_UTILISATION_MISSING"


def verify_national_exports(output: Path) -> tuple[dict[str, int], list[str]]:
    """Check written files, not just the in-memory generation counters."""
    with duckdb.connect() as con:
        for name in ("all_voltage_buses", "all_voltage_branches", "ptd_connections", "lv_loads", "osm_distribution_ways"):
            con.execute(f"CREATE TABLE {name} AS SELECT * FROM read_csv_auto(?, all_varchar=true)",
                        [str(output / f"{name}.csv")])
        queries = {
            "buses": "SELECT count(*) FROM all_voltage_buses",
            "branches": "SELECT count(*) FROM all_voltage_branches",
            "ptds": "SELECT count(*) FROM ptd_connections",
            "loads": "SELECT count(*) FROM lv_loads",
            "osm_ways": "SELECT count(*) FROM osm_distribution_ways",
            "duplicate_buses": "SELECT count(*)-count(DISTINCT bus_id) FROM all_voltage_buses",
            "duplicate_branches": "SELECT count(*)-count(DISTINCT branch_id) FROM all_voltage_branches",
            "orphan_branch_ends": """SELECT count(*) FROM all_voltage_branches e
                LEFT JOIN all_voltage_buses a ON e.from_bus=a.bus_id
                LEFT JOIN all_voltage_buses z ON e.to_bus=z.bus_id
                WHERE a.bus_id IS NULL OR z.bus_id IS NULL""",
            "orphan_load_buses": """SELECT count(*) FROM lv_loads l
                LEFT JOIN all_voltage_buses b ON l.bus_id=b.bus_id WHERE b.bus_id IS NULL""",
            "ptds_with_both_voltage_buses": """SELECT count(*) FROM ptd_connections p
                WHERE EXISTS (SELECT 1 FROM all_voltage_buses b WHERE b.bus_id='PTD:MV:'||p.ptd_code)
                  AND EXISTS (SELECT 1 FROM all_voltage_buses b WHERE b.bus_id='PTD:LV:'||p.ptd_code)""",
        }
        checks = {name: int(con.execute(query).fetchone()[0]) for name, query in queries.items()}
    errors = []
    for name in ("duplicate_buses", "duplicate_branches", "orphan_branch_ends", "orphan_load_buses"):
        if checks[name]:
            errors.append(f"{name}: {checks[name]}")
    if checks["ptds_with_both_voltage_buses"] != checks["ptds"]:
        errors.append("Some PTDs are missing an MV or LV bus")
    if checks["loads"] != 2 * checks["ptds"]:
        errors.append("LV load count does not equal two per PTD")
    return checks, errors


def build_national(database: Path, osm_path: Path, ptd_path: Path, output: Path) -> int:
    """Generate a nationwide OSM-informed equivalent network for every public PTD.

    The operative MV branches are synthetic radial equivalents; OSM way
    geometries are a separate candidate inventory until terminal links can
    be reconstructed. This avoids silently inventing real OSM connectivity.
    """
    output.mkdir(parents=True, exist_ok=True)
    ptds = json.loads(ptd_path.read_text(encoding="utf-8"))
    if isinstance(ptds, dict):
        ptds = ptds.get("results", [])
    if not isinstance(ptds, list) or not ptds:
        raise ValueError("National PTD export must contain a nonempty JSON array")
    with duckdb.connect(str(database), read_only=True) as con:
        base_buses = con.execute("SELECT bus_id, voltage_kv, lat, lon, facility_code, source_status FROM grid.buses").fetchall()
        base_lines = con.execute("SELECT line_id, from_bus, to_bus, voltage_kv FROM grid.lines").fetchall()
        base_trafos = con.execute("SELECT transformer_id, hv_bus, lv_bus, hv_kv, lv_kv FROM grid.transformers").fetchall()
    hv_anchors = [b for b in base_buses if b[1] == 60 and b[2] is not None and b[3] is not None and b[4]]
    if not hv_anchors:
        raise ValueError("No 60 kV source buses with facility coordinates")
    mv_points, point_way_indexes, way_info, osm_counts = scan_national_osm(osm_path, output)
    mv_tree = cKDTree(mv_points)
    hv_tree = cKDTree(np.asarray([xy_km(b[2], b[3]) for b in hv_anchors]))
    valid_ptds = [p for p in ptds if p.get("cod_instalacao") and p.get("coordenadas_geo")
                  and isinstance(p.get("potencia_transformacao_kva"), (int, float))
                  and p["potencia_transformacao_kva"] >= 0]
    if not valid_ptds:
        raise ValueError("No PTDs with code, coordinates and positive capacity")
    locations = np.asarray([xy_km(p["coordenadas_geo"]["lat"], p["coordenadas_geo"]["lon"])
                            for p in valid_ptds])
    mv_distances, mv_indexes = mv_tree.query(locations, workers=-1)
    hv_distances, hv_indexes = hv_tree.query(locations, workers=-1)
    root_keys = sorted({(hv_anchors[int(hv_i)][0], way_info[int(point_way_indexes[int(mv_i)])]["voltage_kv"])
                        for hv_i, mv_i in zip(hv_indexes, mv_indexes)})
    roots = {key: f"MVROOT:{index:05d}" for index, key in enumerate(root_keys, start=1)}
    bus_fields = ["bus_id", "voltage_kv", "lat", "lon", "source_ref", "evidence_status"]
    branch_fields = ["branch_id", "from_bus", "to_bus", "from_kv", "to_kv", "asset_kind", "evidence_status", "source_ref"]
    ptd_fields = ["ptd_code", "mv_voltage_kv", "mv_root_bus", "hv_bus", "nearest_osm_way_id",
                  "osm_vertex_distance_km", "hv_anchor_distance_km", "capacity_kva", "utilisation_band",
                  "customer_count_public", "peak_load_proxy_mva", "load_status", "connection_status"]
    load_fields = ["load_id", "bus_id", "p_mw", "q_mvar", "evidence_status", "source_ptd_code"]
    counts: Counter = Counter()
    selected_voltages: Counter = Counter()
    far_osm = far_hv = 0
    with (output / "all_voltage_buses.csv").open("w", newline="", encoding="utf-8") as bus_file, \
         (output / "all_voltage_branches.csv").open("w", newline="", encoding="utf-8") as branch_file, \
         (output / "ptd_connections.csv").open("w", newline="", encoding="utf-8") as ptd_file, \
         (output / "lv_loads.csv").open("w", newline="", encoding="utf-8") as load_file:
        bus_writer = csv.DictWriter(bus_file, fieldnames=bus_fields)
        branch_writer = csv.DictWriter(branch_file, fieldnames=branch_fields)
        ptd_writer = csv.DictWriter(ptd_file, fieldnames=ptd_fields)
        load_writer = csv.DictWriter(load_file, fieldnames=load_fields)
        for writer in (bus_writer, branch_writer, ptd_writer, load_writer):
            writer.writeheader()
        for bus_id, voltage, lat, lon, code, status in base_buses:
            bus_writer.writerow(dict(bus_id=bus_id, voltage_kv=voltage, lat=lat, lon=lon,
                                     source_ref=code or "", evidence_status=status or "EXISTING_SIMPT60"))
            counts["base_buses"] += 1
        for line_id, a, b, voltage in base_lines:
            branch_writer.writerow(dict(branch_id=line_id, from_bus=a, to_bus=b, from_kv=voltage,
                                        to_kv=voltage, asset_kind="line", evidence_status="EXISTING_SIMPT60", source_ref=line_id))
            counts["base_lines"] += 1
        for tid, a, b, hv_kv, lv_kv in base_trafos:
            branch_writer.writerow(dict(branch_id=tid, from_bus=a, to_bus=b, from_kv=hv_kv,
                                        to_kv=lv_kv, asset_kind="transformer", evidence_status="EXISTING_SIMPT60", source_ref=tid))
            counts["base_transformers"] += 1
        for (hv_bus, mv_kv), root_id in roots.items():
            bus_writer.writerow(dict(bus_id=root_id, voltage_kv=mv_kv, lat="", lon="",
                                     source_ref=hv_bus, evidence_status="SYNTHETIC_MV_ROOT_OSM_VOLTAGE"))
            branch_writer.writerow(dict(branch_id=f"BRIDGE:{root_id}", from_bus=hv_bus, to_bus=root_id,
                                        from_kv=60, to_kv=mv_kv, asset_kind="transformer",
                                        evidence_status="SYNTHETIC_60_MV_BRIDGE", source_ref=hv_bus))
            counts["mv_roots"] += 1
        for ptd, mv_idx, mv_gap, hv_idx, hv_gap in zip(valid_ptds, mv_indexes, mv_distances, hv_indexes, hv_distances):
            code = ptd["cod_instalacao"]
            way = way_info[int(point_way_indexes[int(mv_idx)])]
            mv_kv = way["voltage_kv"]
            hv_bus = hv_anchors[int(hv_idx)][0]
            root_id = roots[(hv_bus, mv_kv)]
            loc = ptd["coordenadas_geo"]
            util, util_status = national_utilisation(ptd.get("nivel_utilizacao", ""))
            capacity_kva = ptd["potencia_transformacao_kva"]
            if capacity_kva == 0:
                util_status = "ZERO_PUBLIC_CAPACITY_NO_NUMERIC_LOAD"
                counts["zero_capacity_ptds"] += 1
            peak_mva = capacity_kva / 1000.0 * util
            connection = "OSM_NEARBY_VOLTAGE_SYNTHETIC_FEEDER" if mv_gap <= 1.0 else "DISTANT_OSM_VOLTAGE_PROXY_SYNTHETIC_FEEDER"
            far_osm += int(mv_gap > 1.0)
            far_hv += int(hv_gap > 20.0)
            selected_voltages[mv_kv] += 1
            mv_bus, lv_bus = f"PTD:MV:{code}", f"PTD:LV:{code}"
            for bus_id, voltage, status in ((mv_bus, mv_kv, connection),
                                            (lv_bus, 0.4, "PUBLIC_PTD_LOCATION_SYNTHETIC_LV_TERMINAL")):
                bus_writer.writerow(dict(bus_id=bus_id, voltage_kv=voltage, lat=loc["lat"], lon=loc["lon"],
                                         source_ref=code, evidence_status=status))
            branch_writer.writerow(dict(branch_id=f"MVFEEDER:{code}", from_bus=root_id, to_bus=mv_bus,
                                        from_kv=mv_kv, to_kv=mv_kv, asset_kind="line",
                                        evidence_status=connection, source_ref=way["osm_way_id"]))
            branch_writer.writerow(dict(branch_id=f"PTDTRAFO:{code}", from_bus=mv_bus, to_bus=lv_bus,
                                        from_kv=mv_kv, to_kv=0.4, asset_kind="transformer",
                                        evidence_status="PUBLIC_PTD_CAPACITY_SYNTHETIC_IMPEDANCE", source_ref=code))
            ptd_writer.writerow(dict(ptd_code=code, mv_voltage_kv=mv_kv, mv_root_bus=root_id, hv_bus=hv_bus,
                                     nearest_osm_way_id=way["osm_way_id"], osm_vertex_distance_km=round(float(mv_gap), 4),
                                     hv_anchor_distance_km=round(float(hv_gap), 4), capacity_kva=capacity_kva,
                                     utilisation_band=ptd.get("nivel_utilizacao", ""), customer_count_public=ptd.get("num_clientes", ""),
                                     peak_load_proxy_mva=round(peak_mva, 6), load_status=util_status,
                                     connection_status=connection))
            previous = lv_bus
            for segment in (1, 2):
                endpoint = f"LV:{code}:{segment}"
                bus_writer.writerow(dict(bus_id=endpoint, voltage_kv=0.4, lat="", lon="",
                                         source_ref=code, evidence_status="SYNTHETIC_LV_FEEDER_NODE"))
                branch_writer.writerow(dict(branch_id=f"LVLINE:{code}:{segment}", from_bus=previous,
                                            to_bus=endpoint, from_kv=0.4, to_kv=0.4, asset_kind="line",
                                            evidence_status="SYNTHETIC_LV_TOPOLOGY_AND_IMPEDANCE", source_ref=code))
                load_writer.writerow(dict(load_id=f"LVLOAD:{code}:{segment}", bus_id=endpoint,
                                          p_mw=round(peak_mva * 0.97 / 2, 8),
                                          q_mvar=round(peak_mva * math.sqrt(1 - 0.97 ** 2) / 2, 8),
                                          evidence_status=util_status, source_ptd_code=code))
                previous = endpoint
            counts["ptds"] += 1
    ptd_codes = [p["cod_instalacao"] for p in valid_ptds]
    base_voltages = {float(b[1]) for b in base_buses if b[1] is not None}
    operative_voltages = sorted(base_voltages | set(selected_voltages) | {0.4})
    all_voltages = sorted(set(operative_voltages) | set(osm_counts))
    errors = []
    if len(ptd_codes) != len(set(ptd_codes)):
        errors.append("Duplicate PTD installation code")
    if len(valid_ptds) != len(ptds):
        errors.append(f"{len(ptds)-len(valid_ptds)} PTDs lack code, coordinates or nonnegative capacity")
    if not {0.4, 60.0, 150.0, 220.0, 400.0} <= set(all_voltages):
        errors.append("Expected transmission and LV voltage classes missing")
    if not selected_voltages:
        errors.append("No PTD assigned to a mapped MV voltage class")
    exported_checks, export_errors = verify_national_exports(output)
    errors.extend(export_errors)
    audit = {
        "result": "PASS" if not errors else "FAIL",
        "scope": "Nationwide all-voltage synthetic equivalent inventory; not an operator-verified MV/LV topology or national solved power flow",
        "public_ptd_export_count": len(ptds), "ptds_in_network": len(valid_ptds),
        "voltage_levels_kv": all_voltages,
        "operative_voltage_levels_kv": operative_voltages,
        "candidate_only_voltage_levels_kv": sorted(set(all_voltages) - set(operative_voltages)),
        "osm_way_counts_by_voltage_kv": {str(k): v for k, v in sorted(osm_counts.items())},
        "ptd_nearest_osm_mv_voltage_counts": {str(k): v for k, v in sorted(selected_voltages.items())},
        "ptds_over_1km_from_osm_mv_vertex": far_osm,
        "ptds_over_20km_from_60kv_anchor": far_hv,
        "object_counts": dict(counts),
        "exported_file_checks": exported_checks,
        "source_files": {"ptd": str(ptd_path), "osm": str(osm_path), "base_database": str(database)},
        "validation_errors": errors,
        "remaining_gates": [
            "OSM ways are candidate geometry, not yet wired into the operative synthetic radial MV layer",
            "Confirm PTD-to-MV electrical terminal links and 60/MV transformer inventory",
            "Replace assumed MV and LV impedances with calibrated Portuguese parameter tables",
            "Build chronological PTD/LV loads from public profiles and reconcile with SimPT60 15-minute cases",
            "Run partitioned nationwide AC power flow and detailed three-phase LV cases",
        ],
    }
    (output / "validation.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--osm", type=Path, default=OSM)
    parser.add_argument("--national", action="store_true", help="Generate the all-voltage national equivalent inventory")
    parser.add_argument("--national-ptd-json", type=Path, default=NATIONAL_PTD,
                        help="Full E-REDES PTD export JSON array; download from the public exports API")
    parser.add_argument("--national-output", type=Path, default=NATIONAL_OUT)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--ptd-json", type=Path, default=None)
    parser.add_argument("--fetch", action="store_true", help="Download and save a public E-REDES PTD snapshot")
    parser.add_argument("--municipality-code", default="1311")
    parser.add_argument("--sample-size", type=int, default=100)
    parser.add_argument("--max-ptd", type=int, default=3)
    parser.add_argument("--radius-km", type=float, default=20.0)
    args = parser.parse_args()
    if args.national:
        if not args.national_ptd_json.exists():
            parser.error(f"Missing national PTD export: {args.national_ptd_json}")
        return build_national(args.database, args.osm, args.national_ptd_json, args.national_output)
    if not 1 <= args.max_ptd <= args.sample_size <= 100:
        parser.error("Require 1 <= --max-ptd <= --sample-size <= 100")
    args.output.mkdir(parents=True, exist_ok=True)
    source_file = args.ptd_json or args.output / "source_ptd_sample.json"
    payload = load_ptds(source_file, args.fetch, args.municipality_code, args.sample_size)
    anchor, selected = select_cluster(payload.get("results", []), source_buses(args.database),
                                      args.max_ptd, args.radius_km)
    mv_kv, selected, osm_evidence = nearby_osm_mv(args.osm, anchor, selected)
    net, tables = build_network(anchor, selected, mv_kv)
    pp.runpp(net, init="flat", max_iteration=30, numba=False)
    vm = [float(net.res_bus.at[i, "vm_pu"]) for i, b in enumerate(tables["nodes"]) if b["voltage_kv"] == 0.4]
    ptd_loading = [float(net.res_trafo.at[i, "loading_percent"]) for i in range(1, len(net.trafo))]
    errors = []
    if not net.converged:
        errors.append("AC power flow did not converge")
    if any(not 0.9 <= v <= 1.1 for v in vm):
        errors.append("LV bus voltage outside 0.9-1.1 pu pilot envelope")
    if any(x > 100 for x in ptd_loading):
        errors.append("PTD transformer above nameplate capacity")
    if len(tables["loads"]) != 3 * len(selected):
        errors.append("LV load cardinality mismatch")
    if len({p["cod_instalacao"] for p in selected}) != len(selected):
        errors.append("Duplicate public PTD code")
    audit = {
        "result": "PASS" if not errors else "FAIL",
        "scope": "Small balanced snapshot pilot, not nationwide or three-phase LV coverage",
        "source_url": SOURCE,
        "municipality_ptd_total_count": payload.get("total_count"),
        "national_ptd_total_count": payload.get("national_ptd_total_count"),
        "source_sample_count": len(payload.get("results", [])),
        "anchor_60kv_bus": anchor["bus_id"],
        "selected_ptds": [{"code": p["cod_instalacao"], "capacity_kva": p["potencia_transformacao_kva"],
                           "utilisation_band": p["nivel_utilizacao"], "customers_public_field": p.get("num_clientes"),
                           "distance_to_anchor_km": round(p["anchor_distance_km"], 3),
                           "nearest_osm_mv_way_id": p["nearest_osm_mv_way_id"],
                           "nearest_osm_mv_voltage_kv": p["nearest_osm_mv_voltage_kv"],
                           "nearest_osm_mv_vertex_distance_km": round(p["nearest_osm_mv_vertex_distance_km"], 3)} for p in selected],
        "osm_mv_evidence": osm_evidence,
        "network_counts": {name: len(value) for name, value in tables.items()},
        "ac_power_flow_converged": bool(net.converged),
        "lv_voltage_pu_min": min(vm), "lv_voltage_pu_max": max(vm),
        "ptd_loading_percent_max": max(ptd_loading),
        "load_p_mw": sum(row["p_mw"] for row in tables["loads"]),
        "validation_errors": errors,
        "remaining_gates": [
            "Reconstruct MV feeder connections for nationwide PTD coverage; OSM proximity does not prove electrical connectivity",
            "Attach hourly/postal-code demand profiles without claiming customer-level observations",
            "Use a three-phase four-wire solver for unbalance and neutral-current studies",
            "Calibrate synthetic feeder lengths and impedances with Portuguese network statistics and benchmark feeders",
            "Validate regional aggregate demand and public power-quality observations independently",
        ],
    }
    for name, table in tables.items():
        write_csv(args.output / f"{name}.csv", table)
    pp.to_json(net, str(args.output / "pilot_network.json"))
    (args.output / "validation.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
