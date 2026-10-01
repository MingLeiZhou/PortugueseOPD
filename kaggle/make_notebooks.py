"""Generate the Kaggle/Zenodo demo notebooks for the SimPT-Power open bundle.
Run: python kaggle/make_notebooks.py   (needs nbformat). Execute/test: see kaggle/README.md."""
from pathlib import Path
import nbformat as nbf
HERE = Path(__file__).parent
md, code = nbf.v4.new_markdown_cell, nbf.v4.new_code_cell

FIND = '''import os, json
from pathlib import Path
def find_data():
    """Locate the SimPT-Power open bundle: $SIMPT_DATA, a Kaggle input dataset, or the local build folder."""
    cands = [os.environ.get("SIMPT_DATA", "")]
    cands += [str(p.parent) for p in Path("/kaggle/input").rglob("bundle_manifest.json")] if Path("/kaggle/input").exists() else []
    cands += [f"{pre}output/simpt_power_release/open_bundle_{tag}" for tag in ("r3", "r2") for pre in ("../", "")]
    for c in cands:
        if c and (Path(c) / "bundle_manifest.json").exists():
            return Path(c)
    raise FileNotFoundError("SimPT-Power bundle not found; set SIMPT_DATA")
DATA = find_data()
manifest = json.loads((DATA / "bundle_manifest.json").read_text())
print(DATA, "|", manifest["bundle_of"])'''

CITE = md("""**Citation.** SimPT-Power is a simulation database built from public data. It is *not* the operator's network or a digital twin: converged power flows show numerical consistency, not agreement with operator state estimates. Please cite the archived release (Zenodo DOI) and the data paper. Data licences follow the sources (E-REDES CC BY 4.0, OpenStreetMap-derived layers ODbL); see `SOURCE_LICENSES.md` in the release.""")

