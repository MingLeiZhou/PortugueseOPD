#!/usr/bin/env python3
"""Create (but do NOT publish) a new Zenodo version of SimPT-Power r3 for r3.1.

Run on your own machine:   export ZENODO_TOKEN=...   (scope deposit:write; never commit it)
                           python3 scripts/zenodo_new_version_r3_1.py [--record 23067065]
The draft keeps all r3 files that are unchanged (the 11 hv_core/*.zst, CORE-3787-REN_model.json, MONTHLY_MANIFEST.json,
observations.zip, licences, requirements) and replaces the changed ones with output/zenodo_upload_r3_1/*.
Review the draft on zenodo.org and press Publish yourself. Prints the reserved DOI for the paper.
"""
import argparse, hashlib, json, os, sys
from pathlib import Path
import requests

API = "https://zenodo.org/api"
ROOT = Path(__file__).resolve().parents[1]
UP = ROOT / "output/zenodo_upload_r3_1"
REPLACE = ["README.md", "SHA256SUMS", "file_manifest.csv", "release.json", "figures.zip", "validation.zip",
           "simpt_power.duckdb.zst", "open_bundle_r3.zip"]   # removed from the inherited file list
UPLOAD = ["README.md", "SHA256SUMS", "file_manifest.csv", "release.json", "figures.zip", "validation.zip",
          "simpt_power.duckdb.zst", "open_bundle_r3_1.zip", "CHANGELOG_r3.1.md"]
VERSION = "2026.10.03-r3.1"
NOTE = ("<p><b>r3.1 (patch of r3).</b> Only the geographic LV layer (LV-GEO-A/B) changes: the PTD input is the station-derived "
        "net load (r3 subtracted PV a second time); each branch uses the cable of its own class; the neutral resistance follows "
        "the phase/neutral cross-section ratio. LV-GEO-A: 205 PTDs below 0.9 p.u. (r3: 111), 38 overloaded (25), 14 not converged (7). "
        "All other tables and the HV core are byte-identical to r3. See CHANGELOG_r3.1.md.</p>")


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(8 << 20), b""): h.update(b)
    return h.hexdigest()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--record", default="23067065"); a = ap.parse_args()
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
    md["description"] = NOTE + md.get("description", "")
    from datetime import date
    md["publication_date"] = date.today().isoformat()
    s.put(f"{API}/deposit/depositions/{did}", data=json.dumps({"metadata": md}), headers={"Content-Type": "application/json"}).raise_for_status()
    d = s.get(f"{API}/deposit/depositions/{did}").json()
    print("files in draft:", sorted(f["filename"] for f in d["files"]))
    print("reserved DOI:", d["metadata"].get("prereserve_doi", {}).get("doi"))
    print("NOT published. Review and publish at:", d["links"]["html"])


if __name__ == "__main__":
    main()
