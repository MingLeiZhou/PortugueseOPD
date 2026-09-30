#!/usr/bin/env python3
"""Find a parallel-feeder scenario for low-voltage flagged PTDs.

Each hypothetical feeder has the same two 50 m ABCN sections and equal share
of a PTD's snapshot load/PV. Counts are scenario requirements, not observed
feeder inventories or evidence of actual voltage compliance.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path

import duckdb
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/"output/all_voltage/all_voltage_staging.duckdb"
OUT=ROOT/"output/all_voltage/lv_parallel_feeder_screen"
CONDUCTORS="ABCN"


def solve(s_total:np.ndarray,pv_mw:float,count:int,z:np.ndarray,length_km:float)->tuple[bool,float,int]:
    """Solve one member of count equal parallel feeders at 400 V."""
    s=np.asarray(s_total,dtype=complex)/count
    pv_w=pv_mw*1e6/count
    s1=np.zeros(4,dtype=complex);s2=np.zeros(4,dtype=complex)
    s1[:3]=s/2+pv_w/6
    s2[:3]=s/2-pv_w/6
    vln=400/math.sqrt(3)
    source=np.array([vln*np.exp(1j*a) for a in (0,-2*math.pi/3,2*math.pi/3)]+[0j])
    v1=source.copy();v2=source.copy()
    for iteration in range(1,101):
        d1=v1[:3]-v1[3];d2=v2[:3]-v2[3]
        if min(np.min(np.abs(d1)),np.min(np.abs(d2)))<100:
            return False,math.nan,iteration
        i1=np.zeros(4,dtype=complex);i2=np.zeros(4,dtype=complex)
        i1[:3]=np.conj(s1[:3]/d1);i2[:3]=np.conj(s2[:3]/d2)
        i1[3]=-i1[:3].sum();i2[3]=-i2[:3].sum()
        new1=source-z@((i1+i2)*length_km)
        new2=new1-z@(i2*length_km)
        change=max(np.max(np.abs(new1-v1)),np.max(np.abs(new2-v2)))
        v1=new1;v2=new2
        if change<1e-8:
            return True,float(min(np.min(np.abs(v1[:3]-v1[3])),
                                  np.min(np.abs(v2[:3]-v2[3])))/vln),iteration
    return False,math.nan,100


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=DB)
    parser.add_argument("--output",type=Path,default=OUT)
    parser.add_argument("--max-feeders",type=int,default=16)
    parser.add_argument("--peak-envelope",action="store_true",
                        help="Design against each PTD's peak-load proxy with zero PV")
    args=parser.parse_args()
    if args.peak_envelope and args.output==OUT:
        args.output=ROOT/"output/all_voltage/lv_parallel_feeder_peak_screen"
    if args.max_feeders<1:parser.error("--max-feeders must be positive")
    args.output.mkdir(parents=True,exist_ok=True)
    with duckdb.connect(str(args.database),read_only=True) as con:
        source_table=("study.lv_fourwire_peak_envelope_snapshot" if args.peak_envelope
                      else "study.lv_fourwire_national_snapshot")
        flagged=con.execute(f"""WITH shares AS (
            SELECT ptd_code,
                   sum(ptd_total_share) FILTER(WHERE phase='A') a_share,
                   sum(ptd_total_share) FILTER(WHERE phase='B') b_share,
                   sum(ptd_total_share) FILTER(WHERE phase='C') c_share
            FROM phase.lv_load_shares GROUP BY 1
        )
        SELECT s.ptd_code,s.status,s.min_phase_voltage_pu,
               s.load_p_mw AS p_mw,
               s.load_p_mw*tan(acos(0.97)) AS q_mvar,
               s.pv_p_mw,
               h.a_share,h.b_share,h.c_share,
               s.transformer_capacity_kva,p.construction_type,
               s.profile_data_status
        FROM {source_table} s
        JOIN shares h ON h.ptd_code=s.ptd_code
        JOIN candidate.ptd_public_attributes p ON p.ptd_code=s.ptd_code
        WHERE s.status!='CONVERGED' OR s.min_phase_voltage_pu<0.9
        ORDER BY s.ptd_code""").fetchall()
        matrix_rows=con.execute("""SELECT from_conductor,to_conductor,r_ohm_per_km,x_ohm_per_km
            FROM phase.impedance_matrix WHERE archetype_id='LV_4WIRE_PROXY_V1'""").fetchall()
        length=con.execute("SELECT min(length_km_proxy),max(length_km_proxy) FROM phase.lv_line_models").fetchone()
        all_ptd_codes=[r[0] for r in con.execute("SELECT ptd_code FROM equivalent.ptd_connections ORDER BY 1").fetchall()]
    if not flagged or len(matrix_rows)!=16 or length[0]!=length[1]:
        parser.error("Flagged cases, ABCN matrix or uniform line lengths missing")
    z=np.zeros((4,4),dtype=complex)
    for a,b,r,x in matrix_rows:
        z[CONDUCTORS.index(a),CONDUCTORS.index(b)]=complex(r,x)
    records=[]
    baseline_differences=[]
    for row in flagged:
        code,status,baseline_pu,p,q,pv,sa,sb,sc,kva,construction,profile_status=row
        s_total=(p+1j*q)*1e6*np.array([sa,sb,sc],dtype=float)
        one_ok,one_pu,_=solve(s_total,pv,1,z,length[0])
        if status=='CONVERGED':
            baseline_differences.append(abs(one_pu-baseline_pu) if one_ok else math.inf)
        required=None;result_pu=None;result_iterations=None
        for count in range(1,args.max_feeders+1):
            ok,pu,iters=solve(s_total,pv,count,z,length[0])
            if ok and pu>=0.9:
                required=count;result_pu=pu;result_iterations=iters
                break
        records.append(dict(ptd_code=code,baseline_status=status,
                            baseline_min_voltage_pu=baseline_pu,
                            required_equal_parallel_feeders=required,
                            scenario_min_voltage_pu=result_pu,
                            scenario_iterations=result_iterations,
                            load_p_mw=p,pv_p_mw=pv,transformer_capacity_kva=kva,
                            construction_type=construction,profile_data_status=profile_status,
                            evidence_status="SYNTHETIC_EQUAL_PARALLEL_FEEDER_SCREEN"))
    path=args.output/"flagged_ptd_feeder_screen.csv"
    with path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    required_by_ptd={r["ptd_code"]:r["required_equal_parallel_feeders"] for r in records}
    layout_path=args.output/"lv_feeder_layout_scenario.csv"
    with layout_path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.writer(handle)
        writer.writerow(("ptd_code","feeder_count","basis","scenario_timestamp_utc","evidence_status"))
        for code in all_ptd_codes:
            required=required_by_ptd.get(code)
            writer.writerow((code,required or 1,
                             "PEAK_PROXY_ENVELOPE_SCREEN" if args.peak_envelope and required else
                             "SNAPSHOT_VOLTAGE_SCREEN" if required else "BASE_SINGLE_FEEDER_PROXY",
                             "PEAK_PROXY_ENVELOPE" if args.peak_envelope else "2025-07-22T11:45:00+00:00",
                             "SYNTHETIC_LAYOUT_NOT_OBSERVED"))
    instance_path=args.output/"lv_feeder_instances_scenario.csv"
    with instance_path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.writer(handle)
        writer.writerow(("feeder_id","ptd_code","feeder_index","load_share","pv_share",
                         "length_km_per_section","section_count","conductors","evidence_status"))
        for code in all_ptd_codes:
            count=required_by_ptd.get(code) or 1
            for index in range(1,count+1):
                writer.writerow((f"LVFEEDER:{code}:{index}",code,index,1/count,1/count,
                                 length[0],2,"ABCN","SYNTHETIC_EQUAL_PARALLEL_FEEDERS"))
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        screen_table=("study.lv_parallel_feeder_peak_screen" if args.peak_envelope
                      else "study.lv_parallel_feeder_screen")
        layout_table=("phase.lv_feeder_layout_peak_scenario" if args.peak_envelope
                      else "phase.lv_feeder_layout_scenario")
        instance_table=("phase.lv_feeder_instances_peak_scenario" if args.peak_envelope
                        else "phase.lv_feeder_instances_scenario")
        con.execute(f"CREATE OR REPLACE TABLE {screen_table} AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(path)])
        con.execute("CREATE SCHEMA IF NOT EXISTS phase")
        con.execute(f"CREATE OR REPLACE TABLE {layout_table} AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(layout_path)])
        con.execute(f"CREATE OR REPLACE TABLE {instance_table} AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(instance_path)])
        layout_errors=con.execute(f"""SELECT count(*) FROM (
            SELECT l.ptd_code FROM {layout_table} l
            LEFT JOIN {instance_table} f USING(ptd_code)
            GROUP BY l.ptd_code,l.feeder_count
            HAVING count(f.feeder_id)!=l.feeder_count
               OR abs(sum(f.load_share)-1)>1e-9
               OR abs(sum(f.pv_share)-1)>1e-9)""").fetchone()[0]
    required=[r["required_equal_parallel_feeders"] for r in records if r["required_equal_parallel_feeders"]]
    checks={"flagged_ptds":len(records),
            "baseline_failed":sum(r["baseline_status"]!='CONVERGED' for r in records),
            "baseline_below_0_9_pu":sum(r["baseline_status"]=='CONVERGED' for r in records),
            "resolved_within_max_feeders":len(required),
            "unresolved_within_max_feeders":len(records)-len(required),
            "max_required_parallel_feeders":max(required) if required else None,
            "national_feeder_layout_ptds":len(all_ptd_codes),
            "national_scenario_feeder_instances":sum(required_by_ptd.get(code) or 1 for code in all_ptd_codes),
            "layout_share_or_count_errors":layout_errors,
            "max_baseline_solver_voltage_difference_pu":max(baseline_differences) if baseline_differences else None}
    errors=[]
    if (checks["flagged_ptds"]!=checks["baseline_failed"]+checks["baseline_below_0_9_pu"]
            or checks["max_baseline_solver_voltage_difference_pu"] is None
            or checks["max_baseline_solver_voltage_difference_pu"]>1e-8
            or layout_errors):
        errors.append("Independent scalar solver does not reproduce baseline flagged cases")
    report={"result":"FAIL" if errors else "PARTIAL","checks":checks,"errors":errors,
            "peak_proxy_envelope":args.peak_envelope,
            "max_feeders":args.max_feeders,
            "scope":"Equal parallel two-section ABCN feeder sensitivity for snapshot failures and undervoltage cases",
            "limitations":["Required feeder count is a synthetic scenario, not a public PTD inventory",
                           "Same assumed cable impedance and 50 m sections on every parallel feeder",
                           "No protection, ampacity, fault or independent voltage validation"]}
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps(report,indent=2))
    return int(bool(errors))


if __name__=='__main__':
    raise SystemExit(main())
