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
OUT = PROCESSED / "dataset_release_stage4"
RELEASE_REPORT = REPORTS / "94_stage4_learning_benchmark_release_note.md"

REQUIRED_INPUTS = [
    STAGE3 / "pt_stage3_graph_nodes.csv",
    STAGE3 / "pt_stage3_graph_edges.csv",
    STAGE3 / "pt_stage3_generator_nodes.csv",
    STAGE3 / "pt_stage3_generator_bus_links.csv",
    STAGE3 / "pt_stage3_line_risk_samples.csv",
    STAGE3 / "pt_stage3_generator_risk_samples.csv",
    STAGE3 / "pt_stage3_manifest.json",
    STAGE3 / "pt_stage3_validation_summary.json",
]

DATASET_ID = "pt_grid_benchmark_stage4_learning"
RELEASE_ID = "pt_grid_benchmark_stage4_learning_v2"
SCHEMA_VERSION = "1.1"
SPLIT_VERSION = "2.0"
LABEL_VERSION = "2.0"
LEAKAGE_VERSION = "1.1"
GROUPED_SEED = 17
SCENARIO_SEED = 29
LINE_RELATIVE_STRESS_QUANTILE = 0.70
GENERATOR_RELATIVE_DISPATCH_QUANTILE = 0.70
MIN_THREE_WAY_POSITIVE_ENTITIES = 3
RECOMMENDED_POSITIVE_ENTITIES = 10


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def require_inputs() -> None:
    missing = [str(path) for path in REQUIRED_INPUTS if not path.exists()]
    if missing:
        raise RuntimeError("Fail-closed: required stage-4 inputs are missing:\n- " + "\n- ".join(missing))


def require_stage3_validation_pass() -> None:
    summary = read_json(STAGE3 / "pt_stage3_validation_summary.json")
    if summary.get("status") != "PASS":
        raise RuntimeError("Fail-closed: stage-3 validation summary is not PASS.")


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def strip_unnamed(df: pd.DataFrame) -> pd.DataFrame:
    return df.loc[:, ~df.columns.astype(str).str.startswith("Unnamed:")].copy()


def load_stage3() -> dict[str, Any]:
    return {
        "graph_nodes": strip_unnamed(read_csv(STAGE3 / "pt_stage3_graph_nodes.csv")),
        "graph_edges": strip_unnamed(read_csv(STAGE3 / "pt_stage3_graph_edges.csv")),
        "generator_nodes": strip_unnamed(read_csv(STAGE3 / "pt_stage3_generator_nodes.csv")),
        "generator_bus_links": strip_unnamed(read_csv(STAGE3 / "pt_stage3_generator_bus_links.csv")),
        "line_samples": strip_unnamed(read_csv(STAGE3 / "pt_stage3_line_risk_samples.csv")),
        "generator_samples": strip_unnamed(read_csv(STAGE3 / "pt_stage3_generator_risk_samples.csv")),
        "manifest": read_json(STAGE3 / "pt_stage3_manifest.json"),
        "validation": read_json(STAGE3 / "pt_stage3_validation_summary.json"),
    }


def prepare_frames(stage3: dict[str, Any]) -> dict[str, Any]:
    graph_nodes = stage3["graph_nodes"].copy()
    graph_edges = stage3["graph_edges"].copy()
    generator_nodes = stage3["generator_nodes"].copy()
    generator_bus_links = stage3["generator_bus_links"].copy()
    line_samples = stage3["line_samples"].copy()
    generator_samples = stage3["generator_samples"].copy()

    for frame, columns in [
        (graph_nodes, ["node_id", "bus_id"]),
        (graph_edges, ["edge_id", "line_id", "source_node_id", "target_node_id"]),
        (generator_nodes, ["node_id", "generator_id", "bus_id"]),
        (generator_bus_links, ["edge_id", "source_node_id", "target_node_id", "generator_id", "bus_id"]),
        (line_samples, ["sample_id", "line_id", "edge_id", "source_node_id", "target_node_id", "from_bus", "to_bus"]),
        (generator_samples, ["sample_id", "generator_id", "node_id", "bus_id", "attached_bus_node_id"]),
    ]:
        for column in columns:
            if column in frame.columns:
                frame[column] = frame[column].astype(str)

    return {
        **stage3,
        "graph_nodes": graph_nodes,
        "graph_edges": graph_edges,
        "generator_nodes": generator_nodes,
        "generator_bus_links": generator_bus_links,
        "line_samples": line_samples,
        "generator_samples": generator_samples,
    }


def partition_for_position(index: int, total: int) -> str:
    if total <= 1:
        return "train"
    if total == 2:
        return "train" if index == 0 else "test"
    train_cut = max(1, int(round(total * 0.6)))
    val_cut = max(train_cut + 1, int(round(total * 0.8))) if total >= 5 else train_cut + 1
    train_cut = min(train_cut, total - 2)
    val_cut = min(max(val_cut, train_cut + 1), total - 1)
    if index < train_cut:
        return "train"
    if index < val_cut:
        return "validation"
    return "test"


def assign_grouped_partitions(group_df: pd.DataFrame, positive_col: str, seed: int) -> dict[str, str]:
    work = group_df.copy()
    work["group_positive"] = work[positive_col].apply(as_bool)
    work = work.sort_values(["group_positive", "group_id"], ascending=[False, True]).reset_index(drop=True)
    if len(work) > 0:
        rotation = seed % len(work)
        work = pd.concat([work.iloc[rotation:], work.iloc[:rotation]], ignore_index=True)
        work = work.sort_values(["group_positive", "group_id"], ascending=[False, True], kind="stable").reset_index(drop=True)
    assignments: dict[str, str] = {}
    positive_indices = work.index[work["group_positive"]].tolist()
    positive_partitions = ["train", "validation", "test"]
    for idx, partition in zip(positive_indices, positive_partitions):
        assignments[str(work.at[idx, "group_id"])] = partition
    for i, row in work.iterrows():
        group_id = str(row["group_id"])
        if group_id in assignments:
            continue
        assignments[group_id] = partition_for_position(i, len(work))
    return assignments


