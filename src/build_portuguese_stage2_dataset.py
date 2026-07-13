from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import ensure_directories, markdown_table, utc_now, write_json, write_text

ROOT = config.ROOT_DIR
PROCESSED = config.PROCESSED_DIR
REPORTS = config.REPORTS_DIR
STAGE1 = PROCESSED / "dataset_release_stage1"
OUT = PROCESSED / "dataset_release_stage2"
RELEASE_REPORT = REPORTS / "90_stage2_dataset_release_note.md"

LINE_POLICY = PROCESSED / "mixed_corridor_policy_table.csv"
ACPF_TRACE = PROCESSED / "acpf_ready" / "pt_acpf_source_traceability.csv"
ACPF_ASSUMPTIONS = PROCESSED / "acpf_ready" / "pt_acpf_assumption_register.csv"
ACPF_COMPONENT_DIAG = PROCESSED / "acpf_diagnostics" / "pt_acpf_component_diagnostics.csv"
S20_ATTEMPTS = PROCESSED / "dcopf_s20_strict_internal" / "s20_strict_internal_dcopf_attempts.csv"
S21_LINE_RESULTS = PROCESSED / "dcopf_s21_diagnostics" / "s21_dcopf_line_results_by_scale.csv"
S21_GEN_RESULTS = PROCESSED / "dcopf_s21_diagnostics" / "s21_dcopf_gen_results_by_scale.csv"
S22_ATTEMPTS = PROCESSED / "s22_mixed_corridor_remediation" / "s22_dcopf_attempts.csv"
S30_SUMMARY = PROCESSED / "dcopf_s30_atpl_00147_parallel_equivalent" / "s30_summary.csv"
S30_TOP_LINES = PROCESSED / "dcopf_s30_atpl_00147_parallel_equivalent" / "s30_top_lines.csv"
S30_TOP_GENERATORS = PROCESSED / "dcopf_s30_atpl_00147_parallel_equivalent" / "s30_top_generators.csv"

REQUIRED_INPUTS = [
    STAGE1 / "pt_dataset_buses.csv",
    STAGE1 / "pt_dataset_lines.csv",
    STAGE1 / "pt_dataset_loads.csv",
    STAGE1 / "pt_dataset_generators.csv",
    STAGE1 / "pt_dataset_generator_assignment.csv",
    STAGE1 / "pt_dataset_generator_dispatch_proxy.csv",
    STAGE1 / "pt_dataset_generator_costs.csv",
    STAGE1 / "pt_dataset_benchmark_summaries.csv",
    STAGE1 / "pt_dataset_line_policy.csv",
    STAGE1 / "pt_dataset_manifest.json",
    LINE_POLICY,
    ACPF_TRACE,
    ACPF_ASSUMPTIONS,
    ACPF_COMPONENT_DIAG,
    S20_ATTEMPTS,
    S21_LINE_RESULTS,
    S21_GEN_RESULTS,
    S22_ATTEMPTS,
    S30_SUMMARY,
    S30_TOP_LINES,
    S30_TOP_GENERATORS,
]

DATASET_ID = "pt_grid_benchmark_stage2"
RELEASE_ID = "pt_grid_benchmark_stage2_v1"
SCHEMA_VERSION = "1.0"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def read_line_policy_csv(path: Path) -> pd.DataFrame:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    return pd.DataFrame(rows)


