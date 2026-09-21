"""Read only hash-locked archived inputs. Never connect to the working database."""
from pathlib import Path
import hashlib
import json
import pandas as pd
import numpy as np
from style import plt

ROOT=Path(__file__).resolve().parents[3]
OUT=ROOT/'paper/figures_final'
LOCK=json.loads(Path(__file__).with_name('source_lock.json').read_text())
USED=set()
STEMS={1:'network_reconstruction',2:'geographic_network_state',3:'temporal_coverage',
       4:'network_parameter_evidence',5:'parameter_spatial_sensitivity',6:'temporal_validation',
       7:'spatial_validation',8:'generation_balance_scope',9:'grid_stress',
       10:'targeted_nminus1',11:'annual_nminus1'}

def source(rel):
    rel=str(rel)
    if rel not in LOCK:
        raise RuntimeError(f'Unregistered source: {rel}')
    p=ROOT/rel
    if not p.exists() or hashlib.sha256(p.read_bytes()).hexdigest()!=LOCK[rel]:
        raise RuntimeError(f'VERSION / SNAPSHOT CONFLICT: {rel}; no automatic refresh permitted')
    USED.add(rel)
    return p

def verify_all():
    for rel in LOCK: source(rel)
    USED.clear()

def csv(rel):
    return pd.read_csv(source('portuguese_hv_network/outputs/'+rel),low_memory=False)

def scope():
    d=csv('validation_experiments/scope_matched_validation_timeseries.csv')
    d['time']=pd.to_datetime(d.timestamp_utc,utc=True).dt.tz_convert('Europe/Lisbon')
    d['day']=d.time.dt.tz_localize(None).dt.normalize()
    return d

def daily_pair(d,a,b):
    q=d.dropna(subset=[a,b])
    p=q.groupby('day')[[a,b]].mean()
    # Reindex to expose absent days. No interpolation or forward fill.
    return q,p.reindex(pd.date_range(d.day.min(),d.day.max(),freq='D'))

def save(fig,n,extra=None):
    OUT.mkdir(exist_ok=True,parents=True)
    stem=f'fig{n:02d}_{STEMS[n]}'
    # Fixed canvas retains the same physical font sizes and 183 mm width.
    for ext in ['pdf','svg','png']:
        tmp=OUT/f'.{stem}.tmp.{ext}'
        fig.savefig(tmp,dpi=600)
        tmp.replace(OUT/f'{stem}.{ext}')
    record={'figure':n,'stem':stem,'dpi':600,'width_inches':fig.get_figwidth(),
            'height_inches':fig.get_figheight(),'source_files':sorted(USED),
            'sha256':{s:LOCK[s] for s in sorted(USED)},'notes':extra or {}}
    (OUT/f'{stem}.json').write_text(json.dumps(record,indent=2)+'\n')
    plt.close(fig)
    USED.clear()
    print(f'{stem}: SVG / PDF / 600 dpi PNG',flush=True)

ROLES=['MAX_NET_EXPORT','MAX_NET_IMPORT','MAX_SOLAR','MAX_WIND','MINIMUM_LOAD','PEAK_LOAD']
ROLE_NAMES=['Max export','Max import','Max solar','Max wind','Min load','Peak load']
