#!/usr/bin/env python3
"""Run pre-registered summer and winter PT60 hourly validation weeks.

The weeks are chosen from E-REDES coverage before model errors are inspected.
Each timestamp is a separate steady-state AC solve; the sequence is not a
dynamic simulation and does not model inter-temporal storage state of charge.
"""
from __future__ import annotations

import argparse
import math
import traceback
from copy import deepcopy
from typing import Any

import pandas as pd

from common import utc_now, write_json
from run_temporal_validation import (
    GENERATOR_INPUT,
    OUTPUT,
    eredes_load_snapshot,
    run_case,
)


WEEKS: tuple[dict[str, Any], ...] = (
    {
        "season": "SUMMER",
        "week_id": "PT60_2025_SUMMER_WEEK_JUL07_13",
        "start_timestamp_utc": "2025-07-07T00:15:00+00:00",
        "rating_mode": "STATIC_SUMMER_RATING",
        "rating_factor_relative_to_static_summer": 1.0,
        "pdirt_reference_season": "SUMMER",
        "rnt_loss_benchmark_date": "2025-07-31",
    },
    {
        "season": "WINTER",
        "week_id": "PT60_2026_WINTER_WEEK_JAN19_25",
        "start_timestamp_utc": "2026-01-19T00:15:00+00:00",
        "rating_mode": "WINTER_SEASONAL_PROXY_1_15",
        "rating_factor_relative_to_static_summer": 1.15,
        "pdirt_reference_season": "WINTER",
        "rnt_loss_benchmark_date": "2026-01-31",
    },
)

RESULT_COLUMNS = [
    "season", "week_id", "case_id", "timestamp_utc", "status", "converged",
    "eredes_record_count", "eredes_unique_substation_count",
    "observed_load_mw", "observed_generation_total_mw",
    "observed_net_import_mw", "model_net_import_mw", "net_import_error_mw",
    "net_import_absolute_error_mw", "net_import_error_percent_of_load",
    "vm_pu_min", "vm_pu_max", "maximum_line_loading_percent",
    "lines_over_100_percent", "maximum_transformer_loading_percent",
    "model_rnt_scope_losses_percent_of_load", "ren_rnt_monthly_loss_percent",
    "unmapped_generation_residual_mw", "eredes_profile_fraction_of_national_load",
    "pdirt_reference_season", "pdirt_reference_regime",
    "maximum_loading_line_id", "maximum_loading_source_line_id",
    "maximum_loading_voltage_kv", "maximum_loading_parameter_status",
    "maximum_loading_from_facility", "maximum_loading_to_facility", "error",
]


def hourly_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for week in WEEKS:
        start = pd.Timestamp(week["start_timestamp_utc"])
        for offset in range(7 * 24):
            timestamp = start + pd.Timedelta(hours=offset)
            cases.append({
                "season": week["season"],
                "week_id": week["week_id"],
                "case_id": f"{week['week_id']}_H{offset:03d}",
                "timestamp_utc": timestamp.isoformat(),
                "load_profile_timestamp_utc": timestamp.isoformat(),
                "load_profile_mode": "SYNCHRONIZED_EREDES",
                "rating_mode": week["rating_mode"],
                "rating_factor_relative_to_static_summer": week["rating_factor_relative_to_static_summer"],
                "pdirt_reference_season": week["pdirt_reference_season"],
                "new_interconnector_in_service": False,
                "external_boundary_buses": 7,
                "physical_cross_border_circuits": 9,
                "rnt_loss_benchmark_date": week["rnt_loss_benchmark_date"],
                "analysis_role": "SEASONAL_CONTINUOUS_WEEK_VALIDATION",
            })
    return cases


