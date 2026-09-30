#!/usr/bin/env python3
"""Audit the five completion gates for the public-data synthetic grid database."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/complete_simulation_database.validation.json"


def read(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(path)
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()

    base = args.database.parent
    reports = {
        "parameters": read(base / "distribution_parameters.validation.json"),
        "timeseries": read(base / "resource_state_timeseries.validation.json"),
        "phase": read(base / "four_wire_root_peak_design/validation.json"),
        "equivalent_pf_fault": read(base / "equivalent_mv_root_peak_batch/validation.json"),
        "attachment": read(base / "mv_osm_attachment_graph.validation.json"),
        "allocation": read(base / "osm_zone_load_allocation/validation.json"),
        "large_mv": read(base / "mv_validated_operating_scenario/validation.json"),
        "small_mv": read(base / "osm_small_component_validated_scenario/validation.json"),
        "normalized": read(base / "normalized_network_model.validation.json"),
    }

    with duckdb.connect(str(args.database)) as con:
        db = {
            "national_ptds": con.execute("SELECT count(*) FROM parameter.ptd_transformer_parameters").fetchone()[0],
            "osm_candidate_components": con.execute("SELECT count(*) FROM candidate.osm_components").fetchone()[0],
            "eligible_source_anchored_components": con.execute("""SELECT count(DISTINCT component_id) FROM (
                SELECT component_id FROM scenario.osm_small_component_validated_design
                UNION ALL SELECT component_id FROM scenario.mv_validated_zone_design)""").fetchone()[0],
            "large_operating_components": con.execute(
                "SELECT count(DISTINCT component_id) FROM scenario.mv_validated_zone_design"
            ).fetchone()[0],
            "large_operating_zones": con.execute(
                "SELECT count(*) FROM scenario.mv_validated_zone_design"
            ).fetchone()[0],
            "small_operating_components": con.execute(
                "SELECT count(*) FROM scenario.osm_small_component_validated_design"
            ).fetchone()[0],
            "osm_operating_ptds": con.execute("""SELECT count(DISTINCT ptd_code) FROM (
                SELECT ptd_code FROM scenario.mv_validated_ptd_feeder
                UNION ALL SELECT ptd_code FROM scenario.osm_small_component_validated_ptd_feeder)""").fetchone()[0],
            "lv_phase_share_ptds": con.execute("SELECT count(DISTINCT ptd_code) FROM phase.lv_load_shares").fetchone()[0],
            "lv_grounding_ptds": con.execute("SELECT count(DISTINCT ptd_code) FROM phase.ptd_neutral_grounding").fetchone()[0],
            "lv_feeders": con.execute("SELECT count(*) FROM model.feeder").fetchone()[0],
            "lv_routes": con.execute("SELECT count(*) FROM model.route").fetchone()[0],
            "registered_objects": con.execute("SELECT count(*) FROM model.object_registry").fetchone()[0],
            "missing_parameter_bindings": con.execute("""SELECT count(*) FROM model.equipment_parameter_binding
                WHERE parameter_status='MISSING_ELECTRICAL_PARAMETER_SET'""").fetchone()[0],
            "scenario_binding_errors": con.execute("""SELECT count(*) FROM model.equipment_scenario_binding s
                LEFT JOIN model.conducting_equipment e USING(equipment_id,network_layer)
                WHERE e.equipment_id IS NULL""").fetchone()[0],
            "mv_reinforced_ptds": con.execute("""SELECT count(*) FROM (
                SELECT ptd_code FROM scenario.mv_validated_ptd_transformer_design
                WHERE capacity_action<>'PUBLIC_CAPACITY_RETAINED'
                UNION ALL
                SELECT ptd_code FROM scenario.osm_small_component_ptd_transformer_design
                WHERE capacity_action<>'PUBLIC_CAPACITY_RETAINED')""").fetchone()[0],
        }

        parameter_checks = reports["parameters"]["checks"]
        time_checks = reports["timeseries"]["checks"]
        phase_checks = reports["phase"]["checks"]
        eq_checks = reports["equivalent_pf_fault"]["checks"]
        attach_checks = reports["attachment"]["checks"]
        alloc_checks = reports["allocation"]["checks"]
        large_checks = reports["large_mv"]["checks"]
        small_checks = reports["small_mv"]["checks"]
        norm_checks = reports["normalized"]["checks"]

        gates = [
            {
                "gate": 1,
                "name": "OSM_MV_PTD_LV_TOPOLOGY",
                "result": "PASS" if (
                    reports["attachment"]["result"] == "PASS"
                    and reports["large_mv"]["result"] == "PASS"
                    and reports["small_mv"]["result"] == "PASS"
                    and attach_checks["assessed_ptds"] == db["national_ptds"]
                    and large_checks["validated_zone_designs"] == 146
                    and small_checks["validated_design_components"] == 127
                    and db["eligible_source_anchored_components"] == 136
                    and db["lv_feeders"] == db["lv_routes"]
                    and norm_checks["route_segment_reference_errors"] == 0
                ) else "FAIL",
                "evidence": {
                    "national_ptds_assessed": attach_checks["assessed_ptds"],
                    "osm_attached_ptds": attach_checks["osm_attached_ptds"],
                    "synthetic_fallback_ptds": attach_checks["synthetic_fallback_ptds"],
                    "source_anchored_osm_components": db["eligible_source_anchored_components"],
                    "validated_large_source_zones": large_checks["validated_zone_designs"],
                    "validated_small_components": small_checks["validated_design_components"],
                    "osm_operating_ptds": db["osm_operating_ptds"],
                    "synthetic_lv_feeders_with_routes": db["lv_routes"],
                },
            },
            {
                "gate": 2,
                "name": "MV_LV_LINE_AND_TRANSFORMER_PARAMETERS",
                "result": "PASS" if (
                    reports["parameters"]["result"] == "PASS"
                    and parameter_checks["positive_mv_parameters"] == parameter_checks["mv_line_rows"]
                    and parameter_checks["positive_ptd_capacity"] == db["national_ptds"]
                    and db["missing_parameter_bindings"] == 0
                    and norm_checks["parameter_binding_reference_errors"] == 0
                ) else "FAIL",
                "evidence": {
                    "mv_line_parameter_rows": parameter_checks["mv_line_rows"],
                    "ptd_transformer_parameter_rows": parameter_checks["ptd_transformer_rows"],
                    "public_standard_overhead_rows": parameter_checks["public_standard_overhead_rows"],
                    "public_standard_cable_rows": parameter_checks["public_standard_cable_rows"],
                    "public_standard_transformer_rows": parameter_checks["public_standard_transformer_rows"],
                    "missing_parameter_bindings": db["missing_parameter_bindings"],
                    "planning_reinforced_ptds": db["mv_reinforced_ptds"],
                    "asset_level_public_conductor_identities": parameter_checks["asset_level_public_mv_conductor_identity_count"],
                    "asset_level_public_transformer_impedances": parameter_checks["asset_level_public_ptd_impedance_count"],
                },
            },
            {
                "gate": 3,
                "name": "SYNCHRONIZED_LOAD_AND_DER_TIMESERIES",
                "result": "PASS" if (
                    reports["timeseries"]["result"] == "PASS"
                    and reports["allocation"]["result"] == "PASS"
                    and time_checks["operating_snapshots"] == time_checks["distinct_snapshot_timestamps"]
                    and time_checks["ptds"] == db["national_ptds"]
                    and time_checks["orphan_timeseries_resources"] == 0
                    and alloc_checks["duplicate_or_missing_ptd_assignments"] == 0
                    and alloc_checks["missing_registered_load_resources"] == 0
                ) else "FAIL",
                "evidence": {
                    "utc_snapshots": time_checks["operating_snapshots"],
                    "resource_timeseries_definitions": time_checks["resource_timeseries_definitions"],
                    "logical_factorized_states": time_checks["logical_factorized_resource_state_rows"],
                    "load_resources": time_checks["load_resources"],
                    "mv_load_resources": time_checks["mv_load_resources"],
                    "solar_der_resources": time_checks["solar_der_resources"],
                    "topology_consistent_ptd_assignments": alloc_checks["assignment_rows"],
                    "maximum_station_factor_error": alloc_checks["maximum_station_factor_sum_error"],
                },
            },
            {
                "gate": 4,
                "name": "THREE_PHASE_PHASE_AND_NEUTRAL_MODEL",
                "result": "PASS" if (
                    reports["phase"]["result"] == "PASS"
                    and db["lv_phase_share_ptds"] == db["national_ptds"]
                    and db["lv_grounding_ptds"] == db["national_ptds"]
                    and phase_checks["converged_ptds"] == db["national_ptds"]
                    and phase_checks["failed_ptds"] == 0
                    and phase_checks["cable_ampacity_exceedances"] == 0
                    and phase_checks["public_fuse_service_current_exceedances"] == 0
                ) else "FAIL",
                "evidence": {
                    "phase_assigned_ptds": db["lv_phase_share_ptds"],
                    "neutral_grounding_ptds": db["lv_grounding_ptds"],
                    "four_wire_converged_ptds": phase_checks["converged_ptds"],
                    "minimum_phase_voltage_pu": phase_checks["min_converged_phase_voltage_pu"],
                    "maximum_neutral_current_a": phase_checks["max_converged_neutral_current_a"],
                    "cable_ampacity_exceedances": phase_checks["cable_ampacity_exceedances"],
                },
            },
            {
                "gate": 5,
                "name": "POWER_FLOW_AND_FAULT_VALIDATION",
                "result": "PASS" if (
                    reports["equivalent_pf_fault"]["result"] == "PASS"
                    and reports["large_mv"]["result"] == "PASS"
                    and reports["small_mv"]["result"] == "PASS"
                    and eq_checks["completed_reports"] == eq_checks["requested_roots"] == 617
                    and eq_checks["failed_roots"] == 0
                    and large_checks["converged_zones"] == large_checks["candidate_zones"]
                    and large_checks["zones_within_voltage_line_transformer_limits"] == large_checks["candidate_zones"]
                    and small_checks["converged_and_within_limits"] == small_checks["selected_baseline_components"]
                    and small_checks["source_fault_cases_within_10_percent"] == small_checks["source_fault_cases"]
                    and norm_checks["mv_operating_zone_acceptance_errors"] == 0
                    and norm_checks["small_osm_operating_acceptance_errors"] == 0
                ) else "FAIL",
                "evidence": {
                    "equivalent_mv_roots_passed": eq_checks["completed_reports"],
                    "equivalent_fault_rows": eq_checks["fault_rows"],
                    "large_osm_zones_passed": large_checks["converged_zones"],
                    "small_osm_components_passed": small_checks["converged_and_within_limits"],
                    "small_component_source_fault_cases_passed": small_checks["source_fault_cases_within_10_percent"],
                    "large_zone_minimum_voltage_pu": large_checks["minimum_voltage_pu"],
                    "large_zone_maximum_line_loading_percent": large_checks["maximum_line_loading_percent"],
                    "large_zone_maximum_transformer_loading_percent": large_checks["maximum_transformer_loading_percent"],
                    "large_zone_maximum_abs_fault_error_percent": large_checks["maximum_abs_fault_error_percent"],
                },
            },
        ]

        con.execute("CREATE SCHEMA IF NOT EXISTS audit")
        con.execute("""CREATE OR REPLACE TABLE audit.complete_simulation_database_gate(
            gate INTEGER,name VARCHAR,result VARCHAR,evidence_json JSON)""")
        con.executemany(
            "INSERT INTO audit.complete_simulation_database_gate VALUES (?,?,?,?)",
            [(row["gate"], row["name"], row["result"], json.dumps(row["evidence"])) for row in gates],
        )
        unavailable = [
            ("INSTALLED_MV_CONDUCTOR_AND_CIRCUIT_IDENTITY", "INFERRED", "Public standards plus electrical design rule"),
            ("AS_OPERATED_FEEDER_BOUNDARIES_AND_SWITCH_STATES", "INFERRED", "Source-rooted radial forest and candidate normally-open points"),
            ("PTD_PHASE_AND_LV_CUSTOMER_TERMINALS", "SIMULATED", "Deterministic phase shares and synthetic radial LV feeders"),
            ("RELAY_SETTINGS_AND_SELECTIVITY", "SIMULATED", "Protection design scenario; no operator setting sheets"),
            ("CALIBRATED_ZERO_SEQUENCE_AND_GROUNDING", "SIMULATED", "Explicit sequence ratios and grounding archetypes"),
            ("CUSTOMER_METER_LOAD_AND_DER_TRACES", "INFERRED", "Public station profiles factorized to PTDs with conservation"),
        ]
        con.execute("""CREATE OR REPLACE TABLE audit.unavailable_operator_data(
            data_item VARCHAR,replacement_truth_class VARCHAR,replacement_rule VARCHAR)""")
        con.executemany("INSERT INTO audit.unavailable_operator_data VALUES (?,?,?)", unavailable)

    errors = [f"gate_{row['gate']}={row['result']}" for row in gates if row["result"] != "PASS"]
    if reports["normalized"]["result"] != "PASS":
        errors.append("normalized_network_model_validation_failed")
    if db["scenario_binding_errors"]:
        errors.append(f"scenario_binding_errors={db['scenario_binding_errors']}")
    report = {
        "result": "PASS" if not errors else "FAIL",
        "completion_scope": "Portugal public-data-based synthetic all-voltage simulation database",
        "scope_exclusion": "Does not claim an as-operated utility digital twin or undisclosed operator asset/setting data",
        "gates": gates,
        "database_summary": db,
        "truth_policy": {
            "REAL_PUBLIC": "Use the frozen public value or geometry and preserve its source.",
            "INFERRED": "Apply a deterministic documented engineering rule when a real asset value is unavailable.",
            "SIMULATED": "Use an explicit scenario/archetype only when neither public data nor defensible inference is available.",
        },
        "unavailable_operator_data": [
            {"data_item": item, "replacement_truth_class": truth, "replacement_rule": rule}
            for item, truth, rule in unavailable
        ],
        "errors": errors,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
