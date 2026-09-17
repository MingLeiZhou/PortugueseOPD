#!/usr/bin/env python3
"""Build representative annual SimPT60 snapshots and screen all physical N-1 elements.

The panel uses six transparent extrema from the available public time series:
peak and minimum system load, maximum wind, maximum solar, maximum net import,
and maximum net export. Each snapshot is rebuilt from the archived 15-minute
inputs. Every recoverable physical circuit and in-service transformer is then
screened with AC power flow. Post-contingency remedial controls are disabled in
the broad pass; failed cases can be rerun with the targeted control workflow.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

import duckdb
import pandas as pd
import pandapower as pp

from run_monthly_15min import DatabaseInputs, _case_from_interval, _sql_string
from run_temporal_validation import GENERATOR_INPUT, MODEL_INPUT, run_case


PROJECT = Path(__file__).resolve().parents[2]
NMINUS1_SCRIPT = PROJECT / "paper/scripts/run_nminus1_application.py"


def _load_nminus1_module():
    spec = importlib.util.spec_from_file_location("pt60_nminus1_application", NMINUS1_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def select_representative_intervals(database: Path) -> list[dict[str, Any]]:
    con = duckdb.connect(str(database), read_only=True)
    try:
        frame = con.execute(
            """
            WITH dispatch AS (
                SELECT source_date AS local_date, source_index,
                       max(value_mw) FILTER (series_name='Consumption + Storage') AS load_mw,
                       max(value_mw) FILTER (series_name='Wind') AS wind_mw,
                       max(value_mw) FILTER (series_name='Solar') AS solar_mw,
                       max(value_mw) FILTER (series_name='Import')
                         - max(value_mw) FILTER (series_name='Export') AS net_import_mw
                FROM main.ren_dispatch
                GROUP BY source_date, source_index
            )
            SELECT c.*, d.load_mw, d.wind_mw, d.solar_mw, d.net_import_mw
            FROM main.interval_calendar c
            JOIN dispatch d USING (local_date, source_index)
            WHERE c.eredes_alignment_status IN ('UNIQUE', 'AMBIGUOUS_DST_FOLD')
              AND d.load_mw IS NOT NULL
            ORDER BY c.timestamp_utc
            """
        ).fetchdf()
    finally:
        con.close()
    selectors = (
        ("PEAK_LOAD", "load_mw", "max"),
        ("MINIMUM_LOAD", "load_mw", "min"),
        ("MAX_WIND", "wind_mw", "max"),
        ("MAX_SOLAR", "solar_mw", "max"),
        ("MAX_NET_IMPORT", "net_import_mw", "max"),
        ("MAX_NET_EXPORT", "net_import_mw", "min"),
    )
    selected: list[dict[str, Any]] = []
    used: set[tuple[str, int]] = set()
    for role, column, direction in selectors:
        values = pd.to_numeric(frame[column], errors="coerce")
        index = values.idxmax() if direction == "max" else values.idxmin()
        row = frame.loc[index].to_dict()
        key = (str(pd.Timestamp(row["local_date"]).date()), int(row["source_index"]))
        if key in used:
            continue
        used.add(key)
        local = pd.Timestamp(row["timestamp_local"])
        row["case_id"] = f"PT60_ANNUAL_{role}_{local.strftime('%Y%m%d_%H%M_%z')}"
        row["representative_role"] = role
        selected.append(row)
    return selected


def build_snapshot(database: Path, interval: dict[str, Any], output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    solved_path = output / f"{interval['case_id']}_solved.json"
    if solved_path.exists():
        return {**interval, "model_path": str(solved_path), "build_status": "REUSED"}
    con = duckdb.connect(":memory:")
    con.execute(f"ATTACH {_sql_string(database.resolve())} AS source (READ_ONLY)")
    inputs = DatabaseInputs(con, database)
    try:
        spatial_path = output / f"{interval['case_id']}_states.npz"
        case = _case_from_interval(interval, spatial_path)
        observation = inputs.observation(interval)
        snapshot = inputs.load_snapshot(interval)
        weather = inputs.weather(interval)
        rnt_loss = inputs.rnt_loss(case)
        generators = pd.read_csv(GENERATOR_INPUT, low_memory=False)
        result, *_ = run_case(
            case,
            generators,
            False,
            save_solved=True,
            observation_override=observation,
            load_snapshot_override=snapshot,
            weather_override=weather,
            rnt_loss_override=rnt_loss,
            model_input=MODEL_INPUT,
            output_dir=output,
        )
    finally:
        con.close()
    return {
        **interval,
        "model_path": str(solved_path),
        "build_status": "COMPLETE",
        "base_converged": bool(result.get("converged", True)),
        "base_vm_pu_min": result.get("vm_pu_min"),
        "base_maximum_line_loading_percent": result.get("maximum_line_loading_percent"),
    }


def run_screen(
    snapshot: dict[str, Any],
    output_root: Path,
    database: Path,
) -> dict[str, Any]:
    role = str(snapshot["representative_role"])
    case_root = output_root / "screens" / role
    result_path = case_root / "nminus1_results.csv"
    if result_path.exists():
        return {"representative_role": role, "status": "REUSED", "result_path": str(result_path)}
    case_root.parent.mkdir(parents=True, exist_ok=True)
    log_path = output_root / "logs" / f"{role}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        sys.executable,
        str(NMINUS1_SCRIPT),
        "--model", str(snapshot["model_path"]),
        "--output-dir", str(case_root),
        "--static-database", str(database),
        "--all-elements",
        "--no-remedial-controls",
    ]
    env = dict(os.environ)
    env.setdefault("MPLCONFIGDIR", "/tmp/pt60-mpl")
    started = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log:
        completed = subprocess.run(
            command,
            cwd=PROJECT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
        )
    return {
        "representative_role": role,
        "status": "COMPLETE" if completed.returncode == 0 else "FAILED",
        "returncode": completed.returncode,
        "elapsed_seconds": time.monotonic() - started,
        "result_path": str(result_path),
        "log_path": str(log_path),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("portuguese_hv_network/outputs/annual_nminus1_panel"),
    )
    parser.add_argument("--screen-workers", type=int, default=4)
    parser.add_argument("--build-only", action="store_true")
    args = parser.parse_args()
    database = args.database.resolve()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    intervals = select_representative_intervals(database)
    snapshots = []
    for number, interval in enumerate(intervals, start=1):
        print(f"[BUILD {number}/{len(intervals)}] {interval['representative_role']} {interval['timestamp_local']}", flush=True)
        snapshots.append(build_snapshot(database, interval, output / "models"))
    pd.DataFrame(snapshots).to_csv(output / "representative_snapshots.csv", index=False)

    runs: list[dict[str, Any]] = []
    if not args.build_only:
        with ThreadPoolExecutor(max_workers=max(1, args.screen_workers)) as pool:
            futures = {pool.submit(run_screen, row, output, database): row for row in snapshots}
            for future in as_completed(futures):
                value = future.result()
                runs.append(value)
                print(
                    f"[SCREEN] {value['representative_role']} {value['status']} "
                    f"elapsed={value.get('elapsed_seconds', 0.0):.1f}s",
                    flush=True,
                )

    frames = []
    nminus1_module = _load_nminus1_module() if runs else None
    for run in runs:
        path = Path(run["result_path"])
        if run["status"] in {"COMPLETE", "REUSED"} and path.exists():
            frame = pd.read_csv(path)
            frame.insert(0, "representative_role", run["representative_role"])
            screen_root = path.parent
            base = pp.from_json(screen_root / "corrected_base_model.json")
            _, base_overload_rows = nminus1_module.line_overload_diagnostics(base, "N0")
            base_screen_ids = {
                str(row["overloaded_source_line_id"])
                for row in base_overload_rows
                if row["exceeds_nminus1_screen_limit"]
            }
            overload_path = screen_root / "nminus1_overloaded_physical_circuits.csv"
            overload = pd.read_csv(overload_path)
            post_screen = overload.loc[
                overload.exceeds_nminus1_screen_limit.fillna(False).astype(bool)
            ].groupby("contingency_id")["overloaded_source_line_id"].agg(
                lambda values: set(values.astype(str))
            ).to_dict()
            new_counts = []
            for contingency_id in frame.element_id.astype(str):
                post_ids = post_screen.get(contingency_id, set())
                new_counts.append(len(post_ids - base_screen_ids))
            frame["base_physical_circuits_over_nminus1_screen_limit"] = len(base_screen_ids)
            frame["new_physical_circuits_over_nminus1_screen_limit"] = new_counts
            frame["incremental_thermal_violation"] = frame[
                "new_physical_circuits_over_nminus1_screen_limit"
            ].gt(0)
            base_vm_min = float(base.res_bus.vm_pu.min())
            base_vm_max = float(base.res_bus.vm_pu.max())
            base_trafo_max = float(base.res_trafo.loading_percent.max())
            frame["base_vm_pu_min"] = base_vm_min
            frame["base_vm_pu_max"] = base_vm_max
            frame["base_maximum_transformer_loading_percent"] = base_trafo_max
            frame["incremental_voltage_violation"] = (
                frame.voltage_violation.fillna(False).astype(bool)
                & (base_vm_min >= 0.90) & (base_vm_max <= 1.10)
            )
            frame["incremental_transformer_violation"] = (
                pd.to_numeric(frame.maximum_transformer_loading_percent, errors="coerce").gt(120.0)
                & (base_trafo_max <= 120.0)
            )
            frame["passes_incremental_contingency_screen"] = (
                frame.primary_converged.fillna(False).astype(bool)
                & ~frame.material_islanding.fillna(False).astype(bool)
                & ~frame.incremental_thermal_violation
                & ~frame.incremental_voltage_violation
                & ~frame.incremental_transformer_violation
            )
            frames.append(frame)
    if frames:
        panel = pd.concat(frames, ignore_index=True)
        panel.to_csv(output / "annual_nminus1_panel.csv", index=False)
        counts = {
            "rows": int(len(panel)),
            "unique_representative_times": int(panel.representative_role.nunique()),
            "unique_physical_elements": int(panel.element_id.nunique()),
            "primary_converged": int(panel.primary_converged.fillna(False).sum()),
            "passes_screen": int(panel.passes_screen.fillna(False).sum()),
            "material_islanding": int(panel.material_islanding.fillna(False).sum()),
            "passes_incremental_contingency_screen": int(
                panel.passes_incremental_contingency_screen.fillna(False).sum()
            ),
        }
    else:
        counts = {"rows": 0}
    summary = {
        "database": str(database),
        "selection_method": "Six public-series extrema; duplicate timestamps removed",
        "representative_snapshots": len(snapshots),
        "screen_workers": args.screen_workers,
        "all_energized_recoverable_physical_elements": True,
        "unenergized_rows_with_no_base_current_excluded": True,
        "broad_pass_remedial_controls": False,
        "snapshots": snapshots,
        "screen_runs": sorted(runs, key=lambda row: row["representative_role"]),
        "panel_counts": counts,
        "elapsed_seconds": time.monotonic() - started,
    }
    (output / "annual_nminus1_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(counts, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
