#!/usr/bin/env python3
"""Pilot: ABCN power flow on geographic LV trees (poles + OSM streets/buildings) versus the compact
two-section design, both solved with the same backward/forward sweep, at each PTD's root-peak instant.

Cable scenarios for the geographic trees:
  design   = the PTD's compact-design cable (chosen for 2 x 50 m feeders)
  standard = typical trunk cable by PTD type: overhead LXS 4x95 (0.359 ohm/km, 230 A),
             cabin/underground LXAV 3x185+95 (0.21 ohm/km, 375 A)
Mutual 4x4 terms = LV_4WIRE_PROXY_V1; transformer = positive-sequence series impedance (root-peak design Sn).
Loads: root-peak PTD load (ptd_snapshot of the published four-wire batch), pf 0.97, public phase shares,
spread over customer points (buildings at poles / street nodes) proportional to building counts."""
import json, math, re, sys, time
from collections import defaultdict, deque
from pathlib import Path
import duckdb, numpy as np, pandas as pd
from scipy.spatial import Delaunay, cKDTree
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import minimum_spanning_tree, dijkstra, connected_components

ROOT = Path(__file__).resolve().parents[1]
DB = Path(__import__("os").environ.get("LV_DB", ROOT / "output/all_voltage/all_voltage_staging.duckdb"))
POLES = ROOT / "output/feasibility/lv_poles_national.csv"
import os
OSMDIR = Path(os.environ.get("LV_OSM_DIR", ROOT / "output/all_voltage/mv_customer_pilot"))
SNAP = ROOT / "output/all_voltage/four_wire_root_peak_design/ptd_snapshot.csv"
OUT = Path(os.environ.get("LV_GEO_OUT", ROOT / "output/all_voltage/lv_geo_pilot"))
POLE_CACHE = ROOT / "output/all_voltage/lv_geo_national/poles_lonlat.npy"


def load_poles():
    if POLE_CACHE.exists():
        return np.load(POLE_CACHE)
    df = pd.read_csv(POLES, sep=";", usecols=[0]).iloc[:, 0].str.split(",", expand=True).astype(float)
    arr = np.c_[df[1].values, df[0].values]
    POLE_CACHE.parent.mkdir(parents=True, exist_ok=True); np.save(POLE_CACHE, arr); return arr
POLE_MAX_M, SPAN_MAX_M, BLDG_POLE_M, BLDG_MAX_M, BLDG_MIN_AREA = 600.0, 150.0, 40.0, 800.0, 30.0
STREET_FROM_POLES = os.environ.get("LV_STREET_FROM_POLES", "0") == "1"   # extend from nearest own pole instead of always from the PTD
POLE_SNAP_M = 50.0
STD = {"overhead": ("LXS 4 x 95", 0.359, 0.1, 230.0), "underground": ("LXAV 3x185+95", 0.21, 0.1, 375.0)}
VLN = 400 / math.sqrt(3)
SRC = np.array([VLN * np.exp(1j * a) for a in (0, -2 * math.pi / 3, 2 * math.pi / 3)] + [0j])


def zmat(r, x, mutual):
    z = mutual.copy(); np.fill_diagonal(z, complex(r, x)); return z


def neutral_r(designation, r):
    """r3.1: neutral resistance scaled by the phase/neutral cross-section ratio (e.g. LXAV 3x185+95 -> r*185/95)."""
    m = re.search(r"(\d+)\s*[x\u00d7]\s*(\d+(?:\.\d+)?)\s*\+\s*(\d+(?:\.\d+)?)", str(designation))
    return r * float(m.group(2)) / float(m.group(3)) if m else r


def zmat4(r, x, mutual, designation):
    z = zmat(r, x, mutual); z[3, 3] = complex(neutral_r(designation, r), x); return z


