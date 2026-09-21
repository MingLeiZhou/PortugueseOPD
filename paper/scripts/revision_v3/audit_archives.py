#!/usr/bin/env python3
"""Read-only audit of archived scientific results; all deltas are postprocessing."""
import sys,json,hashlib,ast
from pathlib import Path
from runtime_paths import CORE_DB,INPUT_DB,PANEL,IS_RELEASE
import pandas as pd,numpy as np,duckdb,pandapower as pp
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'paper/revision_v3';OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT/'paper/scripts'));import run_nminus1_application as n1
EXT=Path('/Volumes/Transcend/PT60_public_data_2025-05-01_2026-03-24')
def sha(p):
 h=hashlib.sha256()
 with open(p,'rb') as f:
  for b in iter(lambda:f.read(2**22),b''):h.update(b)
 return h.hexdigest()
def write(d,name):pd.DataFrame(d).to_csv(OUT/(name+'.csv'),index=False)
def versions():
 c=duckdb.connect(str(CORE_DB),read_only=True);w=None if IS_RELEASE else duckdb.connect(str(INPUT_DB),read_only=True);diff=[];summ=[]
 for table,key in [('buses','bus_id'),('lines','line_id'),('transformers','transformer_id')]:
  a=c.sql('select * from grid.'+table).df().set_index(key);b=(pd.read_parquet(ROOT/'audit_inputs/working_grid'/(table+'.parquet')) if IS_RELEASE else w.sql('select * from grid.'+table).df()).set_index(key)
  summ.append({'table':table,'core_rows':len(a),'work_rows':len(b),'added':len(b.index.difference(a.index)),'removed':len(a.index.difference(b.index))})
  for id in b.index.difference(a.index):
   for col in b:diff.append({'table':table,'id':id,'field':col,'core':None,'working':b.at[id,col],'change':'ADDED'})
  for id in a.index.intersection(b.index):
   for col in a.columns.intersection(b.columns):
    x,y=a.at[id,col],b.at[id,col]
    if (pd.isna(x) and pd.isna(y)) or str(x)==str(y):continue
    if isinstance(x,(float,int)) and isinstance(y,(float,int)) and np.isclose(x,y,atol=1e-12,rtol=1e-12):continue
    diff.append({'table':table,'id':id,'field':col,'core':x,'working':y,'change':'CHANGED'})
 write(diff,'model_field_diff');write(summ,'model_version_counts');c.close();w.close() if w is not None else None