def assign_scenario_holdout_partitions(df: pd.DataFrame) -> dict[str, str]:
    families = sorted(df["scenario_family"].astype(str).unique())
    if len(families) == 1:
        return {families[0]: "test"}
    if len(families) == 2:
        return {families[0]: "train", families[1]: "test"}
    if len(families) == 3:
        return {families[0]: "train", families[1]: "validation", families[2]: "test"}
    assignments: dict[str, str] = {}
    for i, family in enumerate(families):
        assignments[family] = partition_for_position(i, len(families))
    return assignments


def assign_balanced_row_partitions(df: pd.DataFrame, positive_col: str, seed: int) -> dict[str, str]:
    work = df.copy()
    work[positive_col] = work[positive_col].apply(as_bool)
    work["scenario_family"] = work["scenario_family"].astype(str)
    work["sample_id"] = work["sample_id"].astype(str)
    work["leakage_group_id"] = work["leakage_group_id"].astype(str)
    assignments: dict[str, str] = {}

    for is_positive in [True, False]:
        subset = work[work[positive_col] == is_positive].sort_values(
            ["scenario_family", "leakage_group_id", "sample_id"],
            kind="stable",
        ).reset_index(drop=True)
        if subset.empty:
            continue
        rotation = seed % len(subset)
        subset = pd.concat([subset.iloc[rotation:], subset.iloc[:rotation]], ignore_index=True)
        for i, row in subset.iterrows():
            assignments[str(row["sample_id"])] = partition_for_position(i, len(subset))
    return assignments


def build_entity_tables(stage3: dict[str, Any]) -> dict[str, pd.DataFrame]:
    bus_nodes = stage3["graph_nodes"].copy()
    bus_nodes["stage4_benchmark_eligible"] = bus_nodes["prov_diagnostic_only"].apply(as_bool)
    bus_nodes["stage4_exclusion_reason"] = bus_nodes["stage4_benchmark_eligible"].map(lambda v: "" if v else "NON_DIAGNOSTIC_SOURCE")
    bus_nodes["governance_sensitive_flag"] = bus_nodes["has_policy_governed_incident_line"].apply(as_bool)
    bus_nodes["leakage_group_id"] = "bus|" + bus_nodes["node_id"].astype(str)
    bus_nodes["source_release_id"] = stage3["manifest"].get("release_id", "")

    line_edges = stage3["graph_edges"].copy()
    line_edges["stage4_benchmark_eligible"] = line_edges["prov_diagnostic_only"].apply(as_bool)
    line_edges["stage4_exclusion_reason"] = line_edges["stage4_benchmark_eligible"].map(lambda v: "" if v else "NON_DIAGNOSTIC_SOURCE")
    line_edges["governance_sensitive_flag"] = (
        line_edges["policy_class"].fillna("").astype(str).ne("")
        | line_edges["repeated_bottleneck"].apply(as_bool)
        | line_edges["is_mixed_corridor"].apply(as_bool)
        | line_edges["is_parallel_equivalent_required"].apply(as_bool)
    )
    line_edges["leakage_group_id"] = "line|" + line_edges["line_id"].astype(str)
    line_edges["source_release_id"] = stage3["manifest"].get("release_id", "")

    generator_nodes = stage3["generator_nodes"].copy()
    generator_nodes["stage4_benchmark_eligible"] = generator_nodes["prov_diagnostic_only"].apply(as_bool) & generator_nodes["benchmark_usable"].apply(as_bool)
    generator_nodes["stage4_exclusion_reason"] = generator_nodes.apply(
        lambda row: "" if as_bool(row["prov_diagnostic_only"]) and as_bool(row["benchmark_usable"]) else (
            "GENERATOR_NOT_BENCHMARK_USABLE" if not as_bool(row["benchmark_usable"]) else "NON_DIAGNOSTIC_SOURCE"
        ),
        axis=1,
    )
    generator_nodes["governance_sensitive_flag"] = generator_nodes["is_import_interface_proxy"].apply(as_bool)
    generator_nodes["leakage_group_id"] = "generator|" + generator_nodes["generator_id"].astype(str)
    generator_nodes["source_release_id"] = stage3["manifest"].get("release_id", "")

    generator_links = stage3["generator_bus_links"].copy()
    generator_links["stage4_benchmark_eligible"] = generator_links["prov_diagnostic_only"].apply(as_bool)
    generator_links["stage4_exclusion_reason"] = generator_links["stage4_benchmark_eligible"].map(lambda v: "" if v else "NON_DIAGNOSTIC_SOURCE")
    generator_links["governance_sensitive_flag"] = generator_links["dispatch_proxy_class"].astype(str).eq("import_interface_proxy")
    generator_links["leakage_group_id"] = "generator_link|" + generator_links["generator_id"].astype(str)
    generator_links["source_release_id"] = stage3["manifest"].get("release_id", "")

    return {
        "bus_nodes": bus_nodes,
        "line_edges": line_edges,
        "generator_nodes": generator_nodes,
        "generator_links": generator_links,
    }


