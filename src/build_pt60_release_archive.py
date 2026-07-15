"""Build the PT60-Candidate v1.0.2 public release directory and tarball."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import mimetypes
import shutil
import subprocess
import tarfile
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import utc_now, write_json, write_text


VERSION = "v1.0.2"
RELEASE_NAME = f"PT60-Candidate-{VERSION}"
RELEASES_DIR = config.DATA_DIR / "releases"
RELEASE_DIR = RELEASES_DIR / RELEASE_NAME
ARCHIVE_PATH = RELEASES_DIR / f"{RELEASE_NAME}.tar.gz"
SCHEMA_VERSION = "pt60_candidate_schema_v1.0.2"
DATASET_DOI = "10.6084/m9.figshare.32984021"
DATASET_DOI_URL = f"https://doi.org/{DATASET_DOI}"


def root_release_metadata() -> dict[str, Any]:
    path = config.ROOT_DIR / "release_metadata.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def release_timestamp() -> str:
    """Return the frozen release timestamp used in generated package metadata."""
    return str(root_release_metadata().get("generated_at_utc", "2026-07-14T13:20:19Z"))


def generator_commit() -> str:
    """Resolve the generating source commit, including in a detached tag worktree."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=config.ROOT_DIR, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return str(root_release_metadata().get("code_state", {}).get("generator_commit", "pending"))


def source_ids_for_path(rel: str) -> list[str]:
    topology_sources = ["rede-at-teste", "se-at_2025", "pc-at_2025"]
    all_sources = [
        str(row.get("dataset_id"))
        for row in root_release_metadata().get("source_snapshot", {}).get("sources", [])
        if row.get("dataset_id")
    ]
    if rel.startswith("core_topology/"):
        return topology_sources
    if rel.startswith("validation/") and ("osm" in rel.lower() or "openinframap" in rel.lower()):
        return topology_sources + ["openstreetmap", "openinframap"]
    if rel.startswith("validation/"):
        return topology_sources
    if rel.startswith("provenance/"):
        return all_sources or topology_sources
    if rel.startswith("schema/") or rel.startswith("inventory/"):
        return all_sources or topology_sources
    return []


def license_class_for_path(rel: str) -> str:
    if rel == "LICENSE-CODE-MIT":
        return "MIT_CODE_LICENSE"
    if rel in {"DATA_LICENSE.md", "ATTRIBUTION.md"}:
        return "LICENSE_AND_ATTRIBUTION_DOCUMENTATION"
    if rel.startswith("validation/") and ("osm" in rel.lower() or "openinframap" in rel.lower()):
        return "E_REDES_CC_BY_4_0_DERIVED_PLUS_OSM_OPENINFRAMAP_PUBLIC_EVIDENCE_ATTRIBUTION_REQUIRED"
    if rel.startswith("core_topology/"):
        return "E_REDES_CC_BY_4_0_DERIVED_WITH_ATTRIBUTION_AND_MODIFICATION_NOTICE"
    if rel.startswith("provenance/"):
        return "SOURCE_PROVENANCE_METADATA_WITH_E_REDES_ATTRIBUTION_CONTEXT"
    if rel.startswith("schema/") or rel.startswith("inventory/"):
        return "PROJECT_GENERATED_RELEASE_METADATA"
    return "PROJECT_RELEASE_CONTROL_METADATA"


def access_class_for_path(rel: str) -> str:
    if rel.startswith("core_topology/"):
        return "public_core_candidate_dataset"
    if rel.startswith("validation/"):
        return "public_validation_evidence"
    if rel.startswith("schema/") or rel.startswith("inventory/"):
        return "public_release_documentation"
    return "public_release_support"


def schema_version_for_path(rel: str) -> str:
    if rel.startswith("schema/"):
        return SCHEMA_VERSION
    if rel.endswith((".csv", ".json", ".graphml")):
        return SCHEMA_VERSION
    return "not_applicable"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_file(src: Path, dst_rel: str, copied: list[dict[str, Any]], purpose: str, role: str) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Required release input missing: {src}")
    dst = RELEASE_DIR / dst_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    copied.append(
        {
            "path": dst_rel,
            "source_path": str(src.relative_to(config.ROOT_DIR)),
            "purpose": purpose,
            "semantic_role": role,
        }
    )


