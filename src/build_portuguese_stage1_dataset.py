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
OUT = PROCESSED / "dataset_release_stage1"

S16_DIR = PROCESSED / "acpf_s16_backbone_core_depth6"
S30_DIR = PROCESSED / "dcopf_s30_atpl_00147_parallel_equivalent"
GEN_CANDIDATES = PROCESSED / "generator_candidates" / "pt_generator_candidates.csv"
GEN_ASSIGNMENT = PROCESSED / "generator_assignment" / "pt_generator_bus_assignment.csv"
GEN_DISPATCH = PROCESSED / "generator_dispatch_proxies" / "pt_generator_dispatch_proxy_table.csv"
GEN_COSTS = PROCESSED / "generator_costs" / "pt_generator_cost_scenarios.csv"
LINE_POLICY = PROCESSED / "mixed_corridor_policy_table.csv"

S16_SUMMARY = S16_DIR / "s16_backbone_summary.json"
S16_BUSES = S16_DIR / "s16_backbone_buses.csv"
S16_LINES = S16_DIR / "s16_backbone_lines.csv"
S16_LOADS = S16_DIR / "s16_backbone_loads.csv"
S30_SUMMARY = S30_DIR / "s30_summary.csv"

RELEASE_REPORT = REPORTS / "88_stage1_dataset_release_note.md"

REQUIRED_INPUTS = [
    S16_SUMMARY,
    S16_BUSES,
    S16_LINES,
    S16_LOADS,
    S30_SUMMARY,
    LINE_POLICY,
    GEN_CANDIDATES,
    GEN_ASSIGNMENT,
    GEN_DISPATCH,
    GEN_COSTS,
]

DATASET_ID = "pt_grid_benchmark_stage1"
RELEASE_ID = "pt_grid_benchmark_stage1_v1"
SCHEMA_VERSION = "1.0"
FROZEN_ACPF = "S16_BACKBONE_DIAGNOSTIC_CORE_DEPTH6"
FROZEN_DCOPF = "S30_PARALLEL_EQUIVALENT_ATPL_00147_DIAGNOSTIC"


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def read_line_policy_csv(path: Path) -> pd.DataFrame:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    return pd.DataFrame(rows)


def require_inputs() -> None:
    missing = [str(path) for path in REQUIRED_INPUTS if not path.exists()]
    if missing:
        raise RuntimeError("Fail-closed: required input artifacts are missing:\n- " + "\n- ".join(missing))


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    text = str(value).strip().lower()
    return text in {"true", "1", "yes", "y"}


def build_buses() -> pd.DataFrame:
    buses = read_csv(S16_BUSES).copy()
    buses = buses.rename(
        columns={
            "bus_index": "bus_id",
            "name": "bus_name",
            "vn_kv": "voltage_kv",
        }
    )
    buses["source_scenario_id"] = FROZEN_ACPF
    buses["benchmark_role"] = "s16_backbone_bus"
    buses = buses[
        [
            "bus_id",
            "bus_name",
            "voltage_kv",
            "zone",
            "in_service",
            "min_vm_pu",
            "max_vm_pu",
            "source_scenario_id",
            "benchmark_role",
        ]
    ].copy()
    buses["bus_id"] = buses["bus_id"].astype(str)
    return buses


def build_lines() -> pd.DataFrame:
    lines = read_csv(S16_LINES).copy()
    lines = lines.rename(
        columns={
            "line_index": "line_id",
            "name": "line_name",
            "type": "asset_type",
        }
    )
    lines["source_scenario_id"] = FROZEN_ACPF
    lines["parameterization_basis"] = "benchmark_diagnostic"
    lines = lines[
        [
            "line_id",
            "line_name",
            "from_bus",
            "to_bus",
            "length_km",
            "r_ohm_per_km",
            "x_ohm_per_km",
            "c_nf_per_km",
            "max_i_ka",
            "asset_type",
            "in_service",
            "source_scenario_id",
            "parameterization_basis",
        ]
    ].copy()
    lines["line_id"] = lines["line_id"].astype(str)
    lines["from_bus"] = lines["from_bus"].astype(str)
    lines["to_bus"] = lines["to_bus"].astype(str)
    return lines


