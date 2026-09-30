#!/usr/bin/env python3
"""Build the public release SimPT-Power-2026.09.30-r3 from the frozen r2 (r2 itself is not modified).

Same data as r2. Differences, all listed in release.json["public_changes"]:
  * local absolute paths (/Users/..., /Volumes/...) in the databases and JSON files are replaced by
    repository-relative or placeholder paths; databases are rewritten into fresh files so no old
    strings survive in free blocks;
  * not included: code/ (published separately), the manuscript draft and internal working notes in
    documentation/, and the third-party ERSE PDF (its extracted table and source link are kept).
Run on the Mac from the repo root:  .venv/bin/python src/build_public_release.py   (resumable)
"""
import hashlib, json, os, re, shutil, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[1]
SRC_RID, RID = "SimPT-Power-2026.09.30-r2", os.environ.get("PUBLIC_RID", "SimPT-Power-2026.09.30-r3")
R2 = ROOT / "data/releases" / SRC_RID
DEST = ROOT / "data/releases" / RID
R2_DB = ROOT / "output/simpt_power_release/simpt_power.duckdb"
HV = Path(os.environ.get("HV_OUT", "data/external/PT60_public_data_2025-05-01_2026-03-24/simpt_power_hv_core_ren_2026-09-30-r2"))
WORK = ROOT / "output/simpt_power_release/public_r3_work"
STAGE = WORK / "stage"                      # becomes DEST at the end
REPL = [(re.escape(str(ROOT)) + "/", ""),   # repo-relative
        (r"/Users/[^/]+/pt60_src/", "<source_db_dir>/"),
        (r"/Volumes/[^/]+/PT60_public_data_2025-05-01_2026-03-24/", "<external_data>/"),
        (r"/Volumes/[^/]+/", "<external_volume>/"),
        (r"/Users/[^/]+/", "<home>/")]
PAT = r"/Users/|/Volumes/"
NEEDLES = [b"/Users/", b"/Volumes/", __import__("getpass").getuser().encode()]
DROP_DOCS = {"SimPT-Power_zh.md", "MV_LV_TRANSFORMER_FIX_PLAN_2026-09-29_CN.md", "REN_ANNEX_B_LINE_RECONCILIATION_2026-09-29_CN.md", "references_final.bib"}
DROP_OBS = {"erse_bt_characterization__guia_caracterizacao_redes_bt_2016.pdf"}


def sha(p, n=1 << 22):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(n), b""): h.update(b)
    return h.hexdigest()


def clean_text(s: str) -> str:
    for a, b in REPL: s = re.sub(a, b, s)
    return s


def raw_scan(p: Path) -> list:
    """Byte-level scan of a whole file for leftover local paths or the user name."""
    hits, tail = set(), b""
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 24), b""):
            blk = tail + b
            for nd in NEEDLES:
                if nd in blk: hits.add(nd.decode())
            tail = blk[-64:]
    return sorted(hits)


def clean_db(src: Path, final: Path) -> dict:
    """copy -> update strings -> copy again into a fresh file (drops free blocks) -> verify."""
    tmp = final.with_suffix(".tmp.duckdb")
    for p in (tmp, final, Path(str(tmp) + ".wal"), Path(str(final) + ".wal")): p.unlink(missing_ok=True)
    c = duckdb.connect(":memory:")
    c.execute(f"ATTACH '{src}' AS s (READ_ONLY)"); c.execute(f"ATTACH '{tmp}' AS t")
    c.execute("COPY FROM DATABASE s TO t"); c.execute("DETACH s")
    cols = c.execute("""select schema_name, table_name, column_name, data_type from duckdb_columns()
                        where database_name='t' and data_type in ('VARCHAR','JSON')
                        and (schema_name, table_name) in (select schema_name, table_name from duckdb_tables() where database_name='t')""").fetchall()
    changed = {}
    for s, tb, col, typ in cols:
        q = f't."{s}"."{tb}"'; cv = f'CAST("{col}" AS VARCHAR)'
        n = c.execute(f"select count(*) from {q} where regexp_matches({cv}, '{PAT}')").fetchone()[0]
        if not n: continue
        expr = cv
        for a, b in REPL: expr = f"regexp_replace({expr}, '{a}', '{b}', 'g')"
        if typ == "JSON": expr = f"CAST({expr} AS JSON)"
        c.execute(f'UPDATE {q} SET "{col}" = {expr} WHERE regexp_matches({cv}, \'{PAT}\')')
        changed[f"{s}.{tb}.{col}"] = n
    left = []
    for (s, tb) in c.execute("select schema_name, table_name from duckdb_tables() where database_name='t'").fetchall():
        n = c.execute(f'select count(*) from t."{s}"."{tb}" x where regexp_matches(CAST(x AS VARCHAR), \'{PAT}\')').fetchone()[0]
        if n: left.append(f"{s}.{tb}:{n}")
    if left: raise SystemExit(f"paths left in {src.name}: {left}")
    c.execute("CHECKPOINT t"); c.execute(f"ATTACH '{final}' AS f"); c.execute("COPY FROM DATABASE t TO f")
    c.execute("CHECKPOINT f"); c.close(); tmp.unlink(); Path(str(tmp) + ".wal").unlink(missing_ok=True)
    hits = raw_scan(final)
    if hits: raise SystemExit(f"raw bytes still contain {hits} in {final}")
    return changed


def zst(p: Path) -> Path:
    out = p.with_name(p.name + ".zst")
    subprocess.run(["zstd", "-q", "-19", "-T0", "-f", str(p), "-o", str(out)], check=True)
    subprocess.run(["zstd", "-t", "-q", str(out)], check=True)
    return out


