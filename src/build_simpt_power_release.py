#!/usr/bin/env python3
"""Build, validate and freeze SimPT-Power-2026.09.30-r1 (local only; no upload).

Stages (run in order, each idempotent except freeze):
  lv        re-run the national LV geographic model (rule A) with segment export -> output/simpt_power_release/lv_geo_A
  db        copy the working all-voltage DB (never modified) to output/simpt_power_release/simpt_power.duckdb and add
            the extension/observation tables of this release
  validate  checks -> output/simpt_power_release/release_validation.json
  freeze    requires validate PASS and the HV re-solve summary; writes data/releases/<RID>/ (refuses to overwrite)
Usage: python src/build_simpt_power_release.py lv|db|validate|freeze [--workers 6] [--hv-dir DIR]
"""
from __future__ import annotations
import argparse, csv, glob, hashlib, json, os, platform, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RID = os.environ.get("SIMPT_RID", "SimPT-Power-2026.09.30-r2")
WORK = ROOT / "output/simpt_power_release"
STAGING = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
RDB = WORK / "simpt_power.duckdb"
LVA = WORK / "lv_geo_A"
LVB = ROOT / "output/all_voltage/lv_geo_national_poleext"
MVC = ROOT / "output/all_voltage/mv_customer_national_osmtap"
MVU = ROOT / "output/all_voltage/mv_urban_cable_v1"
HVW = ROOT / "work/hv_ren_fix_2026-09-29"
DIAG = ROOT / "output/all_voltage/lv_geo_national/diagnosis"
HV_DEFAULT = Path(os.environ.get("HV_OUT", "data/external/PT60_public_data_2025-05-01_2026-03-24/simpt_power_hv_core_ren_2026-09-30-r2"))
MONTHS = ["2025-05", "2025-06", "2025-07", "2025-08", "2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02", "2026-03"]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""): h.update(b)
    return h.hexdigest()


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


# ---------------------------------------------------------------- lv
def stage_lv(workers: int) -> None:
    env = dict(os.environ, LV_WRITE_SEGMENTS="1", LV_NATIONAL_BASE=str(LVA))
    env.pop("LV_STREET_FROM_POLES", None)
    subprocess.run([sys.executable, str(ROOT / "src/run_lv_geographic_national.py"), "--workers", str(workers)], env=env, check=True)


