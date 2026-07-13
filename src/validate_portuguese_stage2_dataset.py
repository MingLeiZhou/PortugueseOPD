from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import ensure_directories, markdown_table, utc_now, write_json, write_text

ROOT = config.ROOT_DIR
OUT = config.PROCESSED_DIR / "dataset_release_stage2"
REPORTS = config.REPORTS_DIR
VALIDATION_REPORT = REPORTS / "91_stage2_dataset_validation.md"

REQUIRED_FILES = {
    "bus_features": OUT / "pt_stage2_bus_features.csv",
    "line_features": OUT / "pt_stage2_line_features.csv",
    "generator_features": OUT / "pt_stage2_generator_features.csv",
    "line_risk_targets": OUT / "pt_stage2_line_risk_targets.csv",
    "generator_risk_targets": OUT / "pt_stage2_generator_risk_targets.csv",
    "provenance_flags": OUT / "pt_stage2_provenance_flags.csv",
    "manifest": OUT / "pt_stage2_manifest.json",
}

REQUIRED_COLUMNS = {
    "bus_features": {"bus_id", "bus_name", "degree_total", "connected_load_count", "connected_generator_count"},
    "line_features": {"line_id", "line_name", "from_bus", "to_bus", "policy_class", "max_target_loading_percent"},
    "generator_features": {"generator_id", "bus_id", "dispatch_proxy_class", "max_dispatch_mw", "is_import_interface_proxy"},
    "line_risk_targets": {"line_id", "line_name", "scenario_family", "variant_id", "metric_max_loading_percent", "top_congested_flag"},
    "generator_risk_targets": {"generator_id", "scenario_family", "variant_id", "dispatch_mw", "top_dispatch_flag"},
    "provenance_flags": {"entity_type", "entity_id", "publication_allowed", "diagnostic_only", "source_scenario_id"},
}

