#!/usr/bin/env python3
"""Normalize cached/downloaded REN and E-REDES records for pt60_public_model."""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from common import write_json
from time_alignment import utc_interval_start, source_time_metadata
from run_temporal_validation import (
    GENERATION_GROUPS,
    aggregate_facility_loads,
    REN_RNT_BALANCE_CSV,
    REN_URL,
    WEATHER_URL,
    eredes_load_snapshot,
    ren_observation,
    ren_rnt_loss_benchmark,
    weather_observation,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timestamp", required=True, help="Operating timestamp with UTC offset")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--load-profile-timestamp", help="Different public load timestamp; marks output as a seasonal proxy")
    parser.add_argument("--rnt-loss-date", help="REN monthly balance date; defaults to the operating month end")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    timestamp = utc_interval_start(args.timestamp)
    if timestamp.tzinfo is None:
        raise ValueError("--timestamp must contain an explicit UTC offset")
    profile_timestamp = utc_interval_start(args.load_profile_timestamp or args.timestamp)
    loss_date = args.rnt_loss_date or str((timestamp + pd.offsets.MonthEnd(0)).date())
    observation = ren_observation(timestamp, args.refresh)
    load, _ = eredes_load_snapshot(profile_timestamp, args.refresh)
    loss = ren_rnt_loss_benchmark(loss_date, args.refresh)
    weather = weather_observation(timestamp, args.refresh)
    grouped = aggregate_facility_loads(load)
    grouped["p_mw"] = grouped["energy_kwh"] / 250.0
    if profile_timestamp != timestamp:
        reference = ren_observation(profile_timestamp, args.refresh)
        grouped["p_mw"] *= float(observation["load_mw"]) / float(reference["load_mw"])
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    grouped.rename(columns={"codigo_subestacao": "facility_code"})[
        ["facility_code", "substation_name", "p_mw", "observation_status", "valid_observation_count", "missing_observation_count"]
    ].to_csv(output / "loads.csv", index=False)
    spec = {
        "schema_version": "1.0",
        "time_alignment": source_time_metadata(timestamp),
        "case_id": f"PT60_PUBLIC_{timestamp.strftime('%Y%m%d_%H%M')}",
        "timestamp_utc": timestamp.isoformat(),
        "load_profile_timestamp_utc": profile_timestamp.isoformat(),
        "load_profile_mode": "SUPPLIED_PUBLIC_DATA" if profile_timestamp == timestamp else "SUPPLIED_PUBLIC_DATA_SEASONAL_PROXY",
        "new_interconnector_in_service": timestamp >= pd.Timestamp("2026-07-02T00:00:00+00:00"),
        "rnt_loss_benchmark_date": loss_date,
        "national": {
            "consumption_mw": observation["load_mw"],
            "consumption_plus_storage_mw": observation["load_plus_storage_mw"],
            "pumping_mw": observation["pumping_mw"],
            "battery_consumption_mw": observation["battery_consumption_mw"],
            "import_mw": observation["import_mw"],
            "export_mw": observation["export_mw"],
            "rnt_monthly_loss_percent": loss["loss_percent"],
            "generation_by_source_mw": {name: observation["generation_by_source_mw"].get(name, 0.0) for name in GENERATION_GROUPS},
        },
        "weather": weather,
        "sources": {
            "national_balance": observation["source_url"],
            "substation_load": "https://e-redes.opendatasoft.com/explore/dataset/diagrama-de-carga-de-subestacao/",
            "rnt_monthly_loss": loss["source_url"],
            "weather_context": WEATHER_URL,
        },
    }
    write_json(output / "scenario.json", spec)
    print(f"Wrote normalized public case to {output}")


if __name__ == "__main__":
    main()
