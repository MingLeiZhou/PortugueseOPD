#!/usr/bin/env python3
"""Create normalized operating snapshots and factorized LV resource states.

Two gross-load resources and one synthetic PV resource are defined per PTD.
Their signed injections recover the station-conserving PTD net-load scenario at
every timestamp without materializing billions of resource-state rows.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
TOPOLOGY_VERSION = "ALL_VOLTAGE_SWITCHABLE_BASE_V1"
SWITCH_SNAPSHOT = "SWITCH_SNAPSHOT_ALL_CLOSED_V1"
SCENARIO_ID = "PTD_GROSS_LOAD_PV_STATION_NET_CONSERVED_V1"
CHECK_TIMESTAMPS = (
    "2025-07-22T11:45:00+00:00",
    "2026-01-19T19:45:00+00:00",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    args = parser.parse_args()

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS operating")
        con.execute("""CREATE OR REPLACE TABLE operating.operating_snapshot AS
            SELECT 'SNAPSHOT:'||timestamp_utc AS snapshot_id,timestamp_utc,
                   ? AS topology_version_id,? AS switch_snapshot_id,
                   ? AS scenario_id,100.0::DOUBLE AS base_mva,
                   observed_station_count,observed_total_mw,
                   evidence_status AS load_shape_status
            FROM operating.national_load_shape_15min ORDER BY timestamp_utc""",
                    [TOPOLOGY_VERSION,SWITCH_SNAPSHOT,SCENARIO_ID])
        con.execute("""CREATE OR REPLACE TABLE operating.resource_timeseries_definition AS
            SELECT l.load_id AS resource_id,l.source_ptd_code AS ptd_code,
                   'GROSS_LOAD' AS variable_family,'MW_MVAR' AS unit_family,
                   'PTD_REVERSE_FLOW_SCENARIO_GROSS_LOAD_EQUAL_TWO_WAY_SPLIT' AS allocation_rule,
                   'CONSUMPTION_NEGATIVE_NETWORK_INJECTION' AS sign_convention,
                   '15_MINUTES' AS resolution,'UTC' AS timezone,
                   'SCENARIO_RECONSTRUCTION_NOT_RESOURCE_METER' AS evidence_status
            FROM equivalent.lv_loads l
            UNION ALL
            SELECT 'PV:'||a.ptd_code,a.ptd_code,'SOLAR_GENERATION','MW_MVAR',
                   'PTD_PV_CAPACITY_WEIGHTED_NATIONAL_REN_SOLAR',
                   'GENERATION_POSITIVE_NETWORK_INJECTION','15_MINUTES','UTC',
                   a.evidence_status
            FROM operating.ptd_pv_allocations a
            UNION ALL
            SELECT a.resource_id,NULL::VARCHAR,'MV_NET_LOAD','MW_MVAR',
                   'STATION_MV_RESIDUAL_ALLOCATED_BY_MV_ROOT_PTD_PEAK_PROXY',
                   'CONSUMPTION_NEGATIVE_NETWORK_INJECTION','15_MINUTES','UTC',
                   a.evidence_status
            FROM operating.mv_load_resource_allocation a""")
        con.execute("""CREATE OR REPLACE VIEW operating.resource_state_15min AS
            SELECT 'SNAPSHOT:'||r.timestamp_utc AS snapshot_id,r.timestamp_utc,
                   'LVLOAD:'||r.ptd_code||':1' AS resource_id,r.ptd_code,
                   -r.gross_p_mw/2 AS p_mw,-r.gross_q_mvar/2 AS q_mvar,
                   NULL::DOUBLE AS soc_percent,'IN_SERVICE' AS service_status,
                   r.profile_data_status,r.evidence_status AS state_evidence_status
            FROM operating.ptd_reverse_flow_scenario_15min r
            UNION ALL
            SELECT 'SNAPSHOT:'||r.timestamp_utc,r.timestamp_utc,
                   'LVLOAD:'||r.ptd_code||':2',r.ptd_code,
                   -r.gross_p_mw/2,-r.gross_q_mvar/2,NULL::DOUBLE,'IN_SERVICE',
                   r.profile_data_status,r.evidence_status
            FROM operating.ptd_reverse_flow_scenario_15min r
            UNION ALL
            SELECT 'SNAPSHOT:'||r.timestamp_utc,r.timestamp_utc,
                   'PV:'||r.ptd_code,r.ptd_code,r.pv_p_mw,0::DOUBLE,
                   NULL::DOUBLE,'IN_SERVICE',r.profile_data_status,
                   'SYNTHETIC_PTD_PV_RESOURCE_STATE'
            FROM operating.ptd_reverse_flow_scenario_15min r
            UNION ALL
            SELECT 'SNAPSHOT:'||r.timestamp_utc,r.timestamp_utc,
                   a.resource_id,NULL::VARCHAR,
                   -r.p_mw*a.station_root_fraction,-r.q_mvar*a.station_root_fraction,
                   NULL::DOUBLE,'IN_SERVICE',r.profile_data_status,
                   r.evidence_status||';'||a.evidence_status
            FROM operating.station_mv_residual_15min r
            JOIN operating.mv_load_resource_allocation a USING(station_code)""")
        con.execute("""CREATE OR REPLACE VIEW operating.resource_state_snapshot_summary AS
            WITH lv AS (
              SELECT timestamp_utc,count(*) AS ptd_count,
                     -sum(gross_p_mw)+sum(pv_p_mw) AS signed_lv_resource_p_mw,
                     -sum(gross_q_mvar) AS signed_lv_resource_q_mvar,
                     sum(gross_p_mw) AS gross_load_p_mw,sum(pv_p_mw) AS pv_p_mw,
                     sum(net_p_mw) AS ptd_net_load_p_mw,
                     max(abs((gross_p_mw-pv_p_mw)-net_p_mw)) AS max_ptd_reconciliation_error_mw
              FROM operating.ptd_reverse_flow_scenario_15min GROUP BY timestamp_utc
            ), mv AS (
              SELECT timestamp_utc,sum(p_mw) AS mv_load_p_mw,sum(q_mvar) AS mv_load_q_mvar
              FROM operating.station_mv_residual_15min GROUP BY timestamp_utc
            ), n AS (SELECT count(*) AS mv_resource_count FROM operating.mv_load_resource_allocation)
            SELECT 'SNAPSHOT:'||lv.timestamp_utc AS snapshot_id,lv.timestamp_utc,
                   lv.ptd_count,3*lv.ptd_count+n.mv_resource_count AS logical_resource_state_count,
                   lv.signed_lv_resource_p_mw-mv.mv_load_p_mw AS signed_resource_p_mw,
                   lv.signed_lv_resource_q_mvar-mv.mv_load_q_mvar AS signed_resource_q_mvar,
                   lv.gross_load_p_mw,lv.pv_p_mw,lv.ptd_net_load_p_mw,
                   mv.mv_load_p_mw,mv.mv_load_q_mvar,
                   lv.max_ptd_reconciliation_error_mw
            FROM lv JOIN mv USING(timestamp_utc) CROSS JOIN n""")

        resource_counts = dict(con.execute("""SELECT resource_type,count(*)
            FROM model.resource GROUP BY 1""").fetchall())
        snapshot_count = con.execute("SELECT count(*) FROM operating.operating_snapshot").fetchone()[0]
        definitions = con.execute("SELECT count(*) FROM operating.resource_timeseries_definition").fetchone()[0]
        lv_definitions = con.execute("""SELECT count(*) FROM operating.resource_timeseries_definition
            WHERE variable_family IN ('GROSS_LOAD','SOLAR_GENERATION')""").fetchone()[0]
        mv_definitions = con.execute("""SELECT count(*) FROM operating.resource_timeseries_definition
            WHERE variable_family='MV_NET_LOAD'""").fetchone()[0]
        ptd_count = con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0]
        sample_checks = []
        for timestamp in CHECK_TIMESTAMPS:
            row = con.execute("""SELECT s.ptd_count,s.logical_resource_state_count,
                s.signed_resource_p_mw,s.ptd_net_load_p_mw,s.mv_load_p_mw,
                s.max_ptd_reconciliation_error_mw,
                (SELECT count(*) FROM operating.resource_state_15min r
                 WHERE r.timestamp_utc=s.timestamp_utc) AS actual_resource_state_count,
                (SELECT count(DISTINCT resource_id) FROM operating.resource_state_15min r
                 WHERE r.timestamp_utc=s.timestamp_utc) AS distinct_resource_state_count,
                (SELECT max(abs(x.signed_p+y.net_p)) FROM
                   (SELECT ptd_code,sum(p_mw) signed_p
                    FROM operating.resource_state_15min WHERE timestamp_utc=? GROUP BY 1) x
                   JOIN (SELECT ptd_code,net_p_mw net_p
                    FROM operating.ptd_reverse_flow_scenario_15min WHERE timestamp_utc=?) y
                   USING(ptd_code)) AS max_resource_to_ptd_error_mw,
                (SELECT max(abs(x.allocated_p-r.p_mw)) FROM
                   (SELECT a.station_code,sum(-s2.p_mw) AS allocated_p
                    FROM operating.resource_state_15min s2
                    JOIN operating.mv_load_resource_allocation a ON s2.resource_id=a.resource_id
                    WHERE s2.timestamp_utc=? GROUP BY 1) x
                   JOIN operating.station_mv_residual_15min r USING(station_code)
                   WHERE r.timestamp_utc=?) AS max_resource_to_station_mv_error_mw,
                (SELECT max(abs(st.p_mw-lv.net_p_mw-mv.p_mw)) FROM
                   operating.station_15min_complete st
                   JOIN (SELECT assigned_station_code AS station_code,sum(net_p_mw) AS net_p_mw
                         FROM operating.ptd_reverse_flow_scenario_15min
                         WHERE timestamp_utc=?
                           AND allocation_status='DIRECT_STATION_LV_SHARE_WITH_MV_RESIDUAL'
                         GROUP BY 1) lv USING(station_code)
                   JOIN operating.station_mv_residual_15min mv USING(station_code,timestamp_utc)
                   WHERE st.timestamp_utc=?) AS max_station_lv_plus_mv_balance_error_mw
                FROM operating.resource_state_snapshot_summary s WHERE s.timestamp_utc=?""",
                [timestamp,timestamp,timestamp,timestamp,timestamp,timestamp,timestamp]).fetchone()
            if row is None:
                parser.error(f"Missing validation timestamp {timestamp}")
            sample_checks.append({
                "timestamp_utc":timestamp,
                "ptd_count":int(row[0]),
                "logical_resource_state_count":int(row[1]),
                "signed_resource_p_mw":float(row[2]),
                "ptd_net_load_p_mw":float(row[3]),
                "mv_load_p_mw":float(row[4]),
                "max_ptd_reconciliation_error_mw":float(row[5]),
                "actual_resource_state_count":int(row[6]),
                "distinct_resource_state_count":int(row[7]),
                "max_resource_to_ptd_error_mw":float(row[8]),
                "max_resource_to_station_mv_error_mw":float(row[9]),
                "max_station_lv_plus_mv_balance_error_mw":float(row[10]),
            })
        checks = {
            "operating_snapshots": snapshot_count,
            "distinct_snapshot_timestamps": con.execute("SELECT count(DISTINCT timestamp_utc) FROM operating.operating_snapshot").fetchone()[0],
            "ptds": ptd_count,
            "load_resources": int(resource_counts.get("LOAD",0)),
            "mv_load_resources": int(resource_counts.get("MV_LOAD",0)),
            "solar_der_resources": int(resource_counts.get("SOLAR_DER",0)),
            "resource_timeseries_definitions": definitions,
            "lv_resource_timeseries_definitions": lv_definitions,
            "mv_resource_timeseries_definitions": mv_definitions,
            "distinct_resource_timeseries_definitions": con.execute("SELECT count(DISTINCT resource_id) FROM operating.resource_timeseries_definition").fetchone()[0],
            "logical_factorized_resource_state_rows": int(snapshot_count*(lv_definitions+mv_definitions)),
            "orphan_timeseries_resources": con.execute("""SELECT count(*) FROM operating.resource_timeseries_definition d
                LEFT JOIN model.resource r USING(resource_id) WHERE r.resource_id IS NULL""").fetchone()[0],
            "solar_der_terminal_errors": con.execute("""SELECT count(*) FROM model.resource r
                LEFT JOIN model.terminal t ON r.resource_id=t.object_id
                WHERE r.resource_type='SOLAR_DER'
                GROUP BY r.resource_id HAVING count(t.terminal_id)<>1""").fetchall().__len__(),
            "snapshots_missing_topology_version": con.execute("""SELECT count(*) FROM operating.operating_snapshot s
                LEFT JOIN model.topology_version t USING(topology_version_id)
                WHERE t.topology_version_id IS NULL""").fetchone()[0],
            "snapshots_missing_switch_state": con.execute("""SELECT count(*) FROM operating.operating_snapshot s
                WHERE NOT EXISTS (SELECT 1 FROM operating.switch_state_snapshot x
                                  WHERE x.snapshot_id=s.switch_snapshot_id)""").fetchone()[0],
            "validation_snapshots": sample_checks,
        }

    errors = []
    if checks["operating_snapshots"] != checks["distinct_snapshot_timestamps"] or checks["operating_snapshots"] != 31492:
        errors.append("Operating snapshot calendar coverage failed")
    if checks["load_resources"] != 2*checks["ptds"] or checks["solar_der_resources"] != checks["ptds"]:
        errors.append("Expected two load and one solar-DER resource per PTD")
    if checks["mv_load_resources"] != 517 or checks["mv_resource_timeseries_definitions"] != 517:
        errors.append("Expected one MV-load resource and definition per modeled station MV root")
    if checks["lv_resource_timeseries_definitions"] != 3*checks["ptds"] or checks["resource_timeseries_definitions"] != checks["distinct_resource_timeseries_definitions"]:
        errors.append("Resource time-series definition coverage or uniqueness failed")
    for key in ("orphan_timeseries_resources","solar_der_terminal_errors",
                "snapshots_missing_topology_version","snapshots_missing_switch_state"):
        if checks[key]:
            errors.append(f"{key}={checks[key]}")
    for sample in sample_checks:
        expected_states = 3*checks["ptds"]+checks["mv_load_resources"]
        if (sample["actual_resource_state_count"] != expected_states
                or sample["distinct_resource_state_count"] != expected_states
                or sample["max_ptd_reconciliation_error_mw"] > 1e-10
                or sample["max_resource_to_ptd_error_mw"] > 1e-10
                or sample["max_resource_to_station_mv_error_mw"] > 1e-10
                or sample["max_station_lv_plus_mv_balance_error_mw"] > 1e-10
                or abs(sample["signed_resource_p_mw"]+sample["ptd_net_load_p_mw"]
                       +sample["mv_load_p_mw"]) > 1e-8):
            errors.append(f"Resource-state balance failed at {sample['timestamp_utc']}")
    report = {
        "result":"PASS" if not errors else "FAIL",
        "checks":checks,
        "scenario_id":SCENARIO_ID,
        "scope":"Factorized normalized LV load/PV and station MV residual resource states on every all-voltage operating timestamp",
        "limitations":[
            "The resource-state view is factorized and should be filtered by timestamp; its billions of logical rows are not materialized",
            "PTD gross load and PV split is a station-conserving scenario rather than resource-level measurement",
            "Two equal load locations and distal PV placement per PTD are synthetic topology assumptions",
            "PTD connected generation is not technology-specific; SOLAR_DER is an explicit PV scenario classification",
            "Station MV residual is inferred by subtracting the capacity-bounded LV share and allocated across modeled MV roots by PTD peak proxies",
            "This base builder creates LV states; run import_main_grid_generator_states.py afterwards to add the sparse archived main-grid generator window",
        ],
        "errors":errors,
    }
    path=args.database.parent/"resource_state_timeseries.validation.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=="__main__":
    raise SystemExit(main())
