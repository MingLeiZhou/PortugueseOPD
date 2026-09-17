#!/usr/bin/env python3
"""Build query-ready E-REDES AT line-inventory tables in SimPT60 DuckDB.

The source table remains immutable in ``raw_eredes``.  This script creates a
curated reference layer with stable identifiers, normalized units, validation
flags and model-line links.  It intentionally does not infer parallel circuit
counts from conductor or segment counts.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb


SOURCE_ID = "EREDES_PDIRD_E_2020_ANNEX_B_AT_2025"
SOURCE_URL = "https://www.erse.pt/media/340hrot0/proposta-pdird-e-2020_anexo_b.pdf"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Existing SimPT60 DuckDB")
    parser.add_argument("--dry-run", action="store_true", help="Validate all SQL and roll back")
    args = parser.parse_args()

    database = args.database.expanduser().resolve()
    if not database.exists():
        raise FileNotFoundError(database)

    con = duckdb.connect(str(database), read_only=False)
    try:
        con.execute("BEGIN TRANSACTION")
        source_count = con.execute("SELECT count(*) FROM raw_eredes.pdird_at_circuits").fetchone()[0]
        if source_count == 0:
            raise ValueError("raw_eredes.pdird_at_circuits is empty")
        duplicate_count = con.execute(
            """SELECT count(*) FROM (
                   SELECT designation FROM raw_eredes.pdird_at_circuits
                   GROUP BY designation HAVING count(*) > 1
               )"""
        ).fetchone()[0]
        if duplicate_count:
            raise ValueError(f"Source contains {duplicate_count} duplicate designations")

        con.execute("CREATE SCHEMA IF NOT EXISTS reference")
        con.execute("CREATE SCHEMA IF NOT EXISTS provenance")
        con.execute("DROP TABLE IF EXISTS reference.eredes_at_line_inventory")
        con.execute(
            """
            CREATE TABLE reference.eredes_at_line_inventory AS
            SELECT
                'EREDES:PDIRD2025:' || upper(md5(trim(designation))) AS inventory_id,
                ?::VARCHAR AS source_id,
                DATE '2025-12-31' AS effective_date,
                60::SMALLINT AS voltage_kv,
                trim(designation) AS designation,
                nullif(regexp_extract(trim(designation), '^LN60\\s+([0-9]{4}(?:\\s+0[12])?)', 1), '') AS line_reference,
                trim(line_name) AS line_name,
                trim(line_name_normalized) AS line_name_normalized,
                segment_count::INTEGER AS conductor_section_count,
                total_length_km::DOUBLE AS total_length_km,
                minimum_nominal_winter_a::DOUBLE / 1000.0 AS winter_limit_ka,
                minimum_nominal_summer_a::DOUBLE / 1000.0 AS summer_limit_ka,
                sqrt(3.0) * 60.0 * minimum_nominal_winter_a / 1000.0 AS winter_limit_mva,
                sqrt(3.0) * 60.0 * minimum_nominal_summer_a / 1000.0 AS summer_limit_mva,
                conductor_count::INTEGER AS distinct_conductor_configuration_count,
                asset_type_count::INTEGER AS distinct_asset_type_count,
                first_pdf_page::INTEGER AS source_first_pdf_page,
                last_pdf_page::INTEGER AS source_last_pdf_page,
                CASE
                    WHEN total_length_km <= 0 THEN 'INVALID_LENGTH'
                    WHEN minimum_nominal_winter_a <= 0 OR minimum_nominal_summer_a <= 0 THEN 'INVALID_RATING'
                    WHEN minimum_nominal_winter_a < minimum_nominal_summer_a THEN 'REVIEW_SEASONAL_ORDER'
                    ELSE 'VALID'
                END AS quality_status,
                false AS parallel_circuit_count_inferred,
                'Section and conductor counts describe series construction details; they are not treated as parallel circuit counts.' AS interpretation_note,
                ?::VARCHAR AS source_url
            FROM raw_eredes.pdird_at_circuits
            ORDER BY designation
            """,
            [SOURCE_ID, SOURCE_URL],
        )

        con.execute("DROP TABLE IF EXISTS reference.eredes_at_line_model_links")
        con.execute(
            """
            CREATE TABLE reference.eredes_at_line_model_links AS
            WITH
            inventory_refs AS (
                SELECT *, count(*) OVER (PARTITION BY line_reference) AS reference_count
                FROM reference.eredes_at_line_inventory
                WHERE line_reference IS NOT NULL
            ),
            model_code_list AS (
                SELECT DISTINCT name AS eredes_map_code,
                       regexp_extract(name, 'L5([0-9]{4})', 1) AS line_reference
                FROM grid.lines
                WHERE source='E-REDES' AND voltage_kv=60
                  AND regexp_extract(name, 'L5([0-9]{4})', 1) <> ''
            ),
            model_codes AS (
                SELECT *,
                       count(*) OVER (
                           PARTITION BY line_reference
                       ) AS code_count
                FROM model_code_list
            ),
            direct_matches AS (
                SELECT
                    i.inventory_id, l.model_id, l.line_id,
                    l.name AS eredes_map_code, l.from_bus, l.to_bus,
                    l.length_km AS model_segment_length_km,
                    l.max_i_ka AS model_limit_ka, l.parallel AS model_parallel,
                    l.contingency_circuit_id,
                    'REVIEWED_PARAMETER_MATCH_DESIGNATION' AS match_method
                FROM reference.eredes_at_line_inventory i
                JOIN grid.lines l ON trim(l.parameter_match_designation)=i.designation
            ),
            unique_reference_matches AS (
                SELECT
                    i.inventory_id, l.model_id, l.line_id,
                    l.name AS eredes_map_code, l.from_bus, l.to_bus,
                    l.length_km AS model_segment_length_km,
                    l.max_i_ka AS model_limit_ka, l.parallel AS model_parallel,
                    l.contingency_circuit_id,
                    'EXACT_UNIQUE_LINE_REFERENCE' AS match_method
                FROM inventory_refs i
                JOIN model_codes c USING (line_reference)
                JOIN grid.lines l ON l.name=c.eredes_map_code
                WHERE i.reference_count=1 AND c.code_count=1
            )
            SELECT * FROM direct_matches
            UNION ALL
            SELECT r.* FROM unique_reference_matches r
            WHERE NOT EXISTS (
                SELECT 1 FROM direct_matches d
                WHERE d.inventory_id=r.inventory_id AND d.line_id=r.line_id
            )
            ORDER BY inventory_id, line_id
            """
        )

        con.execute("DROP TABLE IF EXISTS reference.eredes_at_line_link_audit")
        con.execute(
            """
            CREATE TABLE reference.eredes_at_line_link_audit AS
            WITH
            inventory_ref_counts AS (
                SELECT line_reference, count(*) AS inventory_count
                FROM reference.eredes_at_line_inventory
                WHERE line_reference IS NOT NULL GROUP BY line_reference
            ),
            model_ref_counts AS (
                SELECT regexp_extract(name, 'L5([0-9]{4})', 1) AS line_reference,
                       count(DISTINCT name) AS model_code_count
                FROM grid.lines
                WHERE source='E-REDES' AND voltage_kv=60
                  AND regexp_extract(name, 'L5([0-9]{4})', 1) <> ''
                GROUP BY 1
            ),
            linked AS (
                SELECT inventory_id,
                       bool_or(match_method='REVIEWED_PARAMETER_MATCH_DESIGNATION') AS reviewed_match,
                       bool_or(match_method='EXACT_UNIQUE_LINE_REFERENCE') AS unique_reference_match,
                       count(DISTINCT line_id) AS linked_model_segment_count
                FROM reference.eredes_at_line_model_links GROUP BY inventory_id
            )
            SELECT
                i.inventory_id, i.designation, i.line_reference,
                coalesce(r.inventory_count, 0) AS inventory_reference_count,
                coalesce(m.model_code_count, 0) AS model_code_count,
                coalesce(l.linked_model_segment_count, 0) AS linked_model_segment_count,
                CASE
                    WHEN coalesce(l.reviewed_match, false) THEN 'LINKED_REVIEWED_PATH'
                    WHEN coalesce(l.unique_reference_match, false) THEN 'LINKED_UNIQUE_REFERENCE'
                    WHEN i.line_reference IS NULL THEN 'UNMATCHED_NO_NUMERIC_REFERENCE'
                    WHEN m.line_reference IS NULL THEN 'UNMATCHED_REFERENCE_ABSENT_FROM_MODEL'
                    WHEN r.inventory_count > 1 OR m.model_code_count > 1 THEN 'UNMATCHED_AMBIGUOUS_REFERENCE'
                    ELSE 'UNMATCHED_REQUIRES_REVIEW'
                END AS link_status
            FROM reference.eredes_at_line_inventory i
            LEFT JOIN inventory_ref_counts r USING (line_reference)
            LEFT JOIN model_ref_counts m USING (line_reference)
            LEFT JOIN linked l USING (inventory_id)
            ORDER BY i.designation
            """
        )

        con.execute("DROP TABLE IF EXISTS reference.eredes_at_inventory_quality")
        con.execute(
            """
            CREATE TABLE reference.eredes_at_inventory_quality AS
            SELECT 'source_rows' AS metric, count(*)::DOUBLE AS value
            FROM reference.eredes_at_line_inventory
            UNION ALL
            SELECT 'valid_rows', count(*)::DOUBLE
            FROM reference.eredes_at_line_inventory WHERE quality_status='VALID'
            UNION ALL
            SELECT 'model_link_rows', count(*)::DOUBLE
            FROM reference.eredes_at_line_model_links
            UNION ALL
            SELECT 'linked_inventory_items', count(DISTINCT inventory_id)::DOUBLE
            FROM reference.eredes_at_line_model_links
            UNION ALL
            SELECT 'total_inventory_length_km', sum(total_length_km)
            FROM reference.eredes_at_line_inventory
            UNION ALL
            SELECT 'unmatched_inventory_items', count(*)::DOUBLE
            FROM reference.eredes_at_line_link_audit WHERE link_status LIKE 'UNMATCHED_%'
            """
        )

        con.execute(
            """
            CREATE TABLE IF NOT EXISTS provenance.public_inventory_imports (
                source_id VARCHAR,
                effective_date DATE,
                source_url VARCHAR,
                source_table VARCHAR,
                target_table VARCHAR,
                source_rows BIGINT,
                target_rows BIGINT,
                imported_at_utc TIMESTAMP,
                transform_version VARCHAR
            )
            """
        )
        con.execute("DELETE FROM provenance.public_inventory_imports WHERE source_id=?", [SOURCE_ID])
        target_count = con.execute("SELECT count(*) FROM reference.eredes_at_line_inventory").fetchone()[0]
        con.execute(
            """INSERT INTO provenance.public_inventory_imports
               VALUES (?, DATE '2025-12-31', ?, 'raw_eredes.pdird_at_circuits',
                       'reference.eredes_at_line_inventory', ?, ?, current_timestamp, '1.0')""",
            [SOURCE_ID, SOURCE_URL, source_count, target_count],
        )

        quality = dict(con.execute(
            "SELECT metric, value FROM reference.eredes_at_inventory_quality"
        ).fetchall())
        if target_count != source_count:
            raise ValueError(f"Row-count mismatch: source={source_count}, target={target_count}")
        invalid = con.execute(
            "SELECT count(*) FROM reference.eredes_at_line_inventory WHERE quality_status <> 'VALID'"
        ).fetchone()[0]

        if args.dry_run:
            con.execute("ROLLBACK")
        else:
            con.execute("COMMIT")
        print(json.dumps({
            "database": str(database),
            "dry_run": args.dry_run,
            "source_rows": source_count,
            "standardized_rows": target_count,
            "invalid_or_review_rows": invalid,
            "model_link_rows": int(quality.get("model_link_rows", 0)),
            "linked_inventory_items": int(quality.get("linked_inventory_items", 0)),
            "tables": [
                "reference.eredes_at_line_inventory",
                "reference.eredes_at_line_model_links",
                "reference.eredes_at_line_link_audit",
                "reference.eredes_at_inventory_quality",
                "provenance.public_inventory_imports",
            ],
        }, ensure_ascii=False, indent=2))
    except Exception:
        try:
            con.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        con.close()


if __name__ == "__main__":
    main()
