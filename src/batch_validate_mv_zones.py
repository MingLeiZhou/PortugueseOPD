#!/usr/bin/env python3
"""Batch solve bounded candidate station zones from large OSM MV components."""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import duckdb

ROOT=Path(__file__).resolve().parents[1]
DATABASE=ROOT/"output/all_voltage/all_voltage_staging.duckdb"
SOLVER=ROOT/"src/validate_osm_component_powerflow.py"
OUT=ROOT/"output/all_voltage/mv_zone_batch"


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=DATABASE)
    parser.add_argument("--output",type=Path,default=OUT)
    parser.add_argument("--max-nodes",type=int,default=5000)
    parser.add_argument("--min-nodes",type=int,default=0,
                        help="Only run zones at or above this size; use zero when aggregating all")
    parser.add_argument("--timeout-seconds",type=int,default=180)
    parser.add_argument("--aggregate-only",action="store_true",
                        help="Rebuild summary from saved per-zone reports without rerunning solvers")
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with duckdb.connect(str(args.database),read_only=True) as con:
        zones=con.execute("""WITH n AS (
              SELECT candidate_station_code AS station_code,component_id,count(*) AS node_count
              FROM candidate.mv_zone_nodes GROUP BY 1,2
            ), p AS (
              SELECT z.candidate_station_code AS station_code,z.component_id,count(*) AS ptd_count
              FROM candidate.ptd_zone_candidates z
              JOIN parameter.ptd_transformer_parameters t USING(ptd_code)
              WHERE z.connection_status='NEAR_ANCHORED_COMPONENT' AND t.sn_mva>0
              GROUP BY 1,2
            ) SELECT n.station_code,n.component_id,n.node_count,coalesce(p.ptd_count,0)
            FROM n LEFT JOIN p USING(station_code,component_id)
            ORDER BY n.node_count,n.station_code,n.component_id""").fetchall()
    attempted=[z for z in zones if args.min_nodes<=z[2]<=args.max_nodes and z[3]>0]
    excluded=[z for z in zones if z not in attempted]
    records=[]
    for index,(station,component,nodes,ptds) in enumerate(attempted,start=1):
        zone_id=f"{station}__{component.replace(':','_')}"
        folder=args.output/zone_id
        if args.aggregate_only:
            report_file=folder/"validation.json"
            report=json.loads(report_file.read_text()) if report_file.exists() else {}
            status=report.get("result","MISSING")
            detail=report.get("error") or " | ".join(report.get("errors",[]))
        else:
            env=os.environ.copy();env.setdefault("MPLCONFIGDIR","/private/tmp")
            try:
                result=subprocess.run([sys.executable,str(SOLVER),"--database",str(args.database),
                                       "--zone-station-code",station,"--zone-component-id",component,
                                       "--output",str(folder)],
                                      capture_output=True,text=True,timeout=args.timeout_seconds,env=env)
                report_file=folder/"validation.json"
                report=json.loads(report_file.read_text()) if report_file.exists() else {}
                status=report.get("result","ERROR") if result.returncode==0 else "ERROR"
                detail=report.get("error") or " | ".join(report.get("errors",[])) or result.stderr.strip()[-500:]
            except subprocess.TimeoutExpired:
                report={};status="TIMEOUT";detail=f"Exceeded {args.timeout_seconds} seconds"
        pf=report.get("power_flow",{})
        max_fault=next((x["relative_error_percent"] for x in report.get("fault_comparison",[])
                        if x["case"]=="max"),None)
        within_limits=(status in {"PARTIAL","PASS"} and
                       pf.get("min_bus_voltage_pu",0)>=0.9 and
                       pf.get("max_line_loading_percent",float("inf"))<=100 and
                       pf.get("max_transformer_loading_percent",float("inf"))<=100)
        records.append({"station_code":station,"component_id":component,"zone_node_count":nodes,
                        "candidate_ptds":ptds,"result":status,
                        "solved_ptds":report.get("counts",{}).get("near_ptds"),
                        "min_voltage_pu":pf.get("min_bus_voltage_pu"),
                        "max_line_loading_percent":pf.get("max_line_loading_percent"),
                        "max_transformer_loading_percent":pf.get("max_transformer_loading_percent"),
                        "within_proxy_operating_limits":within_limits,
                        "max_fault_error_percent":max_fault,
                        "error_detail":detail})
        if not args.aggregate_only:
            print(f"{index}/{len(attempted)} {station} {status}",flush=True)
    with (args.output/"zone_results.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    with (args.output/"excluded_zones.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.writer(handle);writer.writerow(["station_code","component_id","zone_node_count","candidate_ptds"])
        writer.writerows(excluded)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("CREATE OR REPLACE TABLE study.mv_zone_batch AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(args.output/"zone_results.csv")])
    solved=[r for r in records if r["result"] in {"PARTIAL","PASS"}]
    bounded=[r for r in solved if r["within_proxy_operating_limits"]]
    failures=[r for r in records if r["result"] not in {"PARTIAL","PASS"}]
    report={"result":"PARTIAL" if solved else "FAIL","max_nodes_per_zone":args.max_nodes,
            "counts":{"candidate_zones":len(zones),"attempted_zones":len(attempted),
                      "solved_zones":len(solved),"failed_zones":len(failures),
                      "zones_within_proxy_operating_limits":len(bounded),
                      "solved_zones_with_max_fault_error_within_10_percent":sum(
                          r["max_fault_error_percent"] is not None and
                          abs(r["max_fault_error_percent"])<=10 for r in solved),
                      "excluded_zones":len(excluded),
                      "solved_zone_nodes":sum(r["zone_node_count"] for r in solved),
                      "solved_near_ptds":sum(r["solved_ptds"] or 0 for r in solved),
                      "excluded_zone_nodes":sum(z[2] for z in excluded),
                      "excluded_zone_ptds":sum(z[3] for z in excluded)},
            "failures":[{"station_code":r["station_code"],"component_id":r["component_id"],
                         "reason":r["error_detail"]} for r in failures],
            "scope":"Source-distance candidate zones cut from nine large OSM MV components",
            "limitations":["Virtual open boundaries and source assignments are unverified",
                           "Positive-sequence parameters use public E-REDES standards plus inferred type assignments; zero-sequence remains simulated",
                           "PTD simultaneous load remains a peak proxy",
                           "Each zone is solved independently; cross-zone switches and power exchange are omitted"]}
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 0 if solved else 1


if __name__=="__main__":
    raise SystemExit(main())