# ---------------------------------------------------------------- db
# (table, source, loader, evidence_class, description)
def layer_specs():
    ren = ROOT / "portuguese_hv_network/data/raw/ren"
    return [
        ("extension.hv_core_ren_buses", HVW / "outputs/tables/buses.csv", "csv", "REAL_PUBLIC+INFERRED", "CORE-3787-REN buses (REN Annex B/D reconciled HV core)"),
        ("extension.hv_core_ren_lines", HVW / "outputs/tables/lines.csv", "csv", "REAL_PUBLIC+INFERRED", "CORE-3787-REN lines incl. per-voltage circuit split"),
        ("extension.hv_core_ren_transformers", HVW / "outputs/tables/transformers_topology.csv", "csv", "REAL_PUBLIC+INFERRED", "CORE-3787-REN transformers; REN Annex D units flagged REN_ANNEX_D_UNIT"),
        ("extension.hv_core_ren_line_corrections", HVW / "outputs/tables/ren_line_corrections_applied.csv", "csv", "REAL_PUBLIC", "Line exclusions/corrections applied from REN Annex B"),
        ("extension.hv_core_ren_transformer_reconciliation", HVW / "outputs/tables/ren_annex_d_transformer_reconciliation.csv", "csv", "REAL_PUBLIC", "Transformer actions against REN Annex D"),
        ("extension.mv_urban_cable_segment", MVU / "mv_urban_cable_segment.parquet", "parquet", "INFERRED", "MV-URBAN-CABLE-V1 PTD-connection cable/overhead segments"),
        ("extension.mv_urban_cable_ptd", MVU / "mv_urban_cable_ptd.parquet", "parquet", "INFERRED", "MV-URBAN-CABLE-V1 PTD feeder assignment"),
        ("extension.mv_urban_cable_anchor", MVU / "mv_urban_cable_anchor.parquet", "parquet", "INFERRED", "MV-URBAN-CABLE-V1 cluster anchors (root busbar or OSM tap)"),
        ("extension.mv_customer_sites", MVC / "municipality/customers_*.csv", "csvglob", "INFERRED", "Estimated MV customer sites: parish counts (E-REDES) placed on OSM buildings; street connection length; not in power flow"),
        ("extension.lv_geo_ptd_results", LVA / "lv_geo_powerflow_national.csv", "csv", "INFERRED+SIMULATED", "LV-GEO-A per-PTD geographic network lengths and ABCN power-flow results"),
        ("extension.lv_geo_segments", LVA / "segments/lv_geo_segments_*.parquet", "parquet", "INFERRED", "LV-GEO-A segments (pole MST, street routes, service drops) with standard cable and refined parallels"),
        ("extension.lv_geo_ptd_results_pole_extension", LVB / "lv_geo_powerflow_national.csv", "csv", "INFERRED+SIMULATED", "LV-GEO-B sensitivity: street routes extend from the nearest own pole"),
        ("observation.ren_transformers_2025_12_31", ren / "ren_transformers_2025-12-31.csv", "csv;", "REAL_PUBLIC", "REN Caracterização da RNT 31-12-2025 Annex D"),
        ("observation.ren_lines_150kv_2025_12_31", ren / "ren_150kv_lines_2025-12-31.csv", "csv;", "REAL_PUBLIC", "REN Annex B 150 kV circuits"),
        ("observation.ren_lines_400_220kv_2025_12_31", ren / "ren_400_220kv_lines_2025-12-31.csv", "csv;", "REAL_PUBLIC", "REN Annex B 400/220 kV circuits"),
        ("observation.erse_bt_nuts3_2016", ROOT / "data/raw/erse_bt_characterization/erse_bt_by_nuts3_2016.csv", "csv", "REAL_PUBLIC", "ERSE LV network km by NUTS III, 31-12-2016"),
        ("observation.erse_bt_municipality_region_2016", ROOT / "data/raw/erse_bt_characterization/erse_bt_municipality_region_2016.csv", "csv", "REAL_PUBLIC", "Municipality to ERSE region"),
        ("observation.eredes_mat_at_mt_cpe_by_parish_2026_08", ROOT / "data/raw/eredes_mv_customers/mat_at_mt_cpes_by_parish_2026-08.csv", "csv", "REAL_PUBLIC", "E-REDES MAT/AT/MT delivery points by parish"),
        ("observation.ine_census2021_buildings", ROOT / "data/raw/ine_census2021/buildings_by_municipality.csv", "csv", "REAL_PUBLIC", "INE Censos 2021 buildings by municipality"),
        ("extension.validation_lv_lengths_vs_erse_nuts3", DIAG / "lv_lengths_vs_erse_nuts3_2016.csv", "csv", "VALIDATION", "LV-GEO-A lengths vs ERSE by region"),
        ("extension.validation_external_differences", ROOT / "paper/revision_all_voltage/figures/source/fig10_external_differences.csv", "csv", "VALIDATION", "Fig. 10a relative differences"),
    ]


