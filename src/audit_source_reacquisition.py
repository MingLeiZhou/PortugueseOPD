#!/usr/bin/env python3
"""Check whether the frozen topology-critical E-REDES URLs remain retrievable."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "metadata" / "reproduction_source_manifest.json"
OUTPUT = ROOT / "data" / "metadata" / "source_reacquisition_audit.json"


def status(url: str) -> int | str:
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "PT60-Candidate-reacquisition-audit/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return int(exc.code)
    except Exception as exc:  # preserve the failure class without leaking local paths
        curl = shutil.which("curl")
        if curl:
            completed = subprocess.run(
                [curl, "-L", "-sS", "-o", "/dev/null", "-w", "%{http_code}", url],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if completed.stdout.strip().isdigit():
                return int(completed.stdout.strip())
        return f"ERROR:{type(exc).__name__}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--observed-http-status",
        type=int,
        help="Record a status independently observed for all listed URLs (for restricted-network audit runners).",
    )
    args = parser.parse_args()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    rows = []
    for source in manifest["source_rows"]:
        if not source.get("topology_critical"):
            continue
        checks = {
            field: args.observed_http_status if args.observed_http_status is not None else status(source[field])
            for field in ("metadata_url", "portal_url", "csv_export_url", "geojson_export_url")
        }
        rows.append(
            {
                "dataset_id": source["dataset_id"],
                "snapshot_records_count": source["records_count"],
                "snapshot_checked_at": source["checked_at"],
                "snapshot_local_file_available": bool(source.get("local_snapshot_count")),
                "current_http_status": checks,
            }
        )
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status_observation_mode": "external_curl_override" if args.observed_http_status is not None else "direct_request_with_curl_fallback",
        "status": "RAW_REACQUISITION_UNAVAILABLE" if any(404 in row["current_http_status"].values() for row in rows) else "CHECK_URL_RESULTS",
        "sources": rows,
        "claim_boundary": "The public workflow is reproducible from frozen derived inputs; raw/API-to-release byte reproduction is not claimed.",
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
