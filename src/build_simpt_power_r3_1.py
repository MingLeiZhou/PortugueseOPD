#!/usr/bin/env python3
"""Build SimPT-Power-2026.10.03-r3.1: a patch release of SimPT-Power-2026.09.30-r3 (r3 is not modified).

Only the geographic LV layer changes (src/pilot_lv_geographic_powerflow.py, r3.1 fixes):
  1. the PTD input is the station-derived net load (load_p_mw); r3 subtracted PV a second time;
  2. each branch is solved with the cable of its own class (pole/ext lines LXS 4x95, street lines by PTD type,
     service drops with the class of the line they hang on); r3 used one cable per PTD tree;
  3. the neutral resistance is scaled by the phase/neutral cross-section ratio (LXAV 3x185+95: r x 185/95).
All other tables and assets are byte-identical to r3.

Stages (in order):
  db        copy the r3 database (R3_DB, sha256-checked) to R31_DB and replace the three LV-GEO tables
  validate  checks -> WORK/release_validation.json
  compress  zstd R31_DB -> WORK/simpt_power.duckdb.zst  (ZSTD_LEVEL, default 12)
  freeze    write data/releases/<RID>/ (refuses to overwrite)
  bundle    patch the r3 open bundle (lv/*.parquet) -> WORK/open_bundle_r3_1.zip
"""
from __future__ import annotations
import csv, glob, hashlib, json, os, shutil, subprocess, sys, zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RID = "SimPT-Power-2026.10.03-r3.1"
PARENT = "SimPT-Power-2026.09.30-r3"
R3 = ROOT / "data/releases" / PARENT
WORK = ROOT / "output/simpt_power_release_r3_1"
LVA, LVB = WORK / "lv_geo_A", WORK / "lv_geo_B"
R3_DB = Path(os.environ.get("R3_DB", Path.home() / "tmp/r3db/simpt_power.duckdb"))
R31_DB = Path(os.environ.get("R31_DB", Path.home() / "tmp/r31db/simpt_power.duckdb"))
R3_BUNDLE = ROOT / "output/zenodo_upload_r3/reupload/open_bundle_r3.zip"
REPLACED = {
    "extension.lv_geo_ptd_results": (LVA / "lv_geo_powerflow_national.csv", "csv", "INFERRED+SIMULATED", "LV-GEO-A per-PTD geographic network lengths and ABCN power-flow results (r3.1 solver)"),
    "extension.lv_geo_segments": (LVA / "segments/lv_geo_segments_*.parquet", "parquet", "INFERRED", "LV-GEO-A segments with the solved per-branch cable, impedance, neutral resistance, ampacity and refined parallels (r3.1)"),
    "extension.lv_geo_ptd_results_pole_extension": (LVB / "lv_geo_powerflow_national.csv", "csv", "INFERRED+SIMULATED", "LV-GEO-B sensitivity: street routes extend from the nearest own pole (r3.1 solver)"),
}
CHANGES = [
    "LV-GEO-A/B power flow: PTD input is the station-derived net load; r3 subtracted PV a second time.",
    "LV-GEO-A/B power flow: per-branch cable parameters (pole lines LXS 4x95, street lines by PTD type, service drops follow their parent line); r3 used one cable per PTD tree.",
    "LV-GEO-A/B power flow: neutral resistance scaled by the phase/neutral cross-section ratio (LXAV 3x185+95).",
    "extension.lv_geo_segments gains r_ohm_per_km, x_ohm_per_km, r_neutral_ohm_per_km, permissible_current_a; service drops keep cable_class 'service_drop' and carry the cable of the line they hang on in standard_cable.",
    "All other tables, the HV core (hv_core/) and observations are byte-identical to r3.",
]


def sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""): h.update(b)
    return h.hexdigest()


def q(p) -> str:
    return "'" + str(p).replace("'", "''") + "'"


