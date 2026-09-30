#!/usr/bin/env python3
"""Assemble read-only source evidence and generated distribution layers in DuckDB.

The database is a research staging artifact. Candidate OSM connectivity and
synthetic equivalent branches are deliberately kept in distinct schemas.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data/releases/SimPT60-2026.09.21-r1/bundle/inputs.duckdb"
TOPOLOGY = ROOT / "output/distribution_topology"
EQUIVALENT = ROOT / "output/feasibility/all_voltage_national"
OUTPUT = ROOT / "output/all_voltage/all_voltage_staging.duckdb"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=BASE)
    parser.add_argument("--topology", type=Path, default=TOPOLOGY)
    parser.add_argument("--equivalent", type=Path, default=EQUIVALENT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    candidate_files = {
        "osm_nodes": args.topology / "osm_distribution_nodes.csv",
        "osm_segments": args.topology / "osm_distribution_segments.csv",
        "osm_components": args.topology / "osm_components.csv",
        "station_sources": args.topology / "station_mv_candidates.csv",
        "ptd_node_candidates": args.topology / "ptd_osm_candidates.csv",
    }
    equivalent_files = {
        "buses": args.equivalent / "all_voltage_buses.csv",
        "branches": args.equivalent / "all_voltage_branches.csv",
        "ptd_connections": args.equivalent / "ptd_connections.csv",
        "lv_loads": args.equivalent / "lv_loads.csv",
    }
    for path in [args.base, *candidate_files.values(), *equivalent_files.values()]:
        if not path.exists():
            parser.error(f"Required source absent: {path}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(args.output)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS candidate")
        con.execute("CREATE SCHEMA IF NOT EXISTS equivalent")
        con.execute("CREATE SCHEMA IF NOT EXISTS audit")
        quoted_base = str(args.base).replace("'", "''")
        con.execute(f"ATTACH '{quoted_base}' AS frozen (READ_ONLY)")
        for table, path in candidate_files.items():
            con.execute(f"CREATE OR REPLACE TABLE candidate.{table} AS SELECT * FROM read_csv_auto(?, header=true)", [str(path)])
        for table, path in equivalent_files.items():
            con.execute(f"CREATE OR REPLACE TABLE equivalent.{table} AS SELECT * FROM read_csv_auto(?, header=true)", [str(path)])
        con.execute("CREATE OR REPLACE TABLE equivalent.base_buses AS SELECT * FROM frozen.grid.buses")
        con.execute("CREATE OR REPLACE TABLE equivalent.base_lines AS SELECT * FROM frozen.grid.lines")
        con.execute("CREATE OR REPLACE TABLE equivalent.base_transformers AS SELECT * FROM frozen.grid.transformers")
        con.execute("CREATE OR REPLACE TABLE equivalent.base_generators AS SELECT * FROM frozen.grid.generators")
        con.execute("CREATE OR REPLACE TABLE audit.source_files (layer VARCHAR, table_name VARCHAR, path VARCHAR, sha256 VARCHAR)")
        source_rows = [(layer, name, str(path), sha256(path)) for layer, files in
                       (("candidate", candidate_files), ("equivalent", equivalent_files)) for name, path in files.items()]
        con.executemany("INSERT INTO audit.source_files VALUES (?, ?, ?, ?)", source_rows)
        checks = {
            "candidate_node_count": "SELECT count(*) FROM candidate.osm_nodes",
            "candidate_segment_count": "SELECT count(*) FROM candidate.osm_segments",
            "candidate_orphan_segments": """SELECT count(*) FROM candidate.osm_segments s
                LEFT JOIN candidate.osm_nodes a ON s.from_node=a.node_id
                LEFT JOIN candidate.osm_nodes b ON s.to_node=b.node_id
                WHERE a.node_id IS NULL OR b.node_id IS NULL""",
            "equivalent_bus_count": "SELECT count(*) FROM equivalent.buses",
            "equivalent_branch_count": "SELECT count(*) FROM equivalent.branches",
            "equivalent_orphan_branches": """SELECT count(*) FROM equivalent.branches e
                LEFT JOIN equivalent.buses a ON e.from_bus=a.bus_id
                LEFT JOIN equivalent.buses b ON e.to_bus=b.bus_id
                WHERE a.bus_id IS NULL OR b.bus_id IS NULL""",
            "public_ptd_count": "SELECT count(*) FROM equivalent.ptd_connections",
            "ptds_with_osm_candidate": """SELECT count(*) FROM equivalent.ptd_connections p
                INNER JOIN candidate.ptd_node_candidates c ON p.ptd_code=c.ptd_code""",
            "station_source_count": "SELECT count(*) FROM candidate.station_sources",
            "station_source_fault_range_count": """SELECT count(*) FROM candidate.station_sources
                WHERE thevenin_z_magnitude_min_ohm IS NOT NULL
                  AND thevenin_z_magnitude_max_ohm IS NOT NULL""",
        }
        values = {name: int(con.execute(query).fetchone()[0]) for name, query in checks.items()}
        errors = []
        if values["candidate_orphan_segments"] or values["equivalent_orphan_branches"]:
            errors.append("Orphan graph references")
        if values["ptds_with_osm_candidate"] != values["public_ptd_count"]:
            errors.append("PTD lacks OSM candidate")
        con.execute("CREATE OR REPLACE TABLE audit.validation (check_name VARCHAR, value BIGINT)")
        con.executemany("INSERT INTO audit.validation VALUES (?, ?)", list(values.items()))
        con.execute("DETACH frozen")
    report = {"result": "PASS" if not errors else "FAIL", "database": str(args.output),
              "checks": values, "errors": errors,
              "scope": "Staging database with separate OSM candidate and synthetic equivalent layers; not yet a solved all-voltage network"}
    report_path = args.output.with_suffix(".validation.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