def build_line_benchmark_samples(stage3: dict[str, Any]) -> tuple[pd.DataFrame, float]:
    line_samples = stage3["line_samples"].copy()
    line_samples["line_overload_binary_classification_target"] = line_samples["over_100_loading_flag"].apply(as_bool)
    line_samples["line_loading_regression_target"] = pd.to_numeric(line_samples["metric_max_loading_percent"], errors="coerce")
    line_samples["task_membership"] = "line_overload_binary_classification|line_loading_regression"
    line_samples["benchmark_core_candidate"] = ~(
        line_samples["policy_class"].fillna("").astype(str).ne("")
        | line_samples["repeated_bottleneck"].apply(as_bool)
    )
    line_samples["governance_sensitive_flag"] = ~line_samples["benchmark_core_candidate"]
    line_samples["governance_sensitive_reason"] = line_samples.apply(
        lambda row: "" if row["benchmark_core_candidate"] else (
            "MIXED_CORRIDOR_POLICY_DEPENDENT" if str(row.get("policy_class", "")).strip() else "REPEATED_BOTTLENECK_DEPENDENT"
        ),
        axis=1,
    )
    line_samples["leakage_group_id"] = "line|" + line_samples["line_id"].astype(str)
    line_samples["split_eligibility"] = line_samples["diagnostic_only"].apply(as_bool)
    line_samples["benchmark_eligible"] = line_samples["split_eligibility"]
    line_samples["benchmark_exclusion_reason"] = line_samples["benchmark_eligible"].map(lambda v: "" if v else "NON_DIAGNOSTIC_SAMPLE")
    core_mask = line_samples["benchmark_eligible"] & line_samples["benchmark_core_candidate"]
    core_loading = pd.to_numeric(
        line_samples.loc[core_mask, "line_loading_regression_target"], errors="coerce"
    ).dropna()
    if core_loading.empty:
        raise RuntimeError("Fail-closed: no benchmark-core line loading values are available for the relative stress label.")
    relative_threshold = float(core_loading.quantile(LINE_RELATIVE_STRESS_QUANTILE))
    line_samples["line_relative_high_stress_classification_target"] = pd.Series(
        pd.NA, index=line_samples.index, dtype="boolean"
    )
    line_samples.loc[core_mask, "line_relative_high_stress_classification_target"] = (
        pd.to_numeric(line_samples.loc[core_mask, "line_loading_regression_target"], errors="coerce")
        >= relative_threshold
    ).astype(bool)
    line_samples["line_relative_high_stress_eligible"] = core_mask
    line_samples["task_membership"] = (
        "line_overload_binary_classification|line_loading_regression|"
        "line_relative_high_stress_classification"
    )
    line_samples["label_definition_version"] = LABEL_VERSION
    line_samples["source_release_id"] = stage3["manifest"].get("release_id", "")
    return line_samples, relative_threshold


def build_generator_benchmark_samples(stage3: dict[str, Any]) -> tuple[pd.DataFrame, float]:
    generator_samples = stage3["generator_samples"].copy()
    generator_samples["generator_top_dispatch_classification_target"] = generator_samples["top_dispatch_flag"].apply(as_bool)
    generator_samples["generator_dispatch_regression_target"] = pd.to_numeric(generator_samples["dispatch_mw"], errors="coerce")
    generator_samples["task_membership"] = "generator_top_dispatch_classification|generator_dispatch_regression"
    generator_samples["governance_sensitive_flag"] = generator_samples["import_dependence_flag"].apply(as_bool) | generator_samples["dispatch_proxy_class"].astype(str).eq("import_interface_proxy")
    generator_samples["governance_sensitive_reason"] = generator_samples.apply(
        lambda row: "IMPORT_POLICY_DEPENDENT" if as_bool(row["import_dependence_flag"]) else (
            "PROXY_SEMANTICS_DEPENDENT" if str(row["dispatch_proxy_class"]) == "import_interface_proxy" else ""
        ),
        axis=1,
    )
    generator_samples["benchmark_core_candidate"] = ~generator_samples["governance_sensitive_flag"]
    generator_samples["leakage_group_id"] = "generator|" + generator_samples["generator_id"].astype(str)
    generator_samples["split_eligibility"] = generator_samples["diagnostic_only"].apply(as_bool)
    generator_samples["benchmark_eligible"] = generator_samples["split_eligibility"]
    generator_samples["benchmark_exclusion_reason"] = generator_samples["benchmark_eligible"].map(lambda v: "" if v else "NON_DIAGNOSTIC_SAMPLE")
    core_mask = generator_samples["benchmark_eligible"] & generator_samples["benchmark_core_candidate"]
    core_dispatch = pd.to_numeric(
        generator_samples.loc[core_mask, "generator_dispatch_regression_target"], errors="coerce"
    ).dropna()
    if core_dispatch.empty:
        raise RuntimeError("Fail-closed: no benchmark-core generator dispatch values are available for the relative dispatch label.")
    relative_threshold = float(core_dispatch.quantile(GENERATOR_RELATIVE_DISPATCH_QUANTILE))
    generator_samples["generator_relative_high_dispatch_classification_target"] = pd.Series(
        pd.NA, index=generator_samples.index, dtype="boolean"
    )
    generator_samples.loc[core_mask, "generator_relative_high_dispatch_classification_target"] = (
        pd.to_numeric(generator_samples.loc[core_mask, "generator_dispatch_regression_target"], errors="coerce")
        >= relative_threshold
    ).astype(bool)
    generator_samples["generator_relative_high_dispatch_eligible"] = core_mask
    generator_samples["task_membership"] = (
        "generator_top_dispatch_classification|generator_dispatch_regression|"
        "generator_relative_high_dispatch_classification"
    )
    generator_samples["label_definition_version"] = LABEL_VERSION
    generator_samples["source_release_id"] = stage3["manifest"].get("release_id", "")
    return generator_samples, relative_threshold


