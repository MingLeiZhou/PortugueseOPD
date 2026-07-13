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
RELEASE_ID = "pt_grid_benchmark_stage4_learning_v1"
SCHEMA_VERSION = "1.0"
SPLIT_VERSION = "1.0"
LABEL_VERSION = "1.0"
LEAKAGE_VERSION = "1.0"
GROUPED_SEED = 17
SCENARIO_SEED = 29


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


def build_line_benchmark_samples(stage3: dict[str, Any]) -> pd.DataFrame:
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
    line_samples["label_definition_version"] = LABEL_VERSION
    line_samples["source_release_id"] = stage3["manifest"].get("release_id", "")
    return line_samples


def build_generator_benchmark_samples(stage3: dict[str, Any]) -> pd.DataFrame:
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
    generator_samples["label_definition_version"] = LABEL_VERSION
    generator_samples["source_release_id"] = stage3["manifest"].get("release_id", "")
    return generator_samples


def build_task_registry() -> pd.DataFrame:
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
            "recommended_split_id": "line_balanced_recommended_v1",
            "leakage_unit": "line_id",
            "diagnostic_only": True,
            "publication_allowed": False,
            "notes": "Regression task from stage-3 metric_max_loading_percent.",
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
            "recommended_split_id": "generator_balanced_recommended_v1",
            "leakage_unit": "generator_id",
            "diagnostic_only": True,
            "publication_allowed": False,
            "notes": "Regression task from stage-3 dispatch_mw.",
        },
    ]
    return pd.DataFrame(rows)