def copy_csv_selected_columns(
    src: Path,
    dst_rel: str,
    copied: list[dict[str, Any]],
    columns: list[str],
    purpose: str,
    role: str,
) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Required release input missing: {src}")
    df = pd.read_csv(src)
    keep = [column for column in columns if column in df.columns]
    dst = RELEASE_DIR / dst_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    df[keep].to_csv(dst, index=False)
    copied.append(
        {
            "path": dst_rel,
            "source_path": str(src.relative_to(config.ROOT_DIR)),
            "purpose": purpose,
            "semantic_role": role,
        }
    )


def copy_tree(src: Path, dst_rel: str, copied: list[dict[str, Any]], purpose: str, role: str) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Required release input directory missing: {src}")
    for path in sorted(src.rglob("*")):
        if path.is_file():
            rel = Path(dst_rel) / path.relative_to(src)
            copy_file(path, str(rel), copied, purpose, role)


def copy_json_transformed(
    src: Path,
    dst_rel: str,
    copied: list[dict[str, Any]],
    transform: Any,
    purpose: str,
    role: str,
) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Required release input missing: {src}")
    data = json.loads(src.read_text(encoding="utf-8"))
    data = transform(data)
    dst = RELEASE_DIR / dst_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    write_json(dst, data)
    copied.append(
        {
            "path": dst_rel,
            "source_path": str(src.relative_to(config.ROOT_DIR)),
            "purpose": purpose,
            "semantic_role": role,
        }
    )


def public_osm_independence_summary(data: dict[str, Any]) -> dict[str, Any]:
    data = dict(data)
    outputs = dict(data.get("outputs", {}))
    data["outputs"] = {
        "branch_audit": "validation/pt_topology_cross_validation_osm_matches_independence_audit.csv",
        "summary": "validation/pt_osm_openinframap_independence_audit_summary.json",
        "excluded_from_main_public_archive": [
            "raw OSM/OpenInfraMap cache blobs",
            "OSM matched-way history dump",
            "OSM element-level audit with user identifiers or changesets",
        ],
    }
    data["public_release_note"] = (
        "The main public archive includes only summary counts and a sanitized branch-level "
        "independence audit. Raw OSM/OpenInfraMap cache blobs, matched-way history dumps, "
        "user identifiers and changeset-level records are excluded."
    )
    if "osm_element_audit" in outputs:
        data["internal_generation_note"] = "Element-level and history-cache outputs were used internally and excluded from the main public archive."
    return data


def scrub_release_text_files() -> None:
    """Remove development-machine paths from public release text files."""
    replacements = {
        str(config.ROOT_DIR): "<PROJECT_ROOT>",
        "/private/tmp": "<TEMP_DIR>",
        "/tmp": "<TEMP_DIR>",
        "/mnt/data": "<LOCAL_REFERENCE_PATH>",
        "clean-room rerun equality remains P0.12": "full source-to-archive clean-room rerun remains pending",
        "P0.12 clean-room reproduction": "clean-room reproduction",
        "P0.12": "clean-room reproduction",
        "manuscript": "article",
        "Manuscript": "Article",
    }
    text_suffixes = {
        ".csv",
        ".json",
        ".md",
        ".txt",
        ".cff",
        ".tex",
        ".bib",
        ".sha256",
        ".graphml",
        ".svg",
    }
    for path in sorted(RELEASE_DIR.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        original = text
        for old, new in replacements.items():
            text = text.replace(old, new)
        if text != original:
            write_text(path, text)


def count_rows(path: Path) -> int | None:
    suffix = path.suffix.lower()
    try:
        if suffix == ".csv":
            with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                return max(sum(1 for _ in handle) - 1, 0)
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return len(data)
            if isinstance(data, dict):
                if isinstance(data.get("features"), list):
                    return len(data["features"])
                if isinstance(data.get("elements"), list):
                    return len(data["elements"])
        if suffix in {".geojson"}:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data.get("features"), list):
                return len(data["features"])
    except Exception:
        return None
    return None


