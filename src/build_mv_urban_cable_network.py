#!/usr/bin/env python3
"""MV-URBAN-CABLE-V1: synthetic underground MV open-ring cables for PTDs that
have no usable public OSM MV geometry (the 21,134 SYNTHETIC_FALLBACK PTDs).

Why: OSM hardly maps underground MV cables.  E-REDES reports 15,564 km of MV
underground cable (Relatório da Qualidade de Serviço 2024, Tabela 2.1) while the
OSM candidate layer has ~270 km.  The fallback PTDs are the urban/underground
part of the network; today they are connected by single star branches.

Method (all SIMULATED, deterministic):
 1. per (MV root, voltage): PTDs sorted by bearing around the root substation;
 2. consecutive PTDs grouped into feeders (<= CAP_KVA installed and <= CAP_N PTDs);
 3. each feeder = Euclidean MST over {root} + its PTDs  (radial, closed switches);
 4. neighbouring feeders tied end-to-end with one normally-open cable (open ring);
    a single feeder is tied back to the root;
 5. route length = Euclidean length x DETOUR (fixed 1.30); feeders whose PTDs are
    mostly overhead-type or sparse (median spacing > 0.8 km) become overhead rural spurs.
    Official E-REDES totals are used only for validation, not calibration.
Reads the working DuckDB read-only; writes a separate DuckDB + CSV + JSON.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
HV_BUSES = ROOT / "portuguese_hv_network/outputs/tables/buses.csv"
OUT = ROOT / "output/all_voltage/mv_urban_cable_v1"
OFFICIAL_MV_UNDERGROUND_KM = 15564.0   # E-REDES QoS report 2024, Table 2.1 (31 Dec 2024)
OSM_CANDIDATE_CABLE_KM = None          # read from parameter.mv_line_parameters
CAP_KVA_15KV, CAP_N = 8000.0, 12   # feeder capacity scales with voltage (8 MVA installed at 15 kV)
URBAN_MEDIAN_SPACING_KM = 0.8        # feeders with sparser PTDs are treated as overhead rural spurs
MAX_CABLE_SPAN_KM = 2.0              # a single urban-cable link longer than this is modelled as overhead
DETOUR = 1.30   # fixed street-routing factor (not calibrated); official totals are used only for validation
CABLE = {6.0: "336901", 10.0: "336901", 15.0: "336903", 20.0: "336905", 30.0: "336905"}


def xy(lon, lat):
    return np.c_[(np.asarray(lon) + 8.0) * 111320 * math.cos(math.radians(39.5)), (np.asarray(lat) - 39.5) * 110540]


def mst_edges(P: np.ndarray) -> list[tuple[int, int, float]]:
    n = len(P)
    if n < 2:
        return []
    D = np.hypot(P[:, None, 0] - P[None, :, 0], P[:, None, 1] - P[None, :, 1])
    used = np.zeros(n, bool); used[0] = True
    best = D[0].copy(); parent = np.zeros(n, int); edges = []
    for _ in range(n - 1):
        best_masked = np.where(used, np.inf, best); j = int(best_masked.argmin())
        edges.append((int(parent[j]), j, float(D[parent[j], j]))); used[j] = True
        closer = D[j] < best; best[closer] = D[j][closer]; parent[closer] = j
    return edges


MV_CUSTOMER_FILE = ROOT / "data/raw/eredes_mv_customers/mat_at_mt_delivery_points_by_concelho_2025-12.csv"
MV_CUSTOMER_KVA = 630.0     # grouping weight only; loads come from each root's MV residual


def sited_mv_customers(ptd: pd.DataFrame, sites_csv: Path) -> pd.DataFrame:
    """MV customer substations at OSM building sites (build_mv_customer_sites.py); electrical attributes
    (root, voltage, OSM tap candidate) are inherited from the nearest public PTD."""
    S = pd.read_csv(sites_csv, dtype={"cc": str})
    ref = ptd.set_index("ptd_code")
    rows = []
    for r in S.itertuples():
        if r.nearest_ptd not in ref.index:
            continue
        q = ref.loc[r.nearest_ptd]
        rows.append({"ptd_code": r.site_id, "lat": r.lat, "lon": r.lon, "root": q["root"], "kv": q["kv"],
                     "kva": MV_CUSTOMER_KVA, "p_mva": 0.0, "hv_bus": q["hv_bus"], "construction": "Cabine (MV customer, OSM building site)",
                     "osm_lat": q["osm_lat"], "osm_lon": q["osm_lon"], "osm_kv": q["osm_kv"], "osm_seg": q["osm_seg"],
                     "is_fallback": False, "is_customer": True})
    return pd.DataFrame(rows)


def synthetic_mv_customers(ptd: pd.DataFrame) -> pd.DataFrame:
    """Place the public per-municipality count of MT/AT/MAT delivery points (E-REDES open data, Dec 2025)
    as customer substations near randomly chosen cabin-type PTDs of the same municipality (seeded)."""
    counts = pd.read_csv(MV_CUSTOMER_FILE, dtype={"concelho_code": str})
    ptd = ptd.assign(concelho=ptd["ptd_code"].str[:4])
    rows = []
    for cc, n in zip(counts["concelho_code"], counts["delivery_points"]):
        pool = ptd[(ptd["concelho"] == cc) & ptd["construction"].str.startswith("Cabine")]
        if pool.empty:
            pool = ptd[ptd["concelho"] == cc]
        if pool.empty:
            continue
        rng = np.random.default_rng(int(cc))
        pick = pool.iloc[rng.integers(0, len(pool), int(n))]
        dist = rng.uniform(50.0, 300.0, int(n)); ang = rng.uniform(0, 2 * np.pi, int(n))
        lat = pick["lat"].values + dist * np.sin(ang) / 110540.0
        lon = pick["lon"].values + dist * np.cos(ang) / (111320.0 * np.cos(np.radians(pick["lat"].values)))
        for k in range(int(n)):
            r = pick.iloc[k]
            rows.append({"ptd_code": f"MVCUST:{cc}:{k:04d}", "lat": lat[k], "lon": lon[k], "root": r["root"], "kv": r["kv"],
                         "kva": MV_CUSTOMER_KVA, "p_mva": 0.0, "hv_bus": r["hv_bus"], "construction": "Cabine (MV customer, simulated)",
                         "osm_lat": r["osm_lat"], "osm_lon": r["osm_lon"], "osm_kv": r["osm_kv"], "osm_seg": r["osm_seg"],
                         "root_lon": r.get("root_lon"), "root_lat": r.get("root_lat"), "is_fallback": False, "is_customer": True})
    return pd.DataFrame(rows)


def build(con, with_mv_customers: bool = False, sites_csv: Path | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    ptd = con.sql("""
        select a.ptd_code, a.ptd_lat lat, a.ptd_lon lon, p.mv_root_bus root, p.mv_voltage_kv kv,
               p.capacity_kva kva, p.peak_load_proxy_mva p_mva, b.source_ref hv_bus,
               coalesce(pa.construction_type,'') construction, a.projected_lat osm_lat, a.projected_lon osm_lon, a.voltage_kv_candidate osm_kv, a.osm_segment_id_candidate osm_seg
        from candidate.ptd_mv_attachment_assessment a
        join equivalent.ptd_connections p using (ptd_code)
        join equivalent.buses b on b.bus_id = p.mv_root_bus
        left join candidate.ptd_public_attributes pa using (ptd_code)""").df()
    fb = set(con.sql("select ptd_code from candidate.ptd_mv_attachment_assessment where attachment_class like '%SYNTHETIC_FALLBACK'").df()["ptd_code"])
    ptd["is_fallback"] = ptd["ptd_code"].isin(fb)
    ptd["is_customer"] = False
    # MV customer substations are OFF by default: random placement proved assumption-driven (trial 4, archived)
    customers = (sited_mv_customers(ptd, sites_csv) if sites_csv else
                 synthetic_mv_customers(ptd) if with_mv_customers else ptd.iloc[0:0])
    ptd = pd.concat([ptd[ptd["is_fallback"]], customers], ignore_index=True)
    hv = pd.read_csv(HV_BUSES)[["bus_id", "lon", "lat"]].rename(columns={"bus_id": "hv_bus", "lon": "root_lon", "lat": "root_lat"})
    ptd = ptd.drop(columns=[c for c in ("root_lon", "root_lat") if c in ptd.columns]).merge(hv, on="hv_bus", how="left")
    missing = ptd["root_lon"].isna()
    if missing.any():  # fall back to PTD centroid of the root
        cent = ptd.groupby("root")[["lon", "lat"]].mean().rename(columns={"lon": "root_lon", "lat": "root_lat"})
        ptd.loc[missing, ["root_lon", "root_lat"]] = cent.loc[ptd.loc[missing, "root"]].values
    segs, nodes, anchors = [], [], []
    for (root, kv), g in ptd.groupby(["root", "kv"], sort=True):
        A = xy([g["root_lon"].iloc[0]], [g["root_lat"].iloc[0]])[0]
        P = xy(g["lon"], g["lat"])
        # 1) spatial clusters: PTDs linked by MST edges <= MAX_CABLE_SPAN_KM (far clusters get their own anchor)
        parent = list(range(len(P)))
        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]; i = parent[i]
            return i
        for a, b, w in mst_edges(P):
            if w / 1000.0 <= MAX_CABLE_SPAN_KM:
                parent[find(a)] = find(b)
        clusters = {}
        for i in range(len(P)):
            clusters.setdefault(find(i), []).append(i)
        # 2) inside each cluster, bearing sweep around the root with a capacity cap per feeder
        bearing = np.arctan2(P[:, 1] - A[1], P[:, 0] - A[0])
        feeders = []
        for members_c in clusters.values():
            cur, kva = [], 0.0
            for i in sorted(members_c, key=lambda i: bearing[i]):
                k = float(g["kva"].iloc[i] or 0)
                if cur and (kva + k > CAP_KVA_15KV * float(kv) / 15.0 or len(cur) >= CAP_N):
                    feeders.append(cur); cur, kva = [], 0.0
                cur.append(i); kva += k
            if cur:
                feeders.append(cur)
        feeders.sort(key=lambda f: float(np.mean(bearing[f])))
        ends, feeder_kind = [], []
        OSMP = xy(g["osm_lon"].fillna(0), g["osm_lat"].fillna(0))
        osm_ok = (g["osm_kv"].astype(float) == float(kv)).values & g["osm_lat"].notna().values
        for f_no, members in enumerate(feeders):
            # anchor: root substation or the closest same-voltage point of the public OSM MV network
            best_anchor, best_d, anchor_id = A, float(np.min(np.hypot(*(P[members] - A).T))), "ROOTANCHOR:" + root
            for m in members:
                if osm_ok[m]:
                    d = float(np.min(np.hypot(*(P[members] - OSMP[m]).T)))
                    if d < best_d:
                        best_anchor, best_d, anchor_id = OSMP[m], d, "OSMTAP:" + str(g["osm_seg"].iloc[m])
            anchors.append({"anchor_id": anchor_id, "root": root, "kv": kv,
                            "x": float(best_anchor[0]), "y": float(best_anchor[1]),
                            "root_distance_km": float(np.hypot(*(best_anchor - A))) / 1000.0})
            pts = np.vstack([best_anchor, P[members]])
            ids = [anchor_id] + [g["ptd_code"].iloc[m] for m in members]
            edges = mst_edges(pts)
            # path length from anchor to find the feeder end (farthest node)
            adj = {i: [] for i in range(len(pts))}
            for a, b, w in edges:
                adj[a].append((b, w)); adj[b].append((a, w))
            dist = {0: 0.0}; stack = [0]
            while stack:
                u = stack.pop()
                for v, w in adj[u]:
                    if v not in dist:
                        dist[v] = dist[u] + w; stack.append(v)
            end = max(dist, key=dist.get)
            cabins = np.mean([str(g["construction"].iloc[m]).startswith("Cabine") for m in members])
            spacing = float(np.median([w for _, _, w in edges])) / 1000.0 if edges else 0.0
            kind = "UNDERGROUND_CABLE" if cabins >= 0.5 and spacing <= URBAN_MEDIAN_SPACING_KM else "OVERHEAD_RURAL_SPUR"
            ends.append((ids[end], pts[end], ids, pts)); feeder_kind.append(kind)
            for a, b, w in edges:
                segs.append({"root": root, "kv": kv, "feeder": f"{root}:{int(kv)}:F{f_no:02d}", "from_node": ids[a], "to_node": ids[b],
                             "euclid_km": w / 1000.0, "normally_open": False, "kind": kind})
            for m in members:
                nodes.append({"ptd_code": g["ptd_code"].iloc[m], "is_customer": bool(g["is_customer"].iloc[m]), "root": root, "kv": kv, "feeder": f"{root}:{int(kv)}:F{f_no:02d}",
                              "p_mva": float(g["p_mva"].iloc[m] or 0.0), "kva": float(g["kva"].iloc[m] or 0.0)})
        if len(ends) == 1 and float(np.hypot(*(ends[0][1] - ends[0][3][0]))) / 1000.0 <= MAX_CABLE_SPAN_KM:
            end_id, end_pt, ids1, pts1 = ends[0]
            segs.append({"root": root, "kv": kv, "feeder": f"{root}:{int(kv)}:TIE00", "from_node": end_id, "to_node": ids1[0],
                         "euclid_km": float(np.hypot(*(end_pt - pts1[0]))) / 1000.0, "normally_open": True, "kind": segs[-1]["kind"]})
        elif len(ends) > 1:
            for f_no in range(len(ends)):
                if len(ends) == 2 and f_no == 1:
                    break  # two feeders need a single tie
                _, _, ids1, pts1 = ends[f_no]
                _, _, ids2, pts2 = ends[(f_no + 1) % len(ends)]
                D = np.hypot(pts1[1:, None, 0] - pts2[None, 1:, 0], pts1[1:, None, 1] - pts2[None, 1:, 1])
                i, j = np.unravel_index(int(D.argmin()), D.shape)
                if D[i, j] / 1000.0 > MAX_CABLE_SPAN_KM:
                    continue  # neighbouring feeders too far apart: no ring tie (radial rural spur)
                segs.append({"root": root, "kv": kv, "feeder": f"{root}:{int(kv)}:TIE{f_no:02d}", "from_node": ids1[i + 1], "to_node": ids2[j + 1],
                             "euclid_km": float(D[i, j]) / 1000.0, "normally_open": True, "kind": "UNDERGROUND_CABLE" if feeder_kind[f_no] == feeder_kind[(f_no + 1) % len(ends)] == "UNDERGROUND_CABLE" else "OVERHEAD_RURAL_SPUR"})
    return pd.DataFrame(segs), pd.DataFrame(nodes), pd.DataFrame(anchors).drop_duplicates(["anchor_id", "root", "kv"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-mv-customers", action="store_true",
                        help="EXPERIMENTAL: add randomly placed MV customer substations (trial 4; not used for release)")
    parser.add_argument("--mv-customer-sites", type=Path, help="CSV from build_mv_customer_sites.py (OSM building sites)")
    parser.add_argument("--street-detour", type=Path, help="per-municipality street/Euclid detour CSV; replaces the fixed 1.30")
    parser.add_argument("--out", type=Path, help="output directory (default: mv_urban_cable_v1)")
    args = parser.parse_args()
    global OUT
    if args.out:
        OUT = args.out
    t0 = time.time(); OUT.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(DB), read_only=True)
    osm_cable_km = float(con.sql("select coalesce(sum(length_km),0) from parameter.mv_line_parameters where power_tag='cable'").fetchone()[0])
    segs, nodes, anchors = build(con, args.with_mv_customers, args.mv_customer_sites)
    segs.loc[(segs["kind"] == "UNDERGROUND_CABLE") & (segs["euclid_km"] > MAX_CABLE_SPAN_KM), "kind"] = "OVERHEAD_RURAL_SPUR"
    cable = segs["kind"] == "UNDERGROUND_CABLE"
    raw = float(segs.loc[cable, "euclid_km"].sum())
    target = OFFICIAL_MV_UNDERGROUND_KM - osm_cable_km
    detour_unbounded = target / raw   # diagnostic only: factor that would reproduce the official underground total
    detour = DETOUR
    segs["detour"] = detour
    if args.street_detour:
        dt = pd.read_csv(args.street_detour, dtype={"concelho": str}).set_index("concelho")["detour_median"]
        national = float(dt.median())
        def cc_of(node: str) -> str:
            return node.split(":")[1] if node.startswith("MVCUST:") else (node[:4] if node[:4].isdigit() else "")
        cc = [cc_of(a) or cc_of(b) for a, b in zip(segs.from_node, segs.to_node)]
        segs["detour"] = [float(dt.get(c, national)) for c in cc]
        detour = national
    segs["length_km"] = np.maximum(segs["euclid_km"] * segs["detour"], 0.005)
    cat = con.sql("select catalog_id, designation from parameter.mv_cable_catalog").df().set_index("catalog_id")
    segs["catalog_id"] = np.where(cable, segs["kv"].map(lambda v: CABLE.get(float(v), "336903")),
                                  np.where(segs["kv"] >= 20, "136-AL1/22-ST1A", "75-AL1/13-ST1A"))
    segs["designation"] = segs["catalog_id"].map(cat["designation"]).fillna(segs["catalog_id"])
    # electrical constants identical to the existing classes of parameter.mv_line_parameters
    oh30 = segs["kv"] >= 20
    segs["r_ohm_per_km"] = np.where(cable, 0.157057, np.where(oh30, 0.259478, 0.464838))
    segs["x_ohm_per_km"] = np.where(cable, 0.08, np.where(oh30, 0.32, 0.35))
    segs["c_nf_per_km"] = np.where(cable, 240.0, 10.0)
    segs["max_i_ka"] = np.where(cable, 0.391, np.where(oh30, 0.39, 0.26))
    # design rule: long overhead links carry whole PTD clusters -> trunk-class conductor 203-AL1/32-ST1A
    trunk = (~cable) & (segs["euclid_km"] > MAX_CABLE_SPAN_KM)
    segs.loc[trunk, "catalog_id"] = "203-AL1/32-ST1A"; segs.loc[trunk, "designation"] = "203-AL1/32-ST1A"
    segs.loc[trunk, "r_ohm_per_km"] = 0.174085; segs.loc[trunk, "x_ohm_per_km"] = 0.30; segs.loc[trunk, "max_i_ka"] = 0.51
    segs["evidence_status"] = np.where(cable, "SIMULATED_URBAN_OPEN_RING_CABLE", np.where(trunk, "SIMULATED_OVERHEAD_TRUNK_LINK", "SIMULATED_RURAL_OVERHEAD_SPUR"))
    segs.insert(0, "segment_id", [f"MVURB:{i:07d}" for i in range(len(segs))])
    mem = duckdb.connect()
    mem.register("s", segs); mem.register("n", nodes); mem.register("a", anchors)
    mem.execute(f"copy a to '{OUT / 'mv_urban_cable_anchor.parquet'}' (format parquet)")
    mem.execute(f"copy s to '{OUT / 'mv_urban_cable_segment.parquet'}' (format parquet)")
    mem.execute(f"copy n to '{OUT / 'mv_urban_cable_ptd.parquet'}' (format parquet)")
    segs.to_csv(OUT / "mv_urban_cable_segments.csv", index=False)
    summary = {
        "layer": "MV-URBAN-CABLE-V1", "evidence": "SIMULATED", "ptds": int((~nodes["is_customer"]).sum()),
        "mv_customer_substations": int(nodes["is_customer"].sum()), "mv_customer_source": "E-REDES 20-caracterizacao-pes-contrato-ativo, 2025-12, MT/AT/MAT delivery points by municipality (27,585)", "roots": int(nodes["root"].nunique()),
        "feeders": int(nodes["feeder"].nunique()), "segments_closed": int((~segs.normally_open).sum()), "ties_normally_open": int(segs.normally_open.sum()),
        "euclidean_km": round(raw, 1), "detour_factor_that_would_match_official_underground": round(detour_unbounded, 3), "detour_factor_applied": round(detour, 3), "detour_source": "per-municipality street routing (median)" if args.street_detour else "fixed",
        "feeders_underground": int(segs.loc[cable & ~segs.normally_open, "feeder"].nunique()),
        "synthetic_cable_km": round(float(segs.loc[cable, "length_km"].sum()), 1), "closed_cable_km": round(float(segs.loc[cable & ~segs.normally_open, "length_km"].sum()), 1),
        "synthetic_overhead_spur_km": round(float(segs.loc[~cable, "length_km"].sum()), 1),
        "osm_candidate_cable_km": round(osm_cable_km, 1),
        "model_total_underground_km": round(float(segs.loc[cable, "length_km"].sum()) + osm_cable_km, 1),
        "official_underground_km": OFFICIAL_MV_UNDERGROUND_KM, "official_source": "E-REDES Relatório da Qualidade de Serviço 2024, Tabela 2.1",
        "anchors_osm_tap": int(anchors.anchor_id.str.startswith("OSMTAP:").sum()), "anchors_root": int(anchors.anchor_id.str.startswith("ROOTANCHOR:").sum()),
        "osm_tap_to_root_equivalent_trunk_km": round(float(anchors.loc[anchors.anchor_id.str.startswith("OSMTAP:"), "root_distance_km"].sum()), 1),
        "replaced_star_feeder_km": round(float(con.sql("select sum(p.hv_anchor_distance_km) from equivalent.ptd_connections p join candidate.ptd_mv_attachment_assessment a using(ptd_code) where a.attachment_class like '%SYNTHETIC_FALLBACK'").fetchone()[0]), 1),
        "caps": {"installed_kva_per_feeder_at_15kv": CAP_KVA_15KV, "ptds_per_feeder": CAP_N, "urban_median_spacing_km": URBAN_MEDIAN_SPACING_KM, "max_cable_span_km": MAX_CABLE_SPAN_KM}, "seconds": round(time.time() - t0, 1),
    }
    (OUT / "mv_urban_cable.summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
