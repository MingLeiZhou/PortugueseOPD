#!/usr/bin/env python3
"""Replace the static grid.* / geo.* tables in the r2 monthly HV databases with CORE-3787-REN.

The monthly runner copied grid/geo/provenance from the source time-series DB, which still describes
PT60-v2.0.0 (4,943 lines, 228 transformers). The solved state arrays follow CORE-3787-REN
(4,926 lines, 219 transformers). This script:
  1. builds the CORE-3787-REN static tables once (same column schema as the old tables),
  2. per month: moves the old tables to schema legacy_pt60_v2_0_0 and writes the new ones,
  3. checks grid.buses == monthly_model.bus_order and grid.lines == monthly_model.line_order.
State arrays and cases are not touched. Run on the uncompressed .duckdb files, before zstd.

Usage (repo root):
  python3 src/replace_hv_month_grid_tables.py build
  python3 src/replace_hv_month_grid_tables.py apply 2025-05 ... 2026-03   (or: apply all)
  python3 src/replace_hv_month_grid_tables.py check all
"""
import hashlib, json, os, sys
from datetime import datetime, timezone
from pathlib import Path
import duckdb, pandas as pd

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "work/hv_ren_fix_2026-09-29/outputs"
MODEL = WORK / "model/portuguese_hv_candidate.json"
TABLES = WORK / "tables"
OUT = Path(os.environ.get("HV_OUT", "data/external/PT60_public_data_2025-05-01_2026-03-24/simpt_power_hv_core_ren_2026-09-30-r2"))
BUILT = Path(os.environ.get("CORE_STATIC_DB", ROOT / "output/simpt_power_release/core3787_static_tables_r2.duckdb"))
MID = "CORE-3787-REN"
LEGACY = "legacy_pt60_v2_0_0"
MONTHS = ["2025-05", "2025-06", "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]
REPLACED = ["grid.buses", "grid.lines", "grid.transformers", "grid.generators", "grid.load_points", "grid.facilities",
            "grid.branches", "grid.bus_classification", "grid.generator_control_assumptions", "grid.interconnectors",
            "grid.model_summary", "grid.network_models", "geo.line_geometries", "geo.facility_geometries",
            "provenance.entity_evidence", "provenance.raw_record_locator", "provenance.traceability_summary"]


def db(m): return OUT / m / f"pt60_models_{m}.compact.duckdb"


def conform(new: pd.DataFrame, legacy: pd.DataFrame, key: str) -> pd.DataFrame:
    """Rows = new ids; columns = legacy schema. Values from new where present, else from legacy row."""
    cols = list(legacy.columns)
    base = legacy.drop(columns=[c for c in cols if c in new.columns and c != key])
    out = new[[c for c in new.columns if c in cols]].merge(base, on=key, how="left")
    out["model_id"] = MID
    for c in cols:
        if c not in out.columns: out[c] = None
    out = out[cols]
    for c in cols:  # keep legacy dtypes where possible
        try: out[c] = out[c].astype(legacy[c].dtype)
        except Exception: pass
    return out


def build():
    import pandapower as pp
    src = duckdb.connect(str(db("2025-12")), read_only=True)
    L = {t: src.execute(f"select * from {t}").df() for t in REPLACED}
    src.close()
    net = pp.from_json(str(MODEL)); bn = net.bus.name
    T = {}
    b = pd.read_csv(TABLES / "buses.csv", low_memory=False); T["grid.buses"] = conform(b, L["grid.buses"], "bus_id")
    ln = pd.read_csv(TABLES / "lines.csv", low_memory=False)
    par = net.line[["line_id", "length_km", "r_ohm_per_km", "x_ohm_per_km", "c_nf_per_km", "max_i_ka", "parallel", "df", "in_service"]].copy()
    par["from_bus"] = net.line.from_bus.map(bn).values; par["to_bus"] = net.line.to_bus.map(bn).values
    lines = ln.drop(columns=[c for c in par.columns if c != "line_id" and c in ln.columns]).merge(par, on="line_id", how="inner")
    T["grid.lines"] = conform(lines.drop(columns=["geometry_json"]), L["grid.lines"], "line_id")
    tt = pd.read_csv(TABLES / "transformers_topology.csv", low_memory=False)
    tr = net.trafo.copy()
    tp = pd.DataFrame({"transformer_id": tr.name.values, "hv_bus": tr.hv_bus.map(bn).values, "lv_bus": tr.lv_bus.map(bn).values,
                       "hv_kv": tr.vn_hv_kv.values, "lv_kv": tr.vn_lv_kv.values, "sn_mva": tr.sn_mva.values,
                       "vk_percent": tr.vk_percent.values, "vkr_percent": tr.vkr_percent.values, "pfe_kw": tr.pfe_kw.values,
                       "i0_percent": tr.i0_percent.values, "tap_pos": tr.tap_pos.values, "parallel": tr.parallel.values})
    trs = tt.drop(columns=[c for c in tp.columns if c != "transformer_id" and c in tt.columns]).merge(tp, on="transformer_id", how="right")
    T["grid.transformers"] = conform(trs, L["grid.transformers"], "transformer_id")
    g = pd.read_csv(TABLES / "generators.csv", low_memory=False); T["grid.generators"] = conform(g, L["grid.generators"], "generator_id")
    ld = pd.read_csv(TABLES / "loads.csv", low_memory=False); T["grid.load_points"] = conform(ld, L["grid.load_points"], "load_id")
    # facility ids are not unique in either table (same code, several records); the facility set is unchanged, so keep rows 1:1
    fa = L["grid.facilities"].copy(); fa["model_id"] = MID; T["grid.facilities"] = fa
    br = pd.concat([pd.DataFrame({"branch_id": T["grid.lines"].line_id, "branch_type": "line", "from_bus": T["grid.lines"].from_bus,
                                  "to_bus": T["grid.lines"].to_bus, "in_service": T["grid.lines"].in_service}),
                    pd.DataFrame({"branch_id": T["grid.transformers"].transformer_id, "branch_type": "transformer",
                                  "from_bus": T["grid.transformers"].hv_bus, "to_bus": T["grid.transformers"].lv_bus,
                                  "in_service": net.trafo.in_service.values})])
    kv = T["grid.buses"].set_index("bus_id").voltage_kv
    br["from_voltage_kv"] = br.from_bus.map(kv); br["to_voltage_kv"] = br.to_bus.map(kv)
    T["grid.branches"] = conform(br, L["grid.branches"], "branch_id")
    bc = L["grid.bus_classification"].copy(); bc = bc[bc.bus_id.isin(T["grid.buses"].bus_id)]
    bc["has_load"] = bc.bus_id.isin(set(T["grid.load_points"].bus_id)); bc["has_generation"] = bc.bus_id.isin(set(T["grid.generators"].bus_id))
    bc["model_id"] = MID; T["grid.bus_classification"] = bc
    pv = g[g.voltage_control_mode.astype(str).str.startswith("PV")].groupby("bus_id").nameplate_mw.sum()
    gq = net.gen.assign(bus_id=net.gen.bus.map(bn)).groupby("bus_id")[["min_q_mvar", "max_q_mvar"]].sum()
    lg = L["grid.generator_control_assumptions"]
    gc = pd.DataFrame({"bus_id": gq.index, "aggregate_nameplate_mw": gq.index.map(pv).values,
                       "voltage_control_mode": "PV_VOLTAGE_CONTROL_SCENARIO", "assumed_min_q_mvar": gq.min_q_mvar.values,
                       "assumed_max_q_mvar": gq.max_q_mvar.values, "q_limit_status": lg.q_limit_status.iloc[0], "note": lg.note.iloc[0]})
    gc["model_id"] = MID; T["grid.generator_control_assumptions"] = gc[list(lg.columns)]
    ic = L["grid.interconnectors"].copy(); assert ic.bus_id.isin(T["grid.buses"].bus_id).all(); ic["model_id"] = MID; T["grid.interconnectors"] = ic
    geo = pd.DataFrame({"line_id": ln.line_id, "source_line_id": ln.source_line_id, "source": ln.source, "crs": "EPSG:4326", "geometry_json": ln.geometry_json})
    geo = geo[geo.line_id.isin(T["grid.lines"].line_id)]; T["geo.line_geometries"] = conform(geo, L["geo.line_geometries"], "line_id")
    fg = L["geo.facility_geometries"].copy(); fg = fg[fg.facility_id.isin(T["grid.facilities"].facility_id)]; fg["model_id"] = MID
    T["geo.facility_geometries"] = fg
    ids = {"bus": set(T["grid.buses"].bus_id), "line": set(T["grid.lines"].line_id), "transformer": set(T["grid.transformers"].transformer_id),
           "generator": set(T["grid.generators"].generator_id), "facility": set(T["grid.facilities"].facility_id)}
    ee = L["provenance.entity_evidence"]; ee = ee[[e in ids.get(t, set()) for t, e in zip(ee.entity_type, ee.entity_id)]].copy(); ee["model_id"] = MID
    T["provenance.entity_evidence"] = ee
    rl = L["provenance.raw_record_locator"]; rl = rl[[e in ids.get(t, set()) for t, e in zip(rl.entity_type, rl.entity_id)]].copy()
    T["provenance.raw_record_locator"] = rl
    ts = []
    for t in ["line", "transformer", "facility", "generator"]:
        tot = len(ids[t]); tr_ = rl[rl.entity_type == t].entity_id.nunique()
        ts.append({"entity_type": t, "total_entities": tot, "traced_entities": tr_, "traced_percent": round(100 * tr_ / tot, 2)})
    T["provenance.traceability_summary"] = pd.DataFrame(ts)[list(L["provenance.traceability_summary"].columns)]
    sha = hashlib.sha256(MODEL.read_bytes()).hexdigest()
    nm = L["grid.network_models"].iloc[:1].copy()
    nm = nm.assign(model_id=MID, dataset="SimPT-Power", version="2026.09.30-r2",
                   title="CORE-3787-REN: public-record 60-400 kV core reconciled with REN RNT characterisation (31-12-2025)",
                   generated_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                   release_path=f"work/hv_ren_fix_2026-09-29/outputs/model/portuguese_hv_candidate.json#sha256={sha}")
    T["grid.network_models"] = nm
    ms = pd.DataFrame([{"model_id": MID, "entity_type": k.split(".")[1], "row_count": len(T[k])} for k in
                       ["grid.facilities", "grid.buses", "grid.lines", "grid.transformers", "grid.generators", "grid.load_points", "grid.interconnectors"]])
    ms["entity_type"] = ms.entity_type.replace({"load_points": "load_points"}); T["grid.model_summary"] = ms[list(L["grid.model_summary"].columns)]
    assert len(T["grid.lines"]) == len(net.line) and len(T["grid.transformers"]) == len(net.trafo) and len(T["grid.buses"]) == len(net.bus)
    BUILT.unlink(missing_ok=True); o = duckdb.connect(str(BUILT))
    for k, df in T.items():
        s, t = k.split("."); o.execute(f"create schema if not exists {s}"); o.register("df", df); o.execute(f"create or replace table {s}.{t} as select * from df"); o.unregister("df")
    o.close()
    print(json.dumps({k: len(v) for k, v in T.items()}, indent=1)); print("model sha256", sha, "->", BUILT)


def apply(m):
    p = db(m); assert p.exists(), p
    c = duckdb.connect(str(p))
    done = c.execute(f"select count(*) from information_schema.schemata where schema_name='{LEGACY}'").fetchone()[0]
    c.execute(f"ATTACH '{BUILT}' AS built (READ_ONLY)")
    c.execute("BEGIN")
    if not done: c.execute(f"CREATE SCHEMA {LEGACY}")
    for k in REPLACED:
        s, t = k.split(".")
        if not done: c.execute(f"CREATE TABLE {LEGACY}.{s}__{t} AS SELECT * FROM {s}.{t}")
        c.execute(f"DROP TABLE {s}.{t}"); c.execute(f"CREATE TABLE {s}.{t} AS SELECT * FROM built.{s}.{t}")
    c.execute("COMMIT"); c.execute("DETACH built"); c.execute("CHECKPOINT"); c.close()
    check(m)


def check(m):
    c = duckdb.connect(str(db(m)), read_only=True)
    q = lambda s: c.execute(s).fetchone()[0]
    r = {"month": m,
         "model_id": q("select string_agg(distinct model_id, ',') from grid.lines"),
         "bus_mismatch": q("select count(*) from (select bus_id from grid.buses except select bus_id from monthly_model.bus_order union all select bus_id from monthly_model.bus_order except select bus_id from grid.buses)"),
         "line_mismatch": q("select count(*) from (select line_id from grid.lines except select line_id from monthly_model.line_order union all select line_id from monthly_model.line_order except select line_id from grid.lines)"),
         "lines": q("select count(*) from grid.lines"), "transformers": q("select count(*) from grid.transformers"),
         "cases": q("select count(*) from monthly_model.cases"), "manifest_model_sha": q("select source_model_sha256 from monthly_model.run_manifest")[:12]}
    c.close(); print(r)
    assert r["bus_mismatch"] == 0 and r["line_mismatch"] == 0 and r["model_id"] == MID, r


if __name__ == "__main__":
    cmd, *ms = sys.argv[1:]
    ms = MONTHS if ms in (["all"], []) else ms
    if cmd == "build": build()
    elif cmd == "apply": [apply(m) for m in ms]
    elif cmd == "check": [check(m) for m in ms]