def stage_db():
    import duckdb
    r3 = json.loads((R3 / "release.json").read_text())
    assert sha(R3_DB) == r3["database_uncompressed_sha256"], "R3_DB is not the r3 database"
    R31_DB.parent.mkdir(parents=True, exist_ok=True)
    tmp = R31_DB.with_suffix(".copying"); shutil.copy2(R3_DB, tmp); tmp.rename(R31_DB)
    con = duckdb.connect(str(R31_DB))
    for table, (src, kind, ev, desc) in REPLACED.items():
        files = sorted(glob.glob(str(src)))
        if not files: raise SystemExit(f"missing {src}")
        lst = "[" + ",".join(q(f) for f in files) + "]"
        sel = (f"select * from read_csv({lst}, header=true, union_by_name=true, sample_size=-1)" if kind == "csv"
               else f"select * replace (case when segment_kind='drop' then 'service_drop' else cable_class end as cable_class) from read_parquet({lst}, union_by_name=true)")
        con.execute(f"CREATE OR REPLACE TABLE {table} AS {sel}")
        n = con.execute(f"select count(*) from {table}").fetchone()[0]
        con.execute("update extension.release_layer_manifest set row_count=?, description=?, source_paths=?, source_sha256=? where table_name=?",
                    [n, desc, ";".join(os.path.relpath(f, ROOT) for f in files[:3]) + (f";(+{len(files) - 3} files)" if len(files) > 3 else ""),
                     ";".join(sha(Path(f)) for f in files) if len(files) <= 3 else "multiple", table])
        print(f"{table}: {n:,}", flush=True)
    con.execute("CREATE OR REPLACE TABLE extension.release_info AS SELECT ? release_id, ? built_at_utc, ? note, ? parent_release",
                [RID, datetime.now(timezone.utc).isoformat(), "Patch of r3: only the LV-GEO tables differ. " + " ".join(CHANGES[:3]), PARENT])
    con.execute("CHECKPOINT"); con.close(); print("db ok", R31_DB, R31_DB.stat().st_size)