nb1 = nbf.v4.new_notebook()
nb1.cells = [
    md("""# SimPT-Power quick start
A multi-voltage (0.4–400 kV) simulation database of mainland Portugal built from public data.
This notebook reads the open bundle (Parquet + JSON), which is an uncompressed subset of the frozen release, and shows what each layer contains.

| layer | what | files |
|---|---|---|
| HV core CORE-3787-REN | 60–400 kV network, 31,492 solved 15-min snapshots (May 2025 – Mar 2026) | `hv/` |
| MV | 617 root slices, PTD connections, inferred urban cables, MV customer estimate | `mv/` |
| LV | 72,434 secondary substations (PTD), geographic LV network LV-GEO-A | `lv/` |
| observations | public reference tables used for validation | `observations/` |"""),
    code(FIND),
    code('''import pandas as pd
pd.DataFrame(manifest["files"]).T[["rows", "description"]]'''),
    md("## HV core: network map by voltage level"),
    code('''import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
lines = pd.read_parquet(DATA / "hv/lines.parquet")
geom = lines.geometry_json.map(json.loads)
colors = {400: "#6b3fa0", 220: "#b07cc6", 150: "#e0a3c8", 130: "#9ec5e8", 60: "#9ec5e8"}
fig, ax = plt.subplots(figsize=(5, 8))
for kv in [60, 130, 150, 220, 400]:
    sel = lines.voltage_kv == kv
    ax.add_collection(LineCollection([g for g in geom[sel]], colors=colors[kv], lw=0.4 if kv < 150 else 1.0, label=f"{kv} kV"))
ax.autoscale(); ax.set_aspect(1.3); ax.axis("off"); ax.legend(loc="lower right", fontsize=8)
ax.set_title(f"CORE-3787-REN: {len(lines):,} lines")
plt.show()
lines.groupby("voltage_kv").agg(lines=("line_id", "count"), km=("length_km", "sum")).round(0)'''),
    md("## HV core: 31,492 solved snapshots\nOne row per 15-minute snapshot, with observed national load and model results. Overloads are kept as computed."),
    code('''snap = pd.read_parquet(DATA / "hv/snapshots_summary.parquet")
snap["timestamp_utc"] = pd.to_datetime(snap.timestamp_utc, utc=True)
daily = snap.set_index("timestamp_utc").resample("D").agg({"observed_load_mw": "max", "maximum_line_loading_percent": "max"})
fig, ax = plt.subplots(2, 1, figsize=(10, 5), sharex=True)
daily.observed_load_mw.plot(ax=ax[0]); ax[0].set_ylabel("daily peak load (MW)")
daily.maximum_line_loading_percent.plot(ax=ax[1], color="tab:red"); ax[1].axhline(100, ls="--", c="k", lw=0.8)
ax[1].set_ylabel("max line loading (%)"); plt.tight_layout(); plt.show()
print(f"snapshots {len(snap):,}; converged {snap.converged.sum():,}; with at least one line >100%: {(snap.lines_over_100_percent > 0).sum():,}")'''),
    md("## Secondary substations (PTD) and the geographic LV network"),
    code('''ptd = pd.read_parquet(DATA / "lv/ptd_public_attributes.parquet")
lvr = pd.read_parquet(DATA / "lv/lv_geo_ptd_results.parquet")
print(f"PTDs: {len(ptd):,}; with a geographic LV network: {len(lvr):,}")
by = lvr.merge(ptd[["ptd_code", "district_name"]], on="ptd_code").groupby("district_name")[["overhead_km", "street_km", "drops_km"]].sum()
by = by.rename(columns={"overhead_km": "pole network (overhead)", "street_km": "street-routed extensions", "drops_km": "service drops"})
by.sort_values("pole network (overhead)").plot.barh(stacked=True, figsize=(7, 6)); plt.xlabel("km"); plt.ylabel("")
plt.title("LV-GEO-A lengths by district"); plt.show()'''),
    md("### One LV feeder\nSegments of one PTD: overhead lines on public poles, street-routed extensions and service drops."),
    code('''import duckdb
ptd_code = "0911D2009300"
seg = duckdb.sql(f"select * from read_parquet('{DATA}/lv/lv_geo_segments.parquet') where ptd_code = '{ptd_code}'").df()
style = {"overhead": ("tab:blue", 1.4), "underground": ("tab:red", 1.4), "service_drop": ("grey", 0.6)}
fig, ax = plt.subplots(figsize=(6, 6))
for cls, (c, w) in style.items():
    d = seg[seg.cable_class == cls]
    ax.add_collection(LineCollection([[(a, b), (e, f)] for a, b, e, f in zip(d.lon_from, d.lat_from, d.lon_to, d.lat_to)], colors=c, lw=w, label=cls))
ax.autoscale(); ax.set_aspect(1.3); ax.legend(); ax.set_title(ptd_code); plt.show()
seg.groupby("cable_class").length_km.sum().round(2)'''),
    md("## Public observations\nReference tables kept next to the model, e.g. ERSE 2016 LV line lengths by NUTS III region."),
    code('''pd.read_parquet(DATA / "observations/erse_bt_nuts3_2016.parquet").head()'''),
    CITE,
]

