#!/usr/bin/env python3
"""Audit nationwide LV cable-type sensitivity against voltage and ampacity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/lv_cable_sensitivity/validation.json"
MODE = "peak_proxy_peak_layout"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("""CREATE OR REPLACE VIEW study.lv_cable_sensitivity_summary AS
            SELECT s.scenario_mode, s.cable_designation,
                   max(c.installation_type) AS installation_type,
                   max(c.r_hot_ohm_per_km) AS r_hot_ohm_per_km,
                   max(c.permissible_current_a) AS permissible_current_a,
                   count(*) AS ptd_count,
                   count(*) FILTER (WHERE s.status='CONVERGED') AS converged_ptds,
                   count(*) FILTER (WHERE s.status<>'CONVERGED') AS failed_ptds,
                   count(*) FILTER (WHERE s.status='CONVERGED'
                                     AND s.min_phase_voltage_pu<0.9) AS under_voltage_ptds,
                   count(*) FILTER (WHERE s.status='CONVERGED'
                                     AND s.max_conductor_current_a>c.permissible_current_a)
                       AS ampacity_exceedance_ptds,
                   count(*) FILTER (WHERE s.status='CONVERGED'
                                     AND s.source_kva>s.transformer_capacity_kva)
                       AS transformer_overload_ptds,
                   min(s.min_phase_voltage_pu) AS min_converged_voltage_pu,
                   max(s.max_conductor_current_a) AS max_conductor_current_a
            FROM study.lv_fourwire_cable_sensitivity s
            JOIN parameter.lv_cable_catalog c ON s.cable_designation=c.designation
            GROUP BY 1,2""")
        rows = con.execute("""SELECT cable_designation,ptd_count,converged_ptds,
            failed_ptds,under_voltage_ptds,ampacity_exceedance_ptds,
            transformer_overload_ptds,min_converged_voltage_pu
            FROM study.lv_cable_sensitivity_summary WHERE scenario_mode=?
            ORDER BY cable_designation""", [MODE]).fetchall()
        n_ptd = con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0]
        expected = con.execute("""SELECT count(*) FROM parameter.lv_cable_catalog
            WHERE phase_conductor_count=3 AND phase_section_mm2=neutral_section_mm2
              AND (designation LIKE 'LXS 4 x %' OR designation LIKE 'LSXAV 4x%')""").fetchone()[0]
        duplicate_keys = con.execute("""SELECT count(*) FROM (
            SELECT cable_designation,scenario_mode,ptd_code, count(*) AS n
            FROM study.lv_fourwire_cable_sensitivity WHERE scenario_mode=?
            GROUP BY 1,2,3 HAVING count(*)<>1)""", [MODE]).fetchone()[0]
        unknown_types = con.execute("""SELECT count(*) FROM study.lv_fourwire_cable_sensitivity s
            LEFT JOIN parameter.lv_cable_catalog c ON s.cable_designation=c.designation
            WHERE s.scenario_mode=? AND c.designation IS NULL""", [MODE]).fetchone()[0]
    summary = [dict(zip(("designation", "ptds", "converged", "failed", "under_voltage",
                         "ampacity_exceeded_among_converged", "transformer_overloaded_among_converged",
                         "min_converged_voltage_pu"), row)) for row in rows]
    errors = []
    if len(rows) != expected or any(r[1] != n_ptd or r[2]+r[3] != n_ptd for r in rows):
        errors.append("Catalog type coverage or PTD accounting failed")
    if duplicate_keys or unknown_types:
        errors.append("Duplicate PTD result or unknown catalog type")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "scenario_mode": MODE,
        "national_ptds": n_ptd,
        "expected_cable_types": expected,
        "type_results": summary,
        "duplicate_keys": duplicate_keys,
        "unknown_types": unknown_types,
        "scope": "Peak-proxy LV load, zero PV and fixed synthetic parallel-feeder layout across published equal-section 4-core cable types",
        "limitations": [
            "No PTD line has a verified installed cable type or feeder geometry",
            "Mutual impedances, phase shares, source voltage and grounding remain proxy values",
            "Ampacity counts exclude nonconverged PTDs; failure is reported separately",
            "A passing numerical case does not establish real-network voltage or thermal compliance",
        ],
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
