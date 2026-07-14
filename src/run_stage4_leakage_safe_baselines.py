"""Run leakage-safe, multi-seed baselines for the Stage-4 diagnostic tasks.

Targets are scenario-derived. Results demonstrate consumer usability only and
must not be interpreted as prediction of observed Portuguese grid operation.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    f1_score,
    mean_absolute_error,
    mean_squared_error,
    r2_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

import config
from utils import ensure_directories, markdown_table, utc_now, write_json, write_text


STAGE4 = config.PROCESSED_DIR / "dataset_release_stage4"
STAGE5 = config.PROCESSED_DIR / "dataset_release_stage5"
OUT = config.PROCESSED_DIR / "learning_baselines"
REPORT = config.REPORTS_DIR / "100_stage4_leakage_safe_baselines.md"
SEEDS = [11, 23, 37, 53, 71]

LINE_NUMERIC = [
    "scenario_value",
    "length_km",
    "r_ohm_per_km",
    "x_ohm_per_km",
    "c_nf_per_km",
    "max_i_ka",
    "from_bus_degree",
    "to_bus_degree",
    "from_bus_voltage_kv",
    "to_bus_voltage_kv",
    "r_over_x",
    "overhead_share",
    "cable_share",
]
LINE_CATEGORICAL = [
    "scenario_family",
    "asset_type",
    "parameterization_basis",
    "policy_class",
    "bridge_role",
    "is_mixed_corridor",
    "is_policy_weighted_mixed",
    "is_parallel_equivalent_required",
    "endpoint_voltage_pair",
]
GENERATOR_NUMERIC = [
    "scenario_value",
    "pmax_mw_proxy",
    "pmin_mw_proxy",
    "marginal_cost_eur_per_mwh",
    "bus_voltage_kv",
]
GENERATOR_CATEGORICAL = [
    "scenario_family",
    "generation_type",
    "dispatch_proxy_class",
    "cost_class",
    "must_run",
    "curtailable",
    "benchmark_usable",
    "bus_zone",
    "is_dispatchable_proxy",
    "is_import_interface_proxy",
]

TARGET_DERIVED_FORBIDDEN = {
    "target_value",
    "metric_max_loading_percent",
    "over_80_loading_flag",
    "over_100_loading_flag",
    "top_congested_flag",
    "binding_under_internal_dispatch_flag",
    "max_target_loading_percent",
    "top_congested_appearance_count",
    "bottleneck_persistence_count",
    "repeated_bottleneck",
    "preferred_next_scenario",
    "dispatch_mw",
    "dispatch_share_of_total_gen",
    "dispatch_positive_flag",
    "top_dispatch_flag",
    "import_dependence_flag",
    "max_dispatch_mw",
    "max_dispatch_share_of_total_gen",
    "top_dispatch_appearance_count",
}

TASKS = [
    {
        "task_name": "line_loading_regression",
        "task_type": "regression",
        "supervision": "pt_stage5_line_supervision_adapter.csv",
        "entity": "pt_stage5_graph_edge_adapter.csv",
        "join_key": "line_id",
        "numeric": LINE_NUMERIC,
        "categorical": LINE_CATEGORICAL,
    },
    {
        "task_name": "generator_dispatch_regression",
        "task_type": "regression",
        "supervision": "pt_stage5_generator_supervision_adapter.csv",
        "entity": "pt_stage5_generator_node_adapter.csv",
        "join_key": "generator_id",
        "numeric": GENERATOR_NUMERIC,
        "categorical": GENERATOR_CATEGORICAL,
    },
    {
        "task_name": "line_relative_high_stress_classification",
        "task_type": "classification",
        "supervision": "pt_stage5_line_supervision_adapter.csv",
        "entity": "pt_stage5_graph_edge_adapter.csv",
        "join_key": "line_id",
        "numeric": LINE_NUMERIC,
        "categorical": LINE_CATEGORICAL,
    },
    {
        "task_name": "generator_relative_high_dispatch_classification",
        "task_type": "classification",
        "supervision": "pt_stage5_generator_supervision_adapter.csv",
        "entity": "pt_stage5_generator_node_adapter.csv",
        "join_key": "generator_id",
        "numeric": GENERATOR_NUMERIC,
        "categorical": GENERATOR_CATEGORICAL,
    },
]


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    if isinstance(value, (int, float, np.integer, np.floating)):
        if float(value) in {0.0, 1.0}:
            return bool(int(value))
        raise ValueError(f"Invalid numeric boolean label: {value!r}")
    normalized = str(value).strip().lower()
    if normalized not in {"true", "false", "1", "0", "yes", "no", "y", "n"}:
        raise ValueError(f"Invalid boolean label: {value!r}")
    return normalized in {"true", "1", "yes", "y"}


def require_inputs() -> None:
    required = [
        STAGE4 / "pt_stage4_validation_summary.json",
        STAGE5 / "pt_stage5_validation_summary.json",
        STAGE5 / "pt_stage5_feature_contract.csv",
    ]
    required.extend(STAGE5 / task[key] for task in TASKS for key in ("supervision", "entity"))
    missing = sorted({str(path) for path in required if not path.exists()})
    if missing:
        raise RuntimeError("Fail-closed: missing baseline inputs:\n- " + "\n- ".join(missing))
    for path in required[:2]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("status") != "PASS":
            raise RuntimeError(f"Fail-closed: upstream validation is not PASS: {path}")


def preprocessor(numeric: list[str], categorical: list[str]) -> ColumnTransformer:
    numeric_pipeline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="median")),
            ("scale", StandardScaler()),
        ]
    )
    categorical_pipeline = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("onehot", OneHotEncoder(handle_unknown="ignore", sparse_output=False)),
        ]
    )
    return ColumnTransformer(
        [("numeric", numeric_pipeline, numeric), ("categorical", categorical_pipeline, categorical)],
        remainder="drop",
        sparse_threshold=0.0,
    )


def models(task_type: str, seed: int) -> dict[str, Any]:
    if task_type == "regression":
        return {
            "dummy_mean": DummyRegressor(strategy="mean"),
            "ridge": Ridge(alpha=1.0),
            "gbdt": GradientBoostingRegressor(
                random_state=seed,
                n_estimators=150,
                learning_rate=0.04,
                max_depth=2,
                subsample=0.8,
                loss="squared_error",
            ),
        }
    return {
        "dummy_prior": DummyClassifier(strategy="prior", random_state=seed),
        "logistic": LogisticRegression(
            max_iter=2000,
            class_weight="balanced",
            random_state=seed,
        ),
        "gbdt": GradientBoostingClassifier(
            random_state=seed,
            n_estimators=150,
            learning_rate=0.04,
            max_depth=2,
            subsample=0.8,
        ),
    }


def load_task(task: dict[str, Any]) -> tuple[pd.DataFrame, list[str], list[str]]:
    supervision = pd.read_csv(STAGE5 / str(task["supervision"]))
    entity = pd.read_csv(STAGE5 / str(task["entity"]))
    scoped = supervision[supervision["task_name"].astype(str) == str(task["task_name"])].copy()
    scoped = scoped[scoped["recommended_partition"].astype(str).isin({"train", "validation", "test"})]
    scoped = scoped[scoped["benchmark_core_candidate"].map(as_bool)]
    join_key = str(task["join_key"])
    entity[join_key] = entity[join_key].astype(str)
    scoped[join_key] = scoped[join_key].astype(str)
    duplicate_columns = set(scoped.columns) & set(entity.columns) - {join_key}
    entity = entity.drop(columns=sorted(duplicate_columns), errors="ignore")
    frame = scoped.merge(entity, on=join_key, how="inner", validate="many_to_one")
    if len(frame) != len(scoped):
        raise RuntimeError(f"Fail-closed: entity feature join lost rows for {task['task_name']}")
    frame["scenario_value"] = pd.to_numeric(frame["scenario_value"], errors="coerce")
    if frame["target_value"].isna().any():
        raise RuntimeError(f"Fail-closed: missing target values for {task['task_name']}")
    if task["task_type"] == "classification":
        frame["target_value"] = frame["target_value"].map(as_bool)
    else:
        frame["target_value"] = pd.to_numeric(frame["target_value"], errors="coerce")
        if frame["target_value"].isna().any():
            raise RuntimeError(f"Fail-closed: non-numeric regression targets for {task['task_name']}")

    leakage = frame.groupby("entity_id")["recommended_partition"].nunique()
    if int((leakage > 1).sum()):
        raise RuntimeError(f"Fail-closed: entity leakage detected for {task['task_name']}")
    partitions = set(frame["recommended_partition"].astype(str))
    if partitions != {"train", "validation", "test"}:
        raise RuntimeError(f"Fail-closed: incomplete partitions for {task['task_name']}: {partitions}")

    train_mask = frame["recommended_partition"].astype(str).eq("train")
    numeric = [
        column for column in task["numeric"]
        if column in frame.columns and frame.loc[train_mask, column].notna().any()
    ]
    categorical = [
        column for column in task["categorical"]
        if column in frame.columns and frame.loc[train_mask, column].notna().any()
    ]
    selected = set(numeric) | set(categorical)
    overlap = selected & TARGET_DERIVED_FORBIDDEN
    if overlap:
        raise RuntimeError(f"Fail-closed: target-derived baseline features selected: {sorted(overlap)}")
    if not numeric and not categorical:
        raise RuntimeError(f"Fail-closed: no usable features for {task['task_name']}")
    return frame, numeric, categorical


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(math.sqrt(mean_squared_error(y_true, y_pred))),
        "r2": float(r2_score(y_true, y_pred)) if len(y_true) >= 2 else math.nan,
    }


def classification_metrics(y_true: np.ndarray, y_pred: np.ndarray, y_score: np.ndarray) -> dict[str, float]:
    both = len(np.unique(y_true)) == 2
    return {
        "average_precision": float(average_precision_score(y_true, y_score)) if both else math.nan,
        "roc_auc": float(roc_auc_score(y_true, y_score)) if both else math.nan,
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)) if both else math.nan,
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
    }


def positive_score(pipeline: Pipeline, features: pd.DataFrame) -> np.ndarray:
    probabilities = pipeline.predict_proba(features)
    classes = list(pipeline.named_steps["model"].classes_)
    positive_index = classes.index(True) if True in classes else classes.index(1)
    return probabilities[:, positive_index]


def run_task(task: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    frame, numeric, categorical = load_task(task)
    feature_rows = [
        {
            "task_name": task["task_name"],
            "feature": feature,
            "feature_kind": "numeric" if feature in numeric else "categorical",
            "source_role": "EX_ANTE_SCENARIO_CONTROL" if feature in {"scenario_value", "scenario_family"} else "STATIC_ENTITY_FEATURE",
            "used": True,
            "target_derived": False,
            "notes": "Scenario controls are declared inputs to diagnostic scenario prediction; they are not observed operating variables." if feature in {"scenario_value", "scenario_family"} else "",
        }
        for feature in numeric + categorical
    ]
    selected_features = set(numeric) | set(categorical)
    requested_features = set(task["numeric"]) | set(task["categorical"])
    feature_rows.extend(
        {
            "task_name": task["task_name"],
            "feature": feature,
            "feature_kind": "excluded",
            "source_role": "TRAIN_PARTITION_ALL_MISSING_OR_UNAVAILABLE",
            "used": False,
            "target_derived": False,
            "notes": "Excluded before fitting because the feature was absent or had no observed training values.",
        }
        for feature in sorted(requested_features - selected_features)
    )
    feature_rows.extend(
        {
            "task_name": task["task_name"],
            "feature": feature,
            "feature_kind": "excluded",
            "source_role": "POST_SIMULATION_OUTCOME",
            "used": False,
            "target_derived": True,
            "notes": "Explicitly forbidden to prevent target leakage.",
        }
        for feature in sorted(TARGET_DERIVED_FORBIDDEN)
    )

    train = frame[frame["recommended_partition"] == "train"].copy()
    performance: list[dict[str, Any]] = []
    predictions: list[dict[str, Any]] = []
    for seed in SEEDS:
        for model_name, estimator in models(str(task["task_type"]), seed).items():
            pipeline = Pipeline(
                [
                    ("preprocess", preprocessor(numeric, categorical)),
                    ("model", estimator),
                ]
            )
            y_train = train["target_value"].to_numpy()
            if task["task_type"] == "classification":
                y_train = np.asarray([as_bool(value) for value in y_train], dtype=bool)
                if len(np.unique(y_train)) < 2:
                    raise RuntimeError(f"Fail-closed: training partition has one class for {task['task_name']}")
            started = time.perf_counter()
            pipeline.fit(train[numeric + categorical], y_train)
            fit_seconds = time.perf_counter() - started
            for partition in ("validation", "test"):
                subset = frame[frame["recommended_partition"] == partition].copy()
                y_true = subset["target_value"].to_numpy()
                if task["task_type"] == "classification":
                    y_true = np.asarray([as_bool(value) for value in y_true], dtype=bool)
                y_pred = pipeline.predict(subset[numeric + categorical])
                if task["task_type"] == "regression":
                    metrics = regression_metrics(y_true.astype(float), np.asarray(y_pred, dtype=float))
                    y_score = np.full(len(subset), np.nan)
                else:
                    y_score = positive_score(pipeline, subset[numeric + categorical])
                    metrics = classification_metrics(y_true, np.asarray(y_pred, dtype=bool), y_score)
                performance.append(
                    {
                        "task_name": task["task_name"],
                        "task_type": task["task_type"],
                        "model_name": model_name,
                        "seed": seed,
                        "partition": partition,
                        "train_rows": len(train),
                        "evaluation_rows": len(subset),
                        "train_entities": train["entity_id"].nunique(),
                        "evaluation_entities": subset["entity_id"].nunique(),
                        "feature_count_before_encoding": len(numeric) + len(categorical),
                        "fit_seconds": fit_seconds,
                        **metrics,
                    }
                )
                for position, (_, sample) in enumerate(subset.iterrows()):
                    predictions.append(
                        {
                            "task_name": task["task_name"],
                            "model_name": model_name,
                            "seed": seed,
                            "partition": partition,
                            "sample_id": sample["sample_id"],
                            "entity_id": sample["entity_id"],
                            "y_true": bool(y_true[position]) if task["task_type"] == "classification" else float(y_true[position]),
                            "y_pred": bool(y_pred[position]) if task["task_type"] == "classification" else float(y_pred[position]),
                            "y_score": float(y_score[position]) if task["task_type"] == "classification" else math.nan,
                        }
                    )
    return performance, predictions, feature_rows


def summarize(performance: pd.DataFrame) -> pd.DataFrame:
    metric_columns = ["mae", "rmse", "r2", "average_precision", "roc_auc", "balanced_accuracy", "f1"]
    rows = []
    for keys, group in performance.groupby(["task_name", "task_type", "model_name", "partition"], sort=True):
        row = dict(zip(["task_name", "task_type", "model_name", "partition"], keys))
        row["seed_runs"] = int(group["seed"].nunique())
        row["evaluation_rows"] = int(group["evaluation_rows"].iloc[0])
        row["evaluation_entities"] = int(group["evaluation_entities"].iloc[0])
        for metric in metric_columns:
            values = pd.to_numeric(group[metric], errors="coerce").dropna() if metric in group else pd.Series(dtype=float)
            row[f"{metric}_mean"] = float(values.mean()) if len(values) else math.nan
            row[f"{metric}_std"] = float(values.std(ddof=1)) if len(values) > 1 else 0.0 if len(values) else math.nan
        rows.append(row)
    return pd.DataFrame(rows)


def main() -> None:
    ensure_directories()
    require_inputs()
    OUT.mkdir(parents=True, exist_ok=True)
    all_performance: list[dict[str, Any]] = []
    all_predictions: list[dict[str, Any]] = []
    all_features: list[dict[str, Any]] = []
    for task in TASKS:
        performance, predictions, features = run_task(task)
        all_performance.extend(performance)
        all_predictions.extend(predictions)
        all_features.extend(features)

    performance_df = pd.DataFrame(all_performance)
    predictions_df = pd.DataFrame(all_predictions)
    features_df = pd.DataFrame(all_features).drop_duplicates()
    summary_df = summarize(performance_df)
    skipped = pd.DataFrame(
        [
            {
                "task_name": "line_overload_binary_classification",
                "status": "SKIPPED_INSUFFICIENT_SUPPORT",
                "reason": "0 positive benchmark-core entities",
            },
            {
                "task_name": "generator_top_dispatch_classification",
                "status": "SKIPPED_INSUFFICIENT_SUPPORT",
                "reason": "1 positive benchmark-core entity cannot support three-way grouped evaluation",
            },
        ]
    )
    performance_df.to_csv(OUT / "pt_stage4_baseline_runs.csv", index=False)
    summary_df.to_csv(OUT / "pt_stage4_baseline_summary.csv", index=False)
    predictions_df.to_csv(OUT / "pt_stage4_baseline_predictions.csv", index=False)
    features_df.to_csv(OUT / "pt_stage4_baseline_feature_audit.csv", index=False)
    skipped.to_csv(OUT / "pt_stage4_baseline_skipped_tasks.csv", index=False)

    manifest = {
        "generated_at": utc_now(),
        "status": "PASS_DIAGNOSTIC_BASELINES",
        "tasks_run": [task["task_name"] for task in TASKS],
        "tasks_skipped": skipped["task_name"].tolist(),
        "seeds": SEEDS,
        "run_rows": int(len(performance_df)),
        "prediction_rows": int(len(predictions_df)),
        "entity_leakage_violations": 0,
        "target_derived_features_used": 0,
        "fit_preprocessing_on_train_only": True,
        "publication_allowed": False,
        "diagnostic_only": True,
        "label_semantics": "scenario-derived diagnostic targets, not observed Portuguese operations",
    }
    write_json(OUT / "pt_stage4_baseline_manifest.json", manifest)

    test_summary = summary_df[summary_df["partition"] == "test"].copy()
    display_columns = [
        "task_name",
        "model_name",
        "seed_runs",
        "evaluation_rows",
        "mae_mean",
        "rmse_mean",
        "r2_mean",
        "average_precision_mean",
        "balanced_accuracy_mean",
    ]
    display = test_summary[[column for column in display_columns if column in test_summary.columns]].fillna("")
    text = [
        "# 100 Stage-4 Leakage-Safe Baselines",
        "",
        f"Generated: {manifest['generated_at']}",
        "",
        "Status: `PASS_DIAGNOSTIC_BASELINES`",
        "",
        "The baselines use strict entity-grouped Stage-4 partitions. Preprocessing is fitted on train rows only. Post-simulation loading/dispatch outcomes and their aggregates are excluded. Scenario family/value are declared ex-ante diagnostic controls, not observed operating features.",
        "",
        "## Test metrics",
        "",
        markdown_table(display.to_dict("records"), list(display.columns)),
        "",
        "## Models",
        "",
        "- regression: dummy mean, ridge, stochastic gradient-boosted trees",
        "- auxiliary classification: dummy prior, class-balanced logistic regression, stochastic gradient-boosted trees",
        f"- seeds: {SEEDS}",
        "",
        "## Tasks not trained",
        "",
        markdown_table(skipped.to_dict("records"), list(skipped.columns)),
        "",
        "## Boundary",
        "",
        "These results verify dataset and consumer plumbing. Labels are generated by diagnostic scenarios, electrical inputs contain proxy/benchmark values, and no result establishes real-grid predictive validity.",
    ]
    write_text(REPORT, "\n".join(text) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
