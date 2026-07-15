"""Validate a PT60-Candidate release archive in an isolated temporary directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import utc_now, write_json, write_text


REQUIRED_MANIFEST_FIELDS = {
    "path",
    "purpose",
    "semantic_role",
    "source_path",
    "media_type",
    "bytes",
    "sha256",
    "row_or_item_count",
    "dataset_version",
    "schema_version",
    "generator_commit",
    "upstream_source_ids",
    "license_class",
    "public_release_status",
    "artifact_access_class",
}


REQUIRED_FILES = {
    "README.md",
    "CITATION.cff",
    "DATA_LICENSE.md",
    "ATTRIBUTION.md",
    "CHANGELOG.md",
    "manifest.json",
    "checksums.sha256",
    "core_topology/at_interfacility_candidate_branches.csv",
    "core_topology/at_circuit_classification.csv",
    "core_topology/at_paper_logic_parameter_sweep.csv",
    "core_topology/at_paper_logic_graph.graphml",
    "provenance/reproduction_source_manifest.json",
    "validation/internal_validation_summary.json",
    "schema/data_dictionary.csv",
    "schema/file_schema_summary.csv",
    "schema/join_relationships.csv",
    "schema/crs_and_geometry.json",
    "inventory/headline_counts.json",
}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_extract(tar: tarfile.TarFile, destination: Path) -> None:
    destination = destination.resolve()
    for member in tar.getmembers():
        target = (destination / member.name).resolve()
        if destination not in target.parents and target != destination:
            raise RuntimeError(f"Unsafe tar member path: {member.name}")
    tar.extractall(destination, filter="data")


def validate_extracted(root: Path, archive_sha256: str, archive_path: Path) -> dict[str, Any]:
    try:
        archive_path_text = str(archive_path.relative_to(config.ROOT_DIR))
    except ValueError:
        archive_path_text = archive_path.name
    manifest_path = root / "manifest.json"
    checksums_path = root / "checksums.sha256"
    dictionary_path = root / "schema" / "data_dictionary.csv"
    headline_path = root / "inventory" / "headline_counts.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("records", [])
    documented = {record["path"] for record in records}
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}

    missing_manifest_fields = [
        record.get("path", "<missing-path>")
        for record in records
        if not REQUIRED_MANIFEST_FIELDS.issubset(record)
    ]
    missing_documentation = sorted(actual - documented)
    documented_missing = sorted(documented - actual)
    missing_required = sorted(path for path in REQUIRED_FILES if not (root / path).exists())

    manifest_hash_mismatches = []
    for record in records:
        rel = record["path"]
        if rel in {"manifest.json", "checksums.sha256"}:
            continue
        path = root / rel
        if path.exists() and record.get("sha256") != sha256(path):
            manifest_hash_mismatches.append(rel)

    checksum_mismatches = []
    checksum_missing_paths = []
    checksum_documented = set()
    for line in checksums_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(maxsplit=1)
        checksum_documented.add(rel)
        path = root / rel
        if not path.exists():
            checksum_missing_paths.append(rel)
        elif sha256(path) != digest:
            checksum_mismatches.append(rel)

    checksum_undocumented = sorted((actual - {"checksums.sha256"}) - checksum_documented)
    checksum_extra = sorted(checksum_documented - actual)

    df = pd.read_csv(dictionary_path, dtype=str)
    dictionary_paths = set(df["relative_path"])
    machine_readable = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.suffix.lower() in {".csv", ".json", ".graphml"}
    }
    dictionary_missing_paths = sorted(machine_readable - dictionary_paths)
    dictionary_extra_paths = sorted(dictionary_paths - machine_readable)

    headline = json.loads(headline_path.read_text(encoding="utf-8"))
    core_counts = headline.get("core_dataset_counts", {})
    headline_count_checks = {
        "retained_interfacility_branches": core_counts.get("retained_interfacility_branches") == 358,
        "merged_circuit_candidates": core_counts.get("merged_circuit_candidates") == 1341,
        "sensitivity_sweep_rows": core_counts.get("sensitivity_sweep_rows") == 216,
        "selected_graph_edges": core_counts.get("selected_graph_edges") == 358,
    }

    status = "PASS"
    failures: dict[str, Any] = {
        "missing_manifest_fields": missing_manifest_fields,
        "missing_documentation": missing_documentation,
        "documented_missing": documented_missing,
        "missing_required": missing_required,
        "manifest_hash_mismatches": manifest_hash_mismatches,
        "checksum_mismatches": checksum_mismatches,
        "checksum_missing_paths": checksum_missing_paths,
        "checksum_undocumented": checksum_undocumented,
        "checksum_extra": checksum_extra,
        "dictionary_missing_paths": dictionary_missing_paths,
        "dictionary_extra_paths": dictionary_extra_paths,
        "failed_headline_count_checks": [key for key, ok in headline_count_checks.items() if not ok],
    }
    if any(failures.values()):
        status = "FAIL"

    return {
        "generated_at": utc_now(),
        "validation_mode": "package_clean_room_tarball_extraction",
        "archive_path": archive_path_text,
        "archive_sha256": archive_sha256,
        "extracted_root_name": root.name,
        "manifest_records": len(records),
        "file_count": len(actual),
        "machine_readable_paths": len(machine_readable),
        "dictionary_paths": len(dictionary_paths),
        "dictionary_field_records": len(df),
        "headline_count_checks": headline_count_checks,
        "failures": failures,
        "status": status,
    }


def write_report(summary: dict[str, Any], output_report: Path) -> None:
    failures = summary["failures"]
    text = f"""# PT60-Candidate Clean-Room Archive Package Validation

