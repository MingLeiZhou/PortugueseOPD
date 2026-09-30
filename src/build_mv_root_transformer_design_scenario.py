#!/usr/bin/env python3
"""Size MV-root equivalent transformers against the full synchronized envelope.

Public station installed MVA is retained in the base parameter table.  This
script derives a separate simulation design capacity for each synthetic MV root
from all 31,492 synchronized load/PV snapshots.  It includes both the PTD/LV
share and inferred MV residual assigned to that root, then applies a configurable
apparent-power margin.  No public capacity value is overwritten.
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
    parser.add_argument("--margin", type=float, default=1.25)
    args = parser.parse_args()
    if args.margin < 1.0:
        parser.error("--margin must be at least 1.0")

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS parameter")
        con.execute("""CREATE OR REPLACE TABLE parameter.mv_root_transformer_design_scenario AS
            WITH lv_station_coeff AS (
              SELECT c.mv_root_bus,a.profile_station_code,
                     sum(a.factor*a.station_lv_fraction) AS lv_station_p_coeff,
                     sum(CASE WHEN a.allocation_status='DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL'
                              THEN a.factor*a.station_pv_weight ELSE a.ptd_pv_weight END)
                         AS gross_solar_coeff,
                     sum(a.ptd_pv_weight) AS pv_solar_coeff
              FROM equivalent.ptd_connections c
              JOIN operating.ptd_reverse_flow_allocation a USING(ptd_code)
              GROUP BY 1,2
            ),
            mv_station_coeff AS (
              SELECT a.mv_root_bus,a.station_code AS profile_station_code,
                     sum((1-coalesce(s.lv_fraction,0))*a.station_root_fraction) AS mv_station_p_coeff
              FROM operating.mv_load_resource_allocation a
              LEFT JOIN operating.station_lv_split s USING(station_code)
              GROUP BY 1,2
            ),
            station_coeff AS (
              SELECT mv_root_bus,profile_station_code,
                     sum(lv_coeff) AS lv_station_p_coeff,
                     sum(mv_coeff) AS mv_station_p_coeff
              FROM (
                SELECT mv_root_bus,profile_station_code,lv_station_p_coeff AS lv_coeff,0::DOUBLE AS mv_coeff
                FROM lv_station_coeff
                UNION ALL
                SELECT mv_root_bus,profile_station_code,0::DOUBLE,mv_station_p_coeff
                FROM mv_station_coeff
              ) x GROUP BY 1,2
            ),
            solar_coeff AS (
              SELECT mv_root_bus,sum(gross_solar_coeff) AS gross_solar_coeff,
                     sum(pv_solar_coeff) AS pv_solar_coeff
              FROM lv_station_coeff GROUP BY 1
            ),
            station_time AS (
              SELECT c.mv_root_bus,s.timestamp_utc,
                     sum(s.p_mw*(c.lv_station_p_coeff+c.mv_station_p_coeff)) AS station_component_p_mw
              FROM station_coeff c
              JOIN operating.station_15min_complete s
                ON s.station_code=c.profile_station_code
              GROUP BY 1,2
            ),
            root_time AS (
              SELECT t.mv_root_bus,t.timestamp_utc,
                     t.station_component_p_mw+
                       r.national_solar_mw*f.lv_fraction*(c.gross_solar_coeff-c.pv_solar_coeff)
                         AS net_p_mw,
                     (t.station_component_p_mw+
                       r.national_solar_mw*f.lv_fraction*c.gross_solar_coeff)*tan(acos(0.97))
                         AS q_mvar
              FROM station_time t
              JOIN solar_coeff c USING(mv_root_bus)
              JOIN operating.ren_solar_15min r USING(timestamp_utc)
              CROSS JOIN operating.solar_split_assumption f
            ),
            envelope AS (
              SELECT mv_root_bus,count(*) AS snapshot_count,
                     max(sqrt(net_p_mw*net_p_mw+q_mvar*q_mvar)) AS peak_apparent_mva,
                     arg_max(timestamp_utc,sqrt(net_p_mw*net_p_mw+q_mvar*q_mvar)) AS peak_timestamp_utc,
                     arg_max(net_p_mw,sqrt(net_p_mw*net_p_mw+q_mvar*q_mvar)) AS peak_net_p_mw,
                     arg_max(q_mvar,sqrt(net_p_mw*net_p_mw+q_mvar*q_mvar)) AS peak_q_mvar
              FROM root_time GROUP BY 1
            )
            SELECT p.equipment_id,p.mv_root_bus,p.hv_bus,p.hv_kv,p.lv_kv,
                   p.sn_mva AS base_sn_mva,p.capacity_status AS base_capacity_status,
                   e.snapshot_count,e.peak_timestamp_utc,e.peak_net_p_mw,e.peak_q_mvar,
                   e.peak_apparent_mva,?::DOUBLE AS design_margin,
                   e.peak_apparent_mva*? AS margin_required_mva,
                   greatest(p.sn_mva,e.peak_apparent_mva*?,0.1) AS design_sn_mva,
                   CASE WHEN p.sn_mva+1e-12>=e.peak_apparent_mva*?
                        THEN 'BASE_CAPACITY_ENVELOPE_SUFFICIENT'
                        ELSE 'FULL_CALENDAR_ENVELOPE_UPSIZED_SCENARIO' END AS design_status,
                   'MV_ROOT_TRANSFORMER_FULL_CALENDAR_DESIGN_V1' AS scenario_id,
                   'SYNCHRONIZED_LOAD_PV_AND_MV_RESIDUAL_APPARENT_POWER_ENVELOPE' AS evidence_status
            FROM parameter.mv_root_transformer_parameters p
            JOIN envelope e USING(mv_root_bus)
            ORDER BY p.mv_root_bus""", [args.margin]*4)

        checks = {
            "mv_root_parameter_rows": con.execute(
                "SELECT count(*) FROM parameter.mv_root_transformer_parameters").fetchone()[0],
            "design_rows": con.execute(
                "SELECT count(*) FROM parameter.mv_root_transformer_design_scenario").fetchone()[0],
            "roots_with_full_31492_snapshot_envelope": con.execute("""SELECT count(*)
                FROM parameter.mv_root_transformer_design_scenario WHERE snapshot_count=31492""").fetchone()[0],
            "upsized_roots": con.execute("""SELECT count(*)
                FROM parameter.mv_root_transformer_design_scenario
                WHERE design_status='FULL_CALENDAR_ENVELOPE_UPSIZED_SCENARIO'""").fetchone()[0],
            "base_capacity_envelope_violations": con.execute("""SELECT count(*)
                FROM parameter.mv_root_transformer_design_scenario
                WHERE peak_apparent_mva>base_sn_mva+1e-12""").fetchone()[0],
            "design_capacity_margin_violations": con.execute("""SELECT count(*)
                FROM parameter.mv_root_transformer_design_scenario
                WHERE design_sn_mva+1e-12<margin_required_mva""").fetchone()[0],
            "minimum_design_mva": con.execute(
                "SELECT min(design_sn_mva) FROM parameter.mv_root_transformer_design_scenario").fetchone()[0],
            "maximum_design_mva": con.execute(
                "SELECT max(design_sn_mva) FROM parameter.mv_root_transformer_design_scenario").fetchone()[0],
            "maximum_upsize_ratio": con.execute("""SELECT max(design_sn_mva/nullif(base_sn_mva,0))
                FROM parameter.mv_root_transformer_design_scenario""").fetchone()[0],
        }
        top = [dict(zip([d[0] for d in con.description], row)) for row in con.execute("""SELECT
                mv_root_bus,base_sn_mva,peak_timestamp_utc,peak_apparent_mva,design_sn_mva,design_status
            FROM parameter.mv_root_transformer_design_scenario
            ORDER BY design_sn_mva/nullif(base_sn_mva,0) DESC NULLS LAST LIMIT 20""").fetchall()]

    errors = []
    if checks["design_rows"] != checks["mv_root_parameter_rows"]:
        errors.append("MV-root design coverage does not match the base parameter table")
    if checks["roots_with_full_31492_snapshot_envelope"] != checks["design_rows"]:
        errors.append("At least one root lacks the full synchronized calendar")
    if checks["design_capacity_margin_violations"]:
        errors.append("At least one design capacity is below its configured envelope margin")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "largest_upsize_cases": top,
        "errors": errors,
        "scope": "Full-calendar apparent-power envelope and separate simulation design capacity for all synthetic 60-kV/MV root transformers",
        "limitations": [
            "The envelope inherits station-to-root, station LV/MV split, PTD load and embedded-PV allocation scenarios",
            "design_sn_mva is a simulation sizing field and does not replace public station installed capacity",
            "One equivalent transformer represents aggregate root capacity; physical unit count and standard nameplate steps remain unknown",
            "Reactive demand uses the explicit 0.97 power-factor scenario",
        ],
    }
    out = args.database.parent / "mv_root_transformer_design_scenario.validation.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
