#!/usr/bin/env python3
"""Generate publication-style SimPT60 manuscript figures with Matplotlib.

Outputs PDF/SVG plus 300 dpi PNG previews. Quantitative panels are derived
directly from committed result artifacts; conceptual panels contain only rules
and counts already stated in the manuscript.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "pt60-paper-mpl"))
os.environ.setdefault("XDG_CACHE_HOME", tempfile.gettempdir())

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import pandapower as pp
from matplotlib import colors
from matplotlib.collections import LineCollection
from matplotlib.patches import FancyArrowPatch, Patch, Rectangle


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "paper" / "figures" / "scientific"
STYLE = Path(__file__).with_name("pt60-paper.mplstyle")
MANIFEST = ROOT / "paper" / "figure_manifest.csv"

# Okabe-Ito plus neutral tones.
BLUE = "#0072B2"
ORANGE = "#D55E00"
GREEN = "#009E73"
GOLD = "#E69F00"
SKY = "#56B4E9"
PURPLE = "#CC79A7"
BLACK = "#202124"
GRAY = "#6B6B6B"
LIGHT = "#E6E6E6"
PALE = "#F5F5F5"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def save(fig: plt.Figure, stem: str) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for suffix, kwargs in (("pdf", {}), ("svg", {}), ("png", {"dpi": 300})):
        fig.savefig(OUT / f"{stem}.{suffix}", **kwargs)
    plt.close(fig)


def panel_label(ax, label: str) -> None:
    ax.text(-0.04, 1.03, f"({label})", transform=ax.transAxes, ha="left", va="bottom", fontweight="bold", fontsize=9)


def clean_diagram(ax) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")


def box(ax, xy, wh, title, subtitle="", edge=BLACK, face="white", lw=0.8, align="center"):
    x, y = xy
    w, h = wh
    ax.add_patch(Rectangle((x, y), w, h, facecolor=face, edgecolor=edge, linewidth=lw))
    tx = x + w / 2 if align == "center" else x + 0.025
    ha = "center" if align == "center" else "left"
    ax.text(tx, y + h * 0.62, title, ha=ha, va="center", fontsize=8, fontweight="bold")
    if subtitle:
        ax.text(tx, y + h * 0.30, subtitle, ha=ha, va="center", fontsize=7.0, color=GRAY, linespacing=1.15)


def arrow(ax, start, end, color=GRAY, lw=0.9, style="-"):
    ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=8, linewidth=lw, color=color, linestyle=style, shrinkA=2, shrinkB=2))


def fig01() -> None:
    fig, ax = plt.subplots(figsize=(7.2, 3.3))
    clean_diagram(ax)
    sources = [("E-REDES", "network, substations, load"), ("REN", "load, generation, exchange"), ("DGEG / ERSE", "assets and planning records"), ("OSM", "geographic infrastructure"), ("Documents", "ratings and parameters")]
    for i, (t, s) in enumerate(sources):
        box(ax, (0.02 + i * 0.195, 0.76), (0.17, 0.15), t, s, edge=BLUE)
    stages = [("Archive", "raw records"), ("Reconstruct", "topology and parameters"), ("Map", "assets to buses"), ("Align", "15-minute inputs")]
    for i, (t, s) in enumerate(stages):
        x = 0.09 + i * 0.23
        box(ax, (x, 0.46), (0.18, 0.14), t, s, edge=BLACK, face=PALE)
        if i < 3:
            arrow(ax, (x + 0.18, 0.53), (x + 0.23, 0.53), BLUE)
    arrow(ax, (0.50, 0.76), (0.50, 0.61), BLUE)
    outputs = [("Static network", "3,783 buses\n4,943 lines · 228 transformers"), ("Operating inputs", "31,492 cases\n15-minute resolution"), ("Solved AC states", "voltage · flow · loading · loss"), ("Evidence and QC", "source keys · rules · validation")]
    for i, (t, s) in enumerate(outputs):
        x = 0.035 + i * 0.245
        box(ax, (x, 0.14), (0.215, 0.17), t, s, edge=ORANGE if i == 1 else BLACK)
    arrow(ax, (0.50, 0.46), (0.50, 0.32), BLUE)
    ax.text(0.5, 0.03, "Public evidence  →  reproducible transformations  →  computable research dataset", ha="center", va="bottom", fontsize=7, color=GRAY)
    save(fig, "fig01_dataset_overview")


def fig02() -> None:
    fig, ax = plt.subplots(figsize=(7.2, 2.9))
    clean_diagram(ax)
    steps = [
        ("1", "Archive and standardize", "coordinates · time · units · identifiers"),
        ("2", "Reconstruct static network", "buses · lines · transformers · parameters"),
        ("3", "Map assets and align time", "generation · load · storage · exchange"),
        ("4", "Assemble and solve cases", "nodal P/Q · controls · AC power flow"),
        ("5", "Store and validate", "layered database · provenance · quality checks"),
    ]
    for i, (n, t, s) in enumerate(steps):
        x = 0.015 + i * 0.197
        ax.text(x + 0.09, 0.79, n, ha="center", va="center", fontsize=9, fontweight="bold", color=BLUE)
        box(ax, (x, 0.40), (0.18, 0.29), t, s, edge=BLACK, face="white")
        if i < 4:
            arrow(ax, (x + 0.18, 0.545), (x + 0.197, 0.545), BLUE)
    ax.add_patch(Rectangle((0.03, 0.12), 0.94, 0.12, facecolor=PALE, edgecolor=GRAY, linewidth=0.7))
    ax.text(0.05, 0.18, "Cross-cutting audit:", fontweight="bold", fontsize=7.5, va="center")
    ax.text(0.22, 0.18, "source key  ·  evidence grade  ·  transformation rule  ·  coverage  ·  conservation  ·  convergence", fontsize=7.2, va="center", color=GRAY)
    save(fig, "fig02_processing_workflow")


def fig03() -> None:
    fig, axs = plt.subplots(2, 2, figsize=(7.2, 4.6))
    for ax in axs.flat:
        clean_diagram(ax)
    # (a) voltage source layers
    ax = axs[0, 0]; panel_label(ax, "a"); ax.set_title("Voltage-specific source layers", loc="left")
    levels = [(60, ORANGE, "E-REDES"), (130, PURPLE, "OSM / REN"), (150, GOLD, "OSM / REN"), (220, BLUE, "OSM / REN"), (400, BLACK, "REN / OSM")]
    for i, (kv, col, src) in enumerate(levels):
        y = 0.78 - i * 0.15
        ax.plot([0.08, 0.55 + i * 0.05], [y, y], color=col, lw=2.2)
        ax.text(0.67, y, f"{kv} kV", va="center", fontsize=7.5)
        ax.text(0.84, y, src, va="center", fontsize=7.0, color=GRAY)
    # (b) spatial matching
    ax = axs[0, 1]; panel_label(ax, "b"); ax.set_title("Endpoint clustering and facility matching", loc="left")
    pts = np.array([[0.18, .65], [.28, .58], [.36, .70], [.43, .61]])
    ax.scatter(pts[:,0], pts[:,1], s=22, color=ORANGE, zorder=3)
    ax.add_patch(plt.Circle((.31,.635), .22, fill=False, ls="--", lw=.8, color=GRAY))
    box(ax, (.67,.51), (.25,.23), "Substation", "facility and voltage match", edge=BLUE, face=PALE)
    arrow(ax, (.49,.635), (.66,.625), BLUE)
    ax.text(.31,.32,"endpoint cluster ≤ 75 m",ha="center",fontsize=7,color=GRAY)
    ax.text(.795,.40,"facility match ≤ 250 m",ha="center",fontsize=7,color=GRAY)
    # (c) explicit circuits
    ax = axs[1, 0]; panel_label(ax, "c"); ax.set_title("Explicit nodes and parallel circuits", loc="left")
    xs=[.12,.36,.62,.88]
    for y,col in [(0.60,BLUE),(0.43,ORANGE)]: ax.plot(xs,[y]*4,color=col,lw=1.4)
    for x in xs:
        ax.scatter([x,x],[.60,.43],s=30,facecolor="white",edgecolor=BLACK,zorder=3)
    ax.text(.5,.20,"parallel paths remain distinct; junctions remain explicit",ha="center",fontsize=7,color=GRAY)
    # (d) transformers
    ax = axs[1, 1]; panel_label(ax, "d"); ax.set_title("Transformer connection rules", loc="left")
    pairs=[("220/60 kV",.18),("220/150 kV",.50),("400/220 kV",.82)]
    for lab,x in pairs:
        ax.plot([x,x],[.25,.75],color=BLACK,lw=.8)
        ax.add_patch(plt.Circle((x,.55),.07,fill=False,lw=1,color=BLACK)); ax.add_patch(plt.Circle((x,.45),.07,fill=False,lw=1,color=BLACK))
        ax.text(x,.14,lab,ha="center",fontsize=7)
    ax.text(.5,.88,"voltage pair + facility evidence + distance ≤ 1 km",ha="center",fontsize=7,color=GRAY)
    save(fig, "fig03_static_network_reconstruction")


def fig04() -> None:
    fig, axs = plt.subplots(2, 1, figsize=(7.2, 3.8))
    lanes = [
        ("a", "Spatial asset mapping", [("Public assets", "generation · storage · load"), ("Deduplicate", "name · coordinates · capacity"), ("Evidence match", "facility · voltage · distance"), ("Mapped buses", "stable asset and bus IDs")]),
        ("b", "Temporal alignment", [("REN / E-REDES", "source time labels"), ("Normalize", "Europe/Lisbon → UTC"), ("Allocate totals", "observed + explicit residual"), ("Case input", "P/Q · exchange · controls")]),
    ]
    for ax, (lab, title, items) in zip(axs, lanes):
        clean_diagram(ax); panel_label(ax, lab); ax.set_title(title, loc="left")
        for i,(t,s) in enumerate(items):
            x=.03+i*.245; box(ax,(x,.30),(.20,.38),t,s,edge=BLUE if i==3 else BLACK,face=PALE if i==3 else "white")
            if i<3: arrow(ax,(x+.20,.49),(x+.245,.49),BLUE)
    fig.text(.5,.015,"Spatial and temporal lanes join only through stable bus identifiers and interval_start_utc; unmatched totals remain explicit residuals.",ha="center",fontsize=7,color=GRAY)
    save(fig, "fig04_asset_mapping_alignment")


def fig05() -> None:
    fig, axs = plt.subplots(1, 3, figsize=(7.2, 3.2))
    # a
    ax=axs[0]; clean_diagram(ax); panel_label(ax,"a"); ax.set_title("Case assembly",loc="left")
    for i,t in enumerate(["Static network","Nodal P and Q","Generator controls","Boundary exchange"]):
        box(ax,(.03,.76-i*.19),(.43,.13),t,edge=BLACK)
        arrow(ax,(.47,.825-i*.19),(.66,.50),GRAY)
    box(ax,(.68,.36),(.28,.28),"15-minute case","topology + state",edge=ORANGE,face=PALE)
    # b
    ax=axs[1]; clean_diagram(ax); panel_label(ax,"b"); ax.set_title("Two-stage AC solution",loc="left")
    items=[("AC power flow","P/Q and Q limits"),("Discrete tap control","voltage correction"),("Final AC solution","device-level states")]
    for i,(t,s) in enumerate(items):
        y=.70-i*.27; box(ax,(.16,y),(.68,.18),t,s,edge=BLUE if i==2 else BLACK,face=PALE if i==2 else "white")
        if i<2: arrow(ax,(.50,y),(.50,y-.08),BLUE)
    # c
    ax=axs[2]; clean_diagram(ax); panel_label(ax,"c"); ax.set_title("Monthly execution",loc="left")
    items=["Read-only source DB","Independent worker solves","SSD result batches","Single database writer","Atomic monthly publication"]
    for i,t in enumerate(items):
        y=.80-i*.16; box(ax,(.12,y),(.76,.11),t,edge=ORANGE if i==4 else BLACK,face=PALE if i==4 else "white")
        if i<4: arrow(ax,(.50,y),(.50,y-.045),BLUE)
    save(fig, "fig05_case_assembly_execution")


def fig06() -> None:
    fig, (ax1,ax2) = plt.subplots(1,2,figsize=(7.2,3.8),gridspec_kw={"width_ratios":[1.35,1]})
    clean_diagram(ax1); clean_diagram(ax2); panel_label(ax1,"a"); panel_label(ax2,"b")
    ax1.set_title("Layered database model",loc="left"); ax2.set_title("Stable join keys and quality gates",loc="left")
    layers=[("Raw sources","raw_eredes · raw_osm · raw_dgeg · raw_documents"),("Reference and provenance","source catalog · field dictionary · public inventories"),("Static grid","buses · lines · transformers · assets"),("Scenario inputs","15-minute allocations · exchange · controls"),("Monthly results","case · bus · line · transformer states"),("Validation","coverage · balance · external comparison")]
    for i,(t,s) in enumerate(layers):
        y=.82-i*.14; box(ax1,(.04,y),(.90,.105),t,s,edge=ORANGE if i==4 else BLACK,face=PALE if i in (0,4) else "white",align="left")
        if i<5: arrow(ax1,(.49,y),(.49,y-.032),GRAY)
    keys=[("model_id","static model version"),("device_id","physical element"),("interval_start_utc","operating instant"),("case_id","solved case"),("source_record_id","field-level lineage")]
    for i,(k,v) in enumerate(keys):
        y=.82-i*.13; ax2.plot([.07,.15],[y,y],color=BLUE,lw=1.5); ax2.text(.19,y,k,va="center",fontweight="bold",fontsize=7.5); ax2.text(.19,y-.055,v,va="center",fontsize=7.0,color=GRAY)
    box(ax2,(.08,.08),(.84,.16),"Quality-control gates","coverage · conservation · convergence · completeness",edge=ORANGE,face=PALE)
    save(fig, "fig06_database_provenance_qc")


def _load_geo_and_peak():
    geo = ROOT / "portuguese_hv_network" / "site" / "public" / "data" / "pt60"
    lines = gpd.read_file(geo / "lines.geojson").to_crs(3763)
    buses = gpd.read_file(geo / "buses.geojson").to_crs(3763)
    model = ROOT / "portuguese_hv_network" / "outputs" / "annual_nminus1_panel_v2" / "models" / "PT60_ANNUAL_PEAK_LOAD_20260115_1215_+0000_solved.json"
    net = pp.from_json(str(model))
    return lines, buses, net


def fig07() -> None:
    lines, buses, net = _load_geo_and_peak()
    load_map = dict(zip(net.line.line_id.astype(str), net.res_line.loading_percent.astype(float)))
    vm_map = dict(zip(net.bus.bus_id.astype(str), net.res_bus.vm_pu.astype(float)))
    lines["loading_percent"] = lines["id"].astype(str).map(load_map)
    buses["vm_pu"] = buses["id"].astype(str).map(vm_map)
    fig, axs = plt.subplots(1,2,figsize=(7.2,5.0))
    ax=axs[0]; panel_label(ax,"a")
    vcols={60:ORANGE,130:PURPLE,150:GOLD,220:BLUE,400:BLACK}
    for kv in [60,130,150,220,400]:
        q=lines[np.isclose(lines.voltage_kv.astype(float),kv)]
        q.plot(ax=ax,color=vcols[kv],linewidth=.24 if kv<200 else .55,label=f"{kv} kV",alpha=.85)
    ax.set_aspect("equal"); ax.axis("off"); ax.legend(loc="lower left",ncols=2,handlelength=1.5,columnspacing=.9)
    ax.text(.01,.99,"Static geographic network",transform=ax.transAxes,ha="left",va="top",fontsize=8)
    # Build a NetworkX graph from pandapower connectivity while preserving projected bus positions.
    coord={str(r.id):(r.geometry.x,r.geometry.y) for _,r in buses.dropna(subset=["geometry"]).iterrows()}
    idx_to_id=net.bus.bus_id.astype(str).to_dict(); G=nx.MultiGraph()
    for idx,row in net.line.iterrows():
        u=idx_to_id.get(int(row.from_bus)); v=idx_to_id.get(int(row.to_bus))
        if u in coord and v in coord and bool(row.in_service):
            G.add_edge(u,v,key=int(idx),loading=float(net.res_line.at[idx,"loading_percent"]),line_id=str(row.line_id))
    pos={n:coord[n] for n in G.nodes}
    ax=axs[1]; panel_label(ax,"b")
    edges=list(G.edges(keys=True)); vals=np.array([G.edges[e]["loading"] for e in edges])
    nx.draw_networkx_edges(G,pos,ax=ax,edgelist=edges,edge_color=vals,edge_cmap=plt.cm.viridis,edge_vmin=0,edge_vmax=120,width=.32,alpha=.9)
    bq=buses[buses.id.astype(str).isin(G.nodes)].copy(); vm=bq.vm_pu.fillna(1).to_numpy()
    sc=ax.scatter(bq.geometry.x,bq.geometry.y,c=vm,cmap="coolwarm",norm=colors.TwoSlopeNorm(vmin=.90,vcenter=1.0,vmax=1.10),s=2.2,linewidths=0,zorder=3)
    ax.set_aspect("equal"); ax.axis("off"); ax.text(.01,.99,"Solved annual peak-load state",transform=ax.transAxes,ha="left",va="top",fontsize=8)
    cb1=fig.colorbar(plt.cm.ScalarMappable(norm=colors.Normalize(0,120),cmap="viridis"),ax=ax,fraction=.035,pad=.01); cb1.set_label("Line loading (%)")
    cb2=fig.colorbar(sc,ax=ax,fraction=.035,pad=.08); cb2.set_label("Bus voltage (p.u.)")
    total_load=float(net.load.p_mw.sum()) if len(net.load) else np.nan
    fig.text(.5,.015,f"Annual peak input: 2026-01-15 12:15 UTC · model load {total_load:,.0f} MW · EPSG:3763 · line geometry from public-source reconstruction",ha="center",fontsize=7.0,color=GRAY)
    save(fig,"fig07_geographic_operating_state")


def load_scope() -> pd.DataFrame:
    p=ROOT/"portuguese_hv_network"/"outputs"/"validation_experiments"/"scope_matched_validation_timeseries.csv"
    d=pd.read_csv(p,low_memory=False)
    d["local_time"]=pd.to_datetime(d.local_time,utc=True).dt.tz_convert("Europe/Lisbon")
    return d


def fig08() -> None:
    d=load_scope()
    fig,axs=plt.subplots(2,2,figsize=(7.2,4.8))
    # a role matrix
    ax=axs[0,0]; panel_label(ax,"a")
    rows=["E-REDES substation load","REN load and generation","Cross-border exchange","Weather / RNT balance"]
    cols=["Case input","Spatial allocation","Boundary","Context / validation"]
    M=np.array([[0,1,0,1],[1,0,0,1],[1,0,1,0],[0,0,0,1]])
    ax.pcolormesh(np.arange(M.shape[1]+1)-.5,np.arange(M.shape[0]+1)-.5,M,cmap=colors.ListedColormap(["white",SKY]),vmin=0,vmax=1,shading="flat",edgecolors=LIGHT,linewidth=.4)
    ax.set_ylim(M.shape[0]-.5,-.5)
    ax.set_xticks(range(4),cols,rotation=24,ha="right"); ax.set_yticks(range(4),rows); ax.tick_params(length=0); ax.set_title("Source roles",loc="left")
    for sp in ax.spines.values():sp.set_visible(True);sp.set_color(LIGHT)
    # b coverage
    ax=axs[0,1]; panel_label(ax,"b"); ax.set_title("Common modeling window",loc="left")
    start=d.local_time.min();end=d.local_time.max(); names=["Model cases","E-REDES aligned","REN operation","Validation tables"]
    for i,name in enumerate(names):ax.plot([start,end],[i,i],lw=5,color=BLUE if i<3 else ORANGE,solid_capstyle="butt")
    ax.set_yticks(range(4),names);ax.set_ylim(-.6,3.6);ax.invert_yaxis();ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2));ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"));ax.tick_params(axis="x",rotation=25);ax.spines[["left","bottom"]].set_visible(False);ax.tick_params(axis="y",length=0)
    # c week
    ax=axs[1,0]; panel_label(ax,"c");ax.set_title("Natural week containing maximum system load",loc="left")
    t=d.loc[d.observed_load_mw.idxmax(),"local_time"]; startw=(t-pd.Timedelta(days=t.weekday())).normalize();q=d[(d.local_time>=startw)&(d.local_time<startw+pd.Timedelta(days=7))]
    ax.plot(q.local_time,q.observed_load_mw,label="Load",color=BLACK);ax.plot(q.local_time,q.observed_generation_total_mw,label="Generation",color=BLUE);ax.plot(q.local_time,q.observed_net_import_mw,label="Net import",color=ORANGE)
    ax.axhline(0,color=GRAY,lw=.5);ax.set_ylabel("Power (MW)");ax.xaxis.set_major_locator(mdates.DayLocator(interval=2));ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"));ax.legend(ncols=3,loc="upper center");ax.grid(axis="y",color=LIGHT,lw=.5)
    # d monthly completeness
    ax=axs[1,1];panel_label(ax,"d");ax.set_title("Monthly cases and convergence",loc="left")
    g=d.groupby(d.local_time.dt.strftime("%Y-%m")).agg(cases=("case_id","size"),converged=("converged","sum"))
    x=np.arange(len(g));ax.bar(x,g.cases,color=SKY,width=.72,label="Cases");ax.scatter(x,g.converged,color=BLACK,s=10,label="Converged",zorder=3)
    ax.set_xticks(x,[s[5:] for s in g.index]);ax.set_xlabel("Month (2025-05 to 2026-03)");ax.set_ylabel("15-minute cases");ax.set_ylim(0,g.cases.max()*1.16);ax.legend(ncols=2);ax.grid(axis="y",color=LIGHT,lw=.5)
    save(fig,"fig08_timeseries_coverage")


def fig11() -> None:
    d=load_scope()
    daily=d.groupby(d.local_time.dt.date).agg(pt60=("ren_consumption_mw","mean"),external=("external_load_mw","mean"),storage=("ren_storage_load_mw","mean")).reset_index()
    profile=d.groupby("minute_of_day").agg(pt60=("ren_consumption_mw","mean"),external=("external_load_mw","mean"),storage=("ren_storage_load_mw","mean")).reset_index()
    profile[["pt60","external"]]=profile[["pt60","external"]]/profile[["pt60","external"]].mean()
    fig,axs=plt.subplots(1,2,figsize=(7.2,2.9),gridspec_kw={"width_ratios":[1.65,1]})
    ax=axs[0];panel_label(ax,"a");ax.plot(pd.to_datetime(daily.local_time),daily.pt60,color=BLACK,label="REN / SimPT60 consumption");ax.plot(pd.to_datetime(daily.local_time),daily.external,color=BLUE,ls="--",label="E-REDES public consumption");ax.set_ylabel("Daily mean load (MW)");ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2));ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"));ax.grid(axis="y",color=LIGHT,lw=.5);ax.legend(loc="upper left")
    ax2=ax.twinx();ax2.plot(pd.to_datetime(daily.local_time),daily.storage,color=ORANGE,lw=.7,label="Storage load");ax2.set_ylabel("Storage load (MW)",color=ORANGE);ax2.tick_params(axis="y",colors=ORANGE);ax2.spines["right"].set_color(ORANGE)
    ax=axs[1];panel_label(ax,"b");hours=profile.minute_of_day/60;ax.plot(hours,profile.pt60,color=BLACK,label="REN / SimPT60");ax.plot(hours,profile.external,color=BLUE,ls="--",label="E-REDES");ax.axhline(1,color=GRAY,lw=.5);ax.set_xlabel("Local hour");ax.set_ylabel("Normalized load");ax.set_xticks([0,6,12,18,24]);ax.grid(axis="y",color=LIGHT,lw=.5);ax.legend()
    save(fig,"fig11_load_scale_intraday")


def fig13() -> None:
    d=load_scope()
    daily=d.groupby(d.local_time.dt.date).agg(wind=("ren_wind_mw","mean"),wind_ext=("external_wind_mw","mean"),solar=("ren_solar_mw","mean"),solar_ext=("external_solar_mw","mean"),hydro=("ren_hydro_mw","mean"),hydro_ext=("external_hydro_mw","mean"),ac=("model_ac_balance_closure_mw",lambda s:s.abs().mean()),ren=("ren_input_balance_residual_mw",lambda s:s.abs().mean()),external=("external_balance_gap_mw",lambda s:s.abs().mean())).reset_index()
    fig,axs=plt.subplots(2,2,figsize=(7.2,4.5))
    series=[("a","Wind","wind","wind_ext"),("b","Solar","solar","solar_ext"),("c","Hydro","hydro","hydro_ext")]
    for ax,(lab,title,a,b) in zip(axs.flat[:3],series):
        panel_label(ax,lab);x=pd.to_datetime(daily.local_time);ax.plot(x,daily[a],color=BLACK,label="REN national input");ax.plot(x,daily[b],color=BLUE,ls="--",label="E-REDES distribution");ax.set_title(title,loc="left");ax.set_ylabel("Daily mean (MW)");ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3));ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"));ax.grid(axis="y",color=LIGHT,lw=.5)
    axs[0,0].legend(loc="upper left")
    ax=axs[1,1];panel_label(ax,"d");labels=["AC model\nclosure","REN input\nresidual","E-REDES\naccounting gap"];vals=[daily.ac.mean(),daily.ren.mean(),daily.external.mean()];cols=[BLUE,ORANGE,GRAY]
    ax.barh(range(3),vals,color=cols);ax.set_yticks(range(3),labels);ax.invert_yaxis();ax.set_xlabel("Mean absolute residual (MW)");ax.set_xscale("log");ax.set_xlim(1e-7,50);ax.set_xticks([1e-6,1e-3,1,10],["1e-6","1e-3","1","10"]);ax.minorticks_off();ax.grid(axis="x",color=LIGHT,lw=.5)
    for i,v in enumerate(vals):ax.text(v,i,f"  {v:.3g}",va="center",fontsize=7)
    save(fig,"fig13_generation_balance")


def fig18() -> None:
    p=ROOT/"portuguese_hv_network"/"outputs"/"annual_nminus1_panel_v2"/"annual_nminus1_panel.csv"
    d=pd.read_csv(p,low_memory=False)
    order=["MAX_NET_EXPORT","MAX_NET_IMPORT","MAX_SOLAR","MAX_WIND","MINIMUM_LOAD","PEAK_LOAD"]
    names=["Max export","Max import","Max solar","Max wind","Min load","Peak load"]
    fig,axs=plt.subplots(1,3,figsize=(7.2,3.4),gridspec_kw={"width_ratios":[1.0,1.0,1.45]})
    # a
    ax=axs[0];panel_label(ax,"a");strict=[];incremental=[];remaining=[]
    for r in order:
        q=d[d.representative_role==r];s=int(q.passes_screen.fillna(False).sum());inc=int(q.passes_incremental_contingency_screen.fillna(False).sum());strict.append(s);incremental.append(max(inc-s,0));remaining.append(len(q)-inc)
    x=np.arange(6);ax.bar(x,strict,color=BLUE,label="Absolute pass");ax.bar(x,incremental,bottom=strict,color=ORANGE,label="Incremental-only pass");ax.bar(x,remaining,bottom=np.array(strict)+np.array(incremental),color=LIGHT,label="Remaining")
    ax.set_xticks(x,names,rotation=35,ha="right");ax.set_ylabel("Contingency cases");ax.set_ylim(0,1750);ax.legend(fontsize=7);ax.grid(axis="y",color=LIGHT,lw=.5)
    # b
    ax=axs[1];panel_label(ax,"b");metrics=[]
    for r in order:
        q=d[d.representative_role==r];metrics.append([int(q.material_islanding.fillna(False).sum()),int(q.incremental_thermal_violation.fillna(False).sum()),int(q.incremental_voltage_violation.fillna(False).sum()),int((~q.primary_converged.fillna(False)).sum())])
    M=np.array(metrics).T;im=ax.pcolormesh(np.arange(7)-.5,np.arange(5)-.5,M,cmap="Blues",vmin=0,vmax=max(M.max(),1),shading="flat",edgecolors="white",linewidth=.4);ax.set_ylim(3.5,-.5);ax.set_xticks(range(6),names,rotation=35,ha="right");ax.set_yticks(range(4),["Material island","New thermal","New voltage","Non-converged"])
    for i in range(4):
        for j in range(6):ax.text(j,i,str(M[i,j]),ha="center",va="center",fontsize=7,color="white" if M[i,j]>.55*M.max() else BLACK)
    # c
    ax=axs[2];panel_label(ax,"c")
    q=d.copy();q["severity"]=q.maximum_line_loading_percent.fillna(0)+.4*q.unsupplied_load_mw.fillna(0);top=q.groupby("element_id").severity.max().nlargest(8).index.tolist();A=np.full((8,6),np.nan);ann=[["" for _ in range(6)] for _ in range(8)];labels=[]
    for i,e in enumerate(top):
        z=q[q.element_id==e];name=str(z.element_name.dropna().iloc[0] if z.element_name.notna().any() else e);labels.append(name[:31]+("…" if len(name)>31 else ""))
        for j,r in enumerate(order):
            zz=z[z.representative_role==r]
            if zz.empty:continue
            load=float(zz.maximum_line_loading_percent.fillna(0).max());lost=float(zz.unsupplied_load_mw.fillna(0).max());A[i,j]=max(load,100+min(lost,100));ann[i][j]=f"{lost:.0f} MW" if lost>.01 else f"{load:.0f}%"
    heat_colors=["#FFF3D6","#F6C56E","#E77C43","#A93226"]
    heat_bounds=[0,80,100,150,300]
    cmap=colors.ListedColormap(heat_colors);cmap.set_bad("#EEEEEE");norm=colors.BoundaryNorm(heat_bounds,cmap.N)
    im=ax.pcolormesh(np.arange(7)-.5,np.arange(9)-.5,np.ma.masked_invalid(A),cmap=cmap,norm=norm,shading="flat",edgecolors="white",linewidth=.4);ax.set_ylim(7.5,-.5);ax.set_xticks(range(6),names,rotation=35,ha="right");ax.set_yticks(range(8),labels)
    for i in range(8):
        for j in range(6):
            if np.isfinite(A[i,j]):ax.text(j,i,ann[i][j],ha="center",va="center",fontsize=7.0,color="white" if A[i,j]>145 else BLACK)
    ax.legend(handles=[Patch(facecolor=c,label=l) for c,l in zip(heat_colors,["<80","80–100","100–150","≥150"])],title="Loading / severity",loc="upper center",bbox_to_anchor=(.5,-.22),ncols=4,fontsize=7,title_fontsize=7,handlelength=.8,columnspacing=.8)
    save(fig,"fig18_annual_nminus1_panel")


def update_manifest() -> None:
    m=pd.read_csv(MANIFEST,keep_default_na=False)
    for i,row in m.iterrows():
        hashes=[]
        for raw in str(row.source_data).split(";"):
            p=(ROOT/raw).resolve()
            if p.is_file():hashes.append(sha256(p))
        m.at[i,"source_sha256"]=";".join(hashes)
        pdf=ROOT/str(row.output_file)
        m.at[i,"status"]="validated" if pdf.is_file() else "blocked"
    m.to_csv(MANIFEST,index=False)


def main() -> int:
    with plt.style.context(STYLE):
        for fn in [fig01,fig02,fig03,fig04,fig05,fig06,fig07,fig08,fig11,fig13,fig18]:
            fn()
    update_manifest()
    print(json.dumps({"output":str(OUT),"figures":sorted(p.name for p in OUT.glob("*.pdf")),"manifest":str(MANIFEST)},indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
