#!/usr/bin/env python3
"""Build SimPT-Power-2026.10.04-r4 from the rebuilt working database (r3.1 and earlier releases are not modified).

r4 = full rebuild of every load-dependent layer with the calibrated, time-varying station LV/MV split
(src/build_station_lv_share.py; scripts/rebuild_r4.sh). The HV core (hv_core/: CORE-3787-REN, 31,492 solved
snapshots) uses station totals only and is copied unchanged from r3.1.

Stages (run in order; each fits a small machine):
  db        working DB -> RAW (copy) + extension/observation layers + release metadata
  clean     scrub local absolute paths in RAW and rewrite into a fresh FINAL file (no free blocks)
  validate  checks -> output/simpt_power_release_r4/release_validation.json
  compress  zstd FINAL -> output/simpt_power_release_r4/simpt_power.duckdb.zst
  freeze    data/releases/<RID>/ (refuses to overwrite); hv_core/ and observations/ copied from r3.1
  bundle    open_bundle_r4.zip: hv/ from the r3.1 bundle, mv/ lv/ observations/ rebuilt from FINAL
Environment: R4_TMP (default ~/tmp/r4rel) holds the large intermediate databases.
"""
from __future__ import annotations
import csv, glob, hashlib, json, os, re, shutil, subprocess, sys, zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
RID = "SimPT-Power-2026.10.04-r4"
PARENT = "SimPT-Power-2026.10.03-r3.1"
R31 = ROOT / "data/releases" / PARENT
STAGING = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
WORK = ROOT / "output/simpt_power_release_r4"
TMP = Path(os.environ.get("R4_TMP", Path.home() / "tmp/r4rel"))
RAW, FINAL = TMP / "raw.duckdb", TMP / "simpt_power.duckdb"
R31_BUNDLE = ROOT / "output/zenodo_upload_r3/reupload/open_bundle_r3.zip"   # hv/ identical in r3, r3.1 and r4
CHANGES = [
    "Station LV/MV split: time-varying LV share calibrated to the national BT/(BT+MT) consumption share "
    "(logit level shift, E-REDES BTN/IP standard profiles, population-weighted heating/cooling degree days; "
    "embedded PV added back before the split). Replaces the fixed share capped by the PTD peak proxy.",
    "All load-dependent layers rebuilt: PTD and MV residual time series, root-peak timestamps, MV root and PTD "
    "transformer designs, LV feeder designs (two solve-and-refine cycles; voltage, ampacity and public fuse "
    "current), protection settings, short circuits, OSM operating designs, urban-cable root slices and the "
    "geographic LV networks (r3.1 solver).",
    "LV feeder design rules extended: parallel feeders are added for ampacity as well as voltage, and the "
    "nonlinear root-peak result drives a second refinement.",
    "HV core (hv_core/) and observation files are identical to r3.1.",
]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""): h.update(b)
    return h.hexdigest()


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def connect(path, read_only=False):
    import duckdb
    c = duckdb.connect(str(path), read_only=read_only)
    if os.environ.get("R4_DUCKDB_MEMORY"):
        c.execute(f"SET memory_limit='{os.environ['R4_DUCKDB_MEMORY']}'"); c.execute("SET threads=2")
        c.execute(f"SET temp_directory='{TMP / 'spill'}'")
    return c


