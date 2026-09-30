#!/usr/bin/env python3
"""Batch PF and max/min 3ph, 2ph and 1ph-ground fault validation for every MV-root slice.

The nationwide equivalent is too large for a single dense validation case.
Each persisted 60-kV/MV bridge defines an electrically bounded slice containing
one MV root and all of its PTD/LV branches.  This runner executes the existing
case solver for every root, keeps per-root artifacts, and imports nationwide
result tables and a compact root summary into DuckDB.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/equivalent_mv_root_batch"
SOLVER = ROOT / "src/validate_equivalent_island_powerflow.py"
TIMESTAMP = "2025-07-22T11:45:00+00:00"
FAULT_MODEL_VERSION = "IEC60909_3PH_2PH_1PH_GROUND_PROXY_V1"


def safe_name(root_id: str) -> str:
    return root_id.replace(":", "_").replace("/", "_")


def run_case(task: tuple[str, Path, Path, str, bool]) -> dict:
    root_id, database, output_root, timestamp, reuse_existing = task
    case_dir = output_root / "roots" / safe_name(root_id)
    case_dir.mkdir(parents=True, exist_ok=True)
    report_path = case_dir / "validation.json"
    if reuse_existing and report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            if (report.get("timestamp_utc") == timestamp
                    and report.get("fault_model_version") == FAULT_MODEL_VERSION):
                return {
                    "mv_root_bus": root_id,
                    "returncode": int(bool(report.get("errors"))),
                    "report": report,
                    "stdout_tail": "REUSED_EXISTING_CASE",
                    "stderr_tail": "",
                }
        except json.JSONDecodeError:
            pass
    cmd = [
        sys.executable, str(SOLVER), "--database", str(database),
        "--mv-root-bus", root_id, "--timestamp-utc", timestamp,
        "--output", str(case_dir), "--no-persist-results",
    ]
    if os.environ.get("MV_URBAN_CABLE_DIR"):
        cmd += ["--mv-urban-cable", os.environ["MV_URBAN_CABLE_DIR"]]
    env = dict(os.environ)
    mpl_dir = output_root / ".mplconfig"
    mpl_dir.mkdir(parents=True, exist_ok=True)
    env["MPLCONFIGDIR"] = str(mpl_dir)
    proc = subprocess.run(cmd, text=True, capture_output=True, env=env)
    report = None
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "mv_root_bus": root_id,
        "returncode": proc.returncode,
        "report": report,
        "stdout_tail": proc.stdout[-2000:],
        "stderr_tail": proc.stderr[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--timestamp-utc", default=TIMESTAMP)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--mv-urban-cable", type=Path,
                        help="Pass MV-URBAN-CABLE-V1 to every root solve (replaces star feeders of fallback PTDs)")
    parser.add_argument("--per-root-peak", action="store_true",
                        help="Use each root's full-calendar peak timestamp from the transformer design scenario")
    parser.add_argument("--reuse-existing", action="store_true",
                        help="Reuse a case directory when validation.json has the requested timestamp")
    parser.add_argument("--design-screening", action="store_true",
                        help="Treat solved operating-constraint failures as diagnostic input for a later design refinement")
    parser.add_argument("--limit", type=int, default=0,
                        help="Run only the first N roots for a smoke test; 0 means all")
    args = parser.parse_args()
    if args.workers < 1 or args.limit < 0:
        parser.error("--workers must be positive and --limit must be non-negative")
    if args.mv_urban_cable:
        os.environ["MV_URBAN_CABLE_DIR"] = str(args.mv_urban_cable.resolve())
    if args.per_root_peak and args.output == OUT:
        args.output = OUT.parent / "equivalent_mv_root_peak_batch"
    args.output.mkdir(parents=True, exist_ok=True)

    with duckdb.connect(str(args.database), read_only=True) as con:
        root_rows = con.execute("""SELECT p.mv_root_bus,
                   CASE WHEN ? THEN d.peak_timestamp_utc ELSE ? END AS case_timestamp_utc
            FROM parameter.mv_root_transformer_parameters p
            LEFT JOIN parameter.mv_root_transformer_design_scenario d USING(mv_root_bus)
            WHERE EXISTS (SELECT 1 FROM equivalent.ptd_connections c
                          WHERE c.mv_root_bus=p.mv_root_bus)
            ORDER BY p.mv_root_bus""", [args.per_root_peak,args.timestamp_utc]).fetchall()
        expected_ptds = con.execute("""SELECT count(DISTINCT ptd_code)
            FROM equivalent.ptd_connections
            WHERE mv_root_bus IN (SELECT mv_root_bus FROM parameter.mv_root_transformer_parameters)""").fetchone()[0]
    if args.limit:
        root_rows = root_rows[:args.limit]

    tasks = [(root_id, args.database, args.output, timestamp,args.reuse_existing)
             for root_id,timestamp in root_rows]
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_case, task): task[0] for task in tasks}
        for completed, future in enumerate(concurrent.futures.as_completed(futures), 1):
            result = future.result()
            results.append(result)
            if completed % 25 == 0 or completed == len(tasks):
                ok = sum(r["returncode"] == 0 and r["report"] is not None for r in results)
                print(f"completed={completed}/{len(tasks)} reports={ok}", flush=True)

    rows = []
    failures = []
    for result in sorted(results, key=lambda x: x["mv_root_bus"]):
        report = result["report"]
        if not report:
            failures.append({
                "mv_root_bus": result["mv_root_bus"],
                "returncode": result["returncode"],
                "stderr_tail": result["stderr_tail"],
            })
            continue
        checks = report.get("checks", {})
        row = {
            "mv_root_bus": result["mv_root_bus"],
            "timestamp_utc": report.get("timestamp_utc"),
            "result": report.get("result"),
            "returncode": result["returncode"],
            "buses": checks.get("buses"),
            "branches": checks.get("branches"),
            "lines": checks.get("lines"),
            "transformers": checks.get("transformers"),
            "ptds": checks.get("ptds"),
            "mv_load_resources": checks.get("mv_load_resources"),
            "mv_root_transformer_base_sn_mva": checks.get("mv_root_transformer_base_sn_mva"),
            "mv_root_transformer_solver_sn_mva": checks.get("mv_root_transformer_solver_sn_mva"),
            "mv_root_transformer_design_status": checks.get("mv_root_transformer_design_status"),
            "lv_voltage_min_pu": checks.get("lv_voltage_min_pu"),
            "lv_voltage_max_pu": checks.get("lv_voltage_max_pu"),
            "mv_voltage_min_pu": checks.get("mv_voltage_min_pu"),
            "mv_voltage_max_pu": checks.get("mv_voltage_max_pu"),
            "max_line_loading_percent": checks.get("max_line_loading_percent"),
            "max_transformer_loading_percent": checks.get("max_transformer_loading_percent"),
            "voltage_violation_bus_count": checks.get("voltage_violation_bus_count"),
            "line_overload_count": checks.get("line_overload_count"),
            "transformer_overload_count": checks.get("transformer_overload_count"),
            "zero_public_capacity_ptd_transformer_proxy_count": checks.get("zero_public_capacity_ptd_transformer_proxy_count"),
            "active_power_balance_error_mw": checks.get("active_power_balance_error_mw"),
            "active_power_balance_relative": checks.get("active_power_balance_relative"),
            "fault_rows": checks.get("fault_rows"),
            "three_phase_fault_rows": checks.get("three_phase_fault_rows"),
            "two_phase_fault_rows": checks.get("two_phase_fault_rows"),
            "single_phase_ground_fault_rows": checks.get("single_phase_ground_fault_rows"),
            "three_phase_min_fault_current_ka": checks.get("three_phase_min_fault_current_ka"),
            "three_phase_max_fault_current_ka": checks.get("three_phase_max_fault_current_ka"),
            "two_phase_min_fault_current_ka": checks.get("two_phase_min_fault_current_ka"),
            "two_phase_max_fault_current_ka": checks.get("two_phase_max_fault_current_ka"),
            "single_phase_ground_min_fault_current_ka": checks.get("single_phase_ground_min_fault_current_ka"),
            "single_phase_ground_max_fault_current_ka": checks.get("single_phase_ground_max_fault_current_ka"),
            "min_fault_current_ka": checks.get("min_fault_current_ka"),
            "max_fault_current_ka": checks.get("max_fault_current_ka"),
            "error_count": len(report.get("errors", [])),
            "errors_json": json.dumps(report.get("errors", []), ensure_ascii=False),
        }
        rows.append(row)
        if result["returncode"] != 0 or report.get("errors"):
            failures.append({
                "mv_root_bus": result["mv_root_bus"],
                "returncode": result["returncode"],
                "errors": report.get("errors", []),
                "stderr_tail": result["stderr_tail"],
            })

    summary_csv = args.output / "root_summary.csv"
    if rows:
        with summary_csv.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    full_scope = args.limit == 0
    totals = {
        "requested_roots": len(root_rows),
        "completed_reports": len(rows),
        "failed_roots": len(failures),
        "ptds_covered": sum(int(r["ptds"] or 0) for r in rows),
        "expected_nationwide_ptds": int(expected_ptds),
        "buses_solved": sum(int(r["buses"] or 0) for r in rows),
        "branches_solved": sum(int(r["branches"] or 0) for r in rows),
        "fault_rows": sum(int(r["fault_rows"] or 0) for r in rows),
        "three_phase_fault_rows": sum(int(r["three_phase_fault_rows"] or 0) for r in rows),
        "two_phase_fault_rows": sum(int(r["two_phase_fault_rows"] or 0) for r in rows),
        "single_phase_ground_fault_rows": sum(int(r["single_phase_ground_fault_rows"] or 0) for r in rows),
        "roots_with_voltage_violations": sum((r["voltage_violation_bus_count"] or 0) > 0 for r in rows),
        "roots_with_line_overloads": sum((r["line_overload_count"] or 0) > 0 for r in rows),
        "roots_with_transformer_overloads": sum((r["transformer_overload_count"] or 0) > 0 for r in rows),
        "zero_public_capacity_ptd_transformer_proxies": sum(int(r["zero_public_capacity_ptd_transformer_proxy_count"] or 0) for r in rows),
        "maximum_power_balance_error_mw": max((r["active_power_balance_error_mw"] or 0) for r in rows) if rows else None,
        "maximum_power_balance_relative": max((r["active_power_balance_relative"] or 0) for r in rows) if rows else None,
    }

    if rows:
        with duckdb.connect(str(args.database)) as con:
            con.execute("CREATE SCHEMA IF NOT EXISTS study")
            table_prefix = "equivalent_mv_root_peak_batch" if args.per_root_peak else "equivalent_mv_root_batch"
            if args.mv_urban_cable:
                # never overwrite the published star-feeder baseline tables; one table set per cable-layer version
                table_prefix += "_" + "".join(ch if ch.isalnum() else "_" for ch in args.mv_urban_cable.resolve().name)
            con.execute(f"CREATE OR REPLACE TABLE study.{table_prefix}_summary AS SELECT * FROM read_csv_auto(?,header=true)", [str(summary_csv)])
            if full_scope and len(rows) == len(root_rows):
                for suffix in ("bus", "line", "transformer", "fault"):
                    pattern = str(args.output / "roots" / "*" / f"{suffix}_results.csv")
                    if suffix == "fault":
                        con.execute(f"""CREATE OR REPLACE TABLE study.{table_prefix}_fault_results AS
                            SELECT *,0.0::DOUBLE AS fault_resistance_ohm,
                                   0.0::DOUBLE AS fault_reactance_ohm,
                                   CASE WHEN "case"='max' THEN 1000.0 ELSE 500.0 END::DOUBLE
                                       AS source_short_circuit_mva,
                                   0.1::DOUBLE AS source_rx_ratio,
                                   0.1::DOUBLE AS source_r0x0_ratio,
                                   1.0::DOUBLE AS source_x0x1_ratio,
                                   'IEC 60909'::VARCHAR AS calculation_standard,
                                   'FAULT_SOURCE_ZERO_SEQUENCE_PROXY_V1'::VARCHAR
                                       AS parameter_scenario_id
                            FROM read_csv_auto(?,header=true,union_by_name=true)""",[pattern])
                    else:
                        con.execute(f"CREATE OR REPLACE TABLE study.{table_prefix}_{suffix}_results AS SELECT * FROM read_csv_auto(?,header=true,union_by_name=true)", [pattern])

    errors = []
    if not rows:
        errors.append("No MV-root case produced a validation report")
    if failures:
        expected_screening_error=["Voltage, line-loading, or transformer-loading operating constraint failed"]
        unexpected=[f for f in failures if f.get("errors")!=expected_screening_error]
        if not args.design_screening or unexpected:
            errors.append(f"{len(failures)} MV-root cases failed"
                          + (f" ({len(unexpected)} unexpected in screening mode)" if args.design_screening else ""))
    if full_scope and totals["ptds_covered"] != expected_ptds:
        errors.append(f"PTD coverage {totals['ptds_covered']} != {expected_ptds}")
    if (totals["maximum_power_balance_error_mw"] is not None
            and totals["maximum_power_balance_error_mw"] > 1e-5):
        errors.append("At least one root exceeds the active-power balance tolerance")

    report = {
        "result": ("SCREENING_COMPLETE" if args.design_screening and not errors
                   else ("PASS" if not errors and full_scope else ("PARTIAL" if not errors else "FAIL"))),
        "timestamp_utc": "PER_ROOT_FULL_CALENDAR_PEAK" if args.per_root_peak else args.timestamp_utc,
        "checks": totals,
        "errors": errors,
        "failed_case_details": failures,
        "fault_model_version": FAULT_MODEL_VERSION,
        "scope": "Nationwide decomposition of the synthetic equivalent into persisted 60-kV/MV/PTD/LV root slices; each case runs balanced AC PF and max/min 3ph, 2ph and 1ph-ground fault calculations" +
                 (" at that root's full-calendar maximum apparent-power timestamp" if args.per_root_peak else " at one synchronized national timestamp"),
        "limitations": [
            "Root slices validate the nationwide synthetic radial extension but do not reproduce interactions through the meshed transmission network",
            "MV feeder routes, LV topology, cable choices, source strength and several transformer parameters remain explicit scenarios",
            "MV star-equivalent shunt capacitance is zeroed to prevent duplicate charging across unknown shared upstream paths",
            "Source zero-sequence ratios and downstream zero-sequence equipment parameters remain engineering proxies",
            "Two-phase-to-ground faults and non-zero fault impedances are not included",
            "Balanced root-slice power flow complements rather than replaces the separate nationwide ABCN low-voltage studies",
            "Numerical convergence and constraint compliance do not prove correspondence to operator topology or measurements",
        ],
    }
    (args.output / "validation.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
