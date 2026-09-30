#!/usr/bin/env python3
"""Fetch E-REDES open data: (1) active-contract delivery points (CPE) at MAT/AT/MT by parish, latest month;
(2) parish polygons (civil-parishes-portugal-v2, CAOP 2024). Output: data/raw/eredes_mv_customers/"""
import json, urllib.request, datetime
from pathlib import Path
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]; D = ROOT / "data/raw/eredes_mv_customers"; D.mkdir(parents=True, exist_ok=True)
B = "https://e-redes.opendatasoft.com/api/explore/v2.1/catalog/datasets"
get = lambda u: json.loads(urllib.request.urlopen(u, timeout=300).read())
ds = "20-caracterizacao-pes-contrato-ativo"
last = get(f"{B}/{ds}/records?select=max(data)%20as%20d")["results"][0]["d"][:10]
q = (f"{B}/{ds}/exports/json?select=coddistritoconcelhofreguesia%20as%20fre_code,freguesia,tipo_de_instalacao,cpes"
     f"&where=data%3Ddate%27{last}%27%20and%20nivel_de_tensao%20like%20%22Muito%25%22")
rows = get(q); df = pd.DataFrame(rows)
p = df.groupby("fre_code", as_index=False, dropna=False).agg(freguesia=("freguesia", "first"), cpes=("cpes", "sum")).rename(columns={"cpes": "mat_at_mt_cpes"})
p.to_csv(D / f"mat_at_mt_cpes_by_parish_{last[:7]}.csv", index=False)
gj = urllib.request.urlopen(f"{B}/civil-parishes-portugal-v2/exports/geojson", timeout=600).read()
(D / "parishes_caop2024.geojson").write_bytes(gj)
(D / "SOURCE_parish.md").write_text(f"""# E-REDES — MAT/AT/MT delivery points by parish

- Dataset `{ds}` (Caracterização dos CPEs com contrato ativo), month {last[:7]}, nivel_de_tensao = 'Muito Alta, Alta e Média Tensões', all installation types; summed per parish.
- Total {int(p.mat_at_mt_cpes.sum())} CPEs in {len(p)} parishes. Includes the few MAT/AT customers (not separable in this dataset).
- Parish polygons: dataset `civil-parishes-portugal-v2` (CAOP 2024), GeoJSON export.
- Retrieved {datetime.date.today()} from {B}. Licence: E-REDES open data (CC BY 4.0).
""")
print(last, len(p), int(p.mat_at_mt_cpes.sum()), len(gj) // 1e6, "MB")
