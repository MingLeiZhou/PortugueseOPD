#!/usr/bin/env python3
"""Build a station-conserving PTD gross-load/PV allocation scenario.

The baseline PTD net load is nonnegative because all PTDs at a station share
the same nonnegative station profile. Here station gross load is distributed
by PTD load factors while the assumed embedded PV is distributed by separate
PV capacity weights. Their difference may reverse at an individual PTD, and
still sums to the original station LV net load at each timestamp.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
DIRECT = "DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    args = parser.parse_args()
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS operating")
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("""CREATE OR REPLACE TABLE operating.station_lv_pv_weight AS
            SELECT a.assigned_station_code AS station_code,
                   sum(p.national_weight) AS station_pv_weight,
                   count(*) AS direct_ptd_count
            FROM operating.ptd_allocations a
            JOIN operating.ptd_pv_allocations p USING(ptd_code)
            WHERE a.allocation_status='DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL'
            GROUP BY 1""")
        con.execute("""CREATE OR REPLACE TABLE operating.ptd_reverse_flow_allocation AS
            SELECT a.ptd_code,a.assigned_station_code,a.profile_station_code,
                   a.factor,a.station_lv_fraction,a.allocation_status,
                   p.national_weight AS ptd_pv_weight,
                   w.station_pv_weight,
                   'SYNTHETIC_INDEPENDENT_GROSS_AND_PV_WEIGHTS' AS evidence_status
            FROM operating.ptd_allocations a
            JOIN operating.ptd_pv_allocations p USING(ptd_code)
            LEFT JOIN operating.station_lv_pv_weight w
              ON a.assigned_station_code=w.station_code""")
        con.execute("""CREATE OR REPLACE VIEW operating.ptd_reverse_flow_scenario_15min AS
            WITH components AS (
              SELECT a.ptd_code,s.timestamp_utc,a.assigned_station_code,
                     a.allocation_status,s.data_status AS profile_data_status,
                     s.p_mw*a.factor*a.station_lv_fraction AS baseline_net_p_mw,
                     ren.national_solar_mw*f.lv_fraction*a.ptd_pv_weight AS pv_p_mw,
                     CASE WHEN a.allocation_status='DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL'
                          THEN a.factor*(s.p_mw*a.station_lv_fraction+
                               ren.national_solar_mw*f.lv_fraction*a.station_pv_weight)
                          ELSE s.p_mw*a.factor*a.station_lv_fraction+
                               ren.national_solar_mw*f.lv_fraction*a.ptd_pv_weight
                     END AS gross_p_mw
              FROM operating.ptd_reverse_flow_allocation a
              JOIN operating.station_15min_complete s
                ON a.profile_station_code=s.station_code
              JOIN operating.ren_solar_15min ren USING(timestamp_utc)
              CROSS JOIN operating.solar_split_assumption f
            )
            SELECT *, gross_p_mw-pv_p_mw AS net_p_mw,
                   gross_p_mw*tan(acos(0.97)) AS gross_q_mvar,
                   'STATION_NET_CONSERVED_PV_LOCATION_SCENARIO' AS evidence_status
            FROM components""")
        peak_time, national_solar_mw = con.execute("""SELECT timestamp_utc,national_solar_mw
            FROM operating.ren_solar_15min ORDER BY national_solar_mw DESC LIMIT 1""").fetchone()
        con.execute("""CREATE OR REPLACE TABLE study.ptd_reverse_flow_peak_solar_snapshot AS
            SELECT * FROM operating.ptd_reverse_flow_scenario_15min
            WHERE timestamp_utc=?""", [peak_time])
        row = con.execute("""SELECT count(*),count(*) FILTER (WHERE net_p_mw<0),
            count(*) FILTER (WHERE gross_p_mw<0 OR pv_p_mw<0),
            sum(gross_p_mw),sum(pv_p_mw),sum(net_p_mw),sum(baseline_net_p_mw),
            max(abs(gross_p_mw-pv_p_mw-net_p_mw))
            FROM study.ptd_reverse_flow_peak_solar_snapshot""").fetchone()
        station_balance = con.execute("""SELECT max(abs(reconstructed_net_mw-public_station_lv_mw))
            FROM (
              SELECT r.assigned_station_code,sum(r.net_p_mw) AS reconstructed_net_mw,
                     max(s.p_mw*f.lv_fraction) AS public_station_lv_mw
              FROM study.ptd_reverse_flow_peak_solar_snapshot r
              JOIN operating.station_15min_complete s
                ON r.assigned_station_code=s.station_code
               AND r.timestamp_utc=s.timestamp_utc
              JOIN operating.station_lv_split f
                ON r.assigned_station_code=f.station_code
              WHERE r.allocation_status='DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL'
              GROUP BY 1)""").fetchone()[0]
        baseline_difference = con.execute("""SELECT abs(sum(r.net_p_mw)-sum(l.p_mw))
            FROM study.ptd_reverse_flow_peak_solar_snapshot r
            JOIN operating.ptd_load_15min l USING(ptd_code,timestamp_utc)""").fetchone()[0]
        public_cross = con.execute("""SELECT count(*) FILTER (WHERE h.negative_utilization_hours>0),
            count(*) FILTER (WHERE r.net_p_mw<0),
            count(*) FILTER (WHERE h.negative_utilization_hours>0 AND r.net_p_mw<0)
            FROM study.ptd_reverse_flow_peak_solar_snapshot r
            JOIN study.ptd_utilization_histogram_summary h USING(ptd_code)""").fetchone()
        target = con.execute("""SELECT ?*lv_fraction
            FROM operating.solar_split_assumption""", [national_solar_mw]).fetchone()[0]
        ptd_count = con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0]
    checks = dict(zip(("ptd_count", "reverse_flow_ptds", "negative_gross_or_pv_ptds",
                       "gross_total_mw", "pv_total_mw", "net_total_mw", "baseline_net_total_mw",
                       "max_ptd_gross_minus_pv_balance_mw"), row))
    checks.update(
        max_direct_station_lv_net_balance_mw=station_balance,
        national_ptd_net_balance_mw=baseline_difference,
        expected_lv_pv_mw=target,
        public_histogram_reverse_ptds=public_cross[0],
        scenario_reverse_ptds_with_histogram=public_cross[1],
        public_and_scenario_reverse_ptds=public_cross[2],
    )
    errors = []
    if row[0] != ptd_count or row[2]:
        errors.append("PTD coverage or nonnegative gross/PV requirement failed")
    if (station_balance>1e-8 or baseline_difference>1e-8
            or abs(row[4]-target)>1e-8 or row[7]>1e-10):
        errors.append("Station/national net and PV energy accounting failed")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "scenario_timestamp_utc": peak_time,
        "national_solar_mw_at_snapshot": national_solar_mw,
        "checks": checks,
        "scope": "Separate gross load and PV placement within each station while conserving station LV net load",
        "limitations": [
            "PTD gross-load weights, PV technology, PV capacities and station supply areas remain proxies",
            "Public negative-utilization hours are not time-aligned with this solar snapshot",
            "Reactive demand is based on scenario gross load at 0.97 power factor; station reactive measurements are unavailable",
            "Fallback PTDs retain their baseline net load because no direct station energy boundary is known",
            "Reverse flow at a PTD does not establish observed feeder voltages or protection behavior",
        ],
        "errors": errors,
    }
    path = args.database.parent / "ptd_reverse_flow_scenario.validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
