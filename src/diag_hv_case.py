#!/usr/bin/env python3
"""Re-solve one HV snapshot and save the solved pandapower net (diagnostics for the 60 kV hotspot audit).
Usage: .venv/bin/python src/diag_hv_case.py --project work/hv_ren_fix_2026-09-29 --case PT60_MONTH_20250812_2045_+0100 --tag new
       .venv/bin/python src/diag_hv_case.py --project portuguese_hv_network --case PT60_MONTH_20250812_2045_+0100 --tag old
Source DB: HV_SRC_DB (default: Transcend path). Output: output/simpt_power_release/hotspot_audit/solved_<tag>_<case>.json"""
import argparse, os, sys, tempfile
from pathlib import Path
import duckdb, pandas as pd
ROOT = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser(); ap.add_argument("--project", required=True); ap.add_argument("--case", required=True); ap.add_argument("--tag", required=True)
a = ap.parse_args()
sys.path.insert(0, str(ROOT / a.project / "src"))
import run_monthly_15min as rm
from run_temporal_validation import run_case, GENERATOR_INPUT, MODEL_INPUT
src = Path(os.environ.get("HV_SRC_DB", "data/external/PT60_public_data_2025-05-01_2026-03-24/pt60_public_timeseries.duckdb"))
month = pd.Timestamp(a.case.split("_")[2][:8]).strftime("%Y-%m")
con = duckdb.connect(":memory:"); con.execute(f"ATTACH '{src}' AS source (READ_ONLY)")
sched = [i for i in rm.load_month_schedule(con, month) if i["case_id"] == a.case]
if not sched: sys.exit(f"case not found: {a.case}")
iv = sched[0]; inp = rm.DatabaseInputs(con, src)
out = ROOT / "output/simpt_power_release/hotspot_audit"; out.mkdir(parents=True, exist_ok=True)
with tempfile.TemporaryDirectory() as t:
    case = rm._case_from_interval(iv, Path(t) / "s.npz")
    res = run_case(case, pd.read_csv(GENERATOR_INPUT, low_memory=False), False, save_solved=True,
                   observation_override=inp.observation(iv), load_snapshot_override=inp.load_snapshot(iv),
                   weather_override=inp.weather(iv), rnt_loss_override=inp.rnt_loss(case), output_dir=Path(t))
    solved = Path(t) / f"{case['case_id']}_solved.json"
    target = out / f"solved_{a.tag}_{a.case}.json"; target.write_bytes(solved.read_bytes())
    alloc = Path(t) / "asset_allocation.csv"
    if alloc.exists(): (out / f"asset_allocation_{a.tag}_{a.case}.csv").write_bytes(alloc.read_bytes())
print("model", MODEL_INPUT, "->", target, "max line", res[0]["maximum_line_loading_percent"])
