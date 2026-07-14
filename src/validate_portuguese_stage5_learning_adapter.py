from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import ensure_directories, markdown_table, utc_now, write_json, write_text

OUT = config.PROCESSED_DIR / "dataset_release_stage5"
REPORTS = config.REPORTS_DIR
VALIDATION_REPORT = REPORTS / "97_stage5_learning_adapter_validation.md"

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
}

REQUIRED_COLUMNS = {
    "graph_node_adapter": {"adapter_view", "adapter_entity_type", "node_id", "stage4_benchmark_eligible", "governance_sensitive_flag", "leakage_group_id", "source_release_id"},
    "graph_edge_adapter": {"adapter_view", "adapter_entity_type", "edge_id", "line_id", "source_node_id", "target_node_id", "stage4_benchmark_eligible", "leakage_group_id", "source_release_id"},
    "generator_node_adapter": {"adapter_view", "adapter_entity_type", "node_id", "generator_id", "bus_id", "stage4_benchmark_eligible", "leakage_group_id", "source_release_id"},
    "generator_link_adapter": {"adapter_view", "adapter_entity_type", "edge_id", "generator_id", "source_node_id", "target_node_id", "stage4_benchmark_eligible", "leakage_group_id", "source_release_id"},
    "line_supervision_adapter": {"adapter_view", "adapter_sample_type", "sample_id", "entity_id", "line_id", "edge_id", "task_name", "target_column", "target_value", "recommended_split_id", "recommended_partition", "recommended_split_family", "grouped_challenge_partition", "relative_classification_partition", "balanced_plumbing_partition", "scenario_holdout_partition", "leakage_group_id", "challenge_subset", "benchmark_role", "task_readiness", "evaluation_protocol"},
    "generator_supervision_adapter": {"adapter_view", "adapter_sample_type", "sample_id", "entity_id", "generator_id", "node_id", "task_name", "target_column", "target_value", "recommended_split_id", "recommended_partition", "recommended_split_family", "grouped_challenge_partition", "relative_classification_partition", "balanced_plumbing_partition", "scenario_holdout_partition", "leakage_group_id", "challenge_subset", "benchmark_role", "task_readiness", "evaluation_protocol"},
    "feature_contract": {"table_name", "column_name", "semantic_role", "feature_type", "admissible_as_model_input", "target_only", "split_control_only", "provenance_only", "leakage_sensitive_or_forbidden"},
    "adapter_registry": {"adapter_view", "table_name", "adapter_family", "entity_scope", "intended_use", "supports_tasks", "recommended_split_id", "diagnostic_only", "publication_allowed"},
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
            add_error(errors, f"Missing required stage-5 file: {name} -> {path}")


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
        "# 97 Stage-5 Learning Adapter Validation",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: validate the stage-5 framework-neutral adapter release for schema coverage, upstream referential integrity, split/task consistency, feature-contract completeness, preprocessing-contract consistency, and boundary flags.",
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
        write_json(OUT / "pt_stage5_validation_summary.json", summary)
        write_report(summary)
        raise RuntimeError("Stage-5 learning adapter validation failed before reading packaged files.")

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

    for name, df in {
        "graph_node_adapter": graph_nodes,
        "graph_edge_adapter": graph_edges,
        "generator_node_adapter": generator_nodes,
        "generator_link_adapter": generator_links,
        "line_supervision_adapter": line_supervision,
        "generator_supervision_adapter": generator_supervision,
        "feature_contract": feature_contract,
        "adapter_registry": adapter_registry,
    }.items():
        check_columns(name, df, errors)
        unnamed = [col for col in df.columns.astype(str) if col.startswith("Unnamed:")]
        if unnamed:
            add_error(errors, f"{name} contains unnamed columns: {', '.join(unnamed)}")

    check_counts["graph_node_duplicates"] = check_unique(graph_nodes, "node_id", "graph_node_adapter", errors)
    check_counts["graph_edge_duplicates"] = check_unique(graph_edges, "edge_id", "graph_edge_adapter", errors)
    check_counts["generator_node_duplicates"] = check_unique(generator_nodes, "node_id", "generator_node_adapter", errors)
    check_counts["generator_link_duplicates"] = check_unique(generator_links, "edge_id", "generator_link_adapter", errors)
    check_counts["line_supervision_duplicates"] = check_unique_pair(line_supervision, ["sample_id", "task_name"], "line_supervision_adapter", errors)
    check_counts["generator_supervision_duplicates"] = check_unique_pair(generator_supervision, ["sample_id", "task_name"], "generator_supervision_adapter", errors)
    check_counts["feature_contract_duplicates"] = check_unique_pair(feature_contract, ["table_name", "column_name"], "feature_contract", errors)
    check_counts["adapter_registry_duplicates"] = check_unique_pair(adapter_registry, ["adapter_view", "table_name"], "adapter_registry", errors)

    stage3 = config.PROCESSED_DIR / "dataset_release_stage3"
    stage4 = config.PROCESSED_DIR / "dataset_release_stage4"
    stage3_graph_nodes = read_csv(stage3 / "pt_stage3_graph_nodes.csv")
    stage3_graph_edges = read_csv(stage3 / "pt_stage3_graph_edges.csv")
    stage3_generator_nodes = read_csv(stage3 / "pt_stage3_generator_nodes.csv")
    stage3_generator_links = read_csv(stage3 / "pt_stage3_generator_bus_links.csv")
    stage4_line_samples = read_csv(stage4 / "pt_stage4_line_risk_benchmark_samples.csv")
    stage4_generator_samples = read_csv(stage4 / "pt_stage4_generator_risk_benchmark_samples.csv")
    stage4_task_registry = read_csv(stage4 / "pt_stage4_task_registry.csv")
    stage4_split_registry = read_csv(stage4 / "pt_stage4_split_registry.csv")

    graph_node_ids = set(stage3_graph_nodes["node_id"].astype(str))
    graph_edge_ids = set(stage3_graph_edges["edge_id"].astype(str))
    generator_node_ids = set(stage3_generator_nodes["node_id"].astype(str))
    generator_link_ids = set(stage3_generator_links["edge_id"].astype(str))
    line_sample_ids = set(stage4_line_samples["sample_id"].astype(str))
    generator_sample_ids = set(stage4_generator_samples["sample_id"].astype(str))
    task_names = set(stage4_task_registry["task_name"].astype(str))
    split_ids = set(stage4_split_registry["split_id"].astype(str))
    recommended_split_ids = set(stage4_task_registry["recommended_split_id"].astype(str))

    graph_node_missing = int((~graph_nodes["node_id"].astype(str).isin(graph_node_ids)).sum())
    graph_edge_missing = int((~graph_edges["edge_id"].astype(str).isin(graph_edge_ids)).sum())
    generator_node_missing = int((~generator_nodes["node_id"].astype(str).isin(generator_node_ids)).sum())
    generator_link_missing = int((~generator_links["edge_id"].astype(str).isin(generator_link_ids)).sum())
    if graph_node_missing:
        add_error(errors, f"graph_node_adapter has {graph_node_missing} rows not mapping to stage-3 graph nodes")
    if graph_edge_missing:
        add_error(errors, f"graph_edge_adapter has {graph_edge_missing} rows not mapping to stage-3 graph edges")
    if generator_node_missing:
        add_error(errors, f"generator_node_adapter has {generator_node_missing} rows not mapping to stage-3 generator nodes")
    if generator_link_missing:
        add_error(errors, f"generator_link_adapter has {generator_link_missing} rows not mapping to stage-3 generator links")
    check_counts["graph_node_missing_stage3_refs"] = graph_node_missing
    check_counts["graph_edge_missing_stage3_refs"] = graph_edge_missing
    check_counts["generator_node_missing_stage3_refs"] = generator_node_missing
    check_counts["generator_link_missing_stage3_refs"] = generator_link_missing

    line_sample_missing = int((~line_supervision["sample_id"].astype(str).isin(line_sample_ids)).sum())
    generator_sample_missing = int((~generator_supervision["sample_id"].astype(str).isin(generator_sample_ids)).sum())
    line_task_missing = int((~line_supervision["task_name"].astype(str).isin(task_names)).sum())
    generator_task_missing = int((~generator_supervision["task_name"].astype(str).isin(task_names)).sum())
    line_split_missing = int((~line_supervision["recommended_split_id"].astype(str).isin(split_ids)).sum())
    generator_split_missing = int((~generator_supervision["recommended_split_id"].astype(str).isin(split_ids)).sum())
    if line_sample_missing:
        add_error(errors, f"line_supervision_adapter has {line_sample_missing} rows referencing missing stage-4 line sample_ids")
    if generator_sample_missing:
        add_error(errors, f"generator_supervision_adapter has {generator_sample_missing} rows referencing missing stage-4 generator sample_ids")
    if line_task_missing:
        add_error(errors, f"line_supervision_adapter has {line_task_missing} rows referencing missing task names")
    if generator_task_missing:
        add_error(errors, f"generator_supervision_adapter has {generator_task_missing} rows referencing missing task names")
    if line_split_missing:
        add_error(errors, f"line_supervision_adapter has {line_split_missing} rows referencing missing recommended split ids")
    if generator_split_missing:
        add_error(errors, f"generator_supervision_adapter has {generator_split_missing} rows referencing missing recommended split ids")
    check_counts["line_supervision_missing_sample_refs"] = line_sample_missing
    check_counts["generator_supervision_missing_sample_refs"] = generator_sample_missing
    check_counts["line_supervision_missing_task_refs"] = line_task_missing
    check_counts["generator_supervision_missing_task_refs"] = generator_task_missing
    check_counts["line_supervision_missing_split_refs"] = line_split_missing
    check_counts["generator_supervision_missing_split_refs"] = generator_split_missing

    invalid_line_partitions = int((~line_supervision["recommended_partition"].astype(str).isin({"train", "validation", "test"})).sum())
    invalid_generator_partitions = int((~generator_supervision["recommended_partition"].astype(str).isin({"train", "validation", "test"})).sum())
    if invalid_line_partitions:
        add_error(errors, f"line_supervision_adapter has {invalid_line_partitions} invalid recommended partitions")
    if invalid_generator_partitions:
        add_error(errors, f"generator_supervision_adapter has {invalid_generator_partitions} invalid recommended partitions")
    check_counts["line_invalid_recommended_partitions"] = invalid_line_partitions
    check_counts["generator_invalid_recommended_partitions"] = invalid_generator_partitions

    def grouped_supervision_leakage(df: pd.DataFrame) -> int:
        scoped = df[df["benchmark_role"].astype(str).isin({"PRIMARY", "AUXILIARY_LIMITED_SUPPORT"})]
        if scoped.empty:
            return 0
        return int(
            scoped.groupby(["task_name", "leakage_group_id"])["recommended_partition"]
            .nunique()
            .gt(1)
            .sum()
        )

    line_grouped_leakage = grouped_supervision_leakage(line_supervision)
    generator_grouped_leakage = grouped_supervision_leakage(generator_supervision)
    if line_grouped_leakage:
        add_error(errors, f"line supervision has {line_grouped_leakage} grouped evaluation entities crossing partitions")
    if generator_grouped_leakage:
        add_error(errors, f"generator supervision has {generator_grouped_leakage} grouped evaluation entities crossing partitions")
    check_counts["line_grouped_evaluation_leakage_groups"] = line_grouped_leakage
    check_counts["generator_grouped_evaluation_leakage_groups"] = generator_grouped_leakage

    def positive_partition_gaps(df: pd.DataFrame, task_name: str) -> int:
        scoped = df[df["task_name"].astype(str) == task_name]
        missing = 0
        for partition in ["train", "validation", "test"]:
            partition_rows = scoped[scoped["recommended_partition"].astype(str) == partition]
            if partition_rows.empty or not partition_rows["target_value"].apply(as_bool).any():
                missing += 1
        return missing

    line_relative_positive_gaps = positive_partition_gaps(
        line_supervision, "line_relative_high_stress_classification"
    )
    generator_relative_positive_gaps = positive_partition_gaps(
        generator_supervision, "generator_relative_high_dispatch_classification"
    )
    if line_relative_positive_gaps:
        add_error(errors, "line relative-stress supervision lacks positive support in a recommended partition")
    if generator_relative_positive_gaps:
        add_error(errors, "generator relative-dispatch supervision lacks positive support in a recommended partition")
    check_counts["line_relative_positive_partition_gaps"] = line_relative_positive_gaps
    check_counts["generator_relative_positive_partition_gaps"] = generator_relative_positive_gaps

    invalid_challenge_headline = int(pd.concat([line_supervision, generator_supervision], ignore_index=True)[
        lambda df: df["benchmark_role"].astype(str).eq("CHALLENGE_INSUFFICIENT_SUPPORT")
        & df["headline_eligible"].apply(as_bool)
    ].shape[0])
    if invalid_challenge_headline:
        add_error(errors, "insufficient-support challenge supervision rows must not be headline eligible")
    check_counts["insufficient_support_headline_rows"] = invalid_challenge_headline

    adapter_tables = {
        "pt_stage5_graph_node_adapter.csv": graph_nodes,
        "pt_stage5_graph_edge_adapter.csv": graph_edges,
        "pt_stage5_generator_node_adapter.csv": generator_nodes,
        "pt_stage5_generator_link_adapter.csv": generator_links,
        "pt_stage5_line_supervision_adapter.csv": line_supervision,
        "pt_stage5_generator_supervision_adapter.csv": generator_supervision,
    }
    uncovered_columns = 0
    invalid_contract_rows = 0
    contract_pairs = set(zip(feature_contract["table_name"].astype(str), feature_contract["column_name"].astype(str)))
    for table_name, df in adapter_tables.items():
        for column in df.columns.astype(str):
            if (table_name, column) not in contract_pairs:
                uncovered_columns += 1
                add_error(errors, f"feature_contract missing coverage for {table_name}.{column}")
    for _, row in feature_contract.iterrows():
        table_name = str(row["table_name"])
        column_name = str(row["column_name"])
        if table_name not in adapter_tables or column_name not in set(adapter_tables[table_name].columns.astype(str)):
            invalid_contract_rows += 1
            add_error(errors, f"feature_contract references unknown adapter column {table_name}.{column_name}")
    check_counts["feature_contract_uncovered_columns"] = uncovered_columns
    check_counts["feature_contract_invalid_rows"] = invalid_contract_rows

    forbidden_misclassified = int(feature_contract[
        feature_contract["column_name"].astype(str).isin(preprocessing_contract.get("forbidden_input_columns", []))
        & feature_contract["admissible_as_model_input"].apply(as_bool)
    ].shape[0])
    if forbidden_misclassified:
        add_error(errors, f"feature_contract marks {forbidden_misclassified} forbidden preprocessing columns as admissible model input")
    check_counts["forbidden_contract_misclassifications"] = forbidden_misclassified

    registry_missing_tables = int((~adapter_registry["table_name"].astype(str).isin(adapter_tables.keys())).sum())
    registry_missing_splits = int((~adapter_registry["recommended_split_id"].fillna("").astype(str).isin(split_ids | {""})).sum())
    if registry_missing_tables:
        add_error(errors, f"adapter_registry has {registry_missing_tables} rows referencing unknown adapter tables")
    if registry_missing_splits:
        add_error(errors, f"adapter_registry has {registry_missing_splits} rows referencing unknown recommended split ids")
    check_counts["adapter_registry_missing_tables"] = registry_missing_tables
    check_counts["adapter_registry_missing_splits"] = registry_missing_splits

    preprocessing_missing_recommended = len(recommended_split_ids - set(preprocessing_contract.get("recommended_split_ids", [])))
    preprocessing_unknown_recommended = len(set(preprocessing_contract.get("recommended_split_ids", [])) - split_ids)
    if preprocessing_missing_recommended:
        add_error(errors, "preprocessing contract does not include all recommended split ids from stage-4 task registry")
    if preprocessing_unknown_recommended:
        add_error(errors, "preprocessing contract references unknown recommended split ids")
    if not bool(preprocessing_contract.get("policy", {}).get("fit_preprocessing_on_train_only", False)):
        add_error(errors, "preprocessing contract must require train-only preprocessing fit")
    check_counts["preprocessing_missing_recommended_splits"] = preprocessing_missing_recommended
    check_counts["preprocessing_unknown_recommended_splits"] = preprocessing_unknown_recommended

    manifest_row_counts = manifest.get("table_row_counts", {})
    expected_row_counts = {
        "pt_stage5_graph_node_adapter.csv": int(len(graph_nodes)),
        "pt_stage5_graph_edge_adapter.csv": int(len(graph_edges)),
        "pt_stage5_generator_node_adapter.csv": int(len(generator_nodes)),
        "pt_stage5_generator_link_adapter.csv": int(len(generator_links)),
        "pt_stage5_line_supervision_adapter.csv": int(len(line_supervision)),
        "pt_stage5_generator_supervision_adapter.csv": int(len(generator_supervision)),
        "pt_stage5_feature_contract.csv": int(len(feature_contract)),
        "pt_stage5_adapter_registry.csv": int(len(adapter_registry)),
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
        add_error(errors, "Manifest ml_ready should remain true")

    summary = {
        "generated_at": utc_now(),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "check_counts": check_counts,
    }
    write_json(OUT / "pt_stage5_validation_summary.json", summary)
    write_report(summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if errors:
        raise RuntimeError("Stage-5 learning adapter validation failed.")


if __name__ == "__main__":
    main()
