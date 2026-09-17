#!/usr/bin/env python3
"""Publish the annual representative-point N-1 panel into SimPT60 DuckDB."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from datetime import datetime, timezone

import duckdb
import pandas as pd


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    args = parser.parse_args()
    database = args.database.resolve()
    root = args.results_dir.resolve()
    panel_path = root / "annual_nminus1_panel.csv"
    snapshots_path = root / "representative_snapshots.csv"
    summary_path = root / "annual_nminus1_summary.json"
    for path in (panel_path, snapshots_path, summary_path):
        if not path.exists():
            raise FileNotFoundError(path)

    panel = pd.read_csv(panel_path, low_memory=False)
    snapshots = pd.read_csv(snapshots_path, low_memory=False)
    overload_frames = []
    for path in sorted((root / "screens").glob("*/nminus1_overloaded_physical_circuits.csv")):
        try:
            frame = pd.read_csv(path, low_memory=False)
        except pd.errors.EmptyDataError:
            continue
        if frame.empty:
            continue
        frame.insert(0, "representative_role", path.parent.name)
        overload_frames.append(frame)
    overloads = pd.concat(overload_frames, ignore_index=True) if overload_frames else pd.DataFrame({
        "representative_role": pd.Series(dtype="string"),
        "contingency_id": pd.Series(dtype="string"),
    })
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    run_id = "ANNUAL_N1_EXTREMA_V1"
    panel.insert(0, "run_id", run_id)
    snapshots.insert(0, "run_id", run_id)
    overloads.insert(0, "run_id", run_id)
    provenance = pd.DataFrame([{
        "run_id": run_id,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "selection_method": summary["selection_method"],
        "representative_snapshot_count": summary["representative_snapshots"],
        "result_row_count": len(panel),
        "unique_physical_element_count": int(panel.element_id.nunique()),
        "results_path": str(root),
        "panel_sha256": sha256(panel_path),
        "summary_sha256": sha256(summary_path),
        "method_status": "RESEARCH_SCREEN_NOT_OPERATOR_SECURITY_CERTIFICATION",
        "summary_json": json.dumps(summary, ensure_ascii=False, default=str),
    }])

    con = duckdb.connect(str(database), read_only=False)
    try:
        con.execute("BEGIN")
        con.execute("CREATE SCHEMA IF NOT EXISTS scenario")
        con.execute("CREATE SCHEMA IF NOT EXISTS provenance")
        con.register("annual_panel_frame", panel)
        con.register("annual_snapshot_frame", snapshots)
        con.register("annual_overload_frame", overloads)
        con.register("annual_provenance_frame", provenance)
        con.execute("CREATE OR REPLACE TABLE scenario.annual_nminus1_results AS SELECT * FROM annual_panel_frame")
        con.execute("CREATE OR REPLACE TABLE scenario.annual_nminus1_snapshots AS SELECT * FROM annual_snapshot_frame")
        con.execute("CREATE OR REPLACE TABLE scenario.annual_nminus1_overloads AS SELECT * FROM annual_overload_frame")
        con.execute("CREATE TABLE IF NOT EXISTS provenance.annual_nminus1_runs AS SELECT * FROM annual_provenance_frame WHERE false")
        con.execute("DELETE FROM provenance.annual_nminus1_runs WHERE run_id=?", [run_id])
        con.execute("INSERT INTO provenance.annual_nminus1_runs SELECT * FROM annual_provenance_frame")
        con.execute("COMMIT")
        print(json.dumps({
            "database": str(database),
            "run_id": run_id,
            "snapshots": len(snapshots),
            "results": len(panel),
            "overload_rows": len(overloads),
            "tables": [
                "scenario.annual_nminus1_snapshots",
                "scenario.annual_nminus1_results",
                "scenario.annual_nminus1_overloads",
                "provenance.annual_nminus1_runs",
            ],
        }, ensure_ascii=False, indent=2))
    except Exception:
        con.execute("ROLLBACK")
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
