"""Build schema, field dictionary, CRS and join documentation for PT60-Candidate."""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import utc_now, write_json, write_text


VERSION = "v1.0.2"
RELEASE_NAME = f"PT60-Candidate-{VERSION}"
RELEASE_DIR = config.DATA_DIR / "releases" / RELEASE_NAME
SCHEMA_DIR = config.DATA_DIR / "schema" / RELEASE_NAME


def release_timestamp() -> str:
    metadata_path = config.ROOT_DIR / "release_metadata.json"
    if metadata_path.exists():
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        return str(metadata.get("generated_at_utc", "2026-07-14T13:20:19Z"))
    return "2026-07-14T13:20:19Z"

MISSING_TOKENS = {
    "",
    "nan",
    "null",
    "none",
    "na",
    "n/a",
    "missing",
    "missing_not_estimated",
    "not_estimated",
    "pending",
}

FILE_GROUP_RULES = [
    ("core_topology/", "core_topology"),
    ("validation/", "technical_validation"),
    ("provenance/", "provenance"),
]

CORE_SCHEMA_FILES = [
    "core_topology/at_interfacility_candidate_branches.csv",
    "core_topology/at_circuit_classification.csv",
    "core_topology/at_paper_logic_parameter_sweep.csv",
    "core_topology/at_paper_logic_graph.graphml",
    "provenance/reproduction_source_manifest.csv",
    "validation/pt_topology_cross_validation_osm_matches.csv",
    "validation/pt_topology_cross_validation_osm_matches_independence_audit.csv",
    "validation/internal_validation_checks.csv",
    "validation/internal_validation_missingness.csv",
]


def normalise_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    text = str(value).strip().lower()
    return text in MISSING_TOKENS


def file_group(rel: str) -> str:
    for prefix, group in FILE_GROUP_RULES:
        if rel.startswith(prefix):
            return group
    return "release_control"


def file_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return "csv"
    if suffix == ".json":
        return "json"
    if suffix == ".graphml":
        return "graphml"
    return suffix.lstrip(".") or "unknown"


def field_leaf(name: str) -> str:
    """Return a normalized leaf token from a column name or JSON pointer."""
    leaf = name.rsplit(".", 1)[-1].replace("[*]", "")
    return re.sub(r"[^a-z0-9_]+", "_", leaf.lower()).strip("_")


def boolean_like_name(name: str) -> bool:
    leaf = field_leaf(name)
    return (
        leaf.startswith(("is_", "has_"))
        or leaf.endswith(("_flag", "_boolean", "_bool"))
        or leaf in {"mixed_voltage", "mixed_status", "topology_critical"}
    )


def infer_logical_type(name: str, observed_type: str) -> str:
    n = name.lower()
    leaf = field_leaf(name)
    tokens = set(leaf.split("_"))
    count_like = leaf.endswith(("_count", "_counts", "_rows", "_features", "_nodes", "_circuits", "_clusters"))
    if count_like and observed_type in {"integer", "number"}:
        return observed_type
    if observed_type == "boolean" or boolean_like_name(name):
        return "boolean"
    if leaf.endswith("_json") or "details_json" in leaf:
        return "json_encoded_string"
    if "url" in tokens:
        return "url"
    if leaf.endswith("_id") or leaf in {"id", "osm_id"} or "uid" in tokens or "code" in tokens:
        return "identifier"
    if "sha256" in leaf or "checksum" in leaf:
        return "checksum"
    if "date" in tokens or "time" in tokens or leaf.endswith("_at"):
        return "datetime_or_date"
    if leaf in {"lat", "latitude", "north", "south"} or leaf.endswith(("_lat", "_latitude")):
        return "latitude_degrees"
    if leaf in {"lon", "lng", "longitude", "east", "west"} or leaf.endswith(("_lon", "_lng", "_longitude")):
        return "longitude_degrees"
    if leaf.endswith("_m") or "distance_m" in leaf or "length_m" in leaf:
        return "distance_m"
    if leaf.endswith("_km") or "length_km" in leaf:
        return "distance_km"
    if leaf in {"geometry", "geom", "original_geometry"} or leaf.endswith("_geojson"):
        return "geojson_geometry"
    if "voltage" in tokens or "kv" in tokens or "vn_kv" in leaf:
        return "voltage"
    if "score" in tokens or "coverage" in tokens or "rate" in tokens or "percentage" in tokens:
        return "ratio_or_score"
    return observed_type


