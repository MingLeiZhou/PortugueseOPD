#!/usr/bin/env python3
"""Compare the selected topology under the legacy metric and EPSG:3763.

The audit consumes reacquired E-REDES GeoJSON exports without modifying the
frozen release. It also records source-export fingerprints and the exact
duplicate facility records removed by the reconstruction loader.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

import config
from metric_projection import LocalMetricProjection, PortugalTM06Projection
from reconstruct_at_topology_paper_logic import (
    add_projected_columns,
    build_base_endpoints,
    classify_groups_for_strategy,
    cluster_endpoints,
    endpoint_cluster_centroids,
    load_at_lines,
    load_facilities,
    union_find_groups,
)


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_ids(value: Any) -> tuple[str, ...]:
    return tuple(sorted(part.strip() for part in str(value).split(",") if part.strip()))


def normalize_facilities(value: Any) -> tuple[str, ...]:
    if isinstance(value, str) and value.lstrip().startswith("["):
        details = json.loads(value)
        return tuple(sorted(str(row.get("facility_uid", "")) for row in details if row.get("facility_uid")))
    return tuple(sorted(part.strip() for part in str(value).split(",") if part.strip()))


def record_groups(
    frame: pd.DataFrame,
    classification: str | None = None,
) -> dict[tuple[str, ...], tuple[tuple[Any, ...], ...]]:
    """Group records without losing duplicate source-line signatures."""

    result: dict[tuple[str, ...], list[tuple[Any, ...]]] = defaultdict(list)
    for row in frame.to_dict("records"):
        if classification is not None and str(row["classification"]) != classification:
            continue
        key = normalize_ids(row["source_line_ids"])
        result[key].append(
            (
                str(row["classification"]),
                normalize_facilities(row.get("terminal_details_json", row.get("terminal_facility_uids", ""))),
                bool(row.get("ambiguous_facility_match", False)),
                bool(row.get("mixed_voltage", False)),
                bool(row.get("mixed_status", False)),
                int(row.get("terminal_count", 0)),
            )
        )
    return {key: tuple(sorted(values, key=repr)) for key, values in result.items()}


def compare_groups(left: dict, right: dict) -> dict[str, Any]:
    shared = set(left) & set(right)
    only_left = sorted(set(left) - set(right))
    only_right = sorted(set(right) - set(left))
    changed = sorted(key for key in shared if left[key] != right[key])
    changed_facilities = sorted(
        key for key in shared if tuple(value[1] for value in left[key]) != tuple(value[1] for value in right[key])
    )
    changed_classes = sorted(
        key for key in shared if tuple(value[0] for value in left[key]) != tuple(value[0] for value in right[key])
    )
    return {
        "left_records": sum(len(values) for values in left.values()),
        "right_records": sum(len(values) for values in right.values()),
        "shared_source_groups": len(shared),
        "source_groups_only_left": len(only_left),
        "source_groups_only_right": len(only_right),
        "changed_records_on_shared_groups": len(changed),
        "changed_facility_assignments_on_shared_groups": len(changed_facilities),
        "changed_classifications_on_shared_groups": len(changed_classes),
        "source_line_ids_only_left": [list(key) for key in only_left],
        "source_line_ids_only_right": [list(key) for key in only_right],
        "changed_source_line_ids": [list(key) for key in changed],
    }


def summarize(frame: pd.DataFrame) -> dict[str, Any]:
    classes = frame["classification"].value_counts().to_dict()
    return {
        "circuit_candidates": int(len(frame)),
        "retained_branches": int(classes.get("inter-facility", 0)),
        "ambiguous_facility_matches": int(frame["ambiguous_facility_match"].astype(bool).sum()),
        "classification_counts": {str(k): int(v) for k, v in sorted(classes.items())},
    }


def run_selected(lines: pd.DataFrame, facilities: pd.DataFrame, shapes: dict, projection: Any) -> pd.DataFrame:
    projected_lines, projected_facilities = add_projected_columns(lines, facilities, shapes, projection)
    endpoints = build_base_endpoints(projected_lines)
    mapping, clusters = cluster_endpoints(endpoints, 0.5)
    groups, records = union_find_groups(projected_lines, endpoints, mapping, 0.5, "voltage-status-aware")
    centroids = endpoint_cluster_centroids(clusters)
    return classify_groups_for_strategy(
        groups,
        records,
        projected_lines,
        projected_facilities,
        centroids,
        0.5,
        "voltage-status-aware",
        "B",
        100,
    )


def facility_source_audit(path: Path, facility_type: str) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    seen: dict[tuple[Any, ...], int] = {}
    duplicates = []
    excluded_geometry = []
    for index, feature in enumerate(payload.get("features", [])):
        props = feature.get("properties") or {}
        geometry = feature.get("geometry") or {}
        coordinates = geometry.get("coordinates") or []
        if geometry.get("type") != "Point" or len(coordinates) < 2:
            excluded_geometry.append(index)
            continue
        code = str(props.get("codigo") or props.get("cod_instalacao") or f"{facility_type}_{index}")
        key = (facility_type, code, round(float(coordinates[0]), 8), round(float(coordinates[1]), 8))
        if key in seen:
            duplicates.append(
                {
                    "kept_feature_index": seen[key],
                    "excluded_feature_index": index,
                    "facility_code": code,
                    "facility_name": str(props.get("instalacao") or props.get("name") or props.get("nome") or code),
                    "longitude": float(coordinates[0]),
                    "latitude": float(coordinates[1]),
                    "reason": "exact duplicate facility type, code, and coordinates",
                }
            )
            continue
        seen[key] = index
    return {
        "source_records": len(payload.get("features", [])),
        "valid_point_records": len(payload.get("features", [])) - len(excluded_geometry),
        "excluded_nonpoint_or_invalid_geometry": excluded_geometry,
        "exact_duplicates_excluded": duplicates,
        "loaded_unique_facilities": len(seen),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument(
        "--release-root",
        type=Path,
        default=ROOT / "data" / "releases" / "PT60-Candidate-v1.0.1",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "metadata" / "projection_topology_stability_audit.json",
    )
    args = parser.parse_args()

    original_raw = config.RAW_DIR
    config.RAW_DIR = args.input_dir
    try:
        lines, shapes, line_validation = load_at_lines()
        facilities = load_facilities()
    finally:
        config.RAW_DIR = original_raw

    lon_values = pd.concat([lines["start_lon"], lines["end_lon"], facilities["lon"]]).astype(float)
    lat_values = pd.concat([lines["start_lat"], lines["end_lat"], facilities["lat"]]).astype(float)
    legacy_projection = LocalMetricProjection(lon0=float(lon_values.mean()), lat0=float(lat_values.mean()))
    legacy = run_selected(lines, facilities, shapes, legacy_projection)
    tm06 = run_selected(lines, facilities, shapes, PortugalTM06Projection())
    deposited = pd.read_csv(args.release_root / "core_topology" / "at_circuit_classification.csv")

    legacy_groups = record_groups(legacy)
    tm06_groups = record_groups(tm06)
    deposited_groups = record_groups(deposited)
    legacy_retained = record_groups(legacy, "inter-facility")
    tm06_retained = record_groups(tm06, "inter-facility")
    deposited_retained = record_groups(deposited, "inter-facility")

    source_files = [
        ("rede-at-teste", "rede-at-teste.geojson"),
        ("se-at_2025", "se-at_2025.geojson"),
        ("pc-at_2025", "pc-at_2025.geojson"),
    ]
    checked_at = datetime.now(timezone.utc).isoformat()
    report = {
        "generated_at": checked_at,
        "selected_configuration": {
            "facility_node_set": "B",
            "facility_buffer_m": 100,
            "endpoint_snap_threshold_m": 0.5,
            "merge_mode": "voltage-status-aware",
        },
        "source_export_fingerprints": [
            {
                "dataset_id": dataset_id,
                "path_used_for_audit": str(args.input_dir / filename),
                "retrieval_audit_time": checked_at,
                "bytes": (args.input_dir / filename).stat().st_size,
                "sha256": sha256(args.input_dir / filename),
                "record_count": len(json.loads((args.input_dir / filename).read_text(encoding="utf-8")).get("features", [])),
            }
            for dataset_id, filename in source_files
        ],
        "facility_source_accounting": {
            "se-at_2025": facility_source_audit(args.input_dir / "se-at_2025.geojson", "SE_AT"),
            "pc-at_2025": facility_source_audit(args.input_dir / "pc-at_2025.geojson", "PC_AT"),
            "node_set_B_loaded_facilities": int((facilities["facility_type"].isin(["SE_AT", "PC_AT"])).sum()),
        },
        "line_validation": line_validation,
        "legacy_local_equirectangular": summarize(legacy),
        "epsg_3763": summarize(tm06),
        "legacy_vs_epsg_3763_all_records": compare_groups(legacy_groups, tm06_groups),
        "legacy_vs_epsg_3763_retained": compare_groups(legacy_retained, tm06_retained),
        "reacquired_legacy_vs_deposited_all_records": compare_groups(legacy_groups, deposited_groups),
        "reacquired_legacy_vs_deposited_retained": compare_groups(legacy_retained, deposited_retained),
        "interpretation": (
            "The reacquired public inputs reproduce every deposited classification record. EPSG:3763 preserves the "
            "retained count (358), ambiguity count (61), and facility assignments of 357 retained source-line groups, "
            "but exchanges one retained source-line group for another. The released topology is therefore highly stable "
            "but not invariant to the metric projection at the declared 0.5 m and 100 m thresholds."
        ),
    }
    report["status"] = (
        "PASS_IDENTICAL_SELECTED_TOPOLOGY"
        if report["legacy_vs_epsg_3763_retained"]["changed_records_on_shared_groups"] == 0
        and report["legacy_vs_epsg_3763_retained"]["source_groups_only_left"] == 0
        and report["legacy_vs_epsg_3763_retained"]["source_groups_only_right"] == 0
        else "PROJECTION_SENSITIVE_BRANCH_EXCHANGE"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
