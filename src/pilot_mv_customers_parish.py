#!/usr/bin/env python3
"""Pilot v3: MV customer connections placed with PARISH-level official counts (E-REDES CPE MAT/AT/MT, by freguesia).
Per parish, the N official customers go to the N most likely buildings inside the parish polygon:
  priority 1 = OSM non-residential tag, or >=1000 m2 inside an OSM industrial/commercial/retail landuse polygon
  priority 2 = any other building >=300 m2 (fallback, flagged), largest first.
Connection to the nearest existing MV network point (public PTD or its OSM MV projection) along OSM streets.
Three topology assumptions, reported as a range (no calibration to official km):
  shared  = union of street paths (customers sharing corridors, one cable per corridor)
  radial  = one spur per customer (sum of street paths)
  loop_in = in-and-out cable per customer (2 x radial)
Cable vs overhead per customer: underground if >=3 public PTDs within 1 km and most of them are cabin-type, else overhead
(same urban/rural logic as MV-URBAN-CABLE-V1)."""
import json, math, sys
from pathlib import Path
import duckdb, numpy as np, pandas as pd
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree
from shapely.geometry import Polygon, shape, Point
from shapely.strtree import STRtree
from shapely.prepared import prep

ROOT = Path(__file__).resolve().parents[1]
OSMD = ROOT / "output/all_voltage/mv_customer_pilot"; OUT = ROOT / "output/all_voltage/mv_customer_parish_pilot"; OUT.mkdir(parents=True, exist_ok=True)
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
RAW = ROOT / "data/raw/eredes_mv_customers"
NONRES = {"industrial", "commercial", "retail", "warehouse", "office", "hospital", "school", "university", "college", "supermarket",
          "hotel", "public", "civic", "government", "manufacture", "factory", "sports_hall", "stadium", "transportation",
          "train_station", "hangar", "service", "data_center", "parking", "barracks", "prison", "fire_station", "farm_auxiliary", "greenhouse"}
LU = {"industrial", "commercial", "retail"}

import os
NAT_OSM = os.environ.get("MVC_NATIONAL_OSM")   # national mode: compact buildings/landuse + roads from the LV national extract
NAT_ROADS = ROOT / "output/all_voltage/lv_geo_national/osm"

