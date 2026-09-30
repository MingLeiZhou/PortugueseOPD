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
    cands += ["../output/simpt_power_release/open_bundle_r2", "output/simpt_power_release/open_bundle_r2"]
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
for name, nb in [("01_simpt_power_quickstart.ipynb", nb1), ("02_hv_power_flow.ipynb", nb2)]:
    nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}
    nbf.write(nb, HERE / name); print("wrote", HERE / name)