def infer_unit(name: str, logical_type: str) -> str:
    n = name.lower()
    if logical_type == "latitude_degrees" or logical_type == "longitude_degrees":
        return "decimal_degrees"
    if logical_type == "distance_m":
        return "m"
    if logical_type == "distance_km":
        return "km"
    if logical_type == "voltage":
        return "kV or V as encoded by field; see field description"
    if "thermal_limit" in n or "current" in n:
        return "not_estimated_or_source_encoded"
    if logical_type == "ratio_or_score":
        return "unitless"
    if "bytes" in n:
        return "bytes"
    if "count" in n or n.endswith("_rows") or n.endswith("_features"):
        return "count"
    if logical_type == "geojson_geometry":
        return "WGS84 longitude/latitude coordinates unless otherwise stated"
    return "not_applicable"


def infer_key_role(name: str, rel: str) -> tuple[str, str]:
    n = name.lower()
    if n == "branch_id":
        return "primary_key" if "at_interfacility_candidate_branches" in rel else "foreign_key", "core_topology/at_interfacility_candidate_branches.csv:branch_id"
    if n == "circuit_id":
        return "primary_key" if "at_circuit_classification" in rel else "foreign_key", "core_topology/at_circuit_classification.csv:circuit_id"
    if n in {"from_facility_uid", "to_facility_uid", "facility_uid", "terminal_facility_uids"} or n.endswith("facility_uid"):
        return "facility_identifier", "core_topology/at_endpoint_facility_membership_summary.csv:facility_uid where available"
    if n in {"from_facility_code", "to_facility_code", "facility_code"} or n.endswith("facility_code"):
        return "facility_code", "E-REDES public facility code fields"
    if n in {"source_line_ids", "line_ids"}:
        return "source_lineage_key_list", "E-REDES source line identifiers"
    if n == "dataset_id":
        return "source_dataset_identifier", "provenance/reproduction_source_manifest.csv:dataset_id"
    if n == "osm_id":
        return "external_source_identifier", "OpenStreetMap way/relation/node identifier"
    if n in {"path", "source_path"}:
        return "archive_path_identifier", "manifest.json:records.path"
    return "none", ""


def infer_description(name: str, rel: str, logical_type: str) -> str:
    n = name.lower()
    descriptions = {
        "branch_id": "Stable PT60 retained candidate branch identifier.",
        "circuit_id": "Stable reconstructed circuit/group identifier from the fail-closed merge step.",
        "classification": "Fail-closed disposition class for a reconstructed circuit or branch.",
        "facility_node_set": "Selected facility-node universe used during reconstruction.",
        "facility_buffer_m": "Facility matching buffer radius used during reconstruction.",
        "endpoint_snap_threshold_m": "Endpoint clustering/snap threshold used during reconstruction.",
        "merge_mode": "Circuit-segment merge strategy.",
        "from_facility_uid": "Canonical source-side facility identifier assigned by the reconstruction pipeline.",
        "to_facility_uid": "Canonical target-side facility identifier assigned by the reconstruction pipeline.",
        "from_facility_code": "Public source code for the source-side facility where available.",
        "to_facility_code": "Public source code for the target-side facility where available.",
        "from_facility_name": "Public source name for the source-side facility where available.",
        "to_facility_name": "Public source name for the target-side facility where available.",
        "voltage": "Voltage class encoded in the public source or derived candidate record.",
        "status": "Operational/status text encoded in the public source record.",
        "total_length_km": "Candidate circuit or branch length in kilometres.",
        "total_length_m": "Candidate circuit or branch length in metres.",
        "geometry": "GeoJSON geometry encoded as text in release CSV files.",
        "source_line_ids": "Comma-separated public source line identifiers contributing to this record.",
        "confidence_score": "Internal candidate-construction confidence score; not a probability, precision estimate, or operator validation.",
        "external_evidence_status": "OSM/OpenInfraMap public-source concordance category.",
        "evidence_reason": "Human-readable rule explanation for an external-evidence category.",
        "osm_url": "OpenStreetMap object URL used for public-source triangulation.",
        "independence_category": "Risk category describing whether public-source evidence is more independent, unknown, or possibly same-source.",
        "check_id": "Internal validation check identifier.",
        "observed": "Observed value from a validation check.",
        "expected": "Expected value for a validation check.",
        "detail": "Additional validation detail.",
    }
    if n in descriptions:
        return descriptions[n]
    if logical_type == "identifier":
        return f"Identifier field `{name}` in `{rel}`."
    if logical_type == "url":
        return f"URL field `{name}` in `{rel}`."
    if logical_type == "geojson_geometry":
        return "Geometry field encoded as GeoJSON text; coordinates use longitude, latitude order unless explicitly documented otherwise."
    if logical_type in {"distance_m", "distance_km"}:
        return f"Distance or length measurement field `{name}`."
    if logical_type == "ratio_or_score":
        return f"Unitless score, ratio, coverage, percentage, or audit metric `{name}`; interpret according to the file-level method documentation."
    if "validation" in rel:
        return f"Technical-validation field `{name}`."
    if "provenance" in rel:
        return f"Source provenance or release-control field `{name}`."
    return f"Release field `{name}` from `{rel}`."


