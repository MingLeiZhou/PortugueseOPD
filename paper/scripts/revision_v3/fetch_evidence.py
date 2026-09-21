from pathlib import Path
import requests,json,hashlib,pymupdf
OUT=Path(__file__).resolve().parents[2]/'revision_v3/evidence';OUT.mkdir(parents=True,exist_ok=True)
urls={'estoi_pdirt_2015.pdf':'https://www.erse.pt/media/b1edmm30/proposta_pdirt_e_2015_anexos.pdf','ree_interconexiones.pdf':'https://www.ree.es/sites/default/files/jgk4byy3ukct.pdf','apa_AIA3403.pdf':'https://siaia.apambiente.pt/AIADOC/AIA3403/parecerca_3403202192134944.pdf','tocha_II_2019.pdf':'https://siaia.apambiente.pt/AIADOC/AIA3274/projeto%20linha%20eletrica%20pe%20tocha%20ii2019729153214.pdf','dgeg_casal_2025.html':'https://www.dgeg.gov.pt/pt/areas-setoriais/energia/energia-eletrica/atividades-eventos/','apa_ppa421.html':'https://siaia.apambiente.pt/PosAvaliacao/DetalhesPosAvaliacao/421','apa_ppa407.html':'https://siaia.apambiente.pt/PosAvaliacao/DetalhesPosAvaliacao/407','geopandas_CITATION.md':'https://raw.githubusercontent.com/geopandas/geopandas/main/CITATION.md','geopandas_crossref.json':'https://api.crossref.org/works/10.1016/j.compenvurbsys.2026.102495'}
rows=[]
for name,url in urls.items():
 try:
  p=OUT/name
  if not p.exists():
   r=requests.get(url,timeout=35);r.raise_for_status();p.write_bytes(r.content)
  rows.append({'file':name,'url':url,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'accessed':'2026-09-21'})
  if name.endswith('.pdf'):
   doc=pymupdf.open(p);print(name,len(doc),flush=True)
   for i,page in enumerate(doc):
    t=page.get_text()
    if any(s in t.lower() for s in ['estoi','armazenamento','bateria','cartelle','resistência']):(OUT/f'{name}.page{i+1}.txt').write_text(t);print('match',i+1,flush=True)
 except Exception as e:rows.append({'file':name,'url':url,'error':str(e)});print(name,str(e),flush=True)
(OUT/'download_manifest.json').write_text(json.dumps(rows,indent=2))
