#!/usr/bin/env python3
"""Build the PT60 v2.0.0 Portuguese >=60 kV benchmark release."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import shutil
import tarfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "portuguese_hv_network"
OUTPUTS = PROJECT / "outputs"
VERSION = "v2.0.0"
RELEASE_NAME = f"PT60-{VERSION}"
RELEASE_DIR = ROOT / "data" / "releases" / RELEASE_NAME
ARCHIVE_PATH = RELEASE_DIR.parent / f"{RELEASE_NAME}.tar.gz"


FILES = {
    "config/model_config.json": PROJECT / "config" / "model_config.json",
    "config/sources.json": PROJECT / "config" / "sources.json",
    "provenance/source_manifest.json": PROJECT / "data" / "manifests" / "source_manifest.json",
    "topology/buses.csv": OUTPUTS / "tables" / "buses.csv",
    "topology/facilities.csv": OUTPUTS / "tables" / "facilities.csv",
    "topology/lines.csv": OUTPUTS / "tables" / "lines.csv",
    "topology/transformers.csv": OUTPUTS / "tables" / "transformers_topology.csv",
    "scenario/loads.csv": OUTPUTS / "tables" / "loads.csv",
    "scenario/generators.csv": OUTPUTS / "tables" / "generators.csv",
    "scenario/boundaries.csv": OUTPUTS / "tables" / "scenario_boundaries.csv",
    "scenario/operating_quantity_evidence.csv": OUTPUTS / "power_flow" / "operating_quantity_evidence.csv",
    "model/pt60_candidate.json": OUTPUTS / "model" / "portuguese_hv_candidate.json",
    "model/pt60_candidate_solved.json": OUTPUTS / "model" / "portuguese_hv_candidate_solved.json",
    "power_flow/summary.json": OUTPUTS / "power_flow" / "power_flow_summary.json",
    "power_flow/full_scale_operating_case.json": OUTPUTS / "power_flow" / "full_scale_operating_case.json",
    "power_flow/scaling_sweep.csv": OUTPUTS / "power_flow" / "power_flow_scaling_sweep.csv",
    "power_flow/scaling_sweep_summary.json": OUTPUTS / "power_flow" / "power_flow_scaling_sweep_summary.json",
    "power_flow/line_results.csv": OUTPUTS / "power_flow" / "line_operating_results.csv",
    "power_flow/transformer_results.csv": OUTPUTS / "power_flow" / "transformer_operating_results.csv",
    "power_flow/load_operating_points.csv": OUTPUTS / "power_flow" / "load_operating_points.csv",
    "power_flow/generator_operating_points.csv": OUTPUTS / "power_flow" / "generator_operating_points.csv",
    "validation/checks.csv": OUTPUTS / "validation" / "validation_checks.csv",
    "validation/summary.json": OUTPUTS / "validation" / "validation_report.json",
    "validation/evidence_audit_summary.json": OUTPUTS / "validation" / "network_evidence_audit_summary.json",
    "validation/component_audit.csv": OUTPUTS / "validation" / "network_component_audit.csv",
    "validation/voltage_coverage.csv": OUTPUTS / "validation" / "voltage_coverage_reconciliation.csv",
    "validation/transformer_capacity.csv": OUTPUTS / "validation" / "transformer_capacity_reconciliation.csv",
    "validation/corridor_rating_corrections.csv": OUTPUTS / "validation" / "corridor_rating_correction_results.csv",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_count(path: Path) -> int | None:
    if path.suffix == ".csv":
        with path.open(encoding="utf-8", errors="replace", newline="") as handle:
            return max(sum(1 for _ in csv.reader(handle)) - 1, 0)
    return None


def scrub_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: scrub_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [scrub_json(item) for item in value]
    if isinstance(value, str):
        replacements = {
            str(ROOT): "<PROJECT_ROOT>",
            str(PROJECT): "<PROJECT_ROOT>/portuguese_hv_network",
            "/private/tmp": "<TEMP_DIR>",
            "/tmp": "<TEMP_DIR>",
        }
        for old, new in replacements.items():
            value = value.replace(old, new)
    return value


def copy_release_file(destination: str, source: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(f"Missing required release input: {source}")
    target = RELEASE_DIR / destination
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix == ".json":
        value = json.loads(source.read_text(encoding="utf-8"))
        target.write_text(json.dumps(scrub_json(value), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        shutil.copy2(source, target)


def write_support_files() -> None:
    readme = f"""# PT60 {VERSION}

