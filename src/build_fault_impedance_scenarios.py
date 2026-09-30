#!/usr/bin/env python3
"""Build factorized 3ph/2ph/1ph-ground/2ph-ground impedance scenarios.

The persisted Thevenin table is derived from the validated nationwide root-peak
IEC 60909 results.  A view expands it over explicit bolted, 0.1-ohm and 1-ohm
fault-impedance scenarios without duplicating the sequence equivalents.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
REPORT = ROOT / "output/all_voltage/fault_impedance_scenarios.validation.json"


def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=DB)
    parser.add_argument("--report",type=Path,default=REPORT)
    args=parser.parse_args()
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS scenario")
        con.execute("CREATE SCHEMA IF NOT EXISTS parameter")
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("CREATE OR REPLACE MACRO hypot2(x,y) AS sqrt(x*x+y*y)")
        con.execute("""CREATE OR REPLACE TABLE scenario.fault_impedance_scenario(
            scenario_id VARCHAR PRIMARY KEY,r_fault_ohm DOUBLE,x_fault_ohm DOUBLE,
            evidence_status VARCHAR,description VARCHAR)""")
        con.executemany("INSERT INTO scenario.fault_impedance_scenario VALUES (?,?,?,?,?)",[
            ("BOLTED_R0_X0",0.0,0.0,"SIMULATION_SCENARIO",
             "Bolted fault; reproduces the existing IEC 60909 base calculation"),
            ("ARC_R0P1_X0",0.1,0.0,"SIMULATION_SENSITIVITY_NOT_OBSERVED",
             "0.1-ohm resistive fault sensitivity"),
            ("HIGH_R1P0_X0",1.0,0.0,"SIMULATION_SENSITIVITY_NOT_OBSERVED",
             "1.0-ohm high-resistance fault sensitivity"),
        ])
        con.execute("""CREATE OR REPLACE TABLE parameter.fault_thevenin_equivalent AS
            SELECT p.mv_root_bus,p.bus_id,p."case" AS max_min_case,p.timestamp_utc,
                   b.voltage_kv AS nominal_voltage_kv,
                   p.ikss_ka AS three_phase_bolted_current_ka,
                   p.rk_ohm AS r1_ohm,p.xk_ohm AS x1_ohm,
                   g.rk0_ohm AS r0_ohm,g.xk0_ohm AS x0_ohm,
                   p.ikss_ka*hypot2(p.rk_ohm,p.xk_ohm) AS source_phase_voltage_kv,
                   p.source_short_circuit_mva,p.source_rx_ratio,
                   p.source_r0x0_ratio,p.source_x0x1_ratio,
                   'FAULT_SOURCE_ZERO_SEQUENCE_PROXY_V1' AS parameter_scenario_id,
                   'DERIVED_FROM_VALIDATED_ROOT_PEAK_IEC60909_RESULTS' AS evidence_status
            FROM study.equivalent_mv_root_peak_batch_fault_results p
            JOIN study.equivalent_mv_root_peak_batch_fault_results g
              ON p.mv_root_bus=g.mv_root_bus AND p.bus_id=g.bus_id
             AND p."case"=g."case" AND g.fault_type='1ph'
            JOIN equivalent.buses b ON b.bus_id=p.bus_id
            WHERE p.fault_type='3ph'""")

        # The first three branches are the standard sequence-network formulas.
        # The final branch expands a B-C-ground fault and reports the largest
        # faulted-phase current; phase A should be numerical zero.
        con.execute("""CREATE OR REPLACE VIEW study.mv_root_fault_impedance_sensitivity AS
            WITH base AS (
              SELECT * FROM parameter.fault_thevenin_equivalent
            ), scenarios AS (
              SELECT * FROM scenario.fault_impedance_scenario
            ), two_ground_0 AS (
              SELECT b.*,s.scenario_id,s.r_fault_ohm,s.x_fault_ohm,
                     b.r0_ohm+3*s.r_fault_ohm AS ar,
                     b.x0_ohm+3*s.x_fault_ohm AS ai,
                     b.r1_ohm+b.r0_ohm+3*s.r_fault_ohm AS dr,
                     b.x1_ohm+b.x0_ohm+3*s.x_fault_ohm AS di
              FROM base b CROSS JOIN scenarios s
            ), two_ground_1 AS (
              SELECT *,
                (r1_ohm*ar-x1_ohm*ai)*dr+(r1_ohm*ai+x1_ohm*ar)*di AS qrn,
                (r1_ohm*ai+x1_ohm*ar)*dr-(r1_ohm*ar-x1_ohm*ai)*di AS qin,
                dr*dr+di*di AS dd,
                (ar*dr+ai*di)/(dr*dr+di*di) AS uar,
                (ai*dr-ar*di)/(dr*dr+di*di) AS uai,
                (r1_ohm*dr+x1_ohm*di)/(dr*dr+di*di) AS ubr,
                (x1_ohm*dr-r1_ohm*di)/(dr*dr+di*di) AS ubi
              FROM two_ground_0
            ), two_ground_2 AS (
              SELECT *,r1_ohm+qrn/dd AS er,x1_ohm+qin/dd AS ei
              FROM two_ground_1
            ), two_ground_3 AS (
              SELECT *,
                source_phase_voltage_kv*er/(er*er+ei*ei) AS i1r,
               -source_phase_voltage_kv*ei/(er*er+ei*ei) AS i1i
              FROM two_ground_2
            ), two_ground_4 AS (
              SELECT *,
                -(i1r*uar-i1i*uai) AS i2r,-(i1r*uai+i1i*uar) AS i2i,
                -(i1r*ubr-i1i*ubi) AS i0r,-(i1r*ubi+i1i*ubr) AS i0i
              FROM two_ground_3
            ), two_ground AS (
              SELECT *,
                hypot2(i0r+i1r+i2r,i0i+i1i+i2i) AS ia,
                hypot2(i0r-0.5*i1r+sqrt(3)/2*i1i-0.5*i2r-sqrt(3)/2*i2i,
                      i0i-sqrt(3)/2*i1r-0.5*i1i+sqrt(3)/2*i2r-0.5*i2i) AS ib,
                hypot2(i0r-0.5*i1r-sqrt(3)/2*i1i-0.5*i2r+sqrt(3)/2*i2i,
                      i0i+sqrt(3)/2*i1r-0.5*i1i-sqrt(3)/2*i2r-0.5*i2i) AS ic
              FROM two_ground_4
            )
            SELECT b.mv_root_bus,b.bus_id,b.max_min_case,b.timestamp_utc,
                   b.nominal_voltage_kv,'3ph' AS fault_type,s.scenario_id,
                   s.r_fault_ohm,s.x_fault_ohm,
                   b.source_phase_voltage_kv/hypot2(b.r1_ohm+s.r_fault_ohm,
                                                   b.x1_ohm+s.x_fault_ohm) AS fault_current_ka,
                   fault_current_ka AS phase_a_current_ka,
                   fault_current_ka AS phase_b_current_ka,
                   fault_current_ka AS phase_c_current_ka,
                   fault_current_ka AS positive_sequence_current_ka,
                   0.0::DOUBLE AS negative_sequence_current_ka,
                   0.0::DOUBLE AS zero_sequence_current_ka,
                   b.r1_ohm,b.x1_ohm,b.r0_ohm,b.x0_ohm,
                   b.parameter_scenario_id,'IEC_60909_SEQUENCE_NETWORK_FORMULA' AS calculation_method
            FROM base b CROSS JOIN scenarios s
            UNION ALL
            SELECT b.mv_root_bus,b.bus_id,b.max_min_case,b.timestamp_utc,
                   b.nominal_voltage_kv,'2ph',s.scenario_id,s.r_fault_ohm,s.x_fault_ohm,
                   sqrt(3)*b.source_phase_voltage_kv/
                     hypot2(2*b.r1_ohm+s.r_fault_ohm,2*b.x1_ohm+s.x_fault_ohm) AS fault_current_ka,
                   0.0,fault_current_ka,fault_current_ka,
                   fault_current_ka/sqrt(3),fault_current_ka/sqrt(3),0.0,
                   b.r1_ohm,b.x1_ohm,b.r0_ohm,b.x0_ohm,
                   b.parameter_scenario_id,'IEC_60909_SEQUENCE_NETWORK_FORMULA'
            FROM base b CROSS JOIN scenarios s
            UNION ALL
            SELECT b.mv_root_bus,b.bus_id,b.max_min_case,b.timestamp_utc,
                   b.nominal_voltage_kv,'1ph_ground',s.scenario_id,s.r_fault_ohm,s.x_fault_ohm,
                   3*b.source_phase_voltage_kv/
                     hypot2(2*b.r1_ohm+b.r0_ohm+3*s.r_fault_ohm,
                           2*b.x1_ohm+b.x0_ohm+3*s.x_fault_ohm) AS fault_current_ka,
                   fault_current_ka,0.0,0.0,
                   fault_current_ka/3,fault_current_ka/3,fault_current_ka/3,
                   b.r1_ohm,b.x1_ohm,b.r0_ohm,b.x0_ohm,
                   b.parameter_scenario_id,'IEC_60909_SEQUENCE_NETWORK_FORMULA'
            FROM base b CROSS JOIN scenarios s
            UNION ALL
            SELECT mv_root_bus,bus_id,max_min_case,timestamp_utc,nominal_voltage_kv,
                   '2ph_ground',scenario_id,r_fault_ohm,x_fault_ohm,
                   greatest(ib,ic) AS fault_current_ka,ia,ib,ic,
                   hypot2(i1r,i1i),hypot2(i2r,i2i),hypot2(i0r,i0i),
                   r1_ohm,x1_ohm,r0_ohm,x0_ohm,parameter_scenario_id,
                   'SYMMETRICAL_COMPONENTS_TWO_PHASE_GROUND_FORMULA'
            FROM two_ground""")

        base_rows=con.execute("SELECT count(*) FROM parameter.fault_thevenin_equivalent").fetchone()[0]
        distinct_roots=con.execute("SELECT count(DISTINCT mv_root_bus) FROM parameter.fault_thevenin_equivalent").fetchone()[0]
        expected=base_rows*3*4
        logical_rows=con.execute("SELECT count(*) FROM study.mv_root_fault_impedance_sensitivity").fetchone()[0]
        reproduction=con.execute("""WITH reference AS (
              SELECT mv_root_bus,bus_id,"case" AS max_min_case,
                     CASE fault_type WHEN '1ph' THEN '1ph_ground' ELSE fault_type END fault_type,
                     ikss_ka
              FROM study.equivalent_mv_root_peak_batch_fault_results
            )
            SELECT max(abs(v.fault_current_ka-r.ikss_ka))
            FROM study.mv_root_fault_impedance_sensitivity v JOIN reference r
              USING(mv_root_bus,bus_id,max_min_case,fault_type)
            WHERE v.scenario_id='BOLTED_R0_X0'""").fetchone()[0]
        duplicate_keys=con.execute("""SELECT count(*) FROM (
            SELECT mv_root_bus,bus_id,max_min_case,fault_type,scenario_id,count(*) n
            FROM study.mv_root_fault_impedance_sensitivity GROUP BY 1,2,3,4,5 HAVING n<>1)""").fetchone()[0]
        invalid=con.execute("""SELECT count(*) FROM study.mv_root_fault_impedance_sensitivity
            WHERE NOT isfinite(fault_current_ka) OR fault_current_ka<=0
               OR phase_a_current_ka<0 OR phase_b_current_ka<0 OR phase_c_current_ka<0""").fetchone()[0]
        two_ground_a_residual=con.execute("""SELECT max(phase_a_current_ka)
            FROM study.mv_root_fault_impedance_sensitivity WHERE fault_type='2ph_ground'""").fetchone()[0]
        resistance_monotonic_violations=con.execute("""WITH p AS (
            SELECT *,lag(fault_current_ka) OVER
              (PARTITION BY mv_root_bus,bus_id,max_min_case,fault_type ORDER BY r_fault_ohm) prior_current
            FROM study.mv_root_fault_impedance_sensitivity)
            SELECT count(*) FROM p WHERE prior_current IS NOT NULL
              AND fault_type<>'2ph_ground'
              AND fault_current_ka>prior_current+1e-10""").fetchone()[0]
        two_ground_nonmonotonic=con.execute("""WITH p AS (
            SELECT *,lag(fault_current_ka) OVER
              (PARTITION BY mv_root_bus,bus_id,max_min_case,fault_type ORDER BY r_fault_ohm) prior_current
            FROM study.mv_root_fault_impedance_sensitivity)
            SELECT count(*) FROM p WHERE prior_current IS NOT NULL
              AND fault_type='2ph_ground'
              AND fault_current_ka>prior_current+1e-10""").fetchone()[0]
    checks={
        "thevenin_rows":base_rows,"distinct_mv_roots":distinct_roots,
        "impedance_scenarios":3,"fault_types":4,"logical_result_rows":logical_rows,
        "expected_logical_result_rows":expected,
        "bolted_formula_max_reproduction_error_ka":float(reproduction or 0),
        "duplicate_result_keys":duplicate_keys,"invalid_fault_currents":invalid,
        "max_two_phase_ground_healthy_phase_current_ka":float(two_ground_a_residual or 0),
        "resistance_monotonic_violations_for_3ph_2ph_1ph_ground":resistance_monotonic_violations,
        "two_phase_ground_nonmonotonic_transitions":two_ground_nonmonotonic,
    }
    errors=[]
    if base_rows!=581940 or distinct_roots!=617 or logical_rows!=expected:
        errors.append("Nationwide Thevenin or logical scenario coverage failed")
    if reproduction is None or reproduction>1e-10:
        errors.append("Bolted formulas do not reproduce validated pandapower results")
    if duplicate_keys or invalid or two_ground_a_residual>1e-10 or resistance_monotonic_violations:
        errors.append("Fault scenario numerical or key integrity failed")
    report={"result":"PASS" if not errors else "FAIL","checks":checks,"errors":errors,
            "scope":"Factorized nationwide positive/negative/zero-sequence fault sensitivity for 3ph, 2ph, 1ph-ground and 2ph-ground faults at bolted, 0.1-ohm and 1.0-ohm resistance",
            "limitations":["Thevenin equivalents inherit the synthetic topology, source-strength and sequence-parameter scenarios",
                           "Negative-sequence impedance is assumed equal to positive-sequence impedance",
                           "Fault resistance levels are sensitivities, not observed Portuguese fault records",
                           "Two-phase-ground maximum phase current need not decrease monotonically as ground resistance changes because the limit approaches a phase-to-phase fault and sequence-current sharing changes",
                           "Inverter fault-current contribution and protection clearing are excluded"]}
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=="__main__":
    raise SystemExit(main())