def infer_semantic_status(rel: str, name: str) -> str:
    n = name.lower()
    if any(token in n for token in ["r", "x", "b", "thermal_limit", "tap_settings", "impedance"]):
        if n in {"r", "x", "b", "thermal_limit", "tap_settings", "transformer_impedance"}:
            return "not_estimated_in_core_release"
    if rel.startswith("validation/"):
        return "validation_evidence_not_ground_truth"
    if rel.startswith("core_topology/"):
        return "core_candidate_topology"
    if rel.startswith("provenance/"):
        return "provenance"
    return "release_control"


def series_type(values: list[Any], field_name: str = "") -> str:
    non_missing = [v for v in values if not normalise_missing(v)]
    if not non_missing:
        return "string"
    if all(isinstance(v, bool) for v in non_missing):
        return "boolean"
    bool_like = {str(v).strip().lower() for v in non_missing}
    if bool_like <= {"true", "false"} or (
        bool_like <= {"true", "false", "0", "1"} and boolean_like_name(field_name)
    ):
        return "boolean"
    numeric = 0
    integer = 0
    for value in non_missing:
        try:
            f = float(value)
            numeric += 1
            if f.is_integer():
                integer += 1
        except Exception:
            pass
    if numeric == len(non_missing):
        return "integer" if integer == len(non_missing) else "number"
    return "string"


def examples(values: list[Any], limit: int = 3) -> str:
    out = []
    for value in values:
        if normalise_missing(value):
            continue
        text = str(value)
        if len(text) > 120:
            text = text[:117] + "..."
        if text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return json.dumps(out, ensure_ascii=False)


def allowed_values(values: list[Any], limit: int = 25) -> str:
    vals = [str(v) for v in values if not normalise_missing(v)]
    unique = sorted(set(vals))
    if 0 < len(unique) <= limit:
        return json.dumps(unique, ensure_ascii=False)
    return ""


def csv_records(path: Path, rel: str) -> list[dict[str, Any]]:
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    records = []
    for column in df.columns:
        values = df[column].tolist()
        null_count = sum(1 for v in values if normalise_missing(v))
        observed_type = series_type(values, column)
        logical_type = infer_logical_type(column, observed_type)
        key_role, join_target = infer_key_role(column, rel)
        records.append(
            {
                "dataset_version": VERSION,
                "relative_path": rel,
                "file_group": file_group(rel),
                "file_format": "csv",
                "field_name": column,
                "field_scope": "csv_column",
                "description": infer_description(column, rel, logical_type),
                "inferred_type": observed_type,
                "logical_type": logical_type,
                "unit": infer_unit(column, logical_type),
                "nullable": null_count > 0,
                "null_count": null_count,
                "null_rate": round(null_count / len(df), 6) if len(df) else 0,
                "missing_values_observed": json.dumps(sorted({str(v) for v in values if normalise_missing(v)})[:10], ensure_ascii=False),
                "example_values": examples(values),
                "allowed_values_observed": allowed_values(values),
                "key_role": key_role,
                "join_target": join_target,
                "semantic_status": infer_semantic_status(rel, column),
                "source_or_derivation": source_or_derivation(rel),
                "public_release_status": "public",
            }
        )
    return records


def source_or_derivation(rel: str) -> str:
    if rel.startswith("core_topology/"):
        return "Derived from public E-REDES source records by the frozen PT60 fail-closed reconstruction pipeline."
    if rel.startswith("validation/"):
        return "Derived validation output using frozen PT60 artifacts and public OSM/OpenInfraMap evidence where applicable."
    if rel.startswith("provenance/"):
        return "Generated release/source provenance metadata."
    return "Release-control or article-support artifact."


def flatten_json(value: Any, prefix: str, sink: dict[str, list[Any]], depth: int = 0) -> None:
    if depth > 8:
        sink[prefix or "$"].append("<max_depth>")
        return
    if isinstance(value, dict):
        if not value:
            sink[prefix or "$"].append("{}")
        for key, child in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else f"$.{key}"
            flatten_json(child, child_prefix, sink, depth + 1)
    elif isinstance(value, list):
        sink[prefix or "$"].append(f"<array len={len(value)}>")
        for child in value[:100]:
            flatten_json(child, f"{prefix}[*]" if prefix else "$[*]", sink, depth + 1)
    else:
        sink[prefix or "$"].append(value)


