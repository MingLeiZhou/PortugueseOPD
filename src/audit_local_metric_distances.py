#!/usr/bin/env python3
"""Audit the frozen local equirectangular workspace against great-circle distance.

This audit does not alter the released topology.  It quantifies the scale error
of the metric approximation used by the v1.0.1 reconstruction over the actual
released mainland-Portugal coordinate extent.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "data" / "releases" / "PT60-Candidate-v1.0.1"
OUTPUT = ROOT / "paper" / "analysis" / "local_metric_distance_audit.json"
R = 6_371_008.8
LON0 = -8.532604
LAT0 = 39.567953
THRESHOLDS_M = [0.5, 50.0, 100.0, 250.0, 500.0]


def local_xy(lon: float, lat: float) -> tuple[float, float]:
    return (
        R * math.radians(lon - LON0) * math.cos(math.radians(LAT0)),
        R * math.radians(lat - LAT0),
    )


def local_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    ax, ay = local_xy(*a)
    bx, by = local_xy(*b)
    return math.hypot(bx - ax, by - ay)


def great_circle_distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, a)
    lon2, lat2 = map(math.radians, b)
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(min(1.0, math.sqrt(h)))


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return float("nan")
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def coordinate_sequences(geometry: dict) -> list[list[list[float]]]:
    if geometry["type"] == "LineString":
        return [geometry["coordinates"]]
    if geometry["type"] == "MultiLineString":
        return geometry["coordinates"]
    raise ValueError(f"Unsupported geometry type: {geometry['type']}")


def main() -> None:
    branch_path = RELEASE / "core_topology" / "at_interfacility_candidate_branches.csv"
    coordinates: list[tuple[float, float]] = []
    segment_errors: list[dict[str, float]] = []
    with branch_path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            geometry = json.loads(row["geometry"])
            for sequence in coordinate_sequences(geometry):
                points = [(float(lon), float(lat)) for lon, lat, *_ in sequence]
                coordinates.extend(points)
                for a, b in zip(points, points[1:]):
                    reference = great_circle_distance(a, b)
                    if reference == 0:
                        continue
                    approximate = local_distance(a, b)
                    segment_errors.append(
                        {
                            "absolute_error_m": abs(approximate - reference),
                            "relative_error_percent": abs(approximate - reference) / reference * 100,
                        }
                    )

    latitudes = sorted({lat for _, lat in coordinates})
    threshold_results = []
    for threshold in THRESHOLDS_M:
        errors = []
        for lat in latitudes:
            # Construct an east-west displacement whose spherical arc length is
            # the requested threshold, then measure it in the frozen workspace.
            dlon = math.degrees(threshold / (R * math.cos(math.radians(lat))))
            reference = great_circle_distance((LON0, lat), (LON0 + dlon, lat))
            approximate = local_distance((LON0, lat), (LON0 + dlon, lat))
            errors.append(approximate - reference)
        threshold_results.append(
            {
                "threshold_m": threshold,
                "maximum_absolute_scale_error_m": round(max(abs(value) for value in errors), 6),
                "maximum_relative_scale_error_percent": round(max(abs(value) for value in errors) / threshold * 100, 6),
                "signed_error_range_m": [round(min(errors), 6), round(max(errors), 6)],
            }
        )

    absolute = [row["absolute_error_m"] for row in segment_errors]
    relative = [row["relative_error_percent"] for row in segment_errors]
    report = {
        "status": "PASS_WITH_DISCLOSED_APPROXIMATION",
        "release": "PT60-Candidate-v1.0.1",
        "comparison": "pipeline local equirectangular distance versus spherical WGS84-coordinate great-circle distance",
        "pipeline_workspace": {"lon0": LON0, "lat0": LAT0, "radius_m": R, "units": "m"},
        "coordinate_extent_epsg4326": {
            "longitude_min": min(lon for lon, _ in coordinates),
            "longitude_max": max(lon for lon, _ in coordinates),
            "latitude_min": min(lat for _, lat in coordinates),
            "latitude_max": max(lat for _, lat in coordinates),
        },
        "released_geometry_segments_audited": len(segment_errors),
        "segment_error": {
            "absolute_median_m": round(quantile(absolute, 0.5), 6),
            "absolute_p95_m": round(quantile(absolute, 0.95), 6),
            "absolute_max_m": round(max(absolute), 6),
            "relative_median_percent": round(quantile(relative, 0.5), 6),
            "relative_p95_percent": round(quantile(relative, 0.95), 6),
            "relative_max_percent": round(max(relative), 6),
        },
        "threshold_scale_error": threshold_results,
        "interpretation": (
            "The audit quantifies geometric scale distortion only. It does not test facility assignment or topology truth. "
            "The v1.0.1 topology is unchanged; future reconstruction releases should use ETRS89 / Portugal TM06 (EPSG:3763) "
            "or ellipsoidal geodesic distance if thresholds are retuned."
        ),
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
