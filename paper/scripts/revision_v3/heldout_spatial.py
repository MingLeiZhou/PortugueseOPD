#!/usr/bin/env python3
"""Five-fold station-held-out spatial allocation test on 22 fixed timestamps.
The entire station is excluded from fitting. No held-out load or seasonal peak
is used for scaling. Installed capacity is an exogenous predictor. Electrical
route-distance kNN is evaluated against capacity and geographic baselines;
this does not validate individual branch flows or the PDIRT residual itself.
"""
from pathlib import Path
from runtime_paths import INPUT_DB
import sys,json,hashlib
import pandas as pd,numpy as np,networkx as nx,pandapower as pp
ROOT=Path(__file__).resolve().parents[3];OUT=ROOT/'paper/revision_v3';sys.path.insert(0,str(ROOT/'portuguese_hv_network/src'))
import run_simpt60_validation_experiments as v
v.worker_init(str(INPUT_DB));intervals=v.selected_intervals(INPUT_DB,None)
net=pp.from_json(v.MODEL_INPUT);b=net.bus.set_index('bus_id');cap=pd.DataFrame(json.loads((ROOT/'portuguese_hv_network/data/raw/eredes/temporal_validation/substation_capacity_2025.json').read_text())['records']).groupby('codigo_da_instalacao').potencia_instalada.max()
lookup=v._INPUTS.connection.sql('select * from source.grid.load_points').df() if hasattr(v._INPUTS,'connection') else None
# Match stable bus IDs, not names or held-out peak demand.
stations=[]
for code,value in cap.items():
 id='BUS:EREDES:'+code+':60'
 if id in b.index and value>0 and bool(b.at[id,'in_service']):stations.append({'code':code,'bus_id':id,'capacity_mva':float(value),'fold':int(hashlib.sha256(('SimPT60-20260921:'+code).encode()).hexdigest()[:8],16)%5})
s=pd.DataFrame(stations);indices=net.bus.reset_index().set_index('bus_id')['index'];s['bus_index']=s.bus_id.map(indices);coords=v._INPUTS.connection.sql('select bus_id,lon,lat from source.grid.buses').df().set_index('bus_id');s['lon']=s.bus_id.map(coords.lon);s['lat']=s.bus_id.map(coords.lat)
g=nx.Graph();g.add_nodes_from(net.bus.index[net.bus.in_service])
for _,r in net.line[net.line.in_service].iterrows():
 if r.from_bus not in g or r.to_bus not in g:continue
 d=max(float(r.length_km),.001)
 if not g.has_edge(r.from_bus,r.to_bus) or d<g[r.from_bus][r.to_bus]['weight']:g.add_edge(r.from_bus,r.to_bus,weight=d)
for _,r in net.trafo[net.trafo.in_service].iterrows():
 if r.hv_bus in g and r.lv_bus in g:g.add_edge(r.hv_bus,r.lv_bus,weight=.001)
n=len(s);route=np.full((n,n),np.inf)
for i,idx in enumerate(s.bus_index):
 dd=nx.single_source_dijkstra_path_length(g,idx,weight='weight');route[i]=[dd.get(j,np.inf) for j in s.bus_index]
x=s.lon.to_numpy()*np.cos(np.deg2rad(s.lat.mean()))*111.;y=s.lat.to_numpy()*111.;geo=np.hypot(x[:,None]-x[None,:],y[:,None]-y[None,:]);records=[]
for interval in intervals:
 snap=v._INPUTS.load_snapshot(interval);power=snap.groupby('codigo_subestacao').energia.mean()/250.;actual=s.code.map(power).to_numpy(float);capacity=s.capacity_mva.to_numpy();valid=np.isfinite(actual)&(actual>=0)
 for fold in range(5):
  train=np.flatnonzero(valid&(s.fold.to_numpy()!=fold));test=np.flatnonzero(valid&(s.fold.to_numpy()==fold))
  if len(train)<5:raise ValueError('Insufficient training stations')
  for i in test:
   pred={'CAPACITY_GLOBAL':capacity[i]*actual[train].sum()/capacity[train].sum()}
   for name,dist in [('GEOGRAPHIC_KNN5',geo),('NETWORK_ROUTE_KNN5',route)]:
    near=sorted([j for j in train if np.isfinite(dist[i,j])],key=lambda j:(dist[i,j],s.code.iloc[j]))[:5]
    if not near:pred[name]=np.nan;continue
    w=1/np.maximum(dist[i,near],.1);pred[name]=capacity[i]*np.sum(w*actual[near])/np.sum(w*capacity[near])
   for method,p in pred.items():records.append({'case_id':interval['case_id'],'timestamp_utc':interval['timestamp_utc'],'station':s.code.iloc[i],'fold':fold,'method':method,'observed_mw':actual[i],'predicted_mw':p})
 print(interval['case_id'],int(valid.sum()),flush=True)
d=pd.DataFrame(records);d['error_mw']=d.predicted_mw-d.observed_mw;d.to_csv(OUT/'heldout_station_predictions.csv',index=False);s.to_csv(OUT/'heldout_station_folds.csv',index=False);summ=[];rng=np.random.default_rng(20260921)
for method,z in d.groupby('method'):
 z=z.dropna(subset=['predicted_mw']);e=z.error_mw;station=z.groupby('station').agg(n=('error_mw','size'),ae=('error_mw',lambda x:x.abs().sum()),se=('error_mw',lambda x:(x*x).sum()),obs=('observed_mw','sum'))
 cis=[]
 for _ in range(1000):
  sample=station.iloc[rng.integers(0,len(station),len(station))].sum();cis.append(sample.ae/sample.n)
 summ.append({'method':method,'stations':len(station),'pairs':len(z),'mae_mw':e.abs().mean(),'rmse_mw':np.sqrt((e*e).mean()),'bias_mw':e.mean(),'wape':e.abs().sum()/z.observed_mw.sum(),'mae_station_bootstrap_ci_low':np.quantile(cis,.025),'mae_station_bootstrap_ci_high':np.quantile(cis,.975),'pearson':z.observed_mw.corr(z.predicted_mw)})
pd.DataFrame(summ).to_csv(OUT/'heldout_spatial_summary.csv',index=False)
(OUT/'heldout_protocol.json').write_text(json.dumps({'folds':5,'seed_string':'SimPT60-20260921:','fold_rule':'first 8 hex SHA256 modulo 5 of seed_string+station_code','times':len(intervals),'k':5,'weight':'1/max(distance_km,0.1)','trafo_route_length_km':.001,'capacity_source':'2025 installed MVA; maximum across reported seasons','excluded_features':['held-out station load at every timestamp','seasonal peak load','national held-out totals'],'scope':'stress test of spatial imputability and reconstructed route proximity; not independent AC-flow or PDIRT allocation validation'},indent=2))