def build_split_registry() -> pd.DataFrame:
    rows = [
        {
            "split_id": "line_grouped_entity_primary_v1",
            "task_name": "line_overload_binary_classification|line_loading_regression",
            "split_family": "grouped_entity_primary",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "line_id",
            "leakage_policy": "all rows with same line_id stay in one partition",
            "scenario_holdout_rule": "none",
            "entity_holdout_rule": "group by line_id",
            "random_seed": GROUPED_SEED,
            "is_primary_recommended_split": False,
            "notes": "Strict grouped challenge split for line tasks; benchmark-core only and retained for robustness stress testing.",
        },
        {
            "split_id": "line_balanced_recommended_v1",
            "task_name": "line_overload_binary_classification|line_loading_regression",
            "split_family": "balanced_recommended",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "sample_row",
            "leakage_policy": "deterministic row-balanced split with leakage metadata retained; entities may appear across partitions",
            "scenario_holdout_rule": "none",
            "entity_holdout_rule": "none",
            "random_seed": GROUPED_SEED,
            "is_primary_recommended_split": True,
            "notes": "Recommended paper-usable split for line tasks; keeps governance-sensitive rows tagged so classification support remains usable.",
        },
        {
            "split_id": "line_scenario_family_holdout_v1",
            "task_name": "line_overload_binary_classification|line_loading_regression",
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
            "task_name": "generator_top_dispatch_classification|generator_dispatch_regression",
            "split_family": "grouped_entity_primary",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "generator_id",
            "leakage_policy": "all rows with same generator_id stay in one partition",
            "scenario_holdout_rule": "none",
            "entity_holdout_rule": "group by generator_id",
            "random_seed": GROUPED_SEED,
            "is_primary_recommended_split": False,
            "notes": "Strict grouped challenge split for generator tasks; benchmark-core only and retained for robustness stress testing.",
        },
        {
            "split_id": "generator_balanced_recommended_v1",
            "task_name": "generator_top_dispatch_classification|generator_dispatch_regression",
            "split_family": "balanced_recommended",
            "split_version": SPLIT_VERSION,
            "unit_of_separation": "sample_row",
            "leakage_policy": "deterministic row-balanced split with leakage metadata retained; entities may appear across partitions",
            "scenario_holdout_rule": "none",
            "entity_holdout_rule": "none",
            "random_seed": GROUPED_SEED,
            "is_primary_recommended_split": True,
            "notes": "Recommended paper-usable split for generator tasks; keeps governance-sensitive rows tagged so positive support remains usable.",
        },
        {
            "split_id": "generator_scenario_family_holdout_v1",
            "task_name": "generator_top_dispatch_classification|generator_dispatch_regression",
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
        group_positive=("line_overload_binary_classification_target", lambda s: bool(pd.Series(s).apply(as_bool).any())),
        governance_sensitive_flag=("governance_sensitive_flag", lambda s: bool(pd.Series(s).apply(as_bool).any())),
    ).reset_index().rename(columns={"line_id": "group_id"})
    grouped_assignments = assign_grouped_partitions(group_df, "group_positive", GROUPED_SEED)
    balanced_assignments = assign_balanced_row_partitions(eligible, "line_overload_binary_classification_target", GROUPED_SEED)
    scenario_assignments = assign_scenario_holdout_partitions(eligible)

    grouped = core[["sample_id", "line_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    grouped.insert(0, "split_id", "line_grouped_entity_primary_v1")
    grouped["partition"] = grouped["line_id"].astype(str).map(grouped_assignments)
    grouped["group_key"] = grouped["line_id"].astype(str)
    grouped["is_primary_split"] = False

    balanced = eligible[["sample_id", "line_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    balanced.insert(0, "split_id", "line_balanced_recommended_v1")
    balanced["partition"] = balanced["sample_id"].astype(str).map(balanced_assignments)
    balanced["group_key"] = balanced["sample_id"].astype(str)
    balanced["is_primary_split"] = True

    scenario = eligible[["sample_id", "line_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    scenario.insert(0, "split_id", "line_scenario_family_holdout_v1")
    scenario["partition"] = scenario["scenario_family"].astype(str).map(scenario_assignments)
    scenario["group_key"] = scenario["scenario_family"].astype(str)
    scenario["is_primary_split"] = False

    out = pd.concat([grouped, balanced, scenario], ignore_index=True)
    out["challenge_subset"] = out["governance_sensitive_flag"].apply(lambda v: "governance_sensitive" if as_bool(v) else "benchmark_core")
    return out


def build_generator_splits(generator_samples: pd.DataFrame) -> pd.DataFrame:
    eligible = generator_samples[generator_samples["benchmark_eligible"]].copy()
    core = eligible[eligible["benchmark_core_candidate"]].copy()
    group_df = core.groupby("generator_id").agg(
        group_positive=("generator_top_dispatch_classification_target", lambda s: bool(pd.Series(s).apply(as_bool).any())),
        governance_sensitive_flag=("governance_sensitive_flag", lambda s: bool(pd.Series(s).apply(as_bool).any())),
    ).reset_index().rename(columns={"generator_id": "group_id"})
    grouped_assignments = assign_grouped_partitions(group_df, "group_positive", GROUPED_SEED)
    balanced_assignments = assign_balanced_row_partitions(eligible, "generator_top_dispatch_classification_target", GROUPED_SEED)
    scenario_assignments = assign_scenario_holdout_partitions(eligible)

    grouped = core[["sample_id", "generator_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    grouped.insert(0, "split_id", "generator_grouped_entity_primary_v1")
    grouped["partition"] = grouped["generator_id"].astype(str).map(grouped_assignments)
    grouped["group_key"] = grouped["generator_id"].astype(str)
    grouped["is_primary_split"] = False

    balanced = eligible[["sample_id", "generator_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    balanced.insert(0, "split_id", "generator_balanced_recommended_v1")
    balanced["partition"] = balanced["sample_id"].astype(str).map(balanced_assignments)
    balanced["group_key"] = balanced["sample_id"].astype(str)
    balanced["is_primary_split"] = True

    scenario = eligible[["sample_id", "generator_id", "scenario_family", "benchmark_core_candidate", "governance_sensitive_flag", "leakage_group_id"]].copy()
    scenario.insert(0, "split_id", "generator_scenario_family_holdout_v1")
    scenario["partition"] = scenario["scenario_family"].astype(str).map(scenario_assignments)
    scenario["group_key"] = scenario["scenario_family"].astype(str)
    scenario["is_primary_split"] = False

    out = pd.concat([grouped, balanced, scenario], ignore_index=True)
    out["challenge_subset"] = out["governance_sensitive_flag"].apply(lambda v: "governance_sensitive" if as_bool(v) else "benchmark_core")
    return out


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
            "line_overload_binary_classification",
            "line_loading_regression",
            "generator_top_dispatch_classification",
            "generator_dispatch_regression",
        ],
        "primary_splits": [
            "line_balanced_recommended_v1",
            "generator_balanced_recommended_v1",
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
            "Strict grouped challenge splits remain sparse and are intended for robustness stress testing rather than sole headline reporting.",
            "Recommended balanced splits trade entity-level holdout strictness for paper-usable label support while retaining governance-sensitive annotations explicitly.",
            "Governance-sensitive subsets are retained explicitly rather than hidden inside the benchmark core.",
        ],
    }


def write_release_report(manifest: dict[str, Any], row_counts: dict[str, int], line_splits: pd.DataFrame, generator_splits: pd.DataFrame) -> None:
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
        "- `line_overload_binary_classification`",
        "- `line_loading_regression`",
        "- `generator_top_dispatch_classification`",
        "- `generator_dispatch_regression`",
        "",
        "## Split overview",
        "",
        markdown_table(split_rows, ["task_family", "split_id", "train_rows", "validation_rows", "test_rows"]),
        "",
        "## Split policy",
        "",
        "- `line_balanced_recommended_v1` and `generator_balanced_recommended_v1` are the official paper-usable recommended splits.",
        "- `line_grouped_entity_primary_v1` and `generator_grouped_entity_primary_v1` are retained as strict grouped challenge splits for robustness stress testing.",
        "- governance-sensitive rows remain explicitly tagged in all split outputs rather than silently removed from the release.",
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
    line_samples = build_line_benchmark_samples(stage3)
    generator_samples = build_generator_benchmark_samples(stage3)
    task_registry = build_task_registry()
    split_registry = build_split_registry()
    line_splits = build_line_splits(line_samples)
    generator_splits = build_generator_splits(generator_samples)
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
        "pt_stage4_entity_registry.csv": int(len(entity_registry)),
        "pt_stage4_sample_registry.csv": int(len(sample_registry)),
    }
    manifest = build_manifest(row_counts, stage3["manifest"])
    write_json(OUT / "pt_stage4_manifest.json", manifest)
    write_release_report(manifest, row_counts, line_splits, generator_splits)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
