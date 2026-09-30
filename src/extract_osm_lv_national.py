#!/usr/bin/env python3
"""One pass over the Portugal OSM PBF: roads (full geometry) and buildings (centroid + footprint area)
for every municipality bbox (PTD extent + 0.04 deg). Writes output/all_voltage/lv_geo_national/osm/osm_<cc>.json
in the format read by pilot_lv_geographic_powerflow.py."""
import json, math, time
from collections import defaultdict
from pathlib import Path
import duckdb, numpy as np, osmium

ROOT = Path(__file__).resolve().parents[1]
PBF = ROOT / "portuguese_hv_network/data/raw/osm/portugal-latest.osm.pbf"
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/lv_geo_national/osm"
ROAD = {"motorway","trunk","primary","secondary","tertiary","unclassified","residential","service","living_street",
        "primary_link","secondary_link","tertiary_link","trunk_link","road","pedestrian","track"}
PAD = 0.04

def main():
    t0 = time.time(); OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB), read_only=True)
    bb = con.sql(f"""select substr(ptd_code,1,4) cc, min(ptd_lon)-{PAD} w, min(ptd_lat)-{PAD} s, max(ptd_lon)+{PAD} e, max(ptd_lat)+{PAD} n
                     from candidate.ptd_mv_attachment_assessment group by 1 order by 1""").df()
    ccs = list(bb.cc); W, S, E, N = (bb[c].values for c in ("w", "s", "e", "n"))
    (OUT / "bbox.json").write_text(json.dumps({c: [w, s, e, n] for c, w, s, e, n in zip(ccs, W, S, E, N)}))
    res = {cc: {"roads": [], "buildings": []} for cc in ccs}
    fp = osmium.FileProcessor(str(PBF)).with_locations().with_filter(osmium.filter.KeyFilter("highway", "building"))
    k = 0
    for o in fp:
        if not o.is_way():
            continue
        t = o.tags; hw, b = t.get("highway"), t.get("building")
        if not ((hw in ROAD) or b):
            continue
        try:
            c = [(round(nd.lon, 6), round(nd.lat, 6)) for nd in o.nodes]
        except osmium.InvalidLocationError:
            continue
        if len(c) < 2:
            continue
        mx, my = c[len(c) // 2]
        hit = np.flatnonzero((W <= mx) & (mx <= E) & (S <= my) & (my <= N))
        if not len(hit):
            continue
        if hw in ROAD:
            rec = ("roads", {"c": c})
        elif len(c) >= 4:
            a = np.array(c); kx = 111320 * math.cos(math.radians(a[:, 1].mean()))
            x, y = a[:, 0] * kx, a[:, 1] * 110540
            area = 0.5 * abs(float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1])))
            if area < 30:
                continue
            rec = ("buildings", {"p": [round(float(a[:, 0].mean()), 6), round(float(a[:, 1].mean()), 6)], "a": round(area, 1)})
        else:
            continue
        for h in hit:
            res[ccs[h]][rec[0]].append(rec[1])
        k += 1
        if k % 500000 == 0:
            print("ways kept", k, round(time.time() - t0), "s", flush=True)
    for cc in ccs:
        (OUT / f"osm_{cc}.json").write_text(json.dumps(res[cc]))
    print(json.dumps({"municipalities": len(ccs), "ways_kept": k,
                      "roads": sum(len(v["roads"]) for v in res.values()), "buildings": sum(len(v["buildings"]) for v in res.values()),
                      "seconds": round(time.time() - t0, 1)}))

if __name__ == "__main__":
    main()
