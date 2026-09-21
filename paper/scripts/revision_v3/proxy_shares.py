#!/usr/bin/env python3
"""Energy-weighted provenance shares from every archived case (15 minutes)."""
from pathlib import Path
from runtime_paths import MONTHLY_ROOT
import json,collections,duckdb,pandas as pd,numpy as np
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'paper/revision_v3';EXT=MONTHLY_ROOT
fields=['eredes_profile_load_mw','ren_minus_eredes_rnt_load_proxy_mw','observed_consumption_mw','observed_load_mw','ren_pumping_load_mw','mapped_battery_consumption_mw','unmapped_battery_consumption_residual_mw','mapped_asset_generation_mw','unmapped_generation_residual_mw','total_modeled_generation_mw']
loads=[];gens=[];regional=[];inventory=[]
for p in sorted(EXT.glob('*/*.compact.duckdb')):
 if p.name.startswith('._'):continue
 month=p.parent.name;cache=OUT/'proxy_month_cache'/month;cache.mkdir(parents=True,exist_ok=True)
 if (cache/'done.json').exists():
  loads.append(pd.read_csv(cache/'load.csv'));gens.append(pd.read_csv(cache/'generation.csv'));regional.append(pd.read_csv(cache/'regional.csv'));inventory.append(json.loads((cache/'done.json').read_text()));continue
 c=duckdb.connect(str(p),read_only=True);c.execute("SET memory_limit='2GB'");c.execute('SET threads=2')
 b=c.sql('select bus_id,lat from grid.buses').df();lat=b.set_index('bus_id').lat.to_dict()
 def region(id):
  x=lat.get(id,np.nan)
  return 'UNKNOWN' if pd.isna(x) else 'NORTH_ge40.5N' if x>=40.5 else 'CENTRE_38.5to40.5N' if x>=38.5 else 'SOUTH_lt38.5N'
 q='select case_id,timestamp_utc,local_date,'+','.join("try_cast(json_extract(result_json,'$."+k+"') as double) as "+k for k in fields)+' from monthly_model.cases where status=\'COMPLETE\''
 ld=c.sql(q).df();ld['month']=month;ld['observed_consumption_share']=ld.eredes_profile_load_mw/ld.observed_consumption_mw;ld['pdirt_residual_consumption_share']=ld.ren_minus_eredes_rnt_load_proxy_mw/ld.observed_consumption_mw;ld['generation_proxy_share']=ld.unmapped_generation_residual_mw/ld.total_modeled_generation_mw.replace(0,np.nan);ld['high_load_proxy']=ld.pdirt_residual_consumption_share.ge(.5);ld['high_generation_proxy']=ld.generation_proxy_share.ge(.1)
 ld.to_csv(cache/'load.csv',index=False);g=[];sums=collections.defaultdict(float);counts=collections.defaultdict(int)
 cur=c.execute('select a.case_id,c.timestamp_utc,a.records from monthly_model.audit_bundles a join monthly_model.cases c using(case_id) where c.status=\'COMPLETE\'')
 while True:
  batch=cur.fetchmany(32)
  if not batch:break
  for case,ts,raw in batch:
   for rec in json.loads(raw):
    x=rec['payload'];typ=rec['record_type']
    if typ=='GENERATION_SOURCE':
     observed=float(x['ren_observed_mw']);proxy=float(x['unmapped_residual_mw']);g.append({'month':month,'case_id':case,'timestamp_utc':ts,'energy':x['generation_source'],'observed_mw':observed,'mapped_mw':x['mapped_asset_input_mw'],'proxy_mw':proxy,'proxy_share':proxy/observed if observed else np.nan,'proxy_bus_id':x['residual_bus_id'],'proxy_receiving_region':region(x['residual_bus_id']) if proxy else 'NONE','high_proxy_ge10pct':bool(observed>0 and proxy/observed>=.1)})
    elif typ=='LOAD_ALLOCATION':
     bus=x.get('bus_id','');status=x.get('source_status','');mw=float(x.get('applied_p_mw',0) or 0)
     kind='DIRECT_OBSERVED' if 'DIRECT_SYNCHRONIZED' in status else 'PDIRT_RESIDUAL' if 'PDIRT' in status and x.get('mapped_to_pt60') else 'OTHER_OR_STORAGE'
     key=(region(bus),kind);sums[key]+=mw*.25;counts[key]+=1
     if mw>0:sums[('BUS:'+bus,kind)]+=mw*.25;counts[('BUS:'+bus,kind)]+=1
 reg=pd.DataFrame([{'month':month,'receiving_area_or_bus':r,'category':k,'energy_mwh':v,'records':counts[(r,k)]} for (r,k),v in sums.items()]);gd=pd.DataFrame(g)
 gd.to_csv(cache/'generation.csv',index=False);reg.to_csv(cache/'regional.csv',index=False)
 info={'month':month,'cases':len(ld),'buses':len(b),'source_model_hash':c.sql('select distinct source_model_sha256 from monthly_model.run_manifest').fetchall()};(cache/'done.json').write_text(json.dumps(info))
 loads.append(ld);gens.append(gd);regional.append(reg);inventory.append(info);c.close();print(month,len(ld),len(g),flush=True)
