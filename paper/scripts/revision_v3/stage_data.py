#!/usr/bin/env python3
from pathlib import Path
import json,hashlib,subprocess,duckdb,shutil,platform,sys,importlib.metadata
ROOT=Path(__file__).resolve().parents[3];VERSION='SimPT60-2026.09.21-r1';OUT=ROOT/'data/releases'/VERSION;OUT.mkdir(parents=True,exist_ok=True);ASSETS=OUT/'assets';ASSETS.mkdir(exist_ok=True);PKG=OUT/'bundle';PKG.mkdir(exist_ok=True)
EXT=Path('/Volumes/Transcend/PT60_public_data_2025-05-01_2026-03-24')
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(2**23),b''):h.update(b)
 return h.hexdigest()
entries=[]
for p in ([] if (ASSETS/'MONTHLY_MANIFEST.json').exists() else sorted((EXT/'monthly_models').glob('*/*.compact.duckdb'))):
 if p.name.startswith('._'):continue
 target=ASSETS/(p.name+'.zst')
 if not target.exists():subprocess.run(['/opt/homebrew/bin/zstd','-3','-T2',str(p),'-o',str(target)],check=True)
 con=duckdb.connect(str(p),read_only=True);n=con.sql("select count(*) from monthly_model.cases where status='COMPLETE'").fetchone()[0];manifest=con.sql('select * from monthly_model.run_manifest').df().to_dict('records');con.close()
 entries.append({'month':p.parent.name,'file':target.name,'bytes':target.stat().st_size,'sha256':sha(target),'uncompressed_file':p.name,'uncompressed_sha256':sha(p),'cases':n,'run_manifest':manifest});print('FROZEN',p.parent.name,n,flush=True)
if entries:(ASSETS/'MONTHLY_MANIFEST.json').write_text(json.dumps(entries,indent=2,default=str))
db=PKG/'inputs.duckdb'
if not db.exists():
 c=duckdb.connect(str(db));c.execute(f"ATTACH '{EXT/'pt60_public_timeseries.duckdb'}' AS working (READ_ONLY)");first=EXT/'monthly_models/2025-05/pt60_models_2025-05.compact.duckdb';c.execute(f"ATTACH '{first}' AS core (READ_ONLY)")
 for name in ['ren_dispatch','interval_calendar','eredes_load','weather_hourly','ren_rnt_balance','dataset_files','coverage_summary','eredes_snapshot_totals']:
  c.execute(f'create table main.{name} as select * from working.main.{name}')
 for schema in ['grid','geo','provenance']:
  c.execute(f'create schema {schema}')
  for (name,) in c.execute("select table_name from information_schema.tables where table_catalog='core' and table_schema=?",[schema]).fetchall():c.execute(f'create table {schema}."{name}" as select * from core.{schema}."{name}"')
 c.execute('checkpoint');c.close()
for rel in ['portuguese_hv_network/outputs/model/portuguese_hv_candidate.json','portuguese_hv_network/outputs/tables/generators.csv','portuguese_hv_network/outputs/tables/pdirt_annex12_2025_pde_loads.csv','portuguese_hv_network/data/raw/eredes/temporal_validation/substation_capacity_2025.json','LICENSE','DATA_LICENSE.md']:
 src=ROOT/rel;dst=PKG/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
for rel in ['portuguese_hv_network/src','portuguese_hv_network/config']:
 shutil.copytree(ROOT/rel,PKG/rel,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','.DS_Store'))
shutil.copytree(ROOT/'portuguese_hv_network/outputs/annual_nminus1_panel_v2',PKG/'archived_nminus1',dirs_exist_ok=True,ignore=shutil.ignore_patterns('*.png','*.pdf','*.svg','*.log'))
packages={d.metadata['Name']:d.version for d in importlib.metadata.distributions()};(PKG/'environment.json').write_text(json.dumps({'python':sys.version,'platform':platform.platform(),'packages':packages},indent=2));(PKG/'requirements-lock.txt').write_text('\n'.join(f'{k}=={v}' for k,v in sorted(packages.items(),key=lambda kv:kv[0].lower()))+'\n')
print('STAGED',OUT,flush=True)
