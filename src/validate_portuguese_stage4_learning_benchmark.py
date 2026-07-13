from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import ensure_directories, markdown_table, utc_now, write_json, write_text

OUT = config.PROCESSED_DIR / "dataset_release_stage4"
REPORTS = config.REPORTS_DIR
VALIDATION_REPORT = REPORTS / "95_stage4_learning_benchmark_validation.md"

REQUIRED_FILES = {
    "bus_node_features": OUT / "pt_stage4_bus_node_features.csv",
    "line_edge_features": OUT / "pt_stage4_line_edge_features.csv",
    "generator_node_features": OUT / "pt_stage4_generator_node_features.csv",
    "generator_bus_link_features": OUT / "pt_stage4_generator_bus_link_features.csv",
    "line_risk_benchmark_samples": OUT / "pt_stage4_line_risk_benchmark_samples.csv",
    "generator_risk_benchmark_samples": OUT / "pt_stage4_generator_risk_benchmark_samples.csv",
    "task_registry": OUT / "pt_stage4_task_registry.csv",
    "split_registry": OUT / "pt_stage4_split_registry.csv",
    "line_sample_splits": OUT / "pt_stage4_line_sample_splits.csv",
    "generator_sample_splits": OUT / "pt_stage4_generator_sample_splits.csv",
    "label_support_audit": OUT / "pt_stage4_label_support_audit.csv",
    "entity_registry": OUT / "pt_stage4_entity_registry.csv",
    "sample_registry": OUT / "pt_stage4_sample_registry.csv",
    "manifest": OUT / "pt_stage4_manifest.json",
}

