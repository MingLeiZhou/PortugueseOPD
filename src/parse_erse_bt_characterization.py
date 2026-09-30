#!/usr/bin/env python3
"""Parse ERSE 'Caracterização das redes de distribuição de energia elétrica em BT em Portugal Continental' (data 31-12-2016):
LV network km (overhead / underground), PTs and customers per NUTS III (CIM / AM) region, with the member municipalities.
Output: data/raw/erse_bt_characterization/erse_bt_by_nuts3_2016.csv, erse_bt_municipality_region_2016.csv"""
import re, subprocess, unicodedata
from pathlib import Path
import duckdb, pandas as pd
ROOT = Path(__file__).resolve().parents[1]; D = ROOT / "data/raw/erse_bt_characterization"
txt = subprocess.run(["pdftotext", "-layout", str(D / "guia_caracterizacao_redes_bt_2016.pdf"), "-"], capture_output=True, text=True, check=True).stdout
norm = lambda s: re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower())
def num(pg, lab):
    m = re.search(lab + r"\s+(?:km\s+)?([\d ]+?)\s*$", pg, re.M); return int(m.group(1).replace(" ", "")) if m else None
regions, members = [], []
for pg in txt.split("\f"):
    m = re.search(r"Dados de caracteriza\S+ da rede\s+(.+)", pg)
    if not m: continue
    code = m.group(1).strip()
    rec = {"region_code": code, "lv_km": num(pg, r"Rede BT"), "overhead_km": num(pg, r"\(aérea\)"), "underground_km": num(pg, r"\(subterrânea\)"),
           "pts": num(pg, r"n\.º PTs"), "customers": num(pg, r"n\.º clientes")}
    names = " ".join(l.strip() for l in pg.split("\n") if "|" in l)
    items = [re.sub(r"\s+", " ", x).strip(" /") for x in names.split("|")]
    items = [x for x in items if x]
    is_leaf = not any(x.startswith(("CIM", "AMP")) or x in ("Alentejo", "Algarve", "Norte", "Centro") or x.startswith("Área Metro") for x in items)
    rec["level"] = "nuts3" if is_leaf and items else "nuts2/country"
    regions.append(rec)
    if rec["level"] == "nuts3":
        members += [{"region_code": code, "municipality": x} for x in items]
reg = pd.DataFrame(regions).drop_duplicates()
mem = pd.DataFrame(members)
db = duckdb.connect(str(ROOT / "output/all_voltage/all_voltage_staging.duckdb"), read_only=True)
mun = db.sql("select substr(ptd_code,1,4) dico, any_value(municipality_name) AS mname from candidate.ptd_public_attributes group by 1").df()
key = {norm(n): c for c, n in zip(mun.dico, mun.mname)}
alias = {norm("Ponte de Sôr"): norm("Ponte de Sor"), norm("Pedrogão Grande"): norm("Pedrógão Grande")}
mem["dico"] = [key.get(alias.get(norm(x), norm(x))) for x in mem.municipality]
reg.to_csv(D / "erse_bt_by_nuts3_2016.csv", index=False); mem.to_csv(D / "erse_bt_municipality_region_2016.csv", index=False)
print(reg.to_string(index=False)); print("members", len(mem), "unmatched:", mem[mem.dico.isna()].municipality.tolist(),
      "dup dico:", mem.dico[mem.dico.duplicated()].tolist(), "PTD municipalities not covered:", sorted(set(mun.dico) - set(mem.dico)))