def require_inputs() -> None:
    missing = [str(path) for path in REQUIRED_INPUTS if not path.exists()]
    if missing:
        raise RuntimeError("Fail-closed: required stage-2 inputs are missing:\n- " + "\n- ".join(missing))


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def load_stage1() -> dict[str, Any]:
    return {
        "buses": read_csv(STAGE1 / "pt_dataset_buses.csv"),
        "lines": read_csv(STAGE1 / "pt_dataset_lines.csv"),
        "loads": read_csv(STAGE1 / "pt_dataset_loads.csv"),
        "generators": read_csv(STAGE1 / "pt_dataset_generators.csv"),
        "assignment": read_csv(STAGE1 / "pt_dataset_generator_assignment.csv"),
        "dispatch_proxy": read_csv(STAGE1 / "pt_dataset_generator_dispatch_proxy.csv"),
        "generator_costs": read_csv(STAGE1 / "pt_dataset_generator_costs.csv"),
        "benchmark_summaries": read_csv(STAGE1 / "pt_dataset_benchmark_summaries.csv"),
        "line_policy": read_csv(STAGE1 / "pt_dataset_line_policy.csv"),
        "manifest": read_json(STAGE1 / "pt_dataset_manifest.json"),
    }


def prepare_stage1_frames(stage1: dict[str, Any]) -> dict[str, Any]:
    buses = stage1["buses"].copy()
    lines = stage1["lines"].copy()
    loads = stage1["loads"].copy()
    generators = stage1["generators"].copy()
    line_policy = stage1["line_policy"].copy()

    for frame, columns in [
        (buses, ["bus_id"]),
        (lines, ["line_id", "line_name", "from_bus", "to_bus"]),
        (loads, ["load_id", "bus_id"]),
        (generators, ["generator_id", "bus_id"]),
        (line_policy, ["line_id"]),
    ]:
        for column in columns:
            frame[column] = frame[column].astype(str)

    return {
        **stage1,
        "buses": buses,
        "lines": lines,
        "loads": loads,
        "generators": generators,
        "line_policy": line_policy,
    }


def build_bus_features(stage1: dict[str, Any]) -> pd.DataFrame:
    buses = stage1["buses"].copy()
    lines = stage1["lines"].copy()
    loads = stage1["loads"].copy()
    generators = stage1["generators"].copy()
    policy = stage1["line_policy"].copy()

    line_aug = lines.merge(policy.add_prefix("policy_"), left_on="line_name", right_on="policy_line_id", how="left")

    incident_rows: list[dict[str, Any]] = []
    for _, row in line_aug.iterrows():
        for endpoint in ["from_bus", "to_bus"]:
            incident_rows.append(
                {
                    "bus_id": str(row[endpoint]),
                    "asset_type": row.get("asset_type"),
                    "length_km": pd.to_numeric(row.get("length_km"), errors="coerce"),
                    "policy_class": row.get("policy_policy_class"),
                }
            )
    incident = pd.DataFrame(incident_rows)

    if incident.empty:
        incident_summary = pd.DataFrame(columns=["bus_id"])
    else:
        incident_summary = incident.groupby("bus_id").agg(
            degree_total=("bus_id", "size"),
            incident_line_count=("bus_id", "size"),
            incident_total_length_km=("length_km", "sum"),
            incident_cable_count=("asset_type", lambda s: int((s == "cable").sum())),
            incident_overhead_count=("asset_type", lambda s: int((s == "overhead").sum())),
            incident_mixed_count=("asset_type", lambda s: int((s == "mixed").sum())),
            has_policy_governed_incident_line=("policy_class", lambda s: bool(s.fillna("").ne("").any())),
            has_parallel_equivalent_required_incident_line=("policy_class", lambda s: bool((s == "MIXED_PARALLEL_EQUIVALENT_REQUIRED").any())),
        ).reset_index()

    load_summary = loads.groupby("bus_id").agg(
        connected_load_count=("load_id", "size"),
        connected_load_p_mw=("p_mw", "sum"),
        connected_load_q_mvar=("q_mvar", "sum"),
    ).reset_index()

    generator_summary = generators.groupby("bus_id").agg(
        connected_generator_count=("generator_id", "size"),
        connected_generator_pmax_mw=("pmax_mw_proxy", "sum"),
        connected_dispatchable_proxy_count=("dispatch_proxy_class", lambda s: int((s == "dispatchable_proxy").sum())),
        connected_import_proxy_count=("dispatch_proxy_class", lambda s: int((s == "import_interface_proxy").sum())),
    ).reset_index()

    out = buses.merge(incident_summary, on="bus_id", how="left")
    out = out.merge(load_summary, on="bus_id", how="left")
    out = out.merge(generator_summary, on="bus_id", how="left")

    fill_zero = [
        "degree_total",
        "incident_line_count",
        "incident_total_length_km",
        "incident_cable_count",
        "incident_overhead_count",
        "incident_mixed_count",
        "connected_load_count",
        "connected_load_p_mw",
        "connected_load_q_mvar",
        "connected_generator_count",
        "connected_generator_pmax_mw",
        "connected_dispatchable_proxy_count",
        "connected_import_proxy_count",
    ]
    for column in fill_zero:
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0)
    for column in ["has_policy_governed_incident_line", "has_parallel_equivalent_required_incident_line"]:
        out[column] = out[column].apply(as_bool)
    return out