def build_loads() -> pd.DataFrame:
    loads = read_csv(S16_LOADS).copy()
    loads = loads.rename(
        columns={
            "load_index": "load_id",
            "name": "load_name",
            "bus": "bus_id",
            "type": "load_model_type",
        }
    )
    loads["source_scenario_id"] = FROZEN_ACPF
    loads = loads[
        [
            "load_id",
            "load_name",
            "bus_id",
            "p_mw",
            "q_mvar",
            "in_service",
            "load_model_type",
            "source_scenario_id",
        ]
    ].copy()
    loads["load_id"] = loads["load_id"].astype(str)
    loads["bus_id"] = loads["bus_id"].astype(str)
    return loads


def build_compact_generators() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    candidates = read_csv(GEN_CANDIDATES).copy()
    assignment = read_csv(GEN_ASSIGNMENT).copy()
    dispatch = read_csv(GEN_DISPATCH).copy()
    costs = read_csv(GEN_COSTS).copy()

    assignment["assigned_bus_index"] = pd.to_numeric(assignment["assigned_bus_index"], errors="coerce")
    dispatch["assigned_bus_index"] = pd.to_numeric(dispatch["assigned_bus_index"], errors="coerce")
    costs["assigned_bus_index"] = pd.to_numeric(costs["assigned_bus_index"], errors="coerce")

    compact = costs.copy()
    compact = compact.merge(
        candidates[[
            "candidate_id",
            "source_dataset",
            "installation_code",
            "installation_name",
            "district",
            "municipality",
            "generation_type",
            "capacity_mva_or_mw",
            "capacity_field",
            "bus_assignment_status",
            "cost_status",
            "publication_allowed",
            "notes",
        ]],
        on="candidate_id",
        how="left",
        suffixes=("", "_candidate"),
    )

    compact = compact.rename(
        columns={
            "candidate_id": "generator_id",
            "assigned_bus_index": "bus_id",
        }
    )
    compact["bus_id"] = compact["bus_id"].apply(lambda v: "" if pd.isna(v) else str(int(v)))
    compact["benchmark_usable"] = compact["dispatch_proxy_class"].isin(["dispatchable_proxy", "import_interface_proxy"])
    compact["publication_allowed"] = compact["publication_allowed"].apply(as_bool)
    compact = compact[
        compact["benchmark_usable"]
        & compact["bus_id"].ne("")
        & compact["pmax_mw_proxy"].notna()
        & compact["marginal_cost_eur_per_mwh"].notna()
    ].copy()
    compact = compact[
        [
            "generator_id",
            "bus_id",
            "assigned_bus_name",
            "installation_code",
            "installation_name",
            "generation_type",
            "dispatch_proxy_class",
            "cost_class",
            "pmax_mw_proxy",
            "pmin_mw_proxy",
            "marginal_cost_eur_per_mwh",
            "must_run",
            "curtailable",
            "benchmark_usable",
            "publication_allowed",
            "notes",
        ]
    ].copy()
    compact = compact.drop_duplicates(subset=["generator_id"], keep="first")

    return compact, assignment, dispatch, costs