def stage_validate():
    import duckdb
    con = duckdb.connect(str(R31_DB), read_only=True); old = duckdb.connect(str(R3_DB), read_only=True)
    one = lambda s, c=con: c.execute(s).fetchone()[0]
    checks = []
    def chk(name, ok, value, expect): checks.append({"check": name, "status": "PASS" if ok else "FAIL", "value": value, "expected": expect})
    # every table except the replaced ones and the release metadata is unchanged
    tabs = lambda c: {f"{s}.{t}": n for s, t, n in c.execute("select schema_name, table_name, estimated_size from duckdb_tables()").fetchall()}
    a, b = tabs(old), tabs(con)
    skip = set(REPLACED) | {"extension.release_info", "extension.release_layer_manifest"}
    diff = [t for t in sorted(set(a) | set(b)) if t not in skip and (t not in a or t not in b or one(f"select count(*) from {t}", old) != one(f"select count(*) from {t}"))]
    chk("unchanged_tables_same_rowcount", not diff, diff or f"{len(set(a) - skip)} tables", "identical to r3")
    seg = {(k, c): v for k, c, v in con.execute("select segment_kind, cable_class, sum(length_km) from extension.lv_geo_segments group by all").fetchall()}
    ptd = con.execute("select sum(overhead_km), sum(street_km), sum(drops_km), count(*), count(distinct substr(ptd_code,1,4)) from extension.lv_geo_ptd_results").fetchone()
    pole = sum(v for (k, c), v in seg.items() if k == "pole"); street = sum(v for (k, c), v in seg.items() if k == "street"); drop = sum(v for (k, c), v in seg.items() if k == "drop")
    chk("lv_pole_km_segments_equal_ptd_sum", abs(pole - ptd[0]) < 1, round(pole, 1), round(ptd[0], 1))
    chk("lv_street_km_segments_equal_ptd_sum", abs(street - ptd[1]) < 1, round(street, 1), round(ptd[1], 1))
    chk("lv_drop_km_segments_equal_ptd_sum", abs(drop - ptd[2]) < 1, round(drop, 1), round(ptd[2], 1))
    oldlen = old.execute("select sum(overhead_km), sum(street_km), sum(drops_km) from extension.lv_geo_ptd_results").fetchone()
    con.execute(f"ATTACH {q(R3_DB)} AS r3 (READ_ONLY)")
    ndiff = one("""select count(*) from extension.lv_geo_ptd_results x join r3.extension.lv_geo_ptd_results y using(ptd_code)
                   where abs(x.overhead_km-y.overhead_km)>1e-6 or abs(x.street_km-y.street_km)>1e-6 or abs(x.drops_km-y.drops_km)>1e-6""")
    chk("lv_lengths_as_r3", ndiff <= 10 and all(abs(x - y) < 0.1 for x, y in zip(ptd[:3], oldlen)),
        {"km": [round(x, 3) for x in ptd[:3]], "ptds_with_route_difference": ndiff}, {"km_r3": [round(x, 3) for x in oldlen], "tolerance": "<0.1 km total, <=10 PTDs (street-route rebuild)"})
    chk("lv_ptds_solved", ptd[3] == 70988, ptd[3], 70988)
    chk("lv_municipalities", ptd[4] == 278, ptd[4], 278)
    chk("lv_segments_unique", one("select count(*) - count(distinct (ptd_code, node)) from extension.lv_geo_segments") == 0, "dupes", 0)
    chk("lv_segments_have_parameters", one("select count(*) from extension.lv_geo_segments where r_ohm_per_km is null or r_neutral_ohm_per_km is null or permissible_current_a is null") == 0, "null parameter rows", 0)
    chk("lv_service_drops_labelled", one("select count(*) from extension.lv_geo_segments where segment_kind='drop' and cable_class<>'service_drop'") == 0, "mislabelled drops", 0)
    chk("lv_segment_class_consistent_with_cable", one("""select count(*) from extension.lv_geo_segments where (cable_class='overhead' and standard_cable<>'LXS 4 x 95')
                                                         or (cable_class='underground' and standard_cable<>'LXAV 3x185+95')""") == 0, "mismatches", 0)
    dmax = one("""select max(abs(g.load_kw/1000 - s.load_p_mw)) from extension.lv_geo_ptd_results g
                  join study.lv_fourwire_root_peak_design_snapshot s using(ptd_code)""")
    chk("lv_geo_input_equals_compact_net_load", dmax < 1e-9, dmax, "<1e-9 MW (no second PV subtraction)")
    div = one("select count(*) from extension.lv_geo_ptd_results where geo_refined_status='DIVERGED'")
    chk("lv_refined_diverged_reported", True, div, "kept as failures")
    # unchanged release assets
    sums = dict(l.split("  ", 1)[::-1] for l in (R3 / "SHA256SUMS").read_text().splitlines())
    bad = [p for p, h in sums.items() if p.startswith(("hv_core/", "observations/")) and sha(R3 / p) != h]
    chk("r3_hv_core_and_observations_intact", not bad, bad or f"{sum(p.startswith(('hv_core/', 'observations/')) for p in sums)} files", "sha256 as r3")
    s = json.loads((LVA / "summary.json").read_text())
    res = {"release_id": RID, "parent_release": PARENT, "validated_at_utc": datetime.now(timezone.utc).isoformat(),
           "status": "PASS" if all(c["status"] == "PASS" for c in checks) else "FAIL", "checks": checks,
           "lv_geo_a_summary": {k: s[k] for k in ("ptds_solved", "ptds_refined_ok", "ptds_diverged", "vmin_below_0_9", "overloaded", "vmin_quantiles")},
           "lv_lengths_km": {f"{k}/{c}": round(v, 1) for (k, c), v in seg.items()}}
    (WORK / "release_validation.json").write_text(json.dumps(res, indent=2, default=str)); print(json.dumps(res, indent=2, default=str))