def json_records(path: Path, rel: str) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    sink: dict[str, list[Any]] = defaultdict(list)
    flatten_json(data, "$", sink)
    records = []
    total_items = max((len(v) for v in sink.values()), default=1)
    for pointer, values in sorted(sink.items()):
        field = pointer
        observed_type = series_type(values, pointer)
        logical_type = infer_logical_type(field, observed_type)
        key_role, join_target = infer_key_role(field.split(".")[-1].replace("[*]", ""), rel)
        null_count = sum(1 for v in values if normalise_missing(v))
        records.append(
            {
                "dataset_version": VERSION,
                "relative_path": rel,
                "file_group": file_group(rel),
                "file_format": "json",
                "field_name": field,
                "field_scope": "json_pointer",
                "description": infer_description(field, rel, logical_type),
                "inferred_type": observed_type,
                "logical_type": logical_type,
                "unit": infer_unit(field, logical_type),
                "nullable": null_count > 0,
                "null_count": null_count,
                "null_rate": round(null_count / total_items, 6) if total_items else 0,
                "missing_values_observed": json.dumps(sorted({str(v) for v in values if normalise_missing(v)})[:10], ensure_ascii=False),
                "example_values": examples(values),
                "allowed_values_observed": allowed_values(values),
                "key_role": key_role,
                "join_target": join_target,
                "semantic_status": infer_semantic_status(rel, field),
                "source_or_derivation": source_or_derivation(rel),
                "public_release_status": "public",
            }
        )
    return records