def stage_db() -> None:
    import duckdb
    WORK.mkdir(parents=True, exist_ok=True)
    if not RDB.exists():
        print("copying working database (read-only source) ...", flush=True)
        tmp = RDB.with_suffix(".copying"); shutil.copy2(STAGING, tmp); tmp.rename(RDB)
    con = duckdb.connect(str(RDB))
    con.execute("CREATE SCHEMA IF NOT EXISTS extension"); con.execute("CREATE SCHEMA IF NOT EXISTS observation")
    # MV-URBAN-CABLE-V2 was a rejected trial (not adopted); keep it out of the release
    for (t,) in con.sql("select table_name from duckdb_tables() where schema_name='study' and table_name like '%mv_urban_cable_v2%'").fetchall():
        con.execute(f"DROP TABLE study.{t}")
    rows = []
    for table, src, kind, ev, desc in layer_specs():
        files = sorted(glob.glob(str(src))) if any(c in str(src) for c in "*?") else ([str(src)] if Path(src).exists() else [])
        if not files: raise SystemExit(f"missing source for {table}: {src}")
        lst = "[" + ",".join(q(f) for f in files) + "]"
        if kind.startswith("csv"):
            sep = ";" if kind == "csv;" else ","
            sel = f"select * from read_csv({lst}, delim='{sep}', header=true, all_varchar=false, union_by_name=true, sample_size=-1)"
        else:
            sel = f"select * from read_parquet({lst}, union_by_name=true)"
        con.execute(f"CREATE OR REPLACE TABLE {table} AS {sel}")
        n = con.execute(f"select count(*) from {table}").fetchone()[0]
        rows.append((table, n, ev, desc, ";".join(os.path.relpath(f, ROOT) for f in files[:3]) + (f";(+{len(files) - 3} files)" if len(files) > 3 else ""),
                     ";".join(sha(Path(f)) for f in files) if len(files) <= 3 else "multiple, see file_manifest"))
        print(f"{table}: {n:,}", flush=True)
    # MV customer connection totals per municipality (three topologies)
    summ = [json.loads(Path(f).read_text()) for f in sorted(glob.glob(str(MVC / "municipality/summary_*.json")))]
    con.execute("CREATE OR REPLACE TABLE extension.mv_customer_connection_municipality (concelho VARCHAR, official_customers INT, placed INT, not_placed INT, underground_customers INT, overhead_customers INT, topology VARCHAR, underground_km DOUBLE, overhead_km DOUBLE)")
    for s in summ:
        for t in ("shared", "radial", "loop_in"):
            con.execute("insert into extension.mv_customer_connection_municipality values (?,?,?,?,?,?,?,?,?)",
                        [s["concelho"], s.get("official_customers"), s.get("placed"), s.get("not_placed_no_building"), s.get("underground_customers"), s.get("overhead_customers"),
                         t, s.get("underground_km", {}).get(t), s.get("overhead_km", {}).get(t)])
    rows.append(("extension.mv_customer_connection_municipality", len(summ) * 3, "INFERRED", "MV customer connection km per municipality and topology assumption", "output/all_voltage/mv_customer_national_osmtap/municipality/summary_*.json", "multiple"))
    con.execute("CREATE OR REPLACE TABLE extension.release_layer_manifest (table_name VARCHAR, row_count BIGINT, evidence_class VARCHAR, description VARCHAR, source_paths VARCHAR, source_sha256 VARCHAR)")
    con.executemany("insert into extension.release_layer_manifest values (?,?,?,?,?,?)", rows)
    con.execute("CREATE OR REPLACE TABLE extension.release_info AS SELECT ? release_id, ? built_at_utc, ? note",
                [RID, datetime.now(timezone.utc).isoformat(), "Extension layers are not registered in model.object_registry; they reference PTDs by ptd_code and parishes by fre_code."])
    con.execute("CHECKPOINT"); con.close()
    print("db ok:", RDB)