def build_task_registry(line_relative_threshold: float, generator_relative_threshold: float) -> pd.DataFrame:
    rows = [
        {
            "task_name": "line_overload_binary_classification",
            "prediction_unit": "line_sample",
            "target_column": "line_overload_binary_classification_target",
            "task_type": "binary_classification",
            "eligible_table": "pt_stage4_line_risk_benchmark_samples.csv",
            "primary_metric": "average_precision",
            "recommended_split_id": "line_balanced_recommended_v1",
            "leakage_unit": "line_id",
            "eligible_subset": "all_benchmark_eligible",
            "benchmark_role": "CHALLENGE_INSUFFICIENT_SUPPORT",
            "task_readiness": "INSUFFICIENT_CORE_POSITIVE_ENTITIES",
            "evaluation_protocol": "row_balanced_plumbing_only_no_cross_entity_claim",
            "label_threshold": 100.0,
            "threshold_unit": "loading_percent",
            "headline_eligible": False,
            "diagnostic_only": True,
            "publication_allowed": False,
            "notes": "Binary overload task from stage-3 over_100_loading_flag.",
        },
        {
            "task_name": "line_loading_regression",
            "prediction_unit": "line_sample",
            "target_column": "line_loading_regression_target",
            "task_type": "regression",
            "eligible_table": "pt_stage4_line_risk_benchmark_samples.csv",
            "primary_metric": "mae",
            "recommended_split_id": "line_grouped_entity_primary_v1",
            "leakage_unit": "line_id",
            "eligible_subset": "benchmark_core",
            "benchmark_role": "PRIMARY",
            "task_readiness": "READY_GROUPED_REGRESSION",
            "evaluation_protocol": "strict_grouped_entity_holdout",
            "label_threshold": "",
            "threshold_unit": "",
            "headline_eligible": True,
            "diagnostic_only": True,
            "publication_allowed": False,
            "notes": "Regression task from stage-3 metric_max_loading_percent.",
        },
        {
            "task_name": "line_relative_high_stress_classification",
            "prediction_unit": "line_sample",
            "target_column": "line_relative_high_stress_classification_target",
            "task_type": "binary_classification",
            "eligible_table": "pt_stage4_line_risk_benchmark_samples.csv",
            "primary_metric": "average_precision",
            "recommended_split_id": "line_grouped_entity_relative_stress_v1",
            "leakage_unit": "line_id",
            "eligible_subset": "benchmark_core",
            "benchmark_role": "AUXILIARY_LIMITED_SUPPORT",
            "task_readiness": "LIMITED_GROUPED_POSITIVE_ENTITIES",
            "evaluation_protocol": "strict_grouped_entity_holdout_report_entity_support",
            "label_threshold": line_relative_threshold,
            "threshold_unit": "loading_percent_benchmark_core_q70",
            "headline_eligible": False,
            "diagnostic_only": True,
            "publication_allowed": False,
            "notes": "Auxiliary relative-stress label at the frozen benchmark-core 70th percentile; it is not an overload label.",
        },
        {
            "task_name": "generator_top_dispatch_classification",
            "prediction_unit": "generator_sample",
            "target_column": "generator_top_dispatch_classification_target",
            "task_type": "binary_classification",
            "eligible_table": "pt_stage4_generator_risk_benchmark_samples.csv",
            "primary_metric": "average_precision",
            "recommended_split_id": "generator_balanced_recommended_v1",
            "leakage_unit": "generator_id",
            "eligible_subset": "all_benchmark_eligible",
            "benchmark_role": "CHALLENGE_INSUFFICIENT_SUPPORT",
            "task_readiness": "INSUFFICIENT_CORE_POSITIVE_ENTITIES",
            "evaluation_protocol": "row_balanced_plumbing_only_no_cross_entity_claim",
            "label_threshold": "",
            "threshold_unit": "scenario_top_dispatch_flag",
            "headline_eligible": False,
            "diagnostic_only": True,
            "publication_allowed": False,
            "notes": "Binary generator top-dispatch task from stage-3 top_dispatch_flag.",
        },
        {
            "task_name": "generator_dispatch_regression",
            "prediction_unit": "generator_sample",
            "target_column": "generator_dispatch_regression_target",
            "task_type": "regression",
            "eligible_table": "pt_stage4_generator_risk_benchmark_samples.csv",
            "primary_metric": "mae",
            "recommended_split_id": "generator_grouped_entity_primary_v1",
            "leakage_unit": "generator_id",
            "eligible_subset": "benchmark_core",
            "benchmark_role": "PRIMARY",
            "task_readiness": "READY_GROUPED_REGRESSION",
            "evaluation_protocol": "strict_grouped_entity_holdout",
            "label_threshold": "",
            "threshold_unit": "",
            "headline_eligible": True,
            "diagnostic_only": True,
            "publication_allowed": False,
            "notes": "Regression task from stage-3 dispatch_mw.",
        },
        {
            "task_name": "generator_relative_high_dispatch_classification",
            "prediction_unit": "generator_sample",
            "target_column": "generator_relative_high_dispatch_classification_target",
            "task_type": "binary_classification",
            "eligible_table": "pt_stage4_generator_risk_benchmark_samples.csv",
            "primary_metric": "average_precision",
            "recommended_split_id": "generator_grouped_entity_relative_dispatch_v1",
            "leakage_unit": "generator_id",
            "eligible_subset": "benchmark_core",
            "benchmark_role": "AUXILIARY_LIMITED_SUPPORT",
            "task_readiness": "LIMITED_GROUPED_POSITIVE_ENTITIES",
            "evaluation_protocol": "strict_grouped_entity_holdout_report_entity_support",
            "label_threshold": generator_relative_threshold,
            "threshold_unit": "dispatch_mw_benchmark_core_q70",
            "headline_eligible": False,
            "diagnostic_only": True,
            "publication_allowed": False,
            "notes": "Auxiliary relative-dispatch label at the frozen benchmark-core 70th percentile; it is not an operational high-output threshold.",
        },
    ]
    return pd.DataFrame(rows)