def stage_compress():
    lvl = os.environ.get("ZSTD_LEVEL", "12")
    out = WORK / "simpt_power.duckdb.zst"
    subprocess.run(["zstd", "-q", f"-{lvl}", "-T0", "-f", str(R31_DB), "-o", str(out)], check=True); print(out, out.stat().st_size)


README = """# {rid}

Public-data-based multi-voltage (0.4–400 kV) simulation database for mainland Portugal. Patch release of {parent}.

## Changes relative to {parent}
{changes}

Affected results (LV-GEO-A, 70,988 PTDs): {a}

## Verify
`shasum -a 256 -c SHA256SUMS`; then `zstd -d database/simpt_power.duckdb.zst`.

## Scope
Layered public-data-derived simulation database; not an operator network or digital twin. Candidate, inferred, design and estimate layers are kept separate; extension layers are not registered in `model.object_registry`. The overhead/underground split of LV lines is not identifiable from public data (underground is a lower bound). MV customer connections are an estimate range, not part of the solved networks.

## Code
Build and analysis code is published separately (see the data paper, Code Availability); `src/build_simpt_power_r3_1.py` builds this patch release. Example notebooks and an uncompressed Parquet subset are in open_bundle_r3_1.zip.
"""


def stage_freeze():
    REL = ROOT / "data/releases" / RID
    if REL.exists(): raise SystemExit(f"release directory already exists (immutable): {REL}")
    val = json.loads((WORK / "release_validation.json").read_text())
    if val["status"] != "PASS": raise SystemExit("validation not PASS")
    zst = WORK / "simpt_power.duckdb.zst"
    tmp = REL.with_name(REL.name + ".building")
    if tmp.exists(): shutil.rmtree(tmp)
    shutil.copytree(R3, tmp, ignore=shutil.ignore_patterns("simpt_power.duckdb.zst", "SHA256SUMS", "file_manifest.csv", "release.json", "README.md"))
    shutil.copy2(zst, tmp / "database/simpt_power.duckdb.zst")
    shutil.copy2(WORK / "release_validation.json", tmp / "validation/release_validation.json")
    shutil.copy2(LVA / "summary.json", tmp / "validation/lv_geo_A__summary.json")
    shutil.copy2(LVB / "summary.json", tmp / "validation/lv_geo_national_poleext__summary.json")
    fig = ROOT / "paper/revision_all_voltage/figures"
    for f in fig.glob("fig11_internal_feasibility.*"):
        if "BEFORE" not in f.name and (tmp / "figures" / f.name).exists(): shutil.copy2(f, tmp / "figures" / f.name)
    a = val["lv_geo_a_summary"]
    (tmp / "documentation/CHANGELOG_r3.1.md").write_text(f"# {RID}\n\nParent: {PARENT}\n\n" + "\n".join(f"- {c}" for c in CHANGES) +
        "\n\nLV-GEO-A results r3 -> r3.1: below 0.9 p.u. 111 -> {0}, overloaded 25 -> {1}, diverged 7 -> {2}, median Vmin 0.976 -> {3}.\n".format(
            a["vmin_below_0_9"], a["overloaded"], a["ptds_diverged"], a["vmin_quantiles"]["0.5"]))
    (tmp / "README.md").write_text(README.format(rid=RID, parent=PARENT, changes="\n".join(f"- {c}" for c in CHANGES),
        a=f"{a['vmin_below_0_9']} PTDs below 0.9 p.u., {a['overloaded']} overloaded, {a['ptds_diverged']} not converged, median minimum phase voltage {a['vmin_quantiles']['0.5']} p.u."))
    r3sha = {r["path"]: r["sha256"] for r in csv.DictReader((R3 / "file_manifest.csv").open())}
    changed = {"database/simpt_power.duckdb.zst", "validation/release_validation.json", "validation/lv_geo_A__summary.json",
               "validation/lv_geo_national_poleext__summary.json", "documentation/CHANGELOG_r3.1.md", "README.md"} | {f"figures/{f.name}" for f in fig.glob("fig11_internal_feasibility.*")}
    H = {}
    for p in sorted(tmp.rglob("*")):
        if p.is_file():
            rp = str(p.relative_to(tmp)); H[rp] = r3sha[rp] if (rp in r3sha and rp not in changed) else sha(p)
    manifest = [{"path": rp, "bytes": (tmp / rp).stat().st_size, "sha256": h} for rp, h in H.items()]
    with (tmp / "file_manifest.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["path", "bytes", "sha256"]); w.writeheader(); w.writerows(manifest)
    r3 = json.loads((R3 / "release.json").read_text())
    rel = dict(r3)
    rel.update({"release_id": RID, "created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "LOCAL_FROZEN_NOT_UPLOADED",
                "derived_from": PARENT, "patch_of": PARENT, "changes": CHANGES,
                "database_uncompressed_sha256": os.environ.get("R31_DB_SHA") or sha(R31_DB), "database_uncompressed_bytes": R31_DB.stat().st_size,
                "validation_status": val["status"], "public_url": None, "doi": None, "file_count": len(manifest) + 2,
                "code_commit": subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, capture_output=True).stdout.strip() or r3.get("code_commit"),
                "working_tree_note": "Patch built with src/build_simpt_power_r3_1.py and src/pilot_lv_geographic_powerflow.py (r3.1)."})
    for k in ("supersedes_local",): rel.pop(k, None)
    rel["supersedes"] = [PARENT]
    (tmp / "release.json").write_text(json.dumps(rel, indent=2))
    H["file_manifest.csv"] = sha(tmp / "file_manifest.csv"); H["release.json"] = sha(tmp / "release.json")
    (tmp / "SHA256SUMS").write_text("".join(f"{h}  {rp}\n" for rp, h in sorted(H.items())))
    tmp.rename(REL); print("frozen:", REL)


