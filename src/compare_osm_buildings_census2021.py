#!/usr/bin/env python3
"""Independent check of OSM building completeness vs INE Censos 2021, per municipality, and its relation to the
LV underground-length shortfall. No calibration: census counts are used only as a diagnostic.
Each OSM building (>=30 m2) is counted once, in the municipality of its nearest PTD (the same rule the LV model uses).
Census counts buildings (mostly residential, all sizes); the ratio is a completeness index, not an exact rate."""
import json
from pathlib import Path
import duckdb, numpy as np, pandas as pd
from scipy.spatial import cKDTree

ROOT = Path(__file__).resolve().parents[1]; BASE = ROOT / "output/all_voltage/lv_geo_national"; OUT = BASE / "diagnosis"
cen = pd.read_csv(ROOT / "data/raw/ine_census2021/buildings_by_municipality.csv", dtype={"dico": str})
con = duckdb.connect(str(ROOT / "output/all_voltage/all_voltage_staging.duckdb"), read_only=True)
ptd = con.sql("select ptd_code, ptd_lon lon, ptd_lat lat from candidate.ptd_mv_attachment_assessment").df()
k = 111320 * np.cos(np.radians(39.5)); X = lambda lon, lat: np.c_[np.asarray(lon) * k, np.asarray(lat) * 110540.0]
tree = cKDTree(X(ptd.lon, ptd.lat)); pcc = ptd.ptd_code.str[:4].values
counts = {}
for f in sorted((BASE / "osm").glob("osm_*.json")):
    cc = f.stem[4:]; b = [x["p"] for x in json.loads(f.read_text())["buildings"] if x.get("a", 0) >= 30]
    if not b: counts[cc] = 0; continue
    b = np.array(b); _, i = tree.query(X(b[:, 0], b[:, 1])); counts[cc] = int((pcc[i] == cc).sum())
m = pd.read_csv(OUT / "municipality_diagnosis.csv", dtype={"cc": str})
m["osm_buildings_own"] = m.cc.map(counts)
m = m.merge(cen[["dico", "census2021_buildings"]], left_on="cc", right_on="dico", how="left").drop(columns="dico")
m["osm_census_ratio"] = m.osm_buildings_own / m.census2021_buildings
m.to_csv(OUT / "municipality_osm_census.csv", index=False)
mm = m.dropna(subset=["osm_census_ratio", "ug_km_per_cabin_ptd"])
mm = mm[mm.cabin_ptds >= 20]
q = pd.qcut(mm.osm_census_ratio, 4, labels=["Q1 least complete", "Q2", "Q3", "Q4 most complete"])
t = mm.groupby(q, observed=True).agg(municipalities=("cc", "size"), ratio_min=("osm_census_ratio", "min"), ratio_max=("osm_census_ratio", "max"),
                                     cabin_ptds=("cabin_ptds", "sum"), ug_km=("street_cabin_km", "sum"))
t["ug_km_per_cabin_ptd"] = t.ug_km / t.cabin_ptds
# partial check: does completeness matter after controlling for urbanity (cabin share)?
mm = mm.assign(cabin_share=1 - mm.aerial_share)
rank = mm[["ug_km_per_cabin_ptd", "osm_census_ratio", "cabin_share", "buildings_per_ptd"]].rank()
def partial(df, x, y, z):
    r = df.corr(); return (r.loc[x, y] - r.loc[x, z] * r.loc[y, z]) / np.sqrt((1 - r.loc[x, z] ** 2) * (1 - r.loc[y, z] ** 2))
nat = {"osm_buildings_ge30m2": int(m.osm_buildings_own.sum()), "census2021_buildings": int(m.census2021_buildings.sum()),
       "ratio": round(m.osm_buildings_own.sum() / m.census2021_buildings.sum(), 3)}
summary = {"national_mainland": nat, "municipalities_matched": int(m.census2021_buildings.notna().sum()),
           "ratio_quantiles": {p: round(float(m.osm_census_ratio.quantile(p)), 3) for p in (0.05, 0.25, 0.5, 0.75, 0.95)},
           "spearman_ug_per_cabin_vs_ratio": round(float(rank.corr().loc["ug_km_per_cabin_ptd", "osm_census_ratio"]), 3),
           "partial_spearman_controlling_cabin_share": round(float(partial(rank, "ug_km_per_cabin_ptd", "osm_census_ratio", "cabin_share")), 3),
           "by_completeness_quartile": json.loads(t.round(3).to_json(orient="index")),
           "least_complete_municipalities": m.sort_values("osm_census_ratio").head(15)[["cc", "osm_buildings_own", "census2021_buildings", "osm_census_ratio", "ug_km_per_cabin_ptd"]].round(3).to_dict("records"),
           "note": "Census counts buildings (mainly residential); OSM count is >=30 m2 footprints of any use. Index, not calibration."}
(OUT / "osm_census_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False)); print(json.dumps(summary, indent=1, ensure_ascii=False))