def graphml_records(path: Path, rel: str) -> list[dict[str, Any]]:
    tree = ET.parse(path)
    root = tree.getroot()
    ns = {"g": "http://graphml.graphdrawing.org/xmlns"}
    key_meta = {}
    for key in root.findall("g:key", ns):
        key_meta[key.attrib.get("id", "")] = {
            "name": key.attrib.get("attr.name", key.attrib.get("id", "")),
            "scope": key.attrib.get("for", "all"),
            "type": key.attrib.get("attr.type", "string"),
        }
    values_by_key: dict[str, list[Any]] = defaultdict(list)
    for data_el in root.findall(".//g:data", ns):
        values_by_key[data_el.attrib.get("key", "")].append(data_el.text or "")
    records = []
    for key_id, meta in sorted(key_meta.items(), key=lambda kv: (kv[1]["scope"], kv[1]["name"])):
        values = values_by_key.get(key_id, [])
        field = meta["name"]
        logical_type = infer_logical_type(field, meta["type"])
        key_role, join_target = infer_key_role(field, rel)
        null_count = sum(1 for v in values if normalise_missing(v))
        records.append(
            {
                "dataset_version": VERSION,
                "relative_path": rel,
                "file_group": file_group(rel),
                "file_format": "graphml",
                "field_name": field,
                "field_scope": f"graphml_{meta['scope']}_attribute",
                "description": infer_description(field, rel, logical_type),
                "inferred_type": meta["type"],
                "logical_type": logical_type,
                "unit": infer_unit(field, logical_type),
                "nullable": null_count > 0,
                "null_count": null_count,
                "null_rate": round(null_count / len(values), 6) if values else 0,
                "missing_values_observed": json.dumps(sorted({str(v) for v in values if normalise_missing(v)})[:10], ensure_ascii=False),
                "example_values": examples(values),
                "allowed_values_observed": allowed_values(values),
                "key_role": key_role,
                "join_target": join_target,
                "semantic_status": infer_semantic_status(rel, field),
                "source_or_derivation": source_or_derivation(rel),
                "public_release_status": "public",
            }
        )
    return records


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def csv_schema(rel: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    fields = []
    for record in records:
        fields.append(
            {
                "name": record["field_name"],
                "type": record["inferred_type"],
                "logical_type": record["logical_type"],
                "description": record["description"],
                "unit": record["unit"],
                "nullable": record["nullable"],
                "key_role": record["key_role"],
                "join_target": record["join_target"],
                "missing_values": json.loads(record["missing_values_observed"]),
            }
        )
    return {
        "$schema": "https://specs.frictionlessdata.io/schemas/table-schema.json",
        "name": re.sub(r"[^A-Za-z0-9_]+", "_", Path(rel).stem),
        "path": rel,
        "dataset_version": VERSION,
        "description": f"PT60-Candidate schema for {rel}.",
        "fields": fields,
        "missingValues": sorted(MISSING_TOKENS),
    }


def json_schema(rel: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"PT60-Candidate {rel}",
        "description": "Path-level JSON field inventory schema. Nested structures are documented in data_dictionary.csv using JSON-pointer-like paths.",
        "type": ["object", "array"],
        "x-dataset-version": VERSION,
        "x-release-path": rel,
        "x-field-pointers": [
            {
                "pointer": record["field_name"],
                "inferred_type": record["inferred_type"],
                "logical_type": record["logical_type"],
                "description": record["description"],
                "nullable": record["nullable"],
            }
            for record in records
        ],
    }


def graphml_schema(rel: str, records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"PT60-Candidate GraphML attributes for {rel}",
        "description": "GraphML node/edge/all attribute dictionary extracted from the release GraphML file.",
        "type": "object",
        "x-dataset-version": VERSION,
        "x-release-path": rel,
        "x-graphml-attributes": [
            {
                "name": record["field_name"],
                "scope": record["field_scope"],
                "type": record["inferred_type"],
                "logical_type": record["logical_type"],
                "description": record["description"],
                "nullable": record["nullable"],
                "key_role": record["key_role"],
                "join_target": record["join_target"],
            }
            for record in records
        ],
    }


def write_crs_docs() -> None:
    crs = {
        "dataset_version": VERSION,
        "generated_at": release_timestamp(),
        "source_crs": {
            "name": "WGS 84 longitude/latitude from Opendatasoft v2.1 geometry exports",
            "epsg": "EPSG:4326",
            "axis_order_in_release_geometry": "longitude, latitude",
            "coordinate_units": "decimal degrees",
            "evidence_status": "Confirmed for the portal export used by the pipeline: the frozen Opendatasoft v2.1 export URLs contain no epsg override, and the export API default is EPSG:4326. The native CRS before portal ingestion, if different, is not recorded and was not used by the pipeline.",
            "evidence_url": "https://help.opendatasoft.com/apis/ods-explore-v2/",
            "export_parameter": "epsg",
            "export_parameter_default": 4326,
        },
        "processing_metric_crs": {
            "name": "ETRS89 / Portugal TM06",
            "epsg": "EPSG:3763",
            "projection_method": "Transverse Mercator",
            "units": "m",
            "usage": "Endpoint clustering, facility buffering, facility-distance checks, corridor-distance checks and length/coverage diagnostics.",
        },
        "release_geometry_encoding": {
            "csv_geometry_fields": "GeoJSON text in CSV cells",
            "geometry_types_observed": ["LineString", "MultiLineString", "Point-like facility summaries where applicable"],
            "distance_fields": "Fields ending in _m use metres; fields ending in _km use kilometres.",
            "missing_geometry": "Empty string, null, NaN, or MISSING_NOT_ESTIMATED depending on source artifact.",
        },
        "claim_boundary": "Coordinate and distance fields support reproducible candidate-topology reconstruction and validation. They do not imply operator-validated asset positions or operational grid-model readiness.",
    }
    write_json(SCHEMA_DIR / "crs_and_geometry.json", crs)
    text = f"""# CRS, Geometry Encoding, Units and Missing-Value Semantics

Dataset version: PT60-Candidate {VERSION}

## Coordinate reference systems

- Release geometry fields are encoded as GeoJSON-style longitude/latitude coordinate arrays in WGS 84 / EPSG:4326 decimal degrees. The frozen Opendatasoft v2.1 export URLs contain no `epsg` override, and the documented default for geometry-capable exports is EPSG:4326.
- Axis order in CSV geometry cells is longitude, latitude.
- The v1.0.2 reconstruction and validation pipeline uses ETRS89 / Portugal TM06 (EPSG:3763; Transverse Mercator, metres) for endpoint clustering, facility buffering, facility-distance checks, corridor-distance checks and length/coverage diagnostics.
- The native CRS before Opendatasoft portal ingestion, if different, is not recorded. It was not used by the reconstruction pipeline; the relevant input CRS is the EPSG:4326 portal export.

## Units

- Fields ending in `_m` are metres.
- Fields ending in `_km` are kilometres.
- Coverage, score, confidence, rate and percentage fields are unitless.
- Voltage fields preserve the file-specific source encoding. Core topology uses strings such as `60kv`; OSM-derived fields may use numeric strings such as `60000`.
- Electrical parameter fields (`r`, `x`, `b`, `thermal_limit`, `transformer_impedance`, `tap_settings`) are not estimated in the core candidate-topology release when encoded as `MISSING_NOT_ESTIMATED`.

## Missing values

The data dictionary records observed missing tokens per field. Common missing encodings are empty string, JSON null, `NaN`, `MISSING_NOT_ESTIMATED`, `pending` and absent optional JSON keys.

## Claim boundary

These CRS and unit statements support reproducible candidate-topology reconstruction, provenance tracking and public-source validation. They do not convert PT60-Candidate into an operator-validated or AC-power-flow-ready grid model.
"""
    write_text(SCHEMA_DIR / "crs_and_geometry.md", text)


def write_join_docs() -> None:
    joins = [
        {
            "left_path": "core_topology/at_interfacility_candidate_branches.csv",
            "left_field": "branch_id",
            "right_path": "validation/pt_topology_cross_validation_osm_matches.csv",
            "right_field": "branch_id",
            "relationship": "one_to_one_or_one_to_many_best_match_context",
            "required": True,
            "notes": "Use for branch-level public-source concordance categories.",
        },
        {
            "left_path": "core_topology/at_interfacility_candidate_branches.csv",
            "left_field": "circuit_id",
            "right_path": "core_topology/at_circuit_classification.csv",
            "right_field": "circuit_id",
            "relationship": "many_retained_branches_to_full_ledger_record",
            "required": True,
            "notes": "Use to recover fail-closed disposition and source segment lineage.",
        },
        {
            "left_path": "core_topology/at_interfacility_candidate_branches.csv",
            "left_field": "branch_id",
            "right_path": "validation/pt_topology_cross_validation_osm_matches_independence_audit.csv",
            "right_field": "branch_id",
            "relationship": "one_to_one",
            "required": True,
            "notes": "Use for OSM/OpenInfraMap evidence independence-risk category.",
        },
        {
            "left_path": "core_topology/at_interfacility_candidate_branches.csv",
            "left_field": "from_facility_uid/to_facility_uid",
            "right_path": "core_topology/at_endpoint_facility_membership_summary.csv",
            "right_field": "facility_uid",
            "relationship": "many_to_one_where_facility_uid_is_present",
            "required": False,
            "notes": "Facility UID is the preferred internal join key; source facility code/name are descriptive public identifiers.",
        },
        {
            "left_path": "provenance/reproduction_source_manifest.csv",
            "left_field": "dataset_id",
            "right_path": "provenance/reproduction_source_manifest.json",
            "right_field": "$.sources[*].dataset_id or equivalent source list key",
            "relationship": "same_source_manifest_two_formats",
            "required": True,
            "notes": "CSV and JSON are parallel source-manifest representations.",
        },
        {
            "left_path": "manifest.json",
            "left_field": "$.records[*].path",
            "right_path": "checksums.sha256",
            "right_field": "relative path after digest",
            "relationship": "one_to_one_except_checksums_self_reference",
            "required": True,
            "notes": "Use for archive-level integrity checks; checksums.sha256 omits its own digest.",
        },
    ]
    write_csv(SCHEMA_DIR / "join_relationships.csv", joins)


def write_readme(records: list[dict[str, Any]]) -> None:
    file_count = len({r["relative_path"] for r in records})
    field_count = len(records)
    group_counts = Counter(r["file_group"] for r in records)
    text = f"""# PT60-Candidate {VERSION} Schema Package

Generated: {release_timestamp()}

This package documents public CSV, JSON and GraphML fields in the PT60-Candidate {VERSION} release archive.

## Contents

- `data_dictionary.csv`: field-level dictionary covering {field_count} fields/JSON pointers/GraphML attributes across {file_count} public machine-readable files.
- `file_schema_summary.csv`: file-level counts and schema coverage.
- `join_relationships.csv`: recommended joins among core topology, validation, provenance and archive-control files.
- `crs_and_geometry.md` and `crs_and_geometry.json`: CRS, geometry encoding, units and missing-value semantics.
- `json_schemas/`: machine-readable schemas for principal core, validation and provenance files.

## Coverage by file group

{chr(10).join(f'- `{group}`: {count} fields' for group, count in sorted(group_counts.items()))}

## Interpretation limits

The schema package documents a candidate-topology dataset. It does not assert operator validation, branch precision/recall, operational grid-model readiness, AC power-flow readiness or OPF readiness.
"""
    write_text(SCHEMA_DIR / "README.md", text)


def append_schema_self_documentation(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Document generated schema-package CSV/JSON files as public release files.

    This avoids a common archive-review gap where the data dictionary covers the
    dataset but not the schema package that is shipped inside the same archive.
    """

    augmented = list(records)
    csv_columns = {
        "schema/data_dictionary.csv": [
            "dataset_version",
            "relative_path",
            "file_group",
            "file_format",
            "field_name",
            "field_scope",
            "description",
            "inferred_type",
            "logical_type",
            "unit",
            "nullable",
            "null_count",
            "null_rate",
            "missing_values_observed",
            "example_values",
            "allowed_values_observed",
            "key_role",
            "join_target",
            "semantic_status",
            "source_or_derivation",
            "public_release_status",
        ],
        "schema/file_schema_summary.csv": [
            "dataset_version",
            "relative_path",
            "file_group",
            "file_format",
            "field_count",
            "bytes",
            "schema_generated",
            "principal_core_schema",
            "source_or_derivation",
        ],
        "schema/join_relationships.csv": [
            "left_path",
            "left_field",
            "right_path",
            "right_field",
            "relationship",
            "required",
            "notes",
        ],
    }
    for rel, columns in csv_columns.items():
        for column in columns:
            logical_type = infer_logical_type(column, "string")
            key_role, join_target = infer_key_role(column, rel)
            augmented.append(
                {
                    "dataset_version": VERSION,
                    "relative_path": rel,
                    "file_group": "schema",
                    "file_format": "csv",
                    "field_name": column,
                    "field_scope": "csv_column",
                    "description": f"Schema-package metadata field `{column}` in `{rel}`.",
                    "inferred_type": "string",
                    "logical_type": logical_type,
                    "unit": infer_unit(column, logical_type),
                    "nullable": False,
                    "null_count": 0,
                    "null_rate": 0,
                    "missing_values_observed": "[]",
                    "example_values": "[]",
                    "allowed_values_observed": "",
                    "key_role": key_role,
                    "join_target": join_target,
                    "semantic_status": "schema_documentation",
                    "source_or_derivation": "Generated schema package documentation.",
                    "public_release_status": "public",
                }
            )
    json_files = [
        "schema/crs_and_geometry.json",
        "schema/schema_build_summary.json",
    ]
    json_files.extend(
        f"schema/json_schemas/{path.name}"
        for path in sorted((SCHEMA_DIR / "json_schemas").glob("*.schema.json"))
    )
    for rel in json_files:
        for pointer in ["$", "$.$schema", "$.title", "$.description", "$.x-dataset-version", "$.x-release-path"]:
            augmented.append(
                {
                    "dataset_version": VERSION,
                    "relative_path": rel,
                    "file_group": "schema",
                    "file_format": "json",
                    "field_name": pointer,
                    "field_scope": "json_pointer",
                    "description": f"Schema-package JSON pointer `{pointer}` in `{rel}`.",
                    "inferred_type": "string",
                    "logical_type": "schema_metadata",
                    "unit": "not_applicable",
                    "nullable": True,
                    "null_count": 0,
                    "null_rate": 0,
                    "missing_values_observed": "[]",
                    "example_values": "[]",
                    "allowed_values_observed": "",
                    "key_role": "none",
                    "join_target": "",
                    "semantic_status": "schema_documentation",
                    "source_or_derivation": "Generated schema package documentation.",
                    "public_release_status": "public",
                }
            )
    return augmented


def semantic_inference_issues(records: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Fail closed on implausible name-derived logical types."""
    issues = []
    for record in records:
        name = str(record["field_name"])
        leaf = field_leaf(name)
        logical = str(record["logical_type"])
        if logical == "latitude_degrees" and not (
            leaf in {"lat", "latitude", "north", "south"} or leaf.endswith(("_lat", "_latitude"))
        ):
            issues.append({"field": name, "logical_type": logical, "reason": "non_coordinate_name"})
        if logical == "longitude_degrees" and not (
            leaf in {"lon", "lng", "longitude", "east", "west"} or leaf.endswith(("_lon", "_lng", "_longitude"))
        ):
            issues.append({"field": name, "logical_type": logical, "reason": "non_coordinate_name"})
        if logical == "geojson_geometry" and not (
            leaf in {"geometry", "geom", "original_geometry"} or leaf.endswith("_geojson")
        ):
            issues.append({"field": name, "logical_type": logical, "reason": "non_geometry_name"})
        count_like = leaf.endswith(("_count", "_rows", "_features")) or leaf in {"count", "rows", "features"}
        if logical == "boolean" and count_like:
            issues.append({"field": name, "logical_type": logical, "reason": "count_as_boolean"})
    return issues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true", help="Replace existing schema directory.")
    args = parser.parse_args()

    if not RELEASE_DIR.exists():
        raise FileNotFoundError(f"Release directory missing: {RELEASE_DIR}")
    if SCHEMA_DIR.exists():
        if not args.overwrite:
            raise RuntimeError(f"Schema directory already exists; rerun with --overwrite: {SCHEMA_DIR}")
        shutil.rmtree(SCHEMA_DIR)
    (SCHEMA_DIR / "json_schemas").mkdir(parents=True, exist_ok=True)

    all_records: list[dict[str, Any]] = []
    records_by_file: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(RELEASE_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(RELEASE_DIR))
        if rel.startswith("schema/"):
            continue
        suffix = path.suffix.lower()
        if suffix not in {".csv", ".json", ".graphml"}:
            continue
        if suffix == ".csv":
            records = csv_records(path, rel)
        elif suffix == ".json":
            records = json_records(path, rel)
        else:
            records = graphml_records(path, rel)
        records_by_file[rel] = records
        all_records.extend(records)

    summary_rows = []
    for rel, records in sorted(records_by_file.items()):
        path = RELEASE_DIR / rel
        summary_rows.append(
            {
                "dataset_version": VERSION,
                "relative_path": rel,
                "file_group": file_group(rel),
                "file_format": file_format(path),
                "field_count": len(records),
                "bytes": path.stat().st_size,
                "schema_generated": True,
                "principal_core_schema": rel in CORE_SCHEMA_FILES,
                "source_or_derivation": source_or_derivation(rel),
            }
        )
    for rel, records in records_by_file.items():
        if rel not in CORE_SCHEMA_FILES and not rel.startswith("provenance/") and rel not in {"manifest.json", "archive_validation_summary.json"}:
            continue
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", rel).replace("/", "__")
        path = RELEASE_DIR / rel
        if path.suffix.lower() == ".csv":
            schema = csv_schema(rel, records)
        elif path.suffix.lower() == ".json":
            schema = json_schema(rel, records)
        else:
            schema = graphml_schema(rel, records)
        write_json(SCHEMA_DIR / "json_schemas" / f"{safe}.schema.json", schema)

    write_join_docs()
    write_crs_docs()
    all_records = append_schema_self_documentation(all_records)
    schema_file_summary_rows = [
        {
            "dataset_version": VERSION,
            "relative_path": "schema/data_dictionary.csv",
            "file_group": "schema",
            "file_format": "csv",
            "field_count": 21,
            "bytes": 0,
            "schema_generated": True,
            "principal_core_schema": False,
            "source_or_derivation": "Generated schema package documentation.",
        },
        {
            "dataset_version": VERSION,
            "relative_path": "schema/file_schema_summary.csv",
            "file_group": "schema",
            "file_format": "csv",
            "field_count": 9,
            "bytes": 0,
            "schema_generated": True,
            "principal_core_schema": False,
            "source_or_derivation": "Generated schema package documentation.",
        },
        {
            "dataset_version": VERSION,
            "relative_path": "schema/join_relationships.csv",
            "file_group": "schema",
            "file_format": "csv",
            "field_count": 7,
            "bytes": 0,
            "schema_generated": True,
            "principal_core_schema": False,
            "source_or_derivation": "Generated schema package documentation.",
        },
    ]
    summary_rows.extend(schema_file_summary_rows)
    write_csv(SCHEMA_DIR / "data_dictionary.csv", all_records)
    write_csv(SCHEMA_DIR / "file_schema_summary.csv", summary_rows)
    write_readme(all_records)
    semantic_issues = semantic_inference_issues(all_records)
    summary = {
        "generated_at": release_timestamp(),
        "dataset_version": VERSION,
        "schema_dir": str(SCHEMA_DIR.relative_to(config.ROOT_DIR)),
        "release_dir": str(RELEASE_DIR.relative_to(config.ROOT_DIR)),
        "machine_readable_dataset_files_documented_excluding_schema_package": len(records_by_file),
        "total_release_machine_readable_paths_documented": len({record["relative_path"] for record in all_records}),
        "field_records": len(all_records),
        "principal_schema_files": len(list((SCHEMA_DIR / "json_schemas").glob("*.schema.json"))),
        "semantic_inference_issue_count": len(semantic_issues),
        "semantic_inference_issues": semantic_issues,
        "status": "PASS" if records_by_file and all_records and not semantic_issues else "FAIL",
    }
    write_json(SCHEMA_DIR / "schema_build_summary.json", summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary["status"] != "PASS":
        raise RuntimeError("Schema build failed semantic inference validation")


if __name__ == "__main__":
    main()
