#!/usr/bin/env python3
"""Normalize public PTD utilization histograms without inventing tail bins.

The current CSV has 23 values per PTD while the portal's prose example has
24. The central bin alignment is checked against the public peak-utilization
bands; the most negative tail is left without a numeric boundary.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
SOURCE_URL = "https://e-redes.opendatasoft.com/explore/dataset/postos-transformacao-distribuicao/"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    args = parser.parse_args()
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS observation")
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("""CREATE OR REPLACE TABLE observation.ptd_utilization_histogram_bin AS
            WITH expanded AS (
              SELECT p.ptd_code, p.installed_transformer_kva,
                     (ord-1)::INTEGER AS bin_index,
                     try_cast(value AS DOUBLE) AS hours,
                     p.source_sha256
              FROM candidate.ptd_public_attributes p,
                unnest(str_split(trim(both '()' from p.utilization_histogram_raw),';'))
                  WITH ORDINALITY AS x(value,ord)
              WHERE p.utilization_histogram_raw IS NOT NULL
            )
            SELECT ptd_code,bin_index,hours,installed_transformer_kva,
                   CASE WHEN bin_index BETWEEN 1 AND 21
                        THEN (bin_index-11)*10 ELSE NULL END::INTEGER AS lower_pct,
                   CASE WHEN bin_index BETWEEN 1 AND 21
                        THEN (bin_index-10)*10 ELSE NULL END::INTEGER AS upper_pct,
                   CASE WHEN bin_index=0 THEN 'NEGATIVE_TAIL_BOUNDARY_UNRESOLVED'
                        WHEN bin_index=22 THEN 'AT_LEAST_110_PERCENT'
                        ELSE 'CENTRAL_10_PERCENT_BIN' END AS bin_status,
                   source_sha256,'PUBLIC_EREDES_PTD_UTILIZATION_HISTOGRAM' AS evidence_status
            FROM expanded""")
        con.execute("""CREATE OR REPLACE TABLE study.ptd_utilization_histogram_summary AS
            WITH sums AS (
              SELECT ptd_code,max(installed_transformer_kva) AS transformer_kva,
                     count(*) AS bin_count,sum(hours) AS reported_hours,
                     sum(hours) FILTER (WHERE bin_index<=10) AS negative_utilization_hours,
                     sum(hours) FILTER (WHERE bin_index=11) AS zero_to_ten_pct_hours,
                     sum(hours) FILTER (WHERE bin_index>=21) AS over_100_pct_hours,
                     max(bin_index) FILTER (WHERE hours>0) AS highest_nonzero_bin
              FROM observation.ptd_utilization_histogram_bin GROUP BY 1
            )
            SELECT s.*,e.utilisation_band AS public_peak_band,
                   CASE
                     WHEN e.utilisation_band='0%-19%' AND s.highest_nonzero_bin BETWEEN 11 AND 12 THEN 'CONSISTENT'
                     WHEN e.utilisation_band='20%-39%' AND s.highest_nonzero_bin BETWEEN 13 AND 14 THEN 'CONSISTENT'
                     WHEN e.utilisation_band='40%-59%' AND s.highest_nonzero_bin BETWEEN 15 AND 16 THEN 'CONSISTENT'
                     WHEN e.utilisation_band='60%-79%' AND s.highest_nonzero_bin BETWEEN 17 AND 18 THEN 'CONSISTENT'
                     WHEN e.utilisation_band='80%-99%' AND s.highest_nonzero_bin BETWEEN 19 AND 20 THEN 'CONSISTENT'
                     WHEN e.utilisation_band='+100%' AND s.highest_nonzero_bin>=21 THEN 'CONSISTENT'
                     ELSE 'UNEXPLAINED_PEAK_BAND_DIFFERENCE'
                   END AS peak_band_check,
                   'PUBLIC_HISTOGRAM_PERIOD_NOT_ALIGNED_TO_MODEL_TIMELINE' AS time_alignment_status
            FROM sums s JOIN equivalent.ptd_connections e USING(ptd_code)""")
        checks = {
            "histogram_ptds": con.execute("SELECT count(*) FROM study.ptd_utilization_histogram_summary").fetchone()[0],
            "histogram_bins": con.execute("SELECT count(*) FROM observation.ptd_utilization_histogram_bin").fetchone()[0],
            "unexpected_bin_count_ptds": con.execute("""SELECT count(*)
                FROM study.ptd_utilization_histogram_summary WHERE bin_count<>23""").fetchone()[0],
            "invalid_hour_rows": con.execute("""SELECT count(*) FROM observation.ptd_utilization_histogram_bin
                WHERE hours IS NULL OR hours<0 OR abs(hours*4-round(hours*4))>1e-6""").fetchone()[0],
            "ptds_with_negative_utilization_hours": con.execute("""SELECT count(*)
                FROM study.ptd_utilization_histogram_summary WHERE negative_utilization_hours>0""").fetchone()[0],
            "aggregate_negative_utilization_hours": con.execute("""SELECT sum(negative_utilization_hours)
                FROM study.ptd_utilization_histogram_summary""").fetchone()[0],
            "ptds_with_over_100_pct_hours": con.execute("""SELECT count(*)
                FROM study.ptd_utilization_histogram_summary WHERE over_100_pct_hours>0""").fetchone()[0],
            "peak_band_consistent_ptds": con.execute("""SELECT count(*)
                FROM study.ptd_utilization_histogram_summary WHERE peak_band_check='CONSISTENT'""").fetchone()[0],
            "zero_capacity_ptds_with_histogram": con.execute("""SELECT count(*)
                FROM study.ptd_utilization_histogram_summary WHERE transformer_kva<=0""").fetchone()[0],
            "median_reported_hours": con.execute("""SELECT median(reported_hours)
                FROM study.ptd_utilization_histogram_summary""").fetchone()[0],
        }
    errors = []
    if checks["histogram_bins"] != checks["histogram_ptds"]*23 or checks["unexpected_bin_count_ptds"]:
        errors.append("PTD histogram width is not uniformly 23 bins")
    if checks["invalid_hour_rows"]:
        errors.append("Histogram hours are not nonnegative multiples of a quarter hour")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "source_url": SOURCE_URL,
        "scope": "Public PTD 12-month utilization-hour histograms expanded into indexed bins",
        "limitations": [
            "Current export has 23 bins while the portal description's example has 24; the most negative tail boundary is unresolved",
            "Central 0-10%, 10-20% etc. alignment is supported by the published peak bands, but peak-band differences are retained",
            "Histogram periods are trailing 12 months and do not identify exact start/end timestamps for each PTD",
            "A histogram has no temporal order and cannot itself generate a synchronized PTD series",
        ],
        "errors": errors,
    }
    path = args.database.parent / "ptd_utilization_histograms.validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