Generated: {summary['generated_at']}

Validation mode: `{summary['validation_mode']}`

Archive: `{summary['archive_path']}`

Archive SHA-256: `{summary['archive_sha256']}`

Status: `{summary['status']}`

## Results

- Manifest records: {summary['manifest_records']}
- Files after extraction: {summary['file_count']}
- Machine-readable CSV/JSON/GraphML paths: {summary['machine_readable_paths']}
- Data-dictionary documented paths: {summary['dictionary_paths']}
- Data-dictionary field records: {summary['dictionary_field_records']}

## Failure counts

{chr(10).join(f'- `{key}`: {len(value)}' for key, value in failures.items())}

## Scope

This is an archive-package clean-room validation. It proves that a fresh extraction of the downloadable tarball reconciles manifest records, checksums, schema coverage and frozen headline counts without relying on the development release directory.

It does not prove full source-to-archive regeneration from raw E-REDES/API downloads. That stronger validation requires a clean tagged checkout, frozen or re-downloadable source snapshots, and network/source availability.
"""
    write_text(output_report, text)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=config.DATA_DIR / "releases" / "PT60-Candidate-v1.0.2.tar.gz")
    parser.add_argument("--output-json", type=Path, default=config.METADATA_DIR / "clean_room_archive_validation_summary.json")
    parser.add_argument("--output-report", type=Path, default=config.REPORTS_DIR / "108_pt60_clean_room_archive_validation.md")
    args = parser.parse_args()

    archive_path = args.archive.resolve()
    archive_digest = sha256(archive_path)
    with tempfile.TemporaryDirectory(prefix="pt60_clean_room_") as tmp:
        tmp_path = Path(tmp)
        with tarfile.open(archive_path, "r:gz") as tar:
            safe_extract(tar, tmp_path)
        roots = [path for path in tmp_path.iterdir() if path.is_dir()]
        if len(roots) != 1:
            raise RuntimeError(f"Expected one top-level archive directory, found {len(roots)}")
        summary = validate_extracted(roots[0], archive_digest, archive_path)

    write_json(args.output_json, summary)
    write_report(summary, args.output_report)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    if summary["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
