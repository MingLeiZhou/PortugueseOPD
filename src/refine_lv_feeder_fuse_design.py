#!/usr/bin/env python3
"""Refine the root-peak LV feeder design using public E-REDES fuse currents.

The preceding design constrains voltage and cable ampacity.  E-REDES
DIT-C14-100/N also publishes a lower service-fuse current for each standard
cable type.  This step adds equal parallel feeder/protection circuits wherever
the validated root-peak current would exceed that public fuse value.  It keeps
the original count and formula inputs in a separate protection scenario table.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
REPORT = ROOT / "output/all_voltage/lv_feeder_fuse_design.validation.json"
SCENARIO_ID = "LV_FEEDER_ROOT_PEAK_FUSE_REFINED_V3"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS protection")
        con.execute("""CREATE OR REPLACE TABLE protection.lv_feeder_fuse_refinement_scenario AS
            WITH source_v AS (
              SELECT split_part(bus_id,':',3) AS ptd_code,vm_pu AS actual_source_voltage_pu
              FROM study.equivalent_mv_root_peak_batch_bus_results
              WHERE bus_id LIKE 'PTD:LV:%'
            ), end_v AS (
              SELECT split_part(bus_id,':',2) AS ptd_code,vm_pu AS actual_end_voltage_pu
              FROM study.equivalent_mv_root_peak_batch_bus_results
              WHERE bus_id LIKE 'LV:%:2'
            ), line_i AS (
              SELECT split_part(line_id,':',2) AS ptd_code,i_ka*1000 AS actual_total_line_current_a
              FROM study.equivalent_mv_root_peak_batch_line_results
              WHERE line_id LIKE 'LVLINE:%:1'
            ), abcn AS (
              SELECT ptd_code,feeder_count AS abcn_feeder_count,
                     max_conductor_current_a*feeder_count AS abcn_total_max_conductor_current_a
              FROM study.lv_fourwire_root_peak_design_snapshot
              WHERE status='CONVERGED'
            ), base AS (
              SELECT d.*,d.feeder_count AS pre_fuse_count,
                     s.actual_source_voltage_pu,e.actual_end_voltage_pu,
                     i.actual_total_line_current_a,
                     CASE WHEN a.abcn_feeder_count=d.feeder_count
                          THEN a.abcn_total_max_conductor_current_a END
                       AS fresh_abcn_total_max_conductor_current_a
              FROM phase.lv_feeder_root_peak_design_scenario d
              JOIN source_v s USING(ptd_code)
              JOIN end_v e USING(ptd_code)
              JOIN line_i i USING(ptd_code)
              LEFT JOIN abcn a USING(ptd_code)
            )
            SELECT ? AS scenario_id,d.ptd_code,d.mv_root_bus,d.timestamp_utc,
                   d.cable_designation,d.pre_fuse_count AS pre_fuse_feeder_count,
                   c.fuse_service_current_a,d.permissible_current_a,
                   d.actual_total_line_current_a AS root_peak_total_line_current_a,
                   d.actual_source_voltage_pu,d.actual_end_voltage_pu,
                   d.fresh_abcn_total_max_conductor_current_a AS abcn_total_max_conductor_current_a,
                   greatest(1,ceil(d.actual_total_line_current_a/
                     nullif(c.fuse_service_current_a,0)-1e-12)::INTEGER)
                     AS fuse_required_feeder_count,
                   CASE WHEN d.fresh_abcn_total_max_conductor_current_a IS NULL THEN NULL
                        ELSE greatest(1,ceil(d.fresh_abcn_total_max_conductor_current_a/
                          nullif(c.fuse_service_current_a,0)-1e-12)::INTEGER) END
                     AS abcn_fuse_required_feeder_count,
                   greatest(d.pre_fuse_count,fuse_required_feeder_count,
                            coalesce(abcn_fuse_required_feeder_count,1))
                     AS selected_feeder_count,
                   selected_feeder_count-d.pre_fuse_count AS additional_feeder_count,
                   d.actual_total_line_current_a/selected_feeder_count
                     AS selected_current_a_per_feeder,
                   d.fresh_abcn_total_max_conductor_current_a/selected_feeder_count
                     AS selected_abcn_max_conductor_current_a,
                   c.fuse_service_current_a/nullif(selected_current_a_per_feeder,0)
                     AS fuse_margin_ratio,
                   c.fuse_service_current_a/nullif(selected_abcn_max_conductor_current_a,0)
                     AS abcn_fuse_margin_ratio,
                   'PUBLIC_EREDES_FUSE_CURRENT_WITH_SIMULATED_CABLE_AND_PARALLEL_FEEDER_ASSIGNMENT'
                     AS evidence_status,
                   c.source_url,c.source_sha256
            FROM base d
            JOIN parameter.lv_cable_catalog c ON c.designation=d.cable_designation
            ORDER BY d.ptd_code""", [SCENARIO_ID])

        for column, data_type in (
            ("fuse_service_current_a", "DOUBLE"),
            ("fuse_required_feeder_count", "INTEGER"),
            ("fuse_margin_ratio", "DOUBLE"),
        ):
            con.execute(f"ALTER TABLE phase.lv_feeder_root_peak_design_scenario ADD COLUMN IF NOT EXISTS {column} {data_type}")

        con.execute("""UPDATE phase.lv_feeder_root_peak_design_scenario AS d SET
            scenario_id=?,
            feeder_count=r.selected_feeder_count,
            design_current_a_per_feeder=r.selected_current_a_per_feeder,
            ampacity_margin_ratio=d.permissible_current_a/nullif(r.selected_current_a_per_feeder,0),
            linearized_refined_end_voltage_pu=r.actual_source_voltage_pu-
              (r.actual_source_voltage_pu-r.actual_end_voltage_pu)*
              r.pre_fuse_feeder_count/r.selected_feeder_count,
            refinement_status=CASE WHEN r.additional_feeder_count>0
              THEN 'ROOT_PEAK_PUBLIC_FUSE_CURRENT_REFINED_SCENARIO'
              ELSE d.refinement_status END,
            topology_scenario_status=CASE WHEN r.additional_feeder_count>0
              THEN 'SYNTHETIC_RADIAL_TWO_SECTION_ROOT_PEAK_FUSE_REFINED_PARALLEL_FEEDERS'
              ELSE d.topology_scenario_status END,
            fuse_service_current_a=r.fuse_service_current_a,
            fuse_required_feeder_count=r.fuse_required_feeder_count,
            fuse_margin_ratio=r.fuse_margin_ratio,
            root_peak_source_voltage_pu=r.actual_source_voltage_pu,
            root_peak_end_voltage_pu=r.actual_end_voltage_pu,
            root_peak_total_line_current_a=r.root_peak_total_line_current_a,
            root_peak_current_a_per_base_circuit=r.root_peak_total_line_current_a/r.pre_fuse_feeder_count,
            root_peak_base_loading_percent=100*r.root_peak_total_line_current_a/
              r.pre_fuse_feeder_count/nullif(d.permissible_current_a,0)
            FROM protection.lv_feeder_fuse_refinement_scenario r
            WHERE d.ptd_code=r.ptd_code""", [SCENARIO_ID])

        cursor = con.execute("""SELECT
            (SELECT count(*) FROM protection.lv_feeder_fuse_refinement_scenario) AS scenario_rows,
            (SELECT count(DISTINCT ptd_code) FROM protection.lv_feeder_fuse_refinement_scenario) AS distinct_ptds,
            (SELECT count(*) FROM protection.lv_feeder_fuse_refinement_scenario WHERE additional_feeder_count>0) AS ptds_refined,
            (SELECT coalesce(sum(additional_feeder_count),0) FROM protection.lv_feeder_fuse_refinement_scenario) AS additional_feeders,
            (SELECT max(selected_feeder_count) FROM protection.lv_feeder_fuse_refinement_scenario) AS maximum_feeder_count,
            (SELECT count(*) FROM protection.lv_feeder_fuse_refinement_scenario WHERE abcn_fuse_required_feeder_count IS NOT NULL) AS fresh_abcn_inputs,
            (SELECT count(*) FROM protection.lv_feeder_fuse_refinement_scenario WHERE selected_current_a_per_feeder>fuse_service_current_a+1e-9) AS fuse_current_violations,
            (SELECT count(*) FROM protection.lv_feeder_fuse_refinement_scenario WHERE selected_abcn_max_conductor_current_a>fuse_service_current_a+1e-9) AS abcn_fuse_current_violations,
            (SELECT count(*) FROM phase.lv_feeder_root_peak_design_scenario WHERE design_current_a_per_feeder>permissible_current_a+1e-9) AS ampacity_violations,
            (SELECT count(*) FROM phase.lv_feeder_root_peak_design_scenario WHERE linearized_refined_end_voltage_pu<target_voltage_pu-1e-12) AS linearized_voltage_violations,
            (SELECT count(*) FROM phase.lv_feeder_root_peak_design_scenario WHERE fuse_service_current_a IS NULL) AS missing_fuse_values
        """)
        checks = dict(zip([item[0] for item in cursor.description], cursor.fetchone()))

    errors = []
    if checks["scenario_rows"] != 72434 or checks["distinct_ptds"] != 72434:
        errors.append("Fuse refinement does not uniquely cover all 72,434 PTDs")
    for key in ("fuse_current_violations", "abcn_fuse_current_violations", "ampacity_violations", "linearized_voltage_violations", "missing_fuse_values"):
        if checks[key]:
            errors.append(f"{key}={checks[key]}")

    report = {
        "result": "PASS" if not errors else "FAIL",
        "scenario_id": SCENARIO_ID,
        "checks": checks,
        "rule": "selected feeder count = max(existing voltage/ampacity count, ceil(balanced root-peak total current / public E-REDES fuse current), ceil(ABCN root-peak maximum-phase total current / public E-REDES fuse current) when a matching fresh ABCN result exists)",
        "why": "The public standard service-fuse current is lower than cable thermal ampacity; the ABCN term also prevents phase imbalance from overloading the most heavily loaded fuse.",
        "provenance": "parameter.lv_cable_catalog imported from E-REDES DIT-C14-100/N Ed.9, with public source URL and SHA-256 retained per cable type.",
        "limitations": [
            "Cable type and parallel feeder assignment remain simulation designs rather than observed installed assets.",
            "Equal sharing among parallel circuits is assumed.",
            "Manufacturer time-current curves, discrimination with upstream protection and measured fault-loop impedance remain unavailable.",
            "The nonlinear AC and ABCN root-peak studies must be rerun after this refinement.",
        ],
        "errors": errors,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
