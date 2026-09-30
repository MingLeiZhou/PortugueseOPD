#!/usr/bin/env python3
"""Import two archived PT60 weeks as source-backed main-grid generator states.

REN publishes national generation totals by source, not unit telemetry.  This
script reuses the frozen PT60 capacity-constrained allocation algorithm and
stores both its archived full-solar result and a combined-grid variant that
subtracts the existing low-voltage PV scenario before allocating utility solar.
No values are interpolated outside the 336 archived hourly timestamps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
RELEASE = ROOT / "data/releases/PT60-v2.1.0-rc2"
INPUTS = RELEASE / "validation/inputs"
GENERATORS = RELEASE / "scenario/generators.csv"
REFERENCE = RELEASE / "validation/static_control_reference_week.csv"
PT60_SOURCE = RELEASE / "reproduction/portuguese_hv_network/src"

SOURCE_TO_ID = {
    "Hydro": "HYDRO",
    "Solar": "SOLAR",
    "Wind": "WIND",
    "Natural Gas": "NATURAL_GAS",
    "Other Thermal": "OTHER_THERMAL",
    "Biomass": "BIOMASS",
    "Wave": "WAVE",
    "Battery Injection": "BATTERY_INJECTION",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--inputs", type=Path, default=INPUTS)
    parser.add_argument("--generators", type=Path, default=GENERATORS)
    parser.add_argument("--reference", type=Path, default=REFERENCE)
    args = parser.parse_args()

    os.environ.setdefault("MPLCONFIGDIR", "/tmp/pt60-mpl")
    sys.path.insert(0, str(PT60_SOURCE))
    from run_temporal_validation import GENERATION_GROUPS, allocate_generation

    asset_source_to_ren_group = {
        asset_source: ren_group
        for ren_group, asset_sources in GENERATION_GROUPS.items()
        for asset_source in asset_sources
    }

    generators = pd.read_csv(args.generators, low_memory=False)
    inputs = []
    for path in sorted(args.inputs.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        inputs.append((path, payload))
    if len(inputs) != 336:
        parser.error(f"Expected 336 archived inputs, found {len(inputs)}")

    with duckdb.connect(str(args.database), read_only=True) as con:
        lv_fraction = float(con.execute(
            "SELECT lv_fraction FROM operating.solar_split_assumption"
        ).fetchone()[0])
        model_generator_ids = {
            row[0] for row in con.execute(
                "SELECT resource_id FROM model.resource WHERE resource_type='GENERATOR'"
            ).fetchall()
        }
        snapshot_timestamps = {
            row[0] for row in con.execute(
                "SELECT timestamp_utc FROM operating.operating_snapshot"
            ).fetchall()
        }

    dispatch_frames: list[pd.DataFrame] = []
    source_rows: list[dict[str, object]] = []
    residual_rows: list[dict[str, object]] = []
    case_rows: list[dict[str, object]] = []
    for path, payload in inputs:
        case = payload["case"]
        observation = payload["observation"]
        case_id = str(case["case_id"])
        timestamp = pd.Timestamp(case["timestamp_utc"])
        timestamp_text = timestamp.isoformat()
        public_targets = {k: float(v) for k, v in observation["generation_by_source_mw"].items()}
        combined_targets = public_targets.copy()
        combined_targets["Solar"] = public_targets["Solar"] * (1.0 - lv_fraction)
        lv_pv_mw = public_targets["Solar"] * lv_fraction

        raw, raw_residual = allocate_generation(generators, public_targets, timestamp)
        combined, combined_residual = allocate_generation(generators, combined_targets, timestamp)
        raw_p = raw.set_index("generator_id")["scenario_p_mw"]
        raw_status = raw.set_index("generator_id")["snapshot_asset_available"]
        frame = combined[["generator_id", "generation_source", "bus_id", "nameplate_mw",
                          "snapshot_asset_available", "scenario_p_mw"]].copy()
        frame = frame.rename(columns={"scenario_p_mw": "p_mw"})
        frame["ren_generation_group"] = frame["generation_source"].map(
            asset_source_to_ren_group
        )
        if frame["ren_generation_group"].isna().any():
            missing = sorted(frame.loc[frame.ren_generation_group.isna(), "generation_source"].unique())
            raise ValueError(f"Generator sources lack a REN group: {missing}")
        frame["raw_pt60_p_mw"] = frame["generator_id"].map(raw_p)
        frame["raw_snapshot_asset_available"] = frame["generator_id"].map(raw_status)
        frame["case_id"] = case_id
        frame["timestamp_utc"] = timestamp_text
        frame["q_mvar"] = pd.NA
        frame["dispatch_basis"] = "REN_SOURCE_TOTAL_CAPACITY_CONSTRAINED_ASSET_ALLOCATION"
        frame["solar_treatment"] = "LOW_VOLTAGE_PV_SUBTRACTED_FROM_MAIN_GRID_SOLAR"
        dispatch_frames.append(frame)

        raw_res = {r["generation_source"]: r for r in raw_residual}
        combined_res = {r["generation_source"]: r for r in combined_residual}
        for source, observed_mw in public_targets.items():
            source_rows.append({
                "case_id": case_id,
                "timestamp_utc": timestamp_text,
                "generation_source": source,
                "observed_mw": observed_mw,
                "source_url": observation.get("source_url", ""),
                "ren_source_timestamp_local": observation.get("ren_source_timestamp_local", ""),
                "evidence_status": "PUBLIC_REN_NATIONAL_SOURCE_TOTAL",
            })
            rr = raw_res[source]
            cr = combined_res[source]
            residual_rows.append({
                "case_id": case_id,
                "timestamp_utc": timestamp_text,
                "generation_source": source,
                "resource_id": f"GEN:RESIDUAL:{SOURCE_TO_ID[source]}",
                "raw_pt60_residual_mw": float(rr["unmapped_residual_mw"]),
                "p_mw": float(cr["unmapped_residual_mw"]),
                "raw_pt60_mapped_mw": float(rr["mapped_asset_input_mw"]),
                "combined_main_grid_mapped_mw": float(cr["mapped_asset_input_mw"]),
                "mapped_nameplate_capacity_mw": float(cr["mapped_nameplate_capacity_mw"]),
                "evidence_status": str(cr["residual_status"]),
            })
        case_rows.append({
            "case_id": case_id,
            "timestamp_utc": timestamp_text,
            "season": case["season"],
            "week_id": case["week_id"],
            "observed_generation_total_mw": float(observation["generation_total_mw"]),
            "observed_solar_mw": public_targets["Solar"],
            "lv_pv_scenario_mw": lv_pv_mw,
            "main_grid_solar_target_mw": combined_targets["Solar"],
            "input_file": str(path.relative_to(ROOT)),
            "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })

    dispatch = pd.concat(dispatch_frames, ignore_index=True)
    sources = pd.DataFrame(source_rows)
    residuals = pd.DataFrame(residual_rows)
    cases = pd.DataFrame(case_rows)
    reference = pd.read_csv(args.reference, low_memory=False)

    raw_by_case = dispatch.groupby("case_id", as_index=False).raw_pt60_p_mw.sum()
    raw_res_by_case = residuals.groupby("case_id", as_index=False).raw_pt60_residual_mw.sum()
    reference_check = (reference[["case_id", "allocated_generation_mw",
                                  "unmapped_generation_residual_mw"]]
                       .merge(raw_by_case, on="case_id", validate="one_to_one")
                       .merge(raw_res_by_case, on="case_id", validate="one_to_one"))
    reference_check["mapped_error_mw"] = (
        reference_check.raw_pt60_p_mw - reference_check.allocated_generation_mw
    )
    reference_check["residual_error_mw"] = (
        reference_check.raw_pt60_residual_mw
        - reference_check.unmapped_generation_residual_mw
    )

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS observation")
        con.execute("CREATE SCHEMA IF NOT EXISTS operating")
        con.register("case_df", cases)
        con.register("source_df", sources)
        con.register("dispatch_df", dispatch)
        con.register("residual_df", residuals)
        con.execute("CREATE OR REPLACE TABLE observation.ren_generation_case_window AS SELECT * FROM case_df")
        con.execute("CREATE OR REPLACE TABLE observation.ren_generation_source_window AS SELECT * FROM source_df")
        con.execute("CREATE OR REPLACE TABLE operating.main_grid_generator_dispatch_window AS SELECT * FROM dispatch_df")
        con.execute("CREATE OR REPLACE TABLE operating.main_grid_generation_residual_window AS SELECT * FROM residual_df")

        con.execute("""DELETE FROM operating.resource_timeseries_definition
            WHERE resource_id IN (SELECT resource_id FROM model.resource WHERE resource_type='GENERATOR')""")
        con.execute("""INSERT INTO operating.resource_timeseries_definition
            SELECT resource_id,NULL::VARCHAR AS ptd_code,'ACTIVE_GENERATION' AS variable_family,
                   'MW_MVAR' AS unit_family,
                   CASE WHEN resource_id LIKE 'GEN:RESIDUAL:%'
                        THEN 'REN_SOURCE_TOTAL_MINUS_MAPPED_AVAILABLE_CAPACITY'
                        ELSE 'PT60_CAPACITY_CONSTRAINED_SOURCE_TOTAL_ALLOCATION' END,
                   'GENERATION_POSITIVE_NETWORK_INJECTION','SPARSE_HOURLY_TWO_ARCHIVED_WEEKS','UTC',
                   CASE WHEN resource_id LIKE 'GEN:RESIDUAL:%'
                        THEN 'UNMAPPED_NATIONAL_GENERATION_RESIDUAL_PROXY'
                        ELSE 'PUBLIC_SOURCE_TOTAL_DERIVED_UNIT_SCENARIO_NOT_UNIT_TELEMETRY' END
            FROM model.resource WHERE resource_type='GENERATOR'""")
        con.execute("""CREATE OR REPLACE VIEW operating.main_grid_generator_state_observed_window AS
            SELECT 'SNAPSHOT:'||timestamp_utc AS snapshot_id,timestamp_utc,generator_id AS resource_id,
                   NULL::VARCHAR AS ptd_code,p_mw,q_mvar,NULL::DOUBLE AS soc_percent,
                   CASE WHEN NOT snapshot_asset_available THEN 'NOT_YET_AVAILABLE'
                        WHEN bus_id IS NULL THEN 'OUT_OF_MODEL_SCOPE'
                        WHEN p_mw>0 THEN 'IN_SERVICE' ELSE 'AVAILABLE_ZERO_DISPATCH' END AS service_status,
                   'ARCHIVED_REN_HOURLY_WINDOW' AS profile_data_status,
                   dispatch_basis||';'||solar_treatment AS state_evidence_status
            FROM operating.main_grid_generator_dispatch_window
            UNION ALL
            SELECT 'SNAPSHOT:'||timestamp_utc,timestamp_utc,resource_id,NULL::VARCHAR,
                   p_mw,NULL::DOUBLE,NULL::DOUBLE,
                   CASE WHEN p_mw>0 THEN 'IN_SERVICE' ELSE 'AVAILABLE_ZERO_DISPATCH' END,
                   'ARCHIVED_REN_HOURLY_WINDOW',evidence_status
            FROM operating.main_grid_generation_residual_window""")
        con.execute("""CREATE OR REPLACE VIEW operating.resource_state_available AS
            SELECT snapshot_id,timestamp_utc,resource_id,ptd_code,p_mw,q_mvar,soc_percent,
                   service_status,profile_data_status,state_evidence_status
            FROM operating.resource_state_15min
            UNION ALL
            SELECT snapshot_id,timestamp_utc,resource_id,ptd_code,p_mw,q_mvar,soc_percent,
                   service_status,profile_data_status,state_evidence_status
            FROM operating.main_grid_generator_state_observed_window""")

        checks = {
            "archived_cases": int(len(cases)),
            "distinct_case_ids": int(cases.case_id.nunique()),
            "distinct_timestamps": int(cases.timestamp_utc.nunique()),
            "summer_cases": int((cases.season == "SUMMER").sum()),
            "winter_cases": int((cases.season == "WINTER").sum()),
            "source_observations": int(len(sources)),
            "generator_inventory_rows": int(len(generators)),
            "generator_dispatch_rows": int(len(dispatch)),
            "residual_state_rows": int(len(residuals)),
            "generator_state_rows": int(con.execute(
                "SELECT count(*) FROM operating.main_grid_generator_state_observed_window"
            ).fetchone()[0]),
            "generator_q_nonnull_rows": int(con.execute(
                "SELECT count(*) FROM operating.main_grid_generator_state_observed_window WHERE q_mvar IS NOT NULL"
            ).fetchone()[0]),
            "operating_snapshots_without_main_grid_state": int(con.execute("""SELECT count(*)
                FROM operating.operating_snapshot s WHERE NOT EXISTS (
                  SELECT 1 FROM observation.ren_generation_case_window g
                  WHERE g.timestamp_utc=s.timestamp_utc)""").fetchone()[0]),
            "timestamps_missing_operating_snapshot": int(sum(t not in snapshot_timestamps for t in cases.timestamp_utc)),
            "generator_ids_missing_model_resource": int(len(set(generators.generator_id.astype(str)) - model_generator_ids)),
            "residual_ids_missing_model_resource": int(len(set(residuals.resource_id.astype(str)) - model_generator_ids)),
            "negative_dispatch_rows": int((dispatch.p_mw < -1e-9).sum()),
            "nameplate_violation_rows": int((dispatch.p_mw > dispatch.nameplate_mw.fillna(0) + 1e-8).sum()),
            "unavailable_positive_dispatch_rows": int((~dispatch.snapshot_asset_available & dispatch.p_mw.gt(1e-9)).sum()),
            "raw_reference_max_mapped_error_mw": float(reference_check.mapped_error_mw.abs().max()),
            "raw_reference_max_residual_error_mw": float(reference_check.residual_error_mw.abs().max()),
        }
        balance = con.execute("""WITH mapped AS (
              SELECT case_id,ren_generation_group AS generation_source,sum(p_mw) AS mapped_mw
              FROM operating.main_grid_generator_dispatch_window GROUP BY 1,2
            ), residual AS (
              SELECT case_id,generation_source,p_mw AS residual_mw
              FROM operating.main_grid_generation_residual_window
            ), source AS (
              SELECT case_id,generation_source,observed_mw FROM observation.ren_generation_source_window
            ), lv AS (
              SELECT case_id,lv_pv_scenario_mw FROM observation.ren_generation_case_window
            )
            SELECT max(abs(s.observed_mw-m.mapped_mw-r.residual_mw-
                       CASE WHEN s.generation_source='Solar' THEN l.lv_pv_scenario_mw ELSE 0 END)),
                   max(abs(c.observed_generation_total_mw-z.reconstructed_mw))
            FROM source s JOIN mapped m USING(case_id,generation_source)
            JOIN residual r USING(case_id,generation_source) JOIN lv l USING(case_id)
            JOIN observation.ren_generation_case_window c USING(case_id)
            JOIN (SELECT s2.case_id,sum(m2.mapped_mw+r2.residual_mw+
                         CASE WHEN s2.generation_source='Solar' THEN l2.lv_pv_scenario_mw ELSE 0 END) reconstructed_mw
                  FROM source s2 JOIN mapped m2 USING(case_id,generation_source)
                  JOIN residual r2 USING(case_id,generation_source) JOIN lv l2 USING(case_id)
                  GROUP BY 1) z USING(case_id)""").fetchone()
        checks["max_source_balance_error_mw"] = float(balance[0])
        checks["max_national_generation_balance_error_mw"] = float(balance[1])
        checks["resource_definition_orphans"] = int(con.execute("""SELECT count(*)
            FROM operating.resource_timeseries_definition d LEFT JOIN model.resource r USING(resource_id)
            WHERE r.resource_id IS NULL""").fetchone()[0])
        checks["generator_definitions"] = int(con.execute("""SELECT count(*)
            FROM operating.resource_timeseries_definition WHERE variable_family='ACTIVE_GENERATION'""").fetchone()[0])

    errors: list[str] = []
    exact = {"archived_cases": 336, "distinct_case_ids": 336, "distinct_timestamps": 336,
             "summer_cases": 168, "winter_cases": 168, "source_observations": 2688,
             "generator_inventory_rows": 1190, "generator_dispatch_rows": 399840,
             "residual_state_rows": 2688, "generator_state_rows": 402528,
             "generator_definitions": 1198, "generator_q_nonnull_rows": 0,
             "operating_snapshots_without_main_grid_state": 31156}
    for key, expected in exact.items():
        if checks[key] != expected:
            errors.append(f"{key}={checks[key]} expected {expected}")
    for key in ("timestamps_missing_operating_snapshot", "generator_ids_missing_model_resource",
                "residual_ids_missing_model_resource", "negative_dispatch_rows",
                "nameplate_violation_rows", "unavailable_positive_dispatch_rows",
                "resource_definition_orphans"):
        if checks[key]:
            errors.append(f"{key}={checks[key]}")
    for key in ("raw_reference_max_mapped_error_mw", "raw_reference_max_residual_error_mw",
                "max_source_balance_error_mw", "max_national_generation_balance_error_mw"):
        if checks[key] > 1e-8:
            errors.append(f"{key}={checks[key]}")

    report = {
        "result": "PASS" if not errors else "FAIL",
        "checks": checks,
        "scope": "336 archived hourly PT60 cases; public REN source totals allocated to explicit generator resources with LV solar de-duplication",
        "source_artifacts": {
            "generator_inventory": str(args.generators.relative_to(ROOT)),
            "generator_inventory_sha256": hashlib.sha256(args.generators.read_bytes()).hexdigest(),
            "reference_week": str(args.reference.relative_to(ROOT)),
            "reference_week_sha256": hashlib.sha256(args.reference.read_bytes()).hexdigest(),
            "archived_input_directory": str(args.inputs.relative_to(ROOT)),
            "archived_input_files": len(inputs),
        },
        "limitations": [
            "REN values are public national source totals; per-generator P is a deterministic capacity-constrained scenario, not unit telemetry",
            "Generator Q is unknown and stored as NULL; solved reactive output is not reconstructed by this importer",
            "Only two archived seasonal weeks are populated; all other timestamps remain explicitly outside this series coverage",
            "Low-voltage PV removal follows the existing configurable solar-split scenario and is not an observed utility/embedded split",
            "Unmapped source residuals connect to one explicit synthetic national residual-bus resource per source",
        ],
        "errors": errors,
    }
    path = args.database.parent / "main_grid_generator_states.validation.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
