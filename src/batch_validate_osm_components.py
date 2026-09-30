#!/usr/bin/env python3
"""Run the OSM-component network solver across all bounded source components.

Each result retains its own network, PF, fault and validation files. The
aggregate report makes coverage and failures visible; large components are
explicitly excluded by the chosen computational bound.
"""

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
OUT=ROOT/"output/all_voltage/osm_component_batch"


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=DATABASE)
    parser.add_argument("--output",type=Path,default=OUT)
    parser.add_argument("--max-nodes",type=int,default=5000)
    parser.add_argument("--timeout-seconds",type=int,default=120)
    parser.add_argument("--resume-valid",action="store_true",
                        help="Reuse a component result only when its recorded public source set matches the current query")
    args=parser.parse_args()
    if args.max_nodes<=0 or args.timeout_seconds<=0:
        parser.error("Limits must be positive")
    args.output.mkdir(parents=True,exist_ok=True)
    with duckdb.connect(str(args.database),read_only=True) as con:
        components=con.execute("""WITH p AS (
                SELECT s.component_id,count(*) AS ptds
                FROM candidate.ptd_segment_candidates s
                JOIN parameter.ptd_transformer_parameters t USING(ptd_code)
                WHERE s.connection_status='NEAR_ANCHORED_COMPONENT' AND t.sn_mva>0
                GROUP BY 1
            ), a AS (
                SELECT component_id,count(*) AS stations
                FROM candidate.station_sources
                WHERE anchor_status='NEAR_STATION_CANDIDATE'
                  AND short_circuit_max_mv_mva_public>0
                  AND short_circuit_min_mv_mva_public>0
                GROUP BY 1
            )
            SELECT c.component_id,c.node_count,p.ptds,a.stations
            FROM candidate.osm_components c JOIN p USING(component_id)
            JOIN a USING(component_id) ORDER BY c.node_count,c.component_id""").fetchall()
        source_codes={component_id:codes for component_id,codes in con.execute("""
            SELECT component_id,list(station_code ORDER BY station_code)
            FROM candidate.station_sources
            WHERE anchor_status='NEAR_STATION_CANDIDATE'
              AND short_circuit_max_mv_mva_public>0
              AND short_circuit_min_mv_mva_public>0
            GROUP BY component_id""").fetchall()}
    attempted=[r for r in components if r[1]<=args.max_nodes]
    excluded=[r for r in components if r[1]>args.max_nodes]
    records=[]
    for index,(component_id,nodes,ptds,stations) in enumerate(attempted,start=1):
        folder=args.output/component_id.replace(":","_")
        command=[sys.executable,str(SOLVER),"--database",str(args.database),
                 "--component-id",component_id,"--output",str(folder)]
        env=os.environ.copy()
        env.setdefault("MPLCONFIGDIR","/private/tmp")
        report_path=folder/"validation.json"
        report={}
        reused=False
        if args.resume_valid and report_path.exists():
            candidate_report=json.loads(report_path.read_text())
            expected_sources=sorted(source_codes.get(component_id,[]))
            recorded_sources=sorted(candidate_report.get("source_station_codes",[]))
            result_file=(folder/("network.json" if candidate_report.get("result") in {"PASS","PARTIAL"}
                                 else "unsolved_network.json"))
            if (candidate_report.get("component_id")==component_id
                    and candidate_report.get("result") in {"PASS","PARTIAL","FAIL"}
                    and recorded_sources==expected_sources and result_file.exists()):
                report=candidate_report
                reused=True
        try:
            if not reused:
                result=subprocess.run(command,capture_output=True,text=True,
                                      timeout=args.timeout_seconds,env=env)
                report=json.loads(report_path.read_text()) if report_path.exists() else {}
                returncode=result.returncode
                stderr=result.stderr
            else:
                returncode=0
                stderr=""
            status=report.get("result","ERROR") if returncode==0 else "ERROR"
            detail=(report.get("error") or " | ".join(report.get("errors",[]))
                    or (stderr.strip()[-500:] if returncode else ""))
        except subprocess.TimeoutExpired:
            report={};status="TIMEOUT";detail=f"Exceeded {args.timeout_seconds} seconds"
        records.append({"component_id":component_id,"node_count":nodes,
                        "candidate_ptds":ptds,"near_stations":stations,
                        "result":status,"solved_ptds":report.get("counts",{}).get("near_ptds"),
                        "min_voltage_pu":report.get("power_flow",{}).get("min_bus_voltage_pu"),
                        "max_line_loading_percent":report.get("power_flow",{}).get("max_line_loading_percent"),
                        "fault_min_error_percent":next((r["relative_error_percent"] for r in report.get("fault_comparison",[])
                                                        if r["case"]=="min"),None),
                        "error_detail":detail})
        reuse_label=" REUSED" if reused else ""
        print(f"{index}/{len(attempted)} {component_id} {status}{reuse_label}",flush=True)
    with (args.output/"component_results.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(records[0]));writer.writeheader();writer.writerows(records)
    with (args.output/"excluded_large_components.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.writer(handle);writer.writerow(["component_id","node_count","candidate_ptds","near_stations"])
        writer.writerows(excluded)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("CREATE OR REPLACE TABLE study.osm_component_batch AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(args.output/"component_results.csv")])
    passed=[r for r in records if r["result"]=="PARTIAL"]
    failed=[r for r in records if r["result"] not in {"PARTIAL","PASS"}]
    report={"result":"PARTIAL" if passed else "FAIL","max_nodes_per_component":args.max_nodes,
            "counts":{"eligible_components":len(components),"attempted_components":len(attempted),
                      "solved_components":len(passed),"failed_components":len(failed),
                      "excluded_large_components":len(excluded),
                      "solved_osm_nodes":sum(r["node_count"] for r in passed),
                      "solved_near_ptds":sum(r["solved_ptds"] or 0 for r in passed),
                      "excluded_large_osm_nodes":sum(r[1] for r in excluded),
                      "excluded_large_near_ptds":sum(r[2] for r in excluded)},
            "failures":[{"component_id":r["component_id"],"reason":r["error_detail"]} for r in failed],
            "scope":"OSM candidate components with near source and positive-capacity PTDs, bounded by node count",
            "limitations":["Spatial station/PTD terminals and installed equipment types are unverified candidates",
                           "Positive-sequence parameters use public E-REDES standards plus inferred type assignments; zero-sequence remains simulated",
                           "Each component uses all anchored public stations, but real feeder boundaries and open points remain unknown",
                           "Excluded large components are not solved; no national AC/fault conclusion"]}
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 0 if passed else 1


if __name__=="__main__":
    raise SystemExit(main())
