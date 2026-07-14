"""Build the PT60-Candidate v1.0.0 public release directory and tarball."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import mimetypes
import re
import shutil
import tarfile
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import utc_now, write_json, write_text


VERSION = "v1.0.0"
RELEASE_NAME = f"PT60-Candidate-{VERSION}"
RELEASES_DIR = config.DATA_DIR / "releases"
RELEASE_DIR = RELEASES_DIR / RELEASE_NAME
ARCHIVE_PATH = RELEASES_DIR / f"{RELEASE_NAME}.tar.gz"
SCHEMA_VERSION = "pt60_candidate_schema_v1.0.0"


def root_release_metadata() -> dict[str, Any]:
    path = config.ROOT_DIR / "release_metadata.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def generator_commit() -> str:
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
    if rel.startswith("optional_interfaces/") or rel.startswith("optional_diagnostic/"):
        return all_sources or topology_sources
    if rel.startswith("provenance/"):
        return all_sources or topology_sources
    if rel.startswith("schema/") or rel.startswith("inventory/") or rel.startswith("manuscript/"):
        return all_sources or topology_sources
    return []


def license_class_for_path(rel: str) -> str:
    if rel == "LICENSE-CODE-MIT":
        return "MIT_CODE_LICENSE"
    if rel in {"DATA_LICENSE.md", "ATTRIBUTION.md"}:
        return "LICENSE_AND_ATTRIBUTION_DOCUMENTATION"
    if rel.startswith("validation/") and ("osm" in rel.lower() or "openinframap" in rel.lower()):
        return "E_REDES_CC_BY_4_0_DERIVED_PLUS_OSM_OPENINFRAMAP_PUBLIC_EVIDENCE_ATTRIBUTION_REQUIRED"
    if rel.startswith("core_topology/") or rel.startswith("optional_interfaces/") or rel.startswith("optional_diagnostic/"):
        return "E_REDES_CC_BY_4_0_DERIVED_WITH_ATTRIBUTION_AND_MODIFICATION_NOTICE"
    if rel.startswith("provenance/"):
        return "SOURCE_PROVENANCE_METADATA_WITH_E_REDES_ATTRIBUTION_CONTEXT"
    if rel.startswith("schema/") or rel.startswith("inventory/"):
        return "PROJECT_GENERATED_RELEASE_METADATA"
    if rel.startswith("manuscript/"):
        return "MANUSCRIPT_SUPPORT_MIXED_CITATION_AND_PROJECT_CONTENT"
    return "PROJECT_RELEASE_CONTROL_METADATA"


def access_class_for_path(rel: str) -> str:
    if rel.startswith("optional_diagnostic/"):
        return "public_optional_diagnostic_not_operational"
    if rel.startswith("optional_interfaces/"):
        return "public_optional_derivative_interface"
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


def copy_tree(src: Path, dst_rel: str, copied: list[dict[str, Any]], purpose: str, role: str) -> None:
    if not src.exists():
        raise FileNotFoundError(f"Required release input directory missing: {src}")
    for path in sorted(src.rglob("*")):
        if path.is_file():
            rel = Path(dst_rel) / path.relative_to(src)
            copy_file(path, str(rel), copied, purpose, role)


def copy_release_metadata_for_archive(copied: list[dict[str, Any]]) -> None:
    src = config.ROOT_DIR / "release_metadata.json"
    if not src.exists():
        raise FileNotFoundError(f"Required release input missing: {src}")
    data = json.loads(src.read_text(encoding="utf-8"))
    dataset_archive = data.get("availability_status", {}).get("dataset_archive")
    if isinstance(dataset_archive, dict):
        dataset_archive["archive_sha256"] = "RECORDED_EXTERNALLY_AFTER_TARBALL_CREATION"
        dataset_archive["self_reference_note"] = (
            "The tarball digest is recorded in the repository-side release_metadata.json "
            "and deposit record after archive creation. The in-archive copy omits the "
            "digest to avoid self-referential hash instability."
        )
    dst_rel = "provenance/release_metadata.json"
    dst = RELEASE_DIR / dst_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    write_json(dst, data)
    copied.append(
        {
            "path": dst_rel,
            "source_path": str(src.relative_to(config.ROOT_DIR)),
            "purpose": "release freeze metadata with self-referential archive digest omitted",
            "semantic_role": "provenance",
        }
    )


def copy_tasklist_for_archive(copied: list[dict[str, Any]]) -> None:
    src = config.ROOT_DIR / "paper" / "TASKLIST.MD"
    if not src.exists():
        raise FileNotFoundError(f"Required release input missing: {src}")
    text = src.read_text(encoding="utf-8")
    text = re.sub(
        r"The tarball SHA-256 is `[^`]+`",
        "The tarball SHA-256 is `RECORDED_EXTERNALLY_AFTER_TARBALL_CREATION`",
        text,
    )
    text += (
        "\n\nArchive-copy note: this in-archive task list omits the final tarball "
        "digest to avoid self-referential hash instability. Use the repository-side "
        "`release_metadata.json` or deposit record for the final archive SHA-256.\n"
    )
    dst_rel = "manuscript/TASKLIST.MD"
    dst = RELEASE_DIR / dst_rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    write_text(dst, text)
    copied.append(
        {
            "path": dst_rel,
            "source_path": str(src.relative_to(config.ROOT_DIR)),
            "purpose": "manuscript support task list with self-referential archive digest omitted",
            "semantic_role": "manuscript",
        }
    )


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
- `provenance/`: source manifest, release metadata, licensing and responsible-release boundary.
- `validation/`: public-source triangulation, negative controls, OSM/OpenInfraMap independence audit and internal validation outputs.
- `optional_interfaces/`: Stage 1-5 derivative consumer interfaces.
- `optional_diagnostic/`: non-operational electrical-readiness interface files, where included.
- `manuscript/`: current Scientific Data draft route and generated figures.

## Claim boundary

Use this archive for reconstruction research, geospatial data integration, provenance-aware dataset engineering, graph/tabular interface testing and sensitivity analysis.

Do not use it for operational switching, protection studies, security analysis, contingency analysis, congestion analysis, infrastructure targeting, emergency operations, asset-condition assessment or regulatory/commercial capacity claims.

## Licensing

Repository code is MIT licensed. E-REDES-derived data use the E-REDES Open Data Portal terms recorded in `DATA_LICENSE.md`, `ATTRIBUTION.md` and `provenance/reproduction_source_manifest.json`. Reuse must retain E-REDES attribution, link CC BY 4.0, identify source datasets/access dates and indicate transformations.

## Status

Dataset DOI, code DOI, final schemas/data dictionary and clean-room reproduction are still pending. See `manifest.json`, `checksums.sha256` and `excluded_artifacts.json` for archive contents and exclusions.
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

## {VERSION} - 2026-07-14

- Freeze draft for Scientific Data public-source validation route.
- Includes 358 retained candidate branches and a 1,342-row retained/downgraded/rejected circuit ledger.
- Includes 216-configuration sensitivity sweep.
- Includes OSM/OpenInfraMap public-source triangulation, endpoint-name negative control, spatial-alignment negative control, independence audit and internal validation outputs.
- Excludes raw E-REDES downloads from the default public archive.
- Excludes ACPF/DCOPF operational diagnostics from the core archive.

Pending after this archive build:

- dataset DOI;
- code DOI/release tag;
- final field dictionary and JSON schemas;
- clean-room reproduction from final clean tag.
"""
    write_text(RELEASE_DIR / "CHANGELOG.md", text)


