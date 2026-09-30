#!/usr/bin/env python3
"""Fetch INE Censos 2021 building counts by municipality (run on a machine with access to www.ine.pt).
1) downloads the INE indicator catalogue and lists indicators whose title mentions buildings + Censos 2021
2) downloads the chosen indicator (auto: shortest matching title, or --varcd) at municipality level
Output: data/raw/ine_census2021/buildings_by_municipality.csv (+ raw JSON, catalogue matches, SOURCE.md)"""
import argparse, json, re, subprocess, sys, unicodedata
from datetime import date
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]; OUT = ROOT / "data/raw/ine_census2021"; OUT.mkdir(parents=True, exist_ok=True)

def get(url, dest):
    subprocess.run(["curl", "-sS", "-L", "--retry", "3", "-m", "600", "-o", str(dest), url], check=True); return dest

def norm(s): return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--varcd"); a = ap.parse_args()
    varcd = a.varcd
    if not varcd:
        cat = OUT / "ine_catalogue.xml"
        if not cat.exists() or cat.stat().st_size < 1000:
            print("downloading INE catalogue (large, a few minutes)..."); get("https://www.ine.pt/ine/xml_indic.jsp?opc=3&lang=PT", cat)
        cands = []
        for _, el in ET.iterparse(cat):
            if el.tag.lower() != "indicator": continue
            f = {c.tag.lower(): (c.text or "") for c in el}
            t = norm(f.get("title", ""))
            if t.startswith("edificios") and "censos" in t and "2021" in (t + norm(f.get("dates", ""))):
                cands.append((f.get("varcd") or el.get("id") or "", f.get("title", "")))
            el.clear()
        (OUT / "catalogue_matches.json").write_text(json.dumps(cands, indent=1, ensure_ascii=False))
        for c in cands: print(c)
        if not cands: sys.exit("no candidates found; inspect ine_catalogue.xml and pass --varcd")
        cands.sort(key=lambda c: len(c[1])); varcd = cands[0][0]; print("chosen:", cands[0])
    raw = OUT / f"ine_{varcd}_S7A2021.json"
    get(f"https://www.ine.pt/ine/json_indicador/pindica.jsp?op=2&varcd={varcd}&Dim1=S7A2021&lang=PT", raw)
    d = json.loads(raw.read_text())[0]; title = d.get("IndicadorDsg", "")
    rows = []
    for yr, recs in d["Dados"].items():
        for r in recs:
            extra = {k: v for k, v in r.items() if re.match(r"dim_\d+$", k)}
            if any(str(v) not in ("T", "Total") for v in extra.values()):
                extra_t = {k: r.get(k + "_t", "") for k in extra}
                if any(norm(v) not in ("total", "") for v in extra_t.values()): continue
            g = r.get("geocod", "")
            if len(g) == 7 and r.get("valor") not in (None, "", "x"):
                rows.append((g[-4:], r.get("geodsg", ""), int(float(r["valor"]))))
    rows = sorted(set(rows))
    with open(OUT / "buildings_by_municipality.csv", "w") as f:
        f.write("dico,municipality,census2021_buildings\n"); f.writelines(f'{c},"{n}",{v}\n' for c, n, v in rows)
    (OUT / "SOURCE.md").write_text(f"# INE Censos 2021 — buildings by municipality\n\n- Indicator: {varcd} — {title}\n- API: https://www.ine.pt/ine/json_indicador/pindica.jsp?op=2&varcd={varcd}&Dim1=S7A2021&lang=PT\n- Retrieved: {date.today()}\n- Municipality rows: {len(rows)} (geocod length 7; DICO = last 4 digits)\n- Licence: INE open data (reuse with attribution)\n")
    print(f"{len(rows)} municipalities -> {OUT/'buildings_by_municipality.csv'}  ({title})")

if __name__ == "__main__":
    main()