def stage_db():
    import build_simpt_power_release as B
    B.LVA, B.LVB = WORK / "lv_geo_A", WORK / "lv_geo_B"
    TMP.mkdir(parents=True, exist_ok=True)
    for p in (RAW, Path(str(RAW) + ".wal")): p.unlink(missing_ok=True)
    c = connect(":memory:")
    c.execute(f"ATTACH {q(STAGING)} AS s (READ_ONLY)"); c.execute(f"ATTACH {q(RAW)} AS r")
    c.execute("COPY FROM DATABASE s TO r"); c.execute("DETACH s"); c.execute("USE r")
    c.execute("CREATE SCHEMA IF NOT EXISTS extension"); c.execute("CREATE SCHEMA IF NOT EXISTS observation")
    for (t,) in c.execute("select table_name from duckdb_tables() where database_name='r' and schema_name='study' and table_name like '%mv_urban_cable_v2%'").fetchall():
        c.execute(f"DROP TABLE study.{t}")
    rows = []
    for table, src, kind, ev, desc in B.layer_specs():
        files = sorted(glob.glob(str(src))) if any(ch in str(src) for ch in "*?") else ([str(src)] if Path(src).exists() else [])
        if not files: raise SystemExit(f"missing source for {table}: {src}")
        lst = "[" + ",".join(q(f) for f in files) + "]"
        if kind.startswith("csv"):
            sep = ";" if kind == "csv;" else ","
            sel = f"select * from read_csv({lst}, delim='{sep}', header=true, all_varchar=false, union_by_name=true, sample_size=-1)"
        else:
            sel = f"select * from read_parquet({lst}, union_by_name=true)"
        c.execute(f"CREATE OR REPLACE TABLE {table} AS {sel}")
        n = c.execute(f"select count(*) from {table}").fetchone()[0]
        rows.append((table, n, ev, desc, ";".join(os.path.relpath(f, ROOT) for f in files[:3]) + (f";(+{len(files) - 3} files)" if len(files) > 3 else ""),
                     ";".join(sha(Path(f)) for f in files) if len(files) <= 3 else "multiple, see file_manifest"))
        print(f"{table}: {n:,}", flush=True)
    summ = [json.loads(Path(f).read_text()) for f in sorted(glob.glob(str(B.MVC / "municipality/summary_*.json")))]
    c.execute("CREATE OR REPLACE TABLE extension.mv_customer_connection_municipality (concelho VARCHAR, official_customers INT, placed INT, not_placed INT, underground_customers INT, overhead_customers INT, topology VARCHAR, underground_km DOUBLE, overhead_km DOUBLE)")
    for s in summ:
        for t in ("shared", "radial", "loop_in"):
            c.execute("insert into extension.mv_customer_connection_municipality values (?,?,?,?,?,?,?,?,?)",
                      [s["concelho"], s.get("official_customers"), s.get("placed"), s.get("not_placed_no_building"), s.get("underground_customers"), s.get("overhead_customers"),
                       t, s.get("underground_km", {}).get(t), s.get("overhead_km", {}).get(t)])
    rows.append(("extension.mv_customer_connection_municipality", len(summ) * 3, "INFERRED", "MV customer connection km per municipality and topology assumption", "output/all_voltage/mv_customer_national_osmtap/municipality/summary_*.json", "multiple"))
    c.execute("CREATE OR REPLACE TABLE extension.release_layer_manifest (table_name VARCHAR, row_count BIGINT, evidence_class VARCHAR, description VARCHAR, source_paths VARCHAR, source_sha256 VARCHAR)")
    c.executemany("insert into extension.release_layer_manifest values (?,?,?,?,?,?)", rows)
    c.execute("CREATE OR REPLACE TABLE extension.release_info AS SELECT ? release_id, ? built_at_utc, ? note, ? parent_release",
              [RID, datetime.now(timezone.utc).isoformat(), " ".join(CHANGES), PARENT])
    c.execute("CHECKPOINT r"); c.close(); print("db ok", RAW, RAW.stat().st_size)


def stage_clean():
    import build_public_release as P
    c = connect(":memory:"); c.execute(f"ATTACH {q(RAW)} AS t")
    cols = c.execute("""select schema_name, table_name, column_name, data_type from duckdb_columns()
                        where database_name='t' and data_type in ('VARCHAR','JSON')
                        and (schema_name, table_name) in (select schema_name, table_name from duckdb_tables() where database_name='t')""").fetchall()
    changed = {}
    for s, tb, col, typ in cols:
        qq = f't."{s}"."{tb}"'; cv = f'CAST("{col}" AS VARCHAR)'
        n = c.execute(f"select count(*) from {qq} where regexp_matches({cv}, '{P.PAT}')").fetchone()[0]
        if not n: continue
        expr = cv
        for a, b in P.REPL: expr = f"regexp_replace({expr}, '{a}', '{b}', 'g')"
        if typ == "JSON": expr = f"CAST({expr} AS JSON)"
        c.execute(f'UPDATE {qq} SET "{col}" = {expr} WHERE regexp_matches({cv}, \'{P.PAT}\')')
        changed[f"{s}.{tb}.{col}"] = n
    c.execute("CHECKPOINT t")
    for p in (FINAL, Path(str(FINAL) + ".wal")): p.unlink(missing_ok=True)
    c.execute(f"ATTACH {q(FINAL)} AS f"); c.execute("COPY FROM DATABASE t TO f"); c.execute("CHECKPOINT f"); c.close()
    RAW.unlink(); Path(str(RAW) + ".wal").unlink(missing_ok=True)
    hits = P.raw_scan(FINAL)
    (WORK / "path_cleaning.json").write_text(json.dumps({"changed_columns": changed, "raw_byte_hits": hits}, indent=2))
    if hits: raise SystemExit(f"raw bytes still contain {hits}")
    print("clean ok", FINAL.stat().st_size, changed)


