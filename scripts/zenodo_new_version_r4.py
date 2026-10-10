#!/usr/bin/env python3
"""Create (but do NOT publish) a new Zenodo version of SimPT-Power for r4.

Run on your own machine:   export ZENODO_TOKEN=...   (scope deposit:write; never commit it)
                           python3 scripts/zenodo_new_version_r4.py [--record 23122475] [--code-doi 10.5281/zenodo.NNN]
The draft keeps the unchanged r3.1 files (the 11 hv_core/*.zst, CORE-3787-REN_model.json, MONTHLY_MANIFEST.json,
observations.zip, licences, requirements) and replaces the changed ones with output/zenodo_upload_r4/*.
Title, version and description are moved to r4. Review the draft on zenodo.org and press Publish yourself.
Prints the reserved DOI for the paper.
"""
import argparse, hashlib, json, os, re, sys
from datetime import date
from pathlib import Path
import requests

API = "https://zenodo.org/api"
ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "output/zenodo_upload_r4"
REPLACE = ["README.md", "SHA256SUMS", "file_manifest.csv", "release.json", "figures.zip", "validation.zip",
           "simpt_power.duckdb.zst", "open_bundle_r3.zip", "open_bundle_r3_1.zip", "CHANGELOG_r3.1.md"]
UPLOAD = ["README.md", "SHA256SUMS", "file_manifest.csv", "release.json", "figures.zip", "validation.zip",
          "simpt_power.duckdb.zst", "open_bundle_r4.zip", "CHANGELOG_r4.md"]
VERSION = "2026.10.04-r4"
OLD_CODE_DOI = "10.5281/zenodo.23126542"
NOTE = ("<p><b>r4.</b> The LV/MV split of each substation load is now time-varying: the LV share follows the E-REDES LV "
        "standard load profiles with a heating/cooling degree-day correction and is calibrated (three parameters) to the national "
        "LV share of LV+MV consumption; leave-one-month-out error 0.038 (previous fixed share 0.143). All load-dependent layers "
        "were rebuilt (root-peak timestamps, MV and PTD transformer designs, LV feeder designs, protection, short circuits, "
        "operating designs, urban-cable root slices and the geographic LV networks). The HV core and observation files are "
        "identical to r3.1. See CHANGELOG_r4.md.</p>")


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""): h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--record", default="23122475"); ap.add_argument("--code-doi")
    a = ap.parse_args()
    tok = os.environ.get("ZENODO_TOKEN") or sys.exit("set ZENODO_TOKEN")
    s = requests.Session(); s.headers["Authorization"] = f"Bearer {tok}"
    for f in UPLOAD:
        if not (UP / f).exists(): sys.exit(f"missing {UP / f}")
    r = s.post(f"{API}/deposit/depositions/{a.record}/actions/newversion"); r.raise_for_status()
    draft = s.get(r.json()["links"]["latest_draft"]).json(); did = draft["id"]
    print("draft deposition:", did, draft["links"]["html"])
    for f in s.get(f"{API}/deposit/depositions/{did}/files").json():
        if f["filename"] in REPLACE:
            s.delete(f"{API}/deposit/depositions/{did}/files/{f['id']}").raise_for_status(); print("removed", f["filename"])
    bucket = draft["links"]["bucket"]
    for name in UPLOAD:
        p = UP / name; print(f"uploading {name} ({p.stat().st_size / 1e6:.1f} MB) ...", flush=True)
        with open(p, "rb") as fh:
            r = s.put(f"{bucket}/{name}", data=fh); r.raise_for_status()
        if r.json().get("checksum", "").split(":")[-1] != md5(p): sys.exit(f"checksum mismatch for {name}")
    md = draft["metadata"]
    md["version"] = VERSION
    md.pop("doi", None)
    md["title"] = re.sub(r"\br3(\.1)?\b", "r4", md["title"])
    desc = re.sub(r"^\s*<p><b>r3\.1[^<]*</b>.*?</p>", "", md.get("description", ""), flags=re.S)
    for x, y in [("open_bundle_r3_1.zip", "open_bundle_r4.zip"), ("open_bundle_r3.zip", "open_bundle_r4.zip"),
                 ("CHANGELOG_r3.1.md", "CHANGELOG_r4.md"), ("2026.10.03-r3.1", VERSION)]:
        desc = desc.replace(x, y)
    md["description"] = NOTE + desc
    if a.code_doi:
        for ri in md.get("related_identifiers", []):
            if ri.get("identifier", "").endswith(OLD_CODE_DOI): ri["identifier"] = a.code_doi
    md["publication_date"] = date.today().isoformat()
    s.put(f"{API}/deposit/depositions/{did}", data=json.dumps({"metadata": md}), headers={"Content-Type": "application/json"}).raise_for_status()
    d = s.get(f"{API}/deposit/depositions/{did}").json()
    print("title:", d["metadata"]["title"])
    print("related:", d["metadata"].get("related_identifiers"))
    print("files in draft:", sorted(f["filename"] for f in d["files"]))
    left = [x for x in ("r3.1", "r3_1") if x in desc]   # older text kept below the r4 note
    if left: print("NOTE: description still mentions", left, "- check it on the draft page")
    print("reserved DOI:", d["metadata"].get("prereserve_doi", {}).get("doi"))
    print("NOT published. Review and publish at:", d["links"]["html"])


if __name__ == "__main__":
    main()
