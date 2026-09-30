#!/usr/bin/env python3
"""Nationwide LV geographic networks + ABCN power flow (scenario 3: standard cables + parallel refinement).
Runs pilot_lv_geographic_powerflow.run() per municipality in parallel, then aggregates.
Usage: python src/run_lv_geographic_national.py --workers 6"""
import argparse, json, os, sys, time
from multiprocessing import Pool
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("LV_NATIONAL_BASE", ROOT / "output/all_voltage/lv_geo_national"))
OSM_DIR = Path(os.environ.get("LV_NATIONAL_OSM", ROOT / "output/all_voltage/lv_geo_national/osm"))
os.environ["LV_OSM_DIR"] = str(OSM_DIR); os.environ["LV_GEO_OUT"] = str(BASE / "ptd")
sys.path.insert(0, str(ROOT / "src"))
import pilot_lv_geographic_powerflow as lv  # noqa: E402

OFFICIAL = {"overhead_km": 115926.0, "underground_km": 34998.0, "ptds": 71713,
            "source": "E-REDES Relatório da Qualidade de Serviço 2024, Tabela 2.1 (31 Dec 2024)"}

def one(cc):
    done = BASE / "ptd" / f"lv_geo_powerflow_{cc}.csv"
    if done.exists() and done.stat().st_size > 0:
        return {"concelho": cc, "reused": True}
    try:
        return lv.run(cc)
    except Exception as ex:
        return {"concelho": cc, "error": repr(ex)[:300]}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workers", type=int, default=6); args = ap.parse_args()
    t0 = time.time(); (BASE / "ptd").mkdir(parents=True, exist_ok=True)
    lv.load_poles()
    ccs = sorted(json.loads((OSM_DIR / "bbox.json").read_text()))
    with Pool(args.workers) as pool:
        res = []
        for k, r in enumerate(pool.imap_unordered(one, ccs), 1):
            res.append(r)
            if k % 10 == 0 or "error" in r:
                print(f"[{k}/{len(ccs)}] {r.get('concelho')} {'ERROR ' + r['error'] if 'error' in r else ''} {round(time.time() - t0)} s", flush=True)
    (BASE / "municipality_results.json").write_text(json.dumps(res, indent=1))
    frames = [pd.read_csv(f) for f in sorted((BASE / "ptd").glob("lv_geo_powerflow_*.csv")) if f.stat().st_size > 5]
    df = pd.concat(frames, ignore_index=True).drop_duplicates("ptd_code")
    df.to_csv(BASE / "lv_geo_powerflow_national.csv", index=False)
    aerial = df.ctype.astype(str).str.startswith("Aéreo")
    lengths = {"pole_overhead_km": round(df.overhead_km.sum(), 1),
               "street_on_aerial_type_ptds_km": round(df.loc[aerial, "street_km"].sum(), 1),
               "street_on_cabin_type_ptds_km": round(df.loc[~aerial, "street_km"].sum(), 1),
               "service_drops_km": round(df.drops_km.sum(), 1),
               "refined_extra_parallel_km": round(df.get("geo_refined_parallel_km", pd.Series(dtype=float)).sum(), 1)}
    model_oh = lengths["pole_overhead_km"] + lengths["street_on_aerial_type_ptds_km"]
    model_ug = lengths["street_on_cabin_type_ptds_km"]
    ok = df.get("geo_refined_status") == "OK"
    v = df.loc[ok, "geo_refined_vmin"]
    summary = {"ptds_total_expected": 72434, "ptds_solved": int(len(df)), "ptds_refined_ok": int(ok.sum()),
               "ptds_diverged": int((df.get("geo_refined_status") == "DIVERGED").sum()),
               "vmin_below_0_9": int((v < 0.9).sum()), "overloaded": int((df.loc[ok, "geo_refined_loading_pct"] > 100).sum()),
               "vmin_quantiles": {q: round(float(v.quantile(q)), 3) for q in (0.001, 0.01, 0.05, 0.5)},
               "compact_vmin_min": round(float(df["compact_vmin"].min()), 3) if "compact_vmin" in df else None,
               "lengths_km": lengths,
               "comparison_official": {"overhead_model_km": round(model_oh, 1), "overhead_official_km": OFFICIAL["overhead_km"],
                                       "overhead_diff_pct": round((model_oh / OFFICIAL["overhead_km"] - 1) * 100, 1),
                                       "underground_model_km": round(model_ug, 1), "underground_official_km": OFFICIAL["underground_km"],
                                       "underground_diff_pct": round((model_ug / OFFICIAL["underground_km"] - 1) * 100, 1),
                                       "note": "service drops and parallel cables reported separately; not calibrated", "source": OFFICIAL["source"]},
               "municipality_errors": [r for r in res if "error" in r], "seconds": round(time.time() - t0, 1)}
    (BASE / "summary.json").write_text(json.dumps(summary, indent=2)); print(json.dumps(summary, indent=2))

if __name__ == "__main__":
    main()