# ---------------------------------------------------------------- validate
def stage_validate(hv_dir: Path) -> dict:
    import duckdb
    con = duckdb.connect(str(RDB), read_only=True)
    one = lambda s: con.execute(s).fetchone()[0]
    checks = []
    def chk(name, ok, value, expect):
        checks.append({"check": name, "status": "PASS" if ok else "FAIL", "value": value, "expected": expect})
    lv_seg = con.execute("""select segment_kind, cable_class, sum(length_km) from extension.lv_geo_segments group by all""").fetchall()
    seg = {(a, b): c for a, b, c in lv_seg}
    ptd = con.execute("select sum(overhead_km), sum(street_km), sum(drops_km), count(*), count(distinct substr(ptd_code,1,4)) from extension.lv_geo_ptd_results").fetchone()
    chk("lv_pole_km_segments_equal_ptd_sum", abs(seg.get(("pole", "overhead"), 0) - ptd[0]) < 1, round(seg.get(("pole", "overhead"), 0), 1), round(ptd[0], 1))
    chk("lv_street_km_segments_equal_ptd_sum", abs(seg.get(("street", "overhead"), 0) + seg.get(("street", "underground"), 0) - ptd[1]) < 1,
        round(seg.get(("street", "overhead"), 0) + seg.get(("street", "underground"), 0), 1), round(ptd[1], 1))
    chk("lv_ptds_solved", ptd[3] == 70988, ptd[3], 70988)
    chk("lv_municipalities", ptd[4] == 278, ptd[4], 278)
    chk("lv_refined_diverged_le_7", one("select count(*) from extension.lv_geo_ptd_results where geo_refined_status='DIVERGED'") <= 7,
        one("select count(*) from extension.lv_geo_ptd_results where geo_refined_status='DIVERGED'"), "<=7")
    chk("lv_segments_unique", one("select count(*) - count(distinct (ptd_code, node)) from extension.lv_geo_segments") == 0, "dupes", 0)
    chk("mv_customers_official_total", one("select sum(mat_at_mt_cpes) from observation.eredes_mat_at_mt_cpe_by_parish_2026_08") == 27956, one("select sum(mat_at_mt_cpes) from observation.eredes_mat_at_mt_cpe_by_parish_2026_08"), 27956)
    chk("mv_customer_sites_placed", one("select count(*) from extension.mv_customer_sites") == 25815, one("select count(*) from extension.mv_customer_sites"), 25815)
    ug = one("select sum(case when cable_class ilike '%cable%' or cable_class ilike '%under%' then 0 else 0 end) from extension.mv_urban_cable_segment") if False else None
    chk("mv_v1_root_batch_rows", one("select count(*) from study.equivalent_mv_root_peak_batch_mv_urban_cable_v1_summary") == 617, one("select count(*) from study.equivalent_mv_root_peak_batch_mv_urban_cable_v1_summary"), 617)
    chk("mv_v2_trial_removed", one("select count(*) from duckdb_tables() where table_name like '%mv_urban_cable_v2%'") == 0, "tables", 0)
    chk("hv_ren_transformers_match_annex_d", abs(one("select sum(sn_mva*coalesce(parallel,1)) from extension.hv_core_ren_transformers where source_status='REN_ANNEX_D_UNIT'") - 40533) < 1,
        one("select sum(sn_mva*coalesce(parallel,1)) from extension.hv_core_ren_transformers where source_status='REN_ANNEX_D_UNIT'"), 40533)
    dmax = one("""select max(sqrt(power((h.lon-l.lon)*111.32*cos(radians(h.lat)),2)+power((h.lat-l.lat)*110.54,2)))
                  from extension.hv_core_ren_transformers t join extension.hv_core_ren_buses h on h.bus_id=t.hv_bus
                  join extension.hv_core_ren_buses l on l.bus_id=t.lv_bus""")
    chk("hv_transformer_hv_lv_distance_le_10km", dmax <= 10.0, round(dmax, 2), "<=10 km")
    chk("core_tables_untouched_ptd_count", one("select count(*) from candidate.ptd_mv_attachment_assessment") == 72434, one("select count(*) from candidate.ptd_mv_attachment_assessment"), 72434)
    hv = WORK / "hv_core_resolve_summary.json"
    if hv.exists():
        t = json.loads(hv.read_text())["total"]
        chk("hv_cases_31492", t["cases"] == 31492, t["cases"], 31492)
        chk("hv_all_complete", t["complete"] == t["cases"], t["complete"], t["cases"])
        chk("hv_all_converged", t["converged"] == t["cases"], t["converged"], t["cases"])
        bad = []
        for m in sorted(p.name for p in hv_dir.iterdir() if p.is_dir() and p.name[:2] == "20"):
            f = hv_dir / m / f"pt60_models_{m}.compact.duckdb"
            if not f.exists(): bad.append(f"{m}:missing"); continue
            with duckdb.connect(str(f), read_only=True) as mc:
                q = lambda s: mc.execute(s).fetchone()[0]
                mism = q("select count(*) from (select line_id from grid.lines except select line_id from monthly_model.line_order "
                         "union all select line_id from monthly_model.line_order except select line_id from grid.lines)")
                mid = q("select string_agg(distinct model_id, ',') from grid.lines")
                if mism or mid != "CORE-3787-REN": bad.append(f"{m}:{mid}:{mism}")
        chk("hv_month_grid_tables_core3787", not bad, bad or "11 months OK", "grid.lines == line_order, model_id CORE-3787-REN")
    else:
        chk("hv_resolve_summary_present", False, "missing", str(hv))
    res = {"release_id": RID, "validated_at_utc": datetime.now(timezone.utc).isoformat(), "database": str(RDB), "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL", "checks": checks,
           "lv_lengths_km": {f"{a}/{b}": round(c, 1) for (a, b), c in seg.items()}}
    (WORK / "release_validation.json").write_text(json.dumps(res, indent=2, default=str)); print(json.dumps(res, indent=2, default=str))
    return res


