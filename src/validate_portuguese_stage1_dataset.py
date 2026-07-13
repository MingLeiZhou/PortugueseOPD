from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

import config
from utils import ensure_directories, markdown_table, utc_now, write_json, write_text

ROOT = config.ROOT_DIR
OUT = config.PROCESSED_DIR / "dataset_release_stage1"
REPORTS = config.REPORTS_DIR
VALIDATION_REPORT = REPORTS / "89_stage1_dataset_validation.md"

FROZEN_ACPF = "S16_BACKBONE_DIAGNOSTIC_CORE_DEPTH6"
FROZEN_DCOPF = "S30_PARALLEL_EQUIVALENT_ATPL_00147_DIAGNOSTIC"

REQUIRED_FILES = {
    "buses": OUT / "pt_dataset_buses.csv",
    "lines": OUT / "pt_dataset_lines.csv",
    "loads": OUT / "pt_dataset_loads.csv",
    "generators": OUT / "pt_dataset_generators.csv",
    "generator_assignment": OUT / "pt_dataset_generator_assignment.csv",
    "generator_dispatch_proxy": OUT / "pt_dataset_generator_dispatch_proxy.csv",
    "generator_costs": OUT / "pt_dataset_generator_costs.csv",
    "benchmark_summaries": OUT / "pt_dataset_benchmark_summaries.csv",
    "line_policy": OUT / "pt_dataset_line_policy.csv",
    "manifest": OUT / "pt_dataset_manifest.json",
}

REQUIRED_COLUMNS = {
    "buses": {"bus_id", "bus_name", "voltage_kv", "in_service", "source_scenario_id"},
    "lines": {"line_id", "line_name", "from_bus", "to_bus", "length_km", "asset_type", "source_scenario_id"},
    "loads": {"load_id", "load_name", "bus_id", "p_mw", "q_mvar", "source_scenario_id"},
    "generators": {"generator_id", "bus_id", "dispatch_proxy_class", "pmax_mw_proxy", "marginal_cost_eur_per_mwh", "benchmark_usable"},
    "benchmark_summaries": {"benchmark_family", "scenario_id", "variant_id", "publication_allowed", "diagnostic_only"},
    "line_policy": {"line_id", "policy_class", "publication_allowed"},
}


def read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def load_manifest() -> dict[str, Any]:
    with (OUT / "pt_dataset_manifest.json").open("r", encoding="utf-8") as handle:
        return json.load(handle)


def add_error(errors: list[str], message: str) -> None:
    errors.append(message)


def add_warning(warnings: list[str], message: str) -> None:
    warnings.append(message)


def check_required_files(errors: list[str]) -> None:
    for name, path in REQUIRED_FILES.items():
        if not path.exists():
            add_error(errors, f"Missing required packaged file: {name} -> {path}")


def check_columns(name: str, df: pd.DataFrame, errors: list[str]) -> None:
    missing = sorted(REQUIRED_COLUMNS.get(name, set()) - set(df.columns))
    if missing:
        add_error(errors, f"{name} missing required columns: {', '.join(missing)}")


def check_unique(df: pd.DataFrame, column: str, label: str, errors: list[str]) -> int:
    duplicates = int(df[column].astype(str).duplicated().sum()) if column in df.columns else 0
    if duplicates:
        add_error(errors, f"{label} has {duplicates} duplicate key rows in column {column}")
    return duplicates


def check_fk(child: pd.Series, parent: pd.Series, label: str, errors: list[str]) -> int:
    child_vals = set(child.astype(str))
    parent_vals = set(parent.astype(str))
    missing = sorted(value for value in child_vals if value not in parent_vals)
    if missing:
        add_error(errors, f"{label} has {len(missing)} missing references")
    return len(missing)