PT60 is a public-data-informed benchmark dataset for the Portuguese high-voltage
network at nominal voltages of 60, 130, 150, 220 and 400 kV. Version 2 expands
the original 60 kV candidate topology into a national >=60 kV topology and a
reproducible AC power-flow benchmark.

## Contents

- `topology/`: buses, facilities, lines and transformers with evidence status.
- `scenario/`: synchronized or allocated load, generation and boundary inputs.
- `model/`: unsolved and solved pandapower JSON networks.
- `power_flow/`: full-scale results and 50--120% sensitivity sweep.
- `validation/`: structural, evidence, capacity and operating-screen checks.
- `provenance/`: source URLs, hashes, sizes and roles; raw downloads are excluded.

The validated full-scale case has 3,783 buses, 4,943 lines, 228 transformers and
10,268.8 MW load. It converges with a voltage range of 0.9293--1.0117 pu,
maximum line loading of 94.94%, and maximum transformer loading of 83.44%.

## Claim boundary

PT60 is a research benchmark, not an operator state-estimator export. Line
geometry is public-source derived, while most impedances, reactive demand,
generator dispatch, transformer impedance/taps and switching states remain
partly source-backed or explicit engineering assumptions. Results must not be
presented as verified Portuguese operating conditions.

## Reproduction

From the repository root:

```bash
python portuguese_hv_network/src/run_pipeline.py
python src/build_pt60_hv_release.py
```

See `DATA_LICENSE.md`, `ATTRIBUTION.md`, `manifest.json` and
`checksums.sha256` before redistribution.
"""
    citation = f"""cff-version: 1.2.0
message: "If you use PT60, cite the dataset and the PortugueseOPD software repository."
title: "PT60: Portuguese public-record high-voltage topology and AC power-flow benchmark dataset"
type: dataset
authors:
  - name: "PortugueseOPD contributors"
version: {VERSION.removeprefix('v')}
date-released: 2026-09-03
repository-code: "https://github.com/MingLeiZhou/PortugueseOPD"
abstract: >-
  A public-data-informed Portuguese high-voltage network benchmark covering
  nominal voltages from 60 to 400 kV, with provenance-labelled topology,
  scenario inputs, pandapower models, AC power-flow results and validation.
"""
    license_text = """# Data license and reuse boundary

The release is a transformed research dataset assembled from multiple public
sources. E-REDES-derived records are redistributed under the portal's CC BY 4.0
terms. OpenStreetMap-derived content is subject to the Open Database License
(ODbL); consult https://www.openstreetmap.org/copyright. REN, ERSE, Eurostat
GISCO and Geofabrik materials retain their source terms. This document does not
replace the licenses or terms published by those providers.

The repository MIT license applies only to software. Users must preserve source
attribution, modification notices, provenance fields and this claim boundary.
The dataset is not operator validated and is not suitable for operational
switching, protection, contingency, emergency or infrastructure-targeting use.
"""
    attribution = """# Attribution

- E-REDES - Distribuição de Eletricidade, E-REDES Open Data Portal.
- OpenStreetMap contributors; Portugal extract supplied by Geofabrik.
- REN Data Hub and public Portuguese transmission-network publications.
- ERSE / PDIRD-E public planning documents.
- Eurostat GISCO administrative boundary data.

PT60 transforms and combines these sources; it is not an official publication
of any source provider or a representation of the live Portuguese grid.
"""
    changelog = """# Changelog

## v2.0.0 - 2026-09-03

- Expanded the PT60 scope from a conservative 60 kV candidate layer to the
  Portuguese >=60 kV network (60, 130, 150, 220 and 400 kV).