def sweep(parent, length, loads, z, zt, bond, order, par=None):
    """Radial ABCN sweep. parent[k] (root=-1), length[k] km of branch parent->k, loads[k] 3 complex VA."""
    n = len(parent); V = np.tile(SRC, (n, 1)); children = defaultdict(list)
    for k in order[1:]:
        children[parent[k]].append(k)
    for it in range(1, 101):
        d = V[:, :3] - V[:, 3:4]
        if np.min(np.abs(d)) < 100: return None, it
        I = np.zeros((n, 4), complex); I[:, :3] = np.conj(loads / d); I[:, 3] = -I[:, :3].sum(1)
        Ib = I.copy()
        for k in reversed(order[1:]): Ib[parent[k]] += Ib[k]
        tot = Ib[order[0]]
        Vn = V.copy(); Vn[order[0], :3] = SRC[:3] - zt * tot[:3]; Vn[order[0], 3] = bond * (-tot[:3].sum())
        for k in order[1:]: Vn[k] = Vn[parent[k]] - (z[k] if z.ndim == 3 else z) @ Ib[k] * length[k] / (1 if par is None else par[k])
        ch = np.max(np.abs(Vn - V)); V = Vn
        if ch < 1e-6:
            return (V, Ib), it
    return None, 100


def result(V, Ib, parent, order, imax):
    vpu = np.abs(V[:, :3] - V[:, 3:4]) / VLN
    br = [k for k in order[1:]]
    iph = np.max(np.abs(Ib[br, :3])) if br else 0.0
    return float(vpu.min()), float(vpu.max()), float(iph), float(iph / imax * 100)


