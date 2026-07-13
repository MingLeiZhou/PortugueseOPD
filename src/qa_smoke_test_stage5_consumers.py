from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import ensure_directories, utc_now, write_json

OUT = config.PROCESSED_DIR / "dataset_release_stage5"
SUMMARY_PATH = OUT / "pt_stage5_qa_smoke_summary.json"

REQUIRED_FILES = {
    "graph_node_adapter": OUT / "pt_stage5_graph_node_adapter.csv",
    "graph_edge_adapter": OUT / "pt_stage5_graph_edge_adapter.csv",
    "generator_node_adapter": OUT / "pt_stage5_generator_node_adapter.csv",
    "generator_link_adapter": OUT / "pt_stage5_generator_link_adapter.csv",
    "line_supervision_adapter": OUT / "pt_stage5_line_supervision_adapter.csv",
    "generator_supervision_adapter": OUT / "pt_stage5_generator_supervision_adapter.csv",
    "feature_contract": OUT / "pt_stage5_feature_contract.csv",
    "adapter_registry": OUT / "pt_stage5_adapter_registry.csv",
    "preprocessing_contract": OUT / "pt_stage5_train_only_preprocessing_contract.json",
    "manifest": OUT / "pt_stage5_manifest.json",
    "validation_summary": OUT / "pt_stage5_validation_summary.json",
}

EXPECTED_VIEWS = {
    "homogeneous_bus_line_graph",
    "heterogeneous_generator_sidecar",
    "line_risk_supervision",
    "generator_risk_supervision",
}
EXPECTED_LINE_TASKS = {
    "line_overload_binary_classification",
    "line_loading_regression",
    "line_relative_high_stress_classification",
}
EXPECTED_GENERATOR_TASKS = {
    "generator_top_dispatch_classification",
    "generator_dispatch_regression",
    "generator_relative_high_dispatch_classification",
}
FORBIDDEN_FEATURE_COLUMNS = {
    "target_value",
    "task_name",
    "recommended_partition",
    "grouped_challenge_partition",
    "relative_classification_partition",
    "balanced_plumbing_partition",
    "scenario_holdout_partition",
    "leakage_group_id",
    "source_release_id",
}

FORBIDDEN_PREPROCESSING_COLUMNS = {
    "task_name",
    "recommended_partition",
    "grouped_challenge_partition",
    "relative_classification_partition",
    "balanced_plumbing_partition",
    "scenario_holdout_partition",
    "leakage_group_id",
    "source_release_id",
    "target_column",
    "target_value_column",
}


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
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
            add_error(errors, f"Missing required stage-5 QA file: {name} -> {path}")


def contract_row(feature_contract: pd.DataFrame, table_name: str, column_name: str) -> pd.DataFrame:
    return feature_contract[
        (feature_contract["table_name"].astype(str) == table_name)
        & (feature_contract["column_name"].astype(str) == column_name)
    ]


