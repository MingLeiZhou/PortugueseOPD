#!/usr/bin/env python3
"""Offline-replayable review revision; preserves earlier experiment outputs."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
os.environ.setdefault("MPLCONFIGDIR", "/tmp/pt60-mpl")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import pandas as pd
import pandapower as pp
from common import PROJECT, sha256, write_json
from hotspot_connection_audit import correct_connections
from rebuild_snapshot_controls import rebuild_controls
from run_seasonal_week_panel import hourly_cases, _summary_for_week
from run_temporal_validation import (MODEL_INPUT, GENERATOR_INPUT, OUTPUT, run_case,
    eredes_load_snapshot, ren_observation, ren_rnt_loss_benchmark, available_asset_mask)

DEST = OUTPUT / "quality_revision"
INPUTS = DEST / "inputs"
DIAGNOSTIC_TIMESTAMPS = {"2025-07-07T14:15:00+00:00", "2026-01-21T17:15:00+00:00"}
VARIANTS = ("DEDUP_ONLY", "FROZEN_Q", "NO_CAPACITY_UPLIFT", "NO_LOAD_COMPENSATION",
            "NO_REACTORS", "X_MINUS_10_PERCENT", "X_PLUS_10_PERCENT", "UNKNOWN_DATES_EXCLUDED")


def prepare_inputs():
    INPUTS.mkdir(parents=True, exist_ok=True)
    for case in hourly_cases():
        path = INPUTS / f"{case['case_id']}.json"
        if path.exists():
            continue
        stamp = pd.Timestamp(case["timestamp_utc"])
        snapshot, metadata = eredes_load_snapshot(stamp, False)
        snapshot = snapshot[[column for column in ["codigo_subestacao", "subestacao", "energia", "datahora", "data", "hora"] if column in snapshot]].copy()
        write_json(path, dict(case=case, observation=ren_observation(stamp, False),
            snapshot=json.loads(snapshot.to_json(orient="records")),
            load_metadata=metadata, rnt_loss=ren_rnt_loss_benchmark(case["rnt_loss_benchmark_date"], False)))


def dependency_manifest():
    replay_source_names = {
        "build_pandapower.py",
        "common.py",
        "hotspot_connection_audit.py",
        "prepare_public_case.py",
        "pt60_public_model.py",
        "rebuild_snapshot_controls.py",
        "run_review_revision.py",
        "run_seasonal_week_panel.py",
        "run_temporal_validation.py",
        "time_alignment.py",
    }
    replay_sources = {PROJECT / "src" / name for name in replay_source_names}
    missing_sources = sorted(path.name for path in replay_sources if not path.is_file())
    if missing_sources:
        raise FileNotFoundError(f"Missing replay source files: {missing_sources}")
    paths = set(INPUTS.glob("*.json")) | replay_sources
    paths |= set((PROJECT / "config").glob("*.json"))
    paths |= {MODEL_INPUT, GENERATOR_INPUT, PROJECT / "requirements.txt",
              PROJECT / "outputs/tables/lines.csv", PROJECT / "outputs/tables/buses.csv",
              PROJECT / "outputs/tables/transformers_topology.csv",
              PROJECT / "outputs/tables/transformer_capacity_calibration.csv",
              PROJECT / "outputs/tables/pdirt_annex12_2025_pde_loads.csv"}
    records = [{"path": str(p.relative_to(PROJECT.parent)), "sha256": sha256(p)} for p in sorted(paths)]
    return {"sources": records, "fingerprint": hashlib.sha256(json.dumps(records, sort_keys=True).encode()).hexdigest(),
            "design": "336 hourly primary cases; 336 frozen-control references; 672 spatial alternatives preserving national P and residual Q; 14 additional cases at two fixed development diagnostic timestamps (plus the two frozen controls form the 16-case diagnostic set); aggregate exchange and monthly loss held out",
            "input_mode": "UTC interval starts; REN Lisbon start labels matched to E-REDES Lisbon end labels; source labels and 15-minute kWh retained; offline replay requires no network"}


def build_models(output: Path = DEST):
    g = pd.read_csv(GENERATOR_INPUT, low_memory=False)
    original = pp.from_json(MODEL_INPUT)
    revised_g, revised_n, audit = correct_connections(g, original)
    revised_g.to_csv(output / "generators_revised.csv", index=False)
    audit.to_csv(output / "connection_corrections.csv", index=False)
    # Store an inspectable January control state; run_case rebuilds for every date.
    revised_n["pt60_snapshot_controls"] = True
    rebuild_controls(revised_n, revised_g, pd.Timestamp("2026-01-20T19:45:00Z"))
    pp.to_json(revised_n, output / "model_revised.json")
    ledger = pd.read_csv(PROJECT / "outputs/tables/transformer_capacity_calibration.csv")
    ledger["applied_factor"] = ledger.calibration_factor.clip(lower=1)
    ledger["relative_difference_from_ren_pct"] = 100*(ledger.post_calibration_mva/ledger.ren_target_mva-1)
    ledger.to_csv(output / "transformer_capacity_policy.csv", index=False)
    history = []
    for stamp in ["2025-07-07", "2026-01-20"]:
        available = available_asset_mask(revised_g, pd.Timestamp(stamp, tz="UTC"))
        for source, group in revised_g.groupby("generation_source"):
            history.append(dict(timestamp_utc=stamp, source=source, records=len(group),
                undated_records=int(group.available_from_utc.isna().sum()),
                undated_capacity_mw=float(group.loc[group.available_from_utc.isna(), "nameplate_mw"].sum()),
                available_mapped_capacity_mw=float(group.loc[available & revised_g.bus_id.notna(), "nameplate_mw"].sum())))
    pd.DataFrame(history).to_csv(output / "historical_capacity_coverage.csv", index=False)


def solve(job):
    case_id, variant, result_root = job
    data = json.loads((INPUTS / f"{case_id}.json").read_text())
    case = data["case"].copy()
    case["skip_optional_external_acquisition"] = True
    if variant in {"UNIFORM_PDE", "CAPACITY_PDE"}:
        case["residual_allocation_mode"] = variant
    if variant in {"AC_REVISED", "UNIFORM_PDE", "CAPACITY_PDE"}:
        case["spatial_result_path"] = str(Path(result_root) / "spatial_fields" / variant / f"{case_id}.npz")
    g = pd.read_csv(GENERATOR_INPUT, low_memory=False)
    n = pp.from_json(MODEL_INPUT)
    if variant != "DEDUP_ONLY":
        g, n, _ = correct_connections(g, n)
    n["pt60_snapshot_controls"] = variant not in {"DEDUP_ONLY", "FROZEN_Q"}
    if variant == "NO_CAPACITY_UPLIFT":
        table = pd.read_csv(PROJECT / "outputs/tables/transformers_topology.csv").set_index("transformer_id")
        for i, row in n.trafo.iterrows():
            tid = row.get("transformer_id", row["name"])
            if tid in table.index:
                n.trafo.loc[i, "sn_mva"] = float(table.loc[tid, "pre_calibration_sn_mva"])
    if variant == "NO_LOAD_COMPENSATION":
        case["disable_load_compensation"] = True
    if variant == "NO_REACTORS":
        n.shunt.loc[n.shunt.name.fillna("").str.startswith("REACTOR:"), "in_service"] = False
    if variant.startswith("X_"):
        n.line.x_ohm_per_km *= .9 if variant == "X_MINUS_10_PERCENT" else 1.1
    if variant == "UNKNOWN_DATES_EXCLUDED":
        case["exclude_unknown_asset_dates"] = True
    with tempfile.TemporaryDirectory(prefix="pt60-review-") as temporary:
        model = Path(temporary) / "model.json"
        pp.to_json(n, model)
        try:
            result, _, hot, _, _, _, _ = run_case(case, g, False, save_solved=False,
                model_input=model, observation_override=data["observation"],
                load_snapshot_override=pd.DataFrame(data["snapshot"]), rnt_loss_override=data["rnt_loss"],
                weather_override={"source": "Not used in this controlled experiment"},
                diagnostic_line_ids=("LINE:000884", "LINE:003702", "LINE:004013"))
            row = {k:v for k,v in result.items() if not isinstance(v,(dict,list))}
            row.update(variant=variant, status="COMPLETE", maximum_loading_line_id=hot[0]["line_id"])
            for h in hot:
                h.update(variant=variant, timestamp_utc=case["timestamp_utc"], season=case["season"])
            return row, hot
        except Exception as exc:
            return dict(**case, variant=variant, status="FAILED", converged=False,
                        error=f"{type(exc).__name__}: {exc}"), []


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--pilot", action="store_true")
    p.add_argument("--workers", type=int, default=3)
    p.add_argument("--prepare-only", action="store_true")
    p.add_argument("--output-dir", type=Path, default=DEST)
    args=p.parse_args(); output=args.output_dir; output.mkdir(parents=True, exist_ok=True)
    prepare_inputs()
    manifest=dependency_manifest(); mf=output/"experiment_manifest.json"
    if mf.exists() and json.loads(mf.read_text())["fingerprint"] != manifest["fingerprint"]:
        raise RuntimeError("Dependencies changed: choose a new --output-dir to preserve earlier results")
    write_json(mf,manifest); build_models(output)
    if args.prepare_only:return
    cases=hourly_cases()
    jobs=[(c["case_id"],v) for c in cases if not args.pilot or c["timestamp_utc"] in DIAGNOSTIC_TIMESTAMPS for v in ("AC_REVISED", "FROZEN_Q", "UNIFORM_PDE", "CAPACITY_PDE")]
    jobs += [(c["case_id"],v) for c in cases if c["timestamp_utc"] in DIAGNOSTIC_TIMESTAMPS for v in VARIANTS if v != "FROZEN_Q"]
    result_path=output/"revision_results.csv"; hp=output/"line_diagnostics.csv"
    solver_manifest_path=output/"solver_execution_manifest.json"
    if result_path.exists():
        if not solver_manifest_path.exists() or json.loads(solver_manifest_path.read_text()) != manifest:
            raise RuntimeError("Existing results do not match the current solver dependency manifest; choose a new --output-dir")
    else:
        write_json(solver_manifest_path,manifest)
    rows=pd.read_csv(result_path).to_dict("records") if result_path.exists() else []
    hot=pd.read_csv(hp).to_dict("records") if hp.exists() else []
    done={(r["case_id"],r["variant"]) for r in rows}
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures=[pool.submit(solve,(*j, str(output))) for j in jobs if j not in done]
        for f in as_completed(futures):
            row,h=f.result(); rows.append(row); hot.extend(h)
            pd.DataFrame(rows).sort_values(["variant","timestamp_utc"]).to_csv(result_path,index=False)
            pd.DataFrame(hot).to_csv(hp,index=False)
            print(row["variant"],row["timestamp_utc"],row["status"],row.get("error", ""),flush=True)
    frame=pd.DataFrame(rows)
    frame["eredes_record_count"] = frame["load_profile_source_record_count"]
    frame["eredes_unique_substation_count"] = frame["load_profile_unique_substation_count"]
    primary=frame[frame.variant.eq("AC_REVISED")]
    primary.to_csv(output/"seasonal_week_validation.csv",index=False)
    frame[frame.variant.eq("FROZEN_Q")].to_csv(output/"static_control_reference.csv",index=False)
    frame[frame.variant.isin(["UNIFORM_PDE", "CAPACITY_PDE"])].to_csv(output/"spatial_allocation_results.csv",index=False)
    if len(primary)==336:
        write_json(output/"seasonal_week_summary.json",[_summary_for_week(g) for _,g in primary.groupby("season")])
    print("Saved",len(primary),"primary samples; failures",int(frame.status.ne("COMPLETE").sum()),flush=True)


if __name__=="__main__":main()