def main():
    if DEST.exists(): raise SystemExit(f"{DEST} exists (frozen releases are immutable)")
    r2 = json.loads((R2 / "release.json").read_text())
    for d in ["database", "hv_core", "validation", "observations", "figures/source", "documentation"]: (STAGE / d).mkdir(parents=True, exist_ok=True)
    log = {"removed_paths": {}}
    # 1. release database
    fdb = WORK / "simpt_power.duckdb"; done = STAGE / "database/simpt_power.duckdb.zst"
    if not done.exists():
        assert sha(R2_DB) == r2["database_uncompressed_sha256"], "working release DB differs from r2"
        print("database ...", flush=True); log["removed_paths"]["database"] = clean_db(R2_DB, fdb)
        shutil.move(str(zst(fdb)), done); log["database_uncompressed_sha256"] = sha(fdb); log["database_uncompressed_bytes"] = fdb.stat().st_size; fdb.unlink()
        (WORK / "db_log.json").write_text(json.dumps(log, indent=1))
    log.update(json.loads((WORK / "db_log.json").read_text()))
    # 2. monthly HV databases
    man2 = json.loads((R2 / "hv_core/MONTHLY_MANIFEST.json").read_text()); man = []
    for m in man2:
        month = m["month"]; out = STAGE / "hv_core" / m["file"]; mlog = WORK / f"month_{month}.json"
        if not (out.exists() and mlog.exists()):
            src = HV / month / f"pt60_models_{month}.compact.duckdb"
            assert sha(src) == m["uncompressed_sha256"], f"{src} differs from r2 manifest"
            print(month, "...", flush=True); fm = WORK / f"pt60_models_{month}.compact.duckdb"
            ch = clean_db(src, fm); z = zst(fm)
            mlog.write_text(json.dumps({"month": month, "file": m["file"], "bytes": z.stat().st_size, "sha256": sha(z), "uncompressed_sha256": sha(fm),
                                        "r2_uncompressed_sha256": m["uncompressed_sha256"], "removed_paths": ch}, indent=1))
            shutil.move(str(z), out); fm.unlink()
        e = json.loads(mlog.read_text()); log["removed_paths"][month] = e.pop("removed_paths"); man.append(e)
    (STAGE / "hv_core/MONTHLY_MANIFEST.json").write_text(json.dumps(man, indent=2))
    shutil.copy2(R2 / "hv_core/CORE-3787-REN_model.json", STAGE / "hv_core/CORE-3787-REN_model.json")
    # 3. small files
    for d, drop in [("validation", set()), ("observations", DROP_OBS), ("figures", set()), ("documentation", DROP_DOCS)]:
        for p in sorted((R2 / d).rglob("*")):
            if p.is_file() and p.name not in drop:
                q = STAGE / p.relative_to(R2); q.parent.mkdir(parents=True, exist_ok=True)
                if p.suffix in (".json", ".csv", ".md", ".txt"): q.write_text(clean_text(p.read_text()))
                else: shutil.copy2(p, q)
    shutil.copy2(R2 / "SOURCE_LICENSES.md", STAGE / "SOURCE_LICENSES.md")
    lock = ROOT / "kaggle/requirements-full-lock.txt"
    (STAGE / "requirements-lock.txt").write_text("# python==3.13.7 (macOS arm64); environment used to build and solve r2\n" + lock.read_text())
    (STAGE / "README.md").write_text(clean_text((R2 / "README.md").read_text()).replace(SRC_RID, RID).replace(
        "Local frozen release (not yet uploaded).", f"Public release. Same data as {SRC_RID}; local paths removed, see release.json.") +
        "\n## Code\nBuild and analysis code is published separately (see the data paper, Code Availability). Example notebooks and an uncompressed Parquet subset are in open_bundle_r3.zip.\n")
    rel = {k: v for k, v in r2.items() if k not in ("working_tree_note", "file_count")}
    rel.update(release_id=RID, created_at_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"), status="PUBLIC_RELEASE",
               derived_from=SRC_RID, database_uncompressed_sha256=log["database_uncompressed_sha256"], database_uncompressed_bytes=log["database_uncompressed_bytes"],
               doi=os.environ.get("PUBLIC_DOI", "10.5281/zenodo.23067065"),
               public_changes={"data": "identical to r2 except local absolute paths in text fields (replaced by repository-relative or <placeholder> paths)",
                               "not_included": ["code/ (published separately)", "documentation: manuscript draft and internal working notes", "observations: ERSE 2016 guide PDF (third-party; extracted table and source link kept)"],
                               "path_replacements_rows": log["removed_paths"]})
    rel["code_commit"] = r2.get("code_commit")
    (STAGE / "release.json").write_text(json.dumps(rel, indent=2, default=str))
    files = [p for p in sorted(STAGE.rglob("*")) if p.is_file() and p.name not in ("SHA256SUMS", "file_manifest.csv")]
    with open(STAGE / "file_manifest.csv", "w") as f:
        f.write("path,bytes,sha256\n"); [f.write(f"{p.relative_to(STAGE)},{p.stat().st_size},{sha(p)}\n") for p in files]
    with open(STAGE / "SHA256SUMS", "w") as f:
        [f.write(f"{sha(p)}  {p.relative_to(STAGE)}\n") for p in sorted(STAGE.rglob("*")) if p.is_file() and p.name != "SHA256SUMS"]
    bad = [str(p.relative_to(STAGE)) for p in STAGE.rglob("*") if p.is_file() and p.suffix != ".zst" and raw_scan(p)]
    if bad: raise SystemExit(f"local paths left in: {bad}")
    shutil.move(str(STAGE), DEST); print("frozen:", DEST)


if __name__ == "__main__":
    main()
