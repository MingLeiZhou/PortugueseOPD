#!/usr/bin/env python3
"""Import published E-REDES LV cable types without assigning them to assets.

The source lists standardized 0.6/1 kV cable characteristics in its Annex C,
page 27. It does not identify the installed cable of any OSM segment or PTD,
nor provide the complete four-conductor mutual impedance matrix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
SOURCE = ROOT / "output/feasibility/DIT-C14-100_Ed9_2025.pdf"
SOURCE_URL = "https://www.e-redes.pt/sites/eredes/files/2025-12/DIT-C14-100%20Ed9%201.pdf"

# designation, phase conductors, neutral section (mm2), R20, Rmax, X, Z,
# permissible current Iz, and published fuse/service current In=Is.
# Values are transcribed from tables C1/C2, printed page 27 of the source.
ROWS = [
    ("C1", "aerial_bundle", "LXS 2 x 16", 1, 16, 16, 50, 1.910, 2.141, 0.1, 2.025, 85, 63),
    ("C1", "aerial_bundle", "LXS 4 x 16", 3, 16, 16, 50, 1.910, 2.141, 0.1, 2.025, 75, 63),
    ("C1", "aerial_bundle", "LXS 4 x 25", 3, 25, 25, 50, 1.200, 1.345, 0.1, 1.286, 100, 80),
    ("C1", "aerial_bundle", "LXS 4 x 50", 3, 50, 50, 50, 0.641, 0.718, 0.1, 0.704, 150, 125),
    ("C1", "aerial_bundle", "LXS 4 x 70", 3, 70, 70, 50, 0.443, 0.497, 0.1, 0.498, 190, 160),
    ("C1", "aerial_bundle", "LXS 4 x 95", 3, 95, 95, 50, 0.320, 0.359, 0.1, 0.370, 230, 200),
    ("C2", "underground_armoured", "LSXAV 2x16", 1, 16, 16, 90, 1.910, 2.449, 0.1, 2.311, 114, 100),
    ("C2", "underground_armoured", "LSXAV 4x16", 3, 16, 16, 90, 1.910, 2.449, 0.1, 2.311, 96, 80),
    ("C2", "underground_armoured", "LSXAV 4x35", 3, 35, 35, 90, 0.868, 1.113, 0.1, 1.070, 147, 125),
    ("C2", "underground_armoured", "LSXAV 4x95", 3, 95, 95, 90, 0.320, 0.410, 0.1, 0.418, 258, 200),
    ("C2", "underground_armoured", "LXAV 3x185+95", 3, 185, 95, 90, 0.164, 0.210, 0.1, 0.232, 375, 315),
    ("C2", "underground_armoured", "LXAV 3x240+120", 3, 240, 120, 90, 0.125, 0.160, 0.1, 0.186, 435, 315),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()
    digest = hashlib.sha256(args.source.read_bytes()).hexdigest()
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS parameter")
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("""CREATE OR REPLACE TABLE parameter.lv_cable_catalog (
            source_table VARCHAR, installation_type VARCHAR, designation VARCHAR,
            phase_conductor_count INTEGER, phase_section_mm2 INTEGER,
            neutral_section_mm2 INTEGER, hot_temperature_c INTEGER,
            r20_ohm_per_km DOUBLE, r_hot_ohm_per_km DOUBLE,
            x_ohm_per_km DOUBLE, z_published_ohm_per_km DOUBLE,
            permissible_current_a DOUBLE, fuse_service_current_a DOUBLE,
            rated_voltage_v DOUBLE, source_page_printed INTEGER,
            source_url VARCHAR, source_sha256 VARCHAR, evidence_status VARCHAR
        )""")
        con.executemany(
            "INSERT INTO parameter.lv_cable_catalog VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1000,27,?,?,?)",
            [(*row, SOURCE_URL, digest, "PUBLIC_STANDARD_TYPE_NOT_ASSET_ASSIGNMENT") for row in ROWS],
        )
        # Compare the existing phase-diagonal proxy with published cable types.
        # This is sensitivity context, not a mapping of any LV line to a cable.
        con.execute("""CREATE OR REPLACE VIEW study.lv_proxy_cable_catalog_comparison AS
            SELECT c.designation, c.installation_type, c.r_hot_ohm_per_km,
                   c.x_ohm_per_km, c.permissible_current_a,
                   p.r_ohm_per_km AS existing_proxy_r_ohm_per_km,
                   p.x_ohm_per_km AS existing_proxy_x_ohm_per_km,
                   p.r_ohm_per_km/c.r_hot_ohm_per_km AS proxy_to_catalog_r_ratio,
                   'COMPARISON_ONLY_NO_LINE_TYPE_ASSIGNMENT' AS evidence_status
            FROM parameter.lv_cable_catalog c
            JOIN phase.impedance_matrix p
              ON p.archetype_id='LV_4WIRE_PROXY_V1'
             AND p.from_conductor='A' AND p.to_conductor='A'
            WHERE c.phase_conductor_count=3""")
        checks = {
            "catalog_rows": con.execute("SELECT count(*) FROM parameter.lv_cable_catalog").fetchone()[0],
            "distinct_designations": con.execute("SELECT count(DISTINCT designation) FROM parameter.lv_cable_catalog").fetchone()[0],
            "c1_rows": con.execute("SELECT count(*) FROM parameter.lv_cable_catalog WHERE source_table='C1'").fetchone()[0],
            "c2_rows": con.execute("SELECT count(*) FROM parameter.lv_cable_catalog WHERE source_table='C2'").fetchone()[0],
            "invalid_physics_rows": con.execute("""SELECT count(*) FROM parameter.lv_cable_catalog
                WHERE NOT (r20_ohm_per_km>0 AND r_hot_ohm_per_km>r20_ohm_per_km
                  AND x_ohm_per_km>0 AND z_published_ohm_per_km>0
                  AND permissible_current_a>=fuse_service_current_a
                  AND fuse_service_current_a>0 AND rated_voltage_v=1000)""").fetchone()[0],
            "comparison_rows": con.execute("SELECT count(*) FROM study.lv_proxy_cable_catalog_comparison").fetchone()[0],
            "lv_lines_with_verified_cable_assignment": 0,
            "catalog_types_with_published_abcn_mutual_matrix": 0,
        }
    errors = []
    if checks["catalog_rows"] != len(ROWS) or checks["distinct_designations"] != len(ROWS):
        errors.append("Catalog row count or designation uniqueness failed")
    if checks["c1_rows"] != 6 or checks["c2_rows"] != 6 or checks["invalid_physics_rows"]:
        errors.append("Source table counts or physical range checks failed")
    if checks["comparison_rows"] != 10:
        errors.append("Four-conductor catalog comparison is unavailable")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "source_sha256": digest,
        "source_url": SOURCE_URL,
        "source_page_printed": 27,
        "scope": "Published standard LV cable types and comparison to existing uncalibrated phase-diagonal proxy",
        "limitations": [
            "No OSM segment or PTD has a verified installed cable designation",
            "Published X and Z are scalar values, not a full ABCN mutual impedance matrix",
            "The existing LV line model retains its engineering-proxy matrix and line lengths",
        ],
        "errors": errors,
    }
    path = args.database.parent / "lv_cable_catalog.validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
