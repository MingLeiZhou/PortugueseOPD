#!/usr/bin/env python3
"""Nationwide MV customer connections from parish-level official counts (see src/pilot_mv_customers_parish.py).
Runs every municipality in parallel (resumable), then totals the three topology assumptions and adds them to the
MV-URBAN-CABLE-V1 PTD-connection layer for comparison with E-REDES MV km. No calibration.
Usage: python src/run_mv_customers_parish_national.py --workers 6"""
import argparse, json, os, sys, time
from multiprocessing import Pool
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("MVC_NATIONAL_BASE", ROOT / "output/all_voltage/mv_customer_national")); RES = BASE / "municipality"; RES.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MVC_NATIONAL_OSM", str(BASE / "osm"))
sys.path.insert(0, str(ROOT / "src"))
import pilot_mv_customers_parish as mvc  # noqa: E402
RAW = ROOT / "data/raw/eredes_mv_customers"
OFFICIAL = {"mv_underground_km": 15564.0, "mv_overhead_km": 59878.0, "source": "E-REDES Relatório da Qualidade de Serviço 2024, Tabela 2.1"}
V1 = {"underground_km": 8106.0, "overhead_km": 59400.0,
      "note": "MV-URBAN-CABLE-V1 PTD-connection layer + OSM MV network (docs/MV_LV_TRANSFORMER_FIX_PLAN_2026-09-29_CN.md)"}
_G = {}

def init():
    import json as _j
    from shapely.geometry import shape
    _G["counts"] = pd.read_csv(sorted(RAW.glob("mat_at_mt_cpes_by_parish_*.csv"))[-1], dtype={"fre_code": str}).set_index("fre_code").mat_at_mt_cpes.to_dict()
    gj = _j.loads((RAW / "parishes_caop2024.geojson").read_text())
    _G["parishes"] = {f["properties"]["fre_code"]: shape(f["geometry"]).buffer(0) for f in gj["features"]}

def one(cc):
    f = RES / f"summary_{cc}.json"
    if f.exists():
        return json.loads(f.read_text())
    try:
        r = mvc.run(cc, _G["counts"], _G["parishes"], out=RES)
    except Exception as ex:
        r = {"concelho": cc, "error": repr(ex)[:300]}
    if "error" not in r:
        f.write_text(json.dumps(r, ensure_ascii=False))
    return r

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--workers", type=int, default=6); a = ap.parse_args()
    t0 = time.time()
    ccs = sorted(p.stem[4:] for p in Path(os.environ["MVC_NATIONAL_OSM"]).glob("mvc_*.json"))
    res = []
    with Pool(a.workers, initializer=init) as pool:
        for k, r in enumerate(pool.imap_unordered(one, ccs), 1):
            res.append(r)
            if k % 10 == 0 or "error" in r:
                print(f"[{k}/{len(ccs)}] {r.get('concelho')} {'ERROR ' + str(r.get('error')) if 'error' in r else ''} {round(time.time() - t0)} s", flush=True)
    ok = [r for r in res if "underground_km" in r]
    tot = lambda key, topo: round(sum(r[key][topo] for r in ok), 1)
    s = {"official_customers": sum(r.get("official_customers", 0) for r in res), "placed": sum(r.get("placed", 0) for r in res),
         "placed_priority1": sum(r.get("placed_priority1", 0) for r in ok), "placed_fallback": sum(r.get("placed_fallback", 0) for r in ok),
         "not_placed_no_building": sum(r.get("not_placed_no_building", 0) for r in res), "unreachable": sum(r.get("unreachable", 0) for r in ok),
         "underground_customers": sum(r.get("underground_customers", 0) for r in ok), "overhead_customers": sum(r.get("overhead_customers", 0) for r in ok),
         "customer_connection_km": {t: {"underground": tot("underground_km", t), "overhead": tot("overhead_km", t)} for t in ("shared", "radial", "loop_in")},
         "v1_layer": V1, "official": OFFICIAL}
    s["mv_total_with_customers"] = {t: {"underground_km": round(V1["underground_km"] + s["customer_connection_km"][t]["underground"]),
                                        "underground_diff_pct": round(((V1["underground_km"] + s["customer_connection_km"][t]["underground"]) / OFFICIAL["mv_underground_km"] - 1) * 100, 1),
                                        "overhead_km": round(V1["overhead_km"] + s["customer_connection_km"][t]["overhead"]),
                                        "overhead_diff_pct": round(((V1["overhead_km"] + s["customer_connection_km"][t]["overhead"]) / OFFICIAL["mv_overhead_km"] - 1) * 100, 1)}
                                    for t in ("shared", "radial", "loop_in")}
    s["errors"] = [r for r in res if "error" in r]; s["seconds"] = round(time.time() - t0, 1)
    (BASE / "summary.json").write_text(json.dumps(s, indent=2, ensure_ascii=False)); print(json.dumps(s, indent=2, ensure_ascii=False))

if __name__ == "__main__":
    main()