def build_line_targets(stage1: dict[str, Any]) -> pd.DataFrame:
    lines = stage1["lines"].copy()
    line_map = lines[["line_id", "line_name", "asset_type", "from_bus", "to_bus", "source_scenario_id"]].copy()

    s21 = read_csv(S21_LINE_RESULTS).copy()
    s21["line_name"] = s21["name"].astype(str)
    s21 = s21.merge(line_map, on="line_name", how="inner", suffixes=("_scenario", ""))
    s21["scenario_family"] = "S21_NO_EXTGRID_GEN_ONLY_DCOPF"
    s21["variant_id"] = "load_scale_" + s21["load_scale"].astype(str)
    s21["scenario_value"] = s21["load_scale"].astype(float)
    s21["metric_max_loading_percent"] = pd.to_numeric(s21["loading_percent"], errors="coerce")
    s21["over_80_loading_flag"] = s21["metric_max_loading_percent"] >= 80
    s21["over_100_loading_flag"] = s21["metric_max_loading_percent"] >= 100
    s21["dispatch_reference_mw"] = pd.NA
    s21["top_congested_flag"] = s21.groupby("load_scale")["metric_max_loading_percent"].transform("max") == s21["metric_max_loading_percent"]
    s21["binding_under_internal_dispatch_flag"] = s21["over_100_loading_flag"]
    s21_targets = s21[
        [
            "line_id",
            "line_name",
            "scenario_family",
            "variant_id",
            "scenario_value",
            "metric_max_loading_percent",
            "over_80_loading_flag",
            "over_100_loading_flag",
            "top_congested_flag",
            "binding_under_internal_dispatch_flag",
            "asset_type",
            "from_bus",
            "to_bus",
            "source_scenario_id",
        ]
    ].copy()

    s30 = read_csv(S30_TOP_LINES).copy()
    s30["line_name"] = s30["name"].astype(str)
    s30 = s30.merge(line_map, on="line_name", how="inner", suffixes=("_scenario", ""))
    s30["scenario_family"] = "S30_PARALLEL_EQUIVALENT_ATPL_00147_DIAGNOSTIC"
    s30["scenario_value"] = pd.NA
    s30["metric_max_loading_percent"] = pd.to_numeric(s30["loading_percent"], errors="coerce")
    s30["over_80_loading_flag"] = s30["metric_max_loading_percent"] >= 80
    s30["over_100_loading_flag"] = s30["metric_max_loading_percent"] >= 100
    s30["top_congested_flag"] = s30.groupby("variant_id")["metric_max_loading_percent"].transform("max") == s30["metric_max_loading_percent"]
    s30["binding_under_internal_dispatch_flag"] = s30["over_100_loading_flag"]
    s30_targets = s30[
        [
            "line_id",
            "line_name",
            "scenario_family",
            "variant_id",
            "scenario_value",
            "metric_max_loading_percent",
            "over_80_loading_flag",
            "over_100_loading_flag",
            "top_congested_flag",
            "binding_under_internal_dispatch_flag",
            "asset_type",
            "from_bus",
            "to_bus",
            "source_scenario_id",
        ]
    ].copy()

    summary_target_rows: list[dict[str, Any]] = []
    for path, family, variant_column, scenario_value_column in [
        (S20_ATTEMPTS, "S20_STRICT_INTERNAL_DCOPF", "variant_id", "load_scale"),
        (S22_ATTEMPTS, "S22_MIXED_CORRIDOR_TARGETED_REMEDIATION", None, "load_scale"),
    ]:
        df = read_csv(path)
        for _, row in df.iterrows():
            line_name = str(row.get("max_line_name", "")).strip()
            match = line_map[line_map["line_name"] == line_name]
            if match.empty:
                continue
            base = match.iloc[0]
            summary_target_rows.append(
                {
                    "line_id": str(base["line_id"]),
                    "line_name": line_name,
                    "scenario_family": family,
                    "variant_id": str(row[variant_column]) if variant_column and variant_column in row else f"load_scale_{row[scenario_value_column]}",
                    "scenario_value": float(row[scenario_value_column]),
                    "metric_max_loading_percent": float(row.get("max_line_loading_percent", 0.0) or 0.0),
                    "over_80_loading_flag": float(row.get("max_line_loading_percent", 0.0) or 0.0) >= 80,
                    "over_100_loading_flag": float(row.get("max_line_loading_percent", 0.0) or 0.0) >= 100,
                    "top_congested_flag": True,
                    "binding_under_internal_dispatch_flag": float(row.get("max_line_loading_percent", 0.0) or 0.0) >= 100,
                    "asset_type": base["asset_type"],
                    "from_bus": str(base["from_bus"]),
                    "to_bus": str(base["to_bus"]),
                    "source_scenario_id": base["source_scenario_id"],
                }
            )
    target_columns = [
        "line_id",
        "line_name",
        "scenario_family",
        "variant_id",
        "scenario_value",
        "metric_max_loading_percent",
        "over_80_loading_flag",
        "over_100_loading_flag",
        "top_congested_flag",
        "binding_under_internal_dispatch_flag",
        "asset_type",
        "from_bus",
        "to_bus",
        "source_scenario_id",
    ]
    summary_targets = pd.DataFrame(summary_target_rows).reindex(columns=target_columns)
    s21_targets = s21_targets.reindex(columns=target_columns)
    s30_targets = s30_targets.reindex(columns=target_columns)

    combined = pd.DataFrame(
        s21_targets.to_dict(orient="records") + s30_targets.to_dict(orient="records") + summary_targets.to_dict(orient="records"),
        columns=target_columns,
    )
    persistence = combined.groupby("line_name")["top_congested_flag"].sum().rename("bottleneck_persistence_count")
    combined = combined.merge(persistence, on="line_name", how="left")
    combined["bottleneck_persistence_count"] = pd.to_numeric(combined["bottleneck_persistence_count"], errors="coerce").fillna(0).astype(int)
    for column in ["over_80_loading_flag", "over_100_loading_flag", "top_congested_flag", "binding_under_internal_dispatch_flag"]:
        combined[column] = combined[column].apply(as_bool)
    return combined.sort_values(["scenario_family", "variant_id", "line_name"]).reset_index(drop=True)