- Added relation-aware OSM transmission topology and multi-voltage transformers.
- Added synchronized/allocated demand, generation and boundary scenario tables.
- Added pandapower networks, a full-scale AC power-flow case and scaling sweep.
- Preserved evidence status and explicit engineering-assumption labels.

The former PT60-Candidate v1.0.2 release remains available through its deposited
DOI and Git history; v2.0.0 is not schema-compatible with v1.x.
"""
    for name, content in {
        "README.md": readme,
        "CITATION.cff": citation,
        "DATA_LICENSE.md": license_text,
        "ATTRIBUTION.md": attribution,
        "CHANGELOG.md": changelog,
    }.items():
        (RELEASE_DIR / name).write_text(content, encoding="utf-8")
    shutil.copy2(ROOT / "LICENSE", RELEASE_DIR / "LICENSE-CODE-MIT")


def validate_and_manifest() -> None:
    summary = json.loads((RELEASE_DIR / "validation" / "summary.json").read_text(encoding="utf-8"))
    power_flow = json.loads((RELEASE_DIR / "power_flow" / "summary.json").read_text(encoding="utf-8"))
    checks = {
        "source_validation_passed": summary.get("overall_status") == "PASS" and summary.get("checks_passed") == summary.get("checks_total"),
        "expected_buses": summary.get("network", {}).get("buses") == 3783,
        "expected_lines": summary.get("network", {}).get("lines") == 4943,
        "expected_transformers": summary.get("network", {}).get("transformers") == 228,
        "full_scale_converged": power_flow.get("converged") is True and power_flow.get("scenario_scaling") == 1.0,
    }
    if not all(checks.values()):
        raise RuntimeError(f"Release validation failed: {checks}")

    records = []
    for path in sorted(RELEASE_DIR.rglob("*")):
        if not path.is_file() or path.name in {"manifest.json", "checksums.sha256", "archive_validation_summary.json"}:
            continue
        rel = str(path.relative_to(RELEASE_DIR))
        records.append({"path": rel, "bytes": path.stat().st_size, "sha256": sha256(path), "rows": row_count(path)})
    manifest = {
        "dataset": "PT60",
        "version": VERSION,
        "title": "Portuguese public-record high-voltage topology and AC power-flow benchmark dataset",
        "generated_at_utc": summary.get("generated_at"),
        "voltage_levels_kv": [60, 130, 150, 220, 400],
        "records": records,
    }
    (RELEASE_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    archive_summary = {
        "status": "PASS",
        "checks": checks,
        "file_count": len(records) + 3,
        "manifest_record_count": len(records),
        "release_directory": RELEASE_NAME,
    }
    (RELEASE_DIR / "archive_validation_summary.json").write_text(
        json.dumps(archive_summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    checksum_paths = [path for path in sorted(RELEASE_DIR.rglob("*")) if path.is_file() and path.name != "checksums.sha256"]
    checksum_text = "".join(f"{sha256(path)}  {path.relative_to(RELEASE_DIR)}\n" for path in checksum_paths)
    (RELEASE_DIR / "checksums.sha256").write_text(checksum_text, encoding="utf-8")


def build_archive() -> None:
    with ARCHIVE_PATH.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as archive:
                for path in sorted(RELEASE_DIR.rglob("*")):
                    info = archive.gettarinfo(str(path), arcname=str(Path(RELEASE_NAME) / path.relative_to(RELEASE_DIR)))
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = 0
                    if path.is_file():
                        with path.open("rb") as handle:
                            archive.addfile(info, handle)
                    else:
                        archive.addfile(info)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-archive", action="store_true")
    args = parser.parse_args()
    if RELEASE_DIR.exists():
        shutil.rmtree(RELEASE_DIR)
    RELEASE_DIR.mkdir(parents=True)
    for destination, source in FILES.items():
        copy_release_file(destination, source)
    write_support_files()
    validate_and_manifest()
    if not args.no_archive:
        build_archive()
    print(json.dumps({"release": str(RELEASE_DIR), "archive": None if args.no_archive else str(ARCHIVE_PATH)}, indent=2))


if __name__ == "__main__":
    main()
