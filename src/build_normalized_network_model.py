#!/usr/bin/env python3
"""Build normalized object, terminal, topology and parameter-binding tables.

The solvable equivalent network and OSM distribution candidate graph remain
separate network layers. This script makes every edge endpoint explicit as a
terminal, assigns versioned topological nodes/islands, and records missing
parameter bindings. It does not promote spatial candidates to verified links.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
EQ_VERSION = "ALL_VOLTAGE_EQUIVALENT_V1"
CAND_VERSION = "OSM_DISTRIBUTION_CANDIDATE_V1"
ATTACH_VERSION = "OSM_PTD_ATTACHMENT_CANDIDATE_V1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    args = parser.parse_args()

    # Find connected components of the solver-ready equivalent bus/branch graph.
    with duckdb.connect(str(args.database), read_only=True) as con:
        bus_ids = np.asarray([r[0] for r in con.execute(
            "SELECT bus_id FROM equivalent.buses ORDER BY bus_id").fetchall()], dtype=object)
        branch_ends = con.execute(
            "SELECT from_bus,to_bus FROM equivalent.branches").fetchall()
        generator_buses = {r[0] for r in con.execute(
            "SELECT DISTINCT bus_id FROM equivalent.base_generators WHERE bus_id IS NOT NULL").fetchall()}
        source_buses = {r[0] for r in con.execute("""SELECT hv_bus_id
            FROM candidate.station_sources WHERE hv_bus_id IS NOT NULL
            UNION SELECT bus_id FROM equivalent.buses
            WHERE evidence_status='DIRECT_EREDES' AND voltage_kv>=60""").fetchall()}
    bus_index = {bus:i for i,bus in enumerate(bus_ids)}
    a = np.fromiter((bus_index[x] for x,_ in branch_ends), dtype=np.int64, count=len(branch_ends))
    b = np.fromiter((bus_index[y] for _,y in branch_ends), dtype=np.int64, count=len(branch_ends))
    graph = coo_matrix((np.ones(2*len(a),dtype=np.int8),
                        (np.r_[a,b],np.r_[b,a])),shape=(len(bus_ids),len(bus_ids))).tocsr()
    island_count, labels = connected_components(graph,directed=False)
    members: dict[int,list[int]] = {}
    for i,label in enumerate(labels):
        members.setdefault(int(label),[]).append(i)
    island_for_label = {
        label:"ISLAND:EQ:"+min(str(bus_ids[i]) for i in indices)
        for label,indices in members.items()
    }
    island_ids = np.asarray([island_for_label[int(label)] for label in labels],dtype=object)
    source_candidates = generator_buses|source_buses
    island_source_count = {
        island_for_label[label]:sum(str(bus_ids[i]) in source_candidates for i in indices)
        for label,indices in members.items()
    }
    island_rows = [(island,len(indices),island_source_count[island],island_source_count[island]>0,
                    "DERIVED_FROM_EQUIVALENT_BRANCH_CONNECTIVITY_ALL_BRANCHES_IN_SERVICE")
                   for label,indices in members.items()
                   for island in [island_for_label[label]]]
    membership_arrow = pa.table({
        "topology_version_id":pa.array([EQ_VERSION]*len(bus_ids)),
        "topological_node_id":pa.array(["TN:EQ:"+str(x) for x in bus_ids]),
        "island_id":pa.array(island_ids),
    })

    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS model")
        has_switching = bool(con.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='model' AND table_name='switching_device'""").fetchone()[0])
        has_lv_root_peak_design = bool(con.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='phase' AND table_name='lv_feeder_root_peak_design_scenario'""").fetchone()[0])
        has_ptd_root_peak_design = bool(con.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='parameter' AND table_name='ptd_transformer_root_peak_design_scenario'""").fetchone()[0])
        has_mv_root_design = bool(con.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='parameter' AND table_name='mv_root_transformer_design_scenario'""").fetchone()[0])
        has_lv_route_geometry = bool(con.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='phase' AND table_name='lv_feeder_route_scenario'""").fetchone()[0])
        has_mv_validated_operating = bool(con.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='scenario' AND table_name='mv_validated_zone_design'""").fetchone()[0])
        has_small_osm_validated_operating = bool(con.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='scenario' AND table_name='osm_small_component_validated_design'""").fetchone()[0])
        lv_design_table = ("phase.lv_feeder_root_peak_design_scenario" if has_lv_root_peak_design
                           else "phase.lv_feeder_design_scenario")
        con.execute("""CREATE OR REPLACE TABLE model.network_layer (
            network_layer VARCHAR PRIMARY KEY, layer_purpose VARCHAR,
            electrically_solved BOOLEAN, evidence_status VARCHAR)""")
        con.executemany("INSERT INTO model.network_layer VALUES (?,?,?,?)", [
            ("SOLVABLE_EQUIVALENT","All-voltage simulation graph",True,
             "MIXED_PUBLIC_AND_EXPLICIT_SYNTHETIC_OBJECTS"),
            ("OSM_DISTRIBUTION_CANDIDATE","Shared-node OSM MV/LV geometry",False,
             "PUBLIC_OSM_TOPOLOGY_WITH_UNVERIFIED_SOURCE_AND_PTD_TERMINALS"),
            ("OSM_PTD_ATTACHMENT_CANDIDATE","OSM MV graph with constrained inferred PTD taps",False,
             "PUBLIC_OSM_GEOMETRY_AND_PUBLIC_PTD_POINTS_WITH_UNVERIFIED_INFERRED_TERMINALS"),
        ])
        con.execute("""CREATE OR REPLACE TABLE model.topology_version (
            topology_version_id VARCHAR PRIMARY KEY,network_layer VARCHAR,
            topology_basis VARCHAR,switch_state_basis VARCHAR,valid_at_utc VARCHAR,
            evidence_status VARCHAR)""")
        con.executemany("INSERT INTO model.topology_version VALUES (?,?,?,?,?,?)", [
            (EQ_VERSION,"SOLVABLE_EQUIVALENT","EQUIVALENT_BUSES_AND_BRANCHES",
             "NO_SWITCH_OBJECTS_ALL_PERSISTED_BRANCHES_ASSUMED_IN_SERVICE",None,
             "SIMULATION_TOPOLOGY_MIXED_EVIDENCE"),
            (CAND_VERSION,"OSM_DISTRIBUTION_CANDIDATE","OSM_SHARED_NODE_COMPONENTS",
             "NO_PUBLIC_SWITCH_STATE_OR_NORMALLY_OPEN_POINT",None,
             "CANDIDATE_TOPOLOGY_NOT_SOLVER_NETWORK"),
            (ATTACH_VERSION,"OSM_PTD_ATTACHMENT_CANDIDATE",
             "OSM_MV_SEGMENTS_SPLIT_AT_CONSTRAINED_INFERRED_PTD_TAPS",
             "NO_PUBLIC_SWITCH_STATE_OR_NORMALLY_OPEN_POINT",None,
             "CANDIDATE_TOPOLOGY_WITH_INFERRED_PTD_TERMINALS_NOT_SOLVER_NETWORK"),
        ])
        if has_switching:
            con.execute("""INSERT INTO model.topology_version VALUES (
                'ALL_VOLTAGE_SWITCHABLE_BASE_V1','SOLVABLE_EQUIVALENT',
                'EQUIVALENT_BRANCH_GATE_SWITCHING_ABSTRACTION',
                'SWITCH_SNAPSHOT_ALL_CLOSED_V1',NULL,
                'SYNTHETIC_SWITCH_STATE_NOT_OPERATOR_OBSERVATION')""")
        con.execute("""CREATE OR REPLACE TABLE model.base_voltage AS
            SELECT 'BASEV:'||format('{:.6g}',voltage_kv) AS base_voltage_id,
                   voltage_kv AS nominal_voltage_kv,'AC' AS ac_dc
            FROM (SELECT DISTINCT voltage_kv FROM equivalent.buses
                  UNION SELECT DISTINCT voltage_kv FROM candidate.osm_nodes)
            ORDER BY nominal_voltage_kv""")
        con.execute("""CREATE OR REPLACE TABLE model.equipment_container AS
            SELECT 'SYSTEM:PORTUGAL' AS container_id,'POWER_SYSTEM' AS container_type,
                   'Portugal public-data-based synthetic grid' AS name,
                   NULL::DOUBLE AS lat,NULL::DOUBLE AS lon,NULL::DOUBLE AS nominal_voltage_kv,
                   NULL::VARCHAR AS parent_container_id,'MODEL_SCOPE' AS evidence_status
            UNION ALL
            SELECT 'PTD:'||p.ptd_code,'SECONDARY_SUBSTATION',p.ptd_code,t.lat,t.lon,
                   e.mv_voltage_kv,'SYSTEM:PORTUGAL','PUBLIC_EREDES_PTD_LOCATION'
            FROM candidate.ptd_public_attributes p
            JOIN candidate.ptd_coordinates_national t USING(ptd_code)
            JOIN equivalent.ptd_connections e USING(ptd_code)
            UNION ALL
            SELECT 'STATION:'||c.station_code,'PRIMARY_SUBSTATION',c.station_code,
                   coalesce((SELECT avg(lat) FROM candidate.station_sources s
                             WHERE s.station_code=c.station_code),
                            (SELECT avg(lat) FROM equivalent.buses b
                             WHERE b.source_ref=c.station_code)),
                   coalesce((SELECT avg(lon) FROM candidate.station_sources s
                             WHERE s.station_code=c.station_code),
                            (SELECT avg(lon) FROM equivalent.buses b
                             WHERE b.source_ref=c.station_code)),
                   coalesce((SELECT max(mv_voltage_v)/1000.0 FROM candidate.station_sources s
                             WHERE s.station_code=c.station_code),
                            (SELECT max(lv_kv) FROM parameter.mv_root_transformer_parameters p
                             WHERE split_part(p.hv_bus,':',3)=c.station_code)),
                   'SYSTEM:PORTUGAL',
                   CASE WHEN EXISTS (SELECT 1 FROM candidate.station_sources s
                                          WHERE s.station_code=c.station_code)
                        THEN 'PUBLIC_EREDES_STATION_SOURCE'
                        ELSE 'EREDES_LOAD_STATION_MODEL_BUS_NO_PUBLIC_STATION_CHARACTERISTICS' END
            FROM (SELECT station_code FROM candidate.station_sources
                  UNION SELECT station_code FROM operating.station_lv_split) c""")
        con.execute(f"""CREATE OR REPLACE TABLE model.feeder AS
            SELECT 'LVFEEDER:'||d.ptd_code||':'||cast(i.feeder_index AS VARCHAR) AS feeder_id,
                   'SOLVABLE_EQUIVALENT' AS network_layer,
                   'PTD:'||d.ptd_code AS container_id,d.ptd_code,
                   i.feeder_index,d.feeder_count,d.phases,d.section_count,
                   d.section_length_km,d.installation_scenario,d.cable_designation,
                   d.permissible_current_a,d.design_current_a_per_feeder,
                   d.topology_evidence_class,d.model_topology_status,
                   d.electrical_connection_status,d.topology_scenario_status,
                   d.scenario_id
            FROM {lv_design_table} d
            CROSS JOIN range(1,d.feeder_count+1) i(feeder_index)""")
        if has_lv_route_geometry:
            con.execute("""CREATE OR REPLACE TABLE model.route AS
                SELECT 'ROUTE:'||route_id AS route_id,route_id AS feeder_id,
                       ptd_code,'PTD:'||ptd_code AS container_id,scenario_id,
                       total_geometry_length_m,total_solver_length_km,
                       installation_scenario,cable_designation,route_basis,
                       engineering_confidence_score,source_geometry_ref,
                       evidence_status,model_status
                FROM phase.lv_feeder_route_scenario""")
            con.execute("""CREATE OR REPLACE TABLE model.route_point AS
                SELECT route_point_id,'ROUTE:'||route_id AS route_id,ptd_code,
                       point_sequence,lat,lon,radial_distance_m,geometry_basis,
                       source_geometry_ref,evidence_status
                FROM phase.lv_feeder_route_point_scenario
                WHERE route_id IS NOT NULL
                UNION ALL
                SELECT route_point_id,NULL::VARCHAR,ptd_code,point_sequence,lat,lon,
                       radial_distance_m,geometry_basis,source_geometry_ref,evidence_status
                FROM phase.lv_feeder_route_point_scenario
                WHERE route_id IS NULL""")
            con.execute("""CREATE OR REPLACE TABLE model.route_segment AS
                SELECT route_edge_id AS route_segment_id,'ROUTE:'||route_id AS route_id,
                       from_route_point_id,to_route_point_id,section_index,
                       geometry_length_m,solver_length_km,route_basis,
                       engineering_confidence_score,source_geometry_ref,evidence_status
                FROM phase.lv_feeder_route_edge_scenario""")
        else:
            con.execute("""CREATE OR REPLACE TABLE model.route(
                route_id VARCHAR,feeder_id VARCHAR,ptd_code VARCHAR,container_id VARCHAR,
                scenario_id VARCHAR,total_geometry_length_m DOUBLE,total_solver_length_km DOUBLE,
                installation_scenario VARCHAR,cable_designation VARCHAR,route_basis VARCHAR,
                engineering_confidence_score DOUBLE,source_geometry_ref VARCHAR,
                evidence_status VARCHAR,model_status VARCHAR)""")
            con.execute("""CREATE OR REPLACE TABLE model.route_point(
                route_point_id VARCHAR,route_id VARCHAR,ptd_code VARCHAR,point_sequence INTEGER,
                lat DOUBLE,lon DOUBLE,radial_distance_m DOUBLE,geometry_basis VARCHAR,
                source_geometry_ref VARCHAR,evidence_status VARCHAR)""")
            con.execute("""CREATE OR REPLACE TABLE model.route_segment(
                route_segment_id VARCHAR,route_id VARCHAR,from_route_point_id VARCHAR,
                to_route_point_id VARCHAR,section_index INTEGER,geometry_length_m DOUBLE,
                solver_length_km DOUBLE,route_basis VARCHAR,engineering_confidence_score DOUBLE,
                source_geometry_ref VARCHAR,evidence_status VARCHAR)""")
        if has_mv_validated_operating:
            con.execute("""CREATE OR REPLACE TABLE model.mv_operating_zone AS
                SELECT 'MVZONE:'||zone_id AS mv_zone_id,zone_id,station_code,component_id,
                       topology_scenario,source_voltage_setpoint_pu,target_feeder_mva,
                       inferred_feeder_count,selected_overhead_catalog_id,
                       synchronized_peak_timestamp_utc,ptd_count,synchronized_load_mw,
                       synchronized_load_mvar,candidate_open_line_parts,
                       min_voltage_pu AS minimum_voltage_pu,max_line_loading_percent,
                       max_transformer_loading_percent,public_fault_max_mva,
                       calculated_fault_max_mva,fault_max_error_percent,
                       public_fault_min_mva,calculated_fault_min_mva,
                       fault_min_error_percent,topology_evidence_status,
                       parameter_evidence_status,load_evidence_status
                FROM scenario.mv_validated_zone_design""")
            con.execute("""CREATE OR REPLACE TABLE model.mv_operating_feeder AS
                SELECT 'MVOPFEEDER:'||p.zone_id||':'||cast(p.inferred_feeder_number AS VARCHAR)
                         AS feeder_id,
                       p.zone_id,p.station_code,p.component_id,p.inferred_feeder_number,
                       z.target_feeder_mva,count(*) AS ptd_count,
                       sum(p.p_mw) AS p_mw,sum(p.q_mvar) AS q_mvar,
                       'STATION:'||p.station_code AS container_id,
                       'SOURCE_TREE_CONTIGUOUS_SYNCHRONIZED_LOAD_PACKING_INFERRED'
                         AS evidence_status,
                       'OSM_MV_VALIDATED_OPERATING_SCENARIO' AS network_layer
                FROM scenario.mv_validated_ptd_feeder p
                JOIN scenario.mv_validated_zone_design z USING(zone_id,station_code,component_id)
                GROUP BY p.zone_id,p.station_code,p.component_id,p.inferred_feeder_number,
                         z.target_feeder_mva""")
            con.execute("""CREATE OR REPLACE TABLE model.mv_operating_line_state AS
                SELECT 'MVOPLINE:'||zone_id||':'||cast(line_index AS VARCHAR) AS scenario_line_id,
                       zone_id,station_code,component_id,line_index,edge_part_id,
                       in_service,switching_role,inferred_circuit_count,
                       geometric_length_km,electrical_circuit_km,selected_catalog_id,
                       equivalent_r_ohm_per_km,equivalent_x_ohm_per_km,
                       equivalent_c_nf_per_km,equivalent_max_i_ka,evidence_status
                FROM scenario.mv_validated_line_state""")
            con.execute("""CREATE OR REPLACE TABLE model.ptd_transformer_operating_design AS
                SELECT 'PTDTRAFO:'||ptd_code AS equipment_id,zone_id,station_code,
                       component_id,ptd_code,public_capacity_mva,
                       synchronized_apparent_load_mva,target_utilization,
                       required_design_capacity_mva,design_capacity_mva,
                       design_unit_count,design_standard_catalog_id,capacity_action,
                       evidence_status
                FROM scenario.mv_validated_ptd_transformer_design""")
        else:
            con.execute("""CREATE OR REPLACE TABLE model.mv_operating_zone(
                mv_zone_id VARCHAR,zone_id VARCHAR,station_code VARCHAR,component_id VARCHAR)""")
            con.execute("""CREATE OR REPLACE TABLE model.mv_operating_feeder(
                feeder_id VARCHAR,zone_id VARCHAR,station_code VARCHAR,component_id VARCHAR,
                inferred_feeder_number INTEGER)""")
            con.execute("""CREATE OR REPLACE TABLE model.mv_operating_line_state(
                scenario_line_id VARCHAR,zone_id VARCHAR,station_code VARCHAR,component_id VARCHAR,
                line_index BIGINT)""")
            con.execute("""CREATE OR REPLACE TABLE model.ptd_transformer_operating_design(
                equipment_id VARCHAR,zone_id VARCHAR,station_code VARCHAR,component_id VARCHAR,
                ptd_code VARCHAR)""")
        if has_small_osm_validated_operating:
            con.execute("""CREATE OR REPLACE TABLE model.osm_component_operating_design AS
                SELECT * FROM scenario.osm_small_component_validated_design""")
            con.execute("""CREATE OR REPLACE TABLE model.osm_component_operating_feeder AS
                SELECT 'OSMOPFEEDER:'||component_id||':'||cast(inferred_feeder_number AS VARCHAR)
                         AS feeder_id,
                       component_id,inferred_feeder_number,count(*) AS ptd_count,
                       sum(p_mw) AS p_mw,sum(q_mvar) AS q_mvar,
                       'SYSTEM:PORTUGAL' AS container_id,
                       'MULTI_SOURCE_FOREST_CONTIGUOUS_LOAD_PACKING_INFERRED' AS evidence_status,
                       'OSM_MV_VALIDATED_OPERATING_SCENARIO' AS network_layer
                FROM scenario.osm_small_component_validated_ptd_feeder
                GROUP BY component_id,inferred_feeder_number""")
            con.execute("""CREATE OR REPLACE TABLE model.osm_component_operating_line_state AS
                SELECT 'OSMOPLINE:'||component_id||':'||cast(line_index AS VARCHAR)
                         AS scenario_line_id,*
                FROM scenario.osm_small_component_validated_line_state""")
        else:
            con.execute("""CREATE OR REPLACE TABLE model.osm_component_operating_design(
                component_id VARCHAR)""")
            con.execute("""CREATE OR REPLACE TABLE model.osm_component_operating_feeder(
                feeder_id VARCHAR,component_id VARCHAR,inferred_feeder_number INTEGER)""")
            con.execute("""CREATE OR REPLACE TABLE model.osm_component_operating_line_state(
                scenario_line_id VARCHAR,component_id VARCHAR,line_index BIGINT)""")
        con.execute("""CREATE OR REPLACE TABLE model.connectivity_node AS
            SELECT b.bus_id AS connectivity_node_id,'SOLVABLE_EQUIVALENT' AS network_layer,
                   'BASEV:'||format('{:.6g}',b.voltage_kv) AS base_voltage_id,
                   b.voltage_kv AS nominal_voltage_kv,b.lat,b.lon,
                   CASE WHEN p.ptd_code IS NOT NULL THEN 'PTD:'||p.ptd_code
                        WHEN s.station_code IS NOT NULL THEN 'STATION:'||s.station_code
                        ELSE 'SYSTEM:PORTUGAL' END AS container_id,
                   b.source_ref,b.evidence_status
            FROM equivalent.buses b
            LEFT JOIN candidate.ptd_public_attributes p ON b.source_ref=p.ptd_code
            LEFT JOIN (SELECT station_code FROM candidate.station_sources
                       UNION SELECT station_code FROM operating.station_lv_split) s
              ON b.source_ref=s.station_code
            UNION ALL
            SELECT n.node_id,'OSM_DISTRIBUTION_CANDIDATE',
                   'BASEV:'||format('{:.6g}',n.voltage_kv),n.voltage_kv,n.lat,n.lon,
                   'SYSTEM:PORTUGAL',n.component_id,n.source_status
            FROM candidate.osm_nodes n
            UNION ALL
            SELECT 'ATTACHNODE:'||n.node_id,'OSM_PTD_ATTACHMENT_CANDIDATE',
                   'BASEV:'||format('{:.6g}',n.voltage_kv),n.voltage_kv,n.lat,n.lon,
                   CASE WHEN n.node_type='PUBLIC_PTD_POINT_WITH_INFERRED_MV_TERMINAL'
                        THEN 'PTD:'||split_part(n.node_id,':',2)
                        ELSE 'SYSTEM:PORTUGAL' END,
                   n.component_id,n.evidence_status
            FROM candidate.mv_attachment_graph_node n""")
        con.execute("""CREATE OR REPLACE TABLE model.conducting_equipment AS
            SELECT b.branch_id AS equipment_id,'SOLVABLE_EQUIVALENT' AS network_layer,
                   upper(b.asset_kind) AS equipment_type,b.from_kv,b.to_kv,
                   CASE WHEN b.branch_id LIKE 'PTDTRAFO:%' THEN 'PTD:'||b.source_ref
                        WHEN b.branch_id LIKE 'MVFEEDER:%' THEN 'PTD:'||split_part(b.branch_id,':',2)
                        WHEN b.branch_id LIKE 'LVLINE:%' THEN 'PTD:'||b.source_ref
                        ELSE 'SYSTEM:PORTUGAL' END AS container_id,
                   b.source_ref,b.evidence_status,true AS in_service_assumption
            FROM equivalent.branches b
            UNION ALL
            SELECT s.edge_id,'OSM_DISTRIBUTION_CANDIDATE','AC_LINE_SEGMENT',
                   s.voltage_kv,s.voltage_kv,'SYSTEM:PORTUGAL',
                   cast(s.osm_way_id AS VARCHAR),s.source_status,NULL::BOOLEAN
            FROM candidate.osm_segments s
            UNION ALL
            SELECT 'ATTACHEDGE:'||g.edge_id,'OSM_PTD_ATTACHMENT_CANDIDATE','AC_LINE_SEGMENT',
                   g.voltage_kv,g.voltage_kv,
                   CASE WHEN g.edge_type='INFERRED_PTD_CONNECTION_SPUR'
                        THEN 'PTD:'||split_part(g.edge_id,':',2)
                        ELSE 'SYSTEM:PORTUGAL' END,
                   g.parent_segment_id,g.evidence_status,NULL::BOOLEAN
            FROM candidate.mv_attachment_graph_edge g""")
        con.execute("""CREATE OR REPLACE TABLE operating.mv_load_resource_allocation AS
            WITH roots AS (
              SELECT 'MVLOAD:'||p.mv_root_bus AS resource_id,p.mv_root_bus,
                     split_part(p.hv_bus,':',3) AS station_code,p.lv_kv,
                     greatest(coalesce(p.ptd_peak_proxy_mw,0),0.1) AS weight_basis_mw,
                     f.observed_station_peak_mw,f.lv_fraction
              FROM parameter.mv_root_transformer_parameters p
              JOIN operating.station_lv_split f
                ON split_part(p.hv_bus,':',3)=f.station_code
            ), weighted AS (
              SELECT *,weight_basis_mw/sum(weight_basis_mw) OVER (PARTITION BY station_code)
                         AS station_root_fraction
              FROM roots
            )
            SELECT resource_id,mv_root_bus,station_code,lv_kv,station_root_fraction,
                   observed_station_peak_mw*(1-lv_fraction)*station_root_fraction AS rated_p_mw,
                   observed_station_peak_mw*(1-lv_fraction)*station_root_fraction/0.97 AS rated_mva,
                   'INFERRED_MV_RESIDUAL_ALLOCATED_BY_MV_ROOT_PTD_PEAK_PROXY' AS evidence_status
            FROM weighted""")
        con.execute("""CREATE OR REPLACE TABLE model.resource AS
            SELECT l.load_id AS resource_id,'SOLVABLE_EQUIVALENT' AS network_layer,
                   'LOAD' AS resource_type,'PTD:'||l.source_ptd_code AS container_id,
                   l.p_mw AS rated_mw,sqrt(l.p_mw*l.p_mw+l.q_mvar*l.q_mvar) AS rated_mva,
                   l.source_ptd_code AS source_ref,l.evidence_status,
                   true AS connected_to_model
            FROM equivalent.lv_loads l
            UNION ALL
            SELECT g.generator_id,'SOLVABLE_EQUIVALENT','GENERATOR','SYSTEM:PORTUGAL',
                   g.nameplate_mw,g.nameplate_mw,g.source_id,g.source_status,
                   g.bus_id IS NOT NULL
            FROM equivalent.base_generators g
            UNION ALL
            SELECT 'PV:'||a.ptd_code,'SOLVABLE_EQUIVALENT','SOLAR_DER',
                   'PTD:'||a.ptd_code,a.capacity_proxy_mw,a.capacity_proxy_mw,
                   a.ptd_code,a.evidence_status,true
            FROM operating.ptd_pv_allocations a
            UNION ALL
            SELECT v.resource_id,'SOLVABLE_EQUIVALENT','GENERATOR','SYSTEM:PORTUGAL',
                   NULL::DOUBLE,NULL::DOUBLE,v.generation_source,
                   'UNMAPPED_NATIONAL_GENERATION_RESIDUAL_PROXY',true
            FROM (VALUES
                ('GEN:RESIDUAL:HYDRO','Hydro'),
                ('GEN:RESIDUAL:SOLAR','Solar'),
                ('GEN:RESIDUAL:WIND','Wind'),
                ('GEN:RESIDUAL:NATURAL_GAS','Natural Gas'),
                ('GEN:RESIDUAL:OTHER_THERMAL','Other Thermal'),
                ('GEN:RESIDUAL:BIOMASS','Biomass'),
                ('GEN:RESIDUAL:WAVE','Wave'),
                ('GEN:RESIDUAL:BATTERY_INJECTION','Battery Injection')
            ) v(resource_id,generation_source)
            UNION ALL
            SELECT a.resource_id,'SOLVABLE_EQUIVALENT','MV_LOAD',
                   'STATION:'||a.station_code,a.rated_p_mw,a.rated_mva,
                   a.station_code,a.evidence_status,true
            FROM operating.mv_load_resource_allocation a""")
        con.execute("""CREATE OR REPLACE TABLE model.terminal AS
            SELECT 'TERM:'||b.branch_id||':1' AS terminal_id,b.branch_id AS object_id,
                   'CONDUCTING_EQUIPMENT' AS object_category,'SOLVABLE_EQUIVALENT' AS network_layer,
                   b.from_bus AS connectivity_node_id,1 AS sequence_number,
                   CASE WHEN b.from_kv<=0.4 THEN 'ABCN' ELSE 'ABC' END AS phases,
                   'FROM_BUS_ENDPOINT' AS terminal_basis
            FROM equivalent.branches b
            UNION ALL
            SELECT 'TERM:'||b.branch_id||':2',b.branch_id,'CONDUCTING_EQUIPMENT',
                   'SOLVABLE_EQUIVALENT',b.to_bus,2,
                   CASE WHEN b.to_kv<=0.4 THEN 'ABCN' ELSE 'ABC' END,'TO_BUS_ENDPOINT'
            FROM equivalent.branches b
            UNION ALL
            SELECT 'TERM:'||s.edge_id||':1',s.edge_id,'CONDUCTING_EQUIPMENT',
                   'OSM_DISTRIBUTION_CANDIDATE',s.from_node,1,
                   CASE WHEN s.voltage_kv<=0.4 THEN 'ABCN' ELSE 'ABC' END,'OSM_SHARED_NODE_ENDPOINT'
            FROM candidate.osm_segments s
            UNION ALL
            SELECT 'TERM:'||s.edge_id||':2',s.edge_id,'CONDUCTING_EQUIPMENT',
                   'OSM_DISTRIBUTION_CANDIDATE',s.to_node,2,
                   CASE WHEN s.voltage_kv<=0.4 THEN 'ABCN' ELSE 'ABC' END,'OSM_SHARED_NODE_ENDPOINT'
            FROM candidate.osm_segments s
            UNION ALL
            SELECT 'TERM:ATTACHEDGE:'||g.edge_id||':1','ATTACHEDGE:'||g.edge_id,
                   'CONDUCTING_EQUIPMENT','OSM_PTD_ATTACHMENT_CANDIDATE',
                   'ATTACHNODE:'||g.from_node,1,'ABC',
                   CASE WHEN g.edge_type='INFERRED_PTD_CONNECTION_SPUR'
                        THEN 'INFERRED_TAP_OR_PTD_ENDPOINT'
                        ELSE 'OSM_SHARED_OR_INFERRED_SPLIT_ENDPOINT' END
            FROM candidate.mv_attachment_graph_edge g
            UNION ALL
            SELECT 'TERM:ATTACHEDGE:'||g.edge_id||':2','ATTACHEDGE:'||g.edge_id,
                   'CONDUCTING_EQUIPMENT','OSM_PTD_ATTACHMENT_CANDIDATE',
                   'ATTACHNODE:'||g.to_node,2,'ABC',
                   CASE WHEN g.edge_type='INFERRED_PTD_CONNECTION_SPUR'
                        THEN 'INFERRED_TAP_OR_PTD_ENDPOINT'
                        ELSE 'OSM_SHARED_OR_INFERRED_SPLIT_ENDPOINT' END
            FROM candidate.mv_attachment_graph_edge g
            UNION ALL
            SELECT 'TERM:'||l.load_id||':1',l.load_id,'RESOURCE','SOLVABLE_EQUIVALENT',
                   l.bus_id,1,'ABCN','AGGREGATE_LV_LOAD_CONNECTION'
            FROM equivalent.lv_loads l
            UNION ALL
            SELECT 'TERM:'||g.generator_id||':1',g.generator_id,'RESOURCE','SOLVABLE_EQUIVALENT',
                   g.bus_id,1,'ABC','GENERATOR_BUS_ASSIGNMENT'
            FROM equivalent.base_generators g WHERE g.bus_id IS NOT NULL
            UNION ALL
            SELECT 'TERM:PV:'||a.ptd_code||':1','PV:'||a.ptd_code,'RESOURCE',
                   'SOLVABLE_EQUIVALENT','LV:'||a.ptd_code||':2',1,'ABC',
                   'SYNTHETIC_PTD_PV_DISTAL_CONNECTION'
            FROM operating.ptd_pv_allocations a
            UNION ALL
            SELECT 'TERM:'||v.resource_id||':1',v.resource_id,'RESOURCE',
                   'SOLVABLE_EQUIVALENT','BUS:OSM:way:131715746:400',1,'ABC',
                   'UNMAPPED_NATIONAL_GENERATION_RESIDUAL_BUS_PROXY'
            FROM (VALUES
                ('GEN:RESIDUAL:HYDRO'),
                ('GEN:RESIDUAL:SOLAR'),
                ('GEN:RESIDUAL:WIND'),
                ('GEN:RESIDUAL:NATURAL_GAS'),
                ('GEN:RESIDUAL:OTHER_THERMAL'),
                ('GEN:RESIDUAL:BIOMASS'),
                ('GEN:RESIDUAL:WAVE'),
                ('GEN:RESIDUAL:BATTERY_INJECTION')
            ) v(resource_id)
            UNION ALL
            SELECT 'TERM:'||a.resource_id||':1',a.resource_id,'RESOURCE',
                   'SOLVABLE_EQUIVALENT',a.mv_root_bus,1,'ABC',
                   'SYNTHETIC_MV_ROOT_AGGREGATE_LOAD_CONNECTION'
            FROM operating.mv_load_resource_allocation a""")
        con.execute("""CREATE OR REPLACE TABLE model.object_registry AS
            SELECT container_id AS object_id,'EQUIPMENT_CONTAINER' AS object_category,
                   container_type AS object_type,name,parent_container_id AS container_id,
                   NULL::VARCHAR AS source_ref,evidence_status,NULL::VARCHAR AS network_layer
            FROM model.equipment_container
            UNION ALL
            SELECT connectivity_node_id,'CONNECTIVITY_NODE','CONNECTIVITY_NODE',
                   connectivity_node_id,container_id,source_ref,evidence_status,network_layer
            FROM model.connectivity_node
            UNION ALL
            SELECT equipment_id,'CONDUCTING_EQUIPMENT',equipment_type,equipment_id,
                   container_id,source_ref,evidence_status,network_layer
            FROM model.conducting_equipment
            UNION ALL
            SELECT resource_id,'RESOURCE',resource_type,resource_id,container_id,
                   source_ref,evidence_status,network_layer FROM model.resource
            UNION ALL
            SELECT feeder_id,'FEEDER','LV_FEEDER',feeder_id,container_id,ptd_code,
                   topology_scenario_status,network_layer FROM model.feeder
            UNION ALL
            SELECT route_id,'ROUTE','LV_FEEDER_ROUTE',route_id,container_id,
                   feeder_id,evidence_status,'GEOGRAPHIC_SCENARIO' FROM model.route
            UNION ALL
            SELECT route_point_id,'ROUTE_POINT','ROUTE_POINT',route_point_id,
                   coalesce(route_id,'PTD:'||ptd_code),source_geometry_ref,
                   evidence_status,'GEOGRAPHIC_SCENARIO' FROM model.route_point
            UNION ALL
            SELECT route_segment_id,'ROUTE_SEGMENT','ROUTE_SEGMENT',route_segment_id,
                   route_id,source_geometry_ref,evidence_status,'GEOGRAPHIC_SCENARIO'
            FROM model.route_segment""")
        if has_switching:
            con.execute("""INSERT INTO model.object_registry
                SELECT switch_id,'SWITCHING_DEVICE',switch_kind,switch_id,
                       e.container_id,s.source_ref,s.evidence_status,s.network_layer
                FROM model.switching_device s
                JOIN model.conducting_equipment e
                  ON s.controlled_equipment_id=e.equipment_id
                 AND s.network_layer=e.network_layer""")
        if has_mv_validated_operating:
            con.execute("""INSERT INTO model.object_registry
                SELECT feeder_id,'FEEDER','MV_FEEDER',feeder_id,container_id,
                       zone_id,evidence_status,network_layer
                FROM model.mv_operating_feeder""")
        if has_small_osm_validated_operating:
            con.execute("""INSERT INTO model.object_registry
                SELECT feeder_id,'FEEDER','MV_FEEDER',feeder_id,container_id,
                       component_id,evidence_status,network_layer
                FROM model.osm_component_operating_feeder""")
        con.execute("""CREATE OR REPLACE TABLE model.topological_node AS
            SELECT ?||':'||b.bus_id AS topological_node_id,? AS topology_version_id,
                   b.bus_id AS connectivity_node_id,b.voltage_kv AS base_voltage_kv,
                   'ONE_TO_ONE_EQUIVALENT_BUS_NO_SWITCH_COLLAPSE' AS topology_status
            FROM equivalent.buses b
            UNION ALL
            SELECT 'TN:CAND:'||n.node_id,?,n.node_id,n.voltage_kv,
                   'ONE_TO_ONE_OSM_SHARED_NODE' FROM candidate.osm_nodes n
            UNION ALL
            SELECT 'TN:ATTACH:'||n.node_id,?,'ATTACHNODE:'||n.node_id,n.voltage_kv,
                   CASE WHEN n.node_type='INFERRED_SEGMENT_TAP'
                        THEN 'ONE_TO_ONE_INFERRED_PTD_TAP'
                        WHEN n.node_type='PUBLIC_PTD_POINT_WITH_INFERRED_MV_TERMINAL'
                        THEN 'ONE_TO_ONE_PUBLIC_PTD_POINT_INFERRED_TERMINAL'
                        ELSE 'ONE_TO_ONE_OSM_SHARED_NODE' END
            FROM candidate.mv_attachment_graph_node n""",
                    ["TN:EQ",EQ_VERSION,CAND_VERSION,ATTACH_VERSION])
        con.execute("""CREATE OR REPLACE TABLE model.connectivity_topology_map AS
            SELECT topology_version_id,connectivity_node_id,topological_node_id,
                   topology_status AS mapping_status FROM model.topological_node""")
        con.execute("""CREATE OR REPLACE TABLE model.topological_island (
            topology_version_id VARCHAR,island_id VARCHAR,node_count BIGINT,
            source_candidate_count BIGINT,energized_assumption BOOLEAN,evidence_status VARCHAR)""")
        con.executemany("INSERT INTO model.topological_island VALUES (?,?,?,?,?,?)",
                       [(EQ_VERSION,*row) for row in island_rows])
        con.execute("""INSERT INTO model.topological_island
            SELECT ?,component_id,count(*),NULL::BIGINT,NULL::BOOLEAN,
                   'OSM_COMPONENT_NO_VERIFIED_SOURCE_OR_SWITCH_STATE'
            FROM candidate.osm_nodes GROUP BY component_id""",[CAND_VERSION])
        con.execute("""INSERT INTO model.topological_island
            SELECT ?,n.component_id,count(*),
                   max(coalesce(a.near_station_count,0)) AS source_candidate_count,
                   max(coalesce(a.near_station_count,0))>0 AS energized_assumption,
                   CASE WHEN max(coalesce(a.near_station_count,0))>0
                        THEN 'OSM_COMPONENT_WITH_NEAR_PUBLIC_SOURCE_AND_INFERRED_PTD_TAPS'
                        ELSE 'OSM_COMPONENT_WITH_INFERRED_PTD_TAPS_NO_NEAR_PUBLIC_SOURCE' END
            FROM candidate.mv_attachment_graph_node n
            LEFT JOIN (SELECT component_id,max(near_station_count) AS near_station_count
                       FROM candidate.ptd_mv_attachment_assessment GROUP BY 1) a
              USING(component_id)
            GROUP BY n.component_id""",[ATTACH_VERSION])
        con.register("eq_island_membership",membership_arrow)
        con.execute("""CREATE OR REPLACE TABLE model.topological_node_island AS
            SELECT * FROM eq_island_membership
            UNION ALL
            SELECT ?,'TN:CAND:'||node_id,'ISLAND:CAND:'||component_id
            FROM candidate.osm_nodes
            UNION ALL
            SELECT ?,'TN:ATTACH:'||node_id,'ISLAND:ATTACH:'||component_id
            FROM candidate.mv_attachment_graph_node""",[CAND_VERSION,ATTACH_VERSION])
        # Prefix candidate component island identifiers consistently.
        con.execute("""UPDATE model.topological_island
            SET island_id='ISLAND:CAND:'||island_id
            WHERE topology_version_id=?""",[CAND_VERSION])
        con.execute("""UPDATE model.topological_island
            SET island_id='ISLAND:ATTACH:'||island_id
            WHERE topology_version_id=?""",[ATTACH_VERSION])
        con.execute(f"""CREATE OR REPLACE TABLE model.equipment_parameter_binding AS
            WITH binding AS (
              SELECT l.line_id AS equipment_id,'equivalent.base_lines' AS parameter_table,
                     l.line_id AS parameter_key,l.parameter_status AS parameter_status
              FROM equivalent.base_lines l
              UNION ALL
              SELECT t.transformer_id,'equivalent.base_transformers',t.transformer_id,t.parameter_status
              FROM equivalent.base_transformers t
              UNION ALL
              SELECT 'PTDTRAFO:'||p.ptd_code,'parameter.ptd_transformer_parameters',p.ptd_code,p.impedance_status
              FROM parameter.ptd_transformer_parameters p
              UNION ALL
              SELECT l.line_id,'{lv_design_table}',d.ptd_code,
                     d.cable_assignment_status
              FROM phase.lv_line_models l
              JOIN {lv_design_table} d
                ON split_part(l.line_id,':',2)=d.ptd_code
              UNION ALL
              SELECT 'MVFEEDER:'||p.ptd_code,'parameter.mv_line_parameters',m.edge_id,
                     'PTD_NEAREST_OSM_SEGMENT_PARAMETER_SCENARIO'
              FROM candidate.ptd_segment_candidates p
              JOIN parameter.mv_line_parameters m ON p.osm_segment_id_candidate=m.edge_id
              UNION ALL
              SELECT p.equipment_id,'parameter.mv_root_transformer_parameters',p.equipment_id,
                     p.impedance_status
              FROM parameter.mv_root_transformer_parameters p
              UNION ALL
              SELECT m.edge_id,'parameter.mv_line_parameters',m.edge_id,m.parameter_status
              FROM parameter.mv_line_parameters m
              UNION ALL
              SELECT 'ATTACHEDGE:'||g.edge_id,'parameter.mv_line_parameters',
                     g.parent_segment_id,
                     CASE WHEN g.edge_type='OSM_UNCHANGED_SEGMENT' THEN m.parameter_status
                          WHEN g.edge_type='OSM_SEGMENT_SPLIT_AT_INFERRED_PTD_TAP'
                            THEN 'PARENT_OSM_SEGMENT_PARAMETER_INHERITED_BY_SPLIT'
                          ELSE 'PARENT_OSM_SEGMENT_PARAMETER_INFERRED_FOR_PTD_SPUR' END
              FROM candidate.mv_attachment_graph_edge g
              JOIN parameter.mv_line_parameters m ON g.parent_segment_id=m.edge_id
              UNION ALL
              SELECT s.edge_id,'phase.impedance_matrix','LV_4WIRE_PROXY_V1',
                     'ENGINEERING_PROXY_UNCALIBRATED_NO_INSTALLED_CABLE_TYPE'
              FROM candidate.osm_segments s WHERE s.voltage_kv<1
            )
            SELECT e.equipment_id,e.network_layer,b.parameter_table,b.parameter_key,
                   coalesce(b.parameter_status,'MISSING_ELECTRICAL_PARAMETER_SET') AS parameter_status
            FROM model.conducting_equipment e LEFT JOIN binding b USING(equipment_id)""")
        scenario_parts=[]
        if has_ptd_root_peak_design:
            scenario_parts.append("""SELECT 'PTDTRAFO:'||ptd_code AS equipment_id,
                'SOLVABLE_EQUIVALENT' AS network_layer,
                'parameter.ptd_transformer_root_peak_design_scenario' AS scenario_table,
                ptd_code AS scenario_key,scenario_id,design_status AS scenario_status
                FROM parameter.ptd_transformer_root_peak_design_scenario""")
        if has_lv_root_peak_design:
            scenario_parts.append("""SELECT line_id,'SOLVABLE_EQUIVALENT',
                'phase.lv_feeder_root_peak_design_scenario',d.ptd_code,d.scenario_id,
                d.refinement_status FROM phase.lv_line_models l
                JOIN phase.lv_feeder_root_peak_design_scenario d
                  ON split_part(l.line_id,':',2)=d.ptd_code""")
        if has_mv_root_design:
            scenario_parts.append("""SELECT p.equipment_id,'SOLVABLE_EQUIVALENT',
                'parameter.mv_root_transformer_design_scenario',p.mv_root_bus,
                p.scenario_id,p.design_status
                FROM parameter.mv_root_transformer_design_scenario p""")
        if has_mv_validated_operating:
            scenario_parts.append("""SELECT equipment_id,'SOLVABLE_EQUIVALENT',
                'scenario.mv_validated_ptd_transformer_design',ptd_code,
                'MV_OSM_SYNCHRONIZED_PEAK_DESIGN_V1',capacity_action
                FROM model.ptd_transformer_operating_design""")
        if has_small_osm_validated_operating:
            scenario_parts.append("""SELECT 'PTDTRAFO:'||ptd_code,'SOLVABLE_EQUIVALENT',
                'scenario.osm_small_component_ptd_transformer_design',ptd_code,
                'OSM_SMALL_COMPONENT_SYNCHRONIZED_PEAK_DESIGN_V1',capacity_action
                FROM scenario.osm_small_component_ptd_transformer_design""")
        if scenario_parts:
            con.execute("CREATE OR REPLACE TABLE model.equipment_scenario_binding AS " +
                        " UNION ALL ".join(scenario_parts))
        else:
            con.execute("""CREATE OR REPLACE TABLE model.equipment_scenario_binding(
                equipment_id VARCHAR,network_layer VARCHAR,scenario_table VARCHAR,
                scenario_key VARCHAR,scenario_id VARCHAR,scenario_status VARCHAR)""")
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("""CREATE OR REPLACE TABLE study.equivalent_topological_island_audit AS
            WITH nodes AS (
              SELECT i.island_id,count(*) AS node_count,
                     count(*) FILTER (WHERE n.nominal_voltage_kv=0.4) AS lv_node_count,
                     count(*) FILTER (WHERE n.nominal_voltage_kv>=60) AS hv_node_count
              FROM model.topological_node_island i
              JOIN model.topological_node t USING(topology_version_id,topological_node_id)
              JOIN model.connectivity_node n USING(connectivity_node_id)
              WHERE i.topology_version_id=? GROUP BY 1
            ), equipment AS (
              SELECT i.island_id,count(*) AS equipment_count,
                     count(*) FILTER (WHERE e.equipment_type='TRANSFORMER') AS transformer_count,
                     count(*) FILTER (WHERE e.equipment_type IN ('LINE','AC_LINE_SEGMENT')) AS line_count
              FROM model.terminal t
              JOIN model.connectivity_topology_map m
                ON t.connectivity_node_id=m.connectivity_node_id
               AND m.topology_version_id='ALL_VOLTAGE_EQUIVALENT_V1'
              JOIN model.topological_node_island i USING(topology_version_id,topological_node_id)
              JOIN model.conducting_equipment e ON t.object_id=e.equipment_id
              WHERE t.network_layer='SOLVABLE_EQUIVALENT' AND t.sequence_number=1
              GROUP BY 1
            ), resources AS (
              SELECT i.island_id,count(*) AS connected_resource_count,
                     count(*) FILTER (WHERE r.resource_type IN ('LOAD','MV_LOAD')) AS load_count,
                     count(*) FILTER (WHERE r.resource_type='GENERATOR') AS generator_count,
                     sum(r.rated_mw) FILTER (WHERE r.resource_type IN ('LOAD','MV_LOAD')) AS load_proxy_mw,
                     sum(r.rated_mw) FILTER (WHERE r.resource_type='GENERATOR') AS generator_nameplate_mw
              FROM model.terminal t
              JOIN model.connectivity_topology_map m
                ON t.connectivity_node_id=m.connectivity_node_id
               AND m.topology_version_id='ALL_VOLTAGE_EQUIVALENT_V1'
              JOIN model.topological_node_island i USING(topology_version_id,topological_node_id)
              JOIN model.resource r ON t.object_id=r.resource_id
              WHERE t.network_layer='SOLVABLE_EQUIVALENT' GROUP BY 1
            ), ptd AS (
              SELECT i.island_id,count(*) AS ptd_count
              FROM model.topological_node_island i
              JOIN model.topological_node t USING(topology_version_id,topological_node_id)
              WHERE i.topology_version_id=? AND t.connectivity_node_id LIKE 'PTD:LV:%'
              GROUP BY 1
            )
            SELECT x.island_id,x.node_count,x.source_candidate_count,x.energized_assumption,
                   n.lv_node_count,n.hv_node_count,coalesce(e.equipment_count,0) AS equipment_count,
                   coalesce(e.transformer_count,0) AS transformer_count,
                   coalesce(e.line_count,0) AS line_count,
                   coalesce(r.connected_resource_count,0) AS connected_resource_count,
                   coalesce(r.load_count,0) AS load_count,coalesce(r.generator_count,0) AS generator_count,
                   coalesce(r.load_proxy_mw,0) AS load_proxy_mw,
                   coalesce(r.generator_nameplate_mw,0) AS generator_nameplate_mw,
                   coalesce(p.ptd_count,0) AS ptd_count,
                   CASE WHEN x.energized_assumption THEN 'SOURCE_CANDIDATE_PRESENT'
                        WHEN coalesce(r.load_count,0)>0 THEN 'LOAD_ISLAND_WITHOUT_SOURCE_CANDIDATE'
                        ELSE 'ISOLATED_GEOMETRY_NO_SOURCE_OR_LOAD' END AS island_status
            FROM model.topological_island x JOIN nodes n USING(island_id)
            LEFT JOIN equipment e USING(island_id) LEFT JOIN resources r USING(island_id)
            LEFT JOIN ptd p USING(island_id)
            WHERE x.topology_version_id=? ORDER BY x.node_count DESC""",
                    [EQ_VERSION,EQ_VERSION,EQ_VERSION])

        checks = {
            "registered_objects":con.execute("SELECT count(*) FROM model.object_registry").fetchone()[0],
            "distinct_registered_objects":con.execute("SELECT count(DISTINCT object_id) FROM model.object_registry").fetchone()[0],
            "connectivity_nodes":con.execute("SELECT count(*) FROM model.connectivity_node").fetchone()[0],
            "conducting_equipment":con.execute("SELECT count(*) FROM model.conducting_equipment").fetchone()[0],
            "resources":con.execute("SELECT count(*) FROM model.resource").fetchone()[0],
            "mv_load_resources":con.execute("SELECT count(*) FROM model.resource WHERE resource_type='MV_LOAD'").fetchone()[0],
            "mv_load_stations":con.execute("SELECT count(DISTINCT station_code) FROM operating.mv_load_resource_allocation").fetchone()[0],
            "mv_load_root_weight_errors":con.execute("""SELECT count(*) FROM (
                SELECT station_code FROM operating.mv_load_resource_allocation GROUP BY 1
                HAVING abs(sum(station_root_fraction)-1)>1e-10)""").fetchone()[0],
            "feeders":con.execute("SELECT count(*) FROM model.feeder").fetchone()[0],
            "mv_operating_zones":con.execute("SELECT count(*) FROM model.mv_operating_zone").fetchone()[0],
            "mv_operating_feeders":con.execute("SELECT count(*) FROM model.mv_operating_feeder").fetchone()[0],
            "mv_operating_line_states":con.execute("SELECT count(*) FROM model.mv_operating_line_state").fetchone()[0],
            "mv_operating_ptd_transformer_designs":con.execute("SELECT count(*) FROM model.ptd_transformer_operating_design").fetchone()[0],
            "small_osm_operating_components":con.execute("SELECT count(*) FROM model.osm_component_operating_design").fetchone()[0],
            "small_osm_operating_feeders":con.execute("SELECT count(*) FROM model.osm_component_operating_feeder").fetchone()[0],
            "small_osm_operating_line_states":con.execute("SELECT count(*) FROM model.osm_component_operating_line_state").fetchone()[0],
            "small_osm_operating_acceptance_errors":con.execute("""SELECT count(*)
                FROM model.osm_component_operating_design
                WHERE NOT converged OR min_voltage_pu<0.9
                   OR max_line_loading_percent>100 OR max_transformer_loading_percent>100
                   OR max_abs_fault_error_percent>10""").fetchone()[0]
                if has_small_osm_validated_operating else 0,
            "mv_operating_zone_acceptance_errors":con.execute("""SELECT count(*)
                FROM model.mv_operating_zone
                WHERE minimum_voltage_pu<0.9 OR max_line_loading_percent>100
                   OR max_transformer_loading_percent>100
                   OR abs(fault_max_error_percent)>10 OR abs(fault_min_error_percent)>10""").fetchone()[0]
                if has_mv_validated_operating else 0,
            "mv_operating_feeder_container_errors":con.execute("""SELECT count(*)
                FROM model.mv_operating_feeder f LEFT JOIN model.equipment_container c
                  ON f.container_id=c.container_id WHERE c.container_id IS NULL""").fetchone()[0]
                if has_mv_validated_operating else 0,
            "ptds_with_feeders":con.execute("SELECT count(DISTINCT ptd_code) FROM model.feeder").fetchone()[0],
            "routes":con.execute("SELECT count(*) FROM model.route").fetchone()[0],
            "route_points":con.execute("SELECT count(*) FROM model.route_point").fetchone()[0],
            "route_segments":con.execute("SELECT count(*) FROM model.route_segment").fetchone()[0],
            "route_feeder_reference_errors":con.execute("""SELECT count(*) FROM model.route r
                LEFT JOIN model.feeder f ON r.feeder_id=f.feeder_id
                WHERE f.feeder_id IS NULL""").fetchone()[0],
            "route_segment_reference_errors":con.execute("""SELECT count(*) FROM model.route_segment s
                LEFT JOIN model.route r ON s.route_id=r.route_id
                LEFT JOIN model.route_point a ON s.from_route_point_id=a.route_point_id
                LEFT JOIN model.route_point b ON s.to_route_point_id=b.route_point_id
                WHERE r.route_id IS NULL OR a.route_point_id IS NULL OR b.route_point_id IS NULL""").fetchone()[0],
            "feeder_container_reference_errors":con.execute("""SELECT count(*) FROM model.feeder f
                LEFT JOIN model.equipment_container c ON f.container_id=c.container_id
                WHERE c.container_id IS NULL""").fetchone()[0],
            "terminals":con.execute("SELECT count(*) FROM model.terminal").fetchone()[0],
            "orphan_terminal_nodes":con.execute("""SELECT count(*) FROM model.terminal t
                LEFT JOIN model.connectivity_node n
                  ON t.connectivity_node_id=n.connectivity_node_id AND t.network_layer=n.network_layer
                WHERE n.connectivity_node_id IS NULL""").fetchone()[0],
            "orphan_terminal_objects":con.execute("""SELECT count(*) FROM model.terminal t
                LEFT JOIN model.object_registry o ON t.object_id=o.object_id
                WHERE o.object_id IS NULL""").fetchone()[0],
            "equipment_terminal_count_errors":con.execute("""SELECT count(*) FROM (
                SELECT e.equipment_id,count(t.terminal_id) AS n
                FROM model.conducting_equipment e LEFT JOIN model.terminal t ON e.equipment_id=t.object_id
                GROUP BY 1 HAVING count(t.terminal_id)<>2)""").fetchone()[0],
            "connected_resource_terminal_count_errors":con.execute("""SELECT count(*) FROM (
                SELECT r.resource_id,r.connected_to_model,count(t.terminal_id) AS n
                FROM model.resource r LEFT JOIN model.terminal t ON r.resource_id=t.object_id
                GROUP BY 1,2 HAVING (connected_to_model AND count(t.terminal_id)<>1)
                                OR (NOT connected_to_model AND count(t.terminal_id)<>0))""").fetchone()[0],
            "topological_nodes":con.execute("SELECT count(*) FROM model.topological_node").fetchone()[0],
            "topology_mapping_errors":con.execute("""SELECT count(*) FROM (
                SELECT topology_version_id,connectivity_node_id,count(*) AS n
                FROM model.connectivity_topology_map GROUP BY 1,2 HAVING count(*)<>1)""").fetchone()[0],
            "island_membership_errors":con.execute("""SELECT count(*) FROM model.topological_node n
                LEFT JOIN model.topological_node_island i
                  USING(topology_version_id,topological_node_id)
                WHERE i.island_id IS NULL""").fetchone()[0],
            "equivalent_topological_islands":island_count,
            "candidate_topological_islands":con.execute("""SELECT count(*) FROM model.topological_island
                WHERE topology_version_id=?""",[CAND_VERSION]).fetchone()[0],
            "attachment_candidate_topological_islands":con.execute("""SELECT count(*) FROM model.topological_island
                WHERE topology_version_id=?""",[ATTACH_VERSION]).fetchone()[0],
            "equipment_parameter_bindings":con.execute("SELECT count(*) FROM model.equipment_parameter_binding").fetchone()[0],
            "distinct_parameter_bound_equipment":con.execute("SELECT count(DISTINCT equipment_id) FROM model.equipment_parameter_binding").fetchone()[0],
            "parameter_binding_reference_errors":con.execute(f"""SELECT count(*) FROM (
                SELECT equipment_id FROM model.equipment_parameter_binding b
                WHERE parameter_table='equivalent.base_lines' AND NOT EXISTS
                  (SELECT 1 FROM equivalent.base_lines p WHERE p.line_id=b.parameter_key)
                UNION ALL SELECT equipment_id FROM model.equipment_parameter_binding b
                WHERE parameter_table='equivalent.base_transformers' AND NOT EXISTS
                  (SELECT 1 FROM equivalent.base_transformers p WHERE p.transformer_id=b.parameter_key)
                UNION ALL SELECT equipment_id FROM model.equipment_parameter_binding b
                WHERE parameter_table='parameter.ptd_transformer_parameters' AND NOT EXISTS
                  (SELECT 1 FROM parameter.ptd_transformer_parameters p WHERE p.ptd_code=b.parameter_key)
                UNION ALL SELECT equipment_id FROM model.equipment_parameter_binding b
                WHERE parameter_table='parameter.mv_root_transformer_parameters' AND NOT EXISTS
                  (SELECT 1 FROM parameter.mv_root_transformer_parameters p WHERE p.equipment_id=b.parameter_key)
                UNION ALL SELECT equipment_id FROM model.equipment_parameter_binding b
                WHERE parameter_table='parameter.mv_line_parameters' AND NOT EXISTS
                  (SELECT 1 FROM parameter.mv_line_parameters p WHERE p.edge_id=b.parameter_key)
                UNION ALL SELECT equipment_id FROM model.equipment_parameter_binding b
                WHERE parameter_table='phase.lv_line_models' AND NOT EXISTS
                  (SELECT 1 FROM phase.lv_line_models p WHERE p.line_id=b.parameter_key)
                UNION ALL SELECT equipment_id FROM model.equipment_parameter_binding b
                WHERE parameter_table='{lv_design_table}' AND NOT EXISTS
                  (SELECT 1 FROM {lv_design_table} p WHERE p.ptd_code=b.parameter_key)
                UNION ALL SELECT equipment_id FROM model.equipment_parameter_binding b
                WHERE parameter_table='phase.impedance_matrix' AND NOT EXISTS
                  (SELECT 1 FROM phase.impedance_matrix p WHERE p.archetype_id=b.parameter_key))""").fetchone()[0],
            "missing_equipment_parameter_sets":con.execute("""SELECT count(*) FROM model.equipment_parameter_binding
                WHERE parameter_status='MISSING_ELECTRICAL_PARAMETER_SET'""").fetchone()[0],
            "missing_equivalent_parameter_sets":con.execute("""SELECT count(*) FROM model.equipment_parameter_binding
                WHERE network_layer='SOLVABLE_EQUIVALENT'
                  AND parameter_status='MISSING_ELECTRICAL_PARAMETER_SET'""").fetchone()[0],
            "missing_candidate_parameter_sets":con.execute("""SELECT count(*) FROM model.equipment_parameter_binding
                WHERE network_layer='OSM_DISTRIBUTION_CANDIDATE'
                  AND parameter_status='MISSING_ELECTRICAL_PARAMETER_SET'""").fetchone()[0],
            "missing_attachment_candidate_parameter_sets":con.execute("""SELECT count(*) FROM model.equipment_parameter_binding
                WHERE network_layer='OSM_PTD_ATTACHMENT_CANDIDATE'
                  AND parameter_status='MISSING_ELECTRICAL_PARAMETER_SET'""").fetchone()[0],
            "equipment_scenario_bindings":con.execute(
                "SELECT count(*) FROM model.equipment_scenario_binding").fetchone()[0],
            "equipment_scenario_binding_orphans":con.execute("""SELECT count(*)
                FROM model.equipment_scenario_binding s LEFT JOIN model.conducting_equipment e
                  USING(equipment_id,network_layer) WHERE e.equipment_id IS NULL""").fetchone()[0],
            "equipment_scenario_binding_reference_errors":con.execute("""SELECT count(*) FROM (
                SELECT equipment_id FROM model.equipment_scenario_binding b
                WHERE scenario_table='parameter.ptd_transformer_root_peak_design_scenario'
                  AND NOT EXISTS (SELECT 1 FROM parameter.ptd_transformer_root_peak_design_scenario p
                                  WHERE p.ptd_code=b.scenario_key)
                UNION ALL SELECT equipment_id FROM model.equipment_scenario_binding b
                WHERE scenario_table='phase.lv_feeder_root_peak_design_scenario'
                  AND NOT EXISTS (SELECT 1 FROM phase.lv_feeder_root_peak_design_scenario p
                                  WHERE p.ptd_code=b.scenario_key)
                UNION ALL SELECT equipment_id FROM model.equipment_scenario_binding b
                WHERE scenario_table='parameter.mv_root_transformer_design_scenario'
                  AND NOT EXISTS (SELECT 1 FROM parameter.mv_root_transformer_design_scenario p
                                  WHERE p.mv_root_bus=b.scenario_key)
                UNION ALL SELECT equipment_id FROM model.equipment_scenario_binding b
                WHERE scenario_table='scenario.mv_validated_ptd_transformer_design'
                  AND NOT EXISTS (SELECT 1 FROM scenario.mv_validated_ptd_transformer_design p
                                  WHERE p.ptd_code=b.scenario_key)
                UNION ALL SELECT equipment_id FROM model.equipment_scenario_binding b
                WHERE scenario_table='scenario.osm_small_component_ptd_transformer_design'
                  AND NOT EXISTS (SELECT 1 FROM scenario.osm_small_component_ptd_transformer_design p
                                  WHERE p.ptd_code=b.scenario_key))""").fetchone()[0],
            "switch_objects":con.execute("SELECT count(*) FROM model.switching_device").fetchone()[0] if has_switching else 0,
            "switch_registry_errors":con.execute("""SELECT count(*) FROM model.switching_device s
                LEFT JOIN model.object_registry o ON s.switch_id=o.object_id
                WHERE o.object_id IS NULL""").fetchone()[0] if has_switching else 0,
            "switch_terminal_reference_errors":con.execute("""SELECT count(*) FROM model.switching_device s
                LEFT JOIN model.terminal t ON s.controlled_terminal_id=t.terminal_id
                WHERE t.terminal_id IS NULL""").fetchone()[0] if has_switching else 0,
            "switch_state_coverage_errors":con.execute("""SELECT count(*) FROM model.switching_device s
                LEFT JOIN operating.switch_state_snapshot x ON s.switch_id=x.switch_id
                  AND x.snapshot_id='SWITCH_SNAPSHOT_ALL_CLOSED_V1'
                WHERE x.switch_id IS NULL""").fetchone()[0] if has_switching else 0,
            "equivalent_islands_without_source_candidate":con.execute("""SELECT count(*)
                FROM study.equivalent_topological_island_audit
                WHERE NOT energized_assumption""").fetchone()[0],
            "ptds_in_islands_without_source_candidate":con.execute("""SELECT sum(ptd_count)
                FROM study.equivalent_topological_island_audit
                WHERE NOT energized_assumption""").fetchone()[0],
            "loads_in_islands_without_source_candidate":con.execute("""SELECT sum(load_count)
                FROM study.equivalent_topological_island_audit
                WHERE NOT energized_assumption""").fetchone()[0],
        }
        missing_breakdown = dict(con.execute("""SELECT network_layer,equipment_type,count(*)
            FROM model.equipment_parameter_binding b
            JOIN model.conducting_equipment e USING(equipment_id,network_layer)
            WHERE parameter_status='MISSING_ELECTRICAL_PARAMETER_SET'
            GROUP BY 1,2 ORDER BY 1,2""").fetchall()) if False else [
                dict(network_layer=r[0],equipment_type=r[1],count=r[2])
                for r in con.execute("""SELECT network_layer,equipment_type,count(*)
                    FROM model.equipment_parameter_binding b
                    JOIN model.conducting_equipment e USING(equipment_id,network_layer)
                    WHERE parameter_status='MISSING_ELECTRICAL_PARAMETER_SET'
                    GROUP BY 1,2 ORDER BY 1,2""").fetchall()]
    errors = []
    for key in ("orphan_terminal_nodes","orphan_terminal_objects","equipment_terminal_count_errors",
                "connected_resource_terminal_count_errors","topology_mapping_errors",
                "island_membership_errors","feeder_container_reference_errors",
                "route_feeder_reference_errors","route_segment_reference_errors",
                "switch_registry_errors","switch_terminal_reference_errors",
                "switch_state_coverage_errors"):
        if checks[key]: errors.append(f"{key}={checks[key]}")
    for key in ("mv_operating_zone_acceptance_errors","mv_operating_feeder_container_errors",
                "small_osm_operating_acceptance_errors"):
        if checks[key]: errors.append(f"{key}={checks[key]}")
    for key in ("equipment_scenario_binding_orphans","equipment_scenario_binding_reference_errors"):
        if checks[key]: errors.append(f"{key}={checks[key]}")
    if (checks["mv_load_resources"]!=517 or checks["mv_load_stations"]!=397
            or checks["mv_load_root_weight_errors"]):
        errors.append("MV-load resource allocation coverage or station-root weights failed")
    if checks["registered_objects"]!=checks["distinct_registered_objects"]:
        errors.append("Object IDs are not globally unique")
    if (checks["equipment_parameter_bindings"]!=checks["conducting_equipment"]
            or checks["distinct_parameter_bound_equipment"]!=checks["conducting_equipment"]
            or checks["parameter_binding_reference_errors"]):
        errors.append("Every conducting equipment must have one parameter-binding status")
    report = {
        "result":"PASS" if not errors else "FAIL",
        "checks":checks,
        "missing_parameter_breakdown":missing_breakdown,
        "scope":"Normalized equivalent, raw OSM candidate, constrained PTD-attachment candidate, and validated MV operating-design scenario layers",
        "limitations":[
            "Equivalent topology has synthetic branch-gate switches and an all-closed state, but no public switch inventory or time-stamped operator states",
            "The PTD-attachment layer inserts constrained inferred taps and spurs; none are operator terminal records",
            "LV Route/RoutePoint/RouteSegment objects preserve geographic scenarios separately from electrical connectivity",
            "A one-to-one connectivity/topological-node map plus branch gates does not model physical busbar splitting or bays",
            "Parameter binding records provenance and gaps; it does not upgrade proxy values to observations",
            "The validated MV operating layer stores inferred feeder, open-point, circuit and transformer-design objects separately from public asset observations",
            "Unconnected generator records remain registered resources without terminals",
        ],
        "errors":errors,
    }
    path = args.database.parent/"normalized_network_model.validation.json"
    path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=="__main__":
    raise SystemExit(main())
