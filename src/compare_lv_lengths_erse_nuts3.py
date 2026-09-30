#!/usr/bin/env python3
"""Regional (NUTS III) validation of LV lengths: national LV geographic run vs ERSE BT characterisation (31-12-2016).
No calibration. Because the reference is 2016 (national LV +5.7% to 2024), compare both km and the underground share."""
import json, sys
from pathlib import Path
import numpy as np, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; D = ROOT / "data/raw/erse_bt_characterization"; OUT = ROOT / "output/all_voltage/lv_geo_national/diagnosis"
reg = pd.read_csv(D / "erse_bt_by_nuts3_2016.csv"); reg = reg[reg.level == "nuts3"]
mem = pd.read_csv(D / "erse_bt_municipality_region_2016.csv", dtype={"dico": str})
RUN = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "output/all_voltage/lv_geo_national"; TAG = "" if len(sys.argv) < 2 else "_" + RUN.name
nat = pd.read_csv(RUN / "lv_geo_powerflow_national.csv"); nat["dico"] = nat.ptd_code.str[:4]
nat = nat.merge(mem[["dico", "region_code"]], on="dico")
cab = ~nat.ctype.astype(str).str.startswith("Aéreo")
ext = nat["pole_ext_km"] if "pole_ext_km" in nat else 0
nat["m_oh"] = nat.overhead_km + np.where(cab, 0, nat.street_km) + ext; nat["m_ug"] = np.where(cab, nat.street_km, 0)
ptd = pd.read_csv(OUT / "unsolved_ptds.csv", dtype={"cc": str})
g = nat.groupby("region_code").agg(model_ptds=("ptd_code", "size"), model_oh_km=("m_oh", "sum"), model_ug_km=("m_ug", "sum"), model_drops_km=("drops_km", "sum")).reset_index()
t = reg.merge(g, on="region_code")
t["oh_ratio"] = t.model_oh_km / t.overhead_km; t["ug_ratio"] = t.model_ug_km / t.underground_km
t["erse_ug_share"] = t.underground_km / t.lv_km; t["model_ug_share"] = t.model_ug_km / (t.model_oh_km + t.model_ug_km)
t["total_ratio"] = (t.model_oh_km + t.model_ug_km) / t.lv_km
t = t.round(3).sort_values("ug_ratio"); t.to_csv(OUT / f"lv_lengths_vs_erse_nuts3_2016{TAG}.csv", index=False)
cols = ["region_code", "lv_km", "overhead_km", "underground_km", "model_oh_km", "model_ug_km", "oh_ratio", "ug_ratio", "total_ratio", "erse_ug_share", "model_ug_share"]
print(t[cols].to_string(index=False))
r = t[["erse_ug_share", "model_ug_share"]].corr("spearman").iloc[0, 1]
s = {"reference": "ERSE, Caracterização das redes de distribuição de energia elétrica em BT em Portugal Continental (data 31-12-2016), 23 NUTS III",
     "erse_totals_2016": {"overhead": int(t.overhead_km.sum()), "underground": int(t.underground_km.sum())},
     "model_totals": {"overhead": round(t.model_oh_km.sum()), "underground": round(t.model_ug_km.sum())},
     "spearman_ug_share": round(float(r), 3),
     "ratio_ranges": {k: [float(t[k].min()), float(t[k].median()), float(t[k].max())] for k in ("oh_ratio", "ug_ratio", "total_ratio")}}
(OUT / f"lv_lengths_vs_erse_nuts3_summary{TAG}.json").write_text(json.dumps(s, indent=2, ensure_ascii=False)); print(json.dumps(s, indent=1, ensure_ascii=False))
