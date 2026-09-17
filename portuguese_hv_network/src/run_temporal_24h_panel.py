#!/usr/bin/env python3
"""Run 24 consecutive hourly PT60 samples for each seasonal study day."""
from __future__ import annotations

import argparse
from copy import deepcopy

import pandas as pd

from common import utc_now, write_json
from run_temporal_validation import CASES, GENERATOR_INPUT, OUTPUT, run_case


def hourly_cases() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for base in CASES:
        snapshot = pd.Timestamp(base["timestamp_utc"])
        profile = pd.Timestamp(base["load_profile_timestamp_utc"])
        for hour in range(24):
            case = deepcopy(base)
            timestamp = snapshot.normalize() + pd.Timedelta(hours=hour, minutes=snapshot.minute)
            profile_timestamp = profile.normalize() + pd.Timedelta(hours=hour, minutes=profile.minute)
            case["case_id"] = f"{base['case_id']}_H{hour:02d}"
            case["timestamp_utc"] = timestamp.isoformat()
            case["load_profile_timestamp_utc"] = profile_timestamp.isoformat()
            case["analysis_role"] = "CONTINUOUS_24H_PANEL"
            rows.append(case)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    generators = pd.read_csv(GENERATOR_INPUT, low_memory=False)
    metric_rows: list[dict[str, object]] = []
    for position, case in enumerate(hourly_cases(), start=1):
        result, _, hotspots, *_ = run_case(case, generators, args.refresh, save_solved=False)
        top = hotspots[0] if hotspots else {}
        metric_rows.append({
            "base_case_id": str(case["case_id"]).rsplit("_H", 1)[0],
            "case_id": case["case_id"],
            "timestamp_utc": case["timestamp_utc"],
            "load_profile_timestamp_utc": case["load_profile_timestamp_utc"],
            "load_profile_mode": case["load_profile_mode"],
            "converged": result["converged"],
            "observed_load_mw": result["observed_load_mw"],
            "observed_generation_total_mw": result["observed_generation_total_mw"],
            "observed_net_import_mw": result["observed_net_import_mw"],
            "model_net_import_mw": result["model_net_import_mw"],
            "net_import_absolute_error_mw": result["net_import_absolute_error_mw"],
            "model_rnt_scope_losses_percent_of_load": result["model_rnt_scope_losses_percent_of_load"],
            "ren_rnt_monthly_loss_percent": result["ren_rnt_monthly_loss_percent"],
            "vm_pu_min": result["vm_pu_min"],
            "vm_pu_max": result["vm_pu_max"],
            "maximum_line_loading_percent": result["maximum_line_loading_percent"],
            "lines_over_100_percent": result["lines_over_100_percent"],
            "maximum_transformer_loading_percent": result["maximum_transformer_loading_percent"],
            "unmapped_generation_residual_mw": result["unmapped_generation_residual_mw"],
            "boundary_gross_to_abs_net_ratio": result["model_boundary_gross_to_abs_net_ratio"],
            "maximum_loading_line_id": top.get("line_id"),
            "maximum_loading_source_line_id": top.get("source_line_id"),
            "maximum_loading_voltage_kv": top.get("voltage_kv"),
            "maximum_loading_parameter_status": top.get("parameter_status"),
            "maximum_loading_from_facility": top.get("from_facility_name"),
            "maximum_loading_to_facility": top.get("to_facility_name"),
        })
        print(f"[{position:02d}/72] {case['case_id']} converged={result['converged']} max_line={result['maximum_line_loading_percent']:.2f}%")
    frame = pd.DataFrame(metric_rows)
    frame.to_csv(OUTPUT / "continuous_24h_validation.csv", index=False)
    summaries: list[dict[str, object]] = []
    for base_case_id, group in frame.groupby("base_case_id", sort=False):
        summaries.append({
            "base_case_id": base_case_id,
            "samples": int(len(group)),
            "converged_samples": int(group["converged"].sum()),
            "hours_with_line_overload": int(group["lines_over_100_percent"].gt(0).sum()),
            "maximum_line_loading_percent": float(group["maximum_line_loading_percent"].max()),
            "minimum_bus_voltage_pu": float(group["vm_pu_min"].min()),
            "maximum_bus_voltage_pu": float(group["vm_pu_max"].max()),
            "mean_absolute_net_import_error_mw": float(group["net_import_absolute_error_mw"].mean()),
            "mean_rnt_scope_loss_percent": float(group["model_rnt_scope_losses_percent_of_load"].mean()),
            "ren_monthly_rnt_loss_percent": float(group["ren_rnt_monthly_loss_percent"].iloc[0]),
            "load_profile_status": str(group["load_profile_mode"].iloc[0]),
        })
    write_json(OUTPUT / "continuous_24h_summary.json", {
        "generated_at": utc_now(),
        "sampling": "24 consecutive hourly samples on each study day; not all 96 quarter-hours",
        "case_count": int(len(frame)),
        "summaries": summaries,
    })
    print(pd.DataFrame(summaries).to_string(index=False))


if __name__ == "__main__":
    main()