nb2 = nbf.v4.new_notebook()
nb2.cells = [
    md("""# AC power flow on the SimPT-Power HV core
Solve the 60–400 kV core with pandapower, reproduce the national peak snapshot, and run a simple N-1 check.
Pinned version: the release was computed with **pandapower 3.5.2**."""),
    code('''import importlib.util, subprocess, sys
if importlib.util.find_spec("pandapower") is None:  # on Kaggle: enable Internet in the notebook settings
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pandapower==3.5.2"], check=True)
import pandapower as pp
print("pandapower", pp.__version__)'''),
    code(FIND),
    md("## 1. The national peak snapshot (15 Jan 2026 12:15 UTC, 11,329 MW)\nThe bundle includes the pandapower net of this snapshot with its loads and dispatch. Re-solving it gives the stored result."),
    code('''import pandas as pd, numpy as np
net = pp.from_json(str(DATA / "hv/CORE-3787-REN_peak_20260115_1215_solved.json"))
opts = dict(algorithm="nr", init="dc", calculate_voltage_angles=True, max_iteration=50, tolerance_mva=1e-6, enforce_q_lims=True, numba=False)
pp.runpp(net, **opts)
print("converged:", net.converged)
print(f"load {net.res_load.p_mw.sum():,.0f} MW | line losses {net.res_line.pl_mw.sum():.1f} MW | max line loading {net.res_line.loading_percent.max():.1f}%")
stored = pd.read_parquet(DATA / "hv/peak_20260115_1215_line_loading.parquet").set_index("line_id").loading_percent
mine = pd.Series(net.res_line.loading_percent.values, index=net.line.line_id)
print("max |difference| to the stored result (percentage points):", float((mine - stored).abs().max()))'''),
    md("## 2. Line loading map"),
    code('''import json, matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
lines = pd.read_parquet(DATA / "hv/lines.parquet").set_index("line_id")
geom = lines.geometry_json.map(json.loads)
ld = mine.reindex(lines.index)
bins, cols = [0, 20, 40, 60, 80, 100, 1e9], ["#4d9a4d", "#8bb04a", "#c9b43d", "#d98b35", "#d4552e", "#b0172a"]
fig, ax = plt.subplots(figsize=(5, 8))
for i in range(6):
    sel = (ld >= bins[i]) & (ld < bins[i + 1])
    ax.add_collection(LineCollection(list(geom[sel]), colors=cols[i], lw=0.5 + 0.4 * i, label=f"{bins[i]:.0f}–{bins[i+1]:.0f}%" if i < 5 else ">100%"))
ax.autoscale(); ax.set_aspect(1.3); ax.axis("off"); ax.legend(title="loading", loc="lower right", fontsize=8); plt.show()
top = ld.sort_values(ascending=False).head(5).to_frame("loading_percent").join(lines[["name", "voltage_kv", "length_km"]]); top'''),
    md("## 3. A month of loadings for the most loaded line\n`hv/line_loading_2026-01.parquet` holds all 2,976 January snapshots (the release holds all 11 months)."),
    code('''import duckdb
lid = top.index[0]
ts = duckdb.sql(f"select timestamp_utc, loading_percent from read_parquet('{DATA}/hv/line_loading_2026-01.parquet') where line_id = '{lid}' order by 1").df()
ax = ts.set_index("timestamp_utc").loading_percent.plot(figsize=(10, 3)); ax.axhline(100, ls="--", c="k", lw=0.8)
ax.set_ylabel("loading (%)"); ax.set_title(f"{lid} ({lines.at[lid, 'name']}), January 2026"); plt.show()
print(f"intervals above 100%: {(ts.loading_percent > 100).sum()} of {len(ts)}")'''),
    md("## 4. A simple N-1 check at the peak\nTake each 400 kV line out in turn, re-solve, and record the worst loading elsewhere. This is an illustration, not an operator security assessment: switching states are inferred and generator Q limits are engineering proxies."),
    code('''import copy
res = []
cand = net.line.index[(net.line.voltage_kv == 400) & net.line.in_service]
for i in cand:
    n1 = copy.deepcopy(net); n1.line.at[i, "in_service"] = False
    try:
        pp.runpp(n1, **{**opts, "init": "results"})
        res.append((net.line.at[i, "line_id"], n1.res_line.loading_percent.max(), (n1.res_line.loading_percent > 100).sum()))
    except pp.LoadflowNotConverged:
        res.append((net.line.at[i, "line_id"], np.nan, np.nan))
n1 = pd.DataFrame(res, columns=["outaged_line", "max_loading_percent", "lines_over_100"]).sort_values("max_loading_percent", ascending=False)
print(f"{len(n1)} outages; not converged: {n1.max_loading_percent.isna().sum()}")
n1.head(10)'''),
    CITE,
]

