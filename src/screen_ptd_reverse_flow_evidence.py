#!/usr/bin/env python3
"""Cross-screen public PTD reverse-flow hours and a synchronized solar case.

The public histogram window is not timestamp-aligned with the modeled solar
case. Overlap is descriptive evidence, not classification accuracy.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    args = parser.parse_args()
    with duckdb.connect(str(args.database)) as con:
        peak_time, solar_mw = con.execute("""SELECT timestamp_utc,national_solar_mw
            FROM operating.ren_solar_15min ORDER BY national_solar_mw DESC LIMIT 1""").fetchone()
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("""CREATE OR REPLACE TABLE study.ptd_reverse_flow_peak_solar_screen AS
            SELECT h.ptd_code, ? AS scenario_timestamp_utc,
                   h.negative_utilization_hours AS public_negative_hours,
                   h.reported_hours AS public_histogram_hours,
                   l.p_mw AS assigned_ptd_net_load_proxy_mw,
                   coalesce(v.p_mw,0) AS allocated_pv_proxy_mw,
                   l.p_mw AS model_net_p_mw,
                   h.negative_utilization_hours>0 AS public_reverse_flow_in_histogram,
                   l.p_mw<0 AS model_reverse_at_solar_peak,
                   'NON_COINCIDENT_HISTOGRAM_AND_SCENARIO_WINDOWS' AS comparison_status
            FROM study.ptd_utilization_histogram_summary h
            JOIN operating.ptd_load_15min l USING(ptd_code)
            LEFT JOIN operating.ptd_pv_15min v USING(ptd_code,timestamp_utc)
            WHERE l.timestamp_utc=?""", [peak_time,peak_time])
        row = con.execute("""SELECT count(*),
            count(*) FILTER (WHERE public_reverse_flow_in_histogram),
            count(*) FILTER (WHERE model_reverse_at_solar_peak),
            count(*) FILTER (WHERE public_reverse_flow_in_histogram AND model_reverse_at_solar_peak),
            count(*) FILTER (WHERE model_net_p_mw IS NULL)
            FROM study.ptd_reverse_flow_peak_solar_screen""").fetchone()
        histogram_ptds = con.execute("SELECT count(*) FROM study.ptd_utilization_histogram_summary").fetchone()[0]
    checks = dict(zip(("screened_ptds", "public_ptds_with_reverse_hours",
                       "scenario_reverse_ptds_at_solar_peak", "ptds_in_both_sets",
                       "null_net_power_ptds"), row))
    errors = []
    if row[0] != histogram_ptds or row[4]:
        errors.append("Reverse-flow cross-screen coverage or power values failed")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "scenario_timestamp_utc": peak_time,
        "national_solar_mw_at_snapshot": solar_mw,
        "checks": checks,
        "scope": "Public PTD reverse-utilization history versus one modelled peak-solar net-power snapshot",
        "limitations": [
            "Public histogram covers an unspecified trailing 12-month window, not this snapshot",
            "Negative utilization can reflect local generation or other net-flow conditions; technology is not disclosed",
            "Current PTD solar allocation is an uncalibrated aggregate scenario",
            "Cross-sectional overlap is not a precision/recall validation against synchronous truth",
        ],
        "errors": errors,
    }
    path = args.database.parent / "ptd_reverse_flow_screen.validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