REQUIRED_GOVERNED_LINES = {"ATPL_00075", "ATPL_00147", "ATPL_00244", "ATPL_00304"}


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def load_manifest() -> dict[str, Any]:
    with (OUT / "pt_stage2_manifest.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def add_error(errors: list[str], message: str) -> None:
    errors.append(message)


def check_required_files(errors: list[str]) -> None:
    for name, path in REQUIRED_FILES.items():
        if not path.exists():
            add_error(errors, f"Missing required packaged file: {name} -> {path}")


def check_columns(name: str, df: pd.DataFrame, errors: list[str]) -> None:
    missing = sorted(REQUIRED_COLUMNS.get(name, set()) - set(df.columns))
    if missing:
        add_error(errors, f"{name} missing required columns: {', '.join(missing)}")


def check_unique(df: pd.DataFrame, column: str, label: str, errors: list[str]) -> int:
    duplicates = int(df[column].astype(str).duplicated().sum()) if column in df.columns else 0
    if duplicates:
        add_error(errors, f"{label} has {duplicates} duplicate key rows in column {column}")
    return duplicates


def write_report(summary: dict[str, Any]) -> None:
    summary_rows = [{
        "status": summary["status"],
        "error_count": len(summary["errors"]),
        "warning_count": len(summary["warnings"]),
    }]
    check_rows = [{"check": key, "value": value} for key, value in summary["check_counts"].items()]
    warning_rows = [{"warning": warning} for warning in summary["warnings"]]
    error_rows = [{"error": error} for error in summary["errors"]]
    text = [
        "# 91 Stage-2 Dataset Validation",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: validate the packaged stage-2 feature/target/provenance dataset outputs for schema integrity, join coverage, target sanity, governance presence, and boundary flags.",
        "",
        "## Summary",
        "",
        markdown_table(summary_rows, ["status", "error_count", "warning_count"]),
        "",
        "## Check counts",
        "",
        markdown_table(check_rows, ["check", "value"]),
        "",
        "## Warnings",
        "",
        markdown_table(warning_rows, ["warning"]) if warning_rows else "_No rows._\n",
        "",
        "## Errors",
        "",
        markdown_table(error_rows, ["error"]) if error_rows else "_No rows._\n",
    ]
    write_text(VALIDATION_REPORT, "\n".join(text) + "\n")


def main() -> None:
    ensure_directories()
    errors: list[str] = []
    warnings: list[str] = []
    check_counts: dict[str, Any] = {}

    check_required_files(errors)
    if errors:
        summary = {
            "generated_at": utc_now(),
            "status": "FAIL",
            "errors": errors,
            "warnings": warnings,
            "check_counts": check_counts,
        }
        write_json(OUT / "pt_stage2_validation_summary.json", summary)
        write_report(summary)
        raise RuntimeError("Stage-2 dataset validation failed before reading packaged files.")

    bus_features = read_csv(REQUIRED_FILES["bus_features"])
    line_features = read_csv(REQUIRED_FILES["line_features"])
    generator_features = read_csv(REQUIRED_FILES["generator_features"])
    line_targets = read_csv(REQUIRED_FILES["line_risk_targets"])
    generator_targets = read_csv(REQUIRED_FILES["generator_risk_targets"])
    provenance_flags = read_csv(REQUIRED_FILES["provenance_flags"])
    manifest = load_manifest()

    for name, df in {
        "bus_features": bus_features,
        "line_features": line_features,
        "generator_features": generator_features,
        "line_risk_targets": line_targets,
        "generator_risk_targets": generator_targets,
        "provenance_flags": provenance_flags,
    }.items():
        check_columns(name, df, errors)

    check_counts["bus_feature_duplicates"] = check_unique(bus_features, "bus_id", "bus_features", errors)
    check_counts["line_feature_duplicates"] = check_unique(line_features, "line_id", "line_features", errors)
    check_counts["generator_feature_duplicates"] = check_unique(generator_features, "generator_id", "generator_features", errors)

    line_name_set = set(line_features["line_name"].astype(str))
    line_target_missing = int((~line_targets["line_name"].astype(str).isin(line_name_set)).sum())
    if line_target_missing:
        add_error(errors, f"line_risk_targets has {line_target_missing} rows that do not map to line_features.line_name")
    check_counts["line_target_missing_feature_refs"] = line_target_missing

    generator_id_set = set(generator_features["generator_id"].astype(str))
    generator_target_missing = int((~generator_targets["generator_id"].astype(str).isin(generator_id_set)).sum())
    if generator_target_missing:
        add_error(errors, f"generator_risk_targets has {generator_target_missing} rows that do not map to generator_features.generator_id")
    check_counts["generator_target_missing_feature_refs"] = generator_target_missing

    governed_present = set(line_features["line_name"].astype(str)) & REQUIRED_GOVERNED_LINES
    missing_governed = sorted(REQUIRED_GOVERNED_LINES - governed_present)
    if missing_governed:
        add_error(errors, f"Missing governed benchmark lines in line_features: {', '.join(missing_governed)}")
    check_counts["missing_governed_lines"] = len(missing_governed)

    negative_line_loading = int((pd.to_numeric(line_targets["metric_max_loading_percent"], errors="coerce") < 0).sum())
    negative_dispatch = int((pd.to_numeric(generator_targets["dispatch_mw"], errors="coerce") < 0).sum())
    if negative_line_loading:
        add_error(errors, f"line_risk_targets contains {negative_line_loading} negative metric_max_loading_percent rows")
    if negative_dispatch:
        add_error(errors, f"generator_risk_targets contains {negative_dispatch} negative dispatch_mw rows")
    check_counts["negative_line_loading_rows"] = negative_line_loading
    check_counts["negative_generator_dispatch_rows"] = negative_dispatch

    top_congested_without_threshold = int((line_targets["top_congested_flag"].astype(str).str.lower() == "true").sum())
    check_counts["top_congested_true_rows"] = top_congested_without_threshold

    top_dispatch_without_positive = int(
        ((generator_targets["top_dispatch_flag"].astype(str).str.lower() == "true")
         & (pd.to_numeric(generator_targets["dispatch_mw"], errors="coerce") <= 0)).sum()
    )
    if top_dispatch_without_positive:
        add_error(errors, f"generator_risk_targets has {top_dispatch_without_positive} top_dispatch_flag rows with non-positive dispatch_mw")
    check_counts["invalid_top_dispatch_rows"] = top_dispatch_without_positive

    provenance_entity_types = set(provenance_flags["entity_type"].astype(str))
    missing_entity_types = sorted({"bus", "line", "generator"} - provenance_entity_types)
    if missing_entity_types:
        add_error(errors, f"provenance_flags missing entity types: {', '.join(missing_entity_types)}")
    check_counts["missing_provenance_entity_types"] = len(missing_entity_types)

    if bool(manifest.get("publication_allowed", True)):
        add_error(errors, "Manifest publication_allowed should remain false")
    if not bool(manifest.get("diagnostic_only", False)):
        add_error(errors, "Manifest diagnostic_only should remain true")
    if bool(manifest.get("operator_grade_ready", True)):
        add_error(errors, "Manifest operator_grade_ready should remain false")
    if bool(manifest.get("ml_ready", True)):
        add_error(errors, "Manifest ml_ready should remain false")

    summary = {
        "generated_at": utc_now(),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "check_counts": check_counts,
    }
    write_json(OUT / "pt_stage2_validation_summary.json", summary)
    write_report(summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if errors:
        raise RuntimeError("Stage-2 dataset validation failed.")


if __name__ == "__main__":
    main()