nb3 = nbf.v4.new_notebook()
nb3.cells = [
    md("""# MV and LV power flow with SimPT-Power
Two distribution-level examples from the open bundle:
1. **MV**: an urban 10-kV cable network (MV-URBAN-CABLE-V1) behind one 60 kV/MV root transformer, solved with pandapower at the public peak-load proxy.
2. **LV**: a geographic four-wire (ABCN) LV network (LV-GEO-A) of one secondary substation (PTD), solved with the backward/forward sweep used to build the database; the result matches the stored one.
Pinned version: **pandapower 3.5.2**."""),
    code('''import importlib.util, subprocess, sys
if importlib.util.find_spec("pandapower") is None:  # on Kaggle: enable Internet in the notebook settings
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "pandapower==3.5.2"], check=True)
import pandapower as pp
print("pandapower", pp.__version__)'''),
    code(FIND),
    md("""## 1. An urban MV cable network
Root `MVROOT:00298` (60/10 kV, Lisboa) feeds 181 PTDs through inferred underground cable feeders (207 segments, 33.7 km); 26 normally open segments tie neighbouring feeders.
The 60-kV source voltage is taken from the solved HV core at the national peak (15 Jan 2026 12:15 UTC). PTD loads are the public peak-load proxy (midpoint of each PTD's published utilisation band) at power factor 0.97."""),
    code('''import math, duckdb, pandas as pd, numpy as np
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
ROOT = "MVROOT:00298"
q = lambda sql: duckdb.sql(sql.replace("@", str(DATA) + "/")).df()
seg = q(f"select * from read_parquet('@mv/urban_cable_segments.parquet') where root = '{ROOT}'")
tr = q(f"select * from read_parquet('@mv/root_transformers.parquet') where mv_root_bus = '{ROOT}'").iloc[0]
ptd = q(f"select ptd_code, peak_load_proxy_mva, capacity_kva from read_parquet('@mv/ptd_connections.parquet') where mv_root_bus = '{ROOT}'")
vm_hv = q(f"select vm_pu from read_parquet('@hv/peak_20260115_1215_bus_voltage.parquet') where bus_id = '{tr.hv_bus}'").vm_pu.iloc[0]
print(f"{len(seg)} cable segments ({seg.normally_open.sum()} normally open), {seg.feeder.nunique()} feeders, {seg.length_km.sum():.1f} km")
print(f"root transformer {tr.hv_kv:.0f}/{tr.lv_kv:.0f} kV, {tr.sn_mva:.0f} MVA; {len(ptd)} PTDs; HV bus {tr.hv_bus} at {vm_hv:.4f} p.u.")
seg[["designation", "r_ohm_per_km", "x_ohm_per_km", "max_i_ka", "evidence_status"]].drop_duplicates()'''),
    code('''net = pp.create_empty_network()
hv = pp.create_bus(net, tr.hv_kv, name=tr.hv_bus)
pp.create_ext_grid(net, hv, vm_pu=vm_hv)
bus = {}
def b(name):
    if name not in bus:
        bus[name] = pp.create_bus(net, float(seg.kv.iloc[0]), name=name)
    return bus[name]
root = b(f"ROOTANCHOR:{ROOT}")
pp.create_transformer_from_parameters(net, hv, root, sn_mva=tr.sn_mva, vn_hv_kv=tr.hv_kv, vn_lv_kv=tr.lv_kv,
                                      vk_percent=tr.vk_percent, vkr_percent=tr.vkr_percent, pfe_kw=0, i0_percent=0)
for r in seg.itertuples():
    pp.create_line_from_parameters(net, b(r.from_node), b(r.to_node), length_km=r.length_km, r_ohm_per_km=r.r_ohm_per_km,
                                   x_ohm_per_km=r.x_ohm_per_km, c_nf_per_km=r.c_nf_per_km, max_i_ka=r.max_i_ka,
                                   in_service=not r.normally_open, name=r.segment_id)
tanphi = math.tan(math.acos(0.97))
for r in ptd.itertuples():
    p = r.peak_load_proxy_mva * 0.97
    pp.create_load(net, bus[r.ptd_code], p_mw=p, q_mvar=p * tanphi, name=r.ptd_code)
pp.runpp(net, numba=False)
print(f"converged {net.converged}: load {net.res_load.p_mw.sum():.1f} MW, min voltage {net.res_bus.vm_pu[net.bus.vn_kv < 60].min():.4f} p.u., "
      f"max cable loading {net.res_line.loading_percent.max():.1f}%, transformer loading {net.res_trafo.loading_percent.iloc[0]:.1f}%")'''),
    md("Voltage along each feeder (distance from the root) and the heaviest-loaded cable sections."),
    code('''import networkx as nx
g = nx.Graph()
for i, r in net.line[net.line.in_service].iterrows():
    g.add_edge(r.from_bus, r.to_bus, w=r.length_km)
dist = nx.single_source_dijkstra_path_length(g, root, weight="w")
d = pd.DataFrame({"km": pd.Series(dist), "vm_pu": net.res_bus.vm_pu})
d = d.dropna()
fig, ax = plt.subplots(figsize=(7, 3.5))
ax.scatter(d.km, d.vm_pu, s=8); ax.set_xlabel("distance from root (km)"); ax.set_ylabel("voltage (p.u.)")
ax.set_title(f"{ROOT}: MV bus voltages at the peak-load proxy"); plt.show()
net.res_line.join(net.line[["name", "length_km"]]).sort_values("loading_percent", ascending=False)[["name", "length_km", "loading_percent"]].head()'''),
    md("""## 2. A geographic four-wire LV network
PTD `1106D1002700` (Lisboa, prefabricated cabin, 400 kVA) has 301 nodes: a short pole network and 5.3 km of street-routed underground cable (LXAV 3x185+95), with two parallel circuits added where the 0.9-p.u. screen required it (`refined_parallel`).
Each branch carries phases A, B, C and an explicit neutral N. The sweep of eq. (2) of the paper: currents are accumulated from the leaves, then voltages are updated from the transformer, until the change is below 1e-6 V.
Three inputs come from the full DuckDB release (tables named in comments) and are written here as constants: the PTD's phase shares, its design transformer rating and the 4x4 mutual-impedance proxy."""),
    code('''from collections import defaultdict, deque
VLN = 400 / math.sqrt(3)
SRC = np.array([VLN * np.exp(1j * a) for a in (0, -2 * math.pi / 3, 2 * math.pi / 3)] + [0j])
MUTUAL = 0.05 + 0.02j          # phase.impedance_matrix, archetype LV_4WIRE_PROXY_V1 (ohm/km, off-diagonal)
CABLE = {"overhead": ("LXS 4 x 95", 0.359, 0.1, 230.0),        # E-REDES DIT-C14-100/N: r_hot, x (ohm/km), Iz (A)
         "underground": ("LXAV 3x185+95", 0.21, 0.1, 375.0)}

def solve_lv(code, phase_shares, sn_mva, vk=4.0, vkr=None):
    res = q(f"select * from read_parquet('@lv/lv_geo_ptd_results.parquet') where ptd_code = '{code}'").iloc[0]
    s = q(f"select * from read_parquet('@lv/lv_geo_segments.parquet') where ptd_code = '{code}' order by node")
    t = q(f"select * from read_parquet('@lv/ptd_transformers.parquet') where ptd_code = '{code}'").iloc[0]
    vkr = t.vkr_percent if vkr is None else vkr
    n = int(s.node.max()) + 1
    parent = np.full(n, -1); length = np.zeros(n); par = np.ones(n); w = np.zeros(n)
    parent[s.node] = s.parent_node; length[s.node] = s.length_km; par[s.node] = s.refined_parallel; w[s.node] = s.load_weight
    kind = "overhead" if str(res.ctype).startswith("Aéreo") else "underground"
    _, r, x, imax = CABLE[kind]
    z = np.full((4, 4), MUTUAL); np.fill_diagonal(z, complex(r, x))
    base = 0.4 ** 2 / sn_mva
    zt = complex(vkr / 100 * base, math.sqrt(vk ** 2 - vkr ** 2) / 100 * base)
    p = res.load_kw / 1000; pv = res.pv_kw / 1000
    S = (p - pv + 1j * p * math.tan(math.acos(0.97))) * 1e6
    if w.sum() == 0: w[1:] = 1
    loads = np.outer(w / w.sum() * S, phase_shares)
    children = defaultdict(list)
    for k in range(1, n): children[parent[k]].append(k)
    order, dq = [0], deque([0])
    while dq:
        u = dq.popleft()
        for v in children[u]: order.append(v); dq.append(v)
    V = np.tile(SRC, (n, 1))
    for it in range(1, 101):
        I = np.zeros((n, 4), complex)
        I[:, :3] = np.conj(loads / (V[:, :3] - V[:, 3:4])); I[:, 3] = -I[:, :3].sum(1)
        for k in reversed(order[1:]): I[parent[k]] += I[k]
        Vn = V.copy(); Vn[0, :3] = SRC[:3] - zt * I[0, :3]; Vn[0, 3] = 0
        for k in order[1:]: Vn[k] = Vn[parent[k]] - z @ I[k] * length[k] / par[k]
        done = np.max(np.abs(Vn - V)) < 1e-6; V = Vn
        if done: break
    vpu = np.abs(V[:, :3] - V[:, 3:4]) / VLN
    loading = np.max(np.abs(I[1:, :3]).max(1) / (imax * par[1:])) * 100
    return dict(iterations=it, vmin=vpu.min(), vmax=vpu.max(), loading_pct=loading, neutral_A=np.abs(I[1:, 3]).max(),
                stored_vmin=res.geo_refined_vmin, stored_vmax=res.geo_refined_vmax, stored_loading_pct=res.geo_refined_loading_pct), (s, vpu)

# phase.lv_load_shares (A, B, C) and parameter.ptd_transformer_root_peak_design_scenario.design_sn_mva for this PTD
out, (s, vpu) = solve_lv("1106D1002700", phase_shares=[0.37, 0.34, 0.29], sn_mva=0.4)
pd.Series(out)'''),
    md("The sweep reproduces the stored minimum voltage and loading. Phase voltages along the network (the lowest of the three phases at each node):"),
    code('''s = s.assign(vmin=vpu.min(1)[s.node])
fig, ax = plt.subplots(figsize=(6.5, 6))
lc = LineCollection([[(a, b_), (e, f)] for a, b_, e, f in zip(s.lon_from, s.lat_from, s.lon_to, s.lat_to)],
                    array=s.vmin.values, cmap="viridis", linewidths=1 + s.refined_parallel.values)
ax.add_collection(lc); ax.autoscale(); ax.set_aspect(1.3)
ax.plot(s.lon_from.iloc[0], s.lat_from.iloc[0], "ks", ms=7, label="PTD")
fig.colorbar(lc, ax=ax, label="lowest phase-neutral voltage (p.u.)"); ax.legend(); ax.set_title("1106D1002700, LV-GEO-A")
plt.show()'''),
    md("Without the two PTD-specific constants, balanced phase shares and the public transformer rating give a first approximation for any PTD. The database's phase shares range from 0.29 to 0.37, and this imbalance lowers the worst phase voltage, as the comparison shows:"),
    code('''for code in ["0911D2009300", "1106D1002700"]:
    t = q(f"select sn_mva from read_parquet('@lv/ptd_transformers.parquet') where ptd_code = '{code}'").sn_mva.iloc[0]
    o, _ = solve_lv(code, phase_shares=[1/3, 1/3, 1/3], sn_mva=t)
    print(f"{code}: vmin {o['vmin']:.4f} (stored {o['stored_vmin']:.4f}), loading {o['loading_pct']:.1f}% (stored {o['stored_loading_pct']:.1f}%)")'''),
    CITE,
]