l=pd.concat(loads);g=pd.concat(gens);r=pd.concat(regional);l.to_csv(OUT/'load_proxy_shares_all_intervals.csv',index=False);g.to_csv(OUT/'generation_proxy_shares_all_intervals.csv',index=False);r.to_csv(OUT/'proxy_energy_by_region_and_bus.csv',index=False)
rows=[]
for month,z in l.groupby('month'):
 rows.append({'month':month,'cases':len(z),'observed_load_energy_mwh':z.eredes_profile_load_mw.sum()*.25,'pdirt_residual_energy_mwh':z.ren_minus_eredes_rnt_load_proxy_mw.sum()*.25,'direct_share_of_consumption':z.eredes_profile_load_mw.sum()/z.observed_consumption_mw.sum(),'pdirt_share_of_consumption':z.ren_minus_eredes_rnt_load_proxy_mw.sum()/z.observed_consumption_mw.sum(),'generation_proxy_share':z.unmapped_generation_residual_mw.sum()/z.total_modeled_generation_mw.sum(),'load_proxy_max_share':z.pdirt_residual_consumption_share.max(),'generation_proxy_max_share':z.generation_proxy_share.max(),'high_load_proxy_intervals':int(z.high_load_proxy.sum()),'high_generation_proxy_intervals':int(z.high_generation_proxy.sum()),'max_load_partition_error_mw':(z.eredes_profile_load_mw+z.ren_minus_eredes_rnt_load_proxy_mw-z.observed_consumption_mw).abs().max()})
pd.DataFrame(rows).to_csv(OUT/'proxy_monthly_summary.csv',index=False)
a=g.groupby(['month','energy']).agg(observed_mwh=('observed_mw',lambda x:x.sum()*.25),mapped_mwh=('mapped_mw',lambda x:x.sum()*.25),proxy_mwh=('proxy_mw',lambda x:x.sum()*.25),max_proxy_share=('proxy_share','max'),high_proxy_intervals=('high_proxy_ge10pct','sum')).reset_index();a['energy_weighted_proxy_share']=a.proxy_mwh/a.observed_mwh.replace(0,np.nan);a.to_csv(OUT/'generation_proxy_monthly_by_energy.csv',index=False)
r=r.pivot_table(index=['month','receiving_area_or_bus'],columns='category',values='energy_mwh',aggfunc='sum',fill_value=0).reset_index()
for col in ['DIRECT_OBSERVED','PDIRT_RESIDUAL']:
 if col not in r:r[col]=0.
r['pdirt_share_of_consumption']=r.PDIRT_RESIDUAL/(r.PDIRT_RESIDUAL+r.DIRECT_OBSERVED).replace(0,np.nan);r['high_proxy_ge50pct']=r.pdirt_share_of_consumption.ge(.5);r.to_csv(OUT/'load_proxy_regional_shares.csv',index=False)
l[l.high_load_proxy|l.high_generation_proxy].to_csv(OUT/'high_proxy_intervals.csv',index=False);(OUT/'monthly_inventory.json').write_text(json.dumps(inventory,indent=2));print('TOTAL',len(l),len(g),flush=True)