def stage_validate():
    c = connect(FINAL, read_only=True)
    one = lambda s: c.execute(s).fetchone()[0]
    checks = []
    def chk(name, ok, value, expect): checks.append({"check": name, "status": "PASS" if ok else "FAIL", "value": value, "expected": expect})
    chk("ptds", one("select count(*) from candidate.ptd_mv_attachment_assessment") == 72434, one("select count(*) from candidate.ptd_mv_attachment_assessment"), 72434)
    chk("object_ids_unique", one("select count(*)-count(distinct object_id) from model.object_registry") == 0, "dupes", 0)
    chk("calendar_31492", one("select count(*) from operating.operating_snapshot") == 31492, one("select count(*) from operating.operating_snapshot"), 31492)
    chk("lv_share_rows", one("select count(*) from operating.station_lv_share_15min") == 397 * 31492, one("select count(*) from operating.station_lv_share_15min"), 397 * 31492)
    chk("lv_share_bounds", one("select count(*) from operating.station_lv_share_15min where lv_share<0 or lv_share>1") == 0, "out of [0,1]", 0)
    bal = one("""WITH x AS (SELECT timestamp_utc FROM operating.operating_snapshot ORDER BY timestamp_utc LIMIT 2)
        SELECT max(abs(st.p_mw - coalesce(lv.p,0) - mv.p_mw)) FROM operating.station_15min_complete st JOIN x USING(timestamp_utc)
        JOIN operating.station_lv_split f USING(station_code)
        LEFT JOIN (SELECT assigned_station_code AS station_code, timestamp_utc, sum(p_mw) p FROM operating.ptd_load_15min
                   WHERE allocation_status='DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL' AND timestamp_utc IN (SELECT timestamp_utc FROM x) GROUP BY 1,2) lv USING(station_code,timestamp_utc)
        JOIN operating.station_mv_residual_15min mv USING(station_code,timestamp_utc)""")
    chk("station_lv_plus_mv_balance", bal is not None and bal < 1e-8, bal, "<1e-8 MW")
    res = {r[0]: r[1] for r in c.execute("select result, count(*) from study.equivalent_mv_root_peak_batch_summary group by 1").fetchall()}
    chk("root_slices_star_617_solved", sum(res.values()) == 617, res, "617 reports")
    resu = {r[0]: r[1] for r in c.execute("select result, count(*) from study.equivalent_mv_root_peak_batch_mv_urban_cable_v1_summary group by 1").fetchall()}
    chk("root_slices_urban_617_solved", sum(resu.values()) == 617, resu, "617 reports")
    lv = c.execute("select count(*), count(*) filter (where status='CONVERGED') from study.lv_fourwire_root_peak_design_snapshot").fetchone()
    chk("lv_fourwire_root_peak_72434", lv[0] == 72434, {"rows": lv[0], "converged": lv[1]}, 72434)
    chk("protection_devices", one("select count(*) from protection.protective_device") > 0, one("select count(*) from protection.protective_device"), ">0")
    chk("lv_geo_ptds", one("select count(*) from extension.lv_geo_ptd_results") == 70988, one("select count(*) from extension.lv_geo_ptd_results"), 70988)
    chk("lv_geo_segments_unique", one("select count(*)-count(distinct (ptd_code,node)) from extension.lv_geo_segments") == 0, "dupes", 0)
    chk("mv_v2_trial_removed", one("select count(*) from duckdb_tables() where table_name like '%mv_urban_cable_v2%'") == 0, "tables", 0)
    cs = json.loads((ROOT / "output/all_voltage/complete_simulation_database.validation.json").read_text())
    chk("complete_simulation_database_gates", cs.get("result") == "PASS", cs.get("result"), "PASS")
    sums = dict(l.split("  ", 1)[::-1] for l in (R31 / "SHA256SUMS").read_text().splitlines())
    bad = [p for p, h in sums.items() if p.startswith(("hv_core/", "observations/")) and sha(R31 / p) != h]
    chk("r31_hv_core_and_observations_intact", not bad, bad or "ok", "sha256 as r3.1")
    lvs = {r[0]: r[1] for r in c.execute("select scope||'.'||metric, value from audit.station_lv_share_validation").fetchall()}
    out = {"release_id": RID, "parent_release": PARENT, "validated_at_utc": datetime.now(timezone.utc).isoformat(),
           "status": "PASS" if all(x["status"] == "PASS" for x in checks) else "FAIL", "checks": checks, "lv_share_validation": lvs}
    WORK.mkdir(parents=True, exist_ok=True)
    (WORK / "release_validation.json").write_text(json.dumps(out, indent=2, default=str)); print(json.dumps(out, indent=2, default=str))