# ---------------------------------------------------------------- freeze
def stage_freeze(hv_dir: Path) -> None:
    REL = ROOT / "data/releases" / RID
    if REL.exists(): raise SystemExit(f"release directory already exists (immutable): {REL}")
    val = json.loads((WORK / "release_validation.json").read_text())
    if val["status"] != "PASS": raise SystemExit("validation not PASS; run validate")
    for m in MONTHS:
        if not (hv_dir / m / f"pt60_models_{m}.compact.duckdb.zst").exists(): raise SystemExit(f"missing HV asset for {m} in {hv_dir}")
    tmp = REL.with_name(REL.name + ".building"); 
    if tmp.exists(): shutil.rmtree(tmp)
    for d in ("database", "hv_core", "documentation", "validation", "figures/source", "code/src", "code/hv_src", "code/scripts", "code/paper_scripts", "observations"):
        (tmp / d).mkdir(parents=True, exist_ok=True)
    print("compressing database ...", flush=True)
    subprocess.run(["zstd", "-q", "-19", "-T0", str(RDB), "-o", str(tmp / "database/simpt_power.duckdb.zst")], check=True)
    monthly = []
    for m in MONTHS:
        src = hv_dir / m / f"pt60_models_{m}.compact.duckdb.zst"; dst = tmp / "hv_core" / src.name; shutil.copy2(src, dst)
        monthly.append({"month": m, "file": src.name, "bytes": dst.stat().st_size, "sha256": sha(dst),
                        "uncompressed_sha256": sha(hv_dir / m / f"pt60_models_{m}.compact.duckdb")})
    (tmp / "hv_core/MONTHLY_MANIFEST.json").write_text(json.dumps(monthly, indent=2))
    shutil.copy2(HVW / "outputs/model/portuguese_hv_candidate.json", tmp / "hv_core/CORE-3787-REN_model.json")
    shutil.copy2(HVW / "outputs/validation/ren_reconciliation_report.json", tmp / "validation/ren_reconciliation_report.json")
    for f in [WORK / "release_validation.json", WORK / "hv_core_resolve_summary.json", LVA / "summary.json", MVC / "summary.json",
              DIAG / "lv_lengths_vs_erse_nuts3_summary.json", DIAG / "osm_census_summary.json", MVU / "mv_urban_cable_final_comparison.json",
              ROOT / "output/all_voltage/lv_geo_national_poleext/summary.json",
              ROOT / "output/all_voltage/td_cosim/20260115T1215_oltc_core3787_r2/summary.json",
              ROOT / "output/all_voltage/td_cosim/20260115T1215_oltc_core3787_r2/iterations.csv"]:
        if f.exists(): shutil.copy2(f, tmp / "validation" / (f.parent.name + "__" + f.name if f.name == "summary.json" else f.name))
    for f in ["paper/revision_all_voltage/SimPT-Power_zh.md", "docs/MV_LV_TRANSFORMER_FIX_PLAN_2026-09-29_CN.md",
              "docs/REN_ANNEX_B_LINE_RECONCILIATION_2026-09-29_CN.md", "paper/references_final.bib", "DATA_LICENSE.md"]:
        if (ROOT / f).exists(): shutil.copy2(ROOT / f, tmp / "documentation" / Path(f).name)
    for d in ("data/raw/erse_bt_characterization", "data/raw/eredes_mv_customers", "data/raw/ine_census2021"):
        for f in (ROOT / d).glob("*"):
            if f.is_file() and f.suffix != ".geojson": shutil.copy2(f, tmp / "observations" / f"{Path(d).name}__{f.name}")
    for f in (ROOT / "paper/revision_all_voltage/figures").glob("fig1[01]_*"): shutil.copy2(f, tmp / "figures" / f.name)
    for f in (ROOT / "paper/revision_all_voltage/figures/source").glob("fig1[01]_*.csv"): shutil.copy2(f, tmp / "figures/source" / f.name)
    for f in (ROOT / "src").glob("*.py"): shutil.copy2(f, tmp / "code/src" / f.name)
    for f in (HVW / "src").glob("*.py"): shutil.copy2(f, tmp / "code/hv_src" / f.name)
    for f in (ROOT / "scripts").glob("*.sh"): shutil.copy2(f, tmp / "code/scripts" / f.name)
    for f in ["paper/scripts/make_fig_validation_v2.py", "paper/scripts/build_all_voltage_release.py"]: shutil.copy2(ROOT / f, tmp / "code/paper_scripts" / Path(f).name)
    for f in HVW.glob("config/ren_*.csv"): shutil.copy2(f, tmp / "code/hv_src" / f.name)
    import duckdb
    from importlib import metadata as _md  # full environment, not only duckdb (r2 lock had only python/duckdb)
    pins = sorted({f"{d.metadata['Name']}=={d.version}" for d in _md.distributions() if d.metadata['Name']}, key=str.lower)
    (tmp / "requirements-lock.txt").write_text(f"# python=={sys.version.split()[0]}\n" + "\n".join(pins) + "\n")
    (tmp / "SOURCE_LICENSES.md").write_text("# Source licences\n\nE-REDES open data: CC BY 4.0. OpenStreetMap-derived records: ODbL (© OpenStreetMap contributors). REN, ERSE, INE, DGEG, GISCO and standards documents retain their own terms; see documentation/DATA_LICENSE.md and observations/*SOURCE*.md.\n")
    (tmp / "README.md").write_text(README.format(rid=RID))
    manifest = [{"path": str(p.relative_to(tmp)), "bytes": p.stat().st_size, "sha256": sha(p)} for p in sorted(tmp.rglob("*")) if p.is_file()]
    with (tmp / "file_manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "bytes", "sha256"]); w.writeheader(); w.writerows(manifest)
    (tmp / "SHA256SUMS").write_text("".join(f"{sha(p)}  {p.relative_to(tmp)}\n" for p in sorted(tmp.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"))
    git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True).stdout.strip()
    hv = json.loads((WORK / "hv_core_resolve_summary.json").read_text())
    rel = {"release_id": RID, "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "LOCAL_FROZEN_NOT_UPLOADED", "supersedes_local": "SimPT-Power-2026.09.30-r1 (withdrawn: Alto de São João 220/60 kV units attached to a same-name 60 kV bus 173 km away)",
           "supersedes": ["SimPT60-2026.09.21-r1 (HV core CORE-3783)", "SimPT60-AV-2026.09.24-r2 (all-voltage)"],
           "database_asset": "database/simpt_power.duckdb.zst", "database_uncompressed_sha256": sha(RDB), "database_uncompressed_bytes": RDB.stat().st_size,
           "hv_core_model": {"model_id": "CORE-3787-REN", "sha256": hv["model_sha256"], "snapshots": hv["total"]["cases"], "converged": hv["total"]["converged"]},
           "code_commit": git, "working_tree_note": "Uncommitted sources used are copied under code/.", "python": sys.version.split()[0], "platform": platform.platform(),
           "validation_status": val["status"], "public_url": None, "doi": None,
           "file_count": len(manifest) + 1}
    (tmp / "release.json").write_text(json.dumps(rel, indent=2))
    tmp.rename(REL); print(json.dumps(rel, indent=2)); print("frozen:", REL)


README = """# {rid}

Public-data-based multi-voltage (0.4–400 kV) simulation database for mainland Portugal. Local frozen release (not yet uploaded).

## What is new relative to SimPT60-2026.09.21-r1 and SimPT60-AV-2026.09.24-r2
- HV core CORE-3787-REN: 150–400 kV circuits and transmission transformers reconciled with REN Caracterização da RNT (31-12-2025) Annex B/D; all 31,492 15-min snapshots re-solved (`hv_core/`).
- MV-URBAN-CABLE-V1: inferred cable/overhead network connecting PTDs, with 617 re-solved root slices (`study.equivalent_mv_root_peak_batch_mv_urban_cable_v1_*`).
- MV customer connection estimate: 25,815 of 27,956 MAT/AT/MT delivery points placed from parish counts; connection km under three topology assumptions (`extension.mv_customer_*`; not in power flow).
- LV-GEO-A: geographic LV networks for 70,988 PTDs from public poles, OSM buildings and streets, with ABCN power flow (`extension.lv_geo_*`); LV-GEO-B sensitivity.
- New observation tables (REN Annex B/D, ERSE LV by NUTS III 2016, E-REDES delivery points by parish, INE Census 2021 buildings).
- The rejected MV-URBAN-CABLE-V2 trial tables are removed.

## Verify
`shasum -a 256 -c SHA256SUMS`; then `zstd -d database/simpt_power.duckdb.zst`.

## Scope
Layered public-data-derived simulation database; not an operator network or digital twin. Candidate, inferred, design and estimate layers are kept separate; extension layers are not registered in `model.object_registry`. The overhead/underground split of LV lines is not identifiable from public data (underground is a lower bound). MV customer connections are an estimate range, not part of the solved networks.
"""


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("stage", choices=["lv", "db", "validate", "freeze"])
    ap.add_argument("--workers", type=int, default=6); ap.add_argument("--hv-dir", type=Path, default=HV_DEFAULT)
    a = ap.parse_args()
    {"lv": lambda: stage_lv(a.workers), "db": stage_db, "validate": lambda: stage_validate(a.hv_dir), "freeze": lambda: stage_freeze(a.hv_dir)}[a.stage]()