REQUIRED_COLUMNS = {
    "bus_node_features": {"node_id", "stage4_benchmark_eligible", "governance_sensitive_flag", "leakage_group_id", "source_release_id"},
    "line_edge_features": {"edge_id", "line_id", "stage4_benchmark_eligible", "governance_sensitive_flag", "leakage_group_id", "source_release_id"},
    "generator_node_features": {"node_id", "generator_id", "stage4_benchmark_eligible", "governance_sensitive_flag", "leakage_group_id", "source_release_id"},
    "generator_bus_link_features": {"edge_id", "generator_id", "stage4_benchmark_eligible", "governance_sensitive_flag", "leakage_group_id", "source_release_id"},
    "line_risk_benchmark_samples": {"sample_id", "line_id", "benchmark_eligible", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id", "line_overload_binary_classification_target", "line_loading_regression_target", "line_relative_high_stress_classification_target"},
    "generator_risk_benchmark_samples": {"sample_id", "generator_id", "benchmark_eligible", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id", "generator_top_dispatch_classification_target", "generator_dispatch_regression_target", "generator_relative_high_dispatch_classification_target"},
    "task_registry": {"task_name", "target_column", "task_type", "eligible_table", "recommended_split_id", "eligible_subset", "benchmark_role", "task_readiness", "evaluation_protocol", "headline_eligible"},
    "split_registry": {"split_id", "task_name", "split_family", "unit_of_separation", "leakage_policy", "is_primary_recommended_split"},
    "line_sample_splits": {"split_id", "sample_id", "partition", "group_key", "leakage_group_id", "challenge_subset"},
    "generator_sample_splits": {"split_id", "sample_id", "partition", "group_key", "leakage_group_id", "challenge_subset"},
    "label_support_audit": {"task_name", "task_type", "target_column", "subset", "total_rows", "total_entities", "positive_rows", "positive_entities", "support_status", "recommended_split_id", "recommended_split_unit", "leakage_groups_crossing_partitions"},
    "entity_registry": {"registry_id", "entity_type", "entity_id", "benchmark_eligible", "provenance_complete", "leakage_group_id", "split_eligible"},
    "sample_registry": {"registry_id", "sample_type", "sample_id", "benchmark_eligible", "benchmark_core_candidate", "provenance_complete", "leakage_group_id", "split_eligible"},
}


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def load_manifest() -> dict[str, Any]:
    with (OUT / "pt_stage4_manifest.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def add_error(errors: list[str], message: str) -> None:
    errors.append(message)


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    normalized = str(value).strip().lower()
    if normalized not in {"true", "false", "1", "0", "yes", "no", "y", "n"}:
        raise ValueError(f"Invalid boolean value: {value!r}")
    return normalized in {"true", "1", "yes", "y"}


def check_required_files(errors: list[str]) -> None:
    for name, path in REQUIRED_FILES.items():
        if not path.exists():
            add_error(errors, f"Missing required stage-4 file: {name} -> {path}")


def check_columns(name: str, df: pd.DataFrame, errors: list[str]) -> None:
    missing = sorted(REQUIRED_COLUMNS.get(name, set()) - set(df.columns))
    if missing:
        add_error(errors, f"{name} missing required columns: {', '.join(missing)}")


def check_unique(df: pd.DataFrame, column: str, label: str, errors: list[str]) -> int:
    duplicates = int(df[column].astype(str).duplicated().sum()) if column in df.columns else 0
    if duplicates:
        add_error(errors, f"{label} has {duplicates} duplicate key rows in column {column}")
    return duplicates


def check_unique_pair(df: pd.DataFrame, columns: list[str], label: str, errors: list[str]) -> int:
    duplicates = int(df[columns].astype(str).duplicated().sum()) if set(columns).issubset(df.columns) else 0
    if duplicates:
        add_error(errors, f"{label} has {duplicates} duplicate key rows across {', '.join(columns)}")
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
        "# 95 Stage-4 Learning Benchmark Validation",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: validate the stage-4 learning-ready benchmark package for schema integrity, referential integrity, task/split registry consistency, leakage controls, label sanity, and boundary flags.",
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
        write_json(OUT / "pt_stage4_validation_summary.json", summary)
        write_report(summary)
        raise RuntimeError("Stage-4 learning benchmark validation failed before reading packaged files.")

    bus_nodes = read_csv(REQUIRED_FILES["bus_node_features"])
    line_edges = read_csv(REQUIRED_FILES["line_edge_features"])
    generator_nodes = read_csv(REQUIRED_FILES["generator_node_features"])
    generator_links = read_csv(REQUIRED_FILES["generator_bus_link_features"])
    line_samples = read_csv(REQUIRED_FILES["line_risk_benchmark_samples"])
    generator_samples = read_csv(REQUIRED_FILES["generator_risk_benchmark_samples"])
    task_registry = read_csv(REQUIRED_FILES["task_registry"])
    split_registry = read_csv(REQUIRED_FILES["split_registry"])
    line_splits = read_csv(REQUIRED_FILES["line_sample_splits"])
    generator_splits = read_csv(REQUIRED_FILES["generator_sample_splits"])
    label_support = read_csv(REQUIRED_FILES["label_support_audit"])
    entity_registry = read_csv(REQUIRED_FILES["entity_registry"])
    sample_registry = read_csv(REQUIRED_FILES["sample_registry"])
    manifest = load_manifest()

    for name, df in {
        "bus_node_features": bus_nodes,
        "line_edge_features": line_edges,
        "generator_node_features": generator_nodes,
        "generator_bus_link_features": generator_links,
        "line_risk_benchmark_samples": line_samples,
        "generator_risk_benchmark_samples": generator_samples,
        "task_registry": task_registry,
        "split_registry": split_registry,
        "line_sample_splits": line_splits,
        "generator_sample_splits": generator_splits,
        "label_support_audit": label_support,
        "entity_registry": entity_registry,
        "sample_registry": sample_registry,
    }.items():
        check_columns(name, df, errors)
        unnamed = [col for col in df.columns.astype(str) if col.startswith("Unnamed:")]
        if unnamed:
            add_error(errors, f"{name} contains unnamed columns: {', '.join(unnamed)}")

    check_counts["task_registry_duplicates"] = check_unique(task_registry, "task_name", "task_registry", errors)
    check_counts["split_registry_duplicates"] = check_unique(split_registry, "split_id", "split_registry", errors)
    check_counts["line_sample_duplicates"] = check_unique(line_samples, "sample_id", "line_risk_benchmark_samples", errors)
    check_counts["generator_sample_duplicates"] = check_unique(generator_samples, "sample_id", "generator_risk_benchmark_samples", errors)
    check_counts["line_split_duplicates"] = check_unique_pair(line_splits, ["split_id", "sample_id"], "line_sample_splits", errors)
    check_counts["generator_split_duplicates"] = check_unique_pair(generator_splits, ["split_id", "sample_id"], "generator_sample_splits", errors)

    line_sample_ids = set(line_samples["sample_id"].astype(str))
    generator_sample_ids = set(generator_samples["sample_id"].astype(str))
    line_split_missing = int((~line_splits["sample_id"].astype(str).isin(line_sample_ids)).sum())
    generator_split_missing = int((~generator_splits["sample_id"].astype(str).isin(generator_sample_ids)).sum())
    if line_split_missing:
        add_error(errors, f"line_sample_splits has {line_split_missing} rows referencing missing line sample_ids")
    if generator_split_missing:
        add_error(errors, f"generator_sample_splits has {generator_split_missing} rows referencing missing generator sample_ids")
    check_counts["line_split_missing_sample_refs"] = line_split_missing
    check_counts["generator_split_missing_sample_refs"] = generator_split_missing

    valid_tables = {
        "pt_stage4_line_risk_benchmark_samples.csv": set(line_samples.columns),
        "pt_stage4_generator_risk_benchmark_samples.csv": set(generator_samples.columns),
    }
    invalid_task_targets = 0
    invalid_task_splits = 0
    for _, row in task_registry.iterrows():
        table = str(row["eligible_table"])
        target = str(row["target_column"])
        split_id = str(row["recommended_split_id"])
        if target not in valid_tables.get(table, set()):
            invalid_task_targets += 1
            add_error(errors, f"task_registry target_column {target} does not exist in {table}")
        if split_id not in set(split_registry["split_id"].astype(str)):
            invalid_task_splits += 1
            add_error(errors, f"task_registry recommended_split_id {split_id} does not exist in split_registry")
    check_counts["invalid_task_targets"] = invalid_task_targets
    check_counts["invalid_task_splits"] = invalid_task_splits

    line_regression_split = line_splits[line_splits["split_id"].astype(str) == "line_grouped_entity_primary_v1"].copy()
    generator_regression_split = generator_splits[generator_splits["split_id"].astype(str) == "generator_grouped_entity_primary_v1"].copy()
    line_relative_split = line_splits[line_splits["split_id"].astype(str) == "line_grouped_entity_relative_stress_v1"].copy()
    generator_relative_split = generator_splits[generator_splits["split_id"].astype(str) == "generator_grouped_entity_relative_dispatch_v1"].copy()
    line_plumbing_split = line_splits[line_splits["split_id"].astype(str) == "line_balanced_recommended_v1"].copy()
    generator_plumbing_split = generator_splits[generator_splits["split_id"].astype(str) == "generator_balanced_recommended_v1"].copy()

    def leakage_violations(df: pd.DataFrame, split_registry: pd.DataFrame) -> int:
        if df.empty:
            return 0
        grouped_split_ids = set(
            split_registry[split_registry["split_family"].astype(str).str.startswith("grouped_entity")]["split_id"].astype(str)
        )
        scoped = df[df["split_id"].astype(str).isin(grouped_split_ids)].copy()
        if scoped.empty:
            return 0
        return int(scoped.groupby(["split_id", "leakage_group_id"])["partition"].nunique().gt(1).sum())

    line_leakage = leakage_violations(line_splits, split_registry)
    generator_leakage = leakage_violations(generator_splits, split_registry)
    if line_leakage:
        add_error(errors, f"line_sample_splits has {line_leakage} leakage groups spanning multiple partitions")
    if generator_leakage:
        add_error(errors, f"generator_sample_splits has {generator_leakage} leakage groups spanning multiple partitions")
    check_counts["line_leakage_group_violations"] = line_leakage
    check_counts["generator_leakage_group_violations"] = generator_leakage

    excluded_line_in_challenge = int((line_regression_split["challenge_subset"].astype(str) != "benchmark_core").sum())
    excluded_generator_in_challenge = int((generator_regression_split["challenge_subset"].astype(str) != "benchmark_core").sum())
    if excluded_line_in_challenge:
        add_error(errors, f"line grouped challenge split contains {excluded_line_in_challenge} governance-sensitive challenge rows")
    if excluded_generator_in_challenge:
        add_error(errors, f"generator grouped challenge split contains {excluded_generator_in_challenge} governance-sensitive challenge rows")
    check_counts["line_grouped_challenge_rows"] = excluded_line_in_challenge
    check_counts["generator_grouped_challenge_rows"] = excluded_generator_in_challenge

    negative_line_targets = int((pd.to_numeric(line_samples["line_loading_regression_target"], errors="coerce") < 0).sum())
    invalid_generator_top_dispatch = int(
        ((generator_samples["generator_top_dispatch_classification_target"].astype(str).str.lower() == "true")
         & (pd.to_numeric(generator_samples["generator_dispatch_regression_target"], errors="coerce") <= 0)).sum()
    )
    if negative_line_targets:
        add_error(errors, f"line_risk_benchmark_samples contains {negative_line_targets} negative regression targets")
    if invalid_generator_top_dispatch:
        add_error(errors, f"generator_risk_benchmark_samples has {invalid_generator_top_dispatch} top-dispatch rows with non-positive dispatch")
    check_counts["negative_line_targets"] = negative_line_targets
    check_counts["invalid_generator_top_dispatch_rows"] = invalid_generator_top_dispatch

    required_partitions = {"train", "validation", "test"}
    split_partition_checks = {
        "line_regression": line_regression_split,
        "generator_regression": generator_regression_split,
        "line_relative_classification": line_relative_split,
        "generator_relative_classification": generator_relative_split,
        "line_rare_event_plumbing": line_plumbing_split,
        "generator_rare_event_plumbing": generator_plumbing_split,
    }
    for label, split_df in split_partition_checks.items():
        missing_partitions = required_partitions - set(split_df["partition"].astype(str))
        if missing_partitions:
            add_error(errors, f"{label} split is missing partitions: {', '.join(sorted(missing_partitions))}")
        check_counts[f"{label}_missing_partitions"] = int(len(missing_partitions))

    def partitions_without_positive(
        samples: pd.DataFrame,
        split_df: pd.DataFrame,
        target_column: str,
    ) -> int:
        joined = samples.merge(split_df[["sample_id", "partition"]], on="sample_id", how="inner")
        missing = 0
        for partition in ["train", "validation", "test"]:
            scoped = joined[joined["partition"].astype(str) == partition]
            if scoped.empty or not scoped[target_column].apply(as_bool).any():
                missing += 1
        return missing

    line_relative_missing_positive = partitions_without_positive(
        line_samples,
        line_relative_split,
        "line_relative_high_stress_classification_target",
    )
    generator_relative_missing_positive = partitions_without_positive(
        generator_samples,
        generator_relative_split,
        "generator_relative_high_dispatch_classification_target",
    )
    if line_relative_missing_positive:
        add_error(errors, "line relative-stress grouped split lacks positive support in one or more partitions")
    if generator_relative_missing_positive:
        add_error(errors, "generator relative-dispatch grouped split lacks positive support in one or more partitions")
    check_counts["line_relative_grouped_partitions_without_positive_support"] = line_relative_missing_positive
    check_counts["generator_relative_grouped_partitions_without_positive_support"] = generator_relative_missing_positive

    support_index = label_support.set_index(["task_name", "subset"])
    expected_support = {
        ("line_overload_binary_classification", "benchmark_core"): "NO_POSITIVE_ENTITIES",
        ("generator_top_dispatch_classification", "benchmark_core"): "INSUFFICIENT_FOR_THREE_WAY_GROUPED",
        ("line_relative_high_stress_classification", "benchmark_core"): "LIMITED_GROUPED_SUPPORT",
        ("generator_relative_high_dispatch_classification", "benchmark_core"): "LIMITED_GROUPED_SUPPORT",
    }
    support_status_mismatches = 0
    for key, expected_status in expected_support.items():
        if key not in support_index.index or str(support_index.loc[key, "support_status"]) != expected_status:
            support_status_mismatches += 1
            add_error(errors, f"label support audit status mismatch for {key[0]} / {key[1]}")
    check_counts["label_support_status_mismatches"] = support_status_mismatches

    challenge_headline_rows = task_registry[
        task_registry["benchmark_role"].astype(str).eq("CHALLENGE_INSUFFICIENT_SUPPORT")
        & task_registry["headline_eligible"].apply(as_bool)
    ]
    if not challenge_headline_rows.empty:
        add_error(errors, "insufficient-support challenge tasks must not be headline eligible")
    check_counts["insufficient_support_tasks_marked_headline"] = int(len(challenge_headline_rows))

    sample_registry_missing = int((~sample_registry["sample_id"].astype(str).isin(line_sample_ids | generator_sample_ids)).sum())
    if sample_registry_missing:
        add_error(errors, f"sample_registry has {sample_registry_missing} rows referencing unknown sample_ids")
    check_counts["sample_registry_missing_sample_refs"] = sample_registry_missing

    manifest_row_counts = manifest.get("table_row_counts", {})
    expected_row_counts = {
        "pt_stage4_bus_node_features.csv": int(len(bus_nodes)),
        "pt_stage4_line_edge_features.csv": int(len(line_edges)),
        "pt_stage4_generator_node_features.csv": int(len(generator_nodes)),
        "pt_stage4_generator_bus_link_features.csv": int(len(generator_links)),
        "pt_stage4_line_risk_benchmark_samples.csv": int(len(line_samples)),
        "pt_stage4_generator_risk_benchmark_samples.csv": int(len(generator_samples)),
        "pt_stage4_task_registry.csv": int(len(task_registry)),
        "pt_stage4_split_registry.csv": int(len(split_registry)),
        "pt_stage4_line_sample_splits.csv": int(len(line_splits)),
        "pt_stage4_generator_sample_splits.csv": int(len(generator_splits)),
        "pt_stage4_label_support_audit.csv": int(len(label_support)),
        "pt_stage4_entity_registry.csv": int(len(entity_registry)),
        "pt_stage4_sample_registry.csv": int(len(sample_registry)),
    }
    manifest_row_count_mismatches = 0
    for name, expected in expected_row_counts.items():
        observed = manifest_row_counts.get(name)
        if observed != expected:
            manifest_row_count_mismatches += 1
            add_error(errors, f"Manifest table_row_counts mismatch for {name}: expected {expected}, observed {observed}")
    check_counts["manifest_row_count_mismatches"] = manifest_row_count_mismatches

    if bool(manifest.get("publication_allowed", True)):
        add_error(errors, "Manifest publication_allowed should remain false")
    if not bool(manifest.get("diagnostic_only", False)):
        add_error(errors, "Manifest diagnostic_only should remain true")
    if bool(manifest.get("operator_grade_ready", True)):
        add_error(errors, "Manifest operator_grade_ready should remain false")
    if not bool(manifest.get("ml_ready", False)):
        add_error(errors, "Manifest ml_ready should be true for the stage-4 learning-ready benchmark release")

    summary = {
        "generated_at": utc_now(),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "check_counts": check_counts,
    }
    write_json(OUT / "pt_stage4_validation_summary.json", summary)
    write_report(summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if errors:
        raise RuntimeError("Stage-4 learning benchmark validation failed.")


if __name__ == "__main__":
    main()