def _load_existing(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    frame = pd.read_csv(path, low_memory=False)
    return {str(row["case_id"]): row for row in frame.to_dict("records")}


def _save_rows(path: Path, rows: dict[str, dict[str, Any]]) -> None:
    frame = pd.DataFrame(rows.values())
    for column in RESULT_COLUMNS:
        if column not in frame:
            frame[column] = None
    frame[RESULT_COLUMNS].sort_values(["season", "timestamp_utc"]).to_csv(path, index=False)


def _summary_for_week(group: pd.DataFrame) -> dict[str, Any]:
    valid = group[group["status"].eq("COMPLETE")].copy()
    errors = pd.to_numeric(valid["net_import_error_mw"], errors="coerce").dropna()
    absolute_errors = errors.abs()
    absolute_error_percent = absolute_errors / pd.to_numeric(valid.loc[errors.index, "observed_load_mw"], errors="coerce") * 100.0
    daily_mae = valid.assign(
        date=pd.to_datetime(valid["timestamp_utc"], utc=True).dt.date,
        absolute_error=pd.to_numeric(valid["net_import_absolute_error_mw"], errors="coerce"),
    ).groupby("date")["absolute_error"].mean()
    return {
        "season": str(group["season"].iloc[0]),
        "week_id": str(group["week_id"].iloc[0]),
        "start_timestamp_utc": str(group["timestamp_utc"].min()),
        "end_timestamp_utc": str(group["timestamp_utc"].max()),
        "expected_hourly_samples": 168,
        "completed_samples": int(len(valid)),
        "failed_samples": int(len(group) - len(valid)),
        "converged_samples": int(valid["converged"].fillna(False).astype(bool).sum()),
        "net_import_bias_mw": float(errors.mean()) if len(errors) else None,
        "net_import_mae_mw": float(absolute_errors.mean()) if len(errors) else None,
        "net_import_rmse_mw": float(math.sqrt((errors ** 2).mean())) if len(errors) else None,
        "net_import_p95_absolute_error_mw": float(absolute_errors.quantile(0.95)) if len(errors) else None,
        "net_import_max_absolute_error_mw": float(absolute_errors.max()) if len(errors) else None,
        "mean_absolute_net_import_error_percent_of_load": float(absolute_error_percent.mean()) if len(errors) else None,
        "p95_absolute_net_import_error_percent_of_load": float(absolute_error_percent.quantile(0.95)) if len(errors) else None,
        "daily_net_import_mae_min_mw": float(daily_mae.min()) if len(daily_mae) else None,
        "daily_net_import_mae_max_mw": float(daily_mae.max()) if len(daily_mae) else None,
        "minimum_bus_voltage_pu": float(pd.to_numeric(valid["vm_pu_min"], errors="coerce").min()) if len(valid) else None,
        "maximum_bus_voltage_pu": float(pd.to_numeric(valid["vm_pu_max"], errors="coerce").max()) if len(valid) else None,
        "maximum_line_loading_percent": float(pd.to_numeric(valid["maximum_line_loading_percent"], errors="coerce").max()) if len(valid) else None,
        "hours_with_any_line_over_100_percent": int(pd.to_numeric(valid["lines_over_100_percent"], errors="coerce").gt(0).sum()),
        "maximum_transformer_loading_percent": float(pd.to_numeric(valid["maximum_transformer_loading_percent"], errors="coerce").max()) if len(valid) else None,
        "mean_rnt_scope_loss_percent": float(pd.to_numeric(valid["model_rnt_scope_losses_percent_of_load"], errors="coerce").mean()) if len(valid) else None,
        "ren_monthly_rnt_loss_percent": float(pd.to_numeric(valid["ren_rnt_monthly_loss_percent"], errors="coerce").iloc[0]) if len(valid) else None,
        "mean_eredes_fraction_of_national_load": float(pd.to_numeric(valid["eredes_profile_fraction_of_national_load"], errors="coerce").mean()) if len(valid) else None,
        "maximum_unmapped_generation_residual_mw": float(pd.to_numeric(valid["unmapped_generation_residual_mw"], errors="coerce").max()) if len(valid) else None,
        "minimum_eredes_record_count": int(pd.to_numeric(valid["eredes_record_count"], errors="coerce").min()) if len(valid) else None,
        "maximum_eredes_record_count": int(pd.to_numeric(valid["eredes_record_count"], errors="coerce").max()) if len(valid) else None,
        "interpretation_scope": "Repeated hourly steady-state AC solves; REN load and source totals are inputs; held-out aggregate net import mainly diagnoses balance closure and modeled losses.",
    }


def _daily_summary(frame: pd.DataFrame) -> pd.DataFrame:
    valid = frame[frame["status"].eq("COMPLETE")].copy()
    valid["date_utc"] = pd.to_datetime(valid["timestamp_utc"], utc=True).dt.date.astype(str)
    valid["squared_net_import_error_mw2"] = pd.to_numeric(valid["net_import_error_mw"], errors="coerce") ** 2
    rows: list[dict[str, Any]] = []
    for (season, week_id, date), group in valid.groupby(["season", "week_id", "date_utc"], sort=False):
        errors = pd.to_numeric(group["net_import_error_mw"], errors="coerce")
        rows.append({
            "season": season, "week_id": week_id, "date_utc": date,
            "samples": int(len(group)), "converged_samples": int(group["converged"].astype(bool).sum()),
            "mean_observed_load_mw": float(pd.to_numeric(group["observed_load_mw"], errors="coerce").mean()),
            "net_import_bias_mw": float(errors.mean()),
            "net_import_mae_mw": float(errors.abs().mean()),
            "net_import_rmse_mw": float(math.sqrt(group["squared_net_import_error_mw2"].mean())),
            "maximum_line_loading_percent": float(pd.to_numeric(group["maximum_line_loading_percent"], errors="coerce").max()),
            "hours_with_any_line_over_100_percent": int(pd.to_numeric(group["lines_over_100_percent"], errors="coerce").gt(0).sum()),
            "minimum_bus_voltage_pu": float(pd.to_numeric(group["vm_pu_min"], errors="coerce").min()),
            "minimum_eredes_record_count": int(pd.to_numeric(group["eredes_record_count"], errors="coerce").min()),
            "maximum_eredes_record_count": int(pd.to_numeric(group["eredes_record_count"], errors="coerce").max()),
        })
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--restart-failures", action="store_true", help="Retry rows previously saved with status FAILED")
    parser.add_argument("--limit", type=int, help="Run only the first N cases (cheap pilot/resume check)")
    args = parser.parse_args()

    output = OUTPUT / "seasonal_weeks"
    output.mkdir(parents=True, exist_ok=True)
    result_path = output / "seasonal_week_validation.csv"
    rows = _load_existing(result_path)
    generators = pd.read_csv(GENERATOR_INPUT, low_memory=False)
    all_cases = hourly_cases()
    cases = all_cases[: args.limit] if args.limit is not None else all_cases

    for position, case in enumerate(cases, start=1):
        existing = rows.get(case["case_id"])
        if existing and (existing.get("status") == "COMPLETE" or not args.restart_failures):
            print(f"[{position:03d}/{len(cases)}] skip {case['case_id']} status={existing.get('status')}", flush=True)
            continue
        timestamp = pd.Timestamp(case["timestamp_utc"])
        try:
            snapshot, metadata = eredes_load_snapshot(timestamp, args.refresh)
            result, _, hotspots, *_ = run_case(
                deepcopy(case), generators, args.refresh, save_solved=False,
                load_snapshot_override=snapshot, output_dir=output,
            )
            top = hotspots[0] if hotspots else {}
            row = {
                "season": case["season"], "week_id": case["week_id"],
                "case_id": case["case_id"], "timestamp_utc": case["timestamp_utc"],
                "status": "COMPLETE", "converged": result["converged"],
                "eredes_record_count": metadata["record_count"],
                "eredes_unique_substation_count": result["load_profile_unique_substation_count"],
                "observed_load_mw": result["observed_load_mw"],
                "observed_generation_total_mw": result["observed_generation_total_mw"],
                "observed_net_import_mw": result["observed_net_import_mw"],
                "model_net_import_mw": result["model_net_import_mw"],
                "net_import_error_mw": result["net_import_error_mw"],
                "net_import_absolute_error_mw": result["net_import_absolute_error_mw"],
                "net_import_error_percent_of_load": result["net_import_error_percent_of_load"],
                "vm_pu_min": result["vm_pu_min"], "vm_pu_max": result["vm_pu_max"],
                "maximum_line_loading_percent": result["maximum_line_loading_percent"],
                "lines_over_100_percent": result["lines_over_100_percent"],
                "maximum_transformer_loading_percent": result["maximum_transformer_loading_percent"],
                "model_rnt_scope_losses_percent_of_load": result["model_rnt_scope_losses_percent_of_load"],
                "ren_rnt_monthly_loss_percent": result["ren_rnt_monthly_loss_percent"],
                "unmapped_generation_residual_mw": result["unmapped_generation_residual_mw"],
                "eredes_profile_fraction_of_national_load": result["eredes_profile_fraction_of_national_load"],
                "pdirt_reference_season": result["pdirt_reference_season"],
                "pdirt_reference_regime": result["pdirt_reference_regime"],
                "maximum_loading_line_id": top.get("line_id"),
                "maximum_loading_source_line_id": top.get("source_line_id"),
                "maximum_loading_voltage_kv": top.get("voltage_kv"),
                "maximum_loading_parameter_status": top.get("parameter_status"),
                "maximum_loading_from_facility": top.get("from_facility_name"),
                "maximum_loading_to_facility": top.get("to_facility_name"),
                "error": "",
            }
            print(
                f"[{position:03d}/{len(cases)}] {case['case_id']} "
                f"records={metadata['record_count']} converged={result['converged']} "
                f"error={result['net_import_error_mw']:.1f} MW "
                f"max_line={result['maximum_line_loading_percent']:.1f}%",
                flush=True,
            )
        except Exception as exc:
            row = {
                "season": case["season"], "week_id": case["week_id"],
                "case_id": case["case_id"], "timestamp_utc": case["timestamp_utc"],
                "status": "FAILED", "converged": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
            traceback.print_exc()
        rows[case["case_id"]] = row
        _save_rows(result_path, rows)

    frame = pd.read_csv(result_path, low_memory=False)
    summaries = [_summary_for_week(group) for _, group in frame.groupby("week_id", sort=False)]
    write_json(output / "seasonal_week_summary.json", {
        "generated_at": utc_now(),
        "selection_protocol": {
            "time_envelope": "2025-05 through 2026-02",
            "rule": "Natural Monday-Sunday weeks near the middle of summer and winter, selected from E-REDES coverage before inspecting model error.",
            "summer_week": "2025-07-07 through 2025-07-13",
            "winter_week": "2026-01-19 through 2026-01-25",
            "sampling": "One synchronized sample per hour at HH:15 UTC; 168 expected samples per week.",
            "topology_note": "Both weeks precede the 2026-07-02 northern PT-ES interconnector commissioning, so the 7-boundary-bus/9-circuit representation is used.",
        },
        "summaries": summaries,
    })
    failures = frame[~frame["status"].eq("COMPLETE")][["season", "week_id", "case_id", "timestamp_utc", "error"]]
    failures.to_csv(output / "seasonal_week_failures.csv", index=False)
    _daily_summary(frame).to_csv(output / "seasonal_week_daily_summary.csv", index=False)
    print(pd.DataFrame(summaries).to_string(index=False), flush=True)


if __name__ == "__main__":
    main()
