#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from common import PROJECT, RAW, ensure_dirs


def run(script: str, *arguments: str) -> None:
    command = [sys.executable, str(PROJECT / "src" / script), *arguments]
    print("\n$", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the independent Portuguese 60--400 kV candidate network")
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument("--skip-power-flow", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--timestamp")
    parser.add_argument("--skip-osm-download", action="store_true")
    parser.add_argument("--osm-source", choices=("geofabrik", "overpass"), default="geofabrik")
    parser.add_argument("--skip-osm-extract", action="store_true", help="Reuse an existing relation-preserving PBF extraction")
    args = parser.parse_args()
    ensure_dirs()
    if not args.skip_download:
        download_args: list[str] = []
        if args.overwrite:
            download_args.append("--overwrite")
        if args.timestamp:
            download_args += ["--timestamp", args.timestamp]
        if args.skip_osm_download:
            download_args.append("--skip-osm")
        download_args += ["--osm-source", args.osm_source]
        run("download_sources.py", *download_args)
    if args.osm_source == "geofabrik":
        if not (RAW / "osm" / "portugal-latest.osm.pbf").exists():
            raise FileNotFoundError("Missing Geofabrik Portugal PBF; run without --skip-download")
        if not args.skip_osm_extract:
            run("extract_osm_power.py")
    if not args.skip_download:
        run("download_ren_calibration.py", *( ["--overwrite"] if args.overwrite else [] ))
    required = [
        RAW / "eredes" / "rede-at-teste.geojson", RAW / "eredes" / "se-at_2025.geojson",
        RAW / "eredes" / "pc-at_2025.geojson", RAW / "eredes" / "load_snapshot.csv",
        RAW / "osm" / ("portugal_power_osm.json" if args.osm_source == "geofabrik" else "portugal_hv_osm.json"),
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing inputs; run without --skip-download: " + ", ".join(missing))
    for script in ("build_topology.py", "build_transformers.py", "add_demand_generation.py", "assign_parameters.py"):
        run(script)
    run("audit_grid_coverage.py")
    run("audit_circuits_components.py")
    run("build_pandapower.py", *( ["--no-run"] if args.skip_power_flow else [] ))
    run("validate_model.py")
    if not args.skip_power_flow:
        run("export_operating_case.py")
        run("validate_powerflow_scenarios.py")
        run("validate_corridor_rating_corrections.py")
        run("export_web_map_data.py")


if __name__ == "__main__":
    main()