def run(cc, counts, parishes, out=None):
    out = Path(out) if out else OUT
    if NAT_OSM:
        osm = json.loads((Path(NAT_OSM) / f"mvc_{cc}.json").read_text())
        osm["roads"] = json.loads((NAT_ROADS / f"osm_{cc}.json").read_text())["roads"]
    else:
        osm = json.loads((OSMD / f"osm_{cc}.json").read_text())
    con = duckdb.connect(str(DB), read_only=True)
    ptd = con.sql(f"""select a.ptd_code, a.ptd_lon lon, a.ptd_lat lat, a.projected_lon plon, a.projected_lat plat, coalesce(p.construction_type,'') ctype
                      from candidate.ptd_mv_attachment_assessment a left join candidate.ptd_public_attributes p using(ptd_code)""").df()
    lat0 = float(ptd.lat[ptd.ptd_code.str[:4] == cc].mean()); kx = 111320 * math.cos(math.radians(lat0))
    X = lambda lon, lat: np.c_[np.asarray(lon, float) * kx, np.asarray(lat, float) * 110540.0]
    # street graph
    nid, xy, rows, cols, w = {}, [], [], [], []
    for r in osm["roads"]:
        idx = []
        for p in map(tuple, r["c"]):
            if p not in nid: nid[p] = len(xy); xy.append(X([p[0]], [p[1]])[0])
            idx.append(nid[p])
        for a, b in zip(idx, idx[1:]):
            d = float(np.hypot(*(xy[a] - xy[b]))); rows += [a, b]; cols += [b, a]; w += [d, d]
    R = np.array(xy); G = coo_matrix((w, (rows, cols)), shape=(len(R), len(R))).tocsr(); rtree = cKDTree(R)
    # parishes of this municipality
    par = {k: v for k, v in parishes.items() if k.startswith(cc)}
    if not par or not osm["roads"]:
        return {"concelho": cc, "official_customers": int(sum(v for k, v in counts.items() if k.startswith(cc))), "error": "no parishes or roads"}
    ptree = STRtree(list(par.values())); pkeys = list(par.keys())
    lu = [Polygon(l["c"]).buffer(0) for l in osm["landuse"] if l.get("lu") in LU and len(l["c"]) >= 4]
    lutree = STRtree(lu) if lu else None
    cand = []
    for b in osm["buildings"]:
        if "p" in b:
            (lon, lat), area = b["p"], b["a"]
        else:
            c = np.array(b["c"])
            if len(c) < 4: continue
            xy_ = X(c[:, 0], c[:, 1]); area = 0.5 * abs(np.dot(xy_[:-1, 0], xy_[1:, 1]) - np.dot(xy_[1:, 0], xy_[:-1, 1]))
            lon, lat = c[:-1, 0].mean(), c[:-1, 1].mean()
        if area < 300: continue
        pt = Point(lon, lat)
        hit = ptree.query(pt, predicate="within")
        if not len(hit): continue
        in_lu = bool(lutree is not None and len(lutree.query(pt, predicate="within")))
        prio = 1 if (b.get("b") in NONRES or (in_lu and area >= 1000)) else 2
        cand.append((pkeys[hit[0]], lon, lat, area, prio, b.get("b")))
    cand = pd.DataFrame(cand, columns=["fre_code", "lon", "lat", "area", "prio", "tag"])
    ov = ROOT / f"output/all_voltage/overture_pilot/buildings_{cc}.parquet"
    if ov.exists():   # priority 3: Overture footprints >=300 m2 not already an OSM candidate (fills OSM gaps)
        o = duckdb.sql(f"select lon, lat, area_m2 area from read_parquet('{ov}') where area_m2 >= 300").df()
        if len(cand):
            d, _ = cKDTree(X(cand.lon, cand.lat)).query(X(o.lon, o.lat)); o = o[d > 20]
        fc = []
        for lon, lat in zip(o.lon, o.lat):
            hit = ptree.query(Point(lon, lat), predicate="within"); fc.append(pkeys[hit[0]] if len(hit) else None)
        o["fre_code"] = fc; o = o.dropna(subset=["fre_code"]); o["prio"] = 3; o["tag"] = "overture"
        cand = pd.concat([cand, o[cand.columns]], ignore_index=True)
    sel, short = [], {}
    for fc in par:
        n = int(counts.get(fc, 0))
        if n == 0: continue
        c = cand[cand.fre_code == fc].sort_values(["prio", "area"], ascending=[True, False]).head(n)
        sel.append(c)
        if len(c) < n: short[fc] = n - len(c)
    sel = pd.concat(sel, ignore_index=True) if sel else cand.head(0)
    if sel.empty:
        return {"concelho": cc, "official_customers": int(sum(int(counts.get(fc, 0)) for fc in par)), "placed": 0, "not_placed_no_building": int(sum(short.values()))}
    # MV network points: public PTDs (+ OSM projections) near the municipality
    own = ptd.ptd_code.str[:4] == cc
    w0, s0, e0, n0 = ptd.lon[own].min() - .05, ptd.lat[own].min() - .05, ptd.lon[own].max() + .05, ptd.lat[own].max() + .05
    near = ptd[ptd.lon.between(w0, e0) & ptd.lat.between(s0, n0)]
    net = np.vstack([X(near.lon, near.lat), X(near.plon.dropna(), near.plat.dropna())])
    if os.environ.get("MVC_CONNECT_OSM_MV", "0") == "1":   # existing OSM 6-30 kV line vertices are also valid tap points
        on = con.sql(f"select lon, lat from candidate.osm_nodes where voltage_kv between 6 and 30 and lon between {w0} and {e0} and lat between {s0} and {n0}").df()
        net = np.vstack([net, X(on.lon, on.lat)])
    ns, nn = rtree.query(net); src = np.unique(nn[ns <= 200])
    dist, pred, _ = dijkstra(G, directed=False, indices=src, min_only=True, return_predecessors=True, limit=20000)
    S = X(sel.lon, sel.lat); cs, cn = rtree.query(S)
    sel["street_km"] = np.where(np.isfinite(dist[cn]), dist[cn] + cs, np.nan) / 1000
    # urban/rural cable rule from surrounding public PTD types
    P = X(near.lon, near.lat); cabin = ~near.ctype.str.startswith("Aéreo").values
    nb = cKDTree(P).query_ball_point(S, 1000)
    sel["underground"] = [len(ix) >= 3 and cabin[ix].mean() > 0.5 for ix in nb]
    # shared corridors: union of edges on the street paths, per cable class
    shared = {True: 0.0, False: 0.0}
    for ug in (True, False):
        seen = set(); tot = 0.0
        for k in np.flatnonzero((sel.underground.values == ug) & np.isfinite(sel.street_km.values)):
            v = cn[k]; tot += cs[k]
            while pred[v] >= 0 and (v, pred[v]) not in seen:
                seen.add((v, pred[v])); tot += float(np.hypot(*(R[v] - R[pred[v]]))); v = pred[v]
        shared[ug] = tot / 1000
    sel.to_csv(out / f"customers_{cc}.csv", index=False)
    ok = sel.street_km.notna()
    def km(ug, f): return round(float(sel.street_km[ok & (sel.underground == ug)].sum()) * f, 1)
    return {"concelho": cc, "official_customers": int(sum(int(counts.get(fc, 0)) for fc in par)), "parishes_with_customers": int(sum(1 for fc in par if counts.get(fc, 0))),
            "placed": int(len(sel)), "placed_priority1": int((sel.prio == 1).sum()), "placed_fallback": int((sel.prio == 2).sum()), "placed_overture": int((sel.prio == 3).sum()),
            "not_placed_no_building": int(sum(short.values())), "unreachable": int((~ok).sum()),
            "underground_customers": int(sel.underground.sum()), "overhead_customers": int((~sel.underground).sum()),
            "street_km_median": round(float(sel.street_km.median()), 3), "street_km_mean": round(float(sel.street_km.mean()), 3),
            "underground_km": {"shared": round(shared[True], 1), "radial": km(True, 1), "loop_in": km(True, 2)},
            "overhead_km": {"shared": round(shared[False], 1), "radial": km(False, 1), "loop_in": km(False, 2)}}

if __name__ == "__main__":
    counts = pd.read_csv(sorted(RAW.glob("mat_at_mt_cpes_by_parish_*.csv"))[-1], dtype={"fre_code": str}).set_index("fre_code").mat_at_mt_cpes.to_dict()
    gj = json.loads((RAW / "parishes_caop2024.geojson").read_text())
    ccs = sys.argv[1:] or ["0705", "0308", "1106"]
    parishes = {f["properties"]["fre_code"]: shape(f["geometry"]).buffer(0) for f in gj["features"] if f["properties"]["fre_code"][:4] in ccs}
    res = [run(cc, counts, parishes) for cc in ccs]
    for r in res: print(json.dumps(r, ensure_ascii=False), flush=True)
    (OUT / "pilot_summary.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