def contingencies():
 counts=[];members=[];exclusions=[];worsening=[];summary=[];sets=[]
 panel=pd.read_csv(PANEL/'annual_nminus1_panel.csv');print('PANEL COLS',panel.columns.tolist(),flush=True)
 for folder in sorted((PANEL/'screens').iterdir()):
  if not folder.is_dir():continue
  role=folder.name;net=pp.from_json(folder/'corrected_base_model.json');cases=n1.selected_contingencies(net,99999,99999);s={(c['element_type'],c['element_id']) for c in cases};sets.append(s)
  for c in cases:members.append({'role':role,**c})
  line=net.line;active=line.in_service.fillna(False);busbar=line.apply(n1.is_station_busbar,axis=1);finite=np.isfinite(net.res_line.loading_percent.reindex(line.index));zero=net.res_line.loading_percent.reindex(line.index).eq(0)
  eligible=active&~busbar&finite
  for idx,r in line.loc[~eligible].iterrows():
   exclusions.append({'role':role,'line_id':r.get('line_id'),'reason':'OUT_OF_SERVICE' if not active[idx] else 'STATION_BUSBAR' if busbar[idx] else 'MISSING_BASE_RESULT','from_bus_in_service':bool(net.bus.at[r.from_bus,'in_service']),'to_bus_in_service':bool(net.bus.at[r.to_bus,'in_service'])})
  ct=pd.DataFrame(cases);counts.append({'role':role,'bus_rows':len(net.bus),'line_rows':len(line),'line_out_of_service':int((~active).sum()),'active_busbar_excluded':int((active&busbar).sum()),'active_nonbusbar_missing_result':int((active&~busbar&~finite).sum()),'eligible_line_rows':int(eligible.sum()),'zero_current_rows_retained':int((eligible&zero).sum()),'line_outage_groups':int(ct.element_type.eq('line').sum()),'source_circuit_groups':int(ct.physical_identity_status.eq('SOURCE_CIRCUIT_ID').sum()),'osm_fragment_groups':int(ct.physical_identity_status.eq('OSM_WAY_ONLY_FRAGMENT').sum()),'line_parallel_decrement_groups':int((ct.element_type.eq('line')&ct.parallel_before_max.gt(1)).sum()),'trafo_rows':len(net.trafo),'trafo_out_of_service':int((~net.trafo.in_service).sum()),'trafo_missing_result':int((net.trafo.in_service&~np.isfinite(net.res_trafo.loading_percent)).sum()),'trafo_outage_groups':int(ct.element_type.eq('trafo').sum()),'trafo_parallel_groups':int((ct.element_type.eq('trafo')&ct.parallel_before_max.gt(1)).sum()),'equivalent_trafo_units':int(ct.loc[ct.element_type.eq('trafo'),'parallel_before_max'].sum()),'total_outages':len(ct),'set_sha256':hashlib.sha256(json.dumps(sorted(s)).encode()).hexdigest()})
  _,br=n1.line_overload_diagnostics(net,'N0');base={r['overloaded_source_line_id']:r['maximum_loading_percent'] for r in br if r['exceeds_nminus1_screen_limit']}
  post=pd.read_csv(folder/'nminus1_overloaded_physical_circuits.csv');post=post[post.confirmed_with_q_limits.fillna(False)]
  post['base_loading_percent']=post.overloaded_source_line_id.map(base);post['worsening_pp']=(post.maximum_loading_percent-post.base_loading_percent).clip(lower=0)
  details=post[post.base_loading_percent.notna()].copy();details.insert(0,'role',role);details.to_csv(OUT/('nminus1_worsening_detail_'+role+'.csv'),index=False)
  maxd=post.groupby('contingency_id').worsening_pp.max();r=pd.read_csv(folder/'nminus1_results.csv');assert set(zip(r.element_type,r.element_id))==s; r['max_existing_line_worsening_pp']=r.element_id.map(maxd).fillna(0);r.loc[~r.primary_converged,'max_existing_line_worsening_pp']=np.nan
  bmin,bmax=float(net.res_bus.vm_pu.min()),float(net.res_bus.vm_pu.max());bt=float(net.res_trafo.loading_percent.max())
  r['low_voltage_envelope_worsening_pu']=(np.maximum(.9-r.vm_pu_min,0)-max(.9-bmin,0)).clip(lower=0) if bmin<.9 else 0.
  r['high_voltage_envelope_worsening_pu']=(np.maximum(r.vm_pu_max-1.1,0)-max(bmax-1.1,0)).clip(lower=0) if bmax>1.1 else 0.
  r['transformer_envelope_worsening_pp']=(np.maximum(r.maximum_transformer_loading_percent-120,0)-max(bt-120,0)).clip(lower=0) if bt>120 else 0.
  inc=panel.loc[panel.representative_role.eq(role)].set_index('element_id').passes_incremental_contingency_screen
  r['legacy_incremental_pass']=r.element_id.map(inc);r['incremental_and_no_1pp_line_worsening']=r.legacy_incremental_pass & r.max_existing_line_worsening_pp.le(1)
  r.insert(0,'role',role);worsening.append(r)
  ok=r[r.primary_converged];summary.append({'role':role,'base_screen_overloaded_circuits':len(base),'primary_converged':len(ok),'legacy_incremental_passes':int(r.legacy_incremental_pass.sum()),'worsening_gt_0_1pp':int(ok.max_existing_line_worsening_pp.gt(.1).sum()),'worsening_gt_1pp':int(ok.max_existing_line_worsening_pp.gt(1).sum()),'worsening_gt_5pp':int(ok.max_existing_line_worsening_pp.gt(5).sum()),'max_worsening_pp':ok.max_existing_line_worsening_pp.max(),'p95_worsening_pp':ok.max_existing_line_worsening_pp.quantile(.95),'legacy_pass_but_worsening_gt1pp':int((r.legacy_incremental_pass&r.max_existing_line_worsening_pp.gt(1)).sum()),'incremental_and_no1pp_worsening':int(r.incremental_and_no_1pp_line_worsening.sum()),'base_vm_min':bmin,'base_vm_max':bmax,'base_trafo_max':bt,'max_low_envelope_worsening_pu':ok.low_voltage_envelope_worsening_pu.max(),'max_high_envelope_worsening_pu':ok.high_voltage_envelope_worsening_pu.max(),'max_trafo_envelope_worsening_pp':ok.transformer_envelope_worsening_pp.max()})
  print('N1',role,counts[-1],flush=True)
 assert all(s==sets[0] for s in sets),'Different fault universes'
 write(counts,'nminus1_universe_counts');write(members,'nminus1_membership');write(exclusions,'nminus1_exclusions');write(summary,'nminus1_worsening_summary');pd.concat(worsening).to_csv(OUT/'nminus1_case_worsening.csv',index=False)
def hotspots():
 raw=ROOT/'portuguese_hv_network/outputs/validation_experiments/raw_cases';selection=[];denom={};allids=set()
 for p in sorted(raw.glob('*/*.json')):
  o=json.loads(p.read_text())
  if o.get('status')!='COMPLETE':continue
  npz=p.with_suffix('.npz')
  with np.load(npz) as a:
   ids=a['line_id'].astype(str);x=a['loading_percent'];finite=np.flatnonzero(np.isfinite(x));order=sorted(finite,key=lambda j:(-x[j],ids[j]))[:20];allids.update(ids.tolist())
  case=o['interval']['case_id'];denom[case]=denom.get(case,0)+1
  selection.extend({'case_id':case,'variant':o['variant'],'line_id':ids[j],'loading_percent':float(x[j])} for j in order)
 d=pd.DataFrame(selection);write(selection,'top20_membership');freq=d.groupby(['case_id','line_id']).size().rename('selections').reset_index();freq['available_variants']=freq.case_id.map(denom);freq['frequency']=freq.selections/freq.available_variants;freq['class']=np.where(freq.frequency.ge(.8),'PERSISTENT_WITHIN_STATE','ASSUMPTION_DEPENDENT');freq.to_csv(OUT/'top20_frequency_by_state.csv',index=False)
 rows=[]
 for id in sorted(allids):
  z=freq[freq.line_id.eq(id)];rows.append({'line_id':id,'total_selections':int(z.selections.sum()),'total_solved_experiments':sum(denom.values()),'overall_frequency':z.selections.sum()/sum(denom.values()),'states_selected':len(z),'persistent_states':int(z.frequency.ge(.8).sum()),'max_within_state_frequency':z.frequency.max() if len(z) else 0})
 write(rows,'top20_frequency_all_lines');print('TOP20',len(d),len(freq),flush=True)
if __name__=='__main__':versions();hotspots();contingencies()