def build_generator_targets(stage1: dict[str, Any]) -> pd.DataFrame:
    generators = stage1["generators"].copy()
    gen_map = generators[["generator_id", "bus_id", "dispatch_proxy_class", "cost_class", "source_scenario_id"]].copy() if "source_scenario_id" in generators.columns else generators[["generator_id", "bus_id", "dispatch_proxy_class", "cost_class"]].copy()
    if "source_scenario_id" not in gen_map.columns:
        gen_map["source_scenario_id"] = stage1["manifest"]["benchmark_freeze"]["dcopf"]

    s21 = read_csv(S21_GEN_RESULTS).copy()
    s21["generator_id"] = s21["name"].astype(str)
    s21 = s21.merge(gen_map, on="generator_id", how="inner")
    s21["scenario_family"] = "S21_NO_EXTGRID_GEN_ONLY_DCOPF"
    s21["variant_id"] = "load_scale_" + s21["load_scale"].astype(str)
    s21["scenario_value"] = s21["load_scale"].astype(float)
    s21["dispatch_mw"] = pd.to_numeric(s21["p_mw_res"], errors="coerce").fillna(0.0)
    total_dispatch = s21.groupby("load_scale")["dispatch_mw"].transform("sum")
    s21["dispatch_share_of_total_gen"] = s21["dispatch_mw"] / total_dispatch.where(total_dispatch.ne(0), pd.NA)
    s21["dispatch_positive_flag"] = s21["dispatch_mw"] > 0
    s21["top_dispatch_flag"] = s21.groupby("load_scale")["dispatch_mw"].transform("max") == s21["dispatch_mw"]
    s21["import_dependence_flag"] = s21["dispatch_proxy_class"].eq("import_interface_proxy")
    s21_targets = s21[
        [
            "generator_id",
            "bus_id",
            "scenario_family",
            "variant_id",
            "scenario_value",
            "dispatch_mw",
            "dispatch_share_of_total_gen",
            "dispatch_positive_flag",
            "top_dispatch_flag",
            "import_dependence_flag",
            "dispatch_proxy_class",
            "cost_class",
            "source_scenario_id",
        ]
    ].copy()

    s30_summary = read_csv(S30_SUMMARY).copy()
    dispatch_by_variant = s30_summary.set_index("variant_id")["total_gen_dispatch_mw"].to_dict()
    s30 = read_csv(S30_TOP_GENERATORS).copy()
    s30["generator_id"] = s30["name"].astype(str)
    s30 = s30.merge(gen_map, on="generator_id", how="inner")
    s30["scenario_family"] = "S30_PARALLEL_EQUIVALENT_ATPL_00147_DIAGNOSTIC"
    s30["scenario_value"] = pd.NA
    s30["dispatch_mw"] = pd.to_numeric(s30["p_mw_res"], errors="coerce").fillna(0.0)
    s30["dispatch_share_of_total_gen"] = s30.apply(lambda row: row["dispatch_mw"] / dispatch_by_variant.get(row["variant_id"], pd.NA), axis=1)
    s30["dispatch_positive_flag"] = s30["dispatch_mw"] > 0
    s30["top_dispatch_flag"] = s30.groupby("variant_id")["dispatch_mw"].transform("max") == s30["dispatch_mw"]
    s30["import_dependence_flag"] = s30["dispatch_proxy_class"].eq("import_interface_proxy")
    s30_targets = s30[
        [
            "generator_id",
            "bus_id",
            "scenario_family",
            "variant_id",
            "scenario_value",
            "dispatch_mw",
            "dispatch_share_of_total_gen",
            "dispatch_positive_flag",
            "top_dispatch_flag",
            "import_dependence_flag",
            "dispatch_proxy_class",
            "cost_class",
            "source_scenario_id",
        ]
    ].copy()

    target_columns = [
        "generator_id",
        "bus_id",
        "scenario_family",
        "variant_id",
        "scenario_value",
        "dispatch_mw",
        "dispatch_share_of_total_gen",
        "dispatch_positive_flag",
        "top_dispatch_flag",
        "import_dependence_flag",
        "dispatch_proxy_class",
        "cost_class",
        "source_scenario_id",
    ]
    s21_targets = s21_targets.reindex(columns=target_columns)
    s30_targets = s30_targets.reindex(columns=target_columns)
    combined = pd.DataFrame(
        s21_targets.to_dict(orient="records") + s30_targets.to_dict(orient="records"),
        columns=target_columns,
    )
    for column in ["dispatch_positive_flag", "top_dispatch_flag", "import_dependence_flag"]:
        combined[column] = combined[column].apply(as_bool)
    return combined.sort_values(["scenario_family", "variant_id", "generator_id"]).reset_index(drop=True)


