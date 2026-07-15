#!/usr/bin/env python3
"""Check whether topology-critical E-REDES APIs remain publicly retrievable.

Some RND layers deliberately return 404 unless the public API key embedded in
the RND page widget is supplied. The audit records unkeyed and keyed results
separately and never interprets an unkeyed 404 as source loss.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import config
from utils import discover_page_datasets, records_v2_url, request_url


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "metadata" / "reproduction_source_manifest.json"
OUTPUT = ROOT / "data" / "metadata" / "source_reacquisition_audit.json"


def status(url: str, api_key: str | None = None) -> int | str:
    try:
        return int(request_url(url, api_key=api_key).status_code)
    except Exception as exc:  # preserve failure class without leaking keys or paths
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
    page_sources = discover_page_datasets()
    rows = []
    for source in manifest["source_rows"]:
        if not source.get("topology_critical"):
            continue
        api_key = page_sources.get(source["dataset_id"], {}).get("api_key")
        if args.observed_http_status is not None:
            keyed_checks = {
                field: args.observed_http_status
                for field in ("metadata_url", "records_url", "csv_export_url", "geojson_export_url")
            }
        else:
            keyed_checks = {
                "metadata_url": status(source["metadata_url"], api_key),
                "records_url": status(records_v2_url(source["dataset_id"]), api_key),
                "csv_export_url": status(source["csv_export_url"], api_key),
                "geojson_export_url": status(source["geojson_export_url"], api_key),
            }
        rows.append(
            {
                "dataset_id": source["dataset_id"],
                "snapshot_records_count": source["records_count"],
                "snapshot_checked_at": source["checked_at"],
                "snapshot_local_file_available": bool(source.get("local_snapshot_count")),
                "public_entry_page": config.PAGE_URLS["rnd"],
                "public_widget_key_discovered": bool(api_key),
                "metadata_status_without_key": status(source["metadata_url"]),
                "current_keyed_http_status": keyed_checks,
            }
        )
    current_api_retrievable = all(
        row["public_widget_key_discovered"]
        and all(value == 200 for value in row["current_keyed_http_status"].values())
        for row in rows
    )
    report = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "status_observation_mode": "external_keyed_override" if args.observed_http_status is not None else "public_page_key_discovery_and_direct_request",
        "status": "CURRENT_API_RETRIEVABLE" if current_api_retrievable else "CURRENT_API_RETRIEVAL_CHECK_FAILED",
        "sources": rows,
        "claim_boundary": (
            "The workflow can retrieve source inputs through the public E-REDES API using widget keys "
            "discoverable from the official RND page."
        ),
    }
    OUTPUT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
