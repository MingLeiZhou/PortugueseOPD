#!/usr/bin/env python3
"""Build a nationwide protection-design scenario with field-level provenance.

Public E-REDES equipment values are kept separate from inferred settings and
simulation-only choices.  Nothing in this module is an operator relay inventory
or an observed setting file.  Each solved root-slice branch receives a protection
zone and a design device so that load/fault sensitivity can be screened without
silently presenting assumptions as public facts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
REPORT = ROOT / "output/all_voltage/protection_design_scenario.validation.json"
PUBLIC_PDF = ROOT / "data/raw/protection/DMA-C64-410N.pdf"
SOURCE_URL = "https://www.e-redes.pt/sites/edd/files/normative_docs/DMA-C64-410N.pdf"
SCENARIO_ID = "PROTECTION_DESIGN_ROOT_PEAK_V1"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--source-pdf", type=Path, default=PUBLIC_PDF)
    args = parser.parse_args()

    if not args.source_pdf.exists():
        raise SystemExit(f"Missing public source PDF: {args.source_pdf}")
    source_hash = sha256(args.source_pdf)

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS protection")
        con.execute("CREATE SCHEMA IF NOT EXISTS study")

        con.execute("""CREATE OR REPLACE TABLE protection.data_rule(
            rule_id VARCHAR PRIMARY KEY,
            target_fields VARCHAR,
            value_origin VARCHAR,
            rule_text VARCHAR,
            rationale VARCHAR,
            source_ref VARCHAR,
            uncertainty_note VARCHAR
        )""")
        rules = [
            ("RULE_PUBLIC_MV_SWITCHGEAR", "rated_voltage_kv,rated_continuous_current_a,public_short_time_current_ka,public_peak_withstand_current_ka",
             "PUBLIC_STANDARD", "Copy the voltage-class values printed in E-REDES DMA-C64-410/N tables 1, 4, 5 and 6.",
             "These are Portuguese distribution switchgear requirements and are stronger evidence than a generic catalog.",
             f"{SOURCE_URL}#pages=7,12", "A type standard is not an installed-asset inventory."),
            ("RULE_PUBLIC_LV_FUSE", "phase_pickup_current_a",
             "PUBLIC_STANDARD_TYPE_PLUS_INFERRED_ASSIGNMENT", "Use fuse_service_current_a from the selected E-REDES DIT-C14-100/N cable type.",
             "The public table coordinates each standard LV cable type with a service-fuse current.",
             "parameter.lv_cable_catalog", "The cable type assignment and feeder topology remain simulation designs."),
            ("RULE_DERIVED_LOAD_CURRENT", "peak_operating_current_a",
             "DERIVED_FROM_SIMULATION", "Use the validated per-root annual-peak branch current; divide equivalent LV current by the designed parallel-feeder count.",
             "Protection pickup must be referenced to the same conductor or transformer side as the protective device.",
             "study.equivalent_mv_root_peak_batch_*", "The operating state and much of the distribution topology are synthetic scenarios."),
            ("RULE_DERIVED_FAULT_CURRENT", "min_2ph_fault_current_a,min_1ph_ground_fault_current_a,max_3ph_fault_current_a",
             "DERIVED_FROM_SIMULATION", "Join each protected branch to the maximum/minimum IEC 60909 result at its downstream bus; refer LV faults through transformer voltage ratio when protection is on the MV primary.",
             "This gives the current seen by the assigned device rather than the raw downstream-side current.",
             "study.equivalent_mv_root_peak_batch_fault_results", "Sequence parameters and source strengths remain explicit engineering proxies."),
            ("RULE_PHASE_PICKUP", "phase_pickup_current_a",
             "INFERRED_ENGINEERING_VALUE", "For numerical relays set pickup to max(5 A, 1.25 times annual-peak current); for MV transformer fuses select the next preferred fuse size above the same load margin.",
             "The 25 percent margin avoids operation at the validated annual-peak point while retaining fault sensitivity.",
             "PROTECTION_DESIGN_RULE", "Must be replaced by operator coordination settings when available."),
            ("RULE_GROUND_PICKUP", "ground_pickup_current_a",
             "SIMULATED_ENGINEERING_VALUE", "For numerical relays set pickup to max(10 A, 0.2 times phase pickup).",
             "No residual-current measurements or operator ground settings are public; the floor avoids a zero or unrealistically small setting.",
             "PROTECTION_DESIGN_RULE", "Sensitivity is screened, but nuisance-trip/security performance is uncalibrated."),
            ("RULE_CT_RATIO", "ct_primary_a,ct_secondary_a",
             "INFERRED_ENGINEERING_VALUE", "Choose the next preferred CT primary rating not below 1.25 times peak load; use a 1 A secondary.",
             "This prevents normal peak load from exceeding the CT primary rating and supports a reproducible relay model.",
             "PROTECTION_DESIGN_RULE", "CT class, burden, saturation and actual installed ratio are unknown."),
            ("RULE_TIME_CURVE", "phase_curve,time_multiplier,ground_curve,ground_time_multiplier",
             "SIMULATED_ENGINEERING_VALUE", "Use IEC standard inverse with TMS 0.10 for phase and ground relay screening.",
             "A deterministic curve is required to calculate indicative times; coordination is not claimed.",
             "IEC_STANDARD_INVERSE_SCREENING_SCENARIO", "Actual curve family, grading margin and setting group are unavailable."),
            ("RULE_BREAKING_RATING", "selected_interrupting_current_ka",
             "PUBLIC_STANDARD_FLOOR_PLUS_SIMULATED_UPLIFT", "Start with the E-REDES short-time withstand for 10/15/30 kV and select the next preferred kA class at or above 1.25 times modeled maximum 3-phase fault current.",
             "The public value remains visible as a floor; a separate uplift avoids silently accepting an inadequate modeled duty.",
             f"{SOURCE_URL}#page=12", "Selected uplift is a design scenario, not evidence of installed equipment."),
            ("RULE_20KV_INTERPOLATION", "20_kV_equipment_values",
             "SIMULATED_ENGINEERING_VALUE", "Use a 24 kV class, 400 A cell and 12.5 kA/3 s, 31.5 kA peak values for the two 20 kV PTDs.",
             "DMA-C64-410/N does not list a 20 kV network; the values are an explicit interpolation between its 17.5 and 36 kV classes.",
             "PROTECTION_DESIGN_RULE", "Replace if a Portuguese 20 kV equipment specification is found."),
        ]
        con.executemany("INSERT INTO protection.data_rule VALUES (?,?,?,?,?,?,?)", rules)

        con.execute("""CREATE OR REPLACE TABLE protection.mv_switchgear_standard(
            standard_row_id VARCHAR PRIMARY KEY,
            network_nominal_voltage_kv DOUBLE,
            rated_voltage_kv DOUBLE,
            rated_frequency_hz DOUBLE,
            phase_count INTEGER,
            rated_continuous_current_a DOUBLE,
            public_short_time_current_ka DOUBLE,
            short_time_duration_s DOUBLE,
            public_peak_withstand_current_ka DOUBLE,
            internal_arc_current_ka DOUBLE,
            internal_arc_duration_s DOUBLE,
            neutral_regime VARCHAR,
            source_document VARCHAR,
            source_pages VARCHAR,
            source_url VARCHAR,
            source_sha256 VARCHAR,
            evidence_status VARCHAR
        )""")
        standards = [
            ("DMA_C64_410_10KV", 10.0, 12.0, 50.0, 3, 400.0, 16.0, 3.0, 40.0, 16.0, 1.0,
             "EARTHED_BY_IMPEDANCE_LIMITED_TO_1000A_OR_300A", "DMA-C64-410/N Ed.4", "7,8,11,12", SOURCE_URL, source_hash, "PUBLIC_EREDES_TYPE_STANDARD"),
            ("DMA_C64_410_15KV", 15.0, 17.5, 50.0, 3, 400.0, 12.5, 3.0, 31.5, 12.5, 1.0,
             "EARTHED_BY_IMPEDANCE_LIMITED_TO_1000A_OR_300A_OR_ISOLATED", "DMA-C64-410/N Ed.4", "7,8,11,12", SOURCE_URL, source_hash, "PUBLIC_EREDES_TYPE_STANDARD"),
            ("SIM_20KV_INTERPOLATED", 20.0, 24.0, 50.0, 3, 400.0, 12.5, 3.0, 31.5, 12.5, 1.0,
             "UNKNOWN_SIMULATION_ASSUMPTION", "Protection design rule", "N/A", SOURCE_URL, source_hash, "SIMULATED_20KV_INTERPOLATION_NOT_PUBLIC_VALUE"),
            ("DMA_C64_410_30KV", 30.0, 36.0, 50.0, 3, 400.0, 8.0, 3.0, 20.0, 8.0, 1.0,
             "EARTHED_BY_IMPEDANCE_LIMITED_TO_1000A_OR_300A_OR_ISOLATED", "DMA-C64-410/N Ed.4", "7,8,11,12", SOURCE_URL, source_hash, "PUBLIC_EREDES_TYPE_STANDARD"),
        ]
        con.executemany("INSERT INTO protection.mv_switchgear_standard VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", standards)

        # Factorized branch input: one annual-root-peak operating current and one
        # set of downstream max/min fault duties for every solved branch.
        con.execute("""CREATE OR REPLACE TEMP TABLE protection_branch_base AS
            WITH operating AS (
                SELECT l.mv_root_bus,l.timestamp_utc,l.line_id AS branch_id,
                       greatest(l.i_from_ka,l.i_to_ka)*1000 AS raw_peak_current_a
                FROM study.equivalent_mv_root_peak_batch_line_results l
                UNION ALL
                SELECT t.mv_root_bus,t.timestamp_utc,t.transformer_id,
                       CASE WHEN t.transformer_id LIKE 'BRIDGE:MVROOT:%'
                            THEN t.i_lv_ka*1000 ELSE t.i_hv_ka*1000 END
                FROM study.equivalent_mv_root_peak_batch_transformer_results t
            ), faults AS (
                SELECT mv_root_bus,bus_id,
                       max(ikss_ka) FILTER (WHERE fault_type='3ph' AND "case"='max')*1000 AS raw_max_3ph_a,
                       max(ikss_ka) FILTER (WHERE fault_type='2ph' AND "case"='min')*1000 AS raw_min_2ph_a,
                       max(ikss_ka) FILTER (WHERE fault_type='1ph' AND "case"='min')*1000 AS raw_min_1ph_a
                FROM study.equivalent_mv_root_peak_batch_fault_results
                GROUP BY 1,2
            ), joined AS (
                SELECT o.mv_root_bus,o.timestamp_utc,b.*,
                       o.raw_peak_current_a,f.raw_max_3ph_a,f.raw_min_2ph_a,f.raw_min_1ph_a,
                       CASE WHEN b.branch_id LIKE 'LVLINE:%'
                            THEN coalesce(d.feeder_count,1) ELSE 1 END AS parallel_device_count,
                       d.cable_designation,c.fuse_service_current_a AS public_lv_fuse_current_a,
                       CASE WHEN b.branch_id LIKE 'BRIDGE:MVROOT:%' THEN b.to_kv ELSE b.from_kv END AS protection_voltage_kv,
                       CASE WHEN b.branch_id LIKE 'PTDTRAFO:%' THEN b.to_kv/b.from_kv ELSE 1.0 END AS fault_referral_factor
                FROM operating o
                JOIN equivalent.branches b ON b.branch_id=o.branch_id
                JOIN faults f ON f.mv_root_bus=o.mv_root_bus AND f.bus_id=b.to_bus
                LEFT JOIN phase.lv_feeder_root_peak_design_scenario d
                  ON b.branch_id LIKE 'LVLINE:'||d.ptd_code||':%'
                LEFT JOIN parameter.lv_cable_catalog c ON c.designation=d.cable_designation
            )
            SELECT *,raw_peak_current_a/parallel_device_count AS peak_operating_current_a,
                   raw_max_3ph_a*fault_referral_factor/parallel_device_count AS max_3ph_fault_current_a,
                   raw_min_2ph_a*fault_referral_factor/parallel_device_count AS min_2ph_fault_current_a,
                   raw_min_1ph_a*fault_referral_factor/parallel_device_count AS min_1ph_ground_fault_current_a
            FROM joined""")

        con.execute("""CREATE OR REPLACE TABLE protection.protection_zone AS
            SELECT ?||':ZONE:'||branch_id AS zone_id,? AS scenario_id,mv_root_bus,
                   branch_id AS protected_equipment_id,from_bus,to_bus,
                   upper(asset_kind) AS protected_equipment_kind,protection_voltage_kv,
                   CASE WHEN branch_id LIKE 'BRIDGE:MVROOT:%' THEN 'TRANSFORMER_MV_SIDE'
                        WHEN branch_id LIKE 'PTDTRAFO:%' THEN 'TRANSFORMER_MV_PRIMARY'
                        ELSE 'BRANCH_UPSTREAM_END' END AS protection_location,
                   parallel_device_count,
                   'DOWNSTREAM_BUS_FAULT_ZONE' AS zone_definition,
                   'SIMULATION_PROTECTION_ZONE_NOT_OPERATOR_ASSET' AS evidence_status
            FROM protection_branch_base""", [SCENARIO_ID, SCENARIO_ID])

        con.execute("""CREATE OR REPLACE TEMP TABLE protection_device_pre AS
            SELECT b.*,
                   CASE WHEN branch_id LIKE 'LVLINE:%' THEN 'LV_FEEDER_FUSE'
                        WHEN branch_id LIKE 'PTDTRAFO:%' THEN 'MV_TRANSFORMER_FUSE_SWITCH'
                        ELSE 'NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER' END AS device_type,
                   s.standard_row_id,s.rated_voltage_kv AS public_rated_voltage_kv,
                   s.rated_continuous_current_a AS public_continuous_current_a,
                   s.public_short_time_current_ka,s.public_peak_withstand_current_ka,
                   CASE WHEN branch_id LIKE 'LVLINE:%' THEN public_lv_fuse_current_a
                        WHEN branch_id LIKE 'PTDTRAFO:%' THEN
                          CASE WHEN 1.25*peak_operating_current_a<=2 THEN 2
                               WHEN 1.25*peak_operating_current_a<=4 THEN 4
                               WHEN 1.25*peak_operating_current_a<=6.3 THEN 6.3
                               WHEN 1.25*peak_operating_current_a<=10 THEN 10
                               WHEN 1.25*peak_operating_current_a<=16 THEN 16
                               WHEN 1.25*peak_operating_current_a<=20 THEN 20
                               WHEN 1.25*peak_operating_current_a<=25 THEN 25
                               WHEN 1.25*peak_operating_current_a<=31.5 THEN 31.5
                               WHEN 1.25*peak_operating_current_a<=40 THEN 40
                               WHEN 1.25*peak_operating_current_a<=50 THEN 50
                               WHEN 1.25*peak_operating_current_a<=63 THEN 63
                               WHEN 1.25*peak_operating_current_a<=80 THEN 80
                               WHEN 1.25*peak_operating_current_a<=100 THEN 100
                               WHEN 1.25*peak_operating_current_a<=125 THEN 125
                               ELSE ceil(1.25*peak_operating_current_a/25)*25 END
                        ELSE greatest(coalesce(s.rated_continuous_current_a,0),
                                      CASE WHEN 1.25*peak_operating_current_a<=25 THEN 25
                                           WHEN 1.25*peak_operating_current_a<=50 THEN 50
                                           WHEN 1.25*peak_operating_current_a<=75 THEN 75
                                           WHEN 1.25*peak_operating_current_a<=100 THEN 100
                                           WHEN 1.25*peak_operating_current_a<=150 THEN 150
                                           WHEN 1.25*peak_operating_current_a<=200 THEN 200
                                           WHEN 1.25*peak_operating_current_a<=300 THEN 300
                                           WHEN 1.25*peak_operating_current_a<=400 THEN 400
                                           WHEN 1.25*peak_operating_current_a<=600 THEN 600
                                           WHEN 1.25*peak_operating_current_a<=800 THEN 800
                                           WHEN 1.25*peak_operating_current_a<=1000 THEN 1000
                                           WHEN 1.25*peak_operating_current_a<=1200 THEN 1200
                                           WHEN 1.25*peak_operating_current_a<=1600 THEN 1600
                                           WHEN 1.25*peak_operating_current_a<=2000 THEN 2000
                                           WHEN 1.25*peak_operating_current_a<=3000 THEN 3000
                                           WHEN 1.25*peak_operating_current_a<=4000 THEN 4000
                                           ELSE 5000 END) END AS selected_continuous_or_fuse_current_a,
                   CASE WHEN protection_voltage_kv<1 THEN NULL
                        ELSE CASE WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=8 THEN 8
                                  WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=12.5 THEN 12.5
                                  WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=16 THEN 16
                                  WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=20 THEN 20
                                  WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=25 THEN 25
                                  WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=31.5 THEN 31.5
                                  WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=40 THEN 40
                                  WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=50 THEN 50
                                  WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=63 THEN 63
                                  WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=80 THEN 80
                                  WHEN greatest(coalesce(s.public_short_time_current_ka,0),1.25*max_3ph_fault_current_a/1000)<=100 THEN 100
                                  ELSE ceil(1.25*max_3ph_fault_current_a/1000/10)*10 END END AS selected_interrupting_current_ka
            FROM protection_branch_base b
            LEFT JOIN protection.mv_switchgear_standard s
              ON s.network_nominal_voltage_kv=b.protection_voltage_kv""")

        con.execute("""CREATE OR REPLACE TABLE protection.protective_device AS
            SELECT ?||':DEVICE:'||branch_id AS device_id,?||':ZONE:'||branch_id AS zone_id,
                   ? AS scenario_id,'SWITCH:'||branch_id||':UPSTREAM' AS switching_device_id,
                   device_type,protection_voltage_kv,
                   coalesce(public_rated_voltage_kv, CASE WHEN protection_voltage_kv<1 THEN 1.0 END) AS rated_voltage_kv,
                   selected_continuous_or_fuse_current_a AS rated_continuous_or_fuse_current_a,
                   public_continuous_current_a,public_short_time_current_ka,
                   public_peak_withstand_current_ka,selected_interrupting_current_ka,
                   standard_row_id,cable_designation,
                   CASE WHEN protection_voltage_kv<1 THEN 'PUBLIC_LV_CABLE_FUSE_VALUE_WITH_SIMULATED_CABLE_ASSIGNMENT'
                        WHEN selected_interrupting_current_ka>public_short_time_current_ka
                        THEN 'PUBLIC_STANDARD_FLOOR_WITH_SIMULATED_FAULT_DUTY_UPLIFT'
                        ELSE 'PUBLIC_STANDARD_TYPE_WITH_SIMULATED_ASSET_ASSIGNMENT' END AS rating_origin,
                   'SIMULATION_PROTECTION_DESIGN_NOT_OPERATOR_ASSET' AS evidence_status
            FROM protection_device_pre""", [SCENARIO_ID, SCENARIO_ID, SCENARIO_ID])

        con.execute("""CREATE OR REPLACE TEMP TABLE setting_pre AS
            SELECT p.*,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER'
                        THEN greatest(5.0,1.25*peak_operating_current_a)
                        ELSE selected_continuous_or_fuse_current_a END AS phase_pickup_current_a
            FROM protection_device_pre p""")

        con.execute("""CREATE OR REPLACE TABLE protection.setting_group AS
            SELECT ?||':SETTING:'||branch_id AS setting_group_id,
                   ?||':DEVICE:'||branch_id AS device_id,? AS scenario_id,
                   peak_operating_current_a,phase_pickup_current_a,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER'
                        THEN greatest(10.0,0.2*phase_pickup_current_a) END AS ground_pickup_current_a,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER'
                        THEN 'IEC_STANDARD_INVERSE' ELSE 'MANUFACTURER_FUSE_CURVE_REQUIRED' END AS phase_curve,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER' THEN 0.10 END AS phase_time_multiplier,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER' THEN 'IEC_STANDARD_INVERSE' END AS ground_curve,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER' THEN 0.10 END AS ground_time_multiplier,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER'
                              AND 0.8*min_2ph_fault_current_a>=8*phase_pickup_current_a
                        THEN 0.8*min_2ph_fault_current_a END AS instantaneous_phase_pickup_a,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER'
                              AND 0.8*min_2ph_fault_current_a>=8*phase_pickup_current_a
                        THEN 'ENABLED_FROM_MIN_FAULT_AND_LOAD_MARGIN'
                        WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER'
                        THEN 'DISABLED_INSUFFICIENT_SELECTIVITY_MARGIN' END AS instantaneous_status,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER' THEN
                     CASE WHEN 1.25*peak_operating_current_a<=25 THEN 25
                          WHEN 1.25*peak_operating_current_a<=50 THEN 50
                          WHEN 1.25*peak_operating_current_a<=75 THEN 75
                          WHEN 1.25*peak_operating_current_a<=100 THEN 100
                          WHEN 1.25*peak_operating_current_a<=150 THEN 150
                          WHEN 1.25*peak_operating_current_a<=200 THEN 200
                          WHEN 1.25*peak_operating_current_a<=300 THEN 300
                          WHEN 1.25*peak_operating_current_a<=400 THEN 400
                          WHEN 1.25*peak_operating_current_a<=600 THEN 600
                          WHEN 1.25*peak_operating_current_a<=800 THEN 800
                          WHEN 1.25*peak_operating_current_a<=1000 THEN 1000
                          WHEN 1.25*peak_operating_current_a<=1200 THEN 1200
                          WHEN 1.25*peak_operating_current_a<=1600 THEN 1600
                          WHEN 1.25*peak_operating_current_a<=2000 THEN 2000
                          WHEN 1.25*peak_operating_current_a<=3000 THEN 3000
                          WHEN 1.25*peak_operating_current_a<=4000 THEN 4000
                          ELSE 5000 END END AS ct_primary_a,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER' THEN 1.0 END AS ct_secondary_a,
                   CASE WHEN device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER'
                        THEN 'INFERRED_PICKUPS_SIMULATED_TIME_CURVES_NOT_OPERATOR_SETTINGS'
                        WHEN device_type='LV_FEEDER_FUSE'
                        THEN 'PUBLIC_FUSE_TYPE_VALUE_SIMULATED_ASSET_ASSIGNMENT'
                        ELSE 'INFERRED_MV_FUSE_RATING_NOT_OPERATOR_ASSET' END AS evidence_status
            FROM setting_pre""", [SCENARIO_ID, SCENARIO_ID, SCENARIO_ID])

        con.execute("""CREATE OR REPLACE TABLE study.protection_sensitivity_screen AS
            SELECT z.mv_root_bus,z.protected_equipment_id,z.protected_equipment_kind,
                   d.device_id,d.device_type,z.protection_voltage_kv,z.parallel_device_count,
                   b.timestamp_utc,s.peak_operating_current_a,s.phase_pickup_current_a,s.ground_pickup_current_a,
                   b.min_2ph_fault_current_a,b.min_1ph_ground_fault_current_a,b.max_3ph_fault_current_a,
                   b.min_2ph_fault_current_a/nullif(s.phase_pickup_current_a,0) AS phase_sensitivity_ratio,
                   b.min_1ph_ground_fault_current_a/nullif(s.ground_pickup_current_a,0) AS ground_sensitivity_ratio,
                   b.max_3ph_fault_current_a/1000 AS max_3ph_fault_current_ka,
                   d.public_short_time_current_ka,d.selected_interrupting_current_ka,
                   s.peak_operating_current_a<=d.rated_continuous_or_fuse_current_a AS normal_load_within_selected_rating,
                   phase_sensitivity_ratio>=1.5 AS phase_sensitivity_pass,
                   CASE WHEN s.ground_pickup_current_a IS NULL THEN NULL
                        ELSE ground_sensitivity_ratio>=1.5 END AS ground_sensitivity_pass,
                   CASE WHEN d.selected_interrupting_current_ka IS NULL THEN NULL
                        ELSE d.selected_interrupting_current_ka>=1.25*max_3ph_fault_current_ka END AS selected_interrupting_margin_pass,
                   CASE WHEN d.public_short_time_current_ka IS NULL THEN NULL
                        ELSE d.public_short_time_current_ka>=max_3ph_fault_current_ka END AS public_standard_fault_duty_pass,
                   CASE WHEN d.device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER'
                              AND phase_sensitivity_ratio>1
                        THEN s.phase_time_multiplier*0.14/(pow(phase_sensitivity_ratio,0.02)-1) END AS indicative_phase_trip_time_s,
                   CASE WHEN d.device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER'
                              AND ground_sensitivity_ratio>1
                        THEN s.ground_time_multiplier*0.14/(pow(ground_sensitivity_ratio,0.02)-1) END AS indicative_ground_trip_time_s,
                   'SENSITIVITY_ONLY_NO_SELECTIVITY_OR_OPERATOR_SETTING_CLAIM' AS study_status
            FROM protection.protection_zone z
            JOIN protection.protective_device d USING(zone_id)
            JOIN protection.setting_group s USING(device_id)
            JOIN protection_branch_base b ON b.branch_id=z.protected_equipment_id""")

        cursor = con.execute("""SELECT
            (SELECT count(*) FROM protection.protection_zone) AS zones,
            (SELECT count(*) FROM protection.protective_device) AS devices,
            (SELECT count(*) FROM protection.setting_group) AS settings,
            (SELECT count(*) FROM study.protection_sensitivity_screen) AS screens,
            (SELECT count(*) FROM protection.protective_device WHERE device_type='NUMERICAL_OVERCURRENT_RELAY_CIRCUIT_BREAKER') AS relays,
            (SELECT count(*) FROM protection.protective_device WHERE device_type='MV_TRANSFORMER_FUSE_SWITCH') AS mv_fuses,
            (SELECT count(*) FROM protection.protective_device WHERE device_type='LV_FEEDER_FUSE') AS lv_fuses
        """)
        counts = dict(zip([item[0] for item in cursor.description], cursor.fetchone()))
        cursor = con.execute("""SELECT
            count(*) FILTER (WHERE NOT normal_load_within_selected_rating) AS load_rating_failures,
            count(*) FILTER (WHERE NOT phase_sensitivity_pass) AS phase_sensitivity_failures,
            count(*) FILTER (WHERE ground_sensitivity_pass=false) AS ground_sensitivity_failures,
            count(*) FILTER (WHERE selected_interrupting_margin_pass=false) AS selected_breaking_failures,
            count(*) FILTER (WHERE public_standard_fault_duty_pass=false) AS public_standard_fault_duty_exceedances,
            count(*) FILTER (WHERE selected_interrupting_current_ka>public_short_time_current_ka) AS simulated_breaking_rating_uplifts,
            min(phase_sensitivity_ratio) AS min_phase_sensitivity_ratio,
            min(ground_sensitivity_ratio) AS min_ground_sensitivity_ratio,
            max(max_3ph_fault_current_ka) AS max_fault_duty_ka
            FROM study.protection_sensitivity_screen""")
        metrics = dict(zip([item[0] for item in cursor.description], cursor.fetchone()))
        cursor = con.execute("""SELECT
            (SELECT count(*) FROM (SELECT zone_id,count(*) n FROM protection.protection_zone GROUP BY 1 HAVING n<>1)) AS duplicate_zones,
            (SELECT count(*) FROM (SELECT device_id,count(*) n FROM protection.protective_device GROUP BY 1 HAVING n<>1)) AS duplicate_devices,
            (SELECT count(*) FROM study.protection_sensitivity_screen WHERE min_2ph_fault_current_a IS NULL OR max_3ph_fault_current_a IS NULL) AS missing_fault_inputs,
            (SELECT count(*) FROM protection.protective_device WHERE device_type='LV_FEEDER_FUSE' AND rated_continuous_or_fuse_current_a IS NULL) AS missing_lv_public_fuse_values,
            (SELECT count(*) FROM protection.setting_group WHERE phase_pickup_current_a IS NULL OR phase_pickup_current_a<=0) AS invalid_phase_pickups
        """)
        integrity = dict(zip([item[0] for item in cursor.description], cursor.fetchone()))

    errors = []
    expected = 290353
    if any(counts[key] != expected for key in ("zones", "devices", "settings", "screens")):
        errors.append("Protection object coverage does not match the 290,353 solved root-slice branches")
    if counts["relays"] + counts["mv_fuses"] + counts["lv_fuses"] != expected:
        errors.append("Protective-device type partition is incomplete")
    if any(integrity.values()):
        errors.append("Protection keys or required inputs failed integrity checks")

    report = {
        "result": "PASS" if not errors else "FAIL",
        "scenario_id": SCENARIO_ID,
        "public_source": {
            "document": "E-REDES DMA-C64-410/N Ed.4",
            "url": SOURCE_URL,
            "local_file": str(args.source_pdf.relative_to(ROOT)),
            "sha256": source_hash,
            "extracted_public_facts": {
                "network_voltage_kv": [10, 15, 30],
                "switchgear_rated_voltage_kv": [12, 17.5, 36],
                "rated_continuous_current_a": 400,
                "short_time_withstand_ka_3s": [16, 12.5, 8],
                "peak_withstand_ka": [40, 31.5, 20],
                "transformer_fuse_switch_trip_coil_v": 230,
            },
        },
        "coverage": counts,
        "screening_metrics": metrics,
        "integrity": integrity,
        "errors": errors,
        "interpretation": [
            "Public values are type standards, not a list of installed Portuguese relay or breaker assets.",
            "Annual-peak currents and max/min fault currents are derived from the validated synthetic root-peak network.",
            "Pickup, CT and time-curve values are inferred or simulated under named rules in protection.data_rule.",
            "Sensitivity failures and public-standard duty exceedances are retained as study findings; values are not silently changed to force a pass.",
            "The selected interrupting rating is a design scenario with 25 percent fault-duty margin.",
            "Selectivity, transformer inrush, CT saturation, fuse manufacturer curves and actual operator settings still require further data or studies.",
        ],
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
