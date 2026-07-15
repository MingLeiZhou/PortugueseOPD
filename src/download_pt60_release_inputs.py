#!/usr/bin/env python3
"""Download the bounded E-REDES inputs required for a PT60 release rebuild."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from utils import discover_page_datasets, export_url, request_url


DATASETS = {
    "rede-at-teste": ("csv", "geojson"),
    "se-at_2025": ("csv", "geojson"),
    "pc-at_2025": ("csv", "geojson"),
    "se-mt_2025": ("csv", "geojson"),
    "pc-mt_2025": ("csv", "geojson"),
    "caracteristicas-da-rede": ("csv",),
    "carga-na-subestacao": ("csv",),
    "capacidade-rececao-rnd": ("csv",),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    discovered = discover_page_datasets()
    checked_at = datetime.now(timezone.utc).isoformat()
    records = []
    for dataset_id, formats in DATASETS.items():
        api_key = discovered.get(dataset_id, {}).get("api_key")
        for fmt in formats:
            path = args.output_dir / f"{dataset_id}.{fmt}"
            if args.overwrite or not path.exists():
                response = request_url(export_url(dataset_id, fmt), api_key=api_key, stream=True)
                response.raise_for_status()
                temporary = path.with_suffix(path.suffix + ".part")
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            handle.write(chunk)
                temporary.replace(path)
            records.append(
                {
                    "dataset_id": dataset_id,
                    "format": fmt,
                    "path": str(path),
                    "bytes": path.stat().st_size,
                    "sha256": sha256(path),
                    "public_widget_key_discovered": bool(api_key),
                }
            )
    manifest = {
        "checked_at": checked_at,
        "output_dir": str(args.output_dir),
        "records": records,
        "scope": "Bounded source inputs required for topology reconstruction and electrical-readiness metadata; not the full 13-source contextual inventory.",
    }
    manifest_path = args.manifest or args.output_dir / "pt60_release_input_manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
