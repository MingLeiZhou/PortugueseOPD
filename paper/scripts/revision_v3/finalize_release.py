#!/usr/bin/env python3
"""Create immutable GitHub release assets after the source commit exists."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
VERSION = "SimPT60-2026.09.21-r1"
RELEASE = ROOT / "data" / "releases" / VERSION
BUNDLE = RELEASE / "bundle"
MONTHLY = RELEASE / "assets"
PUBLICATION = RELEASE / "publication_assets"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> None:
    PUBLICATION.mkdir(parents=True, exist_ok=True)
    commit = git("rev-parse", "HEAD")
    parent = git("rev-parse", "HEAD^")
    code = {
        "release": VERSION,
        "commit": commit,
        "parent_commit": parent,
        "branch": git("branch", "--show-current"),
        "commit_timestamp": git("show", "-s", "--format=%cI", "HEAD"),
        "working_tree_note": (
            "Commit identifies the public release/reproduction implementation. "
            "Historical monthly run manifests did not store an original code commit."
        ),
        "core_model_sha256": "57f3be5e9488bfad43e9c3504c5d703885a14457f0bcbde21994dff942225013",
        "generator_table_sha256": "3cc22e06f28c35c3d80e530e898a34808eb3e17c6685029c9d0953063e7326bc",
    }
    (BUNDLE / "CODE_VERSION.json").write_text(
        json.dumps(code, indent=2) + "\n", encoding="utf-8"
    )

    core = PUBLICATION / f"{VERSION}-core.tar.zst"
    tar = subprocess.Popen(
        ["tar", "-cf", "-", "bundle"], cwd=RELEASE, stdout=subprocess.PIPE
    )
    assert tar.stdout is not None
    zstd = subprocess.run(
        ["/opt/homebrew/bin/zstd", "-6", "-T2", "-f", "-o", str(core)],
        stdin=tar.stdout,
        check=True,
    )
    tar.stdout.close()
    if tar.wait() != 0 or zstd.returncode != 0:
        raise RuntimeError("Core archive construction failed")

    shutil.copy2(MONTHLY / "MONTHLY_MANIFEST.json", PUBLICATION / "MONTHLY_MANIFEST.json")
    shutil.copy2(ROOT / "output/pdf/paper_final_edited.pdf", PUBLICATION / "SimPT60-paper.pdf")
    shutil.copy2(ROOT / "paper/revision_v3/RELEASE_README.md", PUBLICATION / "README.md")

    publish_files = [core]
    publish_files.extend(sorted(MONTHLY.glob("*.duckdb.zst")))
    publish_files.extend(
        [
            PUBLICATION / "MONTHLY_MANIFEST.json",
            PUBLICATION / "SimPT60-paper.pdf",
            PUBLICATION / "README.md",
        ]
    )
    asset_records = [
        {"name": path.name, "bytes": path.stat().st_size, "sha256": sha256(path)}
        for path in publish_files
    ]
    release_json = {
        **code,
        "published_at_utc": datetime.now(timezone.utc).isoformat(),
        "model_variants": {
            "CORE-3783": {
                "buses": 3783,
                "line_segments": 4943,
                "transformer_rows": 228,
                "uses": "31,492 monthly cases and original validation",
            },
            "N1-3787": {
                "buses": 3787,
                "line_segments": 4943,
                "transformer_rows": 228,
                "uses": "targeted and annual N-1 corrected snapshots only",
            },
        },
        "minimal_reproduction": [
            "python3.13 -m venv .venv",
            ".venv/bin/pip install -r requirements-lock.txt",
            ".venv/bin/python replay.py",
        ],
        "replay_tolerance": {
            "value": 1e-5,
            "fields": [
                "vm_pu_min",
                "vm_pu_max",
                "maximum_line_loading_percent",
                "model_net_import_mw",
            ],
        },
        "assets": asset_records,
        "redistribution": (
            "Raw third-party downloads are not bundled. Their URLs, sizes and SHA-256 values "
            "remain in provenance and evidence manifests."
        ),
    }
    release_path = PUBLICATION / "release.json"
    release_path.write_text(json.dumps(release_json, indent=2) + "\n", encoding="utf-8")

    checksummed = publish_files + [release_path]
    checksum_path = PUBLICATION / "SHA256SUMS"
    checksum_path.write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in checksummed),
        encoding="utf-8",
    )
    print(json.dumps({"commit": commit, "assets": len(checksummed), "core": asset_records[0]}, indent=2))


if __name__ == "__main__":
    main()
