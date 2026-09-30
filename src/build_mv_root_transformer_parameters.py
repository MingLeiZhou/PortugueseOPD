#!/usr/bin/env python3
"""Create explicit parameter scenarios for synthetic 60-kV/MV root bridges.

Public station installed MVA is allocated across its modeled MV voltage roots
in proportion to PTD peak-load proxies. Impedance, vector group, grounding and
tap data remain engineering assumptions because equipment-level values are not
published for these synthetic bridge transformers.
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
        con.execute("CREATE SCHEMA IF NOT EXISTS parameter")
        con.execute("""CREATE OR REPLACE TABLE parameter.mv_root_transformer_parameters AS
            WITH root AS (
              SELECT p.mv_root_bus,p.hv_bus,max(p.mv_voltage_kv) AS lv_kv,
                     count(*) AS ptd_count,sum(p.capacity_kva)/1000.0 AS ptd_nameplate_mva,
                     sum(p.peak_load_proxy_mva)*0.97 AS ptd_peak_proxy_mw
              FROM equivalent.ptd_connections p GROUP BY 1,2
            ), station AS (
              SELECT hv_bus_id,max(installed_mva_public) AS installed_mva_public
              FROM candidate.station_sources GROUP BY 1
            ), weighted AS (
              SELECT r.*,s.installed_mva_public,
                     sum(greatest(r.ptd_peak_proxy_mw,0.1)) OVER (PARTITION BY r.hv_bus)
                       AS station_root_weight_sum_mw,
                     count(*) OVER (PARTITION BY r.hv_bus) AS station_root_count
              FROM root r LEFT JOIN station s ON r.hv_bus=s.hv_bus_id
            )
            SELECT 'BRIDGE:'||mv_root_bus AS equipment_id,mv_root_bus,hv_bus,
                   60.0::DOUBLE AS hv_kv,lv_kv,
                   CASE WHEN installed_mva_public>0 THEN installed_mva_public *
                          greatest(ptd_peak_proxy_mw,0.1)/station_root_weight_sum_mw
                        ELSE greatest(0.1,1.25*ptd_peak_proxy_mw) END AS sn_mva,
                   installed_mva_public AS station_installed_mva_public,
                   ptd_count,ptd_nameplate_mva,ptd_peak_proxy_mw,
                   CASE WHEN installed_mva_public>0
                        THEN 'PUBLIC_STATION_MVA_ALLOCATED_BY_MV_ROOT_PEAK_PROXY'
                        ELSE 'DERIVED_125_PERCENT_OF_PTD_PEAK_PROXY' END AS capacity_status,
                   12.0::DOUBLE AS vk_percent,0.5::DOUBLE AS vkr_percent,
                   8.0::DOUBLE AS vk_low_percent,15.0::DOUBLE AS vk_high_percent,
                   0.2::DOUBLE AS vkr_low_percent,1.5::DOUBLE AS vkr_high_percent,
                   'YNyn0_PROXY' AS vector_group,'UNKNOWN_PROXY' AS grounding_mode,
                   12.0::DOUBLE AS vk0_percent,0.5::DOUBLE AS vkr0_percent,
                   'HV' AS tap_side,0 AS tap_neutral,-8 AS tap_min,8 AS tap_max,
                   1.25::DOUBLE AS tap_step_percent,
                   'ENGINEERING_PROXY_UNCALIBRATED' AS impedance_status,
                   'MV_ROOT_TRANSFORMER_PROXY_V1' AS parameter_set_version
            FROM weighted""")
        checks = {
            "bridge_transformers":con.execute("""SELECT count(*) FROM equivalent.branches
                WHERE evidence_status='SYNTHETIC_60_MV_BRIDGE'""").fetchone()[0],
            "parameter_rows":con.execute("SELECT count(*) FROM parameter.mv_root_transformer_parameters").fetchone()[0],
            "distinct_equipment_ids":con.execute("SELECT count(DISTINCT equipment_id) FROM parameter.mv_root_transformer_parameters").fetchone()[0],
            "rows_using_public_station_capacity":con.execute("""SELECT count(*) FROM parameter.mv_root_transformer_parameters
                WHERE capacity_status='PUBLIC_STATION_MVA_ALLOCATED_BY_MV_ROOT_PEAK_PROXY'""").fetchone()[0],
            "rows_using_derived_capacity":con.execute("""SELECT count(*) FROM parameter.mv_root_transformer_parameters
                WHERE capacity_status='DERIVED_125_PERCENT_OF_PTD_PEAK_PROXY'""").fetchone()[0],
            "invalid_parameter_rows":con.execute("""SELECT count(*) FROM parameter.mv_root_transformer_parameters
                WHERE NOT(sn_mva>0 AND hv_kv>lv_kv AND lv_kv>0
                  AND vkr_percent>0 AND vkr_percent<vk_percent
                  AND vk_low_percent<=vk_percent AND vk_percent<=vk_high_percent
                  AND vkr_low_percent<=vkr_percent AND vkr_percent<=vkr_high_percent
                  AND tap_min<tap_neutral AND tap_neutral<tap_max AND tap_step_percent>0)""").fetchone()[0],
            "station_capacity_allocation_max_error_mva":con.execute("""SELECT max(abs(allocated-installed))
                FROM (SELECT hv_bus,max(station_installed_mva_public) AS installed,
                             sum(sn_mva) AS allocated
                      FROM parameter.mv_root_transformer_parameters
                      WHERE station_installed_mva_public>0 GROUP BY 1)""").fetchone()[0],
            "orphan_parameter_rows":con.execute("""SELECT count(*) FROM parameter.mv_root_transformer_parameters p
                LEFT JOIN equivalent.branches b ON p.equipment_id=b.branch_id
                WHERE b.branch_id IS NULL""").fetchone()[0],
        }
    errors = []
    if (checks["bridge_transformers"]!=checks["parameter_rows"]
            or checks["parameter_rows"]!=checks["distinct_equipment_ids"]
            or checks["invalid_parameter_rows"] or checks["orphan_parameter_rows"]):
        errors.append("Bridge transformer coverage, uniqueness, reference, or range check failed")
    if checks["station_capacity_allocation_max_error_mva"]>1e-8:
        errors.append("Allocated MV-root ratings do not conserve public station installed MVA")
    report = {
        "result":"PASS" if not errors else "FAIL",
        "checks":checks,
        "scope":"Computational parameter scenario for every synthetic 60-kV/MV root bridge",
        "limitations":[
            "Public installed MVA is station aggregate, not transformer-unit or MV-bus equipment data",
            "Allocation across modeled MV voltage roots uses PTD peak-load proxy weights",
            "Impedance, zero-sequence, vector group, grounding and tap parameters are uncalibrated engineering proxies",
            "A complete parameter row does not establish the existence or rating of an actual transformer",
        ],
        "errors":errors,
    }
    path = args.database.parent/"mv_root_transformer_parameters.validation.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=="__main__":
    raise SystemExit(main())