def stage_bundle():
    import duckdb
    work = Path.home() / "tmp/bundle_r31"; shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
    with zipfile.ZipFile(R3_BUNDLE) as z: z.extractall(work)
    src = work / "open_bundle_r3"; out = work / "open_bundle_r3_1"; src.rename(out)
    man = json.loads((out / "bundle_manifest.json").read_text())
    con = duckdb.connect(str(R31_DB), read_only=True)
    for t, p, d in [("extension.lv_geo_ptd_results", "lv/lv_geo_ptd_results.parquet", "LV-GEO-A per-PTD lengths and power-flow results (r3.1 solver)"),
                    ("extension.lv_geo_segments", "lv/lv_geo_segments.parquet", "LV-GEO-A geographic LV segments with solved per-branch cable parameters (5.7 M)")]:
        con.execute(f"COPY (select * from {t}) TO '{out / p}' (FORMAT parquet, COMPRESSION zstd)")
        man["files"][p] = {"rows": con.execute(f"select count(*) from {t}").fetchone()[0], "description": d}
    con.close()
    rel = json.loads((ROOT / "data/releases" / RID / "release.json").read_text())
    man.update({"bundle_of": RID, "created_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "release_database_sha256": rel["database_uncompressed_sha256"]})
    (out / "bundle_manifest.json").write_text(json.dumps(man, indent=2, default=str))
    with open(out / "SHA256SUMS", "w") as f:
        for p in sorted(out.rglob("*")):
            if p.is_file() and p.name != "SHA256SUMS": f.write(f"{sha(p)}  {p.relative_to(out)}\n")
    zp = WORK / "open_bundle_r3_1.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_STORED) as z:
        for p in sorted(out.rglob("*")):
            if p.is_file(): z.write(p, p.relative_to(work))
    print(zp, zp.stat().st_size)


if __name__ == "__main__":
    {"db": stage_db, "validate": stage_validate, "compress": stage_compress, "freeze": stage_freeze, "bundle": stage_bundle}[sys.argv[1]]()
