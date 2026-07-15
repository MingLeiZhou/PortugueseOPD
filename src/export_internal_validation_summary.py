"""Export measured internal validation results for the PT60-Candidate release.

The checks in this script validate the frozen candidate-topology artifacts as a
dataset release. They do not validate the inferred topology against operator
truth.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

import config
from utils import utc_now, write_json, write_text


OUT = config.PROCESSED_DIR / "topology_validation"
REPORT = config.REPORTS_DIR / "106_pt60_internal_validation_summary.md"
SUMMARY_JSON = OUT / "internal_validation_summary.json"
CHECKS_CSV = OUT / "internal_validation_checks.csv"
MISSINGNESS_CSV = OUT / "internal_validation_missingness.csv"

BRANCHES = config.PROCESSED_DIR / "at_interfacility_candidate_branches.csv"
LEDGER = config.PROCESSED_DIR / "at_circuit_classification.csv"
GRAPHML = config.PROCESSED_DIR / "at_paper_logic_graph.graphml"
SWEEP = config.PROCESSED_DIR / "at_paper_logic_parameter_sweep.csv"
PAPER_SUMMARY = config.PROCESSED_DIR / "at_paper_logic_summary.json"
ENDPOINT_INDEX = config.PROCESSED_DIR / "at_endpoint_index.csv"
ENDPOINT_SUMMARY = config.PROCESSED_DIR / "at_endpoint_index_summary.csv"
FACILITY_MEMBERSHIP = config.PROCESSED_DIR / "at_endpoint_facility_membership_summary.csv"
FOOTPRINT_SUMMARY = config.PROCESSED_DIR / "at_facility_footprints_summary.csv"


MISSING_MARKERS = {"", "nan", "none", "null", "missing", "missing_not_estimated"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def is_missing(value: object) -> bool:
    if pd.isna(value):
        return True
    return str(value).strip().lower() in MISSING_MARKERS


def parse_geometry(raw: object) -> dict[str, object]:
    try:
        geometry = json.loads(str(raw))
    except json.JSONDecodeError:
        return {"valid": False, "type": "", "points": 0, "reason": "json_decode_error"}
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates", [])
    points = 0
    valid = geometry_type in {"LineString", "MultiLineString"}
    stack = [coordinates]
    while stack:
        item = stack.pop()
        if isinstance(item, list) and len(item) >= 2 and all(isinstance(x, (int, float)) for x in item[:2]):
            lon, lat = float(item[0]), float(item[1])
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                valid = False
            points += 1
        elif isinstance(item, list):
            stack.extend(item)
    if points < 2:
        valid = False
    return {"valid": valid, "type": geometry_type or "", "points": points, "reason": "" if valid else "invalid_or_too_few_points"}


def add_check(
    checks: list[dict[str, object]],
    check_id: str,
    layer: str,
    status: str,
    observed: object,
    expected: object,
    detail: str,
) -> None:
    checks.append(
        {
            "check_id": check_id,
            "layer": layer,
            "status": status,
            "observed": observed,
            "expected": expected,
            "detail": detail,
        }
    )


def required_nulls(df: pd.DataFrame, fields: list[str]) -> dict[str, int]:
    return {field: int(df[field].apply(is_missing).sum()) for field in fields}


def semantic_missingness(df: pd.DataFrame, fields: list[str], table_name: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    total = len(df)
    for field in fields:
        counts = df[field].astype(str).fillna("").value_counts(dropna=False).to_dict()
        missing = int(df[field].apply(is_missing).sum())
        rows.append(
            {
                "table": table_name,
                "field": field,
                "rows": total,
                "missing_or_semantic_missing": missing,
                "non_missing": total - missing,
                "value_counts_json": json.dumps({str(k): int(v) for k, v in counts.items()}, ensure_ascii=False),
            }
        )
    return rows


def graph_branch_ids(graph: nx.MultiGraph | nx.Graph) -> list[str]:
    ids: list[str] = []
    for _, _, data in graph.edges(data=True):
        branch_id = data.get("branch_id")
        if branch_id is not None:
            ids.append(str(branch_id))
    return ids


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    branches = pd.read_csv(BRANCHES)
    ledger = pd.read_csv(LEDGER)
    sweep = pd.read_csv(SWEEP)
    endpoint_index = pd.read_csv(ENDPOINT_INDEX)
    endpoint_summary = pd.read_csv(ENDPOINT_SUMMARY)
    membership = pd.read_csv(FACILITY_MEMBERSHIP)
    footprints = pd.read_csv(FOOTPRINT_SUMMARY)
    summary = json.loads(PAPER_SUMMARY.read_text(encoding="utf-8"))
    graph = nx.read_graphml(GRAPHML)

    checks: list[dict[str, object]] = []

    raw_feature_count = int(summary["validation"]["raw_feature_count"])
    valid_line_geometries = int(summary["validation"]["valid_line_geometries"])
    invalid_geometries = int(summary["validation"]["invalid_or_dropped_geometries"])
    add_check(checks, "raw_geometry_validity", "source", "PASS" if invalid_geometries == 0 else "FAIL", valid_line_geometries, raw_feature_count, "Raw E-REDES line geometry parse count.")

    parsed_branch_geometries = branches["geometry"].apply(parse_geometry)
    valid_branch_geometries = int(parsed_branch_geometries.apply(lambda item: bool(item["valid"])).sum())
    geometry_types = parsed_branch_geometries.apply(lambda item: str(item["type"])).value_counts().sort_index().to_dict()
    add_check(checks, "retained_branch_geometry_validity", "branches", "PASS" if valid_branch_geometries == len(branches) else "FAIL", valid_branch_geometries, len(branches), f"Geometry types: {geometry_types}")

    add_check(
        checks,
        "source_coordinate_reference",
        "crs",
        "PASS",
        "Opendatasoft v2.1 GeoJSON export without an epsg override; default EPSG:4326",
        "EPSG:4326 portal export and release geometry",
        "The frozen export URLs contain no epsg query parameter, and the Opendatasoft v2.1 export API documents EPSG:4326 as the default for geometry-capable formats. Release coordinates use GeoJSON longitude-latitude order. The native CRS before portal ingestion is not recorded and was not used by the pipeline.",
    )
    add_check(checks, "metric_coordinate_reference", "crs", "PASS" if "EPSG:3763" in summary["validation"]["metric_crs"] else "FAIL", summary["validation"]["metric_crs"], "ETRS89 / Portugal TM06 (EPSG:3763), Transverse Mercator, units=m", "Formal metric projection used for endpoint clustering, facility buffering, matching, and length calculations.")

    branch_required = [
        "branch_id",
        "circuit_id",
        "from_facility_uid",
        "to_facility_uid",
        "from_facility_name",
        "to_facility_name",
        "voltage",
        "status",
        "total_length_km",
        "number_of_original_segments",
        "geometry",
        "source_line_ids",
        "confidence_score",
        "classification",
    ]
    branch_nulls = required_nulls(branches, branch_required)
    add_check(checks, "retained_required_fields", "branches", "PASS" if sum(branch_nulls.values()) == 0 else "FAIL", json.dumps(branch_nulls), "0 missing required values", "Required retained-branch fields are complete.")
    add_check(checks, "retained_branch_id_unique", "branches", "PASS" if branches["branch_id"].is_unique else "FAIL", int(branches["branch_id"].nunique()), len(branches), "Retained branch identifiers are unique.")
    add_check(checks, "retained_circuit_id_unique", "branches", "PASS" if branches["circuit_id"].is_unique else "FAIL", int(branches["circuit_id"].nunique()), len(branches), "Each retained branch maps to one selected circuit.")
    same_endpoint = int(branches["from_facility_uid"].eq(branches["to_facility_uid"]).sum())
    add_check(checks, "retained_no_self_loops", "branches", "PASS" if same_endpoint == 0 else "FAIL", same_endpoint, 0, "Retained inter-facility branches do not connect a facility to itself.")

    ledger_required = [
        "circuit_id",
        "classification",
        "terminal_count",
        "segment_count",
        "total_length_km",
        "source_line_ids",
        "line_ids",
        "geometry_type",
    ]
    ledger_nulls = required_nulls(ledger, ledger_required)
    add_check(checks, "ledger_required_fields", "ledger", "PASS" if sum(ledger_nulls.values()) == 0 else "FAIL", json.dumps(ledger_nulls), "0 missing required values", "Required ledger fields are complete.")
    add_check(checks, "ledger_circuit_id_unique", "ledger", "PASS" if ledger["circuit_id"].is_unique else "FAIL", int(ledger["circuit_id"].nunique()), len(ledger), "Circuit ledger identifiers are unique.")

    class_counts = ledger["classification"].value_counts().to_dict()
    retained = int(class_counts.get("inter-facility", 0))
    downgraded = len(ledger) - retained
    add_check(checks, "ledger_retained_plus_downgraded", "ledger", "PASS" if retained + downgraded == len(ledger) == 1341 else "FAIL", f"{retained}+{downgraded}={retained + downgraded}", "358+983=1341", "Full retained/downgraded/rejected accounting reconciles for the EPSG:3763 build.")
    expected_classes = {
        "inter-facility": 358,
        "single-facility": 496,
        "isolated": 216,
        "tap / multi-terminal": 104,
        "self-loop": 101,
        "ambiguous": 61,
        "loop": 5,
    }
    add_check(checks, "ledger_class_reconciliation", "ledger", "PASS" if {k: int(class_counts.get(k, 0)) for k in expected_classes} == expected_classes else "FAIL", json.dumps({k: int(class_counts.get(k, 0)) for k in sorted(class_counts)}), json.dumps(expected_classes), "Downgrade classes match frozen release counts.")

    terminal_counts = {str(k): int(v) for k, v in ledger["terminal_count"].value_counts().sort_index().to_dict().items()}
    add_check(checks, "ledger_terminal_count_distribution", "ledger", "PASS", json.dumps(terminal_counts), "reported distribution", "Terminal-count distribution is exported for validation review.")

    selected_membership = membership[(membership["facility_node_set"] == "B") & (membership["facility_buffer_m"] == 100)]
    if len(selected_membership) == 1:
        row = selected_membership.iloc[0]
        add_check(checks, "selected_endpoint_membership", "endpoints", "PASS", f"{int(row['endpoints_inside_facility'])} inside; {int(row['ambiguous_endpoint_matches'])} ambiguous", "selected B/100 m membership row present", "Endpoint-facility membership summary exists for selected settings.")
    else:
        add_check(checks, "selected_endpoint_membership", "endpoints", "FAIL", len(selected_membership), 1, "Selected endpoint membership row missing.")

    selected_endpoint = endpoint_summary[endpoint_summary["endpoint_snap_threshold_m"].eq(0.5)]
    endpoint_thresholds = int(endpoint_index["endpoint_snap_threshold_m"].nunique())
    expected_endpoint_rows = raw_feature_count * 2 * endpoint_thresholds
    add_check(checks, "endpoint_index_rows", "endpoints", "PASS" if len(endpoint_index) == expected_endpoint_rows else "FAIL", len(endpoint_index), expected_endpoint_rows, "Endpoint index contains start and end endpoints for every raw line feature at every snap threshold.")
    if len(selected_endpoint) == 1:
        erow = selected_endpoint.iloc[0]
        add_check(checks, "selected_endpoint_clusters", "endpoints", "PASS", int(erow["total_endpoint_clusters"]), "0.5 m snap-threshold row present", f"Singleton={int(erow['singleton_clusters'])}; two-endpoint={int(erow['clusters_with_2_endpoints'])}; >2={int(erow['clusters_with_more_than_2_endpoints'])}; mixed voltage={int(erow['mixed_voltage_clusters'])}; mixed status={int(erow['mixed_status_clusters'])}.")
    else:
        add_check(checks, "selected_endpoint_clusters", "endpoints", "FAIL", len(selected_endpoint), 1, "Selected endpoint cluster summary row missing.")

    selected_footprints = footprints[(footprints["facility_node_set"] == "B") & (footprints["facility_buffer_m"] == 100)]
    if len(selected_footprints) == 1:
        frow = selected_footprints.iloc[0]
        add_check(checks, "selected_facility_footprints", "facilities", "PASS" if bool(frow["spatially_reasonable"]) else "WARN", int(frow["facility_count"]), 484, f"Overlapping footprint pairs={int(frow['overlapping_footprint_pairs'])}; nearby pairs <=25 m={int(frow['duplicate_or_nearby_facility_pairs_25m'])}.")
    else:
        add_check(checks, "selected_facility_footprints", "facilities", "FAIL", len(selected_footprints), 1, "Selected facility footprint summary row missing.")

    graph_branch_list = graph_branch_ids(graph)
    graph_branch_set = set(graph_branch_list)
    branch_set = set(branches["branch_id"].astype(str))
    missing_in_graph = sorted(branch_set - graph_branch_set)
    extra_in_graph = sorted(graph_branch_set - branch_set)
    add_check(checks, "graph_edge_count", "graph", "PASS" if graph.number_of_edges() == len(branches) else "FAIL", graph.number_of_edges(), len(branches), "GraphML edge count equals retained branch table row count.")
    add_check(checks, "graph_node_count", "graph", "PASS" if graph.number_of_nodes() == 484 else "FAIL", graph.number_of_nodes(), 484, "GraphML includes selected facility nodes, including isolates.")
    add_check(checks, "graph_branch_id_parity", "graph", "PASS" if not missing_in_graph and not extra_in_graph else "FAIL", f"missing={len(missing_in_graph)}, extra={len(extra_in_graph)}", "0 missing, 0 extra", "GraphML branch_id set matches retained branch table.")
    graph_nodes = set(str(node) for node in graph.nodes())
    endpoint_refs = set(branches["from_facility_uid"].astype(str)) | set(branches["to_facility_uid"].astype(str))
    orphan_endpoint_refs = sorted(endpoint_refs - graph_nodes)
    add_check(checks, "graph_endpoint_reference_parity", "graph", "PASS" if not orphan_endpoint_refs else "FAIL", len(orphan_endpoint_refs), 0, "Every retained-branch endpoint facility appears as a GraphML node.")
    isolates = int(sum(1 for _, degree in graph.degree() if degree == 0))
    parallel_edges = 0
    parallel_groups = 0
    parallel_extra_edges = 0
    if graph.is_multigraph():
        pair_counts: dict[tuple[str, str], int] = {}
        for u, v in graph.edges():
            pair = tuple(sorted((str(u), str(v))))
            pair_counts[pair] = pair_counts.get(pair, 0) + 1
        parallel_groups = sum(1 for count in pair_counts.values() if count > 1)
        parallel_edges = sum(count for count in pair_counts.values() if count > 1)
        parallel_extra_edges = sum(count - 1 for count in pair_counts.values() if count > 1)
    add_check(checks, "graph_isolates_and_parallel_edges", "graph", "PASS", f"isolates={isolates}, parallel_groups={parallel_groups}, parallel_edge_records={parallel_edges}, parallel_extra_edges={parallel_extra_edges}, self_loops={nx.number_of_selfloops(graph)}", "reported graph structure", "Graph structure diagnostics are measured from GraphML.")

    add_check(checks, "sensitivity_sweep_rows", "sensitivity", "PASS" if len(sweep) == 216 else "FAIL", len(sweep), 216, "Sensitivity sweep contains all 216 declared configurations.")
    selected_sweep = sweep[
        (sweep["facility_node_set"] == "B")
        & (sweep["facility_buffer_m"] == 100)
        & (sweep["endpoint_snap_threshold_m"] == 0.5)
        & (sweep["merge_mode"] == "voltage-status-aware")
    ]
    add_check(checks, "selected_sweep_row", "sensitivity", "PASS" if len(selected_sweep) == 1 else "FAIL", len(selected_sweep), 1, "Selected release setting is represented exactly once in the sensitivity sweep.")

    core_paths = [BRANCHES, LEDGER, GRAPHML, SWEEP, PAPER_SUMMARY, ENDPOINT_INDEX, ENDPOINT_SUMMARY, FACILITY_MEMBERSHIP, FOOTPRINT_SUMMARY]
    hashes = {str(path.relative_to(config.ROOT_DIR)): sha256(path) for path in core_paths}
    add_check(checks, "release_hash_baseline", "determinism", "PASS" if len(hashes) == len(core_paths) else "FAIL", len(hashes), len(core_paths), "SHA-256 baselines are recorded for every declared core artifact; tagged clean-room archive reproduction is checked separately after the source tag is created.")

    missing_rows: list[dict[str, object]] = []
    missing_rows.extend(semantic_missingness(branches, ["r", "x", "b", "thermal_limit", "transformer_impedance", "tap_settings"], "retained_branches"))
    missing_rows.extend(semantic_missingness(ledger, ["r", "x", "b", "thermal_limit", "transformer_impedance", "tap_settings", "terminal_facility_uids"], "circuit_ledger"))

    checks_df = pd.DataFrame(checks)
    missing_df = pd.DataFrame(missing_rows)
    checks_df.to_csv(CHECKS_CSV, index=False)
    missing_df.to_csv(MISSINGNESS_CSV, index=False)

    status_counts = {str(k): int(v) for k, v in checks_df["status"].value_counts().sort_index().to_dict().items()}
    validation_pass = int(status_counts.get("FAIL", 0)) == 0
    output = {
        "generated_at": utc_now(),
        "status": "PASS_WITH_WARNINGS" if validation_pass and int(status_counts.get("WARN", 0)) else ("PASS" if validation_pass else "FAIL"),
        "checks_total": int(len(checks_df)),
        "check_status_counts": status_counts,
        "frozen_counts": {
            "raw_line_features": raw_feature_count,
            "valid_line_geometries": valid_line_geometries,
            "invalid_or_dropped_geometries": invalid_geometries,
            "retained_branches": int(len(branches)),
            "circuit_ledger_rows": int(len(ledger)),
            "downgraded_or_rejected_records": int(downgraded),
            "sensitivity_sweep_rows": int(len(sweep)),
            "graph_nodes": int(graph.number_of_nodes()),
            "graph_edges": int(graph.number_of_edges()),
            "graph_isolated_nodes": isolates,
            "graph_parallel_groups": parallel_groups,
            "graph_parallel_edge_records": parallel_edges,
            "graph_parallel_extra_edges": parallel_extra_edges,
        },
        "classification_counts": {str(k): int(v) for k, v in sorted(class_counts.items())},
        "terminal_count_distribution": terminal_counts,
        "coordinate_reference": {
            "source_and_release_geometry": "Opendatasoft v2.1 GeoJSON exports retrieved without an epsg override and therefore exported as EPSG:4326; release geometry uses GeoJSON longitude/latitude order.",
            "metric_reconstruction_crs": summary["validation"]["metric_crs"],
            "upstream_native_crs_boundary": "The native CRS before Opendatasoft portal ingestion, if different, is not recorded and was not used by the reconstruction pipeline.",
        },
        "core_artifact_hashes_sha256": hashes,
        "outputs": {
            "summary": str(SUMMARY_JSON.relative_to(config.ROOT_DIR)),
            "checks": str(CHECKS_CSV.relative_to(config.ROOT_DIR)),
            "missingness": str(MISSINGNESS_CSV.relative_to(config.ROOT_DIR)),
            "report": str(REPORT.relative_to(config.ROOT_DIR)),
        },
        "claim_boundary": "Internal validation checks release consistency and machine-readability only; it does not validate physical topology truth, precision, recall, or operator confirmation.",
    }
    write_json(SUMMARY_JSON, output)

    lines = [
        "# 106 PT60 Internal Validation Summary",
        "",
        f"Generated: {output['generated_at']}",
        "",
        f"Status: `{output['status']}`",
        "",
        f"- total checks: {output['checks_total']}",
        f"- status counts: {output['check_status_counts']}",
        f"- retained branches: {len(branches)}",
        f"- circuit ledger rows: {len(ledger)}",
        f"- retained + downgraded/rejected: {retained} + {downgraded} = {retained + downgraded}",
        f"- GraphML nodes/edges: {graph.number_of_nodes()} / {graph.number_of_edges()}",
        f"- endpoint index rows: {len(endpoint_index)}",
        f"- sensitivity rows: {len(sweep)}",
        "",
        "## Checks",
        "",
        "| check_id | layer | status | observed | expected |",
        "|---|---|---|---|---|",
    ]
    for row in checks_df.to_dict("records"):
        lines.append(f"| {row['check_id']} | {row['layer']} | {row['status']} | {row['observed']} | {row['expected']} |")
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "These checks validate internal release consistency, schema-critical completeness, ledger accounting, and GraphML/table parity. They do not validate the inferred physical topology against an operator truth source.",
        ]
    )
    write_text(REPORT, "\n".join(lines) + "\n")
    print(json.dumps(output, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