def write_report(summary: dict[str, Any]) -> None:
    summary_rows = [{
        "status": summary["status"],
        "error_count": len(summary["errors"]),
        "warning_count": len(summary["warnings"]),
    }]
    check_rows = [
        {"check": key, "value": value}
        for key, value in summary["check_counts"].items()
    ]
    warning_rows = [{"warning": warning} for warning in summary["warnings"]]
    error_rows = [{"error": error} for error in summary["errors"]]

    text = [
        "# 89 Stage-1 Dataset Validation",
        "",
        f"Generated: {summary['generated_at']}",
        "",
        "Scope: validate the packaged stage-1 benchmark-derived dataset outputs for structural integrity, schema presence, benchmark-freeze consistency, and governance boundary compliance.",
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
        write_json(OUT / "pt_dataset_validation_summary.json", summary)
        write_report(summary)
        raise RuntimeError("Dataset validation failed before reading packaged files.")

    buses = read_csv(REQUIRED_FILES["buses"])
    lines = read_csv(REQUIRED_FILES["lines"])
    loads = read_csv(REQUIRED_FILES["loads"])
    generators = read_csv(REQUIRED_FILES["generators"])
    benchmark_summaries = read_csv(REQUIRED_FILES["benchmark_summaries"])
    line_policy = read_csv(REQUIRED_FILES["line_policy"])
    manifest = load_manifest()

    for name, df in {
        "buses": buses,
        "lines": lines,
        "loads": loads,
        "generators": generators,
        "benchmark_summaries": benchmark_summaries,
        "line_policy": line_policy,
    }.items():
        check_columns(name, df, errors)

    check_counts["bus_duplicates"] = check_unique(buses, "bus_id", "buses", errors)
    check_counts["line_duplicates"] = check_unique(lines, "line_id", "lines", errors)
    check_counts["load_duplicates"] = check_unique(loads, "load_id", "loads", errors)
    check_counts["generator_duplicates"] = check_unique(generators, "generator_id", "generators", errors)

    buses_ids = buses["bus_id"].astype(str)
    check_counts["missing_line_from_bus_refs"] = check_fk(lines["from_bus"].astype(str), buses_ids, "lines.from_bus", errors)
    check_counts["missing_line_to_bus_refs"] = check_fk(lines["to_bus"].astype(str), buses_ids, "lines.to_bus", errors)
    check_counts["missing_load_bus_refs"] = check_fk(loads["bus_id"].astype(str), buses_ids, "loads.bus_id", errors)
    check_counts["missing_generator_bus_refs"] = check_fk(generators["bus_id"].astype(str), buses_ids, "generators.bus_id", errors)

    nonpositive_lengths = int((pd.to_numeric(lines["length_km"], errors="coerce") <= 0).sum())
    if nonpositive_lengths:
        add_error(errors, f"lines contains {nonpositive_lengths} non-positive length_km rows")
    check_counts["nonpositive_line_lengths"] = nonpositive_lengths

    negative_load_p = int((pd.to_numeric(loads["p_mw"], errors="coerce") < 0).sum())
    negative_gen_pmax = int((pd.to_numeric(generators["pmax_mw_proxy"], errors="coerce") < 0).sum())
    if negative_load_p:
        add_error(errors, f"loads contains {negative_load_p} negative p_mw rows")
    if negative_gen_pmax:
        add_error(errors, f"generators contains {negative_gen_pmax} negative pmax_mw_proxy rows")
    check_counts["negative_load_p_rows"] = negative_load_p
    check_counts["negative_generator_pmax_rows"] = negative_gen_pmax

    allowed_voltages = {10.0, 60.0}
    unexpected_voltages = sorted(v for v in pd.to_numeric(buses["voltage_kv"], errors="coerce").dropna().unique() if float(v) not in allowed_voltages)
    if unexpected_voltages:
        add_warning(warnings, f"Unexpected bus voltage levels present: {unexpected_voltages}")
    check_counts["unexpected_voltage_level_count"] = len(unexpected_voltages)

    acpf_rows = benchmark_summaries[benchmark_summaries["benchmark_family"] == "acpf"]
    if len(acpf_rows) != 1:
        add_error(errors, f"Expected exactly one ACPF summary row, found {len(acpf_rows)}")
    elif str(acpf_rows.iloc[0]["scenario_id"]) != FROZEN_ACPF:
        add_error(errors, f"ACPF summary scenario_id mismatch: expected {FROZEN_ACPF}")

    dcopf_rows = benchmark_summaries[benchmark_summaries["benchmark_family"] == "dcopf"]
    if dcopf_rows.empty:
        add_error(errors, "Missing DC OPF summary rows")
    elif any(str(v) != FROZEN_DCOPF for v in dcopf_rows["scenario_id"].astype(str).unique()):
        add_error(errors, f"DC OPF summary contains unexpected scenario IDs: {sorted(dcopf_rows['scenario_id'].astype(str).unique())}")

    required_policy_lines = {"ATPL_00075", "ATPL_00147"}
    policy_lines = set(line_policy["line_id"].astype(str))
    missing_policy = sorted(required_policy_lines - policy_lines)
    if missing_policy:
        add_error(errors, f"Missing benchmark-relevant policy rows: {', '.join(missing_policy)}")
    check_counts["missing_required_policy_rows"] = len(missing_policy)

    if manifest.get("benchmark_freeze", {}).get("acpf") != FROZEN_ACPF:
        add_error(errors, "Manifest ACPF freeze mismatch")
    if manifest.get("benchmark_freeze", {}).get("dcopf") != FROZEN_DCOPF:
        add_error(errors, "Manifest DC OPF freeze mismatch")
    if bool(manifest.get("publication_allowed", True)):
        add_error(errors, "Manifest publication_allowed should remain false")
    if not bool(manifest.get("diagnostic_only", False)):
        add_error(errors, "Manifest diagnostic_only should remain true")
    if bool(manifest.get("ml_ready", True)):
        add_error(errors, "Manifest ml_ready should remain false")

    summary = {
        "generated_at": utc_now(),
        "status": "PASS" if not errors else "FAIL",
        "errors": errors,
        "warnings": warnings,
        "check_counts": check_counts,
    }
    write_json(OUT / "pt_dataset_validation_summary.json", summary)
    write_report(summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))

    if errors:
        raise RuntimeError("Dataset validation failed.")


if __name__ == "__main__":
    main()
