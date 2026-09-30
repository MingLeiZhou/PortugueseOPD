#!/usr/bin/env python3
"""Diagnose the national LV geographic run: (1) which PTDs were not solved and why,
(2) where the underground-length shortfall sits (by municipality, urbanity, OSM building completeness).
Read-only on the working DB. Output: output/all_voltage/lv_geo_national/diagnosis/"""
import json
from pathlib import Path
import duckdb, numpy as np, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "output/all_voltage/lv_geo_national"; OUT = BASE / "diagnosis"; OUT.mkdir(exist_ok=True)
con = duckdb.connect(str(ROOT / "output/all_voltage/all_voltage_staging.duckdb"), read_only=True)
ptd = con.sql("""select a.ptd_code, a.ptd_lon lon, a.ptd_lat lat, coalesce(p.construction_type,'') ctype
                 from candidate.ptd_mv_attachment_assessment a left join candidate.ptd_public_attributes p using(ptd_code)""").df()
ptd["cc"] = ptd.ptd_code.str[:4]
have = {}
for name, q in {"design": "select distinct ptd_code from phase.lv_feeder_root_peak_design_scenario",
                "transformer": """select t.ptd_code from parameter.ptd_transformer_parameters t join parameter.ptd_transformer_root_peak_design_scenario d using(ptd_code)
                                  join phase.ptd_neutral_grounding g using(ptd_code)""",
                "shares": "select distinct ptd_code from phase.lv_load_shares"}.items():
    have[name] = set(con.sql(q).df().ptd_code)
have["snapshot"] = set(pd.read_csv(ROOT / "output/all_voltage/four_wire_root_peak_design/ptd_snapshot.csv", usecols=["ptd_code"]).ptd_code)
nat = pd.read_csv(BASE / "lv_geo_powerflow_national.csv")
solved = set(nat.ptd_code)

def reason(c):
    if c in solved: return "SOLVED"
    miss = [k for k in ("snapshot", "design", "transformer", "shares") if c not in have[k]]
    return "NO_LOAD_OR_DESIGN_DATA:" + "+".join(miss) if miss else "NO_POLE_OR_BUILDING_WITHIN_RADIUS"
ptd["status"] = ptd.ptd_code.map(reason)
ptd["aerial"] = ptd.ctype.str.startswith("Aéreo")
unsolved = ptd[ptd.status != "SOLVED"]
unsolved.to_csv(OUT / "unsolved_ptds.csv", index=False)

# building counts per municipality from the OSM extract
bcount = {}
for f in (BASE / "osm").glob("osm_*.json"):
    d = json.loads(f.read_text()); bcount[f.stem[4:]] = sum(1 for b in d["buildings"] if b.get("a", 0) >= 30)
m = ptd.groupby("cc").agg(ptds=("ptd_code", "size"), aerial_share=("aerial", "mean"),
                          unsolved=("status", lambda s: int((s != "SOLVED").sum()))).reset_index()
nat["cc"] = nat.ptd_code.str[:4]; nat["aerial"] = nat.ctype.astype(str).str.startswith("Aéreo")
g = nat.groupby("cc").apply(lambda d: pd.Series({
    "pole_km": d.overhead_km.sum(), "street_aerial_km": d.loc[d.aerial, "street_km"].sum(),
    "street_cabin_km": d.loc[~d.aerial, "street_km"].sum(), "cabin_ptds": int((~d.aerial).sum()),
    "load_mw": d.load_kw.sum() / 1000}), include_groups=False).reset_index()
m = m.merge(g, on="cc", how="left"); m["osm_buildings"] = m.cc.map(bcount)
m["buildings_per_ptd"] = m.osm_buildings / m.ptds
m["ug_km_per_cabin_ptd"] = m.street_cabin_km / m.cabin_ptds.replace(0, np.nan)
m["mw_per_ptd"] = m.load_mw / m.ptds
m.to_csv(OUT / "municipality_diagnosis.csv", index=False)

# urbanity classes by PTD load density proxy: cabin share
m["class"] = pd.cut(1 - m.aerial_share, [-.01, .3, .6, 1.01], labels=["rural(<30% cabin)", "mixed(30-60%)", "urban(>60% cabin)"])
cls = m.groupby("class", observed=True).agg(municipalities=("cc", "size"), ptds=("ptds", "sum"), cabin_ptds=("cabin_ptds", "sum"),
       ug_km=("street_cabin_km", "sum"), buildings=("osm_buildings", "sum"), unsolved=("unsolved", "sum"))
cls["ug_km_per_cabin_ptd"] = cls.ug_km / cls.cabin_ptds; cls["buildings_per_ptd"] = cls.buildings / cls.ptds
# per-PTD relation: underground km of cabin PTDs vs building count density quartile
mm = m.dropna(subset=["ug_km_per_cabin_ptd"])
q = pd.qcut(mm.buildings_per_ptd, 4, labels=["Q1 lowest", "Q2", "Q3", "Q4 highest"])
bq = mm.groupby(q, observed=True).agg(municipalities=("cc", "size"), bpp_min=("buildings_per_ptd", "min"), bpp_max=("buildings_per_ptd", "max"),
        cabin_ptds=("cabin_ptds", "sum"), ug_km=("street_cabin_km", "sum"))
bq["ug_km_per_cabin_ptd"] = bq.ug_km / bq.cabin_ptds
summary = {
    "ptds_in_register": int(len(ptd)), "solved": int((ptd.status == "SOLVED").sum()),
    "unsolved_by_reason": ptd[ptd.status != "SOLVED"].status.value_counts().to_dict(),
    "unsolved_by_type": unsolved.ctype.replace("", "(blank)").value_counts().head(8).to_dict(),
    "unsolved_top_municipalities": m.sort_values("unsolved", ascending=False).head(10)[["cc", "ptds", "unsolved"]].to_dict("records"),
    "spearman_ug_per_cabin_vs_buildings_per_ptd": round(float(mm[["ug_km_per_cabin_ptd", "buildings_per_ptd"]].corr("spearman").iloc[0, 1]), 3),
    "by_urbanity": json.loads(cls.round(3).to_json(orient="index")),
    "by_building_density_quartile": json.loads(bq.round(3).to_json(orient="index")),
    "lowest_ug_per_cabin_ptd_large_urban": m[m.cabin_ptds >= 200].sort_values("ug_km_per_cabin_ptd").head(10)[
        ["cc", "cabin_ptds", "ug_km_per_cabin_ptd", "buildings_per_ptd"]].round(3).to_dict("records"),
}
(OUT / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False)); print(json.dumps(summary, indent=1, ensure_ascii=False))
