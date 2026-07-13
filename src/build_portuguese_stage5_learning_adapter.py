from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import ensure_directories, markdown_table, utc_now, write_json, write_text

ROOT = config.ROOT_DIR
PROCESSED = config.PROCESSED_DIR
REPORTS = config.REPORTS_DIR
STAGE3 = PROCESSED / "dataset_release_stage3"
STAGE4 = PROCESSED / "dataset_release_stage4"
OUT = PROCESSED / "dataset_release_stage5"
RELEASE_REPORT = REPORTS / "96_stage5_learning_adapter_release_note.md"

STAGE3_REQUIRED = [
    STAGE3 / "pt_stage3_graph_nodes.csv",
    STAGE3 / "pt_stage3_graph_edges.csv",
    STAGE3 / "pt_stage3_generator_nodes.csv",
    STAGE3 / "pt_stage3_generator_bus_links.csv",
    STAGE3 / "pt_stage3_manifest.json",
    STAGE3 / "pt_stage3_validation_summary.json",
]

STAGE4_REQUIRED = [
    STAGE4 / "pt_stage4_bus_node_features.csv",
    STAGE4 / "pt_stage4_line_edge_features.csv",
    STAGE4 / "pt_stage4_generator_node_features.csv",
    STAGE4 / "pt_stage4_generator_bus_link_features.csv",
    STAGE4 / "pt_stage4_line_risk_benchmark_samples.csv",
    STAGE4 / "pt_stage4_generator_risk_benchmark_samples.csv",
    STAGE4 / "pt_stage4_task_registry.csv",
    STAGE4 / "pt_stage4_split_registry.csv",
    STAGE4 / "pt_stage4_line_sample_splits.csv",
    STAGE4 / "pt_stage4_generator_sample_splits.csv",
    STAGE4 / "pt_stage4_manifest.json",
    STAGE4 / "pt_stage4_validation_summary.json",
]

DATASET_ID = "pt_grid_benchmark_stage5_adapter"
RELEASE_ID = "pt_grid_benchmark_stage5_adapter_v2"
SCHEMA_VERSION = "1.1"
FEATURE_CONTRACT_VERSION = "1.1"
PREPROCESSING_CONTRACT_VERSION = "1.1"