def write_optional_readmes() -> None:
    write_text(
        RELEASE_DIR / "optional_interfaces" / "README.md",
        "Stage 1-5 files are optional derivative consumer interfaces. Scenario-derived labels are diagnostic targets, not observed grid events or operator records.\n",
    )
    write_text(
        RELEASE_DIR / "optional_diagnostic" / "README.md",
        "Files in this directory are non-operational diagnostics. They must not be used to claim AC power-flow, OPF, protection or operational readiness.\n",
    )


def write_exclusions() -> None:
    exclusions = {
        "generated_at": utc_now(),
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
                "reason": "The v1.0.0 manuscript route does not use dual independent human adjudication or report precision.",
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
repository-code: "REPLACE_WITH_PUBLIC_CODE_REPOSITORY_URL"
doi: "REPLACE_WITH_DATASET_DOI"
abstract: >-
  A provenance-tracked candidate dataset and fail-closed reconstruction pipeline output for Portuguese 60 kV topology reconstruction from public E-REDES Open Data. The dataset is not operator validated and is not an operational grid model.
"""
    write_text(RELEASE_DIR / "CITATION.cff", text)


def write_headline_counts() -> None:
    metadata = root_release_metadata()
    frozen = metadata.get("frozen_counts", {})
    validation = metadata.get("validation_results", {})
    source_snapshot = metadata.get("source_snapshot", {})
    schema_summary_path = config.DATA_DIR / "schema" / RELEASE_NAME / "schema_build_summary.json"
    schema_summary = json.loads(schema_summary_path.read_text(encoding="utf-8")) if schema_summary_path.exists() else {}
    headline_counts = {
        "dataset": "PT60-Candidate",
        "dataset_version": VERSION,
        "generated_at": utc_now(),
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
    copy_release_metadata_for_archive(copied)

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
    ]:
        copy_file(config.METADATA_DIR / src, f"provenance/{src}", copied, "source and release provenance", "provenance")

    validation_files = [
        "pt_osm_openinframap_60kv_evidence.csv",
        "pt_osm_openinframap_60kv_power_ways.json",
        "pt_topology_cross_validation_osm_matches.csv",
        "pt_topology_cross_validation_source_audit.csv",
        "pt_topology_cross_validation_summary.json",
        "matcher_negative_control_names.csv",
        "matcher_negative_control_names_summary.json",
        "matcher_negative_control_geometry.csv",
        "matcher_negative_control_geometry_summary.json",
        "pt_osm_openinframap_60kv_power_ways_meta.json",
        "pt_osm_matched_way_histories.json",
        "pt_osm_openinframap_independence_audit.csv",
        "pt_osm_openinframap_independence_audit_summary.json",
        "pt_osm_openinframap_matched_way_history_audit.csv",
        "pt_topology_cross_validation_osm_matches_independence_audit.csv",
        "internal_validation_summary.json",
        "internal_validation_checks.csv",
        "internal_validation_missingness.csv",
    ]
    for src in validation_files:
        copy_file(config.PROCESSED_DIR / "topology_validation" / src, f"validation/{src}", copied, "technical validation output", "validation")

    for report in [
        "102_pt60_external_topology_cross_validation.md",
        "103_pt60_endpoint_name_negative_control.md",
        "104_pt60_spatial_alignment_negative_control.md",
        "105_pt60_osm_openinframap_independence_audit.md",
        "106_pt60_internal_validation_summary.md",
        "107_pt60_responsible_release_boundary.md",
    ]:
        copy_file(config.REPORTS_DIR / report, f"validation/reports/{report}", copied, "validation narrative report", "validation_report")

    for stage in range(1, 6):
        src_dir = config.PROCESSED_DIR / f"dataset_release_stage{stage}"
        if src_dir.exists():
            copy_tree(src_dir, f"optional_interfaces/stage{stage}", copied, "optional derivative consumer interface", "optional_interface")

    for src_dir_name in ["pandapower_schema", "lut_scenarios", "load_validation"]:
        src_dir = config.PROCESSED_DIR / src_dir_name
        if src_dir.exists():
            copy_tree(src_dir, f"optional_diagnostic/{src_dir_name}", copied, "optional non-operational diagnostic artifact", "optional_diagnostic")

    schema_dir = config.DATA_DIR / "schema" / RELEASE_NAME
    if schema_dir.exists():
        copy_tree(schema_dir, "schema", copied, "schema, data dictionary, CRS, units and join documentation", "schema")

    paper_dir = config.ROOT_DIR / "paper"
    for src, dst in [
        ("main_scidata_public_validation.pdf", "manuscript/main_scidata_public_validation.pdf"),
        ("main_scidata_public_validation.tex", "manuscript/main_scidata_public_validation.tex"),
        ("main_scidata.tex", "manuscript/main_scidata.tex"),
        ("references.bib", "manuscript/references.bib"),
        ("figure_manifest.csv", "manuscript/figure_manifest.csv"),
    ]:
        copy_file(paper_dir / src, dst, copied, "manuscript support file", "manuscript")
    copy_tasklist_for_archive(copied)
    if (paper_dir / "figures" / "generated").exists():
        copy_tree(paper_dir / "figures" / "generated", "manuscript/figures/generated", copied, "manuscript figure", "manuscript_figure")

    return copied


def write_manifest_and_checksums(copied: list[dict[str, Any]]) -> None:
    write_json(RELEASE_DIR / "manifest.json", {"placeholder": True})
    write_text(RELEASE_DIR / "checksums.sha256", "")
    records = manifest_records(copied)
    manifest = {
        "dataset": "PT60-Candidate",
        "version": VERSION,
        "generated_at": utc_now(),
        "status": "archive_skeleton_pending_schema_and_doi",
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
        "generated_at": utc_now(),
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
    with tarfile.open(ARCHIVE_PATH, "w:gz") as tar:
        tar.add(RELEASE_DIR, arcname=RELEASE_NAME)


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
    write_optional_readmes()
    write_exclusions()
    write_headline_counts()
    copied.append(
        {
            "path": "inventory/headline_counts.json",
            "source_path": "",
            "purpose": "frozen manuscript headline counts and source-summary inventory",
            "semantic_role": "inventory",
        }
    )
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
