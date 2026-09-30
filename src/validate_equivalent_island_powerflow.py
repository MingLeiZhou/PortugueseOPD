#!/usr/bin/env python3
"""Solve a tractable all-voltage equivalent island with persisted parameters.

The selected island must contain PTDs and fit the configured node limit. It is
built from normalized terminals, the 60-kV/MV bridge scenario, PTD transformer
parameters, MV feeder proxies, the LV four-wire diagonal proxy, and the
station-conserving gross-load/PV scenario. Fault results use an explicit source
strength scenario where no matching public station short-circuit record exists.
The inferred station MV residual is connected at each modeled MV root.
"""

from __future__ import annotations

import argparse
import json
import math
import warnings
from pathlib import Path

import duckdb
import networkx as nx
import numpy as np
import pandas as pd
import pandapower as pp
import pandapower.shortcircuit as sc

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/equivalent_island_validation"
TIMESTAMP = "2025-07-22T11:45:00+00:00"
TOPOLOGY = "ALL_VOLTAGE_EQUIVALENT_V1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--timestamp-utc", default=TIMESTAMP)
    parser.add_argument("--max-nodes", type=int, default=5000)
    parser.add_argument("--island-id")
    parser.add_argument("--mv-root-bus",
                        help="Solve one 60-kV/MV-root/PTD slice so inferred MV load enters a tractable case")
    parser.add_argument("--source-sc-max-mva", type=float, default=1000.0)
    parser.add_argument("--source-vm-pu", type=float, default=1.0,
                        help="Source (60-kV) voltage magnitude; 1.0 reproduces the published batches")
    parser.add_argument("--oltc", action="store_true",
                        help="Regulate each 60-kV/MV bridge transformer tap to keep its MV bus in the deadband")
    parser.add_argument("--oltc-deadband", type=float, nargs=2, default=(0.99, 1.01))
    parser.add_argument("--skip-faults", action="store_true",
                        help="Run power flow only (used by T&D co-simulation iterations)")
    parser.add_argument("--source-sc-min-mva", type=float, default=500.0)
    parser.add_argument("--mv-urban-cable", type=Path,
                        help="Directory of MV-URBAN-CABLE-V1 parquet files; replaces the star MV feeders of "
                             "synthetic-fallback PTDs by the simulated open-ring underground cables")
    parser.add_argument("--open-switch-id",
                        help="Apply one synthetic branch-gate OPEN action before solving")
    parser.add_argument("--no-persist-results", action="store_true",
                        help="Write case CSV/JSON files but do not replace shared study tables")
    args = parser.parse_args()
    if args.max_nodes<1 or not (args.source_sc_max_mva>=args.source_sc_min_mva>0):
        parser.error("Require positive node limit and max source short-circuit MVA >= min > 0")
    if args.open_switch_id and args.output==OUT:
        args.output=ROOT/"output/all_voltage/equivalent_island_switch_contingency"
    elif args.mv_root_bus and args.output==OUT:
        args.output=ROOT/"output/all_voltage/equivalent_mv_root_validation"
    args.output.mkdir(parents=True,exist_ok=True)
    with duckdb.connect(str(args.database),read_only=True) as con:
        slice_source_bus = None
        if args.mv_root_bus:
            root = con.execute("""SELECT p.hv_bus,count(c.ptd_code)
                FROM parameter.mv_root_transformer_parameters p
                JOIN equivalent.ptd_connections c USING(mv_root_bus)
                WHERE p.mv_root_bus=? GROUP BY p.hv_bus""",[args.mv_root_bus]).fetchone()
            if not root:
                parser.error("Requested MV root has no modeled PTDs or transformer parameter row")
            slice_source_bus,ptd_count=root
            ptd_codes=[r[0] for r in con.execute("""SELECT ptd_code
                FROM equivalent.ptd_connections WHERE mv_root_bus=? ORDER BY ptd_code""",
                [args.mv_root_bus]).fetchall()]
            branch_rows=con.execute("""SELECT branch_id,from_bus,to_bus,from_kv,to_kv,
                asset_kind,evidence_status FROM equivalent.branches
                WHERE branch_id=? OR
                  ((branch_id LIKE 'MVFEEDER:%' OR branch_id LIKE 'PTDTRAFO:%'
                    OR branch_id LIKE 'LVLINE:%')
                   AND split_part(branch_id,':',2) IN (SELECT unnest(?)))
                ORDER BY branch_id""",
                [f"BRIDGE:{args.mv_root_bus}",ptd_codes]).fetchall()
            node_ids=sorted({r[1] for r in branch_rows}|{r[2] for r in branch_rows})
            node_rows=con.execute("""SELECT bus_id,voltage_kv,lat,lon,evidence_status
                FROM equivalent.buses WHERE bus_id IN (SELECT unnest(?)) ORDER BY bus_id""",
                [node_ids]).fetchall()
            island_id=f"SLICE:EQ:{args.mv_root_bus}"
            node_count=len(node_rows)
            if node_count>args.max_nodes:
                parser.error(f"Requested MV-root slice has {node_count} nodes, above --max-nodes")
        elif args.island_id:
            island = con.execute("""SELECT island_id,node_count,ptd_count
                FROM study.equivalent_topological_island_audit WHERE island_id=?""",
                [args.island_id]).fetchone()
        else:
            island = con.execute("""SELECT island_id,node_count,ptd_count
                FROM study.equivalent_topological_island_audit
                WHERE ptd_count>0 AND node_count<=? ORDER BY node_count DESC LIMIT 1""",
                [args.max_nodes]).fetchone()
        if not args.mv_root_bus:
            if not island:
                parser.error("No PTD-containing equivalent island within node limit")
            island_id,node_count,ptd_count=island
            node_rows=con.execute("""SELECT n.connectivity_node_id,n.nominal_voltage_kv,n.lat,n.lon,n.evidence_status
                FROM model.topological_node_island i
                JOIN model.topological_node t USING(topology_version_id,topological_node_id)
                JOIN model.connectivity_node n USING(connectivity_node_id)
                WHERE i.topology_version_id=? AND i.island_id=? ORDER BY n.connectivity_node_id""",
                [TOPOLOGY,island_id]).fetchall()
        node_set={r[0] for r in node_rows}
        if not args.mv_root_bus:
            branch_rows=con.execute("""SELECT branch_id,from_bus,to_bus,from_kv,to_kv,asset_kind,evidence_status
                FROM equivalent.branches WHERE from_bus IN (SELECT connectivity_node_id
                  FROM model.topological_node_island i JOIN model.topological_node t
                    USING(topology_version_id,topological_node_id)
                  WHERE i.topology_version_id=? AND i.island_id=?)
                  AND to_bus IN (SELECT connectivity_node_id
                  FROM model.topological_node_island i JOIN model.topological_node t
                    USING(topology_version_id,topological_node_id)
                  WHERE i.topology_version_id=? AND i.island_id=?) ORDER BY branch_id""",
                [TOPOLOGY,island_id,TOPOLOGY,island_id]).fetchall()
            ptd_codes=[r[0] for r in con.execute("""SELECT ptd_code FROM equivalent.ptd_connections
                WHERE 'PTD:LV:'||ptd_code IN (SELECT connectivity_node_id
                  FROM model.topological_node_island i JOIN model.topological_node t
                    USING(topology_version_id,topological_node_id)
                  WHERE i.topology_version_id=? AND i.island_id=?) ORDER BY ptd_code""",
                [TOPOLOGY,island_id]).fetchall()]
        bridge={r[0]:r[1:] for r in con.execute("""SELECT p.equipment_id,
            coalesce(d.design_sn_mva,p.sn_mva) AS solver_sn_mva,p.hv_kv,p.lv_kv,
            p.vk_percent,p.vkr_percent,p.vk0_percent,p.vkr0_percent,
            p.tap_side,p.tap_neutral,p.tap_min,p.tap_max,p.tap_step_percent
            FROM parameter.mv_root_transformer_parameters p
            LEFT JOIN parameter.mv_root_transformer_design_scenario d USING(mv_root_bus)""").fetchall()}
        bridge_design={r[0]:r[1:] for r in con.execute("""SELECT p.mv_root_bus,p.sn_mva,
            coalesce(d.design_sn_mva,p.sn_mva),coalesce(d.design_status,'BASE_PARAMETER_ONLY')
            FROM parameter.mv_root_transformer_parameters p
            LEFT JOIN parameter.mv_root_transformer_design_scenario d USING(mv_root_bus)""").fetchall()}
        has_ptd_root_peak_design=bool(con.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='parameter' AND table_name='ptd_transformer_root_peak_design_scenario'""").fetchone()[0])
        if has_ptd_root_peak_design:
            ptd_trafo={r[0]:r[1:] for r in con.execute("""SELECT p.ptd_code,
                d.design_sn_mva,p.hv_kv,p.lv_kv,p.vk_percent,p.vkr_percent,
                p.vk0_percent,p.vkr0_percent,p.vector_group
                FROM parameter.ptd_transformer_parameters p
                JOIN parameter.ptd_transformer_root_peak_design_scenario d USING(ptd_code)
                WHERE p.ptd_code IN (SELECT unnest(?))""",[ptd_codes]).fetchall()}
            ptd_trafo_design={r[0]:r[1:] for r in con.execute("""SELECT p.ptd_code,p.sn_mva,
                d.design_sn_mva,d.design_status
                FROM parameter.ptd_transformer_parameters p
                JOIN parameter.ptd_transformer_root_peak_design_scenario d USING(ptd_code)
                WHERE p.ptd_code IN (SELECT unnest(?))""",[ptd_codes]).fetchall()}
        else:
            ptd_trafo={r[0]:r[1:] for r in con.execute("""SELECT ptd_code,
                CASE WHEN sn_mva>0 THEN sn_mva ELSE 0.1 END,hv_kv,lv_kv,vk_percent,
                vkr_percent,vk0_percent,vkr0_percent,vector_group
                FROM parameter.ptd_transformer_parameters
                WHERE ptd_code IN (SELECT unnest(?))""",[ptd_codes]).fetchall()}
            ptd_trafo_design={r[0]:r[1:] for r in con.execute("""SELECT ptd_code,sn_mva,
                CASE WHEN sn_mva>0 THEN sn_mva ELSE 0.1 END,'BASE_PARAMETER_ONLY'
                FROM parameter.ptd_transformer_parameters
                WHERE ptd_code IN (SELECT unnest(?))""",[ptd_codes]).fetchall()}
        mv_line={r[0]:r[1:] for r in con.execute("""SELECT p.ptd_code,p.hv_anchor_distance_km,
            m.r_ohm_per_km,m.x_ohm_per_km,m.c_nf_per_km,m.max_i_ka,
            m.r0_ohm_per_km,m.x0_ohm_per_km
            FROM equivalent.ptd_connections p
            JOIN candidate.ptd_segment_candidates s USING(ptd_code)
            JOIN parameter.mv_line_parameters m ON s.osm_segment_id_candidate=m.edge_id
            WHERE p.ptd_code IN (SELECT unnest(?))""",[ptd_codes]).fetchall()}
        has_lv_root_peak_design=bool(con.execute("""SELECT count(*) FROM information_schema.tables
            WHERE table_schema='phase' AND table_name='lv_feeder_root_peak_design_scenario'""").fetchone()[0])
        lv_design_table=("phase.lv_feeder_root_peak_design_scenario" if has_lv_root_peak_design
                         else "phase.lv_feeder_design_scenario")
        lv_design={r[0]:r[1:] for r in con.execute(f"""SELECT ptd_code,feeder_count,
            section_length_km,r_hot_ohm_per_km,x_ohm_per_km,
            permissible_current_a/1000.0,cable_designation,scenario_id
            FROM {lv_design_table}
            WHERE ptd_code IN (SELECT unnest(?))""",[ptd_codes]).fetchall()}
        operating={r[0]:r[1:] for r in con.execute("""SELECT ptd_code,gross_p_mw,gross_q_mvar,pv_p_mw,net_p_mw,
            profile_data_status FROM operating.ptd_reverse_flow_scenario_15min
            WHERE timestamp_utc=? AND ptd_code IN (SELECT unnest(?))""",
            [args.timestamp_utc,ptd_codes]).fetchall()}
        mv_operating={r[0]:r[1:] for r in con.execute("""SELECT a.resource_id,a.mv_root_bus,
            a.station_code,r.p_mw*a.station_root_fraction AS p_mw,
            r.q_mvar*a.station_root_fraction AS q_mvar,r.profile_data_status
            FROM operating.mv_load_resource_allocation a
            JOIN operating.station_mv_residual_15min r USING(station_code)
            WHERE r.timestamp_utc=? AND a.mv_root_bus IN (SELECT unnest(?))""",
            [args.timestamp_utc,list(node_set)]).fetchall()}
        switch_action = None
        if args.open_switch_id:
            switch_action=con.execute("""SELECT s.switch_id,s.controlled_equipment_id,
                c.deenergized_load_count,c.impact_status
                FROM model.switching_device s
                JOIN study.switching_branch_criticality c USING(switch_id)
                WHERE s.switch_id=?""",[args.open_switch_id]).fetchone()
            if switch_action is None:
                parser.error("Requested switch ID is absent from switching scenario")
    if len(node_rows)!=node_count or len(ptd_codes)!=ptd_count or len(operating)!=ptd_count:
        parser.error("Island node/PTD/operating coverage mismatch")
    if any(code not in lv_design for code in ptd_codes):
        parser.error("Some island PTDs lack an LV feeder design scenario")
    zero_capacity_ptds={code for code in ptd_codes if float(ptd_trafo_design[code][0])<=0}
    if any(abs(float(operating[code][0]))>1e-12 or abs(float(operating[code][1]))>1e-12
           or abs(float(operating[code][2]))>1e-12 for code in zero_capacity_ptds):
        parser.error("A zero-public-capacity PTD has non-zero load or PV in the selected snapshot")
    if any(a not in node_set or b not in node_set for _,a,b,*_ in branch_rows):
        parser.error("Island branch endpoint escaped selected node set")

    base_node_count=len(node_rows);base_branch_count=len(branch_rows)
    base_mv_load_count=len(mv_operating)
    base_ptd_codes=list(ptd_codes)
    if slice_source_bus:
        source_bus=slice_source_bus
    else:
        source_candidates=[r for r in node_rows if r[1]>=60 and r[4]=='DIRECT_EREDES']
        if len(source_candidates)!=1:
            parser.error(f"Expected one direct E-REDES source bus in selected island; got {len(source_candidates)}")
        source_bus=source_candidates[0][0]
    opened_equipment=None;predicted_deenergized_loads=0;impact_status=None
    disconnected_nodes:set[str]=set();disconnected_ptds:list[str]=[]
    if switch_action:
        _,opened_equipment,predicted_deenergized_loads,impact_status=switch_action
        if opened_equipment not in {r[0] for r in branch_rows}:
            parser.error("Requested switch does not control a branch in the selected island")
        graph=nx.Graph();graph.add_nodes_from(node_set)
        graph.add_edges_from((a,b) for branch,a,b,*_ in branch_rows if branch!=opened_equipment)
        energized=set(nx.node_connected_component(graph,source_bus))
        disconnected_nodes=node_set-energized
        node_rows=[r for r in node_rows if r[0] in energized]
        branch_rows=[r for r in branch_rows if r[0]!=opened_equipment and r[1] in energized and r[2] in energized]
        disconnected_ptds=[code for code in ptd_codes if f"PTD:LV:{code}" not in energized]
        ptd_codes=[code for code in ptd_codes if code not in set(disconnected_ptds)]
        operating={code:value for code,value in operating.items() if code in set(ptd_codes)}
        mv_operating={resource:value for resource,value in mv_operating.items()
                      if value[0] in energized}

    net=pp.create_empty_network(name=f"Equivalent island {island_id}",sn_mva=100.0,f_hz=50.0)
    bus_index={}
    for bus,kv,lat,lon,status in node_rows:
        bus_index[bus]=pp.create_bus(net,vn_kv=float(kv),name=bus,
                                     geodata=(float(lon),float(lat)) if lon is not None and lat is not None else None)
    pp.create_ext_grid(net,bus_index[source_bus],vm_pu=args.source_vm_pu,name=f"SOURCE:{source_bus}",
                       s_sc_max_mva=args.source_sc_max_mva,s_sc_min_mva=args.source_sc_min_mva,
                       rx_max=0.1,rx_min=0.1,r0x0_max=0.1,x0x_max=1.0,
                       r0x0_min=0.1,x0x_min=1.0)
    branch_type_counts={}
    for branch,a,b,from_kv,to_kv,kind,evidence in branch_rows:
        branch_type_counts[evidence]=branch_type_counts.get(evidence,0)+1
        if branch.startswith("BRIDGE:"):
            sn,hv,lv,vk,vkr,vk0,vkr0,tap_side,tap_neutral,tap_min,tap_max,tap_step=bridge[branch]
            idx=pp.create_transformer_from_parameters(net,bus_index[a],bus_index[b],sn_mva=sn,
                vn_hv_kv=hv,vn_lv_kv=lv,vk_percent=vk,vkr_percent=vkr,pfe_kw=0,i0_percent=0,
                shift_degree=0,tap_side=str(tap_side).lower(),tap_neutral=tap_neutral,
                tap_min=tap_min,tap_max=tap_max,tap_step_percent=tap_step,tap_pos=tap_neutral,
                name=branch)
            for col,val in (("vk0_percent",vk0),("vkr0_percent",vkr0),("mag0_percent",100.0),
                            ("mag0_rx",0.0),("si0_hv_partial",0.9),("vector_group","YNyn")):
                net.trafo.at[idx,col]=val
        elif branch.startswith("PTDTRAFO:"):
            code=branch.split(":",1)[1]
            sn,hv,lv,vk,vkr,vk0,vkr0,vector=ptd_trafo[code]
            solver_sn=float(sn)
            idx=pp.create_transformer_from_parameters(net,bus_index[a],bus_index[b],sn_mva=solver_sn,
                vn_hv_kv=hv,vn_lv_kv=lv,vk_percent=vk,vkr_percent=vkr,pfe_kw=0,i0_percent=0,
                shift_degree=0,name=branch)
            public_sn,design_sn,design_status=ptd_trafo_design[code]
            net.trafo.at[idx,"public_sn_mva"]=public_sn
            net.trafo.at[idx,"capacity_solver_status"]=design_status
            for col,val in (("vk0_percent",vk0),("vkr0_percent",vkr0),("mag0_percent",100.0),
                            ("mag0_rx",0.0),("si0_hv_partial",0.9),("vector_group","Dyn")):
                net.trafo.at[idx,col]=val
        elif branch.startswith("MVFEEDER:"):
            code=branch.split(":",1)[1]
            length,r,x,c,max_i,r0,x0=mv_line[code]
            idx=pp.create_line_from_parameters(net,bus_index[a],bus_index[b],length_km=max(float(length),0.001),
                r_ohm_per_km=r,x_ohm_per_km=x,c_nf_per_km=0.0,max_i_ka=max_i,name=branch)
            net.line.at[idx,"r0_ohm_per_km"]=r0;net.line.at[idx,"x0_ohm_per_km"]=x0
            net.line.at[idx,"c0_nf_per_km"]=0.0;net.line.at[idx,"endtemp_degree"]=80.0
            net.line.at[idx,"source_parameter_c_nf_per_km"]=c
            net.line.at[idx,"equivalent_shunt_status"]="ZERO_TO_AVOID_DUPLICATING_SHARED_CORRIDOR_CHARGING"
        elif branch.startswith("LVLINE:"):
            code=branch.split(":")[1]
            parallel,length,lv_r,lv_x,lv_max_i,cable,design_scenario=lv_design[code]
            idx=pp.create_line_from_parameters(net,bus_index[a],bus_index[b],length_km=length,
                r_ohm_per_km=lv_r,x_ohm_per_km=lv_x,c_nf_per_km=0,max_i_ka=lv_max_i,
                parallel=int(parallel),name=branch)
            net.line.at[idx,"r0_ohm_per_km"]=3*lv_r;net.line.at[idx,"x0_ohm_per_km"]=3*lv_x
            net.line.at[idx,"c0_nf_per_km"]=0.0;net.line.at[idx,"endtemp_degree"]=80.0
            net.line.at[idx,"cable_designation"]=cable
            net.line.at[idx,"design_scenario_id"]=design_scenario
        else:
            parser.error(f"Unsupported branch in validation island: {branch}")
    urban_customer_buses=[]
    urban_counts={"urban_ptds":0,"urban_closed_cables":0,"urban_open_ties":0,"urban_cable_km":0.0,
                  "urban_trunks":0,"urban_trunk_km":0.0,"replaced_star_feeder_km":0.0}
    if args.mv_urban_cable and args.mv_root_bus:
        mem=duckdb.connect()
        useg=mem.execute(f"SELECT * FROM '{args.mv_urban_cable/'mv_urban_cable_segment.parquet'}' WHERE root=?",[args.mv_root_bus]).df()
        uanc=mem.execute(f"SELECT * FROM '{args.mv_urban_cable/'mv_urban_cable_anchor.parquet'}' WHERE root=?",[args.mv_root_bus]).df()
        summary_u=json.loads((args.mv_urban_cable/"mv_urban_cable.summary.json").read_text())
        detour=float(summary_u["detour_factor_applied"])
        line_by_name={str(n):i for i,n in net.line.name.items()}
        urban_nodes=set(useg.from_node)|set(useg.to_node)
        for code in sorted(n for n in urban_nodes if not n.startswith(("ROOTANCHOR:","OSMTAP:","MVCUST:"))):
            i=line_by_name.get(f"MVFEEDER:{code}")
            if i is not None and bool(net.line.at[i,"in_service"]):
                net.line.at[i,"in_service"]=False
                urban_counts["urban_ptds"]+=1
                urban_counts["replaced_star_feeder_km"]+=float(net.line.at[i,"length_km"])
        def ubus(node,kv,feeder):
            if node.startswith("ROOTANCHOR:"):
                return bus_index[args.mv_root_bus]
            if node.startswith("OSMTAP:"):
                fkey=feeder.rsplit(":",1)[0]+":F00" if ":TIE" in feeder else feeder
                key=f"URBAN:{node}:{fkey}"  # one equivalent OSM-network path per feeder (as for star PTDs)
                if key not in bus_index:
                    bus_index[key]=pp.create_bus(net,vn_kv=float(kv),name=key)
                    dist=float(uanc.loc[uanc.anchor_id==node,"root_distance_km"].iloc[0])
                    # equivalent path along the public OSM MV network from the root busbar to the tap point
                    t=pp.create_line_from_parameters(net,bus_index[args.mv_root_bus],bus_index[key],
                        length_km=max(dist*detour,0.005),r_ohm_per_km=0.174085,x_ohm_per_km=0.30,c_nf_per_km=0.0,
                        max_i_ka=0.51,name=f"URBANTRUNK:{node}:{fkey}")  # 203-AL1/32-ST1A trunk class
                    net.line.at[t,"r0_ohm_per_km"]=0.174085*3;net.line.at[t,"x0_ohm_per_km"]=0.30*3
                    net.line.at[t,"c0_nf_per_km"]=0.0;net.line.at[t,"endtemp_degree"]=80.0
                    urban_counts["urban_trunks"]+=1;urban_counts["urban_trunk_km"]+=dist*detour
                return bus_index[key]
            if node.startswith("MVCUST:"):
                key=f"URBAN:{node}"
                if key not in bus_index:
                    bus_index[key]=pp.create_bus(net,vn_kv=float(kv),name=key)
                    urban_customer_buses.append(bus_index[key])
                return bus_index[key]
            return bus_index[f"PTD:MV:{node}"]
        for r in useg.itertuples():
            idx=pp.create_line_from_parameters(net,ubus(r.from_node,r.kv,r.feeder),ubus(r.to_node,r.kv,r.feeder),length_km=float(r.length_km),
                r_ohm_per_km=float(r.r_ohm_per_km),x_ohm_per_km=float(r.x_ohm_per_km),c_nf_per_km=float(r.c_nf_per_km),
                max_i_ka=float(r.max_i_ka),in_service=not bool(r.normally_open),name=f"URBANCABLE:{r.segment_id}")
            net.line.at[idx,"r0_ohm_per_km"]=4*float(r.r_ohm_per_km);net.line.at[idx,"x0_ohm_per_km"]=3*float(r.x_ohm_per_km)
            net.line.at[idx,"c0_nf_per_km"]=float(r.c_nf_per_km);net.line.at[idx,"endtemp_degree"]=90.0 if r.kind=="UNDERGROUND_CABLE" else 80.0
            if bool(r.normally_open):
                urban_counts["urban_open_ties"]+=1
            else:
                urban_counts["urban_closed_cables"]+=1;urban_counts["urban_cable_km"]+=float(r.length_km)
    for code in ptd_codes:
        gross_p,gross_q,pv_p,net_p,status=operating[code]
        for section in (1,2):
            pp.create_load(net,bus_index[f"LV:{code}:{section}"],p_mw=gross_p/2,q_mvar=gross_q/2,
                           name=f"GROSS:{code}:{section}")
        pp.create_sgen(net,bus_index[f"LV:{code}:2"],p_mw=pv_p,q_mvar=0,
                       sn_mva=max(float(pv_p),1e-6),name=f"PV:{code}")
    for resource_id,(mv_root_bus,station_code,p_mw,q_mvar,status) in mv_operating.items():
        if urban_customer_buses and mv_root_bus==args.mv_root_bus:
            # MV residual (station load minus PTD load) = MV customers: spread over the simulated customer substations
            share=1.0/len(urban_customer_buses)
            for k,b in enumerate(urban_customer_buses):
                pp.create_load(net,b,p_mw=p_mw*share,q_mvar=q_mvar*share,
                               name=f"{resource_id}:CUST{k:04d}",type="mv_station_residual")
            continue
        pp.create_load(net,bus_index[mv_root_bus],p_mw=p_mw,q_mvar=q_mvar,
                       name=resource_id,type="mv_station_residual")
    # Public PTD generation lacks inverter fault-current limits. Exclude the
    # synthetic PV fleet from SC current instead of inventing a k factor.
    net.sgen["current_source"]=False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore",FutureWarning)
        oltc_ids=[]
        if args.oltc:
            from pandapower.control import DiscreteTapControl
            for tid,tname in net.trafo.name.items():
                if str(tname).startswith("BRIDGE:"):
                    if "tap_changer_type" in net.trafo.columns:  # pandapower>=3 ignores taps without a type
                        net.trafo.at[tid,"tap_changer_type"]="Ratio"
                    DiscreteTapControl(net,tid,vm_lower_pu=args.oltc_deadband[0],
                                       vm_upper_pu=args.oltc_deadband[1],side="lv")
                    oltc_ids.append(tid)
        pp.runpp(net,algorithm="nr",init="flat",calculate_voltage_angles=True,
                 max_iteration=100,tolerance_mva=1e-8,numba=False,run_control=bool(oltc_ids))
    bus_result=net.res_bus.copy();bus_result["bus_id"]=net.bus.name
    line_result=net.res_line.copy();line_result["line_id"]=net.line.name
    trafo_result=net.res_trafo.copy();trafo_result["transformer_id"]=net.trafo.name
    for frame in (bus_result,line_result,trafo_result):
        frame["study_island_id"]=island_id
        frame["mv_root_bus"]=args.mv_root_bus
        frame["timestamp_utc"]=args.timestamp_utc
    bus_result.to_csv(args.output/"bus_results.csv",index=False)
    line_result.to_csv(args.output/"line_results.csv",index=False)
    trafo_result.to_csv(args.output/"transformer_results.csv",index=False)
    fault_frames=[]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore",FutureWarning)
        for fault_type in (() if args.skip_faults else ("3ph","2ph","1ph")):
            for case in ("max","min"):
                sc.calc_sc(net,case=case,fault=fault_type,branch_results=False)
                frame=net.res_bus_sc.copy();frame["bus_id"]=net.bus.name
                frame["fault_type"]=fault_type;frame["case"]=case
                frame["study_island_id"]=island_id
                frame["mv_root_bus"]=args.mv_root_bus
                frame["timestamp_utc"]=args.timestamp_utc
                fault_frames.append(frame)
    fault_result=(pd.concat(fault_frames,ignore_index=True) if fault_frames
                  else pd.DataFrame(columns=["ikss_ka","fault_type","bus_id","case"]))
    fault_result.to_csv(args.output/"fault_results.csv",index=False)
    lv=bus_result.loc[net.bus.vn_kv.to_numpy()==0.4,"vm_pu"]
    mv=bus_result.loc[(net.bus.vn_kv.to_numpy()>0.4)&(net.bus.vn_kv.to_numpy()<60),"vm_pu"]
    ext_p=float(net.res_ext_grid.p_mw.sum())
    total_load_p=float(net.load.p_mw.sum());pv_p=float(net.sgen.p_mw.sum())
    mv_load_mask=net.load.name.astype(str).str.startswith("MVLOAD:")
    mv_load_p=float(net.load.loc[mv_load_mask,"p_mw"].sum())
    lv_gross_load_p=float(net.load.loc[~mv_load_mask,"p_mw"].sum())
    losses=float(net.res_line.pl_mw.sum()+net.res_trafo.pl_mw.sum())
    balance=abs(ext_p+pv_p-total_load_p-losses)
    balance_basis=max(abs(ext_p),abs(total_load_p),abs(pv_p),1e-9)
    balance_relative=balance/balance_basis
    fault_counts=fault_result.groupby("fault_type").size().to_dict()
    fault_min=fault_result.groupby("fault_type").ikss_ka.min().to_dict()
    fault_max=fault_result.groupby("fault_type").ikss_ka.max().to_dict()
    disconnected_mv_load_count=base_mv_load_count-len(mv_operating)
    checks={
        "island_id":island_id,"base_buses":base_node_count,"base_branches":base_branch_count,
        "mv_root_bus":args.mv_root_bus,
        "buses":len(net.bus),"branches":len(branch_rows),
        "lines":len(net.line),"transformers":len(net.trafo),"ptds":len(ptd_codes),
        "open_switch_id":args.open_switch_id,
        "opened_equipment_id":opened_equipment,
        "switch_impact_status":impact_status,
        "predicted_deenergized_load_count":predicted_deenergized_loads,
        "actual_disconnected_nodes":len(disconnected_nodes),
        "actual_disconnected_ptds":len(disconnected_ptds),
        "actual_deenergized_load_count":2*len(disconnected_ptds)+disconnected_mv_load_count,
        "mv_load_resources":len(mv_operating),
        "mv_root_transformer_base_sn_mva":(bridge_design[args.mv_root_bus][0] if args.mv_root_bus else None),
        "mv_root_transformer_solver_sn_mva":(bridge_design[args.mv_root_bus][1] if args.mv_root_bus else None),
        "mv_urban_cable_enabled":bool(args.mv_urban_cable and args.mv_root_bus),**{k:(round(v,3) if isinstance(v,float) else v) for k,v in urban_counts.items()},"urban_mv_customer_substations":len(urban_customer_buses),
        "mv_root_transformer_design_status":(bridge_design[args.mv_root_bus][2] if args.mv_root_bus else None),
        "disconnected_mv_load_resources":disconnected_mv_load_count,
        "power_flow_converged":bool(net.converged),
        "lv_voltage_min_pu":float(lv.min()),"lv_voltage_max_pu":float(lv.max()),
        "mv_voltage_min_pu":float(mv.min()),"mv_voltage_max_pu":float(mv.max()),
        "max_line_loading_percent":float(net.res_line.loading_percent.max()),
        "max_transformer_loading_percent":float(net.res_trafo.loading_percent.max()),
        "voltage_violation_bus_count":int(((net.res_bus.vm_pu<0.9)|(net.res_bus.vm_pu>1.1)).sum()),
        "line_overload_count":int((net.res_line.loading_percent>100.0+1e-8).sum()),
        "transformer_overload_count":int((net.res_trafo.loading_percent>100.0+1e-8).sum()),
        "source_p_mw":ext_p,"source_q_mvar":float(net.res_ext_grid.q_mvar.sum()),
        "source_vm_pu":args.source_vm_pu,"faults_skipped":bool(args.skip_faults),
        "oltc_enabled":bool(args.oltc),"oltc_tap_positions":[int(net.trafo.at[t,"tap_pos"]) for t in oltc_ids],
        "oltc_tap_limits":[[int(net.trafo.at[t,"tap_min"]),int(net.trafo.at[t,"tap_max"])] for t in oltc_ids],"gross_load_p_mw":total_load_p,
        "lv_gross_load_p_mw":lv_gross_load_p,"mv_load_p_mw":mv_load_p,"pv_p_mw":pv_p,
        "network_losses_mw":losses,"active_power_balance_error_mw":balance,
        "active_power_balance_relative":balance_relative,
        "fault_rows":len(fault_result),
        "fault_types":sorted(fault_counts),
        "three_phase_fault_rows":int(fault_counts.get("3ph",0)),
        "two_phase_fault_rows":int(fault_counts.get("2ph",0)),
        "single_phase_ground_fault_rows":int(fault_counts.get("1ph",0)),
        "three_phase_min_fault_current_ka":float(fault_min.get("3ph",math.nan)),
        "three_phase_max_fault_current_ka":float(fault_max.get("3ph",math.nan)),
        "two_phase_min_fault_current_ka":float(fault_min.get("2ph",math.nan)),
        "two_phase_max_fault_current_ka":float(fault_max.get("2ph",math.nan)),
        "single_phase_ground_min_fault_current_ka":float(fault_min.get("1ph",math.nan)),
        "single_phase_ground_max_fault_current_ka":float(fault_max.get("1ph",math.nan)),
        "min_fault_current_ka":float(fault_result.ikss_ka.min()),
        "max_fault_current_ka":float(fault_result.ikss_ka.max()),
        "bridge_parameter_rows_used":branch_type_counts.get("SYNTHETIC_60_MV_BRIDGE",0),
        "ptd_transformer_parameter_rows_used":branch_type_counts.get("PUBLIC_PTD_CAPACITY_SYNTHETIC_IMPEDANCE",0),
        "zero_public_capacity_ptd_transformer_proxy_count":len(zero_capacity_ptds),
        "ptd_transformer_root_peak_design_count":sum(
            ptd_trafo_design[code][2] != 'BASE_PARAMETER_ONLY' for code in ptd_codes),
        "ptd_transformer_root_peak_upsized_count":sum(
            ptd_trafo_design[code][2] == 'ROOT_PEAK_UPSIZED_SIMULATION_SCENARIO' for code in ptd_codes),
        "mv_feeder_parameter_rows_used":branch_type_counts.get("OSM_NEARBY_VOLTAGE_SYNTHETIC_FEEDER",0)+branch_type_counts.get("DISTANT_OSM_VOLTAGE_PROXY_SYNTHETIC_FEEDER",0),
        "lv_line_parameter_rows_used":branch_type_counts.get("SYNTHETIC_LV_TOPOLOGY_AND_IMPEDANCE",0),
        "lv_design_distinct_ptds_used":int(net.line.loc[
            net.line.name.astype(str).str.startswith("LVLINE:"),"name"].astype(str)
            .str.split(":").str[1].nunique()),
        "lv_design_parallel_circuit_sum":int(net.line.loc[
            net.line.name.astype(str).str.startswith("LVLINE:"),"parallel"].sum()),
        "mv_equivalent_lines_with_zero_shunt":int(net.line.name.astype(str).str.startswith("MVFEEDER:").sum()),
    }
    errors=[]
    if not net.converged or balance>1e-5:
        errors.append("Power flow convergence or active-power balance failed")
    if not np.isfinite(lv).all() or not np.isfinite(mv).all():errors.append("Non-finite voltage result")
    if (checks["voltage_violation_bus_count"] or checks["line_overload_count"]
            or checks["transformer_overload_count"]):
        errors.append("Voltage, line-loading, or transformer-loading operating constraint failed")
    if checks["lv_design_distinct_ptds_used"]!=len(ptd_codes):
        errors.append("LV feeder design scenario did not cover every energized PTD")
    if not args.skip_faults and (len(fault_result)!=6*len(net.bus)
            or any(fault_counts.get(kind,0)!=2*len(net.bus) for kind in ("3ph","2ph","1ph"))
            or checks["min_fault_current_ka"]<=0):
        errors.append("Three-phase, phase-to-phase, or single-phase-ground max/min fault coverage failed")
    if switch_action and checks["actual_deenergized_load_count"]!=checks["predicted_deenergized_load_count"]:
        errors.append("Switch graph criticality and solved energized subnetwork disagree")
    report={
        "result":"FAIL" if errors else "PARTIAL",
        "fault_model_version":"IEC60909_3PH_2PH_1PH_GROUND_PROXY_V1",
        "timestamp_utc":args.timestamp_utc,"checks":checks,"errors":errors,
        "scope":"One complete 60-kV/MV/PTD/LV equivalent island using persisted parameter bindings" +
                (" with one synthetic switch-open N-1 action" if switch_action else ""),
        "limitations":[
            "MV feeders are straight synthetic root-to-PTD lines, not operator feeder routes",
            "MV star-equivalent shunt capacitance is set to zero because root-to-PTD proxy paths share unknown upstream corridors and summing catalog capacitance would double count charging",
            "LV topology, selected standard cable, parallel-feeder count, gross-load/PV placement and phase balance are design scenarios",
            "Station MV residual and its split across multiple MV roots are capacity-bounded allocation scenarios",
            "Source short-circuit strengths are explicit 1000/500-MVA defaults, not a matched public station record",
            "Source zero-sequence ratios r0/x0=0.1 and x0/x1=1.0 are explicit uncalibrated fault-study defaults",
            "Synthetic PTD PV is excluded from fault-current contribution because inverter current limits are unavailable",
            "Two-phase-to-ground faults and non-zero fault impedances are not included in this batch",
            "Zero-public-capacity PTDs carry zero load/PV and use an explicit 0.1-MVA transformer proxy only to keep their solver connection and flag them as uncalibrated",
            "PTD transformer capacity and parallel LV feeder count use separate root-peak design scenarios and do not replace public asset fields",
            "Balanced power flow does not replace the separate ABCN neutral-current studies",
            "Numerical convergence is computational validation, not validation of the real Portuguese network",
        ] + (["Opened switch is a synthetic branch gate, not an observed operator breaker action"]
             if switch_action else []),
    }
    (args.output/"validation.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if not args.no_persist_results:
        with duckdb.connect(str(args.database)) as con:
            con.execute("CREATE SCHEMA IF NOT EXISTS study")
            if args.mv_root_bus:
                prefix=("equivalent_mv_root_switch_contingency" if switch_action
                        else "equivalent_mv_root")
            else:
                prefix=("equivalent_island_switch_contingency" if switch_action
                        else "equivalent_island")
            for table,file in ((prefix+"_bus_results","bus_results.csv"),
                               (prefix+"_line_results","line_results.csv"),
                               (prefix+"_transformer_results","transformer_results.csv"),
                               (prefix+"_fault_results","fault_results.csv")):
                if file=="fault_results.csv":
                    con.execute(f"""CREATE OR REPLACE TABLE study.{table} AS
                        SELECT *,0.0::DOUBLE AS fault_resistance_ohm,
                               0.0::DOUBLE AS fault_reactance_ohm,
                               CASE WHEN "case"='max' THEN ? ELSE ? END::DOUBLE
                                   AS source_short_circuit_mva,
                               0.1::DOUBLE AS source_rx_ratio,
                               0.1::DOUBLE AS source_r0x0_ratio,
                               1.0::DOUBLE AS source_x0x1_ratio,
                               'IEC 60909'::VARCHAR AS calculation_standard,
                               'FAULT_SOURCE_ZERO_SEQUENCE_PROXY_V1'::VARCHAR
                                   AS parameter_scenario_id
                        FROM read_csv_auto(?,header=true)""",
                        [args.source_sc_max_mva,args.source_sc_min_mva,str(args.output/file)])
                else:
                    con.execute(f"CREATE OR REPLACE TABLE study.{table} AS SELECT * FROM read_csv_auto(?,header=true)",
                                [str(args.output/file)])
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return int(bool(errors))


if __name__=="__main__":
    raise SystemExit(main())