def build_benchmark_summaries() -> pd.DataFrame:
    with S16_SUMMARY.open("r", encoding="utf-8") as handle:
        s16 = json.load(handle)

    s16_row = {
        "benchmark_family": "acpf",
        "scenario_id": s16.get("scenario_id", FROZEN_ACPF),
        "variant_id": "reference",
        "converged": True,
        "status": s16.get("status", ""),
        "effective_load_p_mw": s16.get("reference_effective_p_mw"),
        "objective_value": None,
        "top_dispatch_generator": None,
        "top_dispatch_mw": None,
        "max_line_loading_percent": s16.get("reference_max_line_loading_percent"),
        "max_line_name": s16.get("reference_worst_line_name"),
        "publication_allowed": as_bool(s16.get("publication_allowed", False)),
        "diagnostic_only": True,
        "source_file": str(S16_SUMMARY.relative_to(ROOT)),
    }

    s30 = read_csv(S30_SUMMARY).copy()
    s30["benchmark_family"] = "dcopf"
    s30["diagnostic_only"] = True
    s30["source_file"] = str(S30_SUMMARY.relative_to(ROOT))
    s30 = s30.rename(columns={"effective_load_p_mw": "effective_load_p_mw"})
    s30 = s30[
        [
            "benchmark_family",
            "scenario_id",
            "variant_id",
            "converged",
            "effective_load_p_mw",
            "error_type",
            "error",
            "objective_value",
            "total_gen_dispatch_mw",
            "top_dispatch_generator",
            "top_dispatch_mw",
            "max_line_loading_percent",
            "max_line_name",
            "publication_allowed",
            "diagnostic_only",
            "source_file",
        ]
    ].copy()
    s30["status"] = s30["converged"].apply(lambda ok: "DIAGNOSTIC_DONE" if as_bool(ok) else "ERROR")

    output_columns = [
        "benchmark_family",
        "scenario_id",
        "variant_id",
        "converged",
        "status",
        "effective_load_p_mw",
        "error_type",
        "error",
        "objective_value",
        "total_gen_dispatch_mw",
        "top_dispatch_generator",
        "top_dispatch_mw",
        "max_line_loading_percent",
        "max_line_name",
        "publication_allowed",
        "diagnostic_only",
        "source_file",
    ]
    s30 = s30.reindex(columns=[column for column in output_columns if column != "status"])
    s30["status"] = s30["converged"].apply(lambda ok: "DIAGNOSTIC_DONE" if as_bool(ok) else "ERROR")
    s30 = s30.reindex(columns=output_columns)

    combined_rows = [
        {column: s16_row.get(column) for column in output_columns}
    ]
    combined_rows.extend(s30.to_dict(orient="records"))
    combined = pd.DataFrame(combined_rows, columns=output_columns)
    combined["publication_allowed"] = combined["publication_allowed"].apply(as_bool)
    return combined[
        [
            "benchmark_family",
            "scenario_id",
            "variant_id",
            "converged",
            "status",
            "effective_load_p_mw",
            "error_type",
            "error",
            "objective_value",
            "total_gen_dispatch_mw",
            "top_dispatch_generator",
            "top_dispatch_mw",
            "max_line_loading_percent",
            "max_line_name",
            "publication_allowed",
            "diagnostic_only",
            "source_file",
        ]
    ]


def build_manifest(row_counts: dict[str, int]) -> dict[str, Any]:
    return {
        "generated_at": utc_now(),
        "dataset_id": DATASET_ID,
        "release_id": RELEASE_ID,
        "schema_version": SCHEMA_VERSION,
        "builder_script": "src/build_portuguese_stage1_dataset.py",
        "release_scope": "benchmark-derived packaged dataset for stage-1 SSL/risk preparation; no ML included",
        "benchmark_freeze": {
            "acpf": FROZEN_ACPF,
            "dcopf": FROZEN_DCOPF,
        },
        "source_artifacts": [str(path.relative_to(ROOT)) for path in REQUIRED_INPUTS],
        "table_row_counts": row_counts,
        "publication_allowed": False,
        "diagnostic_only": True,
        "operator_grade_ready": False,
        "ml_ready": False,
        "downstream_intended_use": [
            "graph/data engineering substrate",
            "benchmark-derived packaged dataset",
            "foundation for later SSL/risk dataset enrichment",
        ],
        "excluded_scope": [
            "AC OPF outputs",
            "ML labels",
            "feature normalization for models",
            "train/validation/test splits",
            "broad exploratory scenarios outside frozen package",
        ],
        "caveats": [
            "Generator semantics remain proxy-governed.",
            "Import/interface behavior remains diagnostic and policy-governed.",
            "Mixed-corridor governance remains part of package interpretation.",
            "Dataset is diagnostic-only and not operator-grade.",
        ],
    }