def stage_compress():
    out = WORK / "simpt_power.duckdb.zst"
    subprocess.run(["zstd", "-q", f"-{os.environ.get('ZSTD_LEVEL', '17')}", "-T0", "-f", str(FINAL), "-o", str(out)], check=True)
    subprocess.run(["zstd", "-t", "-q", str(out)], check=True)
    (WORK / "simpt_power.duckdb.sha256").write_text(sha(FINAL) + "\n")
    print(out, out.stat().st_size)


README = """# {rid}

Public-data-based multi-voltage (0.4–400 kV) simulation database for mainland Portugal.

## Changes relative to {parent}
{changes}

## Verify
`shasum -a 256 -c SHA256SUMS`; then `zstd -d database/simpt_power.duckdb.zst`.

## Scope
Layered public-data-derived simulation database; not an operator network or digital twin. Candidate, inferred, design and estimate layers are kept separate; extension layers are not registered in `model.object_registry`. The national BT/(BT+MT) consumption share is used to calibrate the LV/MV split (three parameters); the leave-one-month-out error is the validation result (`audit.station_lv_share_validation`). The overhead/underground split of LV lines is not identifiable from public data (underground is a lower bound). MV customer connections are an estimate range, not part of the solved networks.

## Code
Build and analysis code is published separately (see the data paper, Code Availability); `scripts/rebuild_r4.sh` and `src/build_simpt_power_r4.py` build this release. Example notebooks and an uncompressed Parquet subset are in open_bundle_r4.zip.
"""


