#!/usr/bin/env python3
"""Import the full E-REDES PTD export without treating suppressed values as zero."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "output/feasibility/ptd_full_export.csv"
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=CSV)
    parser.add_argument("--database", type=Path, default=DB)
    args = parser.parse_args()
    digest = hashlib.sha256(args.csv.read_bytes()).hexdigest()
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS candidate")
        con.execute("""CREATE OR REPLACE TABLE candidate.ptd_public_attributes AS
            SELECT cod_instalacao AS ptd_code,
                   dis_name AS district_name, con_name AS municipality_name,
                   tipo_construtivo AS construction_type,
                   try_cast(potencia_transformacao_kva AS DOUBLE) AS installed_transformer_kva,
                   try_cast(nullif(potencia_contratada,'N/D') AS DOUBLE) AS contracted_power_kva,
                   try_cast(nullif(num_clientes,'<20') AS INTEGER) AS customer_count_exact,
                   num_clientes='<20' AS customer_count_suppressed,
                   try_cast(nullif(potencia_geracao,'N/D') AS DOUBLE) AS connected_generation_kw,
                   potencia_geracao='N/D' AS generation_capacity_suppressed,
                   try_cast(nullif(num_produtores,'<20') AS INTEGER) AS producer_count_exact,
                   num_produtores='<20' AS producer_count_suppressed,
                   histogram AS utilization_histogram_raw,
                   'E_REDES_PTD_PUBLIC_EXPORT' AS evidence_status,
                   ? AS source_sha256
            FROM read_csv(?, delim=';', header=true, all_varchar=true, encoding='utf-8')""",
                    [digest, str(args.csv)])
        row = con.execute("""SELECT count(*),count(DISTINCT ptd_code),
                count(connected_generation_kw),sum(connected_generation_kw),
                count(*) FILTER (WHERE generation_capacity_suppressed),
                count(*) FILTER (WHERE producer_count_suppressed),
                count(contracted_power_kva)
            FROM candidate.ptd_public_attributes""").fetchone()
        unknown = con.execute("""SELECT count(*) FROM candidate.ptd_public_attributes p
            FULL JOIN candidate.ptd_node_candidates c USING(ptd_code)
            WHERE p.ptd_code IS NULL OR c.ptd_code IS NULL""").fetchone()[0]
        capacity_mismatch = con.execute("""SELECT count(*) FROM candidate.ptd_public_attributes p
            JOIN candidate.ptd_node_candidates c USING(ptd_code)
            WHERE abs(p.installed_transformer_kva-c.capacity_kva_public)>1e-6""").fetchone()[0]
    checks = dict(zip(("rows", "unique_ptds", "generation_capacity_numeric_ptds",
                       "numeric_generation_capacity_kw", "generation_capacity_suppressed_ptds",
                       "producer_count_suppressed_ptds", "contracted_power_numeric_ptds"), row))
    checks.update(unmatched_ptd_codes=unknown, transformer_capacity_mismatches=capacity_mismatch)
    errors = []
    if row[0] != row[1] or unknown or capacity_mismatch:
        errors.append("PTD keys or transformer capacities disagree with existing national snapshot")
    if row[2]+row[4] != row[0]:
        errors.append("Generation capacity is neither numeric nor explicitly suppressed")
    report = {"result":"PASS" if not errors else "FAIL", "checks":checks,
              "source_sha256":digest,
              "source_url":"https://e-redes.opendatasoft.com/explore/dataset/postos-transformacao-distribuicao/",
              "scope":"PTD public aggregate attributes; connected generation is not technology-specific",
              "limitations":["N/D generation capacity remains unknown, not zero",
                             "<20 producer/customer counts are suppressed ranges, not exact counts"],
              "errors":errors}
    report_path=args.database.parent/"ptd_public_attributes.validation.json"
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
