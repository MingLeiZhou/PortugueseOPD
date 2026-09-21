#!/usr/bin/env python3
"""Offline one-case reproduction, runnable from the extracted release root."""
from pathlib import Path
import sys,json,argparse,hashlib
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'portuguese_hv_network/src'))
import pandas as pd,duckdb
from run_monthly_15min import DatabaseInputs,_case_from_interval
from run_temporal_validation import run_case,MODEL_INPUT,GENERATOR_INPUT
ap=argparse.ArgumentParser();ap.add_argument('--case-index',type=int,default=0);ap.add_argument('--output',type=Path,default=ROOT/'replay_output');a=ap.parse_args()
golden=json.loads((ROOT/'replay_cases.json').read_text());target=golden[a.case_index];i=target['interval'];a.output.mkdir(parents=True,exist_ok=True)
c=duckdb.connect(':memory:');c.execute("ATTACH '"+str(ROOT/'inputs.duckdb').replace("'","''")+"' AS source (READ_ONLY)");inputs=DatabaseInputs(c,ROOT/'inputs.duckdb')
case=_case_from_interval(i,a.output/'state.npz');g=pd.read_csv(GENERATOR_INPUT,low_memory=False)
# Fail closed on accidental network reads: archived inputs must be sufficient.
import requests
def offline(*args,**kwargs):raise RuntimeError('Unexpected network access during offline replay')
requests.sessions.Session.request=offline
result,*_=run_case(case,g,False,save_solved=True,observation_override=inputs.observation(i),load_snapshot_override=inputs.load_snapshot(i),weather_override=inputs.weather(i),rnt_loss_override=inputs.rnt_loss(case),model_input=MODEL_INPUT,output_dir=a.output)
deltas={k:abs(float(result[k])-float(target['result'][k])) for k in ['vm_pu_min','vm_pu_max','maximum_line_loading_percent','model_net_import_mw']}
if max(deltas.values())>1e-5:raise AssertionError(deltas)
out={'status':'PASS','case':i['case_id'],'converged':result['converged'],'max_absolute_differences':deltas};(a.output/'verification.json').write_text(json.dumps(out,indent=2));print(json.dumps(out,indent=2))