nb4 = nbf.v4.new_notebook()
nb4.cells = [
    md("""# Graph learning on the SimPT-Power HV core
The HV core is a graph (3,787 buses; lines and transformers as edges) with a solved AC power flow for every 15-minute snapshot.
This notebook turns January 2026 (2,976 snapshots) into a supervised task: **predict every line's loading from the snapshot's national load and generation**, train on 1–21 January (hourly snapshots) and test on every 15-minute snapshot of 22–31 January.
It compares two baselines with a small graph neural network written in plain PyTorch. The point is the data pipeline; the model is deliberately simple."""),
    code(FIND),
    code('''import duckdb, numpy as np, pandas as pd, torch, matplotlib.pyplot as plt
torch.manual_seed(0); np.random.seed(0)
buses = pd.read_parquet(DATA / "hv/buses.parquet")
lines = pd.read_parquet(DATA / "hv/lines.parquet")
trafos = pd.read_parquet(DATA / "hv/transformers.parquet")
gens = pd.read_parquet(DATA / "hv/generators.parquet")
loads = pd.read_parquet(DATA / "hv/load_points.parquet")
bix = {b: i for i, b in enumerate(buses.bus_id)}
print(len(buses), "buses |", len(lines), "lines |", len(trafos), "transformers |", len(gens), "generators |", len(loads), "load points")'''),
    md("## 1. Graph tensors\nNode features: voltage level (one-hot), degree, load-point flag and installed generation by technology. Edge features for lines: R, X, length and rating."),
    code('''kv_levels = [60, 130, 150, 220, 400]
X_kv = np.stack([(buses.voltage_kv == k).values for k in kv_levels], 1).astype(np.float32)
tech = gens.assign(t=gens.generation_source.fillna("other").str.lower()).groupby(["bus_id", "t"]).nameplate_mw.sum().unstack(fill_value=0)
top_tech = tech.sum().sort_values(ascending=False).index[:6]
X_gen = np.log1p(tech.reindex(buses.bus_id).fillna(0)[top_tech].values).astype(np.float32)
X_load = buses.bus_id.isin(loads.bus_id).values[:, None].astype(np.float32)
ln = lines[lines.from_bus.isin(bix) & lines.to_bus.isin(bix)].reset_index(drop=True)
src = np.r_[ln.from_bus.map(bix), trafos.hv_bus.map(bix)]; dst = np.r_[ln.to_bus.map(bix), trafos.lv_bus.map(bix)]
deg = np.bincount(np.r_[src, dst], minlength=len(buses))
X_static = np.hstack([X_kv, X_gen, X_load, np.log1p(deg)[:, None]]).astype(np.float32)
edge_index = torch.tensor(np.stack([np.r_[src, dst], np.r_[dst, src]]), dtype=torch.long)
E_attr = np.log1p(ln[["r_ohm_per_km", "x_ohm_per_km", "length_km", "max_i_ka"]].fillna(0).clip(lower=0).values).astype(np.float32)
print("node features", X_static.shape, "| edges (both directions)", edge_index.shape[1], "| tech columns:", list(top_tech))'''),
    md("## 2. Targets: line loading for 2,976 snapshots\nLines without a loading result (out of service or in an unsupplied island) are masked."),
    code('''snap = pd.read_parquet(DATA / "hv/snapshots_summary.parquet")
snap["t"] = pd.to_datetime(snap.timestamp_utc, utc=True)
jan = snap[(snap.t >= "2026-01-01") & (snap.t < "2026-02-01")].sort_values("t").reset_index(drop=True)
ll = duckdb.sql(f"select case_id, line_id, loading_percent from read_parquet('{DATA}/hv/line_loading_2026-01.parquet')").df()
Y = ll.pivot(index="case_id", columns="line_id", values="loading_percent").reindex(index=jan.case_id, columns=ln.line_id)
mask = Y.notna().values; Y = Y.fillna(0).values.astype(np.float32)
hour = jan.t.dt.hour + jan.t.dt.minute / 60
F = np.c_[jan.observed_load_mw / 1e4, jan.observed_generation_total_mw / 1e4, jan.observed_net_import_mw / 1e4,
          np.sin(2 * np.pi * hour / 24), np.cos(2 * np.pi * hour / 24), (jan.t.dt.weekday >= 5)].astype(np.float32)
train = (jan.t < "2026-01-22").values; test = ~train
print("Y", Y.shape, f"| train {train.sum()} / test {test.sum()} snapshots | lines with results: {mask.any(0).sum()}")'''),
    md("## 3. Baselines\n(a) each line's training mean; (b) a separate linear regression per line on the six snapshot features (4,926 independent models)."),
    code('''def mae(pred, sel):
    m = mask[sel]; return float(np.abs(pred - Y[sel])[m].mean())
mean_line = (Y[train] * mask[train]).sum(0) / np.maximum(mask[train].sum(0), 1)
A = np.c_[F, np.ones(len(F))]
W, *_ = np.linalg.lstsq(A[train], Y[train], rcond=None)
res = {"per-line mean": mae(np.tile(mean_line, (test.sum(), 1)), test), "per-line linear": mae(A[test] @ W, test)}
res'''),
    md("""## 4. A small graph neural network
One shared model for all lines: the snapshot features are broadcast to every bus, concatenated with the static bus features, passed through three graph-convolution layers (mean aggregation over neighbours), and each line's value is read out from its two end buses and its own parameters.
Here the network learns the deviation of each line from its training mean, so it adds a shared, topology-aware correction to baseline (a)."""),
    code('''import torch.nn as nn
N = len(buses); ei = edge_index
deg_t = torch.bincount(ei[1], minlength=N).clamp(min=1).float()[:, None]
def propagate(h):          # mean over neighbours: h -> (B, N, d)
    out = torch.zeros_like(h); out.index_add_(1, ei[1], h[:, ei[0]]); return out / deg_t
class GNN(nn.Module):
    def __init__(self, fs, fd, fe, h=32):
        super().__init__()
        self.inp = nn.Linear(fs + fd, h)
        self.layers = nn.ModuleList([nn.Linear(2 * h, h) for _ in range(3)])
        self.out = nn.Sequential(nn.Linear(2 * h + fe + fd, h), nn.ReLU(), nn.Linear(h, 1))
    def forward(self, xs, f, ea, s, d):
        B = f.shape[0]
        h = torch.relu(self.inp(torch.cat([xs.expand(B, -1, -1), f[:, None].expand(-1, xs.shape[1], -1)], -1)))
        for lin in self.layers:
            h = h + torch.relu(lin(torch.cat([h, propagate(h)], -1)))
        hu, hv = h[:, s], h[:, d]
        z = torch.cat([hu + hv, (hu - hv).abs(), ea.expand(B, -1, -1), f[:, None].expand(-1, s.shape[0], -1)], -1)
        return self.out(z).squeeze(-1)
xs = torch.tensor(X_static)[None]; ea = torch.tensor(E_attr)[None]
s_t = torch.tensor(ln.from_bus.map(bix).values); d_t = torch.tensor(ln.to_bus.map(bix).values)
Ft, Yt, Mt = torch.tensor(F), torch.tensor(Y), torch.tensor(mask)
base_t = torch.tensor(mean_line, dtype=torch.float32)
model = GNN(X_static.shape[1], F.shape[1], E_attr.shape[1])
opt = torch.optim.Adam(model.parameters(), lr=3e-3)
idx_tr = np.flatnonzero(train)[::4]          # hourly training snapshots keep the CPU run to a few minutes
for epoch in range(25):
    np.random.shuffle(idx_tr); tot = 0
    for k in range(0, len(idx_tr), 64):
        b = idx_tr[k:k + 64]
        p = model(xs, Ft[b], ea, s_t, d_t) + base_t
        loss = (p - Yt[b]).abs()[Mt[b]].mean()
        opt.zero_grad(); loss.backward(); opt.step(); tot += loss.item() * len(b)
    if epoch % 5 == 4: print(f"epoch {epoch+1}: train MAE {tot / len(idx_tr):.2f} pp")
with torch.no_grad():
    pred = torch.cat([model(xs, Ft[np.flatnonzero(test)[k:k+64]], ea, s_t, d_t) + base_t for k in range(0, test.sum(), 64)]).numpy()
res["per-line mean + GNN correction"] = mae(pred, test)
pd.Series(res, name="test MAE (percentage points)").round(2)'''),
    md("""On lines with a training history, the 4,926 separate linear regressions remain the strongest model; the shared GNN improves on the per-line mean with a single set of weights. Its use is for lines without history, tested next."""),
    md("""## 5. Generalising to unseen lines
Hide 20% of the lines during training and evaluate only on them. No per-line statistic exists for these lines, so the reference is the global mean of the other lines; the GNN is now trained on the loading itself and predicts it from the graph alone."""),
    code('''rng = np.random.default_rng(0)
hidden = rng.random(len(ln)) < 0.2
Mt_tr = Mt.clone(); Mt_tr[:, torch.tensor(hidden)] = False
model2 = GNN(X_static.shape[1], F.shape[1], E_attr.shape[1]); opt = torch.optim.Adam(model2.parameters(), lr=3e-3)
for epoch in range(25):
    np.random.shuffle(idx_tr)
    for k in range(0, len(idx_tr), 64):
        b = idx_tr[k:k + 64]
        loss = (model2(xs, Ft[b], ea, s_t, d_t) - Yt[b]).abs()[Mt_tr[b]].mean()
        opt.zero_grad(); loss.backward(); opt.step()
with torch.no_grad():
    pred2 = torch.cat([model2(xs, Ft[np.flatnonzero(test)[k:k+64]], ea, s_t, d_t) for k in range(0, test.sum(), 64)]).numpy()
m = mask[test][:, hidden]
glob = float(Y[train][:, ~hidden][mask[train][:, ~hidden]].mean())
print(f"hidden lines: {hidden.sum()} | test MAE on them: global mean {np.abs(glob - Y[test][:, hidden])[m].mean():.2f} pp, "
      f"GNN {np.abs(pred2[:, hidden] - Y[test][:, hidden])[m].mean():.2f} pp")'''),
    md("""### Where to go from here
* Use all 31,492 snapshots: the release ships one compressed DuckDB per month (`hv_core/`), with bus voltages and line loadings.
* Add per-bus injections as node features (loads and dispatch are allocation rules in `operating.resource_timeseries_definition` of the main database).
* Topology changes (N-1, see notebook 2) give natural graph-level augmentations."""),
    CITE,
]

for name, nb in [("01_simpt_power_quickstart.ipynb", nb1), ("02_hv_power_flow.ipynb", nb2),
                 ("03_mv_lv_power_flow.ipynb", nb3), ("04_hv_graph_learning.ipynb", nb4)]:
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nbf.write(nb, HERE / name); print("wrote", HERE / name)
