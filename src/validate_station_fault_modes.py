#!/usr/bin/env python3
"""Test inferred station transformer operating modes against public fault range.

The two-unit interpretation of public installed MVA is a scenario. Calibrate
per-unit vk only to the public maximum MV fault level; hold the minimum MV
fault level out as an independent diagnostic of the one-unit minimum mode.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import warnings
from pathlib import Path

import duckdb
import pandapower as pp
import pandapower.shortcircuit as sc
from scipy.optimize import brentq

ROOT=Path(__file__).resolve().parents[1]
DATABASE=ROOT/"output/all_voltage/all_voltage_staging.duckdb"
NETWORK=ROOT/"output/all_voltage/osm_component_pilot/network.json"
OUT=ROOT/"output/all_voltage/station_fault_modes"


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=DATABASE)
    parser.add_argument("--network",type=Path,default=NETWORK)
    parser.add_argument("--output",type=Path,default=OUT)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    net=pp.from_json(str(args.network))
    source_rows=net.trafo.index[net.trafo.name.astype(str).str.startswith("SOURCE_BRIDGE:")].tolist()
    if len(source_rows)!=1:
        parser.error("Expected one pilot source transformer")
    source_idx=source_rows[0]
    t=net.trafo.loc[source_idx]
    station_code=str(t["name"]).split(":",1)[1]
    hv,mv=int(t["hv_bus"]),int(t["lv_bus"])
    total_mva=float(t["sn_mva"])
    mv_kv=float(net.bus.at[mv,"vn_kv"])
    with duckdb.connect(str(args.database),read_only=True) as con:
        rec=con.execute("""SELECT short_circuit_max_mv_mva_public,
            short_circuit_min_mv_mva_public FROM candidate.station_sources
            WHERE station_code=? AND mv_voltage_v=?""",
            [station_code,int(mv_kv*1000)]).fetchone()
    if rec is None or min(rec)<=0:
        parser.error("Public MV short-circuit pair is unavailable")
    public_max,public_min=map(float,rec)
    def fault(case:str)->float:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore",FutureWarning)
            sc.calc_sc(net,case=case,fault="3ph",branch_results=False)
        return math.sqrt(3)*mv_kv*float(net.res_bus_sc.at[mv,"ikss_ka"])
    baseline_max=fault("max")
    baseline_min=fault("min")
    # Replace a single 40 MVA-equivalent bridge by a hypothetical pair whose
    # nameplates sum to the same public installed capacity.
    net.trafo.drop(index=source_idx,inplace=True)
    unit_ids=[]
    for unit in (1,2):
        unit_ids.append(pp.create_transformer_from_parameters(
            net,hv,mv,sn_mva=total_mva/2,vn_hv_kv=float(t["vn_hv_kv"]),
            vn_lv_kv=float(t["vn_lv_kv"]),vk_percent=10,vkr_percent=.5,
            pfe_kw=0,i0_percent=0,shift_degree=0,
            vk0_percent=10,vkr0_percent=.5,mag0_percent=100,mag0_rx=0,
            si0_hv_partial=.9,vector_group="YNyn",
            name=f"INFERRED_UNIT:{station_code}:{unit}"))
    def set_vk(vk:float)->None:
        for idx in unit_ids:
            net.trafo.at[idx,"vk_percent"]=vk
            net.trafo.at[idx,"vkr_percent"]=vk*.05
            net.trafo.at[idx,"vk0_percent"]=vk
            net.trafo.at[idx,"vkr0_percent"]=vk*.05
    def max_residual(vk:float)->float:
        set_vk(vk)
        net.trafo.at[unit_ids[1],"in_service"]=True
        return fault("max")-public_max
    if max_residual(3)*max_residual(20)>0:
        parser.error("Maximum short-circuit target cannot be bracketed within vk=3..20%")
    calibrated_vk=brentq(max_residual,3,20,xtol=1e-10)
    set_vk(calibrated_vk)
    net.trafo.at[unit_ids[1],"in_service"]=True
    inferred_max=fault("max")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore",FutureWarning)
        pp.runpp(net,numba=False,max_iteration=30)
    max_pf_converged=bool(net.converged)
    max_pf_min_vm=float(net.res_bus.vm_pu.min())
    net.trafo.at[unit_ids[1],"in_service"]=False
    inferred_min=fault("min")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore",FutureWarning)
        pp.runpp(net,numba=False,max_iteration=30)
    min_pf_converged=bool(net.converged)
    min_pf_min_vm=float(net.res_bus.vm_pu.min())
    rows=[]
    for model,case,active,calculated,reference in (
        ("SINGLE_EQUIVALENT","max",1,baseline_max,public_max),
        ("SINGLE_EQUIVALENT","min",1,baseline_min,public_min),
        ("TWO_UNIT_MODE","max",2,inferred_max,public_max),
        ("TWO_UNIT_MODE","min",1,inferred_min,public_min)):
        rows.append({"station_code":station_code,"model":model,"case":case,
                     "active_transformer_units":active,"public_ssc_mva":reference,
                     "simulated_ssc_mva":calculated,
                     "relative_error_percent":100*(calculated/reference-1),
                     "evidence_status":("ENGINEERING_PROXY_SINGLE_EQUIVALENT" if model=="SINGLE_EQUIVALENT"
                                        else "INFERRED_OPERATING_MODE_NOT_OPERATOR_CONFIRMED")})
    with (args.output/"fault_operating_modes.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    pp.to_json(net,str(args.output/"one_unit_min_mode_network.json"))
    errors=[]
    if not max_pf_converged or not min_pf_converged:
        errors.append("One of the inferred-mode AC power flows did not converge")
    if abs(rows[-1]["relative_error_percent"])>=abs(rows[1]["relative_error_percent"]):
        errors.append("Held-out minimum fault error did not improve")
    report={"result":"FAIL" if errors else "PARTIAL",
            "station_code":station_code,"public_installed_mva":total_mva,
            "inferred_unit_mva":total_mva/2,"max_calibrated_vk_percent":calibrated_vk,
            "fault_comparison":rows,
            "power_flow":{"two_unit_converged":max_pf_converged,
                          "two_unit_min_voltage_pu":max_pf_min_vm,
                          "one_unit_converged":min_pf_converged,
                          "one_unit_min_voltage_pu":min_pf_min_vm},
            "errors":errors,
            "scope":"One public station and one OSM MV component; max fault used for calibration, min fault held out",
            "limitations":["Public installed MVA does not establish the actual number of transformers",
                           "Two-unit switching is a plausible scenario, not observed switch status",
                           "Source R/X, transformer loss split and downstream topology remain proxies"]}
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("CREATE OR REPLACE TABLE study.station_fault_modes AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(args.output/"fault_operating_modes.csv")])
    print(json.dumps(report,indent=2))
    return 1 if errors else 0


if __name__=="__main__":
    raise SystemExit(main())