FORBIDDEN_INPUT_COLUMNS = {
    "sample_id",
    "scenario_family",
    "variant_id",
    "scenario_value",
    "source_scenario_id",
    "task_name",
    "target_column",
    "target_value_column",
    "recommended_split_id",
    "recommended_partition",
    "recommended_split_family",
    "grouped_challenge_partition",
    "relative_classification_partition",
    "balanced_plumbing_partition",
    "scenario_holdout_partition",
    "governance_sensitive_flag",
    "governance_sensitive_reason",
    "benchmark_core_candidate",
    "challenge_subset",
    "leakage_group_id",
    "label_definition_version",
    "benchmark_role",
    "task_readiness",
    "evaluation_protocol",
    "eligible_subset",
    "headline_eligible",
    "line_relative_high_stress_eligible",
    "generator_relative_high_dispatch_eligible",
    "source_release_id",
    "publication_allowed",
    "diagnostic_only",
    "prov_publication_allowed",
    "prov_diagnostic_only",
    "prov_source_scenario_id",
    "prov_trace_source_ids",
    "prov_assumption_ids",
    "benchmark_eligible",
    "split_eligibility",
    "benchmark_exclusion_reason",
    "stage4_benchmark_eligible",
    "stage4_exclusion_reason",
    "is_primary_split",
    "group_key",
}


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def strip_unnamed(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed:")].copy()


def require_inputs() -> None:
    missing = [str(path) for path in [*STAGE3_REQUIRED, *STAGE4_REQUIRED] if not path.exists()]
    if missing:
        raise RuntimeError("Fail-closed: required stage-5 inputs are missing:\n- " + "\n- ".join(missing))


def require_upstream_validation_pass() -> None:
    stage3_summary = read_json(STAGE3 / "pt_stage3_validation_summary.json")
    stage4_summary = read_json(STAGE4 / "pt_stage4_validation_summary.json")
    if stage3_summary.get("status") != "PASS":
        raise RuntimeError("Fail-closed: stage-3 validation summary is not PASS.")
    if stage4_summary.get("status") != "PASS":
        raise RuntimeError("Fail-closed: stage-4 validation summary is not PASS.")


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    normalized = str(value).strip().lower()
    if normalized not in {"true", "false", "1", "0", "yes", "no", "y", "n"}:
        raise ValueError(f"Invalid boolean value: {value!r}")
    return normalized in {"true", "1", "yes", "y"}


def load_inputs() -> dict[str, Any]:
    return {
        "stage3_graph_nodes": strip_unnamed(read_csv(STAGE3 / "pt_stage3_graph_nodes.csv")),
        "stage3_graph_edges": strip_unnamed(read_csv(STAGE3 / "pt_stage3_graph_edges.csv")),
        "stage3_generator_nodes": strip_unnamed(read_csv(STAGE3 / "pt_stage3_generator_nodes.csv")),
        "stage3_generator_links": strip_unnamed(read_csv(STAGE3 / "pt_stage3_generator_bus_links.csv")),
        "stage3_manifest": read_json(STAGE3 / "pt_stage3_manifest.json"),
        "stage4_bus_features": strip_unnamed(read_csv(STAGE4 / "pt_stage4_bus_node_features.csv")),
        "stage4_line_features": strip_unnamed(read_csv(STAGE4 / "pt_stage4_line_edge_features.csv")),
        "stage4_generator_features": strip_unnamed(read_csv(STAGE4 / "pt_stage4_generator_node_features.csv")),
        "stage4_generator_link_features": strip_unnamed(read_csv(STAGE4 / "pt_stage4_generator_bus_link_features.csv")),
        "stage4_line_samples": strip_unnamed(read_csv(STAGE4 / "pt_stage4_line_risk_benchmark_samples.csv")),
        "stage4_generator_samples": strip_unnamed(read_csv(STAGE4 / "pt_stage4_generator_risk_benchmark_samples.csv")),
        "stage4_task_registry": strip_unnamed(read_csv(STAGE4 / "pt_stage4_task_registry.csv")),
        "stage4_split_registry": strip_unnamed(read_csv(STAGE4 / "pt_stage4_split_registry.csv")),
        "stage4_line_splits": strip_unnamed(read_csv(STAGE4 / "pt_stage4_line_sample_splits.csv")),
        "stage4_generator_splits": strip_unnamed(read_csv(STAGE4 / "pt_stage4_generator_sample_splits.csv")),
        "stage4_manifest": read_json(STAGE4 / "pt_stage4_manifest.json"),
    }


def prepare_frames(data: dict[str, Any]) -> dict[str, Any]:
    id_columns = {
        "stage3_graph_nodes": ["node_id", "bus_id"],
        "stage3_graph_edges": ["edge_id", "source_node_id", "target_node_id", "line_id", "from_bus", "to_bus"],
        "stage3_generator_nodes": ["node_id", "generator_id", "bus_id"],
        "stage3_generator_links": ["edge_id", "source_node_id", "target_node_id", "generator_id", "bus_id"],
        "stage4_bus_features": ["node_id", "bus_id", "leakage_group_id", "source_release_id"],
        "stage4_line_features": ["edge_id", "line_id", "source_node_id", "target_node_id", "leakage_group_id", "source_release_id"],
        "stage4_generator_features": ["node_id", "generator_id", "bus_id", "leakage_group_id", "source_release_id"],
        "stage4_generator_link_features": ["edge_id", "generator_id", "source_node_id", "target_node_id", "bus_id", "leakage_group_id", "source_release_id"],
        "stage4_line_samples": ["sample_id", "line_id", "edge_id", "source_node_id", "target_node_id", "from_bus", "to_bus", "leakage_group_id", "source_release_id"],
        "stage4_generator_samples": ["sample_id", "generator_id", "bus_id", "node_id", "attached_bus_node_id", "leakage_group_id", "source_release_id"],
        "stage4_task_registry": ["task_name", "target_column", "recommended_split_id"],
        "stage4_split_registry": ["split_id", "task_name"],
        "stage4_line_splits": ["split_id", "sample_id", "line_id", "leakage_group_id", "group_key"],
        "stage4_generator_splits": ["split_id", "sample_id", "generator_id", "leakage_group_id", "group_key"],
    }
    prepared = dict(data)
    for key, columns in id_columns.items():
        frame = prepared[key].copy()
        for column in columns:
            if column in frame.columns:
                frame[column] = frame[column].astype(str)
        prepared[key] = frame
    return prepared


def build_graph_node_adapter(data: dict[str, Any]) -> pd.DataFrame:
    stage3 = data["stage3_graph_nodes"].copy()
    stage4 = data["stage4_bus_features"][[
        "node_id",
        "stage4_benchmark_eligible",
        "stage4_exclusion_reason",
        "governance_sensitive_flag",
        "leakage_group_id",
        "source_release_id",
    ]].copy()
    out = stage3.merge(stage4, on="node_id", how="inner", validate="one_to_one")
    out.insert(0, "adapter_view", "homogeneous_bus_line_graph")
    out.insert(1, "adapter_entity_type", "bus_node")
    return out


def build_graph_edge_adapter(data: dict[str, Any]) -> pd.DataFrame:
    stage3 = data["stage3_graph_edges"].copy()
    stage4 = data["stage4_line_features"][[
        "edge_id",
        "stage4_benchmark_eligible",
        "stage4_exclusion_reason",
        "governance_sensitive_flag",
        "leakage_group_id",
        "source_release_id",
    ]].copy()
    out = stage3.merge(stage4, on="edge_id", how="inner", validate="one_to_one")
    out.insert(0, "adapter_view", "homogeneous_bus_line_graph")
    out.insert(1, "adapter_entity_type", "line_edge")
    return out


def build_generator_node_adapter(data: dict[str, Any]) -> pd.DataFrame:
    stage3 = data["stage3_generator_nodes"].copy()
    stage4 = data["stage4_generator_features"][[
        "node_id",
        "stage4_benchmark_eligible",
        "stage4_exclusion_reason",
        "governance_sensitive_flag",
        "leakage_group_id",
        "source_release_id",
    ]].copy()
    out = stage3.merge(stage4, on="node_id", how="inner", validate="one_to_one")
    out.insert(0, "adapter_view", "heterogeneous_generator_sidecar")
    out.insert(1, "adapter_entity_type", "generator_node")
    return out


def build_generator_link_adapter(data: dict[str, Any]) -> pd.DataFrame:
    stage3 = data["stage3_generator_links"].copy()
    stage4 = data["stage4_generator_link_features"][[
        "edge_id",
        "stage4_benchmark_eligible",
        "stage4_exclusion_reason",
        "governance_sensitive_flag",
        "leakage_group_id",
        "source_release_id",
    ]].copy()
    out = stage3.merge(stage4, on="edge_id", how="inner", validate="one_to_one")
    out.insert(0, "adapter_view", "heterogeneous_generator_sidecar")
    out.insert(1, "adapter_entity_type", "generator_bus_link")
    return out


def attach_split_partitions(samples: pd.DataFrame, splits: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    out = samples.copy()
    for split_id, column in mapping.items():
        scoped = splits[splits["split_id"].astype(str) == split_id][["sample_id", "partition"]].copy()
        scoped = scoped.rename(columns={"partition": column})
        out = out.merge(scoped, on="sample_id", how="left", validate="one_to_one")
    return out


def explode_tasks(samples: pd.DataFrame, task_registry: pd.DataFrame, task_names: list[str], target_label: str) -> pd.DataFrame:
    registry = task_registry[task_registry["task_name"].astype(str).isin(task_names)].copy()
    rows: list[pd.DataFrame] = []
    for _, task_row in registry.iterrows():
        task_name = str(task_row["task_name"])
        target_column = str(task_row["target_column"])
        eligible_subset = str(task_row["eligible_subset"])
        if eligible_subset == "benchmark_core":
            piece = samples[
                samples["benchmark_eligible"].apply(as_bool)
                & samples["benchmark_core_candidate"].apply(as_bool)
            ].copy()
        elif eligible_subset == "all_benchmark_eligible":
            piece = samples[samples["benchmark_eligible"].apply(as_bool)].copy()
        else:
            raise RuntimeError(f"Unsupported task eligible_subset: {eligible_subset}")
        piece["task_name"] = task_name
        piece["target_column"] = target_column
        piece["task_type"] = str(task_row["task_type"])
        piece["primary_metric"] = str(task_row["primary_metric"])
        piece["recommended_split_id"] = str(task_row["recommended_split_id"])
        piece["eligible_subset"] = eligible_subset
        piece["benchmark_role"] = str(task_row["benchmark_role"])
        piece["task_readiness"] = str(task_row["task_readiness"])
        piece["evaluation_protocol"] = str(task_row["evaluation_protocol"])
        piece["headline_eligible"] = as_bool(task_row["headline_eligible"])
        piece["target_value"] = piece[target_column]
        piece["target_value_column"] = target_label
        rows.append(piece)
    return pd.concat(rows, ignore_index=True)


def attach_recommended_partitions(
    supervision: pd.DataFrame,
    splits: pd.DataFrame,
    split_registry: pd.DataFrame,
) -> pd.DataFrame:
    lookup = splits[["split_id", "sample_id", "partition"]].rename(
        columns={"split_id": "recommended_split_id", "partition": "recommended_partition"}
    )
    out = supervision.merge(
        lookup,
        on=["recommended_split_id", "sample_id"],
        how="left",
        validate="many_to_one",
    )
    split_families = split_registry.set_index("split_id")["split_family"].astype(str).to_dict()
    out["recommended_split_family"] = out["recommended_split_id"].map(split_families)
    return out


def build_line_supervision_adapter(data: dict[str, Any]) -> pd.DataFrame:
    samples = attach_split_partitions(
        data["stage4_line_samples"],
        data["stage4_line_splits"],
        {
            "line_grouped_entity_primary_v1": "grouped_challenge_partition",
            "line_grouped_entity_relative_stress_v1": "relative_classification_partition",
            "line_balanced_recommended_v1": "balanced_plumbing_partition",
            "line_scenario_family_holdout_v1": "scenario_holdout_partition",
        },
    )
    out = explode_tasks(
        samples,
        data["stage4_task_registry"],
        [
            "line_overload_binary_classification",
            "line_loading_regression",
            "line_relative_high_stress_classification",
        ],
        "line_target_value",
    )
    out = attach_recommended_partitions(out, data["stage4_line_splits"], data["stage4_split_registry"])
    out["challenge_subset"] = out["governance_sensitive_flag"].apply(
        lambda v: "governance_sensitive" if as_bool(v) else "benchmark_core"
    )
    out.insert(0, "adapter_view", "line_risk_supervision")
    out.insert(1, "adapter_sample_type", "line_risk_sample")
    out["entity_id"] = out["line_id"].astype(str)
    out["grouped_challenge_split_id"] = "line_grouped_entity_primary_v1"
    out["relative_classification_split_id"] = "line_grouped_entity_relative_stress_v1"
    out["balanced_plumbing_split_id"] = "line_balanced_recommended_v1"
    out["scenario_holdout_split_id"] = "line_scenario_family_holdout_v1"
    ordered = [
        "adapter_view",
        "adapter_sample_type",
        "sample_id",
        "entity_id",
        "line_id",
        "edge_id",
        "source_node_id",
        "target_node_id",
        "from_bus",
        "to_bus",
        "task_name",
        "task_type",
        "primary_metric",
        "target_column",
        "target_value",
        "target_value_column",
        "recommended_split_id",
        "recommended_split_family",
        "recommended_partition",
        "grouped_challenge_split_id",
        "grouped_challenge_partition",
        "relative_classification_split_id",
        "relative_classification_partition",
        "balanced_plumbing_split_id",
        "balanced_plumbing_partition",
        "scenario_holdout_split_id",
        "scenario_holdout_partition",
        "benchmark_eligible",
        "split_eligibility",
        "benchmark_core_candidate",
        "challenge_subset",
        "governance_sensitive_flag",
        "governance_sensitive_reason",
        "leakage_group_id",
        "scenario_family",
        "variant_id",
        "scenario_value",
        "label_definition_version",
        "source_release_id",
    ]
    remaining = [column for column in out.columns if column not in ordered]
    return out[ordered + remaining].copy()


def build_generator_supervision_adapter(data: dict[str, Any]) -> pd.DataFrame:
    samples = attach_split_partitions(
        data["stage4_generator_samples"],
        data["stage4_generator_splits"],
        {
            "generator_grouped_entity_primary_v1": "grouped_challenge_partition",
            "generator_grouped_entity_relative_dispatch_v1": "relative_classification_partition",
            "generator_balanced_recommended_v1": "balanced_plumbing_partition",
            "generator_scenario_family_holdout_v1": "scenario_holdout_partition",
        },
    )
    out = explode_tasks(
        samples,
        data["stage4_task_registry"],
        [
            "generator_top_dispatch_classification",
            "generator_dispatch_regression",
            "generator_relative_high_dispatch_classification",
        ],
        "generator_target_value",
    )
    out = attach_recommended_partitions(out, data["stage4_generator_splits"], data["stage4_split_registry"])
    out["challenge_subset"] = out["governance_sensitive_flag"].apply(
        lambda v: "governance_sensitive" if as_bool(v) else "benchmark_core"
    )
    out.insert(0, "adapter_view", "generator_risk_supervision")
    out.insert(1, "adapter_sample_type", "generator_risk_sample")
    out["entity_id"] = out["generator_id"].astype(str)
    out["grouped_challenge_split_id"] = "generator_grouped_entity_primary_v1"
    out["relative_classification_split_id"] = "generator_grouped_entity_relative_dispatch_v1"
    out["balanced_plumbing_split_id"] = "generator_balanced_recommended_v1"
    out["scenario_holdout_split_id"] = "generator_scenario_family_holdout_v1"
    ordered = [
        "adapter_view",
        "adapter_sample_type",
        "sample_id",
        "entity_id",
        "generator_id",
        "node_id",
        "bus_id",
        "attached_bus_node_id",
        "task_name",
        "task_type",
        "primary_metric",
        "target_column",
        "target_value",
        "target_value_column",
        "recommended_split_id",
        "recommended_split_family",
        "recommended_partition",
        "grouped_challenge_split_id",
        "grouped_challenge_partition",
        "relative_classification_split_id",
        "relative_classification_partition",
        "balanced_plumbing_split_id",
        "balanced_plumbing_partition",
        "scenario_holdout_split_id",
        "scenario_holdout_partition",
        "benchmark_eligible",
        "split_eligibility",
        "benchmark_core_candidate",
        "challenge_subset",
        "governance_sensitive_flag",
        "governance_sensitive_reason",
        "leakage_group_id",
        "scenario_family",
        "variant_id",
        "scenario_value",
        "label_definition_version",
        "source_release_id",
    ]
    remaining = [column for column in out.columns if column not in ordered]
    return out[ordered + remaining].copy()


def classify_semantic_role(column: str, table_name: str) -> tuple[str, str]:
    if column in {
        "target_value",
        "line_overload_binary_classification_target",
        "line_loading_regression_target",
        "line_relative_high_stress_classification_target",
        "generator_top_dispatch_classification_target",
        "generator_dispatch_regression_target",
        "generator_relative_high_dispatch_classification_target",
    }:
        return "target", "target_only"
    if column in {
        "target_column", "target_value_column", "task_name", "task_type", "primary_metric",
        "recommended_split_id", "recommended_split_family", "recommended_partition",
        "grouped_challenge_split_id", "grouped_challenge_partition",
        "relative_classification_split_id", "relative_classification_partition",
        "balanced_plumbing_split_id", "balanced_plumbing_partition",
        "scenario_holdout_split_id", "scenario_holdout_partition", "benchmark_core_candidate",
        "challenge_subset", "governance_sensitive_flag", "governance_sensitive_reason",
        "leakage_group_id", "benchmark_eligible", "split_eligibility", "benchmark_exclusion_reason",
        "stage4_benchmark_eligible", "stage4_exclusion_reason", "group_key", "is_primary_split",
        "eligible_subset", "benchmark_role", "task_readiness", "evaluation_protocol", "headline_eligible",
        "line_relative_high_stress_eligible", "generator_relative_high_dispatch_eligible",
    }:
        return "split_or_control", "split_control_only"
    if column.startswith("prov_") or column in {"source_release_id", "source_scenario_id", "publication_allowed", "diagnostic_only", "label_definition_version"}:
        return "provenance", "provenance_only"
    if column in {"adapter_view", "adapter_entity_type", "adapter_sample_type", "sample_id", "entity_id", "node_id", "edge_id", "line_id", "generator_id", "bus_id", "source_node_id", "target_node_id", "attached_bus_node_id", "from_bus", "to_bus", "scenario_family", "variant_id", "scenario_value"}:
        return "identifier", "control_only"
    if pd.api.types.is_bool_dtype(pd.Series(dtype=object)):
        return "feature", "model_input_candidate"
    if any(token in column for token in ["loading", "dispatch", "share", "count", "degree", "length", "voltage", "cost", "pmax", "pmin", "marginal", "max_", "min_", "r_", "x_", "c_", "current", "ohm", "kv", "mw", "mvar"]):
        return "feature", "model_input_candidate"
    if table_name.endswith("registry"):
        return "metadata", "control_only"
    return "feature", "model_input_candidate"


def build_feature_contract(adapter_tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for table_name, df in adapter_tables.items():
        for column in df.columns:
            semantic_role, usage = classify_semantic_role(column, table_name)
            dtype = str(df[column].dtype)
            leakage_sensitive = column in {"leakage_group_id", "grouped_challenge_partition", "recommended_partition", "scenario_holdout_partition", "source_scenario_id", "scenario_family", "variant_id", "scenario_value"}
            target_only = usage == "target_only"
            split_control_only = usage in {"split_control_only", "control_only"}
            provenance_only = usage == "provenance_only"
            admissible_as_model_input = not (target_only or split_control_only or provenance_only or column in FORBIDDEN_INPUT_COLUMNS)
            rows.append(
                {
                    "table_name": table_name,
                    "column_name": column,
                    "semantic_role": semantic_role,
                    "feature_type": dtype,
                    "admissible_as_model_input": admissible_as_model_input,
                    "target_only": target_only,
                    "split_control_only": split_control_only,
                    "provenance_only": provenance_only,
                    "leakage_sensitive_or_forbidden": leakage_sensitive or column in FORBIDDEN_INPUT_COLUMNS,
                    "notes": "forbidden_for_naive_training" if column in FORBIDDEN_INPUT_COLUMNS else "",
                }
            )
    return pd.DataFrame(rows)


def build_adapter_registry() -> pd.DataFrame:
    rows = [
        {
            "adapter_view": "homogeneous_bus_line_graph",
            "table_name": "pt_stage5_graph_node_adapter.csv",
            "adapter_family": "graph_structure",
            "entity_scope": "bus_node",
            "intended_use": "canonical bus-node view for homogeneous graph adapters",
            "supports_tasks": "",
            "recommended_split_id": "",
            "diagnostic_only": True,
            "publication_allowed": False,
        },
        {
            "adapter_view": "homogeneous_bus_line_graph",
            "table_name": "pt_stage5_graph_edge_adapter.csv",
            "adapter_family": "graph_structure",
            "entity_scope": "line_edge",
            "intended_use": "canonical line-edge view for homogeneous graph adapters",
            "supports_tasks": "line_overload_binary_classification|line_loading_regression|line_relative_high_stress_classification",
            "recommended_split_id": "",
            "diagnostic_only": True,
            "publication_allowed": False,
        },
        {
            "adapter_view": "heterogeneous_generator_sidecar",
            "table_name": "pt_stage5_generator_node_adapter.csv",
            "adapter_family": "graph_structure",
            "entity_scope": "generator_node",
            "intended_use": "generator node sidecar for heterogeneous or bipartite adapters",
            "supports_tasks": "generator_top_dispatch_classification|generator_dispatch_regression|generator_relative_high_dispatch_classification",
            "recommended_split_id": "",
            "diagnostic_only": True,
            "publication_allowed": False,
        },
        {
            "adapter_view": "heterogeneous_generator_sidecar",
            "table_name": "pt_stage5_generator_link_adapter.csv",
            "adapter_family": "graph_structure",
            "entity_scope": "generator_bus_link",
            "intended_use": "generator-to-bus linkage sidecar for heterogeneous adapters",
            "supports_tasks": "generator_top_dispatch_classification|generator_dispatch_regression|generator_relative_high_dispatch_classification",
            "recommended_split_id": "",
            "diagnostic_only": True,
            "publication_allowed": False,
        },
        {
            "adapter_view": "line_risk_supervision",
            "table_name": "pt_stage5_line_supervision_adapter.csv",
            "adapter_family": "supervision",
            "entity_scope": "line_sample",
            "intended_use": "framework-neutral line supervision table with explicit split assignments and target metadata",
            "supports_tasks": "line_overload_binary_classification|line_loading_regression|line_relative_high_stress_classification",
            "recommended_split_id": "",
            "diagnostic_only": True,
            "publication_allowed": False,
        },
        {
            "adapter_view": "generator_risk_supervision",
            "table_name": "pt_stage5_generator_supervision_adapter.csv",
            "adapter_family": "supervision",
            "entity_scope": "generator_sample",
            "intended_use": "framework-neutral generator supervision table with explicit split assignments and target metadata",
            "supports_tasks": "generator_top_dispatch_classification|generator_dispatch_regression|generator_relative_high_dispatch_classification",
            "recommended_split_id": "",
            "diagnostic_only": True,
            "publication_allowed": False,
        },
    ]
    return pd.DataFrame(rows)


def build_preprocessing_contract(data: dict[str, Any]) -> dict[str, Any]:
    task_registry = data["stage4_task_registry"]
    recommended_split_ids = sorted(set(task_registry["recommended_split_id"].astype(str)))
    primary_evaluation_split_ids = sorted(set(
        task_registry[
            task_registry["benchmark_role"].astype(str).isin({"PRIMARY", "AUXILIARY_LIMITED_SUPPORT"})
        ]["recommended_split_id"].astype(str)
    ))
    plumbing_only_split_ids = sorted(set(
        task_registry[
            task_registry["benchmark_role"].astype(str).eq("CHALLENGE_INSUFFICIENT_SUPPORT")
        ]["recommended_split_id"].astype(str)
    ))
    return {
        "generated_at": utc_now(),
        "contract_name": "pt_stage5_train_only_preprocessing_contract",
        "contract_version": PREPROCESSING_CONTRACT_VERSION,
        "policy": {
            "fit_preprocessing_on_train_only": True,
            "allow_validation_transform_with_frozen_train_state": True,
            "allow_test_transform_with_frozen_train_state": True,
            "forbid_label_aware_preprocessing": True,
            "forbid_heldout_statistics_in_normalization": True,
            "forbid_target_columns_as_model_inputs": True,
            "forbid_split_columns_as_model_inputs": True,
            "forbid_provenance_columns_as_model_inputs": True,
        },
        "recommended_split_ids": recommended_split_ids,
        "primary_evaluation_split_ids": primary_evaluation_split_ids,
        "plumbing_only_split_ids": plumbing_only_split_ids,
        "challenge_split_ids": [
            "line_grouped_entity_primary_v1",
            "line_grouped_entity_relative_stress_v1",
            "generator_grouped_entity_primary_v1",
            "generator_grouped_entity_relative_dispatch_v1",
            "line_scenario_family_holdout_v1",
            "generator_scenario_family_holdout_v1",
        ],
        "forbidden_input_columns": sorted(FORBIDDEN_INPUT_COLUMNS),
        "allowed_partition_values": ["train", "validation", "test"],
        "notes": [
            "This contract is framework-neutral and does not serialize fitted transformers.",
            "Governance-sensitive rows remain tagged and may be filtered explicitly by downstream code rather than silently dropped.",
            "Primary and auxiliary evaluation uses task-specific grouped entity splits.",
            "Row-balanced splits are plumbing-only and cannot support cross-entity generalization claims.",
        ],
    }


def build_manifest(row_counts: dict[str, int], data: dict[str, Any]) -> dict[str, Any]:
    stage3_manifest = data["stage3_manifest"]
    stage4_manifest = data["stage4_manifest"]
    return {
        "generated_at": utc_now(),
        "dataset_id": DATASET_ID,
        "release_id": RELEASE_ID,
        "schema_version": SCHEMA_VERSION,
        "builder_script": "src/build_portuguese_stage5_learning_adapter.py",
        "release_scope": "framework-neutral adapter layer built on validated stage-3 topology and stage-4 benchmark contracts; no model training or framework-specific tensor export included",
        "upstream_stage3_release_id": stage3_manifest.get("release_id"),
        "upstream_stage4_release_id": stage4_manifest.get("release_id"),
        "benchmark_freeze": stage4_manifest.get("benchmark_freeze", {}),
        "source_artifacts": [str(path.relative_to(ROOT)) for path in [*STAGE3_REQUIRED, *STAGE4_REQUIRED]],
        "table_row_counts": row_counts,
        "publication_allowed": False,
        "diagnostic_only": True,
        "operator_grade_ready": False,
        "ml_ready": True,
        "adapter_views": [
            "homogeneous_bus_line_graph",
            "heterogeneous_generator_sidecar",
            "line_risk_supervision",
            "generator_risk_supervision",
        ],
        "recommended_split_ids": sorted(set(data["stage4_task_registry"]["recommended_split_id"].astype(str))),
        "feature_contract_version": FEATURE_CONTRACT_VERSION,
        "preprocessing_contract_version": PREPROCESSING_CONTRACT_VERSION,
        "excluded_scope": [
            "model training",
            "framework-specific tensor exports",
            "learned normalization artifacts",
            "benchmark relabeling",
            "silent removal of governance-sensitive rows",
            "operator-grade promotion",
        ],
        "caveats": [
            "Adapter tables are machine-readable contracts for downstream training code, not model-ready tensors.",
            "Primary regression and auxiliary relative classification tasks use strict grouped entity splits.",
            "Original rare-event classifications remain plumbing/challenge tasks because benchmark-core positive entity support is insufficient.",
        ],
    }


def write_release_report(manifest: dict[str, Any], row_counts: dict[str, int], adapter_registry: pd.DataFrame) -> None:
    summary_rows = [{"table": key, "rows": value} for key, value in row_counts.items()]
    adapter_rows = adapter_registry[["adapter_view", "table_name", "adapter_family", "entity_scope", "recommended_split_id"]].to_dict("records")
    text = [
        "# 96 Stage-5 Learning Adapter Release Note",
        "",
        f"Generated: {manifest['generated_at']}",
        "",
        "Scope: publish framework-neutral adapter tables and contracts on top of the validated stage-3 topology layer and stage-4 learning benchmark package, without model training or tensor export.",
        "",
        "## Release identity",
        "",
        f"- dataset id: `{manifest['dataset_id']}`",
        f"- release id: `{manifest['release_id']}`",
        f"- upstream stage-3 release id: `{manifest['upstream_stage3_release_id']}`",
        f"- upstream stage-4 release id: `{manifest['upstream_stage4_release_id']}`",
        "",
        "## Included outputs",
        "",
        markdown_table(summary_rows, ["table", "rows"]),
        "",
        "## Adapter views",
        "",
        markdown_table(adapter_rows, ["adapter_view", "table_name", "adapter_family", "entity_scope", "recommended_split_id"]),
        "",
        "## Contract guarantees",
        "",
        "- every supervision row carries explicit recommended, grouped-challenge, and scenario-holdout partition references",
        "- every exported adapter column is covered by the feature contract",
        "- train-only preprocessing rules are published separately from learned artifacts",
        "- governance-sensitive subsets remain tagged rather than silently removed",
        "",
        "## Boundary",
        "",
        "- diagnostic-only",
        "- not operator-grade",
        "- no model training",
        "- no framework-specific tensor export",
        "- no persisted fitted preprocessing state",
    ]
    write_text(RELEASE_REPORT, "\n".join(text) + "\n")


def main() -> None:
    ensure_directories()
    require_inputs()
    require_upstream_validation_pass()
    OUT.mkdir(parents=True, exist_ok=True)

    data = prepare_frames(load_inputs())
    graph_node_adapter = build_graph_node_adapter(data)
    graph_edge_adapter = build_graph_edge_adapter(data)
    generator_node_adapter = build_generator_node_adapter(data)
    generator_link_adapter = build_generator_link_adapter(data)
    line_supervision_adapter = build_line_supervision_adapter(data)
    generator_supervision_adapter = build_generator_supervision_adapter(data)

    adapter_tables = {
        "pt_stage5_graph_node_adapter.csv": graph_node_adapter,
        "pt_stage5_graph_edge_adapter.csv": graph_edge_adapter,
        "pt_stage5_generator_node_adapter.csv": generator_node_adapter,
        "pt_stage5_generator_link_adapter.csv": generator_link_adapter,
        "pt_stage5_line_supervision_adapter.csv": line_supervision_adapter,
        "pt_stage5_generator_supervision_adapter.csv": generator_supervision_adapter,
    }
    feature_contract = build_feature_contract(adapter_tables)
    adapter_registry = build_adapter_registry()
    preprocessing_contract = build_preprocessing_contract(data)

    graph_node_adapter.to_csv(OUT / "pt_stage5_graph_node_adapter.csv", index=False)
    graph_edge_adapter.to_csv(OUT / "pt_stage5_graph_edge_adapter.csv", index=False)
    generator_node_adapter.to_csv(OUT / "pt_stage5_generator_node_adapter.csv", index=False)
    generator_link_adapter.to_csv(OUT / "pt_stage5_generator_link_adapter.csv", index=False)
    line_supervision_adapter.to_csv(OUT / "pt_stage5_line_supervision_adapter.csv", index=False)
    generator_supervision_adapter.to_csv(OUT / "pt_stage5_generator_supervision_adapter.csv", index=False)
    feature_contract.to_csv(OUT / "pt_stage5_feature_contract.csv", index=False)
    adapter_registry.to_csv(OUT / "pt_stage5_adapter_registry.csv", index=False)
    write_json(OUT / "pt_stage5_train_only_preprocessing_contract.json", preprocessing_contract)

    row_counts = {
        "pt_stage5_graph_node_adapter.csv": int(len(graph_node_adapter)),
        "pt_stage5_graph_edge_adapter.csv": int(len(graph_edge_adapter)),
        "pt_stage5_generator_node_adapter.csv": int(len(generator_node_adapter)),
        "pt_stage5_generator_link_adapter.csv": int(len(generator_link_adapter)),
        "pt_stage5_line_supervision_adapter.csv": int(len(line_supervision_adapter)),
        "pt_stage5_generator_supervision_adapter.csv": int(len(generator_supervision_adapter)),
        "pt_stage5_feature_contract.csv": int(len(feature_contract)),
        "pt_stage5_adapter_registry.csv": int(len(adapter_registry)),
    }
    manifest = build_manifest(row_counts, data)
    write_json(OUT / "pt_stage5_manifest.json", manifest)
    write_release_report(manifest, row_counts, adapter_registry)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