def stage_freeze():
    REL = ROOT / "data/releases" / RID
    if REL.exists(): raise SystemExit(f"release directory already exists (immutable): {REL}")
    val = json.loads((WORK / "release_validation.json").read_text())
    if val["status"] != "PASS": raise SystemExit("validation not PASS")
    tmp = REL.with_name(REL.name + ".building")
    if tmp.exists(): raise SystemExit(f"remove partial build first: {tmp}")
    # hv_core files are unchanged and never rewritten below: hard-link them (2 GB) instead of copying
    def _cp(src, dst):
        return os.link(src, dst) if "/hv_core/" in str(src) else shutil.copy2(src, dst)
    shutil.copytree(R31, tmp, copy_function=_cp, ignore=shutil.ignore_patterns("simpt_power.duckdb.zst", "SHA256SUMS", "file_manifest.csv", "release.json", "README.md", "CHANGELOG_r3.1.md"))
    shutil.copy2(WORK / "simpt_power.duckdb.zst", tmp / "database/simpt_power.duckdb.zst")
    changed = {"database/simpt_power.duckdb.zst", "README.md", "documentation/CHANGELOG_r4.md"}
    vd = {"release_validation.json": WORK / "release_validation.json",
          "lv_geo_A__summary.json": WORK / "lv_geo_A/summary.json",
          "lv_geo_national_poleext__summary.json": WORK / "lv_geo_B/summary.json",
          "mv_urban_cable_final_comparison.json": ROOT / "output/all_voltage/mv_urban_cable_v1/mv_urban_cable_final_comparison_r4.json",
          "station_lv_share.json": ROOT / "output/all_voltage/station_lv_share/validation.json",
          "complete_simulation_database.json": ROOT / "output/all_voltage/complete_simulation_database.validation.json",
          "lv_lengths_vs_erse_nuts3_summary.json": ROOT / "output/all_voltage/lv_geo_national/diagnosis/lv_lengths_vs_erse_nuts3_summary.json"}
    import build_public_release as P
    for name, src in vd.items():
        if src.exists():
            (tmp / "validation" / name).write_text(P.clean_text(src.read_text())); changed.add(f"validation/{name}")
    fig = ROOT / "paper/revision_all_voltage/figures"
    for f in list((tmp / "figures").glob("*")) + list((tmp / "figures/source").glob("*")):
        if f.is_file():
            src = (fig / f.relative_to(tmp / "figures"))
            if src.exists(): shutil.copy2(src, f); changed.add(str(f.relative_to(tmp)))
    lvs = val.get("lv_share_validation", {})
    (tmp / "documentation/CHANGELOG_r4.md").write_text(f"# {RID}\n\nParent: {PARENT}\n\n" + "\n".join(f"- {x}" for x in CHANGES) + "\n\nLV-share validation: " + json.dumps(lvs) + "\n")
    (tmp / "README.md").write_text(README.format(rid=RID, parent=PARENT, changes="\n".join(f"- {x}" for x in CHANGES)))
    r31sha = {r["path"]: r["sha256"] for r in csv.DictReader((R31 / "file_manifest.csv").open())}
    H = {}
    for p in sorted(tmp.rglob("*")):
        if p.is_file():
            rp = str(p.relative_to(tmp)); H[rp] = r31sha[rp] if (rp in r31sha and rp not in changed) else sha(p)
    with (tmp / "file_manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "bytes", "sha256"]); w.writeheader()
        for rp, h in H.items(): w.writerow({"path": rp, "bytes": (tmp / rp).stat().st_size, "sha256": h})
    r31 = json.loads((R31 / "release.json").read_text()); rel = dict(r31)
    for k in ("patch_of", "changes", "supersedes_local"): rel.pop(k, None)
    rel.update({"release_id": RID, "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "LOCAL_FROZEN_NOT_UPLOADED",
                "derived_from": PARENT, "supersedes": [PARENT], "changes_from_parent": CHANGES,
                "database_uncompressed_sha256": (WORK / "simpt_power.duckdb.sha256").read_text().strip(),
                "database_uncompressed_bytes": FINAL.stat().st_size if FINAL.exists() else None,
                "validation_status": val["status"], "public_url": None, "doi": None, "file_count": len(H) + 2,
                "code_commit": os.environ.get("R4_CODE_COMMIT") or None,
                "working_tree_note": "Built with scripts/rebuild_r4.sh and src/build_simpt_power_r4.py."})
    (tmp / "release.json").write_text(json.dumps(rel, indent=2))
    H["file_manifest.csv"] = sha(tmp / "file_manifest.csv"); H["release.json"] = sha(tmp / "release.json")
    (tmp / "SHA256SUMS").write_text("".join(f"{h}  {rp}\n" for rp, h in sorted(H.items())))
    tmp.rename(REL); print("frozen:", REL)


def stage_bundle():
    work = TMP / "bundle"; shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
    with zipfile.ZipFile(R31_BUNDLE) as z: z.extractall(work)
    out = work / "open_bundle_r4"; (work / "open_bundle_r3").rename(out)
    man = json.loads((out / "bundle_manifest.json").read_text())
    c = connect(FINAL, read_only=True)
    T = [("parameter.mv_root_transformer_parameters", "mv/root_transformers.parquet"), ("equivalent.ptd_connections", "mv/ptd_connections.parquet"),
         ("extension.mv_urban_cable_segment", "mv/urban_cable_segments.parquet"), ("extension.mv_customer_sites", "mv/customer_sites.parquet"),
         ("candidate.ptd_public_attributes", "lv/ptd_public_attributes.parquet"), ("parameter.ptd_transformer_parameters", "lv/ptd_transformers.parquet"),
         ("extension.lv_geo_ptd_results", "lv/lv_geo_ptd_results.parquet"), ("extension.lv_geo_segments", "lv/lv_geo_segments.parquet")]
    for t, p in T:
        c.execute(f"COPY (select * from {t}) TO {q(out / p)} (FORMAT parquet, COMPRESSION zstd)")
        man["files"].setdefault(p, {})["rows"] = c.execute(f"select count(*) from {t}").fetchone()[0]
    c.execute(f"COPY (select * from operating.station_lv_share_15min) TO {q(out / 'mv/station_lv_share_15min.parquet')} (FORMAT parquet, COMPRESSION zstd)")
    man["files"]["mv/station_lv_share_15min.parquet"] = {"rows": c.execute("select count(*) from operating.station_lv_share_15min").fetchone()[0],
                                                          "description": "r4 time-varying LV share of each substation (15 min)"}
    c.close()
    rel = json.loads((ROOT / "data/releases" / RID / "release.json").read_text())
    man.update({"bundle_of": RID, "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "release_database_sha256": rel["database_uncompressed_sha256"]})
    (out / "bundle_manifest.json").write_text(json.dumps(man, indent=2, default=str))
    with open(out / "SHA256SUMS", "w") as f:
        for p in sorted(out.rglob("*")):
            if p.is_file() and p.name != "SHA256SUMS": f.write(f"{sha(p)}  {p.relative_to(out)}\n")
    zp = WORK / "open_bundle_r4.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
        for p in sorted(out.rglob("*")):
            if p.is_file(): z.write(p, p.relative_to(work))
    shutil.rmtree(work); print(zp, zp.stat().st_size)


if __name__ == "__main__":
    {"db": stage_db, "clean": stage_clean, "validate": stage_validate, "compress": stage_compress,
     "freeze": stage_freeze, "bundle": stage_bundle}[sys.argv[1]]()
