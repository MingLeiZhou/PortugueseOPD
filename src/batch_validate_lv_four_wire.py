#!/usr/bin/env python3
"""Batch-solve synthetic PTD ABCN feeders at a shared or per-root UTC snapshot.

This is a national coverage/constraint audit of the *scenario* LV model. It
does not infer real LV phase assignments, feeder lengths, or cable ampacities.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/four_wire_national"
DESIGN_TIMESTAMP = "2025-07-22T11:45:00+00:00"
CONDUCTORS = "ABCN"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--timestamp-utc", default=DESIGN_TIMESTAMP)
    parser.add_argument("--observed-only",action="store_true",
                        help="Audit only PTDs whose station profile is observed at this instant")
    parser.add_argument("--feeder-layout",action="store_true",
                        help="Apply the explicitly synthetic parallel-feeder layout scenario")
    parser.add_argument("--peak-proxy",action="store_true",
                        help="Use each PTD peak-load proxy with zero PV as a load envelope")
    parser.add_argument("--peak-feeder-layout",action="store_true",
                        help="Apply feeder counts designed from the PTD peak-load envelope")
    parser.add_argument("--cable-designation",
                        help="Run an equal-section E-REDES 4-core standard cable sensitivity; installed type remains unknown")
    parser.add_argument("--design-scenario",action="store_true",
                        help="Use the per-PTD feeder count and E-REDES standard cable selected by LV_FEEDER_DESIGN_PEAK_V1")
    parser.add_argument("--transformer-impedance",action="store_true",
                        help="Include each PTD transformer positive/zero-sequence impedance in the ABCN source model")
    parser.add_argument("--root-peak-design",action="store_true",
                        help="Use each MV root's full-calendar peak timestamp plus the refined feeder and transformer design")
    parser.add_argument("--reverse-flow-scenario",action="store_true",
                        help="Use station-conserving independent gross-load/PV PTD allocations")
    args = parser.parse_args()
    if args.reverse_flow_scenario and args.peak_proxy:
        parser.error("Reverse-flow scenario requires a real UTC solar snapshot, not zero-PV peak-proxy load")
    if args.root_peak_design and (args.peak_proxy or args.reverse_flow_scenario or args.cable_designation
                                  or args.feeder_layout or args.peak_feeder_layout):
        parser.error("Root-peak design already fixes the timestamp, feeder, cable and transformer scenario")
    if args.design_scenario and args.cable_designation:
        parser.error("Choose either the per-PTD design scenario or one uniform cable sensitivity")
    if args.root_peak_design:
        args.design_scenario=True
        args.transformer_impedance=True
    if args.peak_feeder_layout:
        args.feeder_layout=True
    scenario_mode=("per_mv_root_annual_peak_root_design" if args.root_peak_design else
                   ("peak_proxy" if args.peak_proxy else "time_snapshot")+(
        "_design_scenario" if args.design_scenario else
        "_peak_layout" if args.peak_feeder_layout else
        "_snapshot_layout" if args.feeder_layout else "_single_feeder"))
    if args.reverse_flow_scenario:
        scenario_mode += "_reverse_flow"
    if args.transformer_impedance:
        scenario_mode += "_ptd_transformer"
    if args.output==OUT and args.cable_designation:
        safe_designation="".join(c if c.isalnum() else "_" for c in args.cable_designation)
        args.output=ROOT/"output/all_voltage/lv_cable_sensitivity"/safe_designation
        if args.reverse_flow_scenario:
            args.output=args.output/"reverse_flow"
    elif args.output==OUT and args.reverse_flow_scenario and not args.design_scenario:
        args.output=ROOT/"output/all_voltage/four_wire_reverse_flow"
    elif args.output==OUT:
        args.output=(ROOT/"output/all_voltage/four_wire_root_peak_design" if args.root_peak_design else
                     ROOT/"output/all_voltage/four_wire_design_transformer_peak" if args.design_scenario and args.peak_proxy and args.transformer_impedance else
                     ROOT/"output/all_voltage/four_wire_design_transformer_reverse_flow" if args.design_scenario and args.reverse_flow_scenario and args.transformer_impedance else
                     ROOT/"output/all_voltage/four_wire_design_transformer_snapshot" if args.design_scenario and args.transformer_impedance else
                     ROOT/"output/all_voltage/four_wire_design_reverse_flow" if args.design_scenario and args.reverse_flow_scenario else
                     ROOT/"output/all_voltage/four_wire_design_peak" if args.design_scenario and args.peak_proxy else
                     ROOT/"output/all_voltage/four_wire_design_snapshot" if args.design_scenario else
                     ROOT/"output/all_voltage/four_wire_peak_layout_holdout" if args.peak_feeder_layout and not args.peak_proxy else
                     ROOT/"output/all_voltage/four_wire_peak_layout" if args.peak_proxy and args.feeder_layout else
                     ROOT/"output/all_voltage/four_wire_peak_envelope" if args.peak_proxy else
                     ROOT/"output/all_voltage/four_wire_parallel" if args.feeder_layout else OUT)
    args.output.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(args.database), read_only=True) as con:
        line_structure = con.execute("""SELECT count(*),count(DISTINCT split_part(line_id,':',2)),
             min(length_km_proxy),max(length_km_proxy),count(DISTINCT impedance_archetype_id)
             FROM phase.lv_line_models""").fetchone()
        if line_structure[0] != 2*line_structure[1] or line_structure[2] != line_structure[3] or line_structure[4] != 1:
            parser.error("Batch solver requires two same-length lines and one impedance archetype per PTD")
        matrix_rows = con.execute("""SELECT from_conductor,to_conductor,r_ohm_per_km,x_ohm_per_km
            FROM phase.impedance_matrix WHERE archetype_id='LV_4WIRE_PROXY_V1'""").fetchall()
        if len(matrix_rows) != 16:
            parser.error("Expected complete ABCN 4x4 impedance matrix")
        cable=None
        if args.cable_designation:
            cable=con.execute("""SELECT designation,phase_conductor_count,phase_section_mm2,
                neutral_section_mm2,r_hot_ohm_per_km,x_ohm_per_km,permissible_current_a,
                source_sha256 FROM parameter.lv_cable_catalog WHERE designation=?""",
                [args.cable_designation]).fetchone()
            if (cable is None or cable[1]!=3 or cable[2]!=cable[3]
                    or not (cable[0].startswith("LXS 4 x ") or cable[0].startswith("LSXAV 4x"))):
                parser.error("Cable sensitivity requires a published equal-section four-core LXS/LSXAV type")
        layout_table=("phase.lv_feeder_layout_peak_scenario" if args.peak_feeder_layout or (args.peak_proxy and args.feeder_layout)
                      else "phase.lv_feeder_layout_scenario")
        design_table=("phase.lv_feeder_root_peak_design_scenario" if args.root_peak_design
                      else "phase.lv_feeder_design_scenario")
        load_table=("operating.ptd_reverse_flow_scenario_15min" if args.reverse_flow_scenario
                    else "operating.ptd_load_15min")
        load_p_expr="l.net_p_mw" if args.reverse_flow_scenario else "l.p_mw"
        load_q_expr="l.gross_q_mvar" if args.reverse_flow_scenario else "l.q_mvar"
        pv_expr="l.pv_p_mw" if args.reverse_flow_scenario else "coalesce(v.p_mw,0)"
        pv_join="" if args.reverse_flow_scenario else """LEFT JOIN operating.ptd_pv_15min v
          ON l.ptd_code=v.ptd_code AND l.timestamp_utc=v.timestamp_utc"""
        root_transformer_join=("""JOIN parameter.ptd_transformer_root_peak_design_scenario rt
          ON rt.ptd_code=l.ptd_code""" if args.root_peak_design else "")
        capacity_expr=("rt.design_sn_mva*1000" if args.root_peak_design
                       else "p.installed_transformer_kva")
        transformer_sn_expr=("rt.design_sn_mva" if args.root_peak_design else "t.sn_mva")
        root_bus_expr=("d.mv_root_bus" if args.root_peak_design else "NULL::VARCHAR")
        design_timestamp_expr=("strftime(d.timestamp_utc AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S+00:00')"
                               if args.root_peak_design else "NULL::VARCHAR")
        timestamp_filter=("l.timestamp_utc=strftime(d.timestamp_utc AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S+00:00')"
                          if args.root_peak_design else "l.timestamp_utc=?")
        query_params=[args.peak_proxy,args.peak_proxy,args.peak_proxy,
                      args.design_scenario,args.feeder_layout,
                      args.design_scenario,args.design_scenario,
                      args.design_scenario,args.design_scenario,
                      args.design_scenario]
        if not args.root_peak_design:
            query_params.append(args.timestamp_utc)
        query_params.append(args.observed_only)
        rows = con.execute(f"""WITH shares AS (
            SELECT ptd_code,
                   sum(ptd_total_share) FILTER (WHERE phase='A') AS a_share,
                   sum(ptd_total_share) FILTER (WHERE phase='B') AS b_share,
                   sum(ptd_total_share) FILTER (WHERE phase='C') AS c_share
            FROM phase.lv_load_shares GROUP BY 1
        )
        SELECT l.ptd_code,
               CASE WHEN ? THEN a.peak_proxy_mw ELSE {load_p_expr} END AS p_mw,
               CASE WHEN ? THEN a.peak_proxy_mw*tan(acos(0.97)) ELSE {load_q_expr} END AS q_mvar,
               CASE WHEN ? THEN 0 ELSE {pv_expr} END AS pv_mw,
               s.a_share,s.b_share,s.c_share,
               {capacity_expr} AS transformer_capacity_kva,
               CASE WHEN ? THEN d.feeder_count WHEN ? THEN f.feeder_count ELSE 1 END AS feeder_count,
               CASE WHEN ? THEN d.cable_designation ELSE NULL END AS design_cable_designation,
               CASE WHEN ? THEN d.r_hot_ohm_per_km ELSE NULL END AS design_r_ohm_per_km,
               CASE WHEN ? THEN d.x_ohm_per_km ELSE NULL END AS design_x_ohm_per_km,
               CASE WHEN ? THEN d.permissible_current_a ELSE NULL END AS design_permissible_current_a,
               CASE WHEN ? THEN d.section_length_km ELSE NULL END AS design_section_length_km,
               {transformer_sn_expr} AS sn_mva,t.lv_kv,t.vk_percent,t.vkr_percent,
               t.vk0_percent,t.vkr0_percent,t.vector_group,
               g.source_neutral_bond_ohm,
               l.allocation_status,l.profile_data_status,l.timestamp_utc AS scenario_timestamp_utc,
               p.installed_transformer_kva AS public_transformer_capacity_kva,
               {root_bus_expr} AS mv_root_bus,
               {design_timestamp_expr} AS design_timestamp_utc
        FROM {load_table} l
        JOIN operating.ptd_allocations a ON a.ptd_code=l.ptd_code
        JOIN shares s ON s.ptd_code=l.ptd_code
        JOIN candidate.ptd_public_attributes p ON p.ptd_code=l.ptd_code
        LEFT JOIN {layout_table} f ON f.ptd_code=l.ptd_code
        LEFT JOIN {design_table} d ON d.ptd_code=l.ptd_code
        JOIN parameter.ptd_transformer_parameters t ON t.ptd_code=l.ptd_code
        {root_transformer_join}
        JOIN phase.ptd_neutral_grounding g ON g.ptd_code=l.ptd_code
        {pv_join}
        WHERE {timestamp_filter} AND (?=false OR l.profile_data_status='PUBLIC_EREDES_STATION_AGGREGATE')
        ORDER BY l.ptd_code""", query_params).fetchall()
        national_count = con.execute("SELECT count(*) FROM equivalent.ptd_connections").fetchone()[0]
    if not rows:
        parser.error("No PTD load at requested UTC instant")
    codes = [row[0] for row in rows]
    if len(codes) != len(set(codes)):
        parser.error("Duplicate PTD rows at requested UTC instant")
    n = len(rows)
    scenario_timestamps = [row[24] for row in rows]
    public_capacity_kva = np.asarray([float(row[25] or 0) for row in rows],dtype=float)
    mv_root_buses = [row[26] for row in rows]
    design_timestamps = [row[27] for row in rows]
    numeric = np.asarray([row[1:9] for row in rows], dtype=float)
    load_p = numeric[:,0]
    load_q = numeric[:,1]
    pv = numeric[:,2]
    share = numeric[:,3:6]
    capacity_kva = numeric[:,6]
    feeder_count = numeric[:,7]
    if not np.all(np.isfinite(feeder_count)) or np.any(feeder_count<1):
        parser.error("Every PTD requires a positive feeder count")
    if np.max(np.abs(share.sum(axis=1)-1)) > 1e-9:
        parser.error("Phase shares do not sum to one")
    z = np.zeros((4,4), dtype=complex)
    for a,b,r,x in matrix_rows:
        z[CONDUCTORS.index(a),CONDUCTORS.index(b)] = complex(r,x)
    if cable:
        # Only self impedances and Iz are published here. The mutual terms
        # remain the explicit engineering proxy and are not attributed to E-REDES.
        np.fill_diagonal(z,complex(cable[4],cable[5]))
    if not np.allclose(z,z.T) or np.linalg.eigvalsh(z.real).min() <= 0:
        parser.error("ABCN impedance matrix is not symmetric positive definite")
    length = np.full(n,float(line_structure[2]),dtype=float)
    cable_names = np.full(n,cable[0] if cable else "PROXY",dtype=object)
    permissible_current = np.full(n,cable[6] if cable else np.nan,dtype=float)
    z_rows = np.tile(z,(n,1,1))
    if args.design_scenario:
        if any(row[9] is None for row in rows):
            parser.error("Per-PTD design scenario does not cover every selected PTD")
        cable_names=np.asarray([row[9] for row in rows],dtype=object)
        design_r=np.asarray([row[10] for row in rows],dtype=float)
        design_x=np.asarray([row[11] for row in rows],dtype=float)
        permissible_current=np.asarray([row[12] for row in rows],dtype=float)
        length=np.asarray([row[13] for row in rows],dtype=float)
        for conductor in range(4):
            z_rows[:,conductor,conductor]=design_r+1j*design_x
        for name in set(cable_names):
            sample=z_rows[np.flatnonzero(cable_names==name)[0]]
            if not np.allclose(sample,sample.T) or np.linalg.eigvalsh(sample.real).min()<=0:
                parser.error(f"Per-PTD cable matrix is invalid: {name}")
    transformer_zabc=np.zeros((n,3,3),dtype=complex)
    transformer_capacity_valid=np.asarray([float(row[14] or 0)>0 for row in rows],dtype=bool)
    if args.transformer_impedance:
        sn_mva=np.asarray([float(row[14] or 0) for row in rows],dtype=float)
        lv_kv=np.asarray([float(row[15] or 0) for row in rows],dtype=float)
        vk=np.asarray([float(row[16] or 0) for row in rows],dtype=float)
        vkr=np.asarray([float(row[17] or 0) for row in rows],dtype=float)
        vk0=np.asarray([float(row[18] or 0) for row in rows],dtype=float)
        vkr0=np.asarray([float(row[19] or 0) for row in rows],dtype=float)
        vector_group=np.asarray([str(row[20] or "") for row in rows],dtype=object)
        neutral_bond=np.asarray([float(row[21] or 0) for row in rows],dtype=float)
        if np.any(transformer_capacity_valid & (lv_kv<=0)):
            parser.error("Positive-capacity PTD transformers require a positive LV voltage")
        if np.any(transformer_capacity_valid & ((vkr<0)|(vk<vkr)|(vkr0<0)|(vk0<vkr0))):
            parser.error("PTD transformer sequence impedance magnitudes are invalid")
        if np.any(transformer_capacity_valid & ~np.char.startswith(vector_group.astype(str),'Dyn')):
            parser.error("ABCN transformer source currently requires a Dyn-family model")
        base_ohm=np.zeros(n,dtype=float)
        base_ohm[transformer_capacity_valid]=lv_kv[transformer_capacity_valid]**2/sn_mva[transformer_capacity_valid]
        r1=vkr/100*base_ohm
        x1=np.sqrt(np.maximum(vk*vk-vkr*vkr,0))/100*base_ohm
        r0=vkr0/100*base_ohm
        x0=np.sqrt(np.maximum(vk0*vk0-vkr0*vkr0,0))/100*base_ohm
        z1=r1+1j*x1; z0=r0+1j*x0
        self_z=(z0+2*z1)/3
        mutual_z=(z0-z1)/3
        transformer_zabc[:]=mutual_z[:,None,None]
        for conductor in range(3):
            transformer_zabc[:,conductor,conductor]=self_z
    else:
        neutral_bond=np.zeros(n,dtype=float)
    s_load = ((load_p+1j*load_q)/feeder_count)[:,None]*share*1e6
    pv_phase = (pv/feeder_count)[:,None]*1e6/3
    s1 = np.zeros((n,4), dtype=complex)
    s2 = np.zeros((n,4), dtype=complex)
    s1[:,:3] = s_load/2+pv_phase/2
    s2[:,:3] = s_load/2-pv_phase/2
    # l.p_mw is already the station-derived net load. The matching added PV
    # in s1 and distal PV injection in s2 cancel at PTD level.
    net_input_balance_mw = np.max(np.abs(
        np.sum((s1+s2)[:,:3],axis=1).real*feeder_count/1e6-load_p))
    vln = 400/math.sqrt(3)
    source = np.asarray([vln*np.exp(1j*a) for a in (0,-2*math.pi/3,2*math.pi/3)]+[0j])
    v1 = np.tile(source,(n,1))
    v2 = np.tile(source,(n,1))
    converged = np.zeros(n,dtype=bool)
    failed = np.zeros(n,dtype=bool)
    iterations = np.zeros(n,dtype=int)
    for iteration in range(1,101):
        active = ~(converged|failed)
        if not active.any():
            break
        delta1 = v1[active,:3]-v1[active,3,None]
        delta2 = v2[active,:3]-v2[active,3,None]
        bad = (np.min(np.abs(delta1),axis=1)<100) | (np.min(np.abs(delta2),axis=1)<100)
        positions = np.flatnonzero(active)
        failed[positions[bad]] = True
        positions = positions[~bad]
        if not len(positions):
            continue
        i1 = np.zeros((len(positions),4),dtype=complex)
        i2 = np.zeros_like(i1)
        i1[:,:3] = np.conj(s1[positions,:3]/delta1[~bad])
        i2[:,:3] = np.conj(s2[positions,:3]/delta2[~bad])
        i1[:,3] = -i1[:,:3].sum(axis=1)
        i2[:,3] = -i2[:,:3].sum(axis=1)
        total_transformer_phase_current=(i1[:,:3]+i2[:,:3])*feeder_count[positions,None]
        transformer_drop=np.einsum("nij,nj->ni",transformer_zabc[positions],total_transformer_phase_current)
        feeder_head=np.tile(source,(len(positions),1))
        feeder_head[:,:3]-=transformer_drop
        feeder_head[:,3]=neutral_bond[positions]*(-np.sum(total_transformer_phase_current,axis=1))
        drop1=np.einsum("nij,nj->ni",z_rows[positions],i1+i2)*length[positions,None]
        drop2=np.einsum("nij,nj->ni",z_rows[positions],i2)*length[positions,None]
        new1 = feeder_head-drop1
        new2 = new1-drop2
        change = np.maximum(np.max(np.abs(new1-v1[positions]),axis=1),
                            np.max(np.abs(new2-v2[positions]),axis=1))
        invalid = ~np.isfinite(change)
        failed[positions[invalid]] = True
        good = ~invalid
        v1[positions[good]] = new1[good]
        v2[positions[good]] = new2[good]
        iterations[positions[good]] = iteration
        converged[positions[good & (change<1e-8)]] = True
    failed |= ~(converged|failed)
    min_pu = np.full(n,np.nan)
    max_pu = np.full(n,np.nan)
    max_neutral_v = np.full(n,np.nan)
    max_neutral_a = np.full(n,np.nan)
    loss_kw = np.full(n,np.nan)
    balance_va = np.full(n,np.nan)
    source_kva = np.full(n,np.nan)
    transformer_loss_kw = np.full(n,np.nan)
    transformer_secondary_min_pu = np.full(n,np.nan)
    max_conductor_a = np.full(n,np.nan)
    good = np.flatnonzero(converged)
    if len(good):
        delta1 = v1[good,:3]-v1[good,3,None]
        delta2 = v2[good,:3]-v2[good,3,None]
        i1 = np.zeros((len(good),4),dtype=complex)
        i2 = np.zeros_like(i1)
        i1[:,:3] = np.conj(s1[good,:3]/delta1)
        i2[:,:3] = np.conj(s2[good,:3]/delta2)
        i1[:,3] = -i1[:,:3].sum(axis=1)
        i2[:,3] = -i2[:,:3].sum(axis=1)
        branch1 = i1+i2
        branch2 = i2
        total_transformer_phase_current=branch1[:,:3]*feeder_count[good,None]
        transformer_drop=np.einsum("nij,nj->ni",transformer_zabc[good],total_transformer_phase_current)
        feeder_head=np.tile(source,(len(good),1))
        feeder_head[:,:3]-=transformer_drop
        feeder_head[:,3]=neutral_bond[good]*(-np.sum(total_transformer_phase_current,axis=1))
        transformer_secondary_min_pu[good]=np.min(
            np.abs(feeder_head[:,:3]-feeder_head[:,3,None]),axis=1)/vln
        max_conductor_a[good] = np.maximum(np.max(np.abs(branch1),axis=1),
                                           np.max(np.abs(branch2),axis=1))
        min_pu[good] = np.minimum(np.min(np.abs(delta1),axis=1),
                                  np.min(np.abs(delta2),axis=1))/vln
        max_pu[good] = np.maximum(np.max(np.abs(delta1),axis=1),
                                  np.max(np.abs(delta2),axis=1))/vln
        max_neutral_v[good] = np.maximum(np.abs(v1[good,3]),np.abs(v2[good,3]))
        max_neutral_a[good] = np.maximum(np.abs(branch1[:,3]),np.abs(branch2[:,3]))
        src_power = np.sum(source[:3]*np.conj(total_transformer_phase_current),axis=1)
        load_power = np.sum(s1[good,:3]+s2[good,:3],axis=1)*feeder_count[good]
        drop1=np.einsum("nij,nj->ni",z_rows[good],branch1)*length[good,None]
        drop2=np.einsum("nij,nj->ni",z_rows[good],branch2)*length[good,None]
        line_loss = np.sum(np.conj(branch1)*drop1,axis=1) + \
                    np.sum(np.conj(branch2)*drop2,axis=1)
        line_loss_total=line_loss*feeder_count[good]
        transformer_loss=np.sum(
            np.conj(total_transformer_phase_current)*transformer_drop,axis=1)
        transformer_loss += neutral_bond[good]*np.abs(np.sum(total_transformer_phase_current,axis=1))**2
        loss_kw[good] = line_loss_total.real/1000
        transformer_loss_kw[good]=transformer_loss.real/1000
        balance_va[good] = np.abs(src_power-load_power-line_loss_total-transformer_loss)
        source_kva[good] = np.abs(src_power)/1000
    args.output.mkdir(parents=True,exist_ok=True)
    result_path = args.output/"ptd_snapshot.csv"
    with result_path.open("w",encoding="utf-8") as handle:
        handle.write("ptd_code,timestamp_utc,status,iterations,min_phase_voltage_pu,max_phase_voltage_pu,max_neutral_voltage_v,max_neutral_current_a,source_kva,transformer_capacity_kva,transformer_loading_pct,line_loss_kw,power_balance_va,load_p_mw,pv_p_mw,feeder_count,allocation_status,profile_data_status,cable_designation,max_conductor_current_a,catalog_permissible_current_a,scenario_mode,transformer_loss_kw,transformer_secondary_min_voltage_pu,transformer_impedance_included,public_transformer_capacity_kva,mv_root_bus\n")
        for k,code in enumerate(codes):
            status = "CONVERGED" if converged[k] else "FAILED_VOLTAGE_COLLAPSE_OR_NONCONVERGENCE"
            loading = source_kva[k]/capacity_kva[k]*100 if capacity_kva[k]>0 else math.nan
            fields = (code,scenario_timestamps[k],status,int(iterations[k]),min_pu[k],max_pu[k],max_neutral_v[k],
                      max_neutral_a[k],source_kva[k],capacity_kva[k],loading,loss_kw[k],
                      balance_va[k],load_p[k],pv[k],int(feeder_count[k]),rows[k][22],rows[k][23],
                      cable_names[k],max_conductor_a[k],permissible_current[k],
                      scenario_mode,transformer_loss_kw[k],transformer_secondary_min_pu[k],
                      args.transformer_impedance,public_capacity_kva[k],mv_root_buses[k])
            handle.write(",".join("" if isinstance(v,float) and math.isnan(v) else str(v) for v in fields)+"\n")
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        table = ("lv_fourwire_root_peak_design_snapshot" if args.root_peak_design else
                 "lv_fourwire_design_transformer_reverse_flow_snapshot" if args.design_scenario and args.reverse_flow_scenario and args.transformer_impedance else
                 "lv_fourwire_design_transformer_peak_snapshot" if args.design_scenario and args.peak_proxy and args.transformer_impedance else
                 "lv_fourwire_design_transformer_snapshot" if args.design_scenario and args.transformer_impedance else
                 "lv_fourwire_design_reverse_flow_snapshot" if args.design_scenario and args.reverse_flow_scenario else
                 "lv_fourwire_design_peak_snapshot" if args.design_scenario and args.peak_proxy else
                 "lv_fourwire_design_snapshot" if args.design_scenario else
                 "lv_fourwire_reverse_flow_snapshot" if args.reverse_flow_scenario else
                 "lv_fourwire_peak_layout_solar_snapshot" if args.peak_feeder_layout and not args.peak_proxy and args.timestamp_utc==DESIGN_TIMESTAMP else
                 "lv_fourwire_peak_layout_holdout_snapshot" if args.peak_feeder_layout and not args.peak_proxy else
                 "lv_fourwire_peak_layout_snapshot" if args.peak_proxy and args.feeder_layout else
                 "lv_fourwire_peak_envelope_snapshot" if args.peak_proxy else
                 "lv_fourwire_parallel_snapshot" if args.feeder_layout and args.timestamp_utc==DESIGN_TIMESTAMP else
                 "lv_fourwire_parallel_holdout_snapshot" if args.feeder_layout else
                 "lv_fourwire_observed_snapshot" if args.observed_only else
                 "lv_fourwire_national_snapshot")
        if cable:
            con.execute("""CREATE TABLE IF NOT EXISTS study.lv_fourwire_cable_sensitivity AS
                SELECT * FROM read_csv_auto(?,header=true) WHERE 1=0""",[str(result_path)])
            con.execute("""ALTER TABLE study.lv_fourwire_cable_sensitivity
                ADD COLUMN IF NOT EXISTS scenario_mode VARCHAR""")
            con.execute("""ALTER TABLE study.lv_fourwire_cable_sensitivity
                ADD COLUMN IF NOT EXISTS transformer_loss_kw DOUBLE""")
            con.execute("""ALTER TABLE study.lv_fourwire_cable_sensitivity
                ADD COLUMN IF NOT EXISTS transformer_secondary_min_voltage_pu DOUBLE""")
            con.execute("""ALTER TABLE study.lv_fourwire_cable_sensitivity
                ADD COLUMN IF NOT EXISTS transformer_impedance_included BOOLEAN""")
            con.execute("""DELETE FROM study.lv_fourwire_cable_sensitivity
                WHERE cable_designation=? AND timestamp_utc=?
                  AND (scenario_mode=? OR scenario_mode IS NULL)""",
                [cable[0],args.timestamp_utc,scenario_mode])
            con.execute("""INSERT INTO study.lv_fourwire_cable_sensitivity
                SELECT * FROM read_csv_auto(?,header=true)""",[str(result_path)])
        else:
            con.execute(f"CREATE OR REPLACE TABLE study.{table} AS SELECT * FROM read_csv_auto(?,header=true)",
                        [str(result_path)])
        fuse_summary = (0, None, None)
        if args.root_peak_design:
            fuse_summary = con.execute(f"""SELECT
                count(*) FILTER (WHERE s.status='CONVERGED'
                  AND s.max_conductor_current_a>c.fuse_service_current_a+1e-9),
                max(s.max_conductor_current_a/c.fuse_service_current_a),
                min(c.fuse_service_current_a-s.max_conductor_current_a)
                FROM study.{table} s
                JOIN parameter.lv_cable_catalog c
                  ON c.designation=s.cable_designation""").fetchone()
    checks = {
        "national_ptds":national_count,
        "ptds_with_synchronized_load":n,
        "ptds_missing_load_at_snapshot":national_count-n,
        "ptds_using_imputed_station_profile":sum(r[23]!='PUBLIC_EREDES_STATION_AGGREGATE' for r in rows),
        "ptds_with_multiple_parallel_feeders":int(np.sum(feeder_count>1)),
        "converged_ptds":int(converged.sum()),
        "failed_ptds":int(failed.sum()),
        "converged_below_0_9_pu":int(np.sum(converged & (min_pu<0.9))),
        "converged_above_1_1_pu":int(np.sum(converged & (max_pu>1.1))),
        "converged_above_100_pct_transformer_loading":int(np.sum(converged & (source_kva>capacity_kva))),
        "zero_capacity_ptds_in_snapshot":int(np.sum(capacity_kva<=0)),
        "zero_capacity_ptds_with_positive_load":int(np.sum((capacity_kva<=0) & (load_p>1e-12))),
        "zero_public_capacity_ptds":int(np.sum(public_capacity_kva<=0)),
        "zero_public_capacity_ptds_with_positive_load":int(np.sum((public_capacity_kva<=0) & (load_p>1e-12))),
        "min_converged_phase_voltage_pu":float(np.nanmin(min_pu)),
        "max_converged_phase_voltage_pu":float(np.nanmax(max_pu)),
        "max_converged_neutral_current_a":float(np.nanmax(max_neutral_a)),
        "max_converged_power_balance_va":float(np.nanmax(balance_va)),
        "max_ptd_net_input_balance_mw":float(net_input_balance_mw),
        "ptds_with_positive_transformer_capacity":int(np.sum(transformer_capacity_valid)),
        "ptds_with_zero_transformer_capacity":int(np.sum(~transformer_capacity_valid)),
        "min_transformer_secondary_voltage_pu":float(np.nanmin(transformer_secondary_min_pu)),
        "total_transformer_loss_kw":float(np.nansum(transformer_loss_kw)),
        "distinct_scenario_timestamps":len(set(scenario_timestamps)),
        "distinct_mv_roots":len(set(x for x in mv_root_buses if x is not None)),
        "root_peak_timestamp_reference_mismatches":sum(
            actual != expected for actual,expected in zip(scenario_timestamps,design_timestamps)
        ) if args.root_peak_design else 0,
        "root_peak_missing_root_references":sum(x is None for x in mv_root_buses)
            if args.root_peak_design else 0,
        "scenario_timestamp_min":min(scenario_timestamps),
        "scenario_timestamp_max":max(scenario_timestamps),
    }
    if cable or args.design_scenario:
        checks["cable_ampacity_exceedances"] = int(np.sum(converged & (max_conductor_a>permissible_current)))
        checks["max_conductor_current_a"] = float(np.nanmax(max_conductor_a))
        checks["minimum_catalog_permissible_current_a"] = float(np.nanmin(permissible_current))
        checks["distinct_cable_designations"] = int(len(set(cable_names)))
    if args.root_peak_design:
        checks["public_fuse_service_current_exceedances"] = int(fuse_summary[0])
        checks["maximum_conductor_to_public_fuse_current_ratio"] = float(fuse_summary[1])
        checks["minimum_public_fuse_current_margin_a"] = float(fuse_summary[2])
    internal_errors = []
    if n>national_count or checks["converged_ptds"]+checks["failed_ptds"] != n:
        internal_errors.append("Coverage accounting failed")
    if checks["max_converged_power_balance_va"]>1e-3:
        internal_errors.append("Converged case power balance failed")
    if checks["max_ptd_net_input_balance_mw"]>1e-10:
        internal_errors.append("Gross load and embedded PV do not recover PTD net load")
    if args.root_peak_design:
        if n != national_count:
            internal_errors.append("Root-peak scenario does not cover every national PTD")
        if checks["failed_ptds"]:
            internal_errors.append("Some root-peak ABCN cases did not converge")
        if checks["converged_below_0_9_pu"] or checks["converged_above_1_1_pu"]:
            internal_errors.append("Root-peak ABCN voltage constraint failed")
        if checks["converged_above_100_pct_transformer_loading"]:
            internal_errors.append("Root-peak transformer capacity constraint failed")
        if checks.get("cable_ampacity_exceedances",0):
            internal_errors.append("Root-peak cable ampacity constraint failed")
        if checks.get("public_fuse_service_current_exceedances",0):
            internal_errors.append("Root-peak public cable service-fuse current constraint failed")
        if (checks["root_peak_timestamp_reference_mismatches"]
                or checks["root_peak_missing_root_references"]):
            internal_errors.append("Root-peak UTC or MV-root reference integrity failed")
    report = {"result":"FAIL" if internal_errors else "PASS" if args.root_peak_design else "PARTIAL",
              "timestamp_utc":None if args.root_peak_design else args.timestamp_utc,
              "timestamp_mode":"PER_MV_ROOT_ANNUAL_PEAK" if args.root_peak_design else "SHARED_UTC_INSTANT",
              "cable_designation_scenario":cable[0] if cable else None,
              "cable_catalog_source_sha256":cable[7] if cable else None,
              "scenario_mode":scenario_mode,
              "observed_only":args.observed_only,
              "feeder_layout_scenario":args.feeder_layout,
              "peak_proxy_load_envelope":args.peak_proxy,
              "peak_feeder_layout_scenario":args.peak_feeder_layout,
              "per_ptd_design_scenario":args.design_scenario,
              "root_peak_design_scenario":args.root_peak_design,
              "ptd_transformer_impedance_included":args.transformer_impedance,
              "reverse_flow_scenario":args.reverse_flow_scenario,
              "checks":checks,"errors":internal_errors,
              "scope":("National synthetic ABCN feeder audit at each MV root's own full-calendar peak instant"
                       if args.root_peak_design else "National synthetic ABCN feeder audit at one shared UTC instant"),
              "limitations":["Topology, phase shares and line impedance are uncalibrated scenarios",
                             "PTD positive-sequence transformer parameters use public standards with inferred asset types; zero sequence remains simulated" if args.transformer_impedance else "Ideal grounded neutral source excludes PTD transformer impedance",
                             "No observed LV line ampacity or independent LV voltage measurements"] +
                            ([("Zero-public-capacity PTDs use an explicit 0.1 MVA uncalibrated connection design and have zero modeled load"
                               if args.root_peak_design else
                               "Zero-public-capacity PTDs use zero transformer impedance and have zero modeled load"),
                              "Dyn5 positive/zero-sequence equivalents do not represent transformer saturation or tap control"] if args.transformer_impedance else []) +
                            (["Per-PTD cable types and feeder counts are design scenarios, not installed-asset observations",
                              "Mutual conductor impedances remain engineering proxies"] if args.design_scenario else []) +
                            (["Cable type is assigned uniformly as a sensitivity scenario, not an installed-asset observation",
                              "Mutual conductor impedances remain engineering proxies"] if cable else []) +
                            (["Gross-load and PV placement is an unobserved station-conserving scenario"]
                             if args.reverse_flow_scenario else [])}
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    return int(bool(internal_errors))


if __name__ == "__main__":
    raise SystemExit(main())
