#!/usr/bin/env python3
"""Power-flow check of MV-URBAN-CABLE-V1 at each PTD's public peak proxy.

Each (MV root, voltage) urban network is solved on its own with the closed
cables only (ties open).  Feeder anchors (root busbar or OSM tap point) are
voltage sources at 1.0 p.u.; PTD loads = peak_load_proxy_mva at pf 0.95.
This isolates the cable network; the upstream MV/HV drop is not included.
"""
from __future__ import annotations

import json
import time
import warnings
from pathlib import Path

import duckdb
import numpy as np
import pandapower as pp
import pandas as pd

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output/all_voltage/mv_urban_cable_v1"


def main() -> None:
    t0 = time.time()
    mem = duckdb.connect()
    segs = mem.sql(f"select * from '{OUT / 'mv_urban_cable_segment.parquet'}'").df()
    ptd = mem.sql(f"select * from '{OUT / 'mv_urban_cable_ptd.parquet'}'").df().set_index("ptd_code")
    rows = []
    for (root, kv), g in segs.groupby(["root", "kv"]):
        net = pp.create_empty_network()
        closed = g[~g.normally_open]
        names = pd.unique(pd.concat([closed.from_node, closed.to_node]))
        bus = {n: pp.create_bus(net, vn_kv=float(kv), name=n) for n in names}
        for n in names:
            if not n.startswith(("ROOTANCHOR:", "OSMTAP:")):
                p = float(ptd.at[n, "p_mva"]) if n in ptd.index else 0.0
                pp.create_load(net, bus[n], p_mw=p * 0.95, q_mvar=p * np.sqrt(1 - 0.95 ** 2))
            else:
                pp.create_ext_grid(net, bus[n], vm_pu=1.0)
        for r in closed.itertuples():
            pp.create_line_from_parameters(net, bus[r.from_node], bus[r.to_node], length_km=float(r.length_km), r_ohm_per_km=r.r_ohm_per_km,
                                           x_ohm_per_km=r.x_ohm_per_km, c_nf_per_km=r.c_nf_per_km, max_i_ka=r.max_i_ka)
        try:
            pp.runpp(net, numba=False)
            ok = bool(net.converged)
        except Exception:
            ok = False
        rows.append({"root": root, "kv": kv, "ptds": int((~pd.Series(names).str.startswith(("ROOTANCHOR:", "OSMTAP:"))).sum()),
                     "converged": ok, "vm_min_pu": float(net.res_bus.vm_pu.min()) if ok else np.nan,
                     "line_loading_max_pct": float(net.res_line.loading_percent.max()) if ok else np.nan,
                     "load_mw": float(net.load.p_mw.sum()), "cable_km": float(closed.length_km.sum())})
    res = pd.DataFrame(rows)
    res.to_csv(OUT / "mv_urban_cable_powerflow.csv", index=False)
    summary = {
        "networks": len(res), "converged": int(res.converged.sum()), "ptds": int(res.ptds.sum()),
        "vm_min_pu": round(float(res.vm_min_pu.min()), 4), "vm_min_p01": round(float(res.vm_min_pu.quantile(0.01)), 4),
        "networks_vm_below_0_95": int((res.vm_min_pu < 0.95).sum()),
        "line_loading_max_pct": round(float(res.line_loading_max_pct.max()), 1), "networks_loading_over_100": int((res.line_loading_max_pct > 100).sum()),
        "load_mw": round(float(res.load_mw.sum()), 1), "seconds": round(time.time() - t0, 1),
        "status": "PASS" if res.converged.all() and (res.vm_min_pu >= 0.95).all() and (res.line_loading_max_pct <= 100).all() else "REVIEW",
        "note": "Cable network only, anchors at 1.0 p.u.; upstream MV/HV drop excluded.",
    }
    (OUT / "mv_urban_cable_powerflow.validation.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
