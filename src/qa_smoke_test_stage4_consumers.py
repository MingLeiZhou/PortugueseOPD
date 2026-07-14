from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import ensure_directories, utc_now, write_json

OUT = config.PROCESSED_DIR / "dataset_release_stage4"
SUMMARY_PATH = OUT / "pt_stage4_qa_smoke_summary.json"

REQUIRED_FILES = {
    "line_samples": OUT / "pt_stage4_line_risk_benchmark_samples.csv",
    "generator_samples": OUT / "pt_stage4_generator_risk_benchmark_samples.csv",
    "task_registry": OUT / "pt_stage4_task_registry.csv",
    "split_registry": OUT / "pt_stage4_split_registry.csv",
    "line_splits": OUT / "pt_stage4_line_sample_splits.csv",
    "generator_splits": OUT / "pt_stage4_generator_sample_splits.csv",
    "label_support_audit": OUT / "pt_stage4_label_support_audit.csv",
    "manifest": OUT / "pt_stage4_manifest.json",
    "validation_summary": OUT / "pt_stage4_validation_summary.json",
}

LINE_TASKS = {
    "line_overload_binary_classification",
    "line_loading_regression",
    "line_relative_high_stress_classification",
}
GENERATOR_TASKS = {
    "generator_top_dispatch_classification",
    "generator_dispatch_regression",
    "generator_relative_high_dispatch_classification",
}
REQUIRED_RECOMMENDED_SPLITS = {
    "line_grouped_entity_primary_v1",
    "line_grouped_entity_relative_stress_v1",
    "generator_grouped_entity_primary_v1",
    "generator_grouped_entity_relative_dispatch_v1",
}


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def add_error(errors: list[str], message: str) -> None:
    errors.append(message)


def add_warning(warnings: list[str], message: str) -> None:
    warnings.append(message)


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
            add_error(errors, f"Missing required stage-4 QA file: {name} -> {path}")


def count_group_leakage(split_df: pd.DataFrame) -> int:
    if split_df.empty:
        return 0
    return int(split_df.groupby("leakage_group_id")["partition"].nunique().gt(1).sum())


