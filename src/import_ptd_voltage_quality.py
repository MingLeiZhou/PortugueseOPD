#!/usr/bin/env python3
"""Import E-REDES period-level power-quality compliance as independent evidence.

Percentages are aggregated over monitoring periods and cannot validate one
15-minute simulated voltage snapshot. PTD rows at 230 V are the LV subset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

ROOT=Path(__file__).resolve().parents[1]
CSV=ROOT/"output/feasibility/ptd_voltage_quality_export.csv"
DB=ROOT/"output/all_voltage/all_voltage_staging.duckdb"


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv",type=Path,default=CSV)
    parser.add_argument("--database",type=Path,default=DB)
    args=parser.parse_args()
    digest=hashlib.sha256(args.csv.read_bytes()).hexdigest()
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS observation")
        con.execute("""CREATE OR REPLACE TABLE observation.power_quality_period AS
            SELECT try_cast(year AS INTEGER) AS report_year,
                   code AS installation_code,tipoinstalacao AS installation_type,
                   name AS installation_name,busname AS bus_name,
                   county AS municipality_name,district AS district_name,
                   try_cast(tension AS DOUBLE) AS nominal_voltage_v,
                   try_cast(startdate AS DATE) AS period_start,
                   try_cast(enddate AS DATE) AS period_end,
                   try_cast(l1 AS DOUBLE) AS voltage_l1_compliance_pct,
                   try_cast(l2 AS DOUBLE) AS voltage_l2_compliance_pct,
                   try_cast(l3 AS DOUBLE) AS voltage_l3_compliance_pct,
                   try_cast(l4 AS DOUBLE) AS flicker_l1_compliance_pct,
                   try_cast(l5 AS DOUBLE) AS flicker_l2_compliance_pct,
                   try_cast(l6 AS DOUBLE) AS flicker_l3_compliance_pct,
                   try_cast(l7 AS DOUBLE) AS thd_l1_compliance_pct,
                   try_cast(l8 AS DOUBLE) AS thd_l2_compliance_pct,
                   try_cast(l9 AS DOUBLE) AS thd_l3_compliance_pct,
                   try_cast(unbalance AS DOUBLE) AS unbalance_compliance_pct,
                   try_cast(frequency AS DOUBLE) AS frequency_compliance_pct,
                   harmonics AS harmonic_compliance_text,
                   observations AS event_note,
                   CASE WHEN observations IS NULL OR trim(observations)=''
                        THEN 'NORMAL_MONITORING_PERIOD'
                        ELSE 'EXCEPTIONAL_EVENT_PERIOD' END AS period_status,
                   'E_REDES_PUBLIC_PERIOD_COMPLIANCE_NOT_INSTANTANEOUS' AS evidence_status,
                   ? AS source_sha256
            FROM read_csv(?,delim=';',header=true,all_varchar=true,encoding='utf-8')""",
                    [digest,str(args.csv)])
        con.execute("""CREATE OR REPLACE VIEW observation.ptd_lv_voltage_quality_period AS
            SELECT * FROM observation.power_quality_period
            WHERE installation_type='PTD' AND nominal_voltage_v=230""")
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("""CREATE OR REPLACE TABLE study.ptd_source_voltage_quality_gap AS
            WITH ranked AS (
                SELECT installation_code,report_year,period_start,period_end,
                       voltage_l1_compliance_pct,voltage_l2_compliance_pct,
                       voltage_l3_compliance_pct,unbalance_compliance_pct,
                       row_number() OVER(PARTITION BY installation_code
                         ORDER BY report_year DESC,period_end DESC) AS rn
                FROM observation.ptd_lv_voltage_quality_period
                WHERE period_status='NORMAL_MONITORING_PERIOD'
            )
            SELECT q.installation_code AS ptd_code,q.report_year,q.period_start,q.period_end,
                   q.voltage_l1_compliance_pct,q.voltage_l2_compliance_pct,
                   q.voltage_l3_compliance_pct,q.unbalance_compliance_pct,
                   least(q.voltage_l1_compliance_pct,q.voltage_l2_compliance_pct,
                         q.voltage_l3_compliance_pct) AS observed_min_voltage_compliance_pct,
                   100.0::DOUBLE AS ideal_source_voltage_compliance_pct,
                   CASE WHEN q.voltage_l1_compliance_pct<100 OR q.voltage_l2_compliance_pct<100
                              OR q.voltage_l3_compliance_pct<100
                        THEN 'HISTORIC_SUB100_COMPLIANCE_NOT_REPRESENTABLE_BY_IDEAL_SOURCE'
                        ELSE 'NO_HISTORIC_VOLTAGE_NONCOMPLIANCE_IN_LATEST_MONITORING_PERIOD'
                   END AS model_gap_status
            FROM ranked q
            JOIN candidate.ptd_public_attributes p ON q.installation_code=p.ptd_code
            WHERE q.rn=1""")
        counts=con.execute("""SELECT count(*),count(DISTINCT installation_code),
                   count(*) FILTER(WHERE period_status='NORMAL_MONITORING_PERIOD'),
                   count(*) FILTER(WHERE period_status='NORMAL_MONITORING_PERIOD'
                     AND (voltage_l1_compliance_pct<100 OR voltage_l2_compliance_pct<100
                          OR voltage_l3_compliance_pct<100)),
                   count(*) FILTER(WHERE period_status='NORMAL_MONITORING_PERIOD'
                     AND unbalance_compliance_pct<100),
                   min(report_year),max(report_year)
            FROM observation.ptd_lv_voltage_quality_period""").fetchone()
        overlap=con.execute("""SELECT count(DISTINCT q.installation_code),count(*)
            FROM observation.ptd_lv_voltage_quality_period q
            JOIN candidate.ptd_public_attributes p ON q.installation_code=p.ptd_code""").fetchone()
        malformed=con.execute("""SELECT count(*) FROM observation.ptd_lv_voltage_quality_period
            WHERE report_year IS NULL OR installation_code IS NULL OR period_start IS NULL
               OR period_end IS NULL OR nominal_voltage_v IS NULL
               OR voltage_l1_compliance_pct NOT BETWEEN 0 AND 100
               OR voltage_l2_compliance_pct NOT BETWEEN 0 AND 100
               OR voltage_l3_compliance_pct NOT BETWEEN 0 AND 100""").fetchone()[0]
        total=con.execute("SELECT count(*) FROM observation.power_quality_period").fetchone()[0]
        source_missing_nominal=con.execute("""SELECT count(*) FROM observation.power_quality_period
            WHERE nominal_voltage_v IS NULL""").fetchone()[0]
        gap=con.execute("""SELECT count(*),
                count(*) FILTER(WHERE observed_min_voltage_compliance_pct<100),
                count(*) FILTER(WHERE report_year=2024),
                count(*) FILTER(WHERE report_year=2024
                  AND observed_min_voltage_compliance_pct<100)
            FROM study.ptd_source_voltage_quality_gap""").fetchone()
    keys=("ptd_230v_period_rows","distinct_ptd_230v_codes","normal_ptd_230v_period_rows",
          "normal_periods_with_any_voltage_noncompliance",
          "normal_periods_with_unbalance_noncompliance","earliest_year","latest_year")
    checks=dict(zip(keys,counts))
    checks.update(all_power_quality_rows=total,matching_current_ptd_codes=overlap[0],
                  matching_current_ptd_period_rows=overlap[1],malformed_ptd_230v_rows=malformed,
                  other_source_rows_missing_nominal_voltage=source_missing_nominal,
                  latest_normal_monitored_current_ptds=gap[0],
                  latest_normal_periods_with_historic_voltage_noncompliance=gap[1],
                  latest_normal_periods_from_2024=gap[2],
                  latest_2024_periods_with_historic_voltage_noncompliance=gap[3])
    errors=[]
    if malformed or counts[0]==0 or overlap[0]==0:
        errors.append("Power-quality source has invalid fields or no current PTD overlap")
    report={"result":"PASS" if not errors else "FAIL","checks":checks,
            "source_sha256":digest,
            "source_url":"https://e-redes.opendatasoft.com/explore/dataset/qualidade_energia_fenomenoscontinuos-final/",
            "scope":"Historical period-level voltage compliance at monitored PTD LV busbars; independent of simulated voltage snapshots",
            "limitations":["Compliance percentages contain no measured voltage waveform or timestamped 15-minute samples",
                           "2014-2024 monitoring periods do not align with the modeled 2025-2026 snapshots",
                           "Monitored PTDs are a selected subset; absence of a row is not evidence of compliance",
                           "An ideal 230 V source cannot represent observed historic voltage noncompliance, but the years do not align for pointwise error calculation"],
            "errors":errors}
    path=args.database.parent/"ptd_voltage_quality.validation.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=='__main__':
    raise SystemExit(main())
