#!/usr/bin/env python3
"""Default-neutral, predeclared operational-proxy ablations on 22 archived states."""
import sys, json, time, argparse
from pathlib import Path
from runtime_paths import INPUT_DB
from concurrent.futures import ProcessPoolExecutor, as_completed
ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT/'portuguese_hv_network/src'))
import run_simpt60_validation_experiments as v
from run_monthly_15min import _case_from_interval
from run_temporal_validation import run_case, MODEL_INPUT
import pandas as pd
OUT=ROOT/'paper/revision_v3/operational_ablations'
VARIANTS={'BASELINE':{}, 'Q_HALF':{'q_limit_scale':0.5}, 'Q_150_PERCENT':{'q_limit_scale':1.5},
 'PV_099':{'pv_voltage_target_pu':0.99},'PV_101':{'pv_voltage_target_pu':1.01},
 'NO_LOAD_COMPENSATION':{'disable_load_compensation':True},'NO_REACTORS':{'disable_proxy_reactors':True},
 'FIXED_NEUTRAL_TAPS':{'disable_tap_control':True},
 'NO_COMPENSATION_FIXED_TAPS':{'disable_load_compensation':True,'disable_proxy_reactors':True,'disable_tap_control':True},
 'RATIO_TAP_CONTROL':{'enable_ratio_tap_model':True},
 'RATIO_TAP_FIXED_NEUTRAL':{'enable_ratio_tap_model':True,'disable_tap_control':True}}
def solve(task):
 interval,name=task; path=OUT/'raw_cases'/name/(interval['case_id']+'.json')
 if path.exists(): return json.loads(path.read_text())
 state=path.with_suffix('.npz');path.parent.mkdir(parents=True,exist_ok=True)
 case=_case_from_interval(interval,state);case.update(VARIANTS[name]);case['case_id']+='__'+name
 start=time.monotonic()
 payload={'variant':name,'interval':interval,'settings':VARIANTS[name],'state_path':str(state)}
 try:
  result,_,hotspots,*_=run_case(case,v._GENERATORS,False,save_solved=False,observation_override=v._INPUTS.observation(interval),load_snapshot_override=v._INPUTS.load_snapshot(interval),weather_override=v._INPUTS.weather(interval),rnt_loss_override=v._INPUTS.rnt_loss(case),model_input=MODEL_INPUT,output_dir=OUT)
  payload.update(status='COMPLETE',result=result,top_lines=hotspots)
 except Exception as e: payload.update(status='FAILED',error=f'{type(e).__name__}: {e}')
 payload['elapsed_seconds']=time.monotonic()-start;v.write_json(path,payload)
 return payload
if __name__=='__main__':
 ap=argparse.ArgumentParser();ap.add_argument('--workers',type=int,default=4);ap.add_argument('--limit',type=int);ap.add_argument('--database',type=Path,default=INPUT_DB);a=ap.parse_args()
 intervals=v.selected_intervals(a.database,a.limit);OUT.mkdir(parents=True,exist_ok=True)
 v.write_json(OUT/'protocol.json',{'variants':VARIANTS,'intervals':intervals,'failed_cases':'retained; no unconstrained-Q fallback','thresholds':{'voltage_pu':[0.9,1.1],'line_percent':100,'trafo_percent':120}})
 rows=[]
 with ProcessPoolExecutor(max_workers=a.workers,initializer=v.worker_init,initargs=(str(a.database),)) as pool:
  for f in as_completed([pool.submit(solve,(i,k)) for i in intervals for k in VARIANTS]):
   r=f.result();rows.append(r);print(r['variant'],r['interval']['timestamp_utc'],r['status'],round(r['elapsed_seconds'],2),flush=True)
 records=[]
 for r in rows:
  res=r.get('result',{});records.append({'variant':r['variant'],'timestamp_utc':r['interval']['timestamp_utc'],'status':r['status'],'error':r.get('error',''),**{k:res.get(k) for k in ['vm_pu_min','vm_pu_max','maximum_line_loading_percent','maximum_transformer_loading_percent','model_total_pt60_losses_mw','model_net_import_mw','converged']}})
 d=pd.DataFrame(records).sort_values(['variant','timestamp_utc']);d.to_csv(OUT/'case_results.csv',index=False)
 base=d[d.variant.eq('BASELINE')].set_index('timestamp_utc');summ=[]
 for name,g in d.groupby('variant'):
  z=g.set_index('timestamp_utc');ok=z.status.eq('COMPLETE') & base.status.eq('COMPLETE');q=z.loc[ok];b=base.loc[ok]
  row={'variant':name,'attempts':len(z),'converged':int(ok.sum()),'failures':int(z.status.ne('COMPLETE').sum())}
  for field in ['vm_pu_min','vm_pu_max','maximum_line_loading_percent','maximum_transformer_loading_percent','model_total_pt60_losses_mw','model_net_import_mw']:
   diff=q[field]-b[field];row['max_abs_delta_'+field]=diff.abs().max();row['median_delta_'+field]=diff.median()
  row['voltage_violation_cases']=int((q.vm_pu_min.lt(.9)|q.vm_pu_max.gt(1.1)).sum());row['line_over100_cases']=int(q.maximum_line_loading_percent.gt(100).sum())
  summ.append(row)
 pd.DataFrame(summ).to_csv(OUT/'summary.csv',index=False)