def build_line_features(stage1: dict[str, Any], line_targets: pd.DataFrame) -> pd.DataFrame:
    lines = stage1["lines"].copy()
    buses = stage1["buses"].copy()
    policy = read_line_policy_csv(LINE_POLICY)
    policy["line_id"] = policy["line_id"].astype(str)

    degree_rows: list[dict[str, Any]] = []
    for _, row in lines.iterrows():
        degree_rows.append({"bus_id": str(row["from_bus"])})
        degree_rows.append({"bus_id": str(row["to_bus"])})
    degrees = pd.DataFrame(degree_rows).groupby("bus_id").size().rename("degree_total").reset_index()

    bus_voltage = buses[["bus_id", "voltage_kv"]].copy().rename(columns={"voltage_kv": "bus_voltage_kv"})
    line_summary = line_targets.groupby("line_name").agg(
        max_target_loading_percent=("metric_max_loading_percent", "max"),
        top_congested_appearance_count=("top_congested_flag", "sum"),
        bottleneck_persistence_count=("bottleneck_persistence_count", "max"),
    ).reset_index()

    out = lines.merge(policy, left_on="line_name", right_on="line_id", how="left", suffixes=("", "_policy"))
    out = out.merge(degrees.rename(columns={"degree_total": "from_bus_degree", "bus_id": "from_bus"}), on="from_bus", how="left")
    out = out.merge(degrees.rename(columns={"degree_total": "to_bus_degree", "bus_id": "to_bus"}), on="to_bus", how="left")
    out = out.merge(bus_voltage.rename(columns={"bus_id": "from_bus", "bus_voltage_kv": "from_bus_voltage_kv"}), on="from_bus", how="left")
    out = out.merge(bus_voltage.rename(columns={"bus_id": "to_bus", "bus_voltage_kv": "to_bus_voltage_kv"}), on="to_bus", how="left")
    out = out.merge(line_summary, on="line_name", how="left")

    out["r_over_x"] = pd.to_numeric(out["r_ohm_per_km"], errors="coerce") / pd.to_numeric(out["x_ohm_per_km"], errors="coerce").replace(0, pd.NA)
    out["is_mixed_corridor"] = out["asset_type"].astype(str).eq("mixed")
    out["is_policy_weighted_mixed"] = out["policy_class"].astype(str).eq("MIXED_WEIGHTED_ALLOWED")
    out["is_parallel_equivalent_required"] = out["policy_class"].astype(str).eq("MIXED_PARALLEL_EQUIVALENT_REQUIRED")
    out["endpoint_voltage_pair"] = out["from_bus_voltage_kv"].astype(str) + "-" + out["to_bus_voltage_kv"].astype(str)

    for column in ["from_bus_degree", "to_bus_degree", "max_target_loading_percent", "top_congested_appearance_count", "bottleneck_persistence_count"]:
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0)
    for column in ["is_mixed_corridor", "is_policy_weighted_mixed", "is_parallel_equivalent_required", "repeated_bottleneck", "publication_allowed"]:
        out[column] = out[column].apply(as_bool)
    return out


