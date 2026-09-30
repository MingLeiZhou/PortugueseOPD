#!/usr/bin/env python3
"""Solve one synthetic LV feeder with explicit A/B/C/neutral conductors.

Uses the persisted 4x4 impedance matrix and time-aligned PTD load/PV views.
The feeder geometry, phase shares, PV location and impedance remain scenarios.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from pathlib import Path

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
OUT = ROOT / "output/all_voltage/four_wire_pilot"
CONDUCTORS = "ABCN"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DATABASE)
    parser.add_argument("--ptd-code", default="1311D2033100")
    parser.add_argument("--timestamp-utc", default="2025-07-22T11:45:00+00:00")
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--read-only", action="store_true",
                        help="Write CSV/JSON outputs only; do not update study tables in the input database.")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(args.database), read_only=True) as con:
        branches = con.execute("""SELECT line_id,from_bus,to_bus,length_km_proxy,impedance_archetype_id
            FROM phase.lv_line_models WHERE from_bus LIKE ? OR to_bus LIKE ? ORDER BY line_id""",
            [f"PTD:LV:{args.ptd_code}", f"LV:{args.ptd_code}:%"]).fetchall()
        # Exact PTD prefix prevents a similarly named identifier from entering this feeder.
        branches = [r for r in branches if r[1] in {f"PTD:LV:{args.ptd_code}",
                     f"LV:{args.ptd_code}:1",f"LV:{args.ptd_code}:2"}
                    and r[2].startswith(f"LV:{args.ptd_code}:")]
        phase_rows = con.execute("""SELECT load_id,phase,p_mw,q_mvar
            FROM operating.lv_phase_load_15min WHERE ptd_code=? AND timestamp_utc=?""",
            [args.ptd_code,args.timestamp_utc]).fetchall()
        pv_row = con.execute("""SELECT p_mw FROM operating.ptd_pv_15min
            WHERE ptd_code=? AND timestamp_utc=?""",[args.ptd_code,args.timestamp_utc]).fetchone()
        load_buses = dict(con.execute("""SELECT load_id,bus_id FROM equivalent.lv_loads
            WHERE source_ptd_code=?""",[args.ptd_code]).fetchall())
        matrix_rows = con.execute("""SELECT from_conductor,to_conductor,r_ohm_per_km,x_ohm_per_km
            FROM phase.impedance_matrix WHERE archetype_id='LV_4WIRE_PROXY_V1'""").fetchall()
        neutral_row = con.execute("""SELECT source_neutral_bond_ohm FROM phase.ptd_neutral_grounding
            WHERE ptd_code=?""",[args.ptd_code]).fetchone()
    if len(branches)!=2 or len(phase_rows)!=6 or len(matrix_rows)!=16 or not pv_row or not neutral_row:
        parser.error("PTD lacks the expected two-branch ABCN feeder or aligned load/PV input")
    if float(neutral_row[0])!=0:
        parser.error("This pilot requires a grounded source neutral")
    z_per_km = np.zeros((4,4),dtype=complex)
    for a,b,r,x in matrix_rows:
        z_per_km[CONDUCTORS.index(a),CONDUCTORS.index(b)] = complex(r,x)
    if not np.allclose(z_per_km,z_per_km.T) or np.min(np.linalg.eigvalsh(z_per_km.real))<=0:
        parser.error("Four-conductor impedance matrix is not symmetric positive definite")
    root=f"PTD:LV:{args.ptd_code}"
    children=defaultdict(list)
    branch_by_child={}
    for line_id,parent,child,length,archetype in branches:
        if child in branch_by_child or length<=0:
            parser.error("Duplicate child or nonpositive LV line length")
        children[parent].append(child)
        branch_by_child[child]=(line_id,parent,float(length))
    order=[]
    queue=[root]
    while queue:
        parent=queue.pop(0)
        for child in children[parent]:
            order.append(child)
            queue.append(child)
    if len(order)!=len(branches):
        parser.error("LV feeder is disconnected or cyclic")
    s_by_bus={bus:np.zeros(4,dtype=complex) for bus in [root,*order]}
    for load_id,phase,p,q in phase_rows:
        s_by_bus[load_buses[load_id]][CONDUCTORS.index(phase)] += complex(p,q)*1e6
    pv_w=float(pv_row[0])*1e6
    # Gross load is net load plus PV. The simulated PV injection is at the
    # distal node; this location is explicitly synthetic.
    load_nodes=sorted({load_buses[load_id] for load_id,_,_,_ in phase_rows})
    for node in load_nodes:
        s_by_bus[node][:3] += pv_w/(3*len(load_nodes))
    distal=order[-1]
    s_by_bus[distal][:3] -= pv_w/3
    vln=400/math.sqrt(3)
    source=np.array([vln*np.exp(1j*angle) for angle in (0,-2*math.pi/3,2*math.pi/3)]+[0j])
    voltages={node:source.copy() for node in [root,*order]}
    branch_current={node:np.zeros(4,dtype=complex) for node in order}
    converged=False
    for iteration in range(1,101):
        load_current={}
        for node in [root,*order]:
            i=np.zeros(4,dtype=complex)
            for phase in range(3):
                delta=voltages[node][phase]-voltages[node][3]
                if abs(delta)<100:
                    parser.error("Voltage collapse in four-wire iteration")
                i[phase]=np.conj(s_by_bus[node][phase]/delta)
            i[3]=-sum(i[:3])
            load_current[node]=i
        for node in reversed(order):
            branch_current[node]=load_current[node]+sum((branch_current[c] for c in children[node]),np.zeros(4,dtype=complex))
        updated={root:source.copy()}
        for node in order:
            _,parent,length=branch_by_child[node]
            updated[node]=updated[parent]-z_per_km*length@branch_current[node]
        change=max(np.max(np.abs(updated[node]-voltages[node])) for node in order)
        voltages=updated
        if change<1e-8:
            converged=True
            break
    # Re-evaluate currents at the converged voltages for the power audit.
    for node in reversed(order):
        i=np.zeros(4,dtype=complex)
        for phase in range(3):
            i[phase]=np.conj(s_by_bus[node][phase]/(voltages[node][phase]-voltages[node][3]))
        i[3]=-sum(i[:3])
        branch_current[node]=i+sum((branch_current[c] for c in children[node]),np.zeros(4,dtype=complex))
    source_current=sum((branch_current[c] for c in children[root]),np.zeros(4,dtype=complex))
    source_power=np.dot(source,np.conj(source_current))
    load_power=sum((np.sum(s[:3]) for s in s_by_bus.values()),0j)
    line_loss=sum((np.vdot(branch_current[node],z_per_km*branch_by_child[node][2]@branch_current[node])
                   for node in order),0j)
    balance=source_power-load_power-line_loss
    node_rows=[]
    for node in [root,*order]:
        for phase in range(3):
            node_rows.append({"bus_id":node,"phase":CONDUCTORS[phase],
                              "phase_to_neutral_v":abs(voltages[node][phase]-voltages[node][3]),
                              "voltage_pu":abs(voltages[node][phase]-voltages[node][3])/vln,
                              "neutral_to_ground_v":abs(voltages[node][3])})
    line_rows=[]
    for node in order:
        line_id,parent,length=branch_by_child[node]
        for k,conductor in enumerate(CONDUCTORS):
            line_rows.append({"line_id":line_id,"from_bus":parent,"to_bus":node,
                              "conductor":conductor,"current_a":abs(branch_current[node][k])})
    for filename,rows in (("node_results.csv",node_rows),("conductor_currents.csv",line_rows)):
        with (args.output/filename).open("w",newline="",encoding="utf-8") as handle:
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
            writer.writeheader();writer.writerows(rows)
    if not args.read_only:
        with duckdb.connect(str(args.database)) as con:
            con.execute("CREATE SCHEMA IF NOT EXISTS study")
            con.execute("""CREATE TABLE IF NOT EXISTS study.lv_fourwire_nodes AS
                SELECT ''::VARCHAR AS ptd_code,''::VARCHAR AS timestamp_utc,*
                FROM read_csv_auto(?,header=true) WHERE false""",[str(args.output/"node_results.csv")])
            con.execute("""CREATE TABLE IF NOT EXISTS study.lv_fourwire_currents AS
                SELECT ''::VARCHAR AS ptd_code,''::VARCHAR AS timestamp_utc,*
                FROM read_csv_auto(?,header=true) WHERE false""",[str(args.output/"conductor_currents.csv")])
            for table,filename in (("lv_fourwire_nodes","node_results.csv"),
                                   ("lv_fourwire_currents","conductor_currents.csv")):
                con.execute(f"DELETE FROM study.{table} WHERE ptd_code=? AND timestamp_utc=?",
                            [args.ptd_code,args.timestamp_utc])
                con.execute(f"INSERT INTO study.{table} SELECT ?::VARCHAR,?::VARCHAR,* "
                            "FROM read_csv_auto(?,header=true)",
                            [args.ptd_code,args.timestamp_utc,str(args.output/filename)])
    min_pu=min(r["voltage_pu"] for r in node_rows)
    max_neutral_v=max(r["neutral_to_ground_v"] for r in node_rows)
    max_neutral_a=max(r["current_a"] for r in line_rows if r["conductor"]=="N")
    errors=[]
    if not converged or abs(balance)>1e-3:
        errors.append("Four-wire convergence or complex-power balance failed")
    if min_pu<0.9 or min_pu>1.1:
        errors.append("Pilot phase voltage outside configured 0.9-1.1 pu bound")
    report={"result":"FAIL" if errors else "PARTIAL", "ptd_code":args.ptd_code,
            "timestamp_utc":args.timestamp_utc,
            "checks":{"converged":converged,"iterations":iteration,"max_voltage_change_v":float(change),
                      "min_phase_to_neutral_voltage_pu":min_pu,
                      "max_neutral_to_ground_v":max_neutral_v,
                      "max_neutral_conductor_current_a":max_neutral_a,
                      "source_p_kw":source_power.real/1000,"load_p_kw":load_power.real/1000,
                      "line_loss_kw":line_loss.real/1000,"power_balance_va":abs(balance),
                      "embedded_pv_kw":pv_w/1000},
            "errors":errors,
            "scope":"One synthetic 0.4 kV two-section feeder with explicit ABCN voltages and currents",
            "limitations":["LV feeder topology and impedance matrix are synthetic",
                           "Phase shares and distal PV connection are scenarios",
                           "Source is an ideal grounded-neutral voltage; transformer impedance is excluded"]}
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 1 if errors else 0


if __name__=="__main__":
    raise SystemExit(main())
