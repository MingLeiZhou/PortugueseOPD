#!/usr/bin/env python3
"""Build the uncompressed, notebook-friendly companion bundle of a frozen SimPT-Power release
(for Zenodo alongside the release, and for import into Kaggle). The frozen release is not modified.

Inputs : data/releases/<RID>/ (model JSON, release.json), the release database
         output/simpt_power_release/simpt_power.duckdb (sha256 checked against release.json),
         the uncompressed monthly HV DBs in HV_OUT (identical to hv_core/*.zst).
Output : output/simpt_power_release/open_bundle_<tag>/  (Parquet + JSON + README + requirements)
Usage  : python3 src/build_open_bundle.py
"""
import hashlib, json, os, shutil
from datetime import datetime, timezone
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[1]
RID = os.environ.get("SIMPT_RID", "SimPT-Power-2026.09.30-r2")
REL = ROOT / "data/releases" / RID
RDB = ROOT / "output/simpt_power_release/simpt_power.duckdb"
HV = Path(os.environ.get("HV_OUT", "data/external/PT60_public_data_2025-05-01_2026-03-24/simpt_power_hv_core_ren_2026-09-30-r2"))
PEAK = "PT60_MONTH_20260115_1215_+0000"
OUT = Path(os.environ.get("BUNDLE_OUT", ROOT / f"output/simpt_power_release/open_bundle_{RID.split('-')[-1]}"))
MONTHS = ["2025-05", "2025-06", "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]


def sha(p, n=1 << 22):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(n), b""): h.update(b)
    return h.hexdigest()


PEAK_JSON = ROOT / "output/simpt_power_release/hotspot_audit/solved_r2peak_pp352_PT60_MONTH_20260115_1215_+0000.json"


def add_peak(files):
    """Solved pandapower net of the national peak snapshot (rebuilt with src/diag_hv_case.py from the same
    inputs as the monthly run, pandapower 3.5.2; its line loadings equal the stored peak results)."""
    shutil.copy2(PEAK_JSON, OUT / "hv/CORE-3787-REN_peak_20260115_1215_solved.json")
    files["hv/CORE-3787-REN_peak_20260115_1215_solved.json"] = {"rows": None, "description": "pandapower net of the national peak snapshot "
        "(15 Jan 2026 12:15 UTC) with its loads, dispatch and solved results; rebuilt by src/diag_hv_case.py, max line loading 132.19% = stored result"}


def write_sums():
    with open(OUT / "SHA256SUMS", "w") as f:
        for p in sorted(OUT.rglob("*")):
            if p.is_file() and p.name != "SHA256SUMS": f.write(f"{sha(p)}  {p.relative_to(OUT)}\n")