def manifest_records(copied: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    copied_by_path = {item["path"]: item for item in copied}
    for path in sorted(RELEASE_DIR.rglob("*")):
        if not path.is_file():
            continue
        rel = str(path.relative_to(RELEASE_DIR))
        source = copied_by_path.get(rel, {})
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        records.append(
            {
                "path": rel,
                "purpose": source.get("purpose", "release metadata"),
                "semantic_role": source.get("semantic_role", "release_control"),
                "source_path": source.get("source_path", ""),
                "media_type": media_type,
                "bytes": path.stat().st_size,
                "sha256": "" if rel in {"manifest.json", "checksums.sha256"} else sha256(path),
                "row_or_item_count": count_rows(path),
                "dataset_version": VERSION,
                "schema_version": schema_version_for_path(rel),
                "generator_commit": generator_commit(),
                "upstream_source_ids": source_ids_for_path(rel),
                "license_class": license_class_for_path(rel),
                "public_release_status": "public",
                "artifact_access_class": access_class_for_path(rel),
            }
        )
    return records


def write_release_readme() -> None:
    text = f"""# PT60-Candidate {VERSION}

PT60-Candidate is a provenance-tracked candidate dataset and fail-closed pipeline output for Portuguese 60 kV topology reconstruction from public E-REDES Open Data.

This archive is a candidate-topology dataset, not an operator-validated or operational grid model.

## Main contents

- `core_topology/`: retained candidate branches, full circuit ledger, GraphML export, sensitivity sweep and reconstruction summaries.
- `provenance/`: source manifest, licensing and responsible-release boundary.
- `validation/`: public-source triangulation, negative controls, OSM/OpenInfraMap independence audit and internal validation outputs.

## Claim boundary

Use this archive for reconstruction research, geospatial data integration, provenance-aware dataset engineering, graph/tabular interface testing and sensitivity analysis.

Do not use it for operational switching, protection studies, security analysis, contingency analysis, congestion analysis, infrastructure targeting, emergency operations, asset-condition assessment or regulatory/commercial capacity claims.

## Licensing

Repository code is MIT licensed. E-REDES-derived data use the E-REDES Open Data Portal terms recorded in `DATA_LICENSE.md`, `ATTRIBUTION.md` and `provenance/reproduction_source_manifest.json`. Reuse must retain E-REDES attribution, link CC BY 4.0, identify source datasets/access dates and indicate transformations.

## Repository and status

Reserved dataset DOI: `{DATASET_DOI}`.

Dataset DOI URL: `{DATASET_DOI_URL}`.

Code DOI remains pending; the public source repository is recorded in the associated manuscript. See `manifest.json`, `checksums.sha256`, `schema/`, `inventory/headline_counts.json` and `excluded_artifacts.json` for archive contents, schemas, checksums and exclusions.
"""
    write_text(RELEASE_DIR / "README.md", text)


def write_attribution() -> None:
    text = """# Attribution

PT60-Candidate is derived from public E-REDES Open Data Portal records.

Required source attribution:

E-REDES - Distribuicao de Eletricidade, "E-REDES Open Data Portal". Accessed via the source dataset URLs and checked-at timestamps recorded in `provenance/reproduction_source_manifest.json`.

License reference:

- E-REDES Open Data Portal: https://e-redes.opendatasoft.com/pages/homepage/
- CC BY 4.0: https://creativecommons.org/licenses/by/4.0/

Modification notice:

PT60-Candidate records are transformed and derived products. The archive reconstructs candidate topology records, circuit classifications, graph exports, validation summaries and public-source concordance audits from the source open-data records. The archive is not an official E-REDES product and is not operator validated.
"""
    write_text(RELEASE_DIR / "ATTRIBUTION.md", text)


def write_changelog() -> None:
    text = f"""# Changelog

## {VERSION} - 2026-07-15

- Uses ETRS89 / Portugal TM06 (EPSG:3763) for facility buffering, endpoint clustering, matching and metric distances.
- Includes 358 retained candidate branches and a 1,341-row retained/downgraded/rejected circuit ledger.
- Records the v1.0.1-to-v1.0.2 projection transition: 357 retained source-line groups are unchanged, one is removed and one is added.
- Includes 216-configuration sensitivity sweep.
- Includes OSM/OpenInfraMap public-source triangulation, endpoint-name negative control, spatial-alignment negative control, independence audit and internal validation outputs.
- Excludes raw E-REDES downloads from the default public archive.
- Excludes ACPF/DCOPF operational diagnostics from the core archive.
"""
    write_text(RELEASE_DIR / "CHANGELOG.md", text)


def write_exclusions() -> None:
    exclusions = {
        "generated_at": release_timestamp(),
        "dataset_version": VERSION,
        "excluded_or_non_core": [
            {
                "artifact_class": "raw_e_redes_downloads",
                "paths": ["data/raw/*"],
                "decision": "excluded_from_default_public_archive",
                "reason": "Source IDs, URLs and checked-at timestamps are provided instead. Raw snapshot deposit requires final repository/license review.",
            },
            {
                "artifact_class": "third_party_parameter_documents_and_catalogs",
                "paths": ["manufacturer catalogs", "standards excerpts", "non-open PDFs"],
                "decision": "excluded_public",
                "reason": "Redistribution permission is not documented.",
            },
            {
                "artifact_class": "acpf_dcopf_operational_diagnostics",
                "paths": ["data/processed/acpf_*", "data/processed/dcopf_*"],
                "decision": "excluded_from_core_archive",
                "reason": "Electrical parameters and operating conditions remain incomplete or diagnostic.",
            },
            {
                "artifact_class": "manual_dual_review_protocol_outputs",
                "paths": ["data/processed/topology_validation/pt_topology_validation_*"],
                "decision": "excluded_from_public_validation_route",
                "reason": "The v1.0.2 article route does not use dual independent human adjudication or report precision.",
            },
            {
                "artifact_class": "raw_osm_openinframap_cache_and_user_history",
                "paths": [
                    "raw OSM/OpenInfraMap cache blobs",
                    "OSM matched-way history dump",
                    "OSM element-level audit containing user identifiers or changesets",
                ],
                "decision": "excluded_from_main_public_archive",
                "reason": "The main public archive keeps table-level evidence and sanitized branch-level independence categories, but excludes raw public-source cache blobs and OSM user/history dumps.",
            },
            {
                "artifact_class": "optional_interfaces_and_diagnostics",
                "paths": ["optional_interfaces/**", "optional_diagnostic/**"],
                "decision": "excluded_from_main_public_archive",
                "reason": "Optional consumer interfaces and non-operational diagnostics require separate labeling and should be deposited as a separate supplementary archive if needed.",
            },
        ],
    }
    write_json(RELEASE_DIR / "excluded_artifacts.json", exclusions)


def write_citation() -> None:
    text = f"""cff-version: 1.2.0
message: "If you use PT60-Candidate, cite the dataset archive and associated paper."
title: "PT60-Candidate: A Provenance-Tracked Candidate Dataset for Portuguese 60 kV Topology Reconstruction"
type: dataset
authors:
  - name: "PortugueseOPD contributors"
version: "{VERSION}"
date-released: 2026-07-14
license: "CC-BY-4.0"
repository-code: ""
doi: "{DATASET_DOI}"
url: "{DATASET_DOI_URL}"
abstract: >-
  A provenance-tracked candidate dataset and fail-closed reconstruction pipeline output for Portuguese 60 kV topology reconstruction from public E-REDES Open Data. The dataset is not operator validated and is not an operational grid model.
"""
    write_text(RELEASE_DIR / "CITATION.cff", text)


def write_headline_counts() -> None:
    metadata = root_release_metadata()
    frozen = metadata.get("frozen_counts", {})
    validation = metadata.get("validation_status", {})
    source_snapshot = metadata.get("source_snapshot", {})
    schema_summary_path = config.DATA_DIR / "schema" / RELEASE_NAME / "schema_build_summary.json"
    schema_summary = json.loads(schema_summary_path.read_text(encoding="utf-8")) if schema_summary_path.exists() else {}
    headline_counts = {
        "dataset": "PT60-Candidate",
        "dataset_version": VERSION,
        "generated_at": release_timestamp(),
        "generator": "src/build_pt60_release_archive.py",
        "generator_commit": generator_commit(),
        "release_archive": {
            "release_dir": str(RELEASE_DIR.relative_to(config.ROOT_DIR)),
            "archive_path": str(ARCHIVE_PATH.relative_to(config.ROOT_DIR)),
        },
        "source_snapshot": {
            "source_datasets": source_snapshot.get("sources", []),
            "source_manifest_json": source_snapshot.get("manifest_json", {}),
            "portal_license": source_snapshot.get("portal_license"),
            "release_conditions": source_snapshot.get("release_conditions", []),
        },
        "core_dataset_counts": {
            "source_line_features": frozen.get("source_line_features"),
            "valid_line_geometries": frozen.get("valid_line_geometries"),
            "facility_rows_loaded": frozen.get("facility_rows_loaded"),
            "merged_circuit_candidates": frozen.get("merged_circuit_candidates"),
            "retained_interfacility_branches": frozen.get("retained_interfacility_branches"),
            "downgraded_or_rejected_records": frozen.get("downgraded_or_rejected_records"),
            "selected_graph_nodes": frozen.get("selected_graph_nodes"),
            "selected_graph_edges": frozen.get("selected_graph_edges"),
            "sensitivity_sweep_rows": frozen.get("sensitivity_sweep_rows"),
        },
        "validation_counts": {
            "osm_openinframap_60kv_public_way_count": frozen.get("osm_openinframap_60kv_public_way_count"),
            "osm_evidence_retained_branches_tested": frozen.get("osm_evidence_retained_branches_tested"),
            "osm_evidence_categories": frozen.get("osm_evidence_categories"),
            "endpoint_name_negative_control": validation.get("endpoint_name_negative_control", {}),
            "spatial_alignment_negative_control": validation.get("spatial_alignment_negative_control", {}),
            "osm_openinframap_independence_audit": validation.get("osm_openinframap_independence_audit", {}),
            "internal_validation": validation.get("internal_validation", {}),
        },
        "schema_counts": {
            "machine_readable_dataset_files_documented_excluding_schema_package": schema_summary.get(
                "machine_readable_dataset_files_documented_excluding_schema_package"
            ),
            "total_release_machine_readable_paths_documented": schema_summary.get("total_release_machine_readable_paths_documented"),
            "field_records": schema_summary.get("field_records"),
            "principal_schema_files": schema_summary.get("principal_schema_files"),
            "schema_status": schema_summary.get("status"),
        },
        "claim_boundary": {
            "supported": metadata.get("freeze_policy", {}).get("claim_boundary", []),
            "prohibited": metadata.get("freeze_policy", {}).get("prohibited_claims", []),
        },
    }
    write_json(RELEASE_DIR / "inventory" / "headline_counts.json", headline_counts)


def copy_release_inputs() -> list[dict[str, Any]]:
    copied: list[dict[str, Any]] = []

    copy_file(config.ROOT_DIR / "DATA_LICENSE.md", "DATA_LICENSE.md", copied, "license and data terms", "license")
    copy_file(config.ROOT_DIR / "LICENSE", "LICENSE-CODE-MIT", copied, "software license", "license")

    for src in [
        "at_interfacility_candidate_branches.csv",
        "at_circuit_classification.csv",
        "at_paper_logic_graph.graphml",
        "at_paper_logic_parameter_sweep.csv",
        "at_paper_logic_summary.json",
        "at_endpoint_index.csv",
        "at_endpoint_index_summary.csv",
        "at_endpoint_facility_membership_summary.csv",
        "at_facility_footprints_summary.csv",
        "at_voltage_class_inventory.csv",
        "at_parameter_availability_matrix.csv",
        "at_parameter_feasibility_summary.json",
    ]:
        path = config.PROCESSED_DIR / src
        if path.exists():
            copy_file(path, f"core_topology/{src}", copied, "core topology release artifact", "core_topology")

    for src in [
        "api_validation.json",
        "license_approvals.csv",
        "reproduction_source_manifest.csv",
        "reproduction_source_manifest.json",
        "responsible_release_boundary.json",
        "pt60_v1.0.2_source_input_manifest.json",
    ]:
        copy_file(config.METADATA_DIR / src, f"provenance/{src}", copied, "source and release provenance", "provenance")

    validation_files = [
        "pt_osm_openinframap_60kv_evidence.csv",
        "pt_topology_cross_validation_osm_matches.csv",
        "pt_topology_cross_validation_source_audit.csv",
        "pt_topology_cross_validation_summary.json",
        "matcher_negative_control_names.csv",
        "matcher_negative_control_names_summary.json",
        "matcher_negative_control_geometry.csv",
        "matcher_negative_control_geometry_summary.json",
        "internal_validation_summary.json",
        "internal_validation_checks.csv",
        "internal_validation_missingness.csv",
        "projection_release_transition.csv",
        "projection_release_transition_summary.json",
    ]
    for src in validation_files:
        copy_file(config.PROCESSED_DIR / "topology_validation" / src, f"validation/{src}", copied, "technical validation output", "validation")

    copy_json_transformed(
        config.PROCESSED_DIR / "topology_validation" / "pt_osm_openinframap_independence_audit_summary.json",
        "validation/pt_osm_openinframap_independence_audit_summary.json",
        copied,
        public_osm_independence_summary,
        "sanitized public OSM/OpenInfraMap independence audit summary",
        "validation",
    )

    copy_csv_selected_columns(
        config.PROCESSED_DIR / "topology_validation" / "pt_topology_cross_validation_osm_matches_independence_audit.csv",
        "validation/pt_topology_cross_validation_osm_matches_independence_audit.csv",
        copied,
        [
            "branch_id",
            "from_facility_code",
            "from_facility_name",
            "to_facility_code",
            "to_facility_name",
            "branch_voltage",
            "branch_length_km",
            "branch_confidence_score",
            "osm_id",
            "osm_power",
            "osm_voltage",
            "osm_operator",
            "osm_name",
            "osm_ref",
            "osm_old_ref",
            "osm_old_name",
            "osm_circuits",
            "osm_cables",
            "osm_length_km",
            "from_name_score",
            "to_name_score",
            "min_distance_m",
            "median_branch_to_osm_m",
            "branch_coverage_250m",
            "branch_coverage_500m",
            "osm_coverage_500m",
            "external_evidence_status",
            "evidence_reason",
            "osm_url",
            "history_versions",
            "history_first_timestamp",
            "history_last_timestamp",
            "history_source_fields_observed",
            "history_source_risk",
            "evidence_role",
            "independence_category",
            "independence_reason",
            "operator_tag_is_operator_confirmation",
        ],
        "sanitized branch-level public-source independence audit without OSM user identifiers or changeset-level history dump",
        "validation",
    )

    schema_dir = config.DATA_DIR / "schema" / RELEASE_NAME
    if schema_dir.exists():
        copy_tree(schema_dir, "schema", copied, "schema, data dictionary, CRS, units and join documentation", "schema")

    return copied


def write_manifest_and_checksums(copied: list[dict[str, Any]]) -> None:
    write_json(RELEASE_DIR / "manifest.json", {"placeholder": True})
    write_text(RELEASE_DIR / "checksums.sha256", "")
    records = manifest_records(copied)
    manifest = {
        "dataset": "PT60-Candidate",
        "version": VERSION,
        "generated_at": release_timestamp(),
        "status": "figshare_dataset_archive_pending_publication_and_code_doi",
        "dataset_doi": DATASET_DOI,
        "dataset_doi_url": DATASET_DOI_URL,
        "generator": "src/build_pt60_release_archive.py",
        "file_count": len(records),
        "records": records,
        "self_reference_note": "manifest.json and checksums.sha256 are listed in the manifest; their sha256 fields are intentionally blank to avoid self-referential hash instability.",
    }
    write_json(RELEASE_DIR / "manifest.json", manifest)
    lines = []
    for path in sorted(RELEASE_DIR.rglob("*")):
        if path.is_file():
            rel = str(path.relative_to(RELEASE_DIR))
            if rel == "checksums.sha256":
                continue
            lines.append(f"{sha256(path)}  {rel}")
    write_text(RELEASE_DIR / "checksums.sha256", "\n".join(lines) + "\n")


def validate_release() -> dict[str, Any]:
    manifest = json.loads((RELEASE_DIR / "manifest.json").read_text(encoding="utf-8"))
    documented = {record["path"] for record in manifest["records"]}
    actual = {str(path.relative_to(RELEASE_DIR)) for path in RELEASE_DIR.rglob("*") if path.is_file()}
    missing_documentation = sorted(actual - documented)
    documented_missing = sorted(documented - actual)
    required = [
        "README.md",
        "CITATION.cff",
        "DATA_LICENSE.md",
        "ATTRIBUTION.md",
        "CHANGELOG.md",
        "manifest.json",
        "checksums.sha256",
        "core_topology/at_interfacility_candidate_branches.csv",
        "core_topology/at_circuit_classification.csv",
        "core_topology/at_paper_logic_graph.graphml",
        "core_topology/at_paper_logic_parameter_sweep.csv",
        "provenance/reproduction_source_manifest.json",
        "inventory/headline_counts.json",
        "validation/internal_validation_summary.json",
        "schema/data_dictionary.csv",
        "schema/file_schema_summary.csv",
        "schema/join_relationships.csv",
        "schema/crs_and_geometry.json",
    ]
    missing_required = [path for path in required if not (RELEASE_DIR / path).exists()]
    checksum_mismatches = []
    checksum_missing_paths = []
    checksum_path = RELEASE_DIR / "checksums.sha256"
    if checksum_path.exists():
        for line in checksum_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            digest, rel = line.split(maxsplit=1)
            path = RELEASE_DIR / rel
            if not path.exists():
                checksum_missing_paths.append(rel)
            elif sha256(path) != digest:
                checksum_mismatches.append(rel)
    return {
        "generated_at": release_timestamp(),
        "release_dir": str(RELEASE_DIR.relative_to(config.ROOT_DIR)),
        "archive_path": str(ARCHIVE_PATH.relative_to(config.ROOT_DIR)),
        "file_count": len(actual),
        "manifest_records": len(documented),
        "missing_documentation": missing_documentation,
        "documented_missing": documented_missing,
        "missing_required": missing_required,
        "checksum_mismatches": checksum_mismatches,
        "checksum_missing_paths": checksum_missing_paths,
        "status": "PASS"
        if not missing_documentation
        and not documented_missing
        and not missing_required
        and not checksum_mismatches
        and not checksum_missing_paths
        else "FAIL",
    }


def make_tarball() -> None:
    if ARCHIVE_PATH.exists():
        ARCHIVE_PATH.unlink()

    def normalise_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
        info.uid = 0
        info.gid = 0
        info.uname = ""
        info.gname = ""
        info.mtime = 0
        info.pax_headers = {}
        return info

    # Fix both gzip and tar metadata so a clean tagged checkout produces the
    # same archive bytes when its release files are unchanged.
    with ARCHIVE_PATH.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as tar:
                tar.add(RELEASE_DIR, arcname=RELEASE_NAME, filter=normalise_tar_info)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overwrite", action="store_true", help="Replace the existing generated release directory.")
    args = parser.parse_args()

    if RELEASE_DIR.exists():
        if not args.overwrite:
            raise RuntimeError(f"Release directory already exists; rerun with --overwrite: {RELEASE_DIR}")
        shutil.rmtree(RELEASE_DIR)
    RELEASE_DIR.mkdir(parents=True, exist_ok=True)

    copied = copy_release_inputs()
    write_release_readme()
    write_attribution()
    write_changelog()
    write_citation()
    write_exclusions()
    write_headline_counts()
    copied.append(
        {
            "path": "inventory/headline_counts.json",
            "source_path": "",
            "purpose": "frozen article headline counts and source-summary inventory",
            "semantic_role": "inventory",
        }
    )
    scrub_release_text_files()
    write_manifest_and_checksums(copied)
    make_tarball()
    validation = validate_release()
    write_json(RELEASE_DIR / "archive_validation_summary.json", validation)
    # Refresh manifest/checksums so archive_validation_summary is also documented.
    copied.append(
        {
            "path": "archive_validation_summary.json",
            "source_path": "",
            "purpose": "archive build validation",
            "semantic_role": "release_control",
        }
    )
    write_manifest_and_checksums(copied)
    make_tarball()
    validation = validate_release()
    write_json(RELEASE_DIR / "archive_validation_summary.json", validation)
    write_manifest_and_checksums(copied)
    make_tarball()
    print(json.dumps(validation, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