def build_generator_features(stage1: dict[str, Any], generator_targets: pd.DataFrame) -> pd.DataFrame:
    generators = stage1["generators"].copy()
    buses = stage1["buses"].copy()
    gen_summary = generator_targets.groupby("generator_id").agg(
        max_dispatch_mw=("dispatch_mw", "max"),
        max_dispatch_share_of_total_gen=("dispatch_share_of_total_gen", "max"),
        top_dispatch_appearance_count=("top_dispatch_flag", "sum"),
    ).reset_index()

    out = generators.merge(buses[["bus_id", "voltage_kv", "zone"]].rename(columns={"voltage_kv": "bus_voltage_kv", "zone": "bus_zone"}), on="bus_id", how="left")
    out = out.merge(gen_summary, on="generator_id", how="left")
    out["is_dispatchable_proxy"] = out["dispatch_proxy_class"].astype(str).eq("dispatchable_proxy")
    out["is_import_interface_proxy"] = out["dispatch_proxy_class"].astype(str).eq("import_interface_proxy")
    for column in ["max_dispatch_mw", "max_dispatch_share_of_total_gen", "top_dispatch_appearance_count"]:
        out[column] = pd.to_numeric(out[column], errors="coerce").fillna(0)
    for column in ["benchmark_usable", "publication_allowed", "is_dispatchable_proxy", "is_import_interface_proxy"]:
        out[column] = out[column].apply(as_bool)
    return out