def write_release_report(manifest: dict[str, Any], row_counts: dict[str, int]) -> None:
    summary_rows = [
        {"table": key, "rows": value}
        for key, value in row_counts.items()
    ]
    text = [
        "# 88 Stage-1 Dataset Release Note",
        "",
        f"Generated: {manifest['generated_at']}",
        "",
        "Scope: package the frozen Portuguese benchmark-candidate v1 machine-readable artifacts into one stage-1 dataset bundle for downstream SSL/risk data engineering, without adding machine-learning logic.",
        "",
        "## Release identity",
        "",
        f"- dataset id: `{manifest['dataset_id']}`",
        f"- release id: `{manifest['release_id']}`",
        f"- schema version: `{manifest['schema_version']}`",
        f"- ACPF freeze: `{manifest['benchmark_freeze']['acpf']}`",
        f"- DC OPF freeze: `{manifest['benchmark_freeze']['dcopf']}`",
        "",
        "## Included packaged tables",
        "",
        markdown_table(summary_rows, ["table", "rows"]),
        "",
        "## Why this package exists",
        "",
        "This release turns the frozen benchmark-candidate v1 artifacts into one reproducible dataset product. It is intended to support later graph/data-engineering and SSL/risk-preparation work, while keeping the current project’s diagnostic-only benchmark boundary explicit.",
        "",
        "## Included governance context",
        "",
        "- S16 backbone buses/lines/loads are the packaged PF structural core.",
        "- S30 benchmark summaries provide the packaged DC OPF outcome layer.",
        "- mixed-corridor policy is carried as an explicit line-policy table.",
        "- generator candidate, assignment, proxy, and cost layers are retained as both compact benchmark-usable rows and broader provenance tables.",
        "",
        "## Major limitations",
        "",
        "- diagnostic-only release",
        "- not operator-grade",
        "- no AC OPF outputs included",
        "- no ML labels or train/validation/test split logic included",
        "- generator and import semantics remain proxy-governed rather than source-backed operator semantics",
        "",
        "## Intended downstream use",
        "",
        "- stable dataset packaging for graph/data engineering",
        "- benchmark-derived substrate for later SSL/risk enrichment",
        "- reproducible machine-readable release companion to the benchmark package documents",
    ]
    write_text(RELEASE_REPORT, "\n".join(text) + "\n")


def main() -> None:
    ensure_directories()
    require_inputs()
    OUT.mkdir(parents=True, exist_ok=True)

    buses = build_buses()
    lines = build_lines()
    loads = build_loads()
    generators, assignment, dispatch, costs = build_compact_generators()
    benchmark_summaries = build_benchmark_summaries()
    line_policy = read_line_policy_csv(LINE_POLICY).copy()

    buses.to_csv(OUT / "pt_dataset_buses.csv", index=False)
    lines.to_csv(OUT / "pt_dataset_lines.csv", index=False)
    loads.to_csv(OUT / "pt_dataset_loads.csv", index=False)
    generators.to_csv(OUT / "pt_dataset_generators.csv", index=False)
    assignment.to_csv(OUT / "pt_dataset_generator_assignment.csv", index=False)
    dispatch.to_csv(OUT / "pt_dataset_generator_dispatch_proxy.csv", index=False)
    costs.to_csv(OUT / "pt_dataset_generator_costs.csv", index=False)
    benchmark_summaries.to_csv(OUT / "pt_dataset_benchmark_summaries.csv", index=False)
    line_policy.to_csv(OUT / "pt_dataset_line_policy.csv", index=False)

    row_counts = {
        "pt_dataset_buses.csv": int(len(buses)),
        "pt_dataset_lines.csv": int(len(lines)),
        "pt_dataset_loads.csv": int(len(loads)),
        "pt_dataset_generators.csv": int(len(generators)),
        "pt_dataset_generator_assignment.csv": int(len(assignment)),
        "pt_dataset_generator_dispatch_proxy.csv": int(len(dispatch)),
        "pt_dataset_generator_costs.csv": int(len(costs)),
        "pt_dataset_benchmark_summaries.csv": int(len(benchmark_summaries)),
        "pt_dataset_line_policy.csv": int(len(line_policy)),
    }
    manifest = build_manifest(row_counts)
    write_json(OUT / "pt_dataset_manifest.json", manifest)
    write_release_report(manifest, row_counts)
    print(json.dumps(manifest, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