def build_split_registry() -> pd.DataFrame:
    rows = [
        {
            "split_id": "line_grouped_entity_primary_v1",
            "task_name": "line_loading_regression",
            "split_family": "grouped_entity_primary",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "line_id",
            "leakage_policy": "all rows with same line_id stay in one partition",
            "scenario_holdout_rule": "none",
            "entity_holdout_rule": "group by line_id",
            "random_seed": GROUPED_SEED,
            "is_primary_recommended_split": True,
            "notes": "Primary strict grouped benchmark-core split for line loading regression.",
        },
        {
            "split_id": "line_grouped_entity_relative_stress_v1",
            "task_name": "line_relative_high_stress_classification",
            "split_family": "grouped_entity_auxiliary",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "line_id",
            "leakage_policy": "all rows with same line_id stay in one partition",
            "scenario_holdout_rule": "none",
            "entity_holdout_rule": "group by line_id and stratify positive entities when support permits",
            "random_seed": GROUPED_SEED,
            "is_primary_recommended_split": True,
            "notes": "Task-specific grouped split for the auxiliary relative-stress label; limited entity support must be reported.",
        },
        {
            "split_id": "line_balanced_recommended_v1",
            "task_name": "line_overload_binary_classification",
            "split_family": "row_balanced_plumbing",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "sample_row",
            "leakage_policy": "deterministic row-balanced split with leakage metadata retained; entities may appear across partitions",
            "scenario_holdout_rule": "none",
            "entity_holdout_rule": "none",
            "random_seed": GROUPED_SEED,
            "is_primary_recommended_split": False,
            "notes": "Backward-compatible row-balanced plumbing split. It is not valid evidence of cross-line generalization.",
        },
        {
            "split_id": "line_scenario_family_holdout_v1",
            "task_name": "line_overload_binary_classification|line_loading_regression|line_relative_high_stress_classification",
            "split_family": "scenario_family_holdout",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "scenario_family",
            "leakage_policy": "all rows with same scenario_family stay in one partition",
            "scenario_holdout_rule": "family-level holdout",
            "entity_holdout_rule": "none",
            "random_seed": SCENARIO_SEED,
            "is_primary_recommended_split": False,
            "notes": "Scenario-family robustness split for line tasks.",
        },
        {
            "split_id": "generator_grouped_entity_primary_v1",
            "task_name": "generator_dispatch_regression",
            "split_family": "grouped_entity_primary",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "generator_id",
            "leakage_policy": "all rows with same generator_id stay in one partition",
            "scenario_holdout_rule": "none",
            "entity_holdout_rule": "group by generator_id",
            "random_seed": GROUPED_SEED,
            "is_primary_recommended_split": True,
            "notes": "Primary strict grouped benchmark-core split for generator dispatch regression.",
        },
        {
            "split_id": "generator_grouped_entity_relative_dispatch_v1",
            "task_name": "generator_relative_high_dispatch_classification",
            "split_family": "grouped_entity_auxiliary",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "generator_id",
            "leakage_policy": "all rows with same generator_id stay in one partition",
            "scenario_holdout_rule": "none",
            "entity_holdout_rule": "group by generator_id and stratify positive entities when support permits",
            "random_seed": GROUPED_SEED,
            "is_primary_recommended_split": True,
            "notes": "Task-specific grouped split for the auxiliary relative-dispatch label; limited entity support must be reported.",
        },
        {
            "split_id": "generator_balanced_recommended_v1",
            "task_name": "generator_top_dispatch_classification",
            "split_family": "row_balanced_plumbing",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "sample_row",
            "leakage_policy": "deterministic row-balanced split with leakage metadata retained; entities may appear across partitions",
            "scenario_holdout_rule": "none",
            "entity_holdout_rule": "none",
            "random_seed": GROUPED_SEED,
            "is_primary_recommended_split": False,
            "notes": "Backward-compatible row-balanced plumbing split. It is not valid evidence of cross-generator generalization.",
        },
        {
            "split_id": "generator_scenario_family_holdout_v1",
            "task_name": "generator_top_dispatch_classification|generator_dispatch_regression|generator_relative_high_dispatch_classification",
            "split_family": "scenario_family_holdout",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "scenario_family",
            "leakage_policy": "all rows with same scenario_family stay in one partition",
            "scenario_holdout_rule": "family-level holdout",
            "entity_holdout_rule": "none",
            "random_seed": SCENARIO_SEED,
            "is_primary_recommended_split": False,
            "notes": "Scenario-family robustness split for generator tasks.",
        },
    ]
    return pd.DataFrame(rows)


