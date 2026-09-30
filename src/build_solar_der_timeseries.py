#!/usr/bin/env python3
"""Factorize public REN solar dispatch using PTD generation capacity where visible.

REN does not identify each PTD's output or the utility/embedded solar split.
E-REDES reports connected generation capacity for only a disclosed PTD subset,
without identifying its technology. All PV interpretation remains a scenario.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/releases/SimPT60-2026.09.21-r1/bundle/inputs.duckdb"
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--lv-fraction", type=float, default=0.10,
                        help="Assumed fraction of national REN solar power allocated to PTD PV")
    parser.add_argument("--ptd-capacity-fraction", type=float, default=0.10,
                        help="PV capacity proxy for PTDs whose generation capacity is suppressed")
    parser.add_argument("--public-generation-pv-fraction", type=float, default=1.0,
                        help="Scenario fraction of disclosed connected generation interpreted as PV")
    args = parser.parse_args()
    if (not 0 < args.lv_fraction < 1 or not 0 < args.ptd_capacity_fraction <= 1
            or not 0 <= args.public_generation_pv_fraction <= 1):
        parser.error("Fractions must be within (0,1)")
    quoted = str(args.base).replace("'", "''")
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS operating")
        con.execute(f"ATTACH '{quoted}' AS frozen (READ_ONLY)")
        con.execute("""CREATE OR REPLACE TABLE operating.ren_solar_15min AS
            SELECT c.timestamp_utc, r.value_mw AS national_solar_mw,
                   CAST(r.source_date AS VARCHAR) AS source_date, r.source_index,
                   'PUBLIC_REN_NATIONAL_AGGREGATE' AS evidence_status
            FROM frozen.main.ren_dispatch r
            JOIN frozen.main.interval_calendar c
              ON r.source_date=c.local_date AND r.source_index=c.source_index
            WHERE r.series_name='Solar'""")
        con.execute("""CREATE OR REPLACE TABLE operating.ptd_pv_allocations AS
            WITH capacities AS (
                SELECT p.ptd_code, e.hv_bus,
                       CASE WHEN a.connected_generation_kw IS NOT NULL
                            THEN a.connected_generation_kw * ? / 1000
                            ELSE greatest(0, try_cast(p.capacity_kva_public AS DOUBLE))
                                 * ? / 1000 END AS capacity_proxy_mw,
                       a.connected_generation_kw AS connected_generation_kw_public,
                       CASE WHEN a.connected_generation_kw IS NOT NULL
                            THEN 'PUBLIC_ALL_TECH_GENERATION_PV_FRACTION_SCENARIO'
                            ELSE 'SUPPRESSED_GENERATION_PTD_KVA_PROXY' END AS capacity_evidence_status
                FROM candidate.ptd_node_candidates p
                JOIN equivalent.ptd_connections e USING (ptd_code)
                JOIN candidate.ptd_public_attributes a USING (ptd_code)
            )
            SELECT ptd_code, hv_bus, capacity_proxy_mw, connected_generation_kw_public,
                   capacity_proxy_mw / sum(capacity_proxy_mw) OVER () AS national_weight,
                   capacity_evidence_status AS evidence_status
            FROM capacities""", [args.public_generation_pv_fraction,
                                  args.ptd_capacity_fraction])
        con.execute("""CREATE OR REPLACE TABLE operating.utility_solar_allocations AS
            SELECT generator_id, bus_id, nameplate_mw,
                   nameplate_mw / sum(nameplate_mw) OVER () AS national_weight,
                   'MAPPED_PLANT_CAPACITY_WEIGHT_PROXY' AS evidence_status
            FROM equivalent.base_generators
            WHERE generation_source='solar' AND bus_id IS NOT NULL AND nameplate_mw>0""")
        con.execute("""CREATE OR REPLACE TABLE operating.solar_split_assumption AS
            SELECT ?::DOUBLE AS lv_fraction, (1-?::DOUBLE) AS utility_fraction,
                   ?::DOUBLE AS ptd_capacity_fraction,
                   ?::DOUBLE AS public_generation_pv_fraction,
                   'SCENARIO_ASSUMPTION_NOT_MEASURED_SPLIT' AS evidence_status""",
                    [args.lv_fraction, args.lv_fraction, args.ptd_capacity_fraction,
                     args.public_generation_pv_fraction])
        con.execute("""CREATE OR REPLACE VIEW operating.ptd_pv_15min AS
            SELECT a.ptd_code, s.timestamp_utc,
                   s.national_solar_mw * f.lv_fraction * a.national_weight AS p_mw,
                   0.0::DOUBLE AS q_mvar, a.hv_bus,
                   'REN_SOLAR_ALLOCATED_LV_SCENARIO' AS evidence_status
            FROM operating.ptd_pv_allocations a
            CROSS JOIN operating.solar_split_assumption f
            JOIN operating.ren_solar_15min s ON true
            WHERE a.national_weight>0""")
        con.execute("""CREATE OR REPLACE VIEW operating.utility_solar_15min AS
            SELECT a.generator_id, s.timestamp_utc,
                   s.national_solar_mw * f.utility_fraction * a.national_weight AS p_mw,
                   a.bus_id, 'REN_SOLAR_ALLOCATED_MAPPED_PLANTS' AS evidence_status
            FROM operating.utility_solar_allocations a
            CROSS JOIN operating.solar_split_assumption f
            JOIN operating.ren_solar_15min s ON true""")
        con.execute("""CREATE OR REPLACE VIEW operating.ptd_gross_load_15min AS
            SELECT l.ptd_code, l.timestamp_utc, l.p_mw AS net_load_proxy_mw,
                   v.p_mw AS embedded_pv_proxy_mw,
                   l.p_mw + coalesce(v.p_mw,0) AS gross_load_proxy_mw,
                   l.profile_data_status,
                   'GROSS_RECONSTRUCTION_SCENARIO_NOT_OBSERVED' AS evidence_status
            FROM operating.ptd_load_15min l
            LEFT JOIN operating.ptd_pv_15min v
              ON l.ptd_code=v.ptd_code AND l.timestamp_utc=v.timestamp_utc""")
        solar_rows, solar_intervals = con.execute(
            "SELECT count(*),count(DISTINCT timestamp_utc) FROM operating.ren_solar_15min").fetchone()
        ptd_count, ptd_weight, ptd_capacity = con.execute(
            "SELECT count(*),sum(national_weight),sum(capacity_proxy_mw) FROM operating.ptd_pv_allocations").fetchone()
        public_count, public_scenario_capacity, suppressed_count = con.execute("""
            SELECT count(*) FILTER (WHERE connected_generation_kw_public IS NOT NULL),
                   sum(capacity_proxy_mw) FILTER (WHERE connected_generation_kw_public IS NOT NULL),
                   count(*) FILTER (WHERE connected_generation_kw_public IS NULL)
            FROM operating.ptd_pv_allocations""").fetchone()
        plant_count, plant_weight = con.execute(
            "SELECT count(*),sum(national_weight) FROM operating.utility_solar_allocations").fetchone()
        max_solar = con.execute("SELECT max(national_solar_mw) FROM operating.ren_solar_15min").fetchone()[0]
        sample = con.execute("""WITH selected AS (
            SELECT timestamp_utc,national_solar_mw FROM operating.ren_solar_15min
            WHERE national_solar_mw>0 ORDER BY timestamp_utc LIMIT 3
        ) SELECT max(abs(s.national_solar_mw-
                     s.national_solar_mw*(f.lv_fraction*p.pw+f.utility_fraction*g.gw)))
          FROM selected s CROSS JOIN operating.solar_split_assumption f
          CROSS JOIN (SELECT sum(national_weight) pw FROM operating.ptd_pv_allocations) p
          CROSS JOIN (SELECT sum(national_weight) gw FROM operating.utility_solar_allocations) g""").fetchone()[0]
        checks = {"ren_solar_rows": solar_rows, "unique_utc_intervals": solar_intervals,
                  "ptd_count": ptd_count, "ptd_capacity_proxy_mw": float(ptd_capacity),
                  "ptd_public_generation_capacity_count":public_count,
                  "ptd_public_generation_pv_scenario_capacity_mw":float(public_scenario_capacity),
                  "ptd_suppressed_generation_capacity_count":suppressed_count,
                  "ptd_weight_sum": float(ptd_weight), "mapped_solar_plants": plant_count,
                  "plant_weight_sum": float(plant_weight), "national_solar_peak_mw": float(max_solar),
                  "sample_national_balance_max_mw": float(sample or 0),
                  "lv_fraction_assumed": args.lv_fraction,
                  "public_generation_pv_fraction_assumed":args.public_generation_pv_fraction,
                  "max_ptd_pv_output_to_capacity_ratio":
                      float(max_solar*args.lv_fraction/ptd_capacity)}
        errors = []
        if solar_rows != solar_intervals or solar_rows < 30000:
            errors.append("REN solar intervals missing or duplicate")
        if ptd_count != con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0]:
            errors.append("Some PTDs lack PV allocation records")
        if abs(ptd_weight-1)>1e-9 or abs(plant_weight-1)>1e-9 or sample>1e-8:
            errors.append("National solar allocation does not conserve dispatch")
        if checks["max_ptd_pv_output_to_capacity_ratio"]>1:
            errors.append("Assumed PTD PV fleet capacity cannot deliver assigned peak")
        con.execute("CREATE OR REPLACE TABLE audit.solar_der_validation (check_name VARCHAR, value DOUBLE)")
        con.executemany("INSERT INTO audit.solar_der_validation VALUES (?, ?)",
                        [(k,float(v)) for k,v in checks.items()])
        con.execute("DETACH frozen")
    report = {"result": "PASS" if not errors else "FAIL", "checks": checks,
              "errors": errors,
              "scope": "Synchronous national solar allocation with disclosed all-technology PTD generation capacities and suppressed-capacity proxies; factorized views",
              "limitations": ["Public PTD connected generation is not technology-specific and is not a PV measurement",
                              "70,427 PTD generation capacities are suppressed; their PV capacity is a PTD-kVA scenario",
                              "REN national solar total does not identify utility versus embedded PV share",
                              "PTD load is treated as net; reconstructed gross load is a scenario"]}
    path = args.database.parent / "solar_der_timeseries.validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