def build_provenance_flags(stage1: dict[str, Any], line_features: pd.DataFrame, generator_features: pd.DataFrame) -> pd.DataFrame:
    trace = read_csv(ACPF_TRACE)
    assumptions = read_csv(ACPF_ASSUMPTIONS)

    line_assumptions = assumptions[assumptions["object_type"] == "line"]
    line_source_ids = ";".join(sorted(set(trace[trace["object_type"] == "line"]["source_ids"].dropna().astype(str))))
    line_assumption_ids = ";".join(sorted(set(line_assumptions["assumption_id"].dropna().astype(str))))
    line_rows = [
        {
            "entity_type": "line",
            "entity_id": str(row["line_id"]),
            "entity_name": str(row["line_name"]),
            "publication_allowed": as_bool(row.get("publication_allowed", False)),
            "diagnostic_only": True,
            "source_scenario_id": row.get("source_scenario_id", ""),
            "policy_class": row.get("policy_class", ""),
            "repeated_bottleneck": as_bool(row.get("repeated_bottleneck", False)),
            "trace_source_ids": line_source_ids,
            "assumption_ids": line_assumption_ids,
        }
        for _, row in line_features.iterrows()
    ]

    generator_rows = [
        {
            "entity_type": "generator",
            "entity_id": str(row["generator_id"]),
            "entity_name": str(row.get("installation_name", row["generator_id"])),
            "publication_allowed": as_bool(row.get("publication_allowed", False)),
            "diagnostic_only": True,
            "source_scenario_id": stage1["manifest"]["benchmark_freeze"]["dcopf"],
            "policy_class": row.get("dispatch_proxy_class", ""),
            "repeated_bottleneck": False,
            "trace_source_ids": "GENERATOR_PROXY_GOVERNED",
            "assumption_ids": "GENERATOR_PROXY_COST_ASSIGNMENT",
        }
        for _, row in generator_features.iterrows()
    ]

    bus_rows = [
        {
            "entity_type": "bus",
            "entity_id": str(row["bus_id"]),
            "entity_name": str(row["bus_name"]),
            "publication_allowed": False,
            "diagnostic_only": True,
            "source_scenario_id": row.get("source_scenario_id", ""),
            "policy_class": "",
            "repeated_bottleneck": False,
            "trace_source_ids": "S16_BACKBONE_PACKAGED",
            "assumption_ids": "",
        }
        for _, row in stage1["buses"].iterrows()
    ]

    return pd.DataFrame(bus_rows + line_rows + generator_rows)


