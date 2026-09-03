from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT = Path(__file__).resolve().parents[1]
RAW = PROJECT / "data" / "raw"
MANIFESTS = PROJECT / "data" / "manifests"
TABLES = PROJECT / "outputs" / "tables"
MODEL = PROJECT / "outputs" / "model"
VALIDATION = PROJECT / "outputs" / "validation"
POWER_FLOW = PROJECT / "outputs" / "power_flow"


def ensure_dirs() -> None:
    for path in (RAW, MANIFESTS, TABLES, MODEL, VALIDATION, POWER_FLOW):
        path.mkdir(parents=True, exist_ok=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def trace(path: Path, source_id: str, url: str, role: str) -> dict[str, Any]:
    return {
        "source_id": source_id,
        "role": role,
        "url": url,
        "local_path": str(path.relative_to(PROJECT)),
        "bytes": path.stat().st_size,
        "sha256": sha256(path),
    }


def normalize_name(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value).lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"\b(subestacao|posto de corte|posto|ren|rnt|se)\b", " ", text)
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", text)).strip()


def haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon, dlat = lon2 - lon1, lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * 6371008.8 * math.asin(min(1.0, math.sqrt(h)))


def polyline_length_km(coords: Iterable[Iterable[float]]) -> float:
    points = [(float(point[0]), float(point[1])) for point in coords]
    return sum(haversine_m(a, b) for a, b in zip(points, points[1:])) / 1000.0


def parse_voltage_values(value: Any, allowed: set[int]) -> list[int]:
    if value is None:
        return []
    values: set[int] = set()
    for raw in re.findall(r"\d+(?:[.,]\d+)?", str(value)):
        number = float(raw.replace(",", "."))
        kv = int(round(number / 1000.0 if number > 1000 else number))
        if kv in allowed:
            values.add(kv)
    return sorted(values)


def parse_osm_voltage_values(value: Any, allowed: set[int]) -> list[int]:
    """Parse OSM power voltages, whose documented unit is volts.

    Values below 1,000 are deliberately not interpreted as kV.  This avoids
    promoting ordinary ``voltage=400`` low-voltage equipment to 400 kV.
    """
    if value is None:
        return []
    values: set[int] = set()
    for raw in re.findall(r"\d+(?:[.,]\d+)?", str(value)):
        volts = float(raw.replace(",", "."))
        if volts < 1000:
            continue
        kv = int(round(volts / 1000.0))
        if kv in allowed:
            values.add(kv)
    return sorted(values)


def parse_mva(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*([kmg]?va)", str(value), re.I)
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    unit = match.group(2).lower()
    return number / 1000.0 if unit == "kva" else number * 1000.0 if unit == "gva" else number


def parse_mw(value: Any) -> float | None:
    if value is None:
        return None
    match = re.search(r"([0-9]+(?:[.,][0-9]+)?)\s*([kmg]?w)", str(value), re.I)
    if not match:
        return None
    number = float(match.group(1).replace(",", "."))
    unit = match.group(2).lower()
    return number / 1000.0 if unit == "kw" else number * 1000.0 if unit == "gw" else number


def element_center(element: dict[str, Any]) -> tuple[float, float] | None:
    if "lon" in element and "lat" in element:
        return float(element["lon"]), float(element["lat"])
    if isinstance(element.get("center"), dict):
        return float(element["center"]["lon"]), float(element["center"]["lat"])
    geometry = element.get("geometry") or []
    if geometry:
        lon = sum(float(p["lon"]) for p in geometry) / len(geometry)
        lat = sum(float(p["lat"]) for p in geometry) / len(geometry)
        return lon, lat
    return None


def point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Dependency-free ray-casting point-in-polygon test."""
    inside = False
    if len(ring) < 3:
        return False
    previous = ring[-1]
    for current in ring:
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if (y1 > lat) != (y2 > lat):
            crossing = (x2 - x1) * (lat - y1) / (y2 - y1) + x1
            if lon < crossing:
                inside = not inside
        previous = current
    return inside


def point_in_geojson_geometry(lon: float, lat: float, geometry: dict[str, Any]) -> bool:
    kind = geometry.get("type")
    coordinates = geometry.get("coordinates") or []
    polygons = [coordinates] if kind == "Polygon" else coordinates if kind == "MultiPolygon" else []
    for polygon in polygons:
        if not polygon or not point_in_ring(lon, lat, polygon[0]):
            continue
        if not any(point_in_ring(lon, lat, hole) for hole in polygon[1:]):
            return True
    return False
