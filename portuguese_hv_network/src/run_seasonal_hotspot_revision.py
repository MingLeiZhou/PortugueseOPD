#!/usr/bin/env python3
"""Reproduce source-based hotspot corrections and declared sensitivity cases.

Original weekly outputs remain immutable. Primary comparison changes only the
three source-identified generator connections. Planning ratings and circuit
continuity are separate experiments, never fitted to the observed exchange.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
import json
import hashlib
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/pt60-mpl")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import pandas as pd
import pandapower as pp
from common import sha256, write_json, PROJECT
from hotspot_connection_audit import correct_connections, isolate_6202_intermediate_connections, RARI, APA_TRANCOSO
from run_seasonal_week_panel import hourly_cases, _summary_for_week, _daily_summary
from run_temporal_validation import MODEL_INPUT, GENERATOR_INPUT, OUTPUT, RAW_REN, RAW_EREDES, run_case

DEST = OUTPUT / "seasonal_revision"
WATCH = ("LINE:000884", "LINE:003702", "LINE:005426")
PEAKS = {"2025-07-07T14:15:00+00:00", "2026-01-21T17:15:00+00:00"}


def prepare():
    DEST.mkdir(parents=True, exist_ok=True)
    old_manifest = json.loads((DEST / "experiment_manifest.json").read_text()) if (DEST / "experiment_manifest.json").exists() else {}
    g = pd.read_csv(GENERATOR_INPUT, low_memory=False)
    net = pp.from_json(MODEL_INPUT)
    revised_g, revised_n, audit = correct_connections(g, net)
    assert revised_g.nameplate_mw.sum() == g.nameplate_mw.sum()
    assert (revised_g.available_from_utc.fillna("") == g.available_from_utc.fillna("")).all()
    audit.to_csv(DEST / "connection_corrections.csv", index=False)
    revised_g.to_csv(DEST / "generators_revised.csv", index=False)
    pp.to_json(revised_n, DEST / "model_revised.json")
    lines = pd.read_csv(PROJECT / "outputs/tables/lines.csv", low_memory=False)
    isolated = isolate_6202_intermediate_connections(revised_n, lines)
    pp.to_json(isolated, DEST / "model_circuit_sensitivity.json")
    records = []
    for source, url in [(MODEL_INPUT, "local original model"), (GENERATOR_INPUT, "local original asset table"),
                        (PROJECT.parent / "temp/pt60_trancoso_apa_connection.pdf", APA_TRANCOSO),
                        (PROJECT.parent / "temp/pt60_rari2024_lines.pdf", RARI)]:
        records.append(dict(path=str(source.relative_to(PROJECT.parent)), sha256=sha256(source), source_url=url))
    cached_inputs = set()
    for case in hourly_cases():
        stamp=pd.Timestamp(case["timestamp_utc"])
        cached_inputs.add(RAW_REN / f"dispatch_{stamp.strftime('%Y-%m-%d')}.json")
        cached_inputs.add(RAW_EREDES / f"load_{stamp.strftime('%Y-%m-%d_%H%M')}.json")
        cached_inputs.add(RAW_REN / f"rnt_balance_{case['rnt_loss_benchmark_date']}.csv")
    cached_inputs.update([DEST / "model_revised.json", DEST / "generators_revised.csv",
                          Path(__file__), Path(__file__).with_name("hotspot_connection_audit.py"),
                          Path(__file__).with_name("run_temporal_validation.py")])
    for source in sorted(cached_inputs):
        records.append(dict(path=str(source.relative_to(PROJECT.parent)),sha256=sha256(source),source_url="local cached experiment input or implementation"))
    digest=hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest()
    if old_manifest.get("run_fingerprint") not in (None,digest) and (DEST/"revision_results.csv").exists():
        raise RuntimeError("Inputs or implementation changed since checkpoint; retain the old revision in a separate directory before rerunning")
    write_json(DEST / "experiment_manifest.json", dict(
        sources=records, run_fingerprint=digest, primary="Four source-identified connection corrections plus independent battery injection de-duplication; no rating, load, dispatch-total or switch changes",
        sensitivity="Planning reference ratings; isolated 6202 interior GIS connections (not observed switches)",
        selection="Both complete previously selected weeks; peak sensitivity selected from original baseline",
        observed_peak_reference="RARI 2024 Annex 4 pp. 56, 65: 59 A and 113 A, annual maxima, not thermal ratings",
        historical_limit="Retains prior commissioned dates; APA commissioning disagreement for Trancoso requires separate audit"))


def solve(job):
    case, variant = job
    case = deepcopy(case)
    case["legacy_bus_aggregation"] = variant == "BASELINE"
    g = pd.read_csv(GENERATOR_INPUT if variant == "BASELINE" else DEST / "generators_revised.csv", low_memory=False)
    model = MODEL_INPUT if variant == "BASELINE" else DEST / ("model_circuit_sensitivity.json" if variant == "CIRCUIT_SENSITIVITY" else "model_revised.json")
    overrides = None
    if variant == "PLANNING_RATING_SENSITIVITY":
        # run_case multiplies summer input by the declared factor. Undo it so
        # the named planning winter/summer value is applied exactly once.
        factor = case["rating_factor_relative_to_static_summer"]
        winter = case["season"] == "WINTER"
        lines = pd.read_csv(PROJECT / "outputs/tables/lines.csv")
        ids = list(lines.loc[lines["name"].eq("1106L5620200"), "line_id"])
        values = [(lid, .582 if winter else .474) for lid in ids] + [("LINE:000884", .362 if winter else .261)]
        overrides = pd.DataFrame([dict(line_id=lid, static_summer_max_i_ka=value/factor,
            source_url="https://www.erse.pt/media/340hrot0/proposta-pdird-e-2020_anexo_b.pdf") for lid,value in values])
    result, _, hot, load, _, _, _ = run_case(case, g, False, save_solved=False,
        model_input=model, line_parameter_overrides=overrides, diagnostic_line_ids=WATCH)
    row = {k:v for k,v in result.items() if not isinstance(v,(dict,list))}
    row.update(variant=variant, status="COMPLETE", eredes_record_count=result["load_profile_source_record_count"])
    row.update(maximum_loading_line_id=hot[0]["line_id"])
    for h in hot:
        h.update(variant=variant, timestamp_utc=case["timestamp_utc"], season=case["season"])
    return row, hot


def main():
    p=argparse.ArgumentParser();p.add_argument("--pilot",action="store_true");p.add_argument("--workers",type=int,default=3);a=p.parse_args()
    prepare()
    cases=hourly_cases()
    jobs=[(c,"CONNECTION_REVISED") for c in cases if not a.pilot or c["timestamp_utc"] in PEAKS]
    jobs += [(c,v) for c in cases if c["timestamp_utc"] in PEAKS
             for v in ("BASELINE","PLANNING_RATING_SENSITIVITY","CIRCUIT_SENSITIVITY")]
    path=DEST/"revision_results.csv";hp=DEST/"revision_line_diagnostics.csv"
    rows=pd.read_csv(path).to_dict("records") if path.exists() else []
    hot=pd.read_csv(hp).to_dict("records") if hp.exists() else []
    done={(r["case_id"],r["variant"]) for r in rows}
    jobs=[j for j in jobs if (j[0]["case_id"],j[1]) not in done]
    with ProcessPoolExecutor(max_workers=a.workers) as pool:
        futures={pool.submit(solve,j):j for j in jobs}
        for f in as_completed(futures):
            r,h=f.result();rows.append(r);hot.extend(h)
            pd.DataFrame(rows).sort_values(["variant","timestamp_utc"]).to_csv(path,index=False)
            pd.DataFrame(hot).to_csv(hp,index=False)
            print(r["variant"],r["timestamp_utc"],f"max={r['maximum_line_loading_percent']:.2f}%",flush=True)
    frame=pd.DataFrame(rows);main=frame[frame.variant.eq("CONNECTION_REVISED")]
    main.to_csv(DEST/"seasonal_week_validation.csv",index=False)
    write_json(DEST/"seasonal_week_summary.json",[_summary_for_week(g) for _,g in main.groupby("season")])
    _daily_summary(main).to_csv(DEST/"seasonal_week_daily_summary.csv",index=False)
    original=pd.read_csv(OUTPUT/"seasonal_weeks/seasonal_week_validation.csv")
    pairs=original.merge(main,on=["case_id","timestamp_utc","season"],suffixes=("_original","_revised"),validate="one_to_one")
    pairs.to_csv(DEST/"paired_week_results.csv",index=False)
    print('Completed revised samples',len(main),flush=True)

if __name__ == "__main__":
    main()
