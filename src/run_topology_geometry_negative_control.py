"""Run spatial-alignment negative controls for PT60 public-source matching.

The control keeps branch endpoint labels and other tabular attributes fixed but
translates each retained branch geometry by a deterministic offset. This tests
whether corridor-based OSM/OpenInfraMap evidence declines when spatial alignment
is deliberately broken. It is a matcher-selectivity check, not a topology
accuracy estimate.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

import config
from cross_validate_pt60_topology_external_sources import (
    build_osm_evidence,
    cross_validate,
)
from utils import utc_now, write_json, write_text


OUT = config.PROCESSED_DIR / "topology_validation"
BRANCHES = config.PROCESSED_DIR / "at_interfacility_candidate_branches.csv"
OSM_CACHE = OUT / "pt_osm_openinframap_60kv_power_ways.json"
REAL_MATCHES = OUT / "pt_topology_cross_validation_osm_matches.csv"
CONTROL_MATCHES = OUT / "matcher_negative_control_geometry.csv"
SUMMARY_JSON = OUT / "matcher_negative_control_geometry_summary.json"
REPORT = config.REPORTS_DIR / "104_pt60_spatial_alignment_negative_control.md"

DEFAULT_SEED = 20260714
DEFAULT_DELTA_LON = 1.75
DEFAULT_DELTA_LAT = 0.85
GEOMETRY_STRONG_STATUSES = {"OSM_GEOMETRY_OPERATOR_STRONG"}
GEOMETRY_MEDIUM_STATUSES = {"OSM_GEOMETRY_MEDIUM"}


def load_osm_from_cache(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise RuntimeError(f"Missing OSM/OpenInfraMap cache: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    return build_osm_evidence(data)


def translate_position(position: Any, delta_lon: float, delta_lat: float) -> Any:
    if not isinstance(position, list) or len(position) < 2:
        return position
    lon, lat = position[0], position[1]
    if not isinstance(lon, (int, float)) or not isinstance(lat, (int, float)):
        return position
    translated = list(position)
    translated[0] = float(lon) + delta_lon
    translated[1] = float(lat) + delta_lat
    return translated


def translate_coordinates(coordinates: Any, delta_lon: float, delta_lat: float) -> Any:
    if not isinstance(coordinates, list):
        return coordinates
    if coordinates and isinstance(coordinates[0], (int, float)):
        return translate_position(coordinates, delta_lon, delta_lat)
    return [translate_coordinates(item, delta_lon, delta_lat) for item in coordinates]


def translate_geometry(raw: object, delta_lon: float, delta_lat: float) -> str:
    geometry = json.loads(str(raw))
    geometry_type = geometry.get("type")
    if geometry_type not in {"LineString", "MultiLineString"}:
        raise RuntimeError(f"Unsupported geometry type in spatial control: {geometry_type}")
    geometry["coordinates"] = translate_coordinates(geometry.get("coordinates"), delta_lon, delta_lat)
    return json.dumps(geometry, ensure_ascii=False, separators=(",", ":"))


def geometry_bounds(raw_geometries: pd.Series) -> dict[str, float]:
    west: list[float] = []
    south: list[float] = []
    east: list[float] = []
    north: list[float] = []
    for raw in raw_geometries:
        geometry = json.loads(str(raw))
        coords = geometry.get("coordinates", [])
        stack = [coords]
        points: list[list[float]] = []
        while stack:
            item = stack.pop()
            if isinstance(item, list) and len(item) >= 2 and all(isinstance(x, (int, float)) for x in item[:2]):
                points.append(item)
            elif isinstance(item, list):
                stack.extend(item)
        if points:
            lons = [float(point[0]) for point in points]
            lats = [float(point[1]) for point in points]
            west.append(min(lons))
            south.append(min(lats))
            east.append(max(lons))
            north.append(max(lats))
    return {
        "west": min(west),
        "south": min(south),
        "east": max(east),
        "north": max(north),
    }


def geometry_validity(raw_geometries: pd.Series) -> dict[str, int]:
    valid = 0
    invalid = 0
    for raw in raw_geometries:
        try:
            geometry = json.loads(str(raw))
        except json.JSONDecodeError:
            invalid += 1
            continue
        if geometry.get("type") not in {"LineString", "MultiLineString"}:
            invalid += 1
            continue
        bounds = geometry_bounds(pd.Series([json.dumps(geometry)]))
        if all(math.isfinite(value) for value in bounds.values()):
            valid += 1
        else:
            invalid += 1
    return {
        "valid_transformed_geometries": valid,
        "invalid_transformed_geometries": invalid,
        "records_excluded_after_transform": 0,
    }


def displace_branch_geometries(branches: pd.DataFrame, seed: int, delta_lon: float, delta_lat: float) -> pd.DataFrame:
    controlled = branches.copy()
    controlled["negative_control_seed"] = seed
    controlled["negative_control_type"] = "fixed_lonlat_translation"
    controlled["geometry_delta_lon"] = delta_lon
    controlled["geometry_delta_lat"] = delta_lat
    controlled["original_geometry"] = controlled["geometry"]
    controlled["geometry"] = controlled["geometry"].apply(
        lambda value: translate_geometry(value, delta_lon=delta_lon, delta_lat=delta_lat)
    )
    return controlled


def status_summary(matches: pd.DataFrame) -> dict[str, object]:
    counts = matches["external_evidence_status"].value_counts().sort_index()
    total = int(len(matches))
    geometry_strong = int(matches["external_evidence_status"].isin(GEOMETRY_STRONG_STATUSES).sum())
    geometry_medium = int(matches["external_evidence_status"].isin(GEOMETRY_MEDIUM_STATUSES).sum())
    corridor_near = pd.to_numeric(matches["min_distance_m"], errors="coerce").le(250)
    corridor_coverage = pd.to_numeric(matches["branch_coverage_500m"], errors="coerce").ge(0.5) | pd.to_numeric(
        matches["osm_coverage_500m"], errors="coerce"
    ).ge(0.5)
    corridor_evidence = corridor_near & corridor_coverage
    median_distance = pd.to_numeric(matches["median_branch_to_osm_m"], errors="coerce")
    finite_median_distance = median_distance[median_distance.apply(math.isfinite)]
    median_of_medians = float(finite_median_distance.median()) if len(finite_median_distance) else None
    return {
        "branches": total,
        "status_counts": {str(k): int(v) for k, v in counts.items()},
        "geometry_status_branches": geometry_strong + geometry_medium,
        "geometry_operator_strong_branches": geometry_strong,
        "geometry_medium_branches": geometry_medium,
        "corridor_evidence_branches": int(corridor_evidence.sum()),
        "corridor_evidence_rate": float(corridor_evidence.mean()) if total else None,
        "median_of_median_branch_to_osm_m": median_of_medians,
        "branches_with_min_distance_lte_250m": int(corridor_near.sum()),
    }


def write_report(summary: dict[str, object]) -> None:
    real_counts = summary["real"]["status_counts"]
    control_counts = summary["negative_control"]["status_counts"]
    statuses = sorted(set(real_counts) | set(control_counts))
    lines = [
        "# 104 PT60 Spatial-Alignment Negative Control",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "## Purpose",
        "",
        "This negative control keeps endpoint names and branch attributes fixed but translates retained branch geometries by a deterministic longitude/latitude offset. The test checks whether corridor-based OSM/OpenInfraMap evidence declines when spatial alignment is deliberately broken.",
        "",
        "## Design",
        "",
        f"- branches tested: {summary['branches_tested']}",
        f"- seed: {summary['seed']}",
        f"- displacement: {summary['delta_lon_degrees']} degrees longitude and {summary['delta_lat_degrees']} degrees latitude",
        "- endpoint names, facility codes, voltage, status, length and branch identifiers are retained",
        "- interpretation: spatial matcher selectivity only; this is not an accuracy, precision or recall estimate",
        "",
        "## Results",
        "",
        f"- real corridor-evidence branches: {summary['real']['corridor_evidence_branches']} / {summary['branches_tested']} ({summary['real']['corridor_evidence_rate']:.4f})",
        f"- displaced-geometry corridor-evidence branches: {summary['negative_control']['corridor_evidence_branches']} / {summary['branches_tested']} ({summary['negative_control']['corridor_evidence_rate']:.4f})",
        f"- absolute corridor-evidence rate drop: {summary['corridor_evidence_rate_drop']:.4f}",
        f"- relative corridor-evidence reduction: {summary['corridor_evidence_relative_reduction']:.4f}",
        "",
        "| external_evidence_status | real branches | displaced-geometry branches |",
        "|---|---:|---:|",
    ]
    for status in statuses:
        lines.append(f"| {status} | {int(real_counts.get(status, 0))} | {int(control_counts.get(status, 0))} |")
    lines.extend(
        [
            "",
            "## Paper Interpretation",
            "",
            "A decline in corridor evidence after spatial displacement supports the selectivity of the geometry component of the public-source matcher. Name-based evidence can remain because endpoint labels are intentionally retained; these categories should still be described as public-source concordance, not branch truth.",
        ]
    )
    write_text(REPORT, "\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--delta-lon", type=float, default=DEFAULT_DELTA_LON)
    parser.add_argument("--delta-lat", type=float, default=DEFAULT_DELTA_LAT)
    args = parser.parse_args()

    if not BRANCHES.exists():
        raise RuntimeError(f"Missing retained branch table: {BRANCHES}")
    OUT.mkdir(parents=True, exist_ok=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    branches = pd.read_csv(BRANCHES)
    osm = load_osm_from_cache(OSM_CACHE)
    if REAL_MATCHES.exists():
        real_matches = pd.read_csv(REAL_MATCHES)
    else:
        real_matches = cross_validate(branches, osm)

    controlled_branches = displace_branch_geometries(
        branches,
        seed=args.seed,
        delta_lon=args.delta_lon,
        delta_lat=args.delta_lat,
    )
    control_matches = cross_validate(controlled_branches, osm)
    identity_cols = [
        "branch_id",
        "negative_control_seed",
        "negative_control_type",
        "geometry_delta_lon",
        "geometry_delta_lat",
        "original_geometry",
    ]
    control_matches = control_matches.merge(
        controlled_branches[identity_cols],
        on="branch_id",
        how="left",
        validate="one_to_one",
    )
    control_matches.to_csv(CONTROL_MATCHES, index=False)

    real = status_summary(real_matches)
    control = status_summary(control_matches)
    real_rate = float(real["corridor_evidence_rate"] or 0.0)
    control_rate = float(control["corridor_evidence_rate"] or 0.0)
    original_bounds = geometry_bounds(branches["geometry"])
    displaced_bounds = geometry_bounds(controlled_branches["geometry"])
    transformed_validity = geometry_validity(controlled_branches["geometry"])
    summary = {
        "generated_at": utc_now(),
        "seed": args.seed,
        "branches_tested": int(len(branches)),
        "control_design": "fixed_lonlat_translation",
        "delta_lon_degrees": args.delta_lon,
        "delta_lat_degrees": args.delta_lat,
        "preserved_fields": [
            "branch_id",
            "from_facility_name",
            "to_facility_name",
            "from_facility_code",
            "to_facility_code",
            "voltage",
            "status",
            "total_length_km",
            "confidence_score",
            "number_of_original_segments",
        ],
        "corrupted_fields": ["geometry"],
        "original_geometry_bounds": original_bounds,
        "displaced_geometry_bounds": displaced_bounds,
        "transformed_geometry_validity": transformed_validity,
        "real": real,
        "negative_control": control,
        "corridor_evidence_rate_drop": real_rate - control_rate,
        "corridor_evidence_relative_reduction": (real_rate - control_rate) / real_rate if real_rate else None,
        "outputs": {
            "negative_control_matches": str(CONTROL_MATCHES.relative_to(config.ROOT_DIR)),
            "summary": str(SUMMARY_JSON.relative_to(config.ROOT_DIR)),
            "report": str(REPORT.relative_to(config.ROOT_DIR)),
        },
        "claim_boundary": "This negative control tests spatial matcher selectivity only. It does not estimate topology precision, recall, operator validation, or real-grid completeness.",
    }
    write_json(SUMMARY_JSON, summary)
    write_report(summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
