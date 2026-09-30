#!/usr/bin/env python3
"""Refine PTD transformer and LV feeder design from nationwide root-peak results.

The input is the complete per-root full-calendar peak batch.  Public PTD
capacity and the earlier peak-proxy feeder design remain unchanged.  Separate
scenario tables add a 25% PTD-transformer apparent-power margin and enough
parallel LV feeder circuits to target 0.905 pu at the remote equivalent node
under each root's maximum apparent-power timestamp.
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
    parser.add_argument("--transformer-margin", type=float, default=1.25)
    parser.add_argument("--target-voltage-pu", type=float, default=0.905)
    args = parser.parse_args()
    if args.transformer_margin < 1 or not 0.9 <= args.target_voltage_pu < 1.0:
        parser.error("Require transformer margin >=1 and target voltage in [0.9,1.0)")

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS parameter")
        con.execute("CREATE SCHEMA IF NOT EXISTS phase")
        con.execute("""CREATE OR REPLACE TABLE parameter.ptd_transformer_root_peak_design_scenario AS
            WITH result AS (
              SELECT split_part(transformer_id,':',2) AS ptd_code,mv_root_bus,timestamp_utc,
                     sqrt(p_hv_mw*p_hv_mw+q_hv_mvar*q_hv_mvar) AS root_peak_apparent_mva,
                     loading_percent AS base_solver_loading_percent
              FROM study.equivalent_mv_root_peak_batch_transformer_results
              WHERE transformer_id LIKE 'PTDTRAFO:%'
            )
            SELECT p.ptd_code,r.mv_root_bus,r.timestamp_utc,p.capacity_kva_public,
                   p.sn_mva AS public_sn_mva,
                   CASE WHEN p.sn_mva>0 THEN p.sn_mva ELSE 0.1 END AS base_solver_sn_mva,
                   r.root_peak_apparent_mva,r.base_solver_loading_percent,
                   ?::DOUBLE AS design_margin,
                   greatest(CASE WHEN p.sn_mva>0 THEN p.sn_mva ELSE 0.1 END,
                            r.root_peak_apparent_mva*?) AS design_sn_mva,
                   CASE WHEN p.sn_mva<=0 THEN 'ZERO_PUBLIC_CAPACITY_0P1_MVA_CONNECTION_PROXY'
                        WHEN p.sn_mva+1e-12>=r.root_peak_apparent_mva*?
                          THEN 'PUBLIC_CAPACITY_ROOT_PEAK_MARGIN_SUFFICIENT'
                        ELSE 'ROOT_PEAK_UPSIZED_SIMULATION_SCENARIO' END AS design_status,
                   'PTD_TRANSFORMER_ROOT_PEAK_DESIGN_V1' AS scenario_id,
                   'ROOT_FULL_CALENDAR_PEAK_RESULT_NOT_INSTALLED_ASSET' AS evidence_status
            FROM parameter.ptd_transformer_parameters p JOIN result r USING(ptd_code)
            ORDER BY p.ptd_code""", [args.transformer_margin]*3)

        con.execute("""CREATE OR REPLACE TABLE phase.lv_feeder_root_peak_design_scenario AS
            WITH source_v AS (
              SELECT split_part(bus_id,':',3) AS ptd_code,vm_pu AS root_peak_source_voltage_pu
              FROM study.equivalent_mv_root_peak_batch_bus_results
              WHERE bus_id LIKE 'PTD:LV:%'
            ), end_v AS (
              SELECT split_part(bus_id,':',2) AS ptd_code,mv_root_bus,timestamp_utc,
                     vm_pu AS root_peak_end_voltage_pu
              FROM study.equivalent_mv_root_peak_batch_bus_results
              WHERE bus_id LIKE 'LV:%:2'
            ), current_result AS (
              SELECT split_part(line_id,':',2) AS ptd_code,i_ka,loading_percent
              FROM study.equivalent_mv_root_peak_batch_line_results
              WHERE line_id LIKE 'LVLINE:%:1'
            ), required AS (
              SELECT d.*,e.mv_root_bus,e.timestamp_utc,s.root_peak_source_voltage_pu,
                     e.root_peak_end_voltage_pu,c.i_ka*1000 AS root_peak_current_a_per_base_circuit,
                     c.loading_percent AS root_peak_base_loading_percent,
                     CASE WHEN e.root_peak_end_voltage_pu>=? THEN d.feeder_count
                          WHEN s.root_peak_source_voltage_pu<=? THEN d.feeder_count
                          ELSE greatest(d.feeder_count,ceil(
                            d.feeder_count*(s.root_peak_source_voltage_pu-e.root_peak_end_voltage_pu)
                            /(s.root_peak_source_voltage_pu-?)-1e-12)::INTEGER) END
                       AS refined_feeder_count
              FROM phase.lv_feeder_design_scenario d
              JOIN source_v s USING(ptd_code)
              JOIN end_v e USING(ptd_code)
              JOIN current_result c USING(ptd_code)
            )
            SELECT 'LV_FEEDER_ROOT_PEAK_DESIGN_V2' AS scenario_id,ptd_code,
                   refined_feeder_count AS feeder_count,
                   voltage_required_feeder_count,ampacity_required_feeder_count,
                   section_count,section_length_km,phases,installation_scenario,
                   cable_designation,phase_section_mm2,neutral_section_mm2,
                   r_hot_ohm_per_km,x_ohm_per_km,permissible_current_a,
                   permissible_current_a*root_peak_base_loading_percent/100
                     *feeder_count/refined_feeder_count
                     AS design_current_a_per_feeder,
                   permissible_current_a/nullif(permissible_current_a*root_peak_base_loading_percent/100
                     *feeder_count/refined_feeder_count,0)
                     AS ampacity_margin_ratio,
                   peak_load_proxy_kva,design_factor,ptd_construction_type,
                   installation_basis,
                   'PUBLIC_STANDARD_TYPE_ROOT_PEAK_REFINED_SCENARIO_NOT_INSTALLED_ASSET'
                     AS cable_assignment_status,
                   topology_evidence_class,model_topology_status,electrical_connection_status,
                   nearest_pole_distance_m,pole_count_50m,pole_count_100m,pole_count_200m,
                   'SYNTHETIC_RADIAL_TWO_SECTION_ROOT_PEAK_REFINED_PARALLEL_FEEDERS'
                     AS topology_scenario_status,
                   mv_root_bus,timestamp_utc,feeder_count AS base_feeder_count,
                   root_peak_source_voltage_pu,root_peak_end_voltage_pu,
                   root_peak_current_a_per_base_circuit AS root_peak_total_line_current_a,
                   permissible_current_a*root_peak_base_loading_percent/100
                     AS root_peak_current_a_per_base_circuit,
                   root_peak_base_loading_percent,
                   ?::DOUBLE AS target_voltage_pu,
                   root_peak_source_voltage_pu-
                     (root_peak_source_voltage_pu-root_peak_end_voltage_pu)*feeder_count/refined_feeder_count
                     AS linearized_refined_end_voltage_pu,
                   CASE WHEN refined_feeder_count>feeder_count
                        THEN 'ROOT_PEAK_VOLTAGE_REFINED_SCENARIO'
                        ELSE 'BASE_FEEDER_DESIGN_RETAINED' END AS refinement_status
            FROM required ORDER BY ptd_code""",
            [args.target_voltage_pu,args.target_voltage_pu,args.target_voltage_pu,args.target_voltage_pu])

        checks = {
            "ptd_transformer_design_rows": con.execute(
                "SELECT count(*) FROM parameter.ptd_transformer_root_peak_design_scenario").fetchone()[0],
            "ptd_transformers_upsized": con.execute("""SELECT count(*) FROM
                parameter.ptd_transformer_root_peak_design_scenario
                WHERE design_status='ROOT_PEAK_UPSIZED_SIMULATION_SCENARIO'""").fetchone()[0],
            "zero_public_capacity_connection_proxies": con.execute("""SELECT count(*) FROM
                parameter.ptd_transformer_root_peak_design_scenario WHERE public_sn_mva<=0""").fetchone()[0],
            "ptd_transformer_margin_violations": con.execute("""SELECT count(*) FROM
                parameter.ptd_transformer_root_peak_design_scenario
                WHERE design_sn_mva+1e-12<root_peak_apparent_mva*design_margin""").fetchone()[0],
            "lv_feeder_design_rows": con.execute(
                "SELECT count(*) FROM phase.lv_feeder_root_peak_design_scenario").fetchone()[0],
            "lv_feeders_refined": con.execute("""SELECT count(*) FROM phase.lv_feeder_root_peak_design_scenario
                WHERE feeder_count>base_feeder_count""").fetchone()[0],
            "additional_parallel_feeders": con.execute("""SELECT sum(feeder_count-base_feeder_count)
                FROM phase.lv_feeder_root_peak_design_scenario""").fetchone()[0],
            "maximum_refined_feeder_count": con.execute(
                "SELECT max(feeder_count) FROM phase.lv_feeder_root_peak_design_scenario").fetchone()[0],
            "source_voltage_below_target": con.execute("""SELECT count(*)
                FROM phase.lv_feeder_root_peak_design_scenario
                WHERE root_peak_source_voltage_pu<=target_voltage_pu""").fetchone()[0],
            "linearized_target_violations": con.execute("""SELECT count(*)
                FROM phase.lv_feeder_root_peak_design_scenario
                WHERE linearized_refined_end_voltage_pu<target_voltage_pu-1e-12""").fetchone()[0],
            "refined_ampacity_violations": con.execute("""SELECT count(*)
                FROM phase.lv_feeder_root_peak_design_scenario
                WHERE design_current_a_per_feeder>permissible_current_a+1e-9""").fetchone()[0],
        }

    errors=[]
    if checks["ptd_transformer_design_rows"]!=72434 or checks["lv_feeder_design_rows"]!=72434:
        errors.append("Root-peak PTD transformer or feeder design does not cover all PTDs")
    for key in ("ptd_transformer_margin_violations","source_voltage_below_target",
                "linearized_target_violations","refined_ampacity_violations"):
        if checks[key]: errors.append(f"{key}={checks[key]}")
    report={
        "result":"PASS" if not errors else "FAIL",
        "checks":checks,"errors":errors,
        "scope":"Separate PTD transformer and LV feeder design scenarios refined against each MV root's full-calendar peak solved state",
        "limitations":[
            "Root-peak refinement is a simulation design and does not identify installed PTD transformers, cables, or parallel circuits",
            "The feeder-count formula linearly scales the solved two-section voltage drop; the nonlinear batch rerun is the acceptance test",
            "A root's aggregate peak need not equal every individual PTD's own annual peak",
            "Transformer design uses an equivalent MVA rating and does not infer physical unit count or standard nameplate steps",
        ],
    }
    out=args.database.parent/"root_peak_distribution_design.validation.json"
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=="__main__":
    raise SystemExit(main())
