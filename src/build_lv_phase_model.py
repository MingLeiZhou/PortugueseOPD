#!/usr/bin/env python3
"""Create an explicit synthetic ABCN low-voltage model in the staging DB.

Phase allocations and impedance values are engineering scenarios, not
observations of Portuguese customer phases or cable construction.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "output/all_voltage/all_voltage_staging.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=STAGING)
    args = parser.parse_args()
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS phase")
        con.execute("""CREATE OR REPLACE TABLE phase.lv_line_models AS
            SELECT branch_id AS line_id, from_bus, to_bus, 'ABCN' AS phases,
                   'LV_4WIRE_PROXY_V1' AS impedance_archetype_id,
                   0.05::DOUBLE AS length_km_proxy,
                   'ENGINEERING_PROXY_UNCALIBRATED' AS parameter_status
            FROM equivalent.branches
            WHERE evidence_status='SYNTHETIC_LV_TOPOLOGY_AND_IMPEDANCE'""")
        con.execute("""CREATE OR REPLACE TABLE phase.lv_load_shares AS
            WITH phases(phase, phase_index) AS (VALUES ('A',0),('B',1),('C',2)),
            keyed AS (
              SELECT load_id, source_ptd_code, phase, phase_index,
                     hash(source_ptd_code)%3 AS rotation
              FROM equivalent.lv_loads CROSS JOIN phases
            )
            SELECT load_id, source_ptd_code AS ptd_code, phase,
                   0.5 * CASE (phase_index+rotation)%3
                     WHEN 0 THEN 0.37 WHEN 1 THEN 0.34 ELSE 0.29 END
                     AS ptd_total_share,
                   'DETERMINISTIC_SYNTHETIC_PHASE_ALLOCATION' AS evidence_status
            FROM keyed""")
        con.execute("""CREATE OR REPLACE TABLE phase.ptd_neutral_grounding AS
            SELECT ptd_code, 'TN_PROXY' AS grounding_archetype,
                   0.0::DOUBLE AS source_neutral_bond_ohm,
                   'ASSUMED_NOT_PUBLIC_PTD_WIRING' AS evidence_status
            FROM equivalent.ptd_connections""")
        con.execute("""CREATE OR REPLACE TABLE phase.impedance_matrix (
            archetype_id VARCHAR, from_conductor VARCHAR, to_conductor VARCHAR,
            r_ohm_per_km DOUBLE, x_ohm_per_km DOUBLE, evidence_status VARCHAR)""")
        conductors = "ABCN"
        matrix = []
        for a in conductors:
            for b in conductors:
                r, x = (0.5, 0.1) if a == b else (0.05, 0.02)
                matrix.append(("LV_4WIRE_PROXY_V1", a, b, r, x,
                               "ENGINEERING_PROXY_UNCALIBRATED"))
        con.executemany("INSERT INTO phase.impedance_matrix VALUES (?, ?, ?, ?, ?, ?)", matrix)
        con.execute("""CREATE OR REPLACE VIEW operating.lv_phase_load_15min AS
            SELECT p.ptd_code, p.timestamp_utc, s.load_id, s.phase,
                   p.p_mw*s.ptd_total_share AS p_mw,
                   p.q_mvar*s.ptd_total_share AS q_mvar,
                   p.allocation_status, p.profile_data_status,
                   s.evidence_status AS phase_status
            FROM operating.ptd_load_15min p
            JOIN phase.lv_load_shares s ON p.ptd_code=s.ptd_code""")
        share_errors = con.execute("""SELECT count(*) FROM (
            SELECT ptd_code FROM phase.lv_load_shares GROUP BY 1
            HAVING abs(sum(ptd_total_share)-1.0)>1e-10)""").fetchone()[0]
        orphan_phase_loads = con.execute("""SELECT count(*) FROM phase.lv_load_shares s
            LEFT JOIN equivalent.lv_loads l ON s.load_id=l.load_id WHERE l.load_id IS NULL""").fetchone()[0]
        sample_balance = con.execute("""WITH one AS (
            SELECT p_mw,q_mvar FROM operating.ptd_load_15min
            WHERE ptd_code='1311D2033100'
              AND timestamp_utc='2025-07-22T11:45:00+00:00'
        ), phases AS (
            SELECT sum(p_mw) p_mw,sum(q_mvar) q_mvar
            FROM operating.lv_phase_load_15min
            WHERE ptd_code='1311D2033100'
              AND timestamp_utc='2025-07-22T11:45:00+00:00'
        ) SELECT abs(o.p_mw-p.p_mw),abs(o.q_mvar-p.q_mvar)
          FROM one o CROSS JOIN phases p""").fetchone()
        checks = {
            "lv_lines_with_abcn_model": con.execute("SELECT count(*) FROM phase.lv_line_models").fetchone()[0],
            "lv_phase_load_shares": con.execute("SELECT count(*) FROM phase.lv_load_shares").fetchone()[0],
            "ptd_neutral_grounding_rows": con.execute("SELECT count(*) FROM phase.ptd_neutral_grounding").fetchone()[0],
            "impedance_matrix_rows": con.execute("SELECT count(*) FROM phase.impedance_matrix").fetchone()[0],
            "share_sum_errors": share_errors,
            "orphan_phase_loads": orphan_phase_loads,
            "sample_p_balance_mw": float(sample_balance[0]) if sample_balance else None,
            "sample_q_balance_mvar": float(sample_balance[1]) if sample_balance else None,
        }
        con.execute("CREATE OR REPLACE TABLE audit.phase_validation (check_name VARCHAR, value DOUBLE)")
        con.executemany("INSERT INTO audit.phase_validation VALUES (?, ?)",
                        [(k, float(v)) for k, v in checks.items() if v is not None])
    resistive = np.full((4, 4), 0.05)
    reactive = np.full((4, 4), 0.02)
    np.fill_diagonal(resistive, 0.5)
    np.fill_diagonal(reactive, 0.1)
    checks["min_r_eigenvalue"] = float(np.linalg.eigvalsh(resistive).min())
    checks["min_x_eigenvalue"] = float(np.linalg.eigvalsh(reactive).min())
    errors = []
    if checks["lv_phase_load_shares"] != 3 * 2 * checks["ptd_neutral_grounding_rows"]:
        errors.append("Expected six phase allocations per PTD")
    if checks["share_sum_errors"] or checks["orphan_phase_loads"]:
        errors.append("Phase load foreign key or sum failure")
    if (checks["sample_p_balance_mw"] is None or checks["sample_p_balance_mw"] > 1e-10
            or checks["sample_q_balance_mvar"] > 1e-10):
        errors.append("Sample phase P/Q does not reconcile with PTD P/Q")
    if checks["min_r_eigenvalue"] <= 0 or checks["min_x_eigenvalue"] <= 0:
        errors.append("Proxy impedance matrix is not positive definite")
    report = {"result": "PASS" if not errors else "FAIL", "checks": checks, "errors": errors,
              "scope": "Synthetic four-conductor ABCN schema and factorized phase loads; numeric impedance and phase assignment not calibrated"}
    path = args.database.parent / "lv_phase_model.validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