def run(cc):
    t0 = time.time()
    con = duckdb.connect(str(DB), read_only=True)
    ptd = con.sql("""select a.ptd_code, a.ptd_lon lon, a.ptd_lat lat, coalesce(p.construction_type,'') ctype
                     from candidate.ptd_mv_attachment_assessment a left join candidate.ptd_public_attributes p using(ptd_code)""").df()
    own0 = ptd.ptd_code.str[:4] == cc
    w, s, e, n = ptd.lon[own0].min() - .01, ptd.lat[own0].min() - .01, ptd.lon[own0].max() + .01, ptd.lat[own0].max() + .01
    ptd = ptd[ptd.lon.between(w - .03, e + .03) & ptd.lat.between(s - .03, n + .03)].reset_index(drop=True)
    own = (ptd.ptd_code.str[:4] == cc).values
    codes = list(ptd.ptd_code[own])
    shares = con.sql("select ptd_code, phase, ptd_total_share from phase.lv_load_shares where substr(ptd_code,1,4)=?", params=[cc]).df() \
        .pivot_table(index="ptd_code", columns="phase", values="ptd_total_share", aggfunc="sum")
    des = con.sql("select ptd_code, feeder_count, section_length_km, cable_designation, r_hot_ohm_per_km r, x_ohm_per_km x, permissible_current_a imax "
                  "from phase.lv_feeder_root_peak_design_scenario where substr(ptd_code,1,4)=?", params=[cc]).df().set_index("ptd_code")
    tr = con.sql("""select t.ptd_code, t.lv_kv, t.vk_percent, t.vkr_percent, d.design_sn_mva, g.source_neutral_bond_ohm bond
                    from parameter.ptd_transformer_parameters t join parameter.ptd_transformer_root_peak_design_scenario d using(ptd_code)
                    join phase.ptd_neutral_grounding g using(ptd_code) where substr(t.ptd_code,1,4)=?""", params=[cc]).df().set_index("ptd_code")
    mut = np.zeros((4, 4), complex)
    for a, b, r, x in con.sql("select from_conductor,to_conductor,r_ohm_per_km,x_ohm_per_km from phase.impedance_matrix where archetype_id='LV_4WIRE_PROXY_V1'").fetchall():
        mut["ABCN".index(a), "ABCN".index(b)] = complex(r, x)
    snap = pd.read_csv(SNAP).set_index("ptd_code")
    lat0 = float(ptd.lat.mean()); kx = 111320 * math.cos(math.radians(lat0)); ky = 110540.0
    X = lambda lon, lat: np.c_[np.asarray(lon) * kx, np.asarray(lat) * ky]
    P = X(ptd.lon, ptd.lat); ptree = cKDTree(P); idx_of = {c: i for i, c in enumerate(ptd.ptd_code)}
    pl = load_poles(); pl = pl[(pl[:, 0] >= w) & (pl[:, 0] <= e) & (pl[:, 1] >= s) & (pl[:, 1] <= n)]
    poles = pd.DataFrame({"lon": pl[:, 0], "lat": pl[:, 1]})
    Q = np.unique(np.round(X(poles.lon, poles.lat), 1), axis=0); dq, qo = ptree.query(Q); Q, qo = Q[dq <= POLE_MAX_M], qo[dq <= POLE_MAX_M]
    osm = json.loads((OSMDIR / f"osm_{cc}.json").read_text())
    B = []
    for b in osm["buildings"]:
        if "p" in b:
            if b["a"] >= BLDG_MIN_AREA: B.append(X([b["p"][0]], [b["p"][1]])[0])
            continue
        c = np.array(b["c"]); xy = X(c[:, 0], c[:, 1])
        if 0.5 * abs(np.dot(xy[:-1, 0], xy[1:, 1]) - np.dot(xy[1:, 0], xy[:-1, 1])) >= BLDG_MIN_AREA: B.append(xy.mean(0))
    B = np.array(B).reshape(-1, 2)
    if len(Q) and len(B):
        bq_d, bq = cKDTree(Q).query(B)
    else:
        bq_d, bq = np.full(len(B), np.inf), np.zeros(len(B), int)
    db, bo = ptree.query(B) if len(B) else (np.zeros(0), np.zeros(0, int))
    near = bq_d <= BLDG_POLE_M
    nid, xs, rows, cols, wt = {}, [], [], [], []
    for r in osm["roads"]:
        idx = []
        for p in map(tuple, r["c"]):
            if p not in nid: nid[p] = len(xs); xs.append(X([p[0]], [p[1]])[0])
            idx.append(nid[p])
        for u, v in zip(idx, idx[1:]):
            d_ = float(np.hypot(*(xs[u] - xs[v]))); rows += [u, v]; cols += [v, u]; wt += [d_, d_]
    if not xs:
        xs = [np.array([0.0, 0.0])]
    R = np.array(xs); G = coo_matrix((wt, (rows, cols)), shape=(len(R), len(R))).tocsr(); rtree = cKDTree(R)
    out = []; segs = []
    WRITE_SEG = os.environ.get("LV_WRITE_SEGMENTS", "0") == "1"
    for code in codes:
        i = idx_of[code]
        if code not in snap.index or code not in des.index or code not in tr.index or code not in shares.index: continue
        sp = snap.loc[code]; dz = des.loc[code]; t = tr.loc[code]
        pmw = float(sp.load_p_mw); pv = float(sp.pv_p_mw); qmv = pmw * math.tan(math.acos(0.97))
        sh = shares.loc[code, ["A", "B", "C"]].values.astype(float)
        base = t.lv_kv ** 2 / t.design_sn_mva if t.design_sn_mva > 0 else 1e9
        zt = complex(t.vkr_percent / 100 * base, math.sqrt(max(t.vk_percent ** 2 - t.vkr_percent ** 2, 0)) / 100 * base)
        bond = float(t.bond or 0)
        # ---- geographic tree: nodes = [PTD] + poles + street nodes + building drops
        coords = [P[i]]; parent = [-1]; length = [0.0]; weight = [0.0]; kind = ["root"]
        pole_ix = np.flatnonzero(qo == i)
        if len(pole_ix):
            pts = np.vstack([P[i], Q[pole_ix]])
            if len(pts) >= 3:
                try:
                    tri = Delaunay(pts); sx = tri.simplices; a = np.r_[sx[:, 0], sx[:, 1], sx[:, 2]]; b = np.r_[sx[:, 1], sx[:, 2], sx[:, 0]]
                except Exception:
                    a = np.zeros(len(pts) - 1, int); b = np.arange(1, len(pts))
            else:
                a = np.array([0]); b = np.array([1])
            wg = np.hypot(*(pts[a] - pts[b]).T) + 1e-6
            M = minimum_spanning_tree(coo_matrix((wg, (a, b)), shape=(len(pts), len(pts)))).tocoo()
            adj = defaultdict(list)
            for u, v, ww in zip(M.row, M.col, M.data): adj[u].append((v, ww)); adj[v].append((u, ww))
            loc = {0: 0}; dq_ = deque([0])
            while dq_:
                u = dq_.popleft()
                for v, ww in adj[u]:
                    if v not in loc:
                        loc[v] = len(coords); coords.append(pts[v]); parent.append(loc[u]); length.append(ww / 1000); weight.append(0.0); kind.append("pole"); dq_.append(v)
            pole_node = {pole_ix[k - 1]: loc[k] for k in loc if k > 0}
            for bi in np.flatnonzero(near & (bo == i)):
                if bq[bi] in pole_node: weight[pole_node[bq[bi]]] += 1
        oh_km = sum(length)
        # street part
        sb = np.flatnonzero((~near) & (bo == i) & (db <= BLDG_MAX_M))
        ug_km = 0.0; ext_km = 0.0
        if len(sb):
            bs, bn = rtree.query(B[sb]); ps, pn = rtree.query(P[i])
            # sources of street routes: the PTD, and (optional) this PTD's poles snapped to the road graph,
            # so that a building beyond pole reach is served by extending the nearest existing network
            src = {int(pn): (0, float(ps), "street")}
            if STREET_FROM_POLES and len(pole_ix):
                qd, qn = rtree.query(Q[pole_ix])
                for qi, d_, rn in zip(pole_ix, qd, qn):
                    if d_ <= POLE_SNAP_M and int(rn) not in src: src[int(rn)] = (pole_node[qi], float(d_), "ext")
            srcs = list(src)
            if len(srcs) == 1:
                dist, pred = dijkstra(G, directed=False, indices=srcs[0], return_predecessors=True, limit=2000); sid = np.full(len(dist), srcs[0])
            else:
                dist, pred, sid = dijkstra(G, directed=False, indices=srcs, return_predecessors=True, limit=2000, min_only=True)
            node_of = {}
            def attach(v):
                kk = src[int(sid[v])][2]; path = []
                while v not in node_of and v not in src:
                    path.append(v); v = pred[v]
                    if v < 0: return None
                if v not in node_of:
                    tn, off, _k = src[v]
                    node_of[v] = len(coords); coords.append(R[v]); parent.append(tn); length.append(off / 1000); weight.append(0.0); kind.append(kk)
                par = node_of[v]
                for u in reversed(path):
                    node_of[u] = len(coords); coords.append(R[u]); parent.append(par); length.append(float(np.hypot(*(R[u] - coords[par]))) / 1000)
                    weight.append(0.0); kind.append(kk); par = node_of[u]
                return par
            for k, bi in enumerate(sb):
                if not np.isfinite(dist[bn[k]]): continue
                nd = attach(int(bn[k]))
                if nd is None: continue
                coords.append(B[bi]); parent.append(nd); length.append(max(bs[k], 5.0) / 1000); weight.append(1.0); kind.append("drop")
            ug_km = sum(l for l, kk in zip(length, kind) if kk == "street")
            ext_km = sum(l for l, kk in zip(length, kind) if kk == "ext")
        nn = len(coords)
        if nn == 1: continue
        wsum = sum(weight)
        if wsum == 0: weight = [0.0] + [1.0] * (nn - 1); wsum = nn - 1
        S_ptd = (pmw + 1j * qmv) * 1e6   # r3.1: load_p_mw is already the station-derived net load (PV included); r3 subtracted PV twice
        loads = np.array([[S_ptd * wk / wsum * sh[ph] for ph in range(3)] for wk in weight], complex)
        order = [0]; ch = defaultdict(list)
        for k in range(1, nn): ch[parent[k]].append(k)
        q_ = deque([0])
        while q_:
            u = q_.popleft()
            for v in ch[u]: order.append(v); q_.append(v)
        parent = np.array(parent); length = np.array(length)
        typ = "overhead" if ctype_is_aerial(ptd.ctype[i]) else "underground"
        rec = {"ptd_code": code, "ctype": ptd.ctype[i], "load_kw": pmw * 1000, "pv_kw": pv * 1000, "nodes": nn, "root_feeders": len(ch[0]),
               "overhead_km": round(oh_km, 3), "street_km": round(ug_km, 3), "pole_ext_km": round(ext_km, 3), "drops_km": round(float(length[[k for k in range(nn) if kind[k] == 'drop']].sum()), 3),
               "design_cable": dz.cable_designation, "compact_feeders": int(dz.feeder_count), "compact_section_km": float(dz.section_length_km)}
        # design refinement (standard cable): parallel cables where ampacity or 0.9 p.u. is violated (max 4 in parallel)
        # r3.1: per-branch cable class (pole/ext = overhead, street = by PTD type, service drop = class of the line it hangs on)
        cls = [typ] * nn
        for k in order[1:]:
            kk = kind[k]
            cls[k] = "overhead" if kk in ("pole", "ext") else (typ if kk == "street" else (cls[parent[k]] if parent[k] > 0 else typ))
        ZB = np.stack([zmat4(STD[c][1], STD[c][2], mut, STD[c][0]) for c in cls]); IMAX = np.array([STD[c][3] for c in cls])
        par = np.ones(nn); z = ZB; imax = IMAX; refined = None
        for rep in range(12):
            sol, it = sweep(parent, length, loads, z, zt, bond, order, par)
            if sol is None:
                par = np.minimum(par + 1, 4); continue
            V, Ib = sol
            need = np.ceil(np.max(np.abs(Ib[:, :3]), axis=1) / imax); need[0] = 1
            vpu = np.abs(V[:, :3] - V[:, 3:4]).min(1) / VLN
            changed = False
            if np.any(need > par):
                par = np.minimum(np.maximum(par, need), 4); changed = True
            elif vpu.min() < 0.9:
                k = int(vpu.argmin()); path = []
                while k > 0: path.append(k); k = parent[k]
                if np.all(par[path] >= 4): refined = sol; break
                par[path] = np.minimum(par[path] + 1, 4); changed = True
            refined = sol
            if not changed: break
        if refined is None:
            rec["geo_refined_status"] = "DIVERGED"
        else:
            V, Ib = refined; vpu = np.abs(V[:, :3] - V[:, 3:4]) / VLN
            ld = float(np.max(np.abs(Ib[1:, :3]).max(1) / (IMAX[1:] * par[1:])) * 100) if nn > 1 else 0
            rec.update({"geo_refined_status": "OK", "geo_refined_vmin": float(vpu.min()), "geo_refined_vmax": float(vpu.max()), "geo_refined_loading_pct": ld,
                        "geo_refined_parallel_km": float((length * (par - 1)).sum()), "geo_refined_max_parallel": int(par.max())})
        if WRITE_SEG:
            C = np.array(coords); lon = C[:, 0] / kx; lat = C[:, 1] / ky
            for k in range(1, nn):
                kk = kind[k]; c = cls[k]; cab_k, r_k, x_k, i_k = STD[c]
                segs.append((code, k, int(parent[k]), kk, "service_drop" if kk == "drop" else c, cab_k, float(length[k]), int(par[k]),
                             round(float(lon[parent[k]]), 6), round(float(lat[parent[k]]), 6), round(float(lon[k]), 6), round(float(lat[k]), 6), float(weight[k]),
                             r_k, x_k, neutral_r(cab_k, r_k), i_k))
        for name, (cab, zz, im) in {"design": (dz.cable_designation, zmat4(dz.r, dz.x, mut, dz.cable_designation), np.full(nn, float(dz.imax))),
                                    "standard": ("PER_BRANCH_STANDARD", ZB, IMAX)}.items():
            sol, it = sweep(parent, length, loads, zz, zt, bond, order)
            if sol is None: rec.update({f"geo_{name}_status": "DIVERGED"}); continue
            Vs, Ibs = sol; vp = np.abs(Vs[:, :3] - Vs[:, 3:4]) / VLN
            vmin, vmax = float(vp.min()), float(vp.max()); ld = float(np.max(np.abs(Ibs[1:, :3]).max(1) / im[1:]) * 100) if nn > 1 else 0.0
            rec.update({f"geo_{name}_status": "OK", f"geo_{name}_vmin": vmin, f"geo_{name}_vmax": vmax, f"geo_{name}_loading_pct": ld, f"geo_{name}_cable": cab})
        # compact design with the same solver: feeder_count identical 2-section feeders, half load each section
        fcount = int(dz.feeder_count); L = float(dz.section_length_km)
        par_c = np.array([-1, 0, 1]); len_c = np.array([0, L, L]); lc = np.array([[0] * 3, [S_ptd / fcount / 2 * sh[p] for p in range(3)], [S_ptd / fcount / 2 * sh[p] for p in range(3)]], complex)
        zt_c = zt * fcount   # one feeder sees the transformer as if carrying fcount x its current
        sol, it = sweep(par_c, len_c, lc, zmat(dz.r, dz.x, mut), zt_c, bond * fcount, [0, 1, 2])
        if sol is not None:
            vmin, vmax, imx, ld = result(*sol, par_c, [0, 1, 2], dz.imax)
            rec.update({"compact_vmin": vmin, "compact_vmax": vmax, "compact_loading_pct": ld, "published_vmin": float(sp.min_phase_voltage_pu)})
        out.append(rec)
    df = pd.DataFrame(out); df.to_csv(OUT / f"lv_geo_powerflow_{cc}.csv", index=False)
    if WRITE_SEG:
        sd = OUT.parent / "segments"; sd.mkdir(parents=True, exist_ok=True)
        sg = pd.DataFrame(segs, columns=["ptd_code", "node", "parent_node", "segment_kind", "cable_class", "standard_cable", "length_km",
                                         "refined_parallel", "lon_from", "lat_from", "lon_to", "lat_to", "load_weight",
                                         "r_ohm_per_km", "x_ohm_per_km", "r_neutral_ohm_per_km", "permissible_current_a"])
        duckdb.sql(f"COPY (SELECT * FROM sg) TO '{sd / f'lv_geo_segments_{cc}.parquet'}' (FORMAT parquet)")
    if df.empty or "geo_refined_vmin" not in df:
        return {"concelho": cc, "ptds_solved": int(len(df)), "seconds": round(time.time() - t0, 1)}
    def stat(col):
        v = df[col].dropna() if col in df else pd.Series(dtype=float)
        if v.empty: return None
        return {"min": round(float(v.min()), 3), "p05": round(float(v.quantile(.05)), 3), "median": round(float(v.median()), 3)}
    summ = {"concelho": cc, "ptds_solved": len(df), "seconds": round(time.time() - t0, 1),
            "compact_same_solver_vmin": stat("compact_vmin"), "published_compact_vmin": stat("published_vmin"),
            "geo_design_cable_vmin": stat("geo_design_vmin"), "geo_standard_cable_vmin": stat("geo_standard_vmin"),
            "geo_refined_vmin": stat("geo_refined_vmin"), "refined_below_0_9": int((df.geo_refined_vmin < 0.9).sum()),
            "refined_overloaded": int((df.geo_refined_loading_pct > 100).sum()), "refined_diverged": int((df.get("geo_refined_status") == "DIVERGED").sum()),
            "refined_extra_parallel_km": round(float(df.geo_refined_parallel_km.sum()), 1),
            "lengths_km": {"overhead": round(float(df.overhead_km.sum()), 1), "street": round(float(df.street_km.sum()), 1), "drops": round(float(df.drops_km.sum()), 1)},
            "ptds_vmin_below_0_9": {"compact": int((df.compact_vmin < 0.9).sum()), "geo_design": int((df.get("geo_design_vmin", pd.Series(dtype=float)) < 0.9).sum()),
                                    "geo_standard": int((df.get("geo_standard_vmin", pd.Series(dtype=float)) < 0.9).sum())},
            "ptds_overloaded": {"compact": int((df.compact_loading_pct > 100).sum()), "geo_design": int((df.get("geo_design_loading_pct", pd.Series(dtype=float)) > 100).sum()),
                                "geo_standard": int((df.get("geo_standard_loading_pct", pd.Series(dtype=float)) > 100).sum())},
            "diverged": {"geo_design": int((df.get("geo_design_status") == "DIVERGED").sum()), "geo_standard": int((df.get("geo_standard_status") == "DIVERGED").sum())}}
    return summ


def ctype_is_aerial(c):
    return str(c).startswith("Aéreo")


if __name__ == "__main__":
    res = [run(cc) for cc in sys.argv[1:]]
    f = OUT / "powerflow_summary.json"; old = json.loads(f.read_text()) if f.exists() else []
    old = [r for r in old if r["concelho"] not in sys.argv[1:]] + res; f.write_text(json.dumps(old, indent=2)); print(json.dumps(res, indent=2))
