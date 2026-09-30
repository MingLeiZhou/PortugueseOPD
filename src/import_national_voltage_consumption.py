#!/usr/bin/env python3
"""Import E-REDES 15-minute national BT/MT/AT/MAT energy as scope evidence.

Repeated UTC keys around the autumn clock change are preserved in the raw
table and excluded from the unique-time comparison. National consumption and
substation loading have different metering boundaries, so their difference
must not be used as an automatic PTD load scaling factor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "output/feasibility/consumo_total_nacional_overlap.csv"
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
SOURCE_URL = "https://e-redes.opendatasoft.com/explore/dataset/consumo-total-nacional/"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=CSV)
    parser.add_argument("--database", type=Path, default=DB)
    args = parser.parse_args()
    digest = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS observation")
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("""CREATE OR REPLACE TABLE observation.national_consumption_voltage_15min AS
            WITH raw AS (
              SELECT datahora AS timestamp_utc,
                     try_cast(bt AS DOUBLE) AS bt_kwh,
                     try_cast(mt AS DOUBLE) AS mt_kwh,
                     try_cast("at" AS DOUBLE) AS at_kwh,
                     try_cast(mat AS DOUBLE) AS mat_kwh,
                     try_cast(total AS DOUBLE) AS total_kwh
              FROM read_csv(?, delim=';', header=true, all_varchar=true, encoding='utf-8')
            ), numbered AS (
              SELECT row_number() OVER (ORDER BY timestamp_utc,bt_kwh,mt_kwh,at_kwh,mat_kwh)
                       AS source_row_id,
                     *, count(*) OVER (PARTITION BY timestamp_utc) AS utc_key_multiplicity
              FROM raw
            )
            SELECT *, CASE WHEN utc_key_multiplicity=1 THEN 'UNIQUE_UTC_KEY'
                           ELSE 'AMBIGUOUS_DUPLICATE_UTC_KEY' END AS time_key_status,
                   ? AS source_sha256,
                   'PUBLIC_EREDES_NATIONAL_VOLTAGE_ENERGY' AS evidence_status
            FROM numbered""", [str(args.csv), digest])
        con.execute("""CREATE OR REPLACE VIEW observation.national_consumption_unique_15min AS
            SELECT timestamp_utc, bt_kwh, mt_kwh, at_kwh, mat_kwh, total_kwh,
                   bt_kwh/250 AS bt_mw, mt_kwh/250 AS mt_mw,
                   at_kwh/250 AS at_mw, mat_kwh/250 AS mat_mw,
                   total_kwh/250 AS total_mw, source_sha256, evidence_status
            FROM observation.national_consumption_voltage_15min
            WHERE utc_key_multiplicity=1""")
        con.execute("""CREATE OR REPLACE TABLE study.station_national_consumption_15min AS
            WITH station AS (
              SELECT s.timestamp_utc,
                     sum(s.p_mw) AS station_total_mw,
                     sum(s.p_mw*f.lv_fraction) AS model_direct_lv_mw,
                     sum(s.p_mw*(1-f.lv_fraction)) AS model_station_mv_residual_mw,
                     count(*) AS station_count,
                     count(*) FILTER (WHERE s.data_status<>'PUBLIC_EREDES_STATION_AGGREGATE')
                       AS estimated_station_count
              FROM operating.station_15min_complete s
              JOIN operating.station_lv_split f USING(station_code)
              GROUP BY 1
            )
            SELECT s.*, n.bt_mw AS national_bt_mw, n.mt_mw AS national_mt_mw,
                   n.at_mw AS national_at_mw, n.mat_mw AS national_mat_mw,
                   n.total_mw AS national_total_mw,
                   s.model_direct_lv_mw/nullif(s.station_total_mw,0) AS model_lv_fraction,
                   n.bt_mw/nullif(n.bt_mw+n.mt_mw,0) AS national_bt_share_of_bt_mt,
                   s.station_total_mw/nullif(n.bt_mw+n.mt_mw,0) AS station_to_national_bt_mt_ratio,
                   CASE WHEN n.timestamp_utc IS NULL THEN 'NO_UNIQUE_NATIONAL_UTC_RECORD'
                        ELSE 'DIFFERENT_METERING_BOUNDARIES_COMPARISON_ONLY' END AS comparison_status
            FROM station s
            LEFT JOIN observation.national_consumption_unique_15min n USING(timestamp_utc)""")
        checks = {
            "raw_rows": con.execute("SELECT count(*) FROM observation.national_consumption_voltage_15min").fetchone()[0],
            "unique_utc_keys": con.execute("SELECT count(*) FROM observation.national_consumption_unique_15min").fetchone()[0],
            "ambiguous_rows": con.execute("""SELECT count(*) FROM observation.national_consumption_voltage_15min
                WHERE utc_key_multiplicity>1""").fetchone()[0],
            "ambiguous_utc_keys": con.execute("""SELECT count(DISTINCT timestamp_utc)
                FROM observation.national_consumption_voltage_15min
                WHERE utc_key_multiplicity>1""").fetchone()[0],
            "null_or_negative_energy_rows": con.execute("""SELECT count(*)
                FROM observation.national_consumption_voltage_15min
                WHERE bt_kwh IS NULL OR mt_kwh IS NULL OR at_kwh IS NULL
                   OR mat_kwh IS NULL OR total_kwh IS NULL
                   OR bt_kwh<0 OR mt_kwh<0 OR at_kwh<0 OR mat_kwh<0 OR total_kwh<0""").fetchone()[0],
            "max_total_balance_error_kwh": con.execute("""SELECT max(abs(total_kwh-bt_kwh-mt_kwh-at_kwh-mat_kwh))
                FROM observation.national_consumption_voltage_15min""").fetchone()[0],
            "model_timestamps": con.execute("SELECT count(*) FROM study.station_national_consumption_15min").fetchone()[0],
            "matched_unique_national_timestamps": con.execute("""SELECT count(*)
                FROM study.station_national_consumption_15min
                WHERE national_bt_mw IS NOT NULL""").fetchone()[0],
            "station_split_balance_max_mw": con.execute("""SELECT max(abs(station_total_mw-
                model_direct_lv_mw-model_station_mv_residual_mw))
                FROM study.station_national_consumption_15min""").fetchone()[0],
        }
        snapshot = con.execute("""SELECT timestamp_utc,station_total_mw,model_direct_lv_mw,
            model_station_mv_residual_mw,national_bt_mw,national_mt_mw,
            model_lv_fraction,national_bt_share_of_bt_mt,station_to_national_bt_mt_ratio
            FROM study.station_national_consumption_15min
            WHERE timestamp_utc='2025-07-22T11:45:00+00:00'""").fetchone()
        ranges = con.execute("""SELECT
            median(station_to_national_bt_mt_ratio),
            median(model_lv_fraction),median(national_bt_share_of_bt_mt)
            FROM study.station_national_consumption_15min
            WHERE national_bt_mw IS NOT NULL""").fetchone()
    errors = []
    if checks["raw_rows"] != checks["unique_utc_keys"]+checks["ambiguous_rows"]:
        errors.append("Raw/unique/ambiguous row accounting failed")
    if checks["null_or_negative_energy_rows"] or checks["max_total_balance_error_kwh"]>1e-5:
        errors.append("Energy values missing, negative, or fail voltage-level sum")
    if checks["station_split_balance_max_mw"]>1e-6:
        errors.append("Station LV/MV split does not reconcile")
    if not snapshot or snapshot[4] is None:
        errors.append("Reference snapshot is not present in the public national series")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "reference_snapshot": dict(zip(("timestamp_utc", "station_total_mw", "model_direct_lv_mw",
                                      "model_station_mv_residual_mw", "national_bt_mw", "national_mt_mw",
                                      "model_lv_fraction", "national_bt_share_of_bt_mt",
                                      "station_to_national_bt_mt_ratio"), snapshot)) if snapshot else None,
        "overlap_medians": dict(zip(("station_to_national_bt_mt_ratio", "model_lv_fraction",
                                     "national_bt_share_of_bt_mt"), ranges)),
        "source_sha256": digest,
        "source_url": SOURCE_URL,
        "scope": "Independent national 15-minute energy by voltage level; kWh converted to average MW by dividing by 250",
        "limitations": [
            "National voltage-level consumption and the modeled substation-load sample have different metering boundaries",
            "The comparison cannot by itself assign national BT/MT energy to a specific station or PTD",
            "Repeated UTC keys around the autumn time change are retained raw and excluded from direct time joins",
            "Station LV fractions are existing capacity-bounded scenarios, not measurements",
        ],
        "errors": errors,
    }
    path = args.database.parent / "national_voltage_consumption.validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