def build_manifest(row_counts: dict[str, int], stage1_manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "dataset_id": DATASET_ID,
        "release_id": RELEASE_ID,
        "schema_version": SCHEMA_VERSION,
        "builder_script": "src/build_portuguese_stage2_dataset.py",
        "release_scope": "feature/target/provenance dataset layer for later SSL and risk-prediction preparation; no ML included",
        "upstream_stage1_release_id": stage1_manifest.get("release_id"),
        "benchmark_freeze": stage1_manifest.get("benchmark_freeze", {}),
        "source_artifacts": [str(path.relative_to(ROOT)) for path in REQUIRED_INPUTS],
        "table_row_counts": row_counts,
        "publication_allowed": False,
        "diagnostic_only": True,
        "operator_grade_ready": False,
        "ml_ready": False,
        "downstream_intended_use": [
            "graph entity feature engineering",
            "scenario-conditioned risk target construction",
            "later SSL and risk-prediction dataset preparation",
        ],
        "excluded_scope": [
            "model training",
            "train/validation/test splits",
            "feature normalization for models",
            "framework-specific graph exports",
            "AC OPF products",
        ],
        "caveats": [
            "Targets remain benchmark-diagnostic rather than operator-grade labels.",
            "Line governance joins rely on semantic corridor IDs via line_name.",
            "Generator semantics remain proxy-governed.",
        ],
    }


def write_release_report(manifest: dict[str, Any], row_counts: dict[str, int]) -> None:
    summary_rows = [{"table": key, "rows": value} for key, value in row_counts.items()]
    text = [
        "# 90 Stage-2 Dataset Release Note",
        "",
        f"Generated: {manifest['generated_at']}",
        "",
        "Scope: build a stage-2 Portuguese benchmark-derived feature/target/provenance dataset layer for later SSL and risk-prediction preparation, without performing machine learning.",
        "",
        "## Release identity",
        "",
        f"- dataset id: `{manifest['dataset_id']}`",
        f"- release id: `{manifest['release_id']}`",
        f"- upstream stage-1 release id: `{manifest['upstream_stage1_release_id']}`",
        "",
        "## Included tables",
        "",
        markdown_table(summary_rows, ["table", "rows"]),
        "",
        "## What stage-2 adds",
        "",
        "- static bus, line, and generator feature tables built from the validated stage-1 package",
        "- scenario-conditioned line and generator target tables from existing S20/S21/S22/S30 diagnostics",
        "- consolidated provenance/governance flags for diagnostic interpretation",
        "",
        "## Boundary",
        "",
        "- diagnostic-only",
        "- not operator-grade",
        "- no ML training or data splits",
        "- no AC OPF outputs",
    ]
    write_text(RELEASE_REPORT, "\n".join(text) + "\n")


def main() -> None:
    ensure_directories()
    require_inputs()
    OUT.mkdir(parents=True, exist_ok=True)

    stage1 = prepare_stage1_frames(load_stage1())
    bus_features = build_bus_features(stage1)
    line_targets = build_line_targets(stage1)
    generator_targets = build_generator_targets(stage1)
    line_features = build_line_features(stage1, line_targets)
    generator_features = build_generator_features(stage1, generator_targets)
    provenance_flags = build_provenance_flags(stage1, line_features, generator_features)

    bus_features.to_csv(OUT / "pt_stage2_bus_features.csv", index=False)
    line_features.to_csv(OUT / "pt_stage2_line_features.csv", index=False)
    generator_features.to_csv(OUT / "pt_stage2_generator_features.csv", index=False)
    line_targets.to_csv(OUT / "pt_stage2_line_risk_targets.csv", index=False)
    generator_targets.to_csv(OUT / "pt_stage2_generator_risk_targets.csv", index=False)
    provenance_flags.to_csv(OUT / "pt_stage2_provenance_flags.csv", index=False)

    row_counts = {
        "pt_stage2_bus_features.csv": int(len(bus_features)),
        "pt_stage2_line_features.csv": int(len(line_features)),
        "pt_stage2_generator_features.csv": int(len(generator_features)),
        "pt_stage2_line_risk_targets.csv": int(len(line_targets)),
        "pt_stage2_generator_risk_targets.csv": int(len(generator_targets)),
        "pt_stage2_provenance_flags.csv": int(len(provenance_flags)),
    }
    manifest = build_manifest(row_counts, stage1["manifest"])
    write_json(OUT / "pt_stage2_manifest.json", manifest)
    write_release_report(manifest, row_counts)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
