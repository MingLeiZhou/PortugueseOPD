#!/usr/bin/env python3
"""Build full-calendar factorized main-grid generator states from public REN totals.

The public series identifies national generation by source, not unit telemetry.
Outside the 336 archived PT60 replay hours, available mapped assets share each
source target in proportion to nameplate capacity.  The archived deterministic
PT60 allocation overrides this general scenario wherever it exists.  Solar is
reduced by the configured LV fraction so the unified resource view does not
double count embedded PV.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
BASE = ROOT / "data/releases/SimPT60-2026.09.21-r1/bundle/inputs.duckdb"
SOURCES = (
    "Hydro", "Solar", "Wind", "Natural Gas", "Other Thermal", "Biomass",
    "Wave", "Battery Injection",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--base", type=Path, default=BASE)
    args = parser.parse_args()
    base_sql = str(args.base).replace("'", "''")

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS observation")
        con.execute("CREATE SCHEMA IF NOT EXISTS operating")
        con.execute("CREATE SCHEMA IF NOT EXISTS audit")
        con.execute(f"ATTACH '{base_sql}' AS frozen (READ_ONLY)")
        con.execute("""CREATE OR REPLACE TABLE observation.ren_generation_source_15min AS
            SELECT c.timestamp_utc,r.series_name AS generation_source,r.value_mw AS observed_mw,
                   cast(r.source_date AS VARCHAR) AS source_date,r.source_index,
                   r.source_time_local,
                   'PUBLIC_REN_NATIONAL_SOURCE_TOTAL' AS evidence_status
            FROM frozen.main.ren_dispatch r
            JOIN frozen.main.interval_calendar c
              ON r.source_date=c.local_date AND r.source_index=c.source_index
            WHERE r.series_name IN (SELECT unnest(?))
            ORDER BY c.timestamp_utc,r.series_name""", [list(SOURCES)])
        con.execute("""CREATE OR REPLACE TABLE operating.main_grid_generator_allocation_definition AS
            WITH source_map(asset_source,generation_source) AS (VALUES
              ('hydro','Hydro'),('solar','Solar'),('wind','Wind'),
              ('gas','Natural Gas'),('gas;oil','Natural Gas'),('oil;gas','Natural Gas'),
              ('oil','Other Thermal'),('diesel','Other Thermal'),('geothermal','Other Thermal'),
              ('biomass','Biomass'),('biogas','Biomass'),('biomass;gas','Biomass'),
              ('waste','Biomass'),('wave','Wave'),('battery','Battery Injection')
            )
            SELECT g.generator_id,m.generation_source,g.generation_source AS asset_source,
                   g.nameplate_mw,g.bus_id,g.available_from_utc,
                   g.bus_id IS NOT NULL AS mapped_to_model,
                   'PT60_PUBLIC_ASSET_INVENTORY_SOURCE_GROUP_MAPPING' AS evidence_status
            FROM equivalent.base_generators g
            JOIN source_map m ON lower(g.generation_source)=m.asset_source""")
        con.execute("""CREATE OR REPLACE TABLE operating.main_grid_source_target_15min AS
            SELECT o.timestamp_utc,o.generation_source,o.observed_mw,
                   CASE WHEN o.generation_source='Solar'
                        THEN o.observed_mw*(1-f.lv_fraction) ELSE o.observed_mw END
                        AS main_grid_target_mw,
                   CASE WHEN o.generation_source='Solar'
                        THEN o.observed_mw*f.lv_fraction ELSE 0::DOUBLE END
                        AS lv_pv_scenario_mw,
                   o.source_date,o.source_index,o.evidence_status,
                   CASE WHEN o.generation_source='Solar'
                        THEN 'PUBLIC_REN_TOTAL_MINUS_CONFIGURED_LV_PV_SCENARIO'
                        ELSE 'PUBLIC_REN_NATIONAL_SOURCE_TOTAL' END AS target_status
            FROM observation.ren_generation_source_15min o
            CROSS JOIN operating.solar_split_assumption f""")
        con.execute("""CREATE OR REPLACE TABLE operating.main_grid_source_capacity_15min AS
            SELECT t.timestamp_utc,t.generation_source,
                   count(*) FILTER (WHERE d.mapped_to_model AND
                       (d.available_from_utc IS NULL OR d.available_from_utc<=try_cast(t.timestamp_utc AS TIMESTAMPTZ)))
                       AS available_asset_count,
                   coalesce(sum(d.nameplate_mw) FILTER (WHERE d.mapped_to_model AND
                       (d.available_from_utc IS NULL OR d.available_from_utc<=try_cast(t.timestamp_utc AS TIMESTAMPTZ))),0)
                       AS available_nameplate_mw
            FROM operating.main_grid_source_target_15min t
            JOIN operating.main_grid_generator_allocation_definition d USING(generation_source)
            GROUP BY 1,2""")
        con.execute("""CREATE OR REPLACE VIEW operating.main_grid_generator_dispatch_15min_general AS
            SELECT t.timestamp_utc,d.generator_id,d.generation_source,d.asset_source,
                   d.bus_id,d.nameplate_mw,
                   d.mapped_to_model AND
                     (d.available_from_utc IS NULL OR d.available_from_utc<=try_cast(t.timestamp_utc AS TIMESTAMPTZ))
                     AS snapshot_asset_available,
                   CASE WHEN d.mapped_to_model AND
                                  (d.available_from_utc IS NULL OR d.available_from_utc<=try_cast(t.timestamp_utc AS TIMESTAMPTZ))
                                  AND c.available_nameplate_mw>0
                        THEN least(t.main_grid_target_mw,c.available_nameplate_mw)
                             *d.nameplate_mw/c.available_nameplate_mw
                        ELSE 0::DOUBLE END AS p_mw,
                   NULL::DOUBLE AS q_mvar,
                   'PUBLIC_REN_SOURCE_TOTAL_CAPACITY_PROPORTIONAL_SCENARIO' AS dispatch_basis
            FROM operating.main_grid_source_target_15min t
            JOIN operating.main_grid_source_capacity_15min c USING(timestamp_utc,generation_source)
            JOIN operating.main_grid_generator_allocation_definition d USING(generation_source)""")
        con.execute("""CREATE OR REPLACE VIEW operating.main_grid_generation_residual_15min_general AS
            SELECT t.timestamp_utc,t.generation_source,
                   'GEN:RESIDUAL:'||CASE t.generation_source
                     WHEN 'Hydro' THEN 'HYDRO' WHEN 'Solar' THEN 'SOLAR'
                     WHEN 'Wind' THEN 'WIND' WHEN 'Natural Gas' THEN 'NATURAL_GAS'
                     WHEN 'Other Thermal' THEN 'OTHER_THERMAL' WHEN 'Biomass' THEN 'BIOMASS'
                     WHEN 'Wave' THEN 'WAVE' ELSE 'BATTERY_INJECTION' END AS resource_id,
                   greatest(t.main_grid_target_mw-c.available_nameplate_mw,0) AS p_mw,
                   'PUBLIC_REN_TARGET_MINUS_AVAILABLE_MAPPED_CAPACITY' AS evidence_status
            FROM operating.main_grid_source_target_15min t
            JOIN operating.main_grid_source_capacity_15min c USING(timestamp_utc,generation_source)""")
        con.execute("""CREATE OR REPLACE VIEW operating.main_grid_generator_state_15min_general AS
            SELECT 'SNAPSHOT:'||timestamp_utc AS snapshot_id,timestamp_utc,
                   generator_id AS resource_id,NULL::VARCHAR AS ptd_code,p_mw,q_mvar,
                   NULL::DOUBLE AS soc_percent,
                   CASE WHEN NOT snapshot_asset_available THEN 'NOT_YET_AVAILABLE_OR_OUT_OF_MODEL'
                        WHEN p_mw>0 THEN 'IN_SERVICE' ELSE 'AVAILABLE_ZERO_DISPATCH' END AS service_status,
                   'FULL_REN_15MIN_CALENDAR' AS profile_data_status,
                   dispatch_basis AS state_evidence_status
            FROM operating.main_grid_generator_dispatch_15min_general
            UNION ALL
            SELECT 'SNAPSHOT:'||timestamp_utc,timestamp_utc,resource_id,NULL::VARCHAR,
                   p_mw,NULL::DOUBLE,NULL::DOUBLE,
                   CASE WHEN p_mw>0 THEN 'IN_SERVICE' ELSE 'AVAILABLE_ZERO_DISPATCH' END,
                   'FULL_REN_15MIN_CALENDAR',evidence_status
            FROM operating.main_grid_generation_residual_15min_general""")
        con.execute("""CREATE OR REPLACE VIEW operating.main_grid_generator_state_15min AS
            SELECT g.* FROM operating.main_grid_generator_state_15min_general g
            WHERE NOT EXISTS (SELECT 1 FROM observation.ren_generation_case_window a
                              WHERE a.timestamp_utc=g.timestamp_utc)
            UNION ALL
            SELECT * FROM operating.main_grid_generator_state_observed_window""")
        con.execute("""CREATE OR REPLACE VIEW operating.resource_state_available AS
            SELECT snapshot_id,timestamp_utc,resource_id,ptd_code,p_mw,q_mvar,soc_percent,
                   service_status,profile_data_status,state_evidence_status
            FROM operating.resource_state_15min
            UNION ALL
            SELECT snapshot_id,timestamp_utc,resource_id,ptd_code,p_mw,q_mvar,soc_percent,
                   service_status,profile_data_status,state_evidence_status
            FROM operating.main_grid_generator_state_15min""")
        # The 336-hour archived PT60 release and the full REN calendar were
        # frozen from separate public acquisitions.  Preserve their overlap as
        # a revision audit rather than silently requiring bit-identical values.
        # Archived unit allocations remain authoritative at overlap timestamps.
        con.execute("""CREATE OR REPLACE TABLE audit.ren_generation_source_revision_overlap AS
            SELECT a.timestamp_utc,a.generation_source,
                   a.observed_mw AS archived_observed_mw,
                   f.observed_mw AS full_calendar_observed_mw,
                   a.observed_mw-f.observed_mw AS difference_mw,
                   abs(a.observed_mw-f.observed_mw) AS absolute_difference_mw,
                   CASE WHEN abs(f.observed_mw)>1e-12
                        THEN 100*abs(a.observed_mw-f.observed_mw)/abs(f.observed_mw)
                        ELSE NULL END AS relative_difference_percent,
                   CASE WHEN abs(a.observed_mw-f.observed_mw)<=1e-9 THEN 'IDENTICAL'
                        ELSE 'PUBLIC_SOURCE_REVISION_DIFFERENCE' END AS revision_status,
                   'ARCHIVED_PT60_ALLOCATION_OVERRIDES_FULL_CALENDAR_AT_OVERLAP' AS resolution_rule
            FROM observation.ren_generation_source_window a
            JOIN observation.ren_generation_source_15min f
              USING(timestamp_utc,generation_source)
            ORDER BY a.timestamp_utc,a.generation_source""")
        con.execute("""UPDATE operating.resource_timeseries_definition
            SET allocation_rule=CASE WHEN resource_id LIKE 'GEN:RESIDUAL:%'
                  THEN 'REN_SOURCE_TOTAL_MINUS_AVAILABLE_MAPPED_CAPACITY'
                  ELSE 'ARCHIVED_PT60_OR_FULL_CALENDAR_CAPACITY_PROPORTIONAL_ALLOCATION' END,
                resolution='15_MINUTES',
                evidence_status=CASE WHEN resource_id LIKE 'GEN:RESIDUAL:%'
                  THEN 'UNMAPPED_NATIONAL_GENERATION_RESIDUAL_PROXY'
                  ELSE 'PUBLIC_SOURCE_TOTAL_DERIVED_UNIT_SCENARIO_NOT_UNIT_TELEMETRY' END
            WHERE variable_family='ACTIVE_GENERATION'""")

        checks = {
            "operating_snapshots": con.execute("SELECT count(*) FROM operating.operating_snapshot").fetchone()[0],
            "ren_source_rows": con.execute("SELECT count(*) FROM observation.ren_generation_source_15min").fetchone()[0],
            "ren_source_distinct_timestamps": con.execute("SELECT count(DISTINCT timestamp_utc) FROM observation.ren_generation_source_15min").fetchone()[0],
            "ren_source_distinct_groups": con.execute("SELECT count(DISTINCT generation_source) FROM observation.ren_generation_source_15min").fetchone()[0],
            "allocation_definitions": con.execute("SELECT count(*) FROM operating.main_grid_generator_allocation_definition").fetchone()[0],
            "source_capacity_rows": con.execute("SELECT count(*) FROM operating.main_grid_source_capacity_15min").fetchone()[0],
            "timestamps_missing_generator_state": con.execute("""SELECT count(*) FROM operating.operating_snapshot s
                WHERE NOT EXISTS (SELECT 1 FROM operating.main_grid_source_capacity_15min g
                                  WHERE g.timestamp_utc=s.timestamp_utc)""").fetchone()[0],
            "archived_source_overlap_rows": con.execute("""SELECT count(*)
                FROM audit.ren_generation_source_revision_overlap""").fetchone()[0],
            "archived_source_overlap_changed_rows": con.execute("""SELECT count(*)
                FROM audit.ren_generation_source_revision_overlap
                WHERE revision_status='PUBLIC_SOURCE_REVISION_DIFFERENCE'""").fetchone()[0],
            "archived_nonwind_overlap_max_difference_mw": con.execute("""SELECT max(absolute_difference_mw)
                FROM audit.ren_generation_source_revision_overlap
                WHERE generation_source<>'Wind'""").fetchone()[0],
            "archived_wind_overlap_changed_rows": con.execute("""SELECT count(*)
                FROM audit.ren_generation_source_revision_overlap
                WHERE generation_source='Wind'
                  AND revision_status='PUBLIC_SOURCE_REVISION_DIFFERENCE'""").fetchone()[0],
            "archived_wind_overlap_max_difference_mw": con.execute("""SELECT max(absolute_difference_mw)
                FROM audit.ren_generation_source_revision_overlap
                WHERE generation_source='Wind'""").fetchone()[0],
            "archived_wind_overlap_mean_difference_mw": con.execute("""SELECT avg(absolute_difference_mw)
                FROM audit.ren_generation_source_revision_overlap
                WHERE generation_source='Wind'""").fetchone()[0],
            "archived_wind_overlap_max_relative_difference_percent": con.execute("""SELECT max(relative_difference_percent)
                FROM audit.ren_generation_source_revision_overlap
                WHERE generation_source='Wind'""").fetchone()[0],
            "source_balance_max_error_mw": con.execute("""SELECT max(abs(
                t.observed_mw-least(t.main_grid_target_mw,c.available_nameplate_mw)
                -greatest(t.main_grid_target_mw-c.available_nameplate_mw,0)-t.lv_pv_scenario_mw))
                FROM operating.main_grid_source_target_15min t
                JOIN operating.main_grid_source_capacity_15min c USING(timestamp_utc,generation_source)""").fetchone()[0],
            "resource_definition_count": con.execute("SELECT count(*) FROM operating.resource_timeseries_definition").fetchone()[0],
            "model_resource_count": con.execute("SELECT count(*) FROM model.resource").fetchone()[0],
            "definition_orphans": con.execute("""SELECT count(*) FROM operating.resource_timeseries_definition d
                LEFT JOIN model.resource r USING(resource_id) WHERE r.resource_id IS NULL""").fetchone()[0],
            "full_calendar_logical_generator_states": con.execute("SELECT count(*) FROM operating.operating_snapshot").fetchone()[0]*1198,
            "snapshots_using_archived_override": con.execute("SELECT count(*) FROM observation.ren_generation_case_window").fetchone()[0],
        }
        samples = []
        for timestamp, expected_status in (
            ("2025-07-07T00:15:00+00:00", "ARCHIVED_REN_HOURLY_WINDOW"),
            ("2025-07-22T11:45:00+00:00", "FULL_REN_15MIN_CALENDAR"),
            ("2026-01-19T19:45:00+00:00", "FULL_REN_15MIN_CALENDAR"),
        ):
            row = con.execute("""SELECT count(*),count(DISTINCT resource_id),
                count(*) FILTER (WHERE profile_data_status=?),
                count(*) FILTER (WHERE q_mvar IS NOT NULL),min(p_mw),
                (SELECT count(*) FROM operating.resource_state_available WHERE timestamp_utc=?)
                FROM operating.main_grid_generator_state_15min WHERE timestamp_utc=?""",
                [expected_status,timestamp,timestamp]).fetchone()
            samples.append({
                "timestamp_utc": timestamp,
                "expected_profile_status": expected_status,
                "states": int(row[0]), "distinct_resources": int(row[1]),
                "matching_profile_status": int(row[2]), "nonnull_q_rows": int(row[3]),
                "minimum_p_mw": float(row[4]), "unified_resource_states": int(row[5]),
            })
        checks["sample_snapshots"] = samples
        con.execute("DETACH frozen")

    errors: list[str] = []
    expected = {
        "operating_snapshots": 31492,
        "ren_source_rows": 31492*8,
        "ren_source_distinct_timestamps": 31492,
        "ren_source_distinct_groups": 8,
        "allocation_definitions": 1190,
        "source_capacity_rows": 31492*8,
        "archived_source_overlap_rows": 336*8,
        "resource_definition_count": 219017,
        "model_resource_count": 219017,
        "snapshots_using_archived_override": 336,
    }
    for key, value in expected.items():
        if checks[key] != value:
            errors.append(f"{key}={checks[key]} expected {value}")
    for key in ("timestamps_missing_generator_state", "definition_orphans"):
        if checks[key]:
            errors.append(f"{key}={checks[key]}")
    if checks["archived_nonwind_overlap_max_difference_mw"] is None or checks["archived_nonwind_overlap_max_difference_mw"] > 1e-8:
        errors.append("Non-wind public source overlap differs between frozen acquisitions: "
                      f"max={checks['archived_nonwind_overlap_max_difference_mw']} MW")
    # REN wind values have 159 small revisions between the two frozen public
    # acquisitions.  This bounded check detects a material shift while allowing
    # the observed revision; the audit table retains every differing row.
    if (checks["archived_source_overlap_changed_rows"] != checks["archived_wind_overlap_changed_rows"]
            or checks["archived_wind_overlap_max_difference_mw"] is None
            or checks["archived_wind_overlap_max_difference_mw"] > 2.0
            or checks["archived_wind_overlap_max_relative_difference_percent"] is None
            or checks["archived_wind_overlap_max_relative_difference_percent"] > 0.1):
        errors.append("Archived/full-calendar wind revision exceeds policy: "
                      f"changed={checks['archived_wind_overlap_changed_rows']}, "
                      f"max_mw={checks['archived_wind_overlap_max_difference_mw']}, "
                      f"max_percent={checks['archived_wind_overlap_max_relative_difference_percent']}")
    if checks["source_balance_max_error_mw"] is None or checks["source_balance_max_error_mw"] > 1e-8:
        errors.append(f"source_balance_max_error_mw={checks['source_balance_max_error_mw']}")
    for sample in samples:
        if (sample["states"] != 1198 or sample["distinct_resources"] != 1198
                or sample["matching_profile_status"] != 1198
                or sample["nonnull_q_rows"] or sample["minimum_p_mw"] < -1e-10
                or sample["unified_resource_states"] != 219017):
            errors.append(f"Generator/unified state sample failed at {sample['timestamp_utc']}")

    report = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "scope": "Full 31,492-snapshot public REN source totals with factorized 1,190-asset plus 8-residual generator states; archived PT60 allocation overrides 336 hours",
        "limitations": [
            "REN publishes national source totals rather than unit telemetry",
            "Outside 336 archived PT60 hours, available mapped assets share source output in proportion to nameplate capacity",
            "Generator reactive power remains unknown and is stored as NULL",
            "The utility versus embedded solar split follows the configured LV scenario rather than an observed boundary",
            "The two frozen REN acquisitions differ at 159 wind overlap points; archived allocation wins and every difference is retained in audit.ren_generation_source_revision_overlap",
            "The 37.7 million generator states are a filtered view and are not materialized",
        ],
        "errors": errors,
    }
    path = args.database.parent / "main_grid_generator_timeseries_full.validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