def check_recommended_split(
    *,
    split_df: pd.DataFrame,
    sample_df: pd.DataFrame,
    split_id: str,
    sample_id_column: str,
    regression_target: str,
    classification_target: str,
    label: str,
    errors: list[str],
    check_counts: dict[str, Any],
) -> None:
    scoped = split_df[split_df["split_id"].astype(str) == split_id].copy()
    if scoped.empty:
        add_error(errors, f"{label} recommended split {split_id} has no rows")
        check_counts[f"{label}_recommended_rows"] = 0
        return

    merged = scoped.merge(sample_df, on=sample_id_column, how="left", validate="many_to_one", suffixes=("_split", ""))
    missing = int(merged[regression_target].isna().sum() + merged[classification_target].isna().sum())
    if missing:
        add_error(errors, f"{label} recommended split {split_id} has {missing} missing joined target values")

    partitions = set(scoped["partition"].astype(str))
    missing_partitions = {"train", "validation", "test"} - partitions
    if missing_partitions:
        add_error(errors, f"{label} recommended split {split_id} missing partitions: {', '.join(sorted(missing_partitions))}")

    empty_partition_count = 0
    no_positive_partition_count = 0
    for partition in ["train", "validation", "test"]:
        part = merged[merged["partition"].astype(str) == partition]
        if part.empty:
            empty_partition_count += 1
            add_error(errors, f"{label} recommended split {split_id} partition {partition} is empty")
            continue
        positives = part[classification_target].astype(str).str.lower() == "true"
        if not positives.any():
            no_positive_partition_count += 1
            add_error(errors, f"{label} recommended split {split_id} partition {partition} has no positive classification rows")

    negative_regression = int((pd.to_numeric(merged[regression_target], errors="coerce") < 0).sum())
    if negative_regression:
        add_error(errors, f"{label} recommended split {split_id} has {negative_regression} negative regression targets")

    check_counts[f"{label}_recommended_rows"] = int(len(scoped))
    check_counts[f"{label}_recommended_missing_partitions"] = int(len(missing_partitions))
    check_counts[f"{label}_recommended_empty_partitions"] = empty_partition_count
    check_counts[f"{label}_recommended_no_positive_partitions"] = no_positive_partition_count
    check_counts[f"{label}_recommended_negative_regression_rows"] = negative_regression


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
        raise RuntimeError("Stage-4 QA smoke test failed before reading required files.")

    line_samples = read_csv(REQUIRED_FILES["line_samples"])
    generator_samples = read_csv(REQUIRED_FILES["generator_samples"])
    task_registry = read_csv(REQUIRED_FILES["task_registry"])
    split_registry = read_csv(REQUIRED_FILES["split_registry"])
    line_splits = read_csv(REQUIRED_FILES["line_splits"])
    generator_splits = read_csv(REQUIRED_FILES["generator_splits"])
    label_support = read_csv(REQUIRED_FILES["label_support_audit"])
    manifest = read_json(REQUIRED_FILES["manifest"])
    validation_summary = read_json(REQUIRED_FILES["validation_summary"])

    if validation_summary.get("status") != "PASS":
        add_error(errors, "Stage-4 validation summary must be PASS before consumer smoke testing")
    if bool(manifest.get("publication_allowed", True)):
        add_error(errors, "Stage-4 manifest publication_allowed should remain false")
    if not bool(manifest.get("diagnostic_only", False)):
        add_error(errors, "Stage-4 manifest diagnostic_only should remain true")
    if bool(manifest.get("operator_grade_ready", True)):
        add_error(errors, "Stage-4 manifest operator_grade_ready should remain false")
    if not bool(manifest.get("ml_ready", False)):
        add_error(errors, "Stage-4 manifest ml_ready should remain true")

    task_names = set(task_registry["task_name"].astype(str))
    missing_tasks = (LINE_TASKS | GENERATOR_TASKS) - task_names
    if missing_tasks:
        add_error(errors, f"Stage-4 task registry missing expected tasks: {', '.join(sorted(missing_tasks))}")

    split_ids = set(split_registry["split_id"].astype(str))
    missing_recommended = REQUIRED_RECOMMENDED_SPLITS - split_ids
    if missing_recommended:
        add_error(errors, f"Stage-4 split registry missing recommended splits: {', '.join(sorted(missing_recommended))}")

    unresolved_recommended = set(task_registry["recommended_split_id"].astype(str)) - split_ids
    if unresolved_recommended:
        add_error(errors, f"Stage-4 task registry references unknown splits: {', '.join(sorted(unresolved_recommended))}")

    recommended_primary = set(
        split_registry[split_registry["is_primary_recommended_split"].apply(as_bool)]["split_id"].astype(str)
    )
    if recommended_primary != REQUIRED_RECOMMENDED_SPLITS:
        add_error(errors, "Stage-4 primary recommended splits do not match the expected grouped evaluation split ids")

    line_challenge = line_splits[line_splits["split_id"].astype(str) == "line_grouped_entity_primary_v1"].copy()
    generator_challenge = generator_splits[generator_splits["split_id"].astype(str) == "generator_grouped_entity_primary_v1"].copy()
    if line_challenge.empty:
        add_error(errors, "Stage-4 line grouped challenge split is missing")
    if generator_challenge.empty:
        add_error(errors, "Stage-4 generator grouped challenge split is missing")

    line_challenge_noncore = int((line_challenge["challenge_subset"].astype(str) != "benchmark_core").sum()) if not line_challenge.empty else 0
    generator_challenge_noncore = int((generator_challenge["challenge_subset"].astype(str) != "benchmark_core").sum()) if not generator_challenge.empty else 0
    if line_challenge_noncore:
        add_error(errors, f"Stage-4 line grouped challenge split has {line_challenge_noncore} non-core rows")
    if generator_challenge_noncore:
        add_error(errors, f"Stage-4 generator grouped challenge split has {generator_challenge_noncore} non-core rows")

    line_challenge_leakage = count_group_leakage(line_challenge)
    generator_challenge_leakage = count_group_leakage(generator_challenge)
    if line_challenge_leakage:
        add_error(errors, f"Stage-4 line grouped challenge split has {line_challenge_leakage} leakage groups spanning partitions")
    if generator_challenge_leakage:
        add_error(errors, f"Stage-4 generator grouped challenge split has {generator_challenge_leakage} leakage groups spanning partitions")

    check_counts["line_grouped_challenge_rows"] = int(len(line_challenge))
    check_counts["generator_grouped_challenge_rows"] = int(len(generator_challenge))
    check_counts["line_grouped_challenge_leakage_groups"] = line_challenge_leakage
    check_counts["generator_grouped_challenge_leakage_groups"] = generator_challenge_leakage

    support_index = label_support.set_index(["task_name", "subset"])
    expected_support = {
        ("line_overload_binary_classification", "benchmark_core"): "NO_POSITIVE_ENTITIES",
        ("generator_top_dispatch_classification", "benchmark_core"): "INSUFFICIENT_FOR_THREE_WAY_GROUPED",
        ("line_relative_high_stress_classification", "benchmark_core"): "LIMITED_GROUPED_SUPPORT",
        ("generator_relative_high_dispatch_classification", "benchmark_core"): "LIMITED_GROUPED_SUPPORT",
    }
    support_mismatches = 0
    for key, expected in expected_support.items():
        if key not in support_index.index or str(support_index.loc[key, "support_status"]) != expected:
            support_mismatches += 1
            add_error(errors, f"Stage-4 support audit mismatch for {key[0]} / {key[1]}")
    check_counts["label_support_status_mismatches"] = support_mismatches

    check_recommended_split(
        split_df=line_splits,
        sample_df=line_samples,
        split_id="line_balanced_recommended_v1",
        sample_id_column="sample_id",
        regression_target="line_loading_regression_target",
        classification_target="line_overload_binary_classification_target",
        label="line",
        errors=errors,
        check_counts=check_counts,
    )
    check_recommended_split(
        split_df=generator_splits,
        sample_df=generator_samples,
        split_id="generator_balanced_recommended_v1",
        sample_id_column="sample_id",
        regression_target="generator_dispatch_regression_target",
        classification_target="generator_top_dispatch_classification_target",
        label="generator",
        errors=errors,
        check_counts=check_counts,
    )
    check_recommended_split(
        split_df=line_splits,
        sample_df=line_samples,
        split_id="line_grouped_entity_relative_stress_v1",
        sample_id_column="sample_id",
        regression_target="line_loading_regression_target",
        classification_target="line_relative_high_stress_classification_target",
        label="line_relative_grouped",
        errors=errors,
        check_counts=check_counts,
    )
    check_recommended_split(
        split_df=generator_splits,
        sample_df=generator_samples,
        split_id="generator_grouped_entity_relative_dispatch_v1",
        sample_id_column="sample_id",
        regression_target="generator_dispatch_regression_target",
        classification_target="generator_relative_high_dispatch_classification_target",
        label="generator_relative_grouped",
        errors=errors,
        check_counts=check_counts,
    )

    line_relative_split = line_splits[
        line_splits["split_id"].astype(str) == "line_grouped_entity_relative_stress_v1"
    ]
    generator_relative_split = generator_splits[
        generator_splits["split_id"].astype(str) == "generator_grouped_entity_relative_dispatch_v1"
    ]
    line_relative_leakage = count_group_leakage(line_relative_split)
    generator_relative_leakage = count_group_leakage(generator_relative_split)
    if line_relative_leakage:
        add_error(errors, f"Stage-4 line relative grouped split has {line_relative_leakage} leakage groups")
    if generator_relative_leakage:
        add_error(errors, f"Stage-4 generator relative grouped split has {generator_relative_leakage} leakage groups")
    check_counts["line_relative_grouped_leakage_groups"] = line_relative_leakage
    check_counts["generator_relative_grouped_leakage_groups"] = generator_relative_leakage

    invalid_generator_dispatch = int(
        ((generator_samples["generator_top_dispatch_classification_target"].astype(str).str.lower() == "true")
         & (pd.to_numeric(generator_samples["generator_dispatch_regression_target"], errors="coerce") <= 0)).sum()
    )
    if invalid_generator_dispatch:
        add_error(errors, f"Stage-4 generator sample table has {invalid_generator_dispatch} positive classification rows with non-positive dispatch targets")
    check_counts["generator_invalid_positive_dispatch_rows"] = invalid_generator_dispatch

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
        raise RuntimeError("Stage-4 QA consumer smoke test failed.")


if __name__ == "__main__":
    main()