def build_line_splits(line_samples: pd.DataFrame) -> pd.DataFrame:
    eligible = line_samples[line_samples["benchmark_eligible"]].copy()
    core = eligible[eligible["benchmark_core_candidate"]].copy()
    group_df = core.groupby("line_id").agg(
        group_positive=("line_relative_high_stress_classification_target", lambda s: bool(pd.Series(s).apply(as_bool).any())),
        governance_sensitive_flag=("governance_sensitive_flag", lambda s: bool(pd.Series(s).apply(as_bool).any())),
    ).reset_index().rename(columns={"line_id": "group_id"})
    grouped_assignments = assign_grouped_partitions(group_df, "group_positive", GROUPED_SEED)
    balanced_assignments = assign_balanced_row_partitions(eligible, "line_overload_binary_classification_target", GROUPED_SEED)
    scenario_assignments = assign_scenario_holdout_partitions(eligible)

    grouped = core[["sample_id", "line_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    grouped.insert(0, "split_id", "line_grouped_entity_primary_v1")
    grouped["partition"] = grouped["line_id"].astype(str).map(grouped_assignments)
    grouped["group_key"] = grouped["line_id"].astype(str)
    grouped["is_primary_split"] = True

    relative_grouped = core[["sample_id", "line_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    relative_grouped.insert(0, "split_id", "line_grouped_entity_relative_stress_v1")
    relative_grouped["partition"] = relative_grouped["line_id"].astype(str).map(grouped_assignments)
    relative_grouped["group_key"] = relative_grouped["line_id"].astype(str)
    relative_grouped["is_primary_split"] = True

    balanced = eligible[["sample_id", "line_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    balanced.insert(0, "split_id", "line_balanced_recommended_v1")
    balanced["partition"] = balanced["sample_id"].astype(str).map(balanced_assignments)
    balanced["group_key"] = balanced["sample_id"].astype(str)
    balanced["is_primary_split"] = False

    scenario = eligible[["sample_id", "line_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    scenario.insert(0, "split_id", "line_scenario_family_holdout_v1")
    scenario["partition"] = scenario["scenario_family"].astype(str).map(scenario_assignments)
    scenario["group_key"] = scenario["scenario_family"].astype(str)
    scenario["is_primary_split"] = False

    out = pd.concat([grouped, relative_grouped, balanced, scenario], ignore_index=True)
    out["challenge_subset"] = out["governance_sensitive_flag"].apply(lambda v: "governance_sensitive" if as_bool(v) else "benchmark_core")
    return out


def build_generator_splits(generator_samples: pd.DataFrame) -> pd.DataFrame:
    eligible = generator_samples[generator_samples["benchmark_eligible"]].copy()
    core = eligible[eligible["benchmark_core_candidate"]].copy()
    group_df = core.groupby("generator_id").agg(
        group_positive=("generator_relative_high_dispatch_classification_target", lambda s: bool(pd.Series(s).apply(as_bool).any())),
        governance_sensitive_flag=("governance_sensitive_flag", lambda s: bool(pd.Series(s).apply(as_bool).any())),
    ).reset_index().rename(columns={"generator_id": "group_id"})
    grouped_assignments = assign_grouped_partitions(group_df, "group_positive", GROUPED_SEED)
    balanced_assignments = assign_balanced_row_partitions(eligible, "generator_top_dispatch_classification_target", GROUPED_SEED)
    scenario_assignments = assign_scenario_holdout_partitions(eligible)

    grouped = core[["sample_id", "generator_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    grouped.insert(0, "split_id", "generator_grouped_entity_primary_v1")
    grouped["partition"] = grouped["generator_id"].astype(str).map(grouped_assignments)
    grouped["group_key"] = grouped["generator_id"].astype(str)
    grouped["is_primary_split"] = True

    relative_grouped = core[["sample_id", "generator_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    relative_grouped.insert(0, "split_id", "generator_grouped_entity_relative_dispatch_v1")
    relative_grouped["partition"] = relative_grouped["generator_id"].astype(str).map(grouped_assignments)
    relative_grouped["group_key"] = relative_grouped["generator_id"].astype(str)
    relative_grouped["is_primary_split"] = True

    balanced = eligible[["sample_id", "generator_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    balanced.insert(0, "split_id", "generator_balanced_recommended_v1")
    balanced["partition"] = balanced["sample_id"].astype(str).map(balanced_assignments)
    balanced["group_key"] = balanced["sample_id"].astype(str)
    balanced["is_primary_split"] = False

    scenario = eligible[["sample_id", "generator_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    scenario.insert(0, "split_id", "generator_scenario_family_holdout_v1")
    scenario["partition"] = scenario["scenario_family"].astype(str).map(scenario_assignments)
    scenario["group_key"] = scenario["scenario_family"].astype(str)
    scenario["is_primary_split"] = False

    out = pd.concat([grouped, relative_grouped, balanced, scenario], ignore_index=True)
    out["challenge_subset"] = out["governance_sensitive_flag"].apply(lambda v: "governance_sensitive" if as_bool(v) else "benchmark_core")
    return out


def positive_entity_support_status(positive_entities: int) -> str:
    if positive_entities == 0:
        return "NO_POSITIVE_ENTITIES"
    if positive_entities < MIN_THREE_WAY_POSITIVE_ENTITIES:
        return "INSUFFICIENT_FOR_THREE_WAY_GROUPED"
    if positive_entities < RECOMMENDED_POSITIVE_ENTITIES:
        return "LIMITED_GROUPED_SUPPORT"
    return "ADEQUATE_GROUPED_SUPPORT"


def build_label_support_audit(
    line_samples: pd.DataFrame,
    generator_samples: pd.DataFrame,
    task_registry: pd.DataFrame,
    split_registry: pd.DataFrame,
    line_splits: pd.DataFrame,
    generator_splits: pd.DataFrame,
) -> pd.DataFrame:
    table_map = {
        "pt_stage4_line_risk_benchmark_samples.csv": (line_samples, line_splits, "line_id"),
        "pt_stage4_generator_risk_benchmark_samples.csv": (generator_samples, generator_splits, "generator_id"),
    }
    split_units = split_registry.set_index("split_id")["unit_of_separation"].astype(str).to_dict()
    rows: list[dict[str, Any]] = []

    for _, task in task_registry.iterrows():
        samples, splits, entity_column = table_map[str(task["eligible_table"])]
        task_type = str(task["task_type"])
        target_column = str(task["target_column"])
        recommended_split_id = str(task["recommended_split_id"])
        subset_masks = {
            "all_benchmark_eligible": samples["benchmark_eligible"].apply(as_bool),
            "benchmark_core": samples["benchmark_eligible"].apply(as_bool)
            & samples["benchmark_core_candidate"].apply(as_bool),
            "governance_sensitive": samples["benchmark_eligible"].apply(as_bool)
            & samples["governance_sensitive_flag"].apply(as_bool),
        }
        audit_subsets = list(subset_masks) if task_type == "binary_classification" else [str(task["eligible_subset"])]

        for subset_name in audit_subsets:
            scoped = samples[subset_masks[subset_name]].copy()
            split_rows = splits[splits["split_id"].astype(str) == recommended_split_id][
                ["sample_id", "partition", "leakage_group_id"]
            ].copy()
            joined = scoped.merge(split_rows, on="sample_id", how="inner", suffixes=("", "_split"))
            leakage_groups = int(
                split_rows.groupby("leakage_group_id")["partition"].nunique().gt(1).sum()
            ) if not split_rows.empty else 0

            if task_type == "binary_classification":
                positives = scoped[target_column].apply(as_bool)
                positive_rows = int(positives.sum())
                positive_entities = int(scoped.loc[positives, entity_column].astype(str).nunique())
                joined_positive = joined[target_column].apply(as_bool) if not joined.empty else pd.Series(dtype=bool)
                partitions_with_positive = sorted(
                    joined.loc[joined_positive, "partition"].astype(str).unique().tolist()
                ) if not joined.empty else []
                support_status = positive_entity_support_status(positive_entities)
                if support_status == "NO_POSITIVE_ENTITIES":
                    recommendation = "Use the continuous regression task or generate independent positive scenarios."
                elif support_status == "INSUFFICIENT_FOR_THREE_WAY_GROUPED":
                    recommendation = "Do not force a three-way grouped evaluation; use descriptive analysis or grouped two-fold evaluation."
                elif support_status == "LIMITED_GROUPED_SUPPORT":
                    recommendation = "Report positive entity counts and uncertainty; do not make strong generalization claims."
                else:
                    recommendation = "Grouped evaluation is supported, subject to ordinary benchmark limitations."
                positive_rate = positive_rows / len(scoped) if len(scoped) else 0.0
            else:
                positive_rows = ""
                positive_entities = ""
                positive_rate = ""
                partitions_with_positive = []
                support_status = "CONTINUOUS_TARGET_GROUPED_EVALUATION"
                recommendation = "Use strict grouped entity holdout and report continuous-target distribution by partition."

            rows.append(
                {
                    "task_name": str(task["task_name"]),
                    "task_type": task_type,
                    "target_column": target_column,
                    "subset": subset_name,
                    "entity_column": entity_column,
                    "total_rows": int(len(scoped)),
                    "total_entities": int(scoped[entity_column].astype(str).nunique()),
                    "positive_rows": positive_rows,
                    "positive_rate": positive_rate,
                    "positive_entities": positive_entities,
                    "minimum_three_way_positive_entities": MIN_THREE_WAY_POSITIVE_ENTITIES,
                    "recommended_positive_entities": RECOMMENDED_POSITIVE_ENTITIES,
                    "support_status": support_status,
                    "benchmark_role": str(task["benchmark_role"]),
                    "recommended_split_id": recommended_split_id,
                    "recommended_split_unit": split_units.get(recommended_split_id, ""),
                    "split_rows_in_subset": int(len(joined)),
                    "partitions_present": "|".join(sorted(joined["partition"].astype(str).unique())) if not joined.empty else "",
                    "partitions_with_positive": "|".join(partitions_with_positive),
                    "leakage_groups_crossing_partitions": leakage_groups,
                    "recommendation": recommendation,
                }
            )
    return pd.DataFrame(rows)


def build_entity_registry(entity_tables: dict[str, pd.DataFrame]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    mapping = [
        ("bus_node", entity_tables["bus_nodes"], "node_id"),
        ("line_edge", entity_tables["line_edges"], "edge_id"),
        ("generator_node", entity_tables["generator_nodes"], "node_id"),
        ("generator_bus_link", entity_tables["generator_links"], "edge_id"),
    ]
    for entity_type, df, key in mapping:
        for _, row in df.iterrows():
            rows.append(
                {
                    "registry_id": f"{entity_type}|{row[key]}",
                    "entity_type": entity_type,
                    "entity_id": str(row[key]),
                    "benchmark_eligible": as_bool(row["stage4_benchmark_eligible"]),
                    "exclusion_reason_code": str(row["stage4_exclusion_reason"]),
                    "exclusion_reason_text": str(row["stage4_exclusion_reason"]),
                    "provenance_complete": True,
                    "governance_sensitive_flag": as_bool(row["governance_sensitive_flag"]),
                    "leakage_group_id": str(row["leakage_group_id"]),
                    "split_eligible": as_bool(row["stage4_benchmark_eligible"]),
                }
            )
    return pd.DataFrame(rows)


def build_sample_registry(line_samples: pd.DataFrame, generator_samples: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for sample_type, df, key in [
        ("line_risk_sample", line_samples, "sample_id"),
        ("generator_risk_sample", generator_samples, "sample_id"),
    ]:
        for _, row in df.iterrows():
            exclusion_reason = "" if as_bool(row["benchmark_eligible"]) else str(row["benchmark_exclusion_reason"])
            rows.append(
                {
                    "registry_id": f"{sample_type}|{row[key]}",
                    "sample_type": sample_type,
                    "sample_id": str(row[key]),
                    "benchmark_eligible": as_bool(row["benchmark_eligible"]),
                    "benchmark_core_candidate": as_bool(row["benchmark_core_candidate"]),
                    "exclusion_reason_code": exclusion_reason,
                    "exclusion_reason_text": exclusion_reason,
                    "provenance_complete": True,
                    "governance_sensitive_flag": as_bool(row["governance_sensitive_flag"]),
                    "leakage_group_id": str(row["leakage_group_id"]),
                    "split_eligible": as_bool(row["split_eligibility"]),
                }
            )
    return pd.DataFrame(rows)


def build_manifest(row_counts: dict[str, int], stage3_manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "dataset_id": DATASET_ID,
        "release_id": RELEASE_ID,
        "schema_version": SCHEMA_VERSION,
        "builder_script": "src/build_portuguese_stage4_learning_benchmark.py",
        "release_scope": "learning-ready benchmark packaging layer with task registries, split registries, benchmark inclusion rules, and leakage controls built on the validated stage-3 graph/sample release; no model training included",
        "upstream_stage3_release_id": stage3_manifest.get("release_id"),
        "benchmark_freeze": stage3_manifest.get("benchmark_freeze", {}),
        "source_artifacts": [str(path.relative_to(ROOT)) for path in REQUIRED_INPUTS],
        "table_row_counts": row_counts,
        "publication_allowed": False,
        "diagnostic_only": True,
        "operator_grade_ready": False,
        "ml_ready": True,
        "primary_tasks": [
            "line_loading_regression",
            "generator_dispatch_regression",
        ],
        "auxiliary_tasks": [
            "line_relative_high_stress_classification",
            "generator_relative_high_dispatch_classification",
        ],
        "insufficient_support_challenge_tasks": [
            "line_overload_binary_classification",
            "generator_top_dispatch_classification",
        ],
        "primary_splits": [
            "line_grouped_entity_primary_v1",
            "line_grouped_entity_relative_stress_v1",
            "generator_grouped_entity_primary_v1",
            "generator_grouped_entity_relative_dispatch_v1",
        ],
        "split_policy_version": SPLIT_VERSION,
        "label_policy_version": LABEL_VERSION,
        "leakage_policy_version": LEAKAGE_VERSION,
        "benchmark_core_definition": "benchmark-core excludes governance-sensitive line samples and governance-sensitive generator samples from the core subset while retaining them as auditable challenge subsets",
        "governance_sensitive_subsets": [
            "mixed corridor / repeated bottleneck dependent line samples",
            "import-policy / proxy-semantics dependent generator samples",
        ],
        "downstream_intended_use": [
            "reproducible supervised graph-learning benchmark packaging",
            "leakage-safe split definition for later ML workflows",
            "task registry for downstream SSL and risk benchmarking",
        ],
        "excluded_scope": [
            "model training",
            "framework-specific tensor exports",
            "learned normalization fitted on held-out data",
            "graph augmentation pipelines",
            "new PF/DCOPF scenario generation",
            "operator-grade promotion",
        ],
        "caveats": [
            "Benchmark tasks are diagnostic benchmark targets, not real-system operator-grade labels.",
            "The original overload and top-dispatch labels lack enough independent benchmark-core positive entities for three-way grouped evaluation.",
            "Relative high-stress and high-dispatch labels are auxiliary percentile labels, not operational overload or dispatch thresholds.",
            "Backward-compatible balanced row splits are plumbing-only and must not support cross-entity generalization claims.",
            "Governance-sensitive subsets are retained explicitly rather than hidden inside the benchmark core.",
        ],
    }


def write_release_report(
    manifest: dict[str, Any],
    row_counts: dict[str, int],
    line_splits: pd.DataFrame,
    generator_splits: pd.DataFrame,
    label_support: pd.DataFrame,
) -> None:
    summary_rows = [{"table": key, "rows": value} for key, value in row_counts.items()]
    split_rows = []
    for split_df, task_family in [(line_splits, "line"), (generator_splits, "generator")]:
        for split_id, sub in split_df.groupby("split_id"):
            counts = sub["partition"].value_counts().to_dict()
            split_rows.append(
                {
                    "task_family": task_family,
                    "split_id": split_id,
                    "train_rows": counts.get("train", 0),
                    "validation_rows": counts.get("validation", 0),
                    "test_rows": counts.get("test", 0),
                }
            )
    text = [
        "# 94 Stage-4 Learning Benchmark Release Note",
        "",
        f"Generated: {manifest['generated_at']}",
        "",
        "Scope: package the validated stage-3 graph/sample release into a learning-ready benchmark layer with explicit task definitions, split registries, leakage controls, and benchmark-core versus governance-sensitive challenge subsets, without model training.",
        "",
        "## Release identity",
        "",
        f"- dataset id: `{manifest['dataset_id']}`",
        f"- release id: `{manifest['release_id']}`",
        f"- upstream stage-3 release id: `{manifest['upstream_stage3_release_id']}`",
        "",
        "## Included outputs",
        "",
        markdown_table(summary_rows, ["table", "rows"]),
        "",
        "## Official tasks",
        "",
        "- primary: `line_loading_regression`, `generator_dispatch_regression`",
        "- auxiliary limited-support: `line_relative_high_stress_classification`, `generator_relative_high_dispatch_classification`",
        "- insufficient-support challenge/plumbing: `line_overload_binary_classification`, `generator_top_dispatch_classification`",
        "",
        "## Split overview",
        "",
        markdown_table(split_rows, ["task_family", "split_id", "train_rows", "validation_rows", "test_rows"]),
        "",
        "## Split policy",
        "",
        "- primary regression and auxiliary classification evaluation uses strict entity-grouped splits.",
        "- `line_balanced_recommended_v1` and `generator_balanced_recommended_v1` are retained only as backward-compatible plumbing splits.",
        "- governance-sensitive rows remain explicitly tagged in all split outputs rather than silently removed from the release.",
        "",
        "## Label support",
        "",
        markdown_table(
            label_support[label_support["subset"] == "benchmark_core"].to_dict("records"),
            ["task_name", "task_type", "total_rows", "total_entities", "positive_rows", "positive_entities", "support_status"],
        ),
        "",
        "## What stage-4 adds",
        "",
        "- benchmark task registry",
        "- leakage-safe split registries",
        "- benchmark-core versus governance-sensitive challenge subset annotations",
        "- explicit benchmark inclusion/exclusion registries",
        "",
        "## Boundary",
        "",
        "- diagnostic-only",
        "- not operator-grade",
        "- learning-ready for benchmark packaging, not model-training output",
        "- no framework-specific tensor export",
        "- no hidden train/test preprocessing state",
    ]
    write_text(RELEASE_REPORT, "\n".join(text) + "\n")


def main() -> None:
    ensure_directories()
    require_inputs()
    require_stage3_validation_pass()
    OUT.mkdir(parents=True, exist_ok=True)

    stage3 = prepare_frames(load_stage3())
    entity_tables = build_entity_tables(stage3)
    line_samples, line_relative_threshold = build_line_benchmark_samples(stage3)
    generator_samples, generator_relative_threshold = build_generator_benchmark_samples(stage3)
    task_registry = build_task_registry(line_relative_threshold, generator_relative_threshold)
    split_registry = build_split_registry()
    line_splits = build_line_splits(line_samples)
    generator_splits = build_generator_splits(generator_samples)
    label_support = build_label_support_audit(
        line_samples,
        generator_samples,
        task_registry,
        split_registry,
        line_splits,
        generator_splits,
    )
    entity_registry = build_entity_registry(entity_tables)
    sample_registry = build_sample_registry(line_samples, generator_samples)

    entity_tables["bus_nodes"].to_csv(OUT / "pt_stage4_bus_node_features.csv", index=False)
    entity_tables["line_edges"].to_csv(OUT / "pt_stage4_line_edge_features.csv", index=False)
    entity_tables["generator_nodes"].to_csv(OUT / "pt_stage4_generator_node_features.csv", index=False)
    entity_tables["generator_links"].to_csv(OUT / "pt_stage4_generator_bus_link_features.csv", index=False)
    line_samples.to_csv(OUT / "pt_stage4_line_risk_benchmark_samples.csv", index=False)
    generator_samples.to_csv(OUT / "pt_stage4_generator_risk_benchmark_samples.csv", index=False)
    task_registry.to_csv(OUT / "pt_stage4_task_registry.csv", index=False)
    split_registry.to_csv(OUT / "pt_stage4_split_registry.csv", index=False)
    line_splits.to_csv(OUT / "pt_stage4_line_sample_splits.csv", index=False)
    generator_splits.to_csv(OUT / "pt_stage4_generator_sample_splits.csv", index=False)
    label_support.to_csv(OUT / "pt_stage4_label_support_audit.csv", index=False)
    entity_registry.to_csv(OUT / "pt_stage4_entity_registry.csv", index=False)
    sample_registry.to_csv(OUT / "pt_stage4_sample_registry.csv", index=False)

    row_counts = {
        "pt_stage4_bus_node_features.csv": int(len(entity_tables["bus_nodes"])),
        "pt_stage4_line_edge_features.csv": int(len(entity_tables["line_edges"])),
        "pt_stage4_generator_node_features.csv": int(len(entity_tables["generator_nodes"])),
        "pt_stage4_generator_bus_link_features.csv": int(len(entity_tables["generator_links"])),
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
    manifest = build_manifest(row_counts, stage3["manifest"])
    write_json(OUT / "pt_stage4_manifest.json", manifest)
    write_release_report(manifest, row_counts, line_splits, generator_splits, label_support)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