def ensure_partition_health(
    *,
    df: pd.DataFrame,
    task_names: set[str],
    recommended_splits: dict[str, str],
    label: str,
    errors: list[str],
    check_counts: dict[str, Any],
) -> None:
    task_values = set(df["task_name"].astype(str))
    missing_tasks = task_names - task_values
    if missing_tasks:
        add_error(errors, f"Stage-5 {label} supervision missing tasks: {', '.join(sorted(missing_tasks))}")

    expected_split = df["task_name"].astype(str).map(recommended_splits)
    wrong_split = int((df["recommended_split_id"].astype(str) != expected_split).sum())
    if wrong_split:
        add_error(errors, f"Stage-5 {label} supervision has {wrong_split} rows with unexpected recommended split ids")

    invalid_recommended_partitions = int((~df["recommended_partition"].astype(str).isin({"train", "validation", "test"})).sum())
    grouped_raw = df["grouped_challenge_partition"].fillna("").astype(str)
    grouped_required = df["benchmark_core_candidate"].apply(as_bool)
    invalid_grouped_partitions = int((grouped_required & ~grouped_raw.isin({"train", "validation", "test"})).sum())
    invalid_grouped_governance_rows = int((~grouped_required & grouped_raw.ne("")).sum())
    invalid_scenario_partitions = int((~df["scenario_holdout_partition"].astype(str).isin({"train", "validation", "test"})).sum())
    if invalid_recommended_partitions:
        add_error(errors, f"Stage-5 {label} supervision has invalid recommended partitions")
    if invalid_grouped_partitions:
        add_error(errors, f"Stage-5 {label} supervision has benchmark-core rows without valid grouped challenge partitions")
    if invalid_grouped_governance_rows:
        add_error(errors, f"Stage-5 {label} supervision has governance-sensitive rows unexpectedly assigned to grouped challenge partitions")
    if invalid_scenario_partitions:
        add_error(errors, f"Stage-5 {label} supervision has invalid scenario holdout partitions")

    empty_partition_count = 0
    for task_name in sorted(task_names):
        scoped = df[df["task_name"].astype(str) == task_name]
        for partition in ["train", "validation", "test"]:
            if scoped[scoped["recommended_partition"].astype(str) == partition].empty:
                empty_partition_count += 1
                add_error(errors, f"Stage-5 {label} supervision task {task_name} missing recommended partition rows for {partition}")

    check_counts[f"{label}_rows"] = int(len(df))
    check_counts[f"{label}_unexpected_split_rows"] = wrong_split
    check_counts[f"{label}_invalid_recommended_partitions"] = invalid_recommended_partitions
    check_counts[f"{label}_invalid_grouped_partitions"] = invalid_grouped_partitions
    check_counts[f"{label}_invalid_scenario_partitions"] = invalid_scenario_partitions
    check_counts[f"{label}_empty_task_partition_slots"] = empty_partition_count


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
        write_json(SUMMARY_PATH, summary)
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        raise RuntimeError("Stage-5 QA smoke test failed before reading required files.")

    graph_nodes = read_csv(REQUIRED_FILES["graph_node_adapter"])
    graph_edges = read_csv(REQUIRED_FILES["graph_edge_adapter"])
    generator_nodes = read_csv(REQUIRED_FILES["generator_node_adapter"])
    generator_links = read_csv(REQUIRED_FILES["generator_link_adapter"])
    line_supervision = read_csv(REQUIRED_FILES["line_supervision_adapter"])
    generator_supervision = read_csv(REQUIRED_FILES["generator_supervision_adapter"])
    feature_contract = read_csv(REQUIRED_FILES["feature_contract"])
    adapter_registry = read_csv(REQUIRED_FILES["adapter_registry"])
    preprocessing_contract = read_json(REQUIRED_FILES["preprocessing_contract"])
    manifest = read_json(REQUIRED_FILES["manifest"])
    validation_summary = read_json(REQUIRED_FILES["validation_summary"])

    if validation_summary.get("status") != "PASS":
        add_error(errors, "Stage-5 validation summary must be PASS before consumer smoke testing")
    if bool(manifest.get("publication_allowed", True)):
        add_error(errors, "Stage-5 manifest publication_allowed should remain false")
    if not bool(manifest.get("diagnostic_only", False)):
        add_error(errors, "Stage-5 manifest diagnostic_only should remain true")
    if bool(manifest.get("operator_grade_ready", True)):
        add_error(errors, "Stage-5 manifest operator_grade_ready should remain false")
    if not bool(manifest.get("ml_ready", False)):
        add_error(errors, "Stage-5 manifest ml_ready should remain true")

    adapter_views = set(adapter_registry["adapter_view"].astype(str))
    missing_views = EXPECTED_VIEWS - adapter_views
    if missing_views:
        add_error(errors, f"Stage-5 adapter registry missing expected views: {', '.join(sorted(missing_views))}")

    required_tables = set(path.name for key, path in REQUIRED_FILES.items() if key not in {"preprocessing_contract", "manifest", "validation_summary"})
    registry_tables = set(adapter_registry["table_name"].astype(str))
    missing_registry_tables = {
        "pt_stage5_graph_node_adapter.csv",
        "pt_stage5_graph_edge_adapter.csv",
        "pt_stage5_generator_node_adapter.csv",
        "pt_stage5_generator_link_adapter.csv",
        "pt_stage5_line_supervision_adapter.csv",
        "pt_stage5_generator_supervision_adapter.csv",
    } - registry_tables
    if missing_registry_tables:
        add_error(errors, f"Stage-5 adapter registry missing expected table rows: {', '.join(sorted(missing_registry_tables))}")
    check_counts["adapter_registry_view_count"] = int(len(adapter_views))

    graph_node_ids = set(graph_nodes["node_id"].astype(str))
    generator_node_ids = set(generator_nodes["node_id"].astype(str))
    edge_source_missing = int((~graph_edges["source_node_id"].astype(str).isin(graph_node_ids)).sum())
    edge_target_missing = int((~graph_edges["target_node_id"].astype(str).isin(graph_node_ids)).sum())
    generator_link_source_missing = int((~generator_links["source_node_id"].astype(str).isin(generator_node_ids)).sum())
    generator_link_target_missing = int((~generator_links["target_node_id"].astype(str).isin(graph_node_ids)).sum())
    if edge_source_missing:
        add_error(errors, f"Stage-5 graph edge adapter has {edge_source_missing} missing source node refs")
    if edge_target_missing:
        add_error(errors, f"Stage-5 graph edge adapter has {edge_target_missing} missing target node refs")
    if generator_link_source_missing:
        add_error(errors, f"Stage-5 generator link adapter has {generator_link_source_missing} missing generator node refs")
    if generator_link_target_missing:
        add_error(errors, f"Stage-5 generator link adapter has {generator_link_target_missing} missing graph node refs")
    check_counts["graph_edge_missing_source_refs"] = edge_source_missing
    check_counts["graph_edge_missing_target_refs"] = edge_target_missing
    check_counts["generator_link_missing_source_refs"] = generator_link_source_missing
    check_counts["generator_link_missing_target_refs"] = generator_link_target_missing

    ensure_partition_health(
        df=line_supervision,
        task_names=EXPECTED_LINE_TASKS,
        recommended_splits={
            "line_overload_binary_classification": "line_balanced_recommended_v1",
            "line_loading_regression": "line_grouped_entity_primary_v1",
            "line_relative_high_stress_classification": "line_grouped_entity_relative_stress_v1",
        },
        label="line_supervision",
        errors=errors,
        check_counts=check_counts,
    )
    ensure_partition_health(
        df=generator_supervision,
        task_names=EXPECTED_GENERATOR_TASKS,
        recommended_splits={
            "generator_top_dispatch_classification": "generator_balanced_recommended_v1",
            "generator_dispatch_regression": "generator_grouped_entity_primary_v1",
            "generator_relative_high_dispatch_classification": "generator_grouped_entity_relative_dispatch_v1",
        },
        label="generator_supervision",
        errors=errors,
        check_counts=check_counts,
    )

    def grouped_leakage(df: pd.DataFrame) -> int:
        scoped = df[df["benchmark_role"].astype(str).isin({"PRIMARY", "AUXILIARY_LIMITED_SUPPORT"})]
        return int(
            scoped.groupby(["task_name", "leakage_group_id"])["recommended_partition"]
            .nunique()
            .gt(1)
            .sum()
        )

    line_grouped_leakage = grouped_leakage(line_supervision)
    generator_grouped_leakage = grouped_leakage(generator_supervision)
    if line_grouped_leakage:
        add_error(errors, f"Stage-5 line grouped evaluation has {line_grouped_leakage} leakage groups")
    if generator_grouped_leakage:
        add_error(errors, f"Stage-5 generator grouped evaluation has {generator_grouped_leakage} leakage groups")
    check_counts["line_grouped_evaluation_leakage_groups"] = line_grouped_leakage
    check_counts["generator_grouped_evaluation_leakage_groups"] = generator_grouped_leakage

    def positive_partition_gaps(df: pd.DataFrame, task_name: str) -> int:
        scoped = df[df["task_name"].astype(str) == task_name]
        return sum(
            scoped[scoped["recommended_partition"].astype(str) == partition]["target_value"]
            .apply(as_bool)
            .sum() == 0
            for partition in ["train", "validation", "test"]
        )

    line_relative_gaps = positive_partition_gaps(
        line_supervision, "line_relative_high_stress_classification"
    )
    generator_relative_gaps = positive_partition_gaps(
        generator_supervision, "generator_relative_high_dispatch_classification"
    )
    if line_relative_gaps:
        add_error(errors, "Stage-5 line relative-stress task lacks a positive recommended partition")
    if generator_relative_gaps:
        add_error(errors, "Stage-5 generator relative-dispatch task lacks a positive recommended partition")
    check_counts["line_relative_positive_partition_gaps"] = int(line_relative_gaps)
    check_counts["generator_relative_positive_partition_gaps"] = int(generator_relative_gaps)

    line_target_alignment = int(
        (~line_supervision["task_name"].astype(str).map({
            "line_overload_binary_classification": "line_overload_binary_classification_target",
            "line_loading_regression": "line_loading_regression_target",
            "line_relative_high_stress_classification": "line_relative_high_stress_classification_target",
        }).eq(line_supervision["target_column"].astype(str))).sum()
    )
    generator_target_alignment = int(
        (~generator_supervision["task_name"].astype(str).map({
            "generator_top_dispatch_classification": "generator_top_dispatch_classification_target",
            "generator_dispatch_regression": "generator_dispatch_regression_target",
            "generator_relative_high_dispatch_classification": "generator_relative_high_dispatch_classification_target",
        }).eq(generator_supervision["target_column"].astype(str))).sum()
    )
    if line_target_alignment:
        add_error(errors, f"Stage-5 line supervision has {line_target_alignment} target alignment mismatches")
    if generator_target_alignment:
        add_error(errors, f"Stage-5 generator supervision has {generator_target_alignment} target alignment mismatches")
    check_counts["line_target_alignment_mismatches"] = line_target_alignment
    check_counts["generator_target_alignment_mismatches"] = generator_target_alignment

    invalid_generator_positive_dispatch = int(
        ((generator_supervision["task_name"].astype(str) == "generator_top_dispatch_classification")
         & (generator_supervision["target_value"].astype(str).str.lower() == "true")
         & (pd.to_numeric(generator_supervision["generator_dispatch_regression_target"], errors="coerce") <= 0)).sum()
    )
    if invalid_generator_positive_dispatch:
        add_error(errors, f"Stage-5 generator supervision has {invalid_generator_positive_dispatch} positive classification rows with non-positive dispatch targets")
    check_counts["generator_positive_dispatch_mismatches"] = invalid_generator_positive_dispatch

    adapter_tables = {
        "pt_stage5_graph_node_adapter.csv": graph_nodes,
        "pt_stage5_graph_edge_adapter.csv": graph_edges,
        "pt_stage5_generator_node_adapter.csv": generator_nodes,
        "pt_stage5_generator_link_adapter.csv": generator_links,
        "pt_stage5_line_supervision_adapter.csv": line_supervision,
        "pt_stage5_generator_supervision_adapter.csv": generator_supervision,
    }
    uncovered_columns = 0
    for table_name, df in adapter_tables.items():
        covered = set(feature_contract[feature_contract["table_name"].astype(str) == table_name]["column_name"].astype(str))
        missing = set(df.columns.astype(str)) - covered
        if missing:
            uncovered_columns += len(missing)
            add_error(errors, f"Stage-5 feature contract missing columns for {table_name}: {', '.join(sorted(missing))}")
    check_counts["feature_contract_uncovered_columns"] = uncovered_columns

    forbidden_misclassified = 0
    for column in sorted(FORBIDDEN_FEATURE_COLUMNS):
        candidates = feature_contract[feature_contract["column_name"].astype(str) == column]
        if candidates.empty:
            add_error(errors, f"Stage-5 feature contract does not describe forbidden column {column}")
            forbidden_misclassified += 1
            continue
        if candidates["admissible_as_model_input"].apply(as_bool).any():
            add_error(errors, f"Stage-5 feature contract marks forbidden column {column} as admissible input")
            forbidden_misclassified += 1
    check_counts["forbidden_column_misclassifications"] = forbidden_misclassified

    if not bool(preprocessing_contract.get("policy", {}).get("fit_preprocessing_on_train_only", False)):
        add_error(errors, "Stage-5 preprocessing contract must require train-only preprocessing fit")
    recommended_split_ids = set(preprocessing_contract.get("recommended_split_ids", []))
    expected_splits = {
        "line_balanced_recommended_v1",
        "line_grouped_entity_primary_v1",
        "line_grouped_entity_relative_stress_v1",
        "generator_balanced_recommended_v1",
        "generator_grouped_entity_primary_v1",
        "generator_grouped_entity_relative_dispatch_v1",
    }
    if recommended_split_ids != expected_splits:
        add_error(errors, "Stage-5 preprocessing contract task-specific split ids do not match the expected contract")
    primary_evaluation_splits = set(preprocessing_contract.get("primary_evaluation_split_ids", []))
    expected_primary_splits = expected_splits - {
        "line_balanced_recommended_v1",
        "generator_balanced_recommended_v1",
    }
    if primary_evaluation_splits != expected_primary_splits:
        add_error(errors, "Stage-5 preprocessing contract does not isolate grouped primary/auxiliary evaluation splits")
    forbidden_from_contract = set(preprocessing_contract.get("forbidden_input_columns", []))
    missing_forbidden = FORBIDDEN_PREPROCESSING_COLUMNS - forbidden_from_contract
    if missing_forbidden:
        add_error(errors, f"Stage-5 preprocessing contract missing forbidden input columns: {', '.join(sorted(missing_forbidden))}")
    check_counts["preprocessing_missing_forbidden_columns"] = int(len(missing_forbidden))

    summary = {
        "generated_at": utc_now(),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "check_counts": check_counts,
    }
    write_json(SUMMARY_PATH, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if errors:
        raise RuntimeError("Stage-5 QA consumer smoke test failed.")


if __name__ == "__main__":
    main()
