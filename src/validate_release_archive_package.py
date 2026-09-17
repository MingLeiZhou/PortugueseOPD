"""Validate a PT60-Candidate release archive in an isolated temporary directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

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


def validate_v2_extracted(root: Path, manifest: dict[str, Any], archive_sha256: str, archive_path: Path) -> dict[str, Any]:
    """Validate the PT60 >=60 kV v2 package contract."""
    records = manifest.get("records", [])
    documented = {str(record.get("path", "")) for record in records}
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    allowed_support = {"manifest.json", "checksums.sha256", "archive_validation_summary.json"}
    required = {
        "README.md", "CITATION.cff", "DATA_LICENSE.md", "ATTRIBUTION.md",
        "PUBLIC_MODEL_INTERFACE.md", "manifest.json", "checksums.sha256",
        "config/model_config.json", "config/sources.json",
        "config/public_input_schema.json", "config/public_benchmark_scope.json",
        "topology/buses.csv", "topology/lines.csv", "topology/transformers.csv",
        "scenario/loads.csv", "scenario/generators.csv",
        "model/pt60_candidate.json", "model/pt60_candidate_solved.json",
        "power_flow/summary.json", "validation/summary.json",
        "validation/temporal/multi_snapshot_comparison.csv",
        "validation/temporal/public_benchmark_gap_status.json",
    }


def validate_review_candidate_extracted(
    root: Path,
    manifest: dict[str, Any],
    archive_sha256: str,
    archive_path: Path,
) -> dict[str, Any]:
    """Validate the reader-facing v2.1 release candidate and replay contract."""
    records = manifest.get("files", [])
    documented = {str(record.get("path", "")) for record in records}
    actual = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    allowed_support = {"manifest.json", "checksums.sha256"}
    required = {
        "README.md",
        "VERSION.json",
        "DATA_LICENSE.md",
        "ATTRIBUTION.md",
        "LICENSE",
        "PUBLIC_MODEL_INTERFACE.md",
        "requirements.txt",
        "manifest.json",
        "checksums.sha256",
        "config/model_config.json",
        "config/sources.json",
        "config/public_input_schema.json",
        "config/public_benchmark_scope.json",
        "topology/buses.csv",
        "topology/lines.csv",
        "topology/transformers.csv",
        "scenario/loads.csv",
        "scenario/generators.csv",
        "scenario/boundaries.csv",
        "model/model_template.json",
        "model/PT60_2026W_JAN20_solved.json",
        "model/january_input.json",
        "model/summary.json",
        "validation/seasonal_week_validation.csv",
        "validation/static_control_reference_week.csv",
        "validation/diagnostic_results.csv",
        "validation/experiment_manifest.json",
        "validation/solver_execution_manifest.json",
        "reproduction/portuguese_hv_network/outputs/temporal_validation/review_revision/installed_capacity_comparison.csv",
        "examples/public_case/scenario.json",
        "examples/public_case/loads.csv",
        "examples/public_case/provenance.json",
        "paper/PT60_SCIENTIFIC_DATA_CN_READER_FIRST_DRAFT.md",
        "paper/PT60_DATA_DOCUMENTATION_CN.md",
        "paper/PT60_VALIDATION_SUPPLEMENT_CN.md",
        "paper/scripts/run_seasonal_validation.py",
        "paper/scripts/replay_archived_validation_case.py",
        "paper/scripts/replay_pt60_january.py",
        "paper/figure_manifest_pt60_reader.csv",
    }
    missing_required = sorted(required - actual)
    documented_missing = sorted(documented - actual)
    undocumented = sorted(actual - documented - allowed_support)
    malformed_records = [
        str(row.get("path", "<missing>"))
        for row in records
        if not {"path", "bytes", "sha256"}.issubset(row)
    ]
    hash_mismatches = [
        str(row["path"])
        for row in records
        if (root / str(row.get("path", ""))).exists()
        and sha256(root / str(row["path"])) != row.get("sha256")
    ]

    checksum_mismatches: list[str] = []
    checksum_missing: list[str] = []
    checksum_paths: set[str] = set()
    for line in (root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(maxsplit=1)
        checksum_paths.add(rel)
        path = root / rel
        if not path.exists():
            checksum_missing.append(rel)
        elif sha256(path) != digest:
            checksum_mismatches.append(rel)
    checksum_undocumented = sorted((actual - {"checksums.sha256", "manifest.json"}) - checksum_paths)

    csv_read_errors: list[str] = []
    for rel in sorted(path for path in actual if path.endswith(".csv")):
        try:
            pd.read_csv(root / rel, nrows=2)
        except Exception as exc:
            csv_read_errors.append(f"{rel}:{type(exc).__name__}")

    markdown_broken_links: list[str] = []
    for page in sorted(path for path in root.rglob("*.md") if "reproduction" not in path.parts):
        for target in re.findall(r"\]\(([^)]+)\)", page.read_text(encoding="utf-8")):
            target = unquote(target.split("#")[0]).strip("<>")
            if not target or "://" in target or target.startswith("/"):
                continue
            if not (page.parent / target).resolve().exists():
                markdown_broken_links.append(f"{page.relative_to(root)} -> {target}")

    figure_source_errors: list[str] = []
    figure_manifest = root / "paper/figure_manifest_pt60_reader.csv"
    if figure_manifest.exists():
        for row in pd.read_csv(figure_manifest).to_dict("records"):
            for key in ("output_file", "source_data"):
                rel = str(row[key])
                path = root / rel
                if not path.exists():
                    figure_source_errors.append(f"missing:{rel}")
                elif key == "source_data" and sha256(path) != str(row["source_sha256"]):
                    figure_source_errors.append(f"hash:{rel}")
    source_manifest = root / "paper/figures/pt60_review/source_manifest.json"
    if source_manifest.exists():
        for row in json.loads(source_manifest.read_text(encoding="utf-8")).get("sources", []):
            path = root / str(row["path"])
            if not path.exists():
                figure_source_errors.append(f"missing:{row['path']}")
            elif sha256(path) != str(row["sha256"]):
                figure_source_errors.append(f"hash:{row['path']}")

    stale_reader_artifacts = sorted(
        path
        for path in actual
        if any(
            token in path
            for token in (
                "PT60_REVIEW_REVISION_SUPPLEMENT_CN.md",
                "installed_capacity_revised.csv",
                "pt60_figure_table_metrics.py",
                "paper/figures/pt60_main/",
                "paper/figures/pt60_seasonal/",
                "requirements-reference.txt",
                "validation/model_revised.json",
                "validation/generators_revised.csv",
                "validation/clean_replay",
                "validation/fixed_q_reference_week.csv",
                "provenance/installed_capacity_2026-03.json",
                "provenance/installed_capacity_2026-08.json",
            )
        )
    )

    input_paths = sorted((root / "validation/inputs").glob("*.json"))
    main_results = pd.read_csv(root / "validation/seasonal_week_validation.csv") if (root / "validation/seasonal_week_validation.csv").exists() else pd.DataFrame()
    diagnostics = pd.read_csv(root / "validation/diagnostic_results.csv") if (root / "validation/diagnostic_results.csv").exists() else pd.DataFrame()
    static_control = pd.read_csv(root / "validation/static_control_reference_week.csv") if (root / "validation/static_control_reference_week.csv").exists() else pd.DataFrame()
    generators = pd.read_csv(root / "scenario/generators.csv", keep_default_na=False) if (root / "scenario/generators.csv").exists() else pd.DataFrame()
    requirements_text = (root / "requirements.txt").read_text(encoding="utf-8") if (root / "requirements.txt").exists() else ""
    january_input = json.loads((root / "model/january_input.json").read_text(encoding="utf-8")) if (root / "model/january_input.json").exists() else {}
    january_summary = json.loads((root / "model/summary.json").read_text(encoding="utf-8")) if (root / "model/summary.json").exists() else {}
    example_provenance = json.loads((root / "examples/public_case/provenance.json").read_text(encoding="utf-8")) if (root / "examples/public_case/provenance.json").exists() else {}
    experiment_manifest = json.loads((root / "validation/experiment_manifest.json").read_text(encoding="utf-8")) if (root / "validation/experiment_manifest.json").exists() else {}
    solver_manifest = json.loads((root / "validation/solver_execution_manifest.json").read_text(encoding="utf-8")) if (root / "validation/solver_execution_manifest.json").exists() else {}
    january_input_path = root / "model/january_input.json"
    capacity_comparison = pd.read_csv(root / "validation/installed_capacity_comparison.csv") if (root / "validation/installed_capacity_comparison.csv").exists() else pd.DataFrame()
    historical_capacity = pd.read_csv(root / "validation/historical_capacity_coverage.csv") if (root / "validation/historical_capacity_coverage.csv").exists() else pd.DataFrame()
    interface_text = (root / "PUBLIC_MODEL_INTERFACE.md").read_text(encoding="utf-8") if (root / "PUBLIC_MODEL_INTERFACE.md").exists() else ""
    count_checks = {
        "buses": len(pd.read_csv(root / "topology/buses.csv")) == 3783 if (root / "topology/buses.csv").exists() else False,
        "lines": len(pd.read_csv(root / "topology/lines.csv")) == 4943 if (root / "topology/lines.csv").exists() else False,
        "transformers": len(pd.read_csv(root / "topology/transformers.csv")) == 228 if (root / "topology/transformers.csv").exists() else False,
        "mapped_assets": int(generators["bus_id"].ne("").sum()) == 1041 if "bus_id" in generators else False,
        "hourly_inputs": len(input_paths) == 336,
        "primary_summaries": len(main_results) == 336 and bool(main_results.get("converged", pd.Series(dtype=bool)).all()),
        "static_control_reference": len(static_control) == 336 and bool(static_control.get("converged", pd.Series(dtype=bool)).all()),
        "diagnostic_summaries": len(diagnostics) == 16 and ("variant" in diagnostics) and bool(diagnostics["variant"].ne("AC_REVISED").all()),
        "generator_nulls_normalized": "original_bus_id" in generators and not bool(generators["original_bus_id"].str.lower().isin({"nan", "none", "null"}).any()),
        "portable_declared_requirements": (
            bool(requirements_text.strip())
            and "@ file:" not in requirements_text
            and "file://" not in requirements_text
            and all(
                dependency in requirements_text.lower()
                for dependency in ("geopandas", "matplotlib", "pillow", "shapely")
            )
        ),
        "january_example_role": january_input.get("case", {}).get("analysis_role") == "REUSE_EXAMPLE_WITHIN_WINTER_WEEK" and january_summary.get("analysis_role") == "REUSE_EXAMPLE_WITHIN_WINTER_WEEK",
        "example_provenance": example_provenance.get("source") == "model/january_input.json" and january_input_path.exists() and example_provenance.get("sha256") == sha256(january_input_path),
        "solver_manifest_matches_experiment": bool(experiment_manifest) and experiment_manifest == solver_manifest,
        "capacity_records_match_validation_windows": set(capacity_comparison.get("官方基准月", pd.Series(dtype=str)).astype(str)) == {"2025-07", "2026-01"} and set(historical_capacity.get("timestamp_utc", pd.Series(dtype=str)).astype(str)) == {"2025-07-07", "2026-01-20"},
        "public_interface_uses_release_paths": "model/model_template.json" in interface_text and "scenario/generators.csv" in interface_text and "model_revised.json" not in interface_text and "generators_revised.csv" not in interface_text,
    }

    failures = {
        "missing_required": missing_required,
        "documented_missing": documented_missing,
        "undocumented_files": undocumented,
        "malformed_manifest_records": malformed_records,
        "manifest_hash_mismatches": hash_mismatches,
        "checksum_mismatches": checksum_mismatches,
        "checksum_missing_paths": checksum_missing,
        "checksum_undocumented": checksum_undocumented,
        "csv_read_errors": csv_read_errors,
        "markdown_broken_links": markdown_broken_links,
        "figure_source_errors": figure_source_errors,
        "stale_reader_artifacts": stale_reader_artifacts,
        "failed_count_checks": [key for key, ok in count_checks.items() if not ok],
    }
    try:
        archive_path_text = str(archive_path.relative_to(config.ROOT_DIR))
    except ValueError:
        archive_path_text = archive_path.name
    return {
        "generated_at": utc_now(),
        "validation_mode": "pt60_v2_1_reader_candidate_clean_room_tarball_extraction",
        "archive_path": archive_path_text,
        "archive_sha256": archive_sha256,
        "extracted_root_name": root.name,
        "manifest_records": len(records),
        "file_count": len(actual),
        "machine_readable_paths": len([path for path in actual if Path(path).suffix in {".csv", ".json"}]),
        "dictionary_paths": 0,
        "dictionary_field_records": 0,
        "headline_count_checks": count_checks,
        "failures": failures,
        "status": "PASS" if not any(failures.values()) else "FAIL",
    }
    missing_required = sorted(required - actual)
    documented_missing = sorted(documented - actual)
    undocumented = sorted(actual - documented - allowed_support)
    malformed_records = [
        str(row.get("path", "<missing>")) for row in records
        if not {"path", "bytes", "sha256", "rows"}.issubset(row)
    ]
    hash_mismatches = [
        str(row["path"]) for row in records
        if (root / str(row["path"])).exists()
        and sha256(root / str(row["path"])) != row.get("sha256")
    ]
    checksum_mismatches: list[str] = []
    checksum_missing: list[str] = []
    checksum_paths: set[str] = set()
    for line in (root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, rel = line.split(maxsplit=1)
        checksum_paths.add(rel)
        path = root / rel
        if not path.exists():
            checksum_missing.append(rel)
        elif sha256(path) != digest:
            checksum_mismatches.append(rel)
    checksum_undocumented = sorted((actual - {"checksums.sha256"}) - checksum_paths)
    csv_read_errors: list[str] = []
    for rel in sorted(path for path in actual if path.endswith(".csv")):
        try:
            pd.read_csv(root / rel, nrows=2)
        except Exception as exc:
            csv_read_errors.append(f"{rel}:{type(exc).__name__}")
    failures = {
        "missing_required": missing_required,
        "documented_missing": documented_missing,
        "undocumented_files": undocumented,
        "malformed_manifest_records": malformed_records,
        "manifest_hash_mismatches": hash_mismatches,
        "checksum_mismatches": checksum_mismatches,
        "checksum_missing_paths": checksum_missing,
        "checksum_undocumented": checksum_undocumented,
        "csv_read_errors": csv_read_errors,
    }
    try:
        archive_path_text = str(archive_path.relative_to(config.ROOT_DIR))
    except ValueError:
        archive_path_text = archive_path.name
    return {
        "generated_at": utc_now(),
        "validation_mode": "pt60_v2_package_clean_room_tarball_extraction",
        "archive_path": archive_path_text,
        "archive_sha256": archive_sha256,
        "extracted_root_name": root.name,
        "manifest_records": len(records),
        "file_count": len(actual),
        "machine_readable_paths": len([path for path in actual if Path(path).suffix in {".csv", ".json"}]),
        "dictionary_paths": 0,
        "dictionary_field_records": 0,
        "headline_count_checks": {
            "buses": len(pd.read_csv(root / "topology/buses.csv")) == 3783,
            "lines": len(pd.read_csv(root / "topology/lines.csv")) == 4943,
            "transformers": len(pd.read_csv(root / "topology/transformers.csv")) == 228,
        },
        "failures": failures,
        "status": "PASS" if not any(failures.values()) else "FAIL",
    }


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
    if manifest.get("dataset") == "PT60" and manifest.get("version") == "v2.1.0-rc1" and "files" in manifest:
        return validate_review_candidate_extracted(root, manifest, archive_sha256, archive_path)
    if manifest.get("dataset") == "PT60" and manifest.get("version") == "v2.0.0":
        return validate_v2_extracted(root, manifest, archive_sha256, archive_path)
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