def main():
    rel = json.loads((REL / "release.json").read_text())
    if OUT.exists() and any(OUT.iterdir()): raise SystemExit(f"output exists, remove or set BUNDLE_OUT: {OUT}")
    # a public release derived from another (r3 <- r2) has identical data; the working DB is the parent's
    parent = json.loads((REL.parent / rel["derived_from"] / "release.json").read_text()) if rel.get("derived_from") else rel
    assert sha(RDB) == parent["database_uncompressed_sha256"], "working DB does not match the release (or its parent)"
    for d in ["hv", "mv", "lv", "observations"]: (OUT / d).mkdir(parents=True, exist_ok=True)
    shutil.copy2(REL / "hv_core/CORE-3787-REN_model.json", OUT / "hv/CORE-3787-REN_model.json")
    files = {}
    add_peak(files)

    def dump(con, sql, rel_path, desc):
        p = OUT / rel_path
        con.execute(f"COPY ({sql}) TO '{p}' (FORMAT parquet, COMPRESSION zstd)")
        n = con.execute(f"select count(*) from read_parquet('{p}')").fetchone()[0]
        files[rel_path] = {"rows": n, "description": desc}; print(f"{rel_path:48s} {n:>10,}", flush=True)

    # ---- HV core (static tables + results) from the monthly DBs
    jan = duckdb.connect(str(HV / "2026-01/pt60_models_2026-01.compact.duckdb"), read_only=True)
    assert jan.execute("select min(model_id) from grid.lines").fetchone()[0] == "CORE-3787-REN"
    dump(jan, "select * from grid.buses", "hv/buses.parquet", "CORE-3787-REN buses (60-400 kV)")
    dump(jan, "select l.*, g.geometry_json from grid.lines l left join geo.line_geometries g using(line_id)", "hv/lines.parquet", "CORE-3787-REN lines with parameters and WGS84 geometry (JSON)")
    dump(jan, "select * from grid.transformers", "hv/transformers.parquet", "CORE-3787-REN transformers")
    dump(jan, "select * from grid.generators", "hv/generators.parquet", "Generation units mapped to HV buses")
    dump(jan, "select * from grid.load_points", "hv/load_points.parquet", "Load points (E-REDES substations and PDIRT points)")
    dump(jan, f"select case_id, timestamp_utc, bus_id, vm_pu from monthly_model.bus_states_expanded where case_id='{PEAK}'", "hv/peak_20260115_1215_bus_voltage.parquet", "Bus voltages at the national peak-load snapshot")
    dump(jan, f"select case_id, timestamp_utc, line_id, loading_percent from monthly_model.line_states_expanded where case_id='{PEAK}'", "hv/peak_20260115_1215_line_loading.parquet", "Line loadings at the national peak-load snapshot")
    dump(jan, "select case_id, timestamp_utc, line_id, cast(loading_percent as float) loading_percent from monthly_model.line_states_expanded", "hv/line_loading_2026-01.parquet", "Line loading of all 2,976 snapshots in January 2026 (one month as an example; all months in hv_core/*.zst of the release)")
    jan.close()
    con = duckdb.connect()
    cols = "case_id, timestamp_utc, timestamp_local, converged, observed_load_mw, observed_generation_total_mw, observed_net_import_mw, model_net_import_mw, vm_pu_min, vm_pu_max, maximum_line_loading_percent, lines_over_100_percent, maximum_transformer_loading_percent, model_losses_percent_of_load"
    for m in MONTHS: con.execute(f"ATTACH '{HV / m / f'pt60_models_{m}.compact.duckdb'}' AS m{m.replace('-', '')} (READ_ONLY)")
    union = " union all ".join(f"select {cols} from m{m.replace('-', '')}.monthly_model.cases" for m in MONTHS)
    dump(con, f"select * from ({union}) order by timestamp_utc", "hv/snapshots_summary.parquet", "One row per 15-min snapshot (31,492): observed load, model results and limit indicators")
    con.close()
    # ---- MV / LV / observations from the release database
    r = duckdb.connect(str(RDB), read_only=True)
    T = [("parameter.mv_root_transformer_parameters", "mv/root_transformers.parquet", "60 kV/MV root transformers of the 617 root slices"),
         ("equivalent.ptd_connections", "mv/ptd_connections.parquet", "PTD (secondary substation) to MV root / HV bus connections"),
         ("extension.mv_urban_cable_segment", "mv/urban_cable_segments.parquet", "MV-URBAN-CABLE-V1 inferred underground cable segments"),
         ("extension.mv_customer_sites", "mv/customer_sites.parquet", "MV customer delivery points placed by parish (estimate)"),
         ("candidate.ptd_public_attributes", "lv/ptd_public_attributes.parquet", "E-REDES public attributes of the 72,434 PTDs"),
         ("parameter.ptd_transformer_parameters", "lv/ptd_transformers.parquet", "PTD transformer parameters"),
         ("extension.lv_geo_ptd_results", "lv/lv_geo_ptd_results.parquet", "LV-GEO-A per-PTD lengths and power-flow results"),
         ("extension.lv_geo_segments", "lv/lv_geo_segments.parquet", "LV-GEO-A geographic LV segments (5.7 M)")]
    for t, p, d in T: dump(r, f"select * from {t}", p, d)
    for (t,) in r.execute("select table_name from extension.release_layer_manifest where table_name like 'observation.%' order by 1").fetchall():
        dump(r, f"select * from {t}", f"observations/{t.split('.')[1]}.parquet", f"Public observation table {t}")
    r.close()
    for n in ["requirements.txt", "requirements-full-lock.txt"]:  # the release's requirements-lock.txt only pins python/duckdb
        if (ROOT / "kaggle" / n).exists(): shutil.copy2(ROOT / "kaggle" / n, OUT / n)
    man = {"bundle_of": RID, "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "release_database_sha256": rel["database_uncompressed_sha256"], "hv_model": rel["hv_core_model"],
           "note": "Uncompressed subset for notebooks. The frozen release (DuckDB + all 11 monthly HV databases) is the reference; cite its DOI.",
           "files": files}
    (OUT / "bundle_manifest.json").write_text(json.dumps(man, indent=2, default=str))
    write_sums(); print("bundle ->", OUT)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--add-peak"]:  # patch an existing bundle built before the peak JSON was added
        man = json.loads((OUT / "bundle_manifest.json").read_text()); add_peak(man["files"])
        (OUT / "bundle_manifest.json").write_text(json.dumps(man, indent=2, default=str)); write_sums(); print("peak added ->", OUT)
    else:
        main()
