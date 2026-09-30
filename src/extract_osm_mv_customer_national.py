#!/usr/bin/env python3
"""One pass over the Portugal OSM PBF for MV customer placement, nationwide:
buildings >=300 m2 (centroid, area, building tag) and industrial/commercial/retail landuse polygons (closed ways),
per municipality bbox (same bbox as the LV national extract). Roads are reused from output/all_voltage/lv_geo_national/osm.
Output: output/all_voltage/mv_customer_national/osm/mvc_<cc>.json"""
import json, math, time
from pathlib import Path
import numpy as np, osmium

ROOT = Path(__file__).resolve().parents[1]
PBF = ROOT / "portuguese_hv_network/data/raw/osm/portugal-latest.osm.pbf"
BBOX = json.loads((ROOT / "output/all_voltage/lv_geo_national/osm/bbox.json").read_text())
OUT = ROOT / "output/all_voltage/mv_customer_national/osm"
LANDUSE = {"industrial", "commercial", "retail"}

def main():
    t0 = time.time(); OUT.mkdir(parents=True, exist_ok=True)
    ccs = list(BBOX); W, S, E, N = (np.array([BBOX[c][i] for c in ccs]) for i in range(4))
    res = {cc: {"buildings": [], "landuse": []} for cc in ccs}
    fp = osmium.FileProcessor(str(PBF)).with_locations().with_filter(osmium.filter.KeyFilter("building", "landuse"))
    k = 0
    for o in fp:
        if not o.is_way():
            continue
        t = o.tags; b, lu = t.get("building"), t.get("landuse")
        if not b and lu not in LANDUSE:
            continue
        try:
            c = [(round(nd.lon, 6), round(nd.lat, 6)) for nd in o.nodes]
        except osmium.InvalidLocationError:
            continue
        if len(c) < 4:
            continue
        a = np.array(c); mx, my = float(a[:-1, 0].mean()), float(a[:-1, 1].mean())
        hit = np.flatnonzero((W <= mx) & (mx <= E) & (S <= my) & (my <= N))
        if not len(hit):
            continue
        if b:
            kx = 111320 * math.cos(math.radians(my)); x, y = a[:, 0] * kx, a[:, 1] * 110540
            area = 0.5 * abs(float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])))
            if area < 300:
                continue
            rec = ("buildings", {"p": [round(mx, 6), round(my, 6)], "a": round(area, 1), "b": b})
        else:
            rec = ("landuse", {"lu": lu, "c": c})
        for h in hit:
            res[ccs[h]][rec[0]].append(rec[1])
        k += 1
    for cc in ccs:
        (OUT / f"mvc_{cc}.json").write_text(json.dumps(res[cc]))
    print(json.dumps({"municipalities": len(ccs), "kept": k, "buildings": sum(len(v["buildings"]) for v in res.values()),
                      "landuse": sum(len(v["landuse"]) for v in res.values()), "seconds": round(time.time() - t0, 1)}))

if __name__ == "__main__":
    main()
