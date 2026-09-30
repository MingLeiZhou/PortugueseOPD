#!/usr/bin/env python3
"""Build a nationwide, electrically constrained synthetic LV feeder design.

The public PTD export, E-REDES standard cable catalogue, public pole density,
and OSM LV proximity are combined without promoting geographic proximity to an
observed electrical connection.  Every installed cable assignment and feeder
count remains an explicit simulation scenario.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/lv_feeder_design_scenario.validation.json"
VOLTAGE_KV = 0.4
DESIGN_FACTOR = 1.25
SECTION_COUNT = 2
SECTION_LENGTH_KM = 0.05
SCENARIO_ID = "LV_FEEDER_DESIGN_PEAK_V1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()

    with duckdb.connect(str(args.database), read_only=True) as con:
        ptd_rows = con.execute("""
            SELECT p.ptd_code,p.peak_load_proxy_mva,
                   coalesce(f.feeder_count,1) AS voltage_feeder_count,
                   a.construction_type,e.evidence_class,
                   e.model_topology_status,e.electrical_connection_status,
                   e.nearest_pole_distance_m,e.pole_count_50m,e.pole_count_100m,
                   e.pole_count_200m
            FROM equivalent.ptd_connections p
            JOIN candidate.ptd_public_attributes a USING(ptd_code)
            JOIN candidate.ptd_lv_topology_evidence e USING(ptd_code)
            LEFT JOIN phase.lv_feeder_layout_peak_scenario f USING(ptd_code)
            ORDER BY p.ptd_code
        """).fetchall()
        cables = con.execute("""
            SELECT installation_type,designation,phase_section_mm2,
                   neutral_section_mm2,r_hot_ohm_per_km,x_ohm_per_km,
                   permissible_current_a,source_sha256,evidence_status
            FROM parameter.lv_cable_catalog
            WHERE phase_conductor_count=3
            ORDER BY installation_type,permissible_current_a,designation
        """).fetchall()

    by_installation: dict[str, list[tuple]] = {}
    for cable in cables:
        by_installation.setdefault(cable[0], []).append(cable)
    required_types = {"aerial_bundle", "underground_armoured"}
    if not ptd_rows or not required_types.issubset(by_installation):
        parser.error("PTD evidence, feeder screen, or three-phase cable catalogue missing")

    rows = []
    option_rows = []
    for (
        code, peak_mva, voltage_count, construction, evidence_class,
        topology_status, connection_status, nearest_pole_m,
        poles_50m, poles_100m, poles_200m,
    ) in ptd_rows:
        peak_mva = max(float(peak_mva or 0.0), 0.0)
        voltage_count = max(int(voltage_count or 1), 1)
        is_aerial_ptd = str(construction or "").strip().lower().startswith("aéreo")
        installation = "aerial_bundle" if is_aerial_ptd else "underground_armoured"
        installation_basis = (
            "PUBLIC_PTD_CONSTRUCTION_TYPE_AS_FEEDER_PROXY"
            if construction else "NO_PTD_CONSTRUCTION_GENERIC_UNDERGROUND_SCENARIO"
        )
        max_ampacity = max(c[6] for c in by_installation[installation])
        total_design_current = peak_mva * 1000.0 * DESIGN_FACTOR / (
            math.sqrt(3.0) * VOLTAGE_KV
        )
        ampacity_count = max(1, math.ceil(total_design_current / max_ampacity - 1e-12))
        feeder_count = max(voltage_count, ampacity_count)
        design_current = total_design_current / feeder_count
        eligible = [c for c in by_installation[installation] if c[6] + 1e-12 >= design_current]
        if not eligible:
            raise RuntimeError(f"No adequate {installation} cable for {code}: {design_current} A")
        selected = eligible[0]
        cable_status = (
            "PUBLIC_STANDARD_TYPE_SCENARIO_SELECTED_BY_PEAK_AMPACITY_125PCT_"
            "NOT_INSTALLED_ASSET"
        )
        rows.append((
            SCENARIO_ID, code, feeder_count, voltage_count, ampacity_count,
            SECTION_COUNT, SECTION_LENGTH_KM, "ABCN", installation,
            selected[1], selected[2], selected[3], selected[4], selected[5],
            selected[6], design_current,
            selected[6] / design_current if design_current > 0 else None,
            peak_mva * 1000.0, DESIGN_FACTOR, construction,
            installation_basis, cable_status, evidence_class,
            topology_status, connection_status, nearest_pole_m,
            poles_50m, poles_100m, poles_200m,
            "SYNTHETIC_RADIAL_TWO_SECTION_EQUAL_PARALLEL_FEEDERS",
        ))
        for alternative in by_installation[installation]:
            option_rows.append((
                SCENARIO_ID, code, installation, alternative[1],
                alternative[6], design_current,
                alternative[6] + 1e-12 >= design_current,
                alternative[4], alternative[5], alternative[7],
                "PUBLIC_STANDARD_CATALOGUE_OPTION_NOT_ASSET_ASSIGNMENT",
            ))

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS phase")
        con.execute("""CREATE OR REPLACE TABLE phase.lv_feeder_design_scenario (
            scenario_id VARCHAR,ptd_code VARCHAR,feeder_count INTEGER,
            voltage_required_feeder_count INTEGER,ampacity_required_feeder_count INTEGER,
            section_count INTEGER,section_length_km DOUBLE,phases VARCHAR,
            installation_scenario VARCHAR,cable_designation VARCHAR,
            phase_section_mm2 INTEGER,neutral_section_mm2 INTEGER,
            r_hot_ohm_per_km DOUBLE,x_ohm_per_km DOUBLE,
            permissible_current_a DOUBLE,design_current_a_per_feeder DOUBLE,
            ampacity_margin_ratio DOUBLE,peak_load_proxy_kva DOUBLE,
            design_factor DOUBLE,ptd_construction_type VARCHAR,
            installation_basis VARCHAR,cable_assignment_status VARCHAR,
            topology_evidence_class VARCHAR,model_topology_status VARCHAR,
            electrical_connection_status VARCHAR,nearest_pole_distance_m DOUBLE,
            pole_count_50m INTEGER,pole_count_100m INTEGER,pole_count_200m INTEGER,
            topology_scenario_status VARCHAR)""")
        con.executemany(
            "INSERT INTO phase.lv_feeder_design_scenario VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        con.execute("""CREATE OR REPLACE TABLE phase.lv_feeder_cable_options_scenario (
            scenario_id VARCHAR,ptd_code VARCHAR,installation_scenario VARCHAR,
            cable_designation VARCHAR,permissible_current_a DOUBLE,
            design_current_a_per_feeder DOUBLE,ampacity_adequate BOOLEAN,
            r_hot_ohm_per_km DOUBLE,x_ohm_per_km DOUBLE,
            source_sha256 VARCHAR,evidence_status VARCHAR)""")
        con.executemany(
            "INSERT INTO phase.lv_feeder_cable_options_scenario VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            option_rows,
        )
        checks = {
            "national_ptds": con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0],
            "designed_ptds": con.execute("SELECT count(*) FROM phase.lv_feeder_design_scenario").fetchone()[0],
            "distinct_designed_ptds": con.execute("SELECT count(DISTINCT ptd_code) FROM phase.lv_feeder_design_scenario").fetchone()[0],
            "feeder_instances": con.execute("SELECT sum(feeder_count) FROM phase.lv_feeder_design_scenario").fetchone()[0],
            "ptds_multiple_feeders": con.execute("SELECT count(*) FROM phase.lv_feeder_design_scenario WHERE feeder_count>1").fetchone()[0],
            "max_feeder_count": con.execute("SELECT max(feeder_count) FROM phase.lv_feeder_design_scenario").fetchone()[0],
            "aerial_ptd_scenarios": con.execute("SELECT count(*) FROM phase.lv_feeder_design_scenario WHERE installation_scenario='aerial_bundle'").fetchone()[0],
            "underground_ptd_scenarios": con.execute("SELECT count(*) FROM phase.lv_feeder_design_scenario WHERE installation_scenario='underground_armoured'").fetchone()[0],
            "ampacity_violations": con.execute("""SELECT count(*) FROM phase.lv_feeder_design_scenario
                WHERE design_current_a_per_feeder>permissible_current_a+1e-9""").fetchone()[0],
            "voltage_feeder_count_violations": con.execute("""SELECT count(*) FROM phase.lv_feeder_design_scenario
                WHERE feeder_count<voltage_required_feeder_count""").fetchone()[0],
            "ampacity_feeder_count_violations": con.execute("""SELECT count(*) FROM phase.lv_feeder_design_scenario
                WHERE feeder_count<ampacity_required_feeder_count""").fetchone()[0],
            "catalog_reference_errors": con.execute("""SELECT count(*) FROM phase.lv_feeder_design_scenario d
                LEFT JOIN parameter.lv_cable_catalog c
                  ON d.installation_scenario=c.installation_type
                 AND d.cable_designation=c.designation
                WHERE c.designation IS NULL""").fetchone()[0],
            "option_rows": con.execute("SELECT count(*) FROM phase.lv_feeder_cable_options_scenario").fetchone()[0],
            "ptds_without_adequate_catalog_option": con.execute("""SELECT count(*) FROM (
                SELECT ptd_code FROM phase.lv_feeder_cable_options_scenario
                GROUP BY 1 HAVING NOT bool_or(ampacity_adequate))""").fetchone()[0],
            "observed_installed_cable_assignments": con.execute("""SELECT count(*) FROM phase.lv_feeder_design_scenario
                WHERE cable_assignment_status NOT LIKE '%NOT_INSTALLED_ASSET%'""").fetchone()[0],
            "verified_electrical_connections": con.execute("""SELECT count(*) FROM phase.lv_feeder_design_scenario
                WHERE electrical_connection_status<>'NO_VERIFIED_LV_TERMINAL_OR_FEEDER'""").fetchone()[0],
        }

    errors = []
    if checks["national_ptds"] != checks["designed_ptds"] or checks["designed_ptds"] != checks["distinct_designed_ptds"]:
        errors.append("National PTD design coverage or uniqueness failed")
    for key in (
        "ampacity_violations", "voltage_feeder_count_violations",
        "ampacity_feeder_count_violations", "catalog_reference_errors",
        "ptds_without_adequate_catalog_option", "observed_installed_cable_assignments",
    ):
        if checks[key]:
            errors.append(f"Nonzero {key}: {checks[key]}")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "scenario_id": SCENARIO_ID,
        "scope": "Nationwide synthetic LV feeder count and standard-cable assignment constrained by peak voltage and ampacity screens",
        "assumptions": {
            "design_factor": DESIGN_FACTOR,
            "nominal_voltage_kv": VOLTAGE_KV,
            "section_count": SECTION_COUNT,
            "section_length_km": SECTION_LENGTH_KM,
            "installation_scenario": "Aerial PTD construction selects aerial bundle; all other PTD construction selects underground armoured cable",
        },
        "limitations": [
            "PTD construction type is only a feeder-installation proxy and does not identify the installed cable",
            "Public pole and OSM proximity remain geographic evidence, not verified electrical edges",
            "Two equal radial sections, equal parallel feeders, and 50-m section length are simulation assumptions",
            "Cable selection uses public standard types and a peak-load proxy; it is not an asset inventory",
            "Ampacity screening does not include soil, ambient temperature, grouping, or detailed protection constraints",
        ],
        "errors": errors,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
