#!/usr/bin/env python3
"""Build a topology-consistent PTD load allocation for OSM source zones.

Eligible PTDs in a source-distance zone are reassigned to that candidate
station.  All other PTDs keep their baseline assignment.  For every station
with a complete profile, PTD factors are renormalized so their sum reproduces
the existing station LV share at every UTC timestamp.  The scenario is an
inference and does not overwrite the baseline operating tables.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/osm_zone_load_allocation"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS scenario")
        con.execute("""CREATE OR REPLACE TABLE scenario.osm_zone_ptd_assignment AS
            WITH eligible_zone AS (
              SELECT z.ptd_code,z.candidate_station_code,z.component_id,
                     z.distance_to_osm_segment_km
              FROM candidate.ptd_zone_candidates z
              JOIN candidate.ptd_node_candidates n USING(ptd_code)
              WHERE z.candidate_station_code IS NOT NULL
                AND z.connection_status='NEAR_ANCHORED_COMPONENT'
                AND z.distance_to_osm_segment_km<=0.2
                AND n.capacity_kva_public>0
            ), preliminary AS (
              SELECT a.ptd_code,
                     a.assigned_station_code AS baseline_assigned_station_code,
                     a.profile_station_code AS baseline_profile_station_code,
                     a.factor AS baseline_factor,
                     a.station_lv_fraction AS baseline_station_lv_fraction,
                     a.peak_proxy_mw,
                     z.candidate_station_code AS zone_candidate_station_code,
                     z.component_id AS zone_component_id,
                     z.distance_to_osm_segment_km,
                     coalesce(z.candidate_station_code,a.assigned_station_code) AS scenario_assigned_station_code,
                     CASE WHEN z.candidate_station_code IS NOT NULL
                          THEN 'OSM_GRAPH_DISTANCE_ZONE_REASSIGNMENT_UNVERIFIED'
                          ELSE 'BASELINE_STATION_ASSIGNMENT_RETAINED' END AS assignment_status
              FROM operating.ptd_allocations a LEFT JOIN eligible_zone z USING(ptd_code)
            ), profiled AS (
              SELECT p.*,s.lv_fraction AS scenario_station_lv_fraction,
                     CASE WHEN s.station_code IS NOT NULL THEN true ELSE false END AS has_scenario_station_profile
              FROM preliminary p LEFT JOIN operating.station_lv_split s
                ON s.station_code=p.scenario_assigned_station_code
            ), denominator AS (
              SELECT scenario_assigned_station_code,sum(peak_proxy_mw) AS assigned_peak_proxy_sum_mw,
                     count(*) AS assigned_ptd_count
              FROM profiled WHERE has_scenario_station_profile GROUP BY 1
            )
            SELECT p.ptd_code,p.baseline_assigned_station_code,p.baseline_profile_station_code,
                   p.zone_candidate_station_code,p.zone_component_id,p.distance_to_osm_segment_km,
                   p.scenario_assigned_station_code,
                   CASE WHEN p.has_scenario_station_profile THEN p.scenario_assigned_station_code
                        ELSE p.baseline_profile_station_code END AS scenario_profile_station_code,
                   p.peak_proxy_mw,
                   CASE WHEN p.has_scenario_station_profile
                        THEN p.peak_proxy_mw/d.assigned_peak_proxy_sum_mw
                        ELSE p.baseline_factor END AS scenario_factor,
                   CASE WHEN p.has_scenario_station_profile THEN p.scenario_station_lv_fraction
                        ELSE p.baseline_station_lv_fraction END AS scenario_station_lv_fraction,
                   d.assigned_peak_proxy_sum_mw,d.assigned_ptd_count,
                   p.assignment_status,
                   CASE WHEN p.has_scenario_station_profile
                        THEN 'SCENARIO_ASSIGNED_STATION_PUBLIC_OR_IMPUTED_COMPLETE_PROFILE'
                        ELSE 'BASELINE_PROFILE_FALLBACK_NO_SCENARIO_STATION_TIMESERIES' END AS profile_status,
                   'INFERRED_TOPOLOGY_ASSIGNMENT_PRESERVES_STATION_LV_TOTAL' AS evidence_status
            FROM profiled p LEFT JOIN denominator d USING(scenario_assigned_station_code)""")
        con.execute("""CREATE OR REPLACE VIEW scenario.osm_zone_ptd_load_15min AS
            SELECT a.ptd_code,s.timestamp_utc,
                   s.p_mw*a.scenario_factor*a.scenario_station_lv_fraction AS p_mw,
                   s.p_mw*a.scenario_factor*a.scenario_station_lv_fraction*tan(acos(0.97)) AS q_mvar,
                   a.scenario_assigned_station_code,a.zone_component_id,
                   a.scenario_profile_station_code,a.assignment_status,a.profile_status,
                   s.data_status AS profile_data_status,a.evidence_status
            FROM scenario.osm_zone_ptd_assignment a
            JOIN operating.station_15min_complete s
              ON s.station_code=a.scenario_profile_station_code""")
        counts = con.execute("""SELECT count(*) AS rows,count(DISTINCT ptd_code) AS ptds,
                   count(*) FILTER (WHERE zone_candidate_station_code IS NOT NULL) AS zone_reassigned,
                   count(*) FILTER (WHERE zone_candidate_station_code IS NULL) AS baseline_retained,
                   count(*) FILTER (WHERE profile_status LIKE 'BASELINE_PROFILE_FALLBACK%') AS fallback_ptds,
                   count(DISTINCT scenario_assigned_station_code) FILTER
                     (WHERE profile_status LIKE 'BASELINE_PROFILE_FALLBACK%') AS fallback_assigned_stations,
                   count(DISTINCT scenario_assigned_station_code) AS assigned_stations
            FROM scenario.osm_zone_ptd_assignment""").fetchone()
        duplicate_ptds = con.execute("""SELECT count(*) FROM (
            SELECT ptd_code,count(*) n FROM scenario.osm_zone_ptd_assignment GROUP BY 1 HAVING n<>1)""").fetchone()[0]
        factor_error = con.execute("""SELECT max(abs(factor_sum-1)) FROM (
            SELECT scenario_assigned_station_code,sum(scenario_factor) factor_sum
            FROM scenario.osm_zone_ptd_assignment
            WHERE profile_status='SCENARIO_ASSIGNED_STATION_PUBLIC_OR_IMPUTED_COMPLETE_PROFILE'
            GROUP BY 1)""").fetchone()[0]
        timestamps = [row[0] for row in con.execute("""SELECT timestamp_utc
            FROM operating.operating_snapshot ORDER BY timestamp_utc
            LIMIT 1""").fetchall()]
        timestamps += [con.execute("SELECT timestamp_utc FROM operating.operating_snapshot ORDER BY timestamp_utc LIMIT 1 OFFSET 15746").fetchone()[0]]
        timestamps += [con.execute("SELECT timestamp_utc FROM operating.operating_snapshot ORDER BY timestamp_utc DESC LIMIT 1").fetchone()[0]]
        conservation = []
        for timestamp in timestamps:
            maximum = con.execute("""WITH actual AS (
                  SELECT scenario_assigned_station_code AS station_code,sum(p_mw) AS p_mw
                  FROM scenario.osm_zone_ptd_load_15min
                  WHERE timestamp_utc=?
                    AND profile_status='SCENARIO_ASSIGNED_STATION_PUBLIC_OR_IMPUTED_COMPLETE_PROFILE'
                  GROUP BY 1
                ), expected AS (
                  SELECT a.scenario_assigned_station_code AS station_code,
                         s.p_mw*max(a.scenario_station_lv_fraction) AS p_mw
                  FROM scenario.osm_zone_ptd_assignment a
                  JOIN operating.station_15min_complete s
                    ON s.station_code=a.scenario_assigned_station_code AND s.timestamp_utc=?
                  WHERE a.profile_status='SCENARIO_ASSIGNED_STATION_PUBLIC_OR_IMPUTED_COMPLETE_PROFILE'
                  GROUP BY 1,s.p_mw
                ) SELECT max(abs(actual.p_mw-expected.p_mw))
                FROM actual JOIN expected USING(station_code)""", [timestamp, timestamp]).fetchone()[0]
            conservation.append({"timestamp_utc": timestamp, "max_station_lv_balance_error_mw": float(maximum or 0.0)})
        missing_definitions = con.execute("""SELECT count(*) FROM scenario.osm_zone_ptd_assignment a
            WHERE NOT EXISTS (SELECT 1 FROM model.resource r
                              WHERE r.source_ref=a.ptd_code AND r.resource_type='LOAD')""").fetchone()[0]

    checks = {
        "assignment_rows": int(counts[0]), "distinct_ptds": int(counts[1]),
        "zone_reassigned_ptds": int(counts[2]), "baseline_retained_ptds": int(counts[3]),
        "fallback_profile_ptds": int(counts[4]), "fallback_assigned_station_count": int(counts[5]),
        "assigned_station_count": int(counts[6]),
        "duplicate_or_missing_ptd_assignments": int(duplicate_ptds),
        "maximum_station_factor_sum_error": float(factor_error or 0.0),
        "missing_registered_load_resources": int(missing_definitions),
        "sample_station_lv_conservation": conservation,
    }
    errors = []
    if counts[0] != 72434 or counts[1] != 72434 or duplicate_ptds:
        errors.append("PTD assignment cardinality failure")
    if float(factor_error or 0.0) > 1e-12:
        errors.append("Scenario station factors do not sum to one")
    if any(row["max_station_lv_balance_error_mw"] > 1e-9 for row in conservation):
        errors.append("Scenario station LV load does not conserve the selected station total")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "errors": errors,
        "scope": "All PTDs, all existing UTC snapshots through a factorized scenario view",
        "limitations": [
            "OSM source-zone assignment and virtual boundaries remain inferred rather than operator-confirmed.",
            "PTD weights remain peak-proxy weights rather than customer meter measurements.",
            "Assignments whose scenario station lacks a complete profile retain each PTD's documented baseline profile; this includes one OSM zone source station and baseline-only station codes.",
        ],
    }
    (args.output / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
