#!/usr/bin/env python3
"""Freeze REN source-level dispatch at the E-REDES calibration timestamp."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import requests

from common import PROJECT, RAW, ensure_dirs, read_json, trace, utc_now, write_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    ensure_dirs()
    config = read_json(PROJECT / "config" / "model_config.json")
    sources = read_json(PROJECT / "config" / "sources.json")
    timestamp = str(config["calibration_timestamp_utc"])
    date = timestamp[:10]
    time_label = timestamp[11:16]
    raw_path = RAW / "ren" / f"dispatch_{date}.json"
    summary_path = RAW / "ren" / "dispatch_calibration.json"
    if args.overwrite or not raw_path.exists():
        response = requests.get(
            str(sources["ren_dispatch_daily_api"]),
            params={"culture": "en-US", "date": date},
            headers={"User-Agent": "Portuguese-HV-Candidate research data pipeline/0.2"},
            timeout=120,
        )
        response.raise_for_status()
        raw_path.parent.mkdir(parents=True, exist_ok=True)
        raw_path.write_bytes(response.content)
    payload = read_json(raw_path)
    categories = payload["xAxis"]["categories"]
    if time_label not in categories:
        raise ValueError(f"REN response has no {time_label} sample")
    index = categories.index(time_label)
    values = {str(series["name"]): float(series["data"][index]) for series in payload["series"]}
    generation_names = ["Hydro", "Solar", "Wind", "Natural Gas", "Other Thermal", "Biomass", "Coal", "Wave", "Battery Injection"]
    summary = {
        "generated_at": utc_now(),
        "calibration_timestamp_utc": timestamp,
        "source_url": f'{sources["ren_dispatch_daily_api"]}?culture=en-US&date={date}',
        "source_file": trace(raw_path, "ren-dispatch-daily", str(sources["ren_dispatch_daily_api"]), "REN 15-minute national electricity balance"),
        "consumption_mw": values.get("Consumption"),
        "consumption_plus_storage_mw": values.get("Consumption + Storage"),
        "import_mw": values.get("Import", 0.0),
        "export_mw": values.get("Export", 0.0),
        "generation_by_source_mw": {name: values.get(name, 0.0) for name in generation_names},
        "all_series_at_timestamp": values,
    }
    write_json(summary_path, summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
