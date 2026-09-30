#!/usr/bin/env python3
"""Diagnose every failed OSM component without accepting scaled cases as valid.

The original peak-proxy operating point remains the target. Load scaling only
shows where the candidate network loses voltage/loadability.  The Dyn5 clock
angle is set to zero for this balanced positive-sequence diagnostic because
every LV bus is a transformer leaf and the clock angle does not change power
or voltage magnitudes there; retaining 150 degrees can drive a flat-start
Newton or BFSW solve to a false near-zero-voltage branch.
"""

from __future__ import annotations

import argparse
import csv
import json
import warnings
from pathlib import Path

import duckdb
import pandapower as pp

ROOT=Path(__file__).resolve().parents[1]
DATABASE=ROOT/"output/all_voltage/all_voltage_staging.duckdb"
BATCH=ROOT/"output/all_voltage/osm_component_batch"
OUT=ROOT/"output/all_voltage/component_loadability"
SCALES=(0.0,0.01,0.05,0.10,0.20,0.30,0.40,0.50,0.60,0.70,0.80,0.90,1.0)


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=DATABASE)
    parser.add_argument("--batch",type=Path,default=BATCH)
    parser.add_argument("--output",type=Path,default=OUT)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    batch_report=args.batch/"validation.json"
    if not batch_report.exists():
        parser.error(f"Missing batch report: {batch_report}")
    failed_ids=[row["component_id"] for row in json.loads(batch_report.read_text())["failures"]]
    if not failed_ids:
        parser.error("The batch report contains no failed components")
    rows=[]
    for component_id in failed_ids:
        path=args.batch/component_id.replace(":","_")/"unsolved_network.json"
        if not path.exists():
            parser.error(f"Missing failed network: {path}")
        net=pp.from_json(str(path))
        lv_leaf_only=all(
            int((net.trafo.lv_bus == bus).sum()) == 1
            and int((net.line.from_bus == bus).sum() + (net.line.to_bus == bus).sum()) == 0
            for bus in net.trafo.lv_bus
        )
        if not lv_leaf_only:
            parser.error(f"Cannot suppress transformer clock angle: non-leaf LV bus in {component_id}")
        net.trafo["shift_degree"]=0.0
        full_mw=float(net.load.p_mw.sum())
        for scale in SCALES:
            net.load["scaling"]=scale
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore",FutureWarning)
                    pp.runpp(net,algorithm="nr",numba=False,max_iteration=100,init="flat")
                converged=bool(net.converged)
                min_voltage=float(net.res_bus.vm_pu.min())
                max_line=float(net.res_line.loading_percent.max())
                max_trafo=float(net.res_trafo.loading_percent.max())
            except (pp.LoadflowNotConverged, ValueError, RuntimeError, FloatingPointError):
                converged=False;min_voltage=None;max_line=None;max_trafo=None
            rows.append({"component_id":component_id,"load_scale":scale,
                         "full_peak_proxy_p_mw":full_mw,
                         "converged":converged,"min_voltage_pu":min_voltage,
                         "max_line_loading_percent":max_line,
                         "max_transformer_loading_percent":max_trafo,
                         "transformer_clock_angle_treatment":"SUPPRESSED_FOR_BALANCED_MAGNITUDE_DIAGNOSTIC_LV_LEAVES_ONLY",
                         "diagnostic_status":"SCALED_DIAGNOSTIC_NOT_VALIDATED_OPERATING_POINT"})
    csv_path=args.output/"loadability.csv"
    with csv_path.open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("CREATE OR REPLACE TABLE study.osm_component_loadability_diagnostic AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(csv_path)])
    original=[r for r in rows if r["load_scale"]==1]
    checks={"failed_components_examined":len(failed_ids),
            "original_load_converged_count":sum(r["converged"] for r in original),
            "diagnostic_rows":len(rows),
            "full_load_failure_preserved":all(not r["converged"] for r in original)}
    report={"result":"PARTIAL","checks":checks,
            "component_diagnostics":{component_id:[r for r in rows if r["component_id"]==component_id]
                                     for component_id in failed_ids},
            "scope":"Every failed bounded OSM component; Newton load scales with LV-leaf clock-angle suppression, not alternative accepted load cases",
            "unresolved":["Verify source count and station-to-feeder boundaries",
                          "Check OSM conductor identity, current ratings and missing parallel feeders",
                          "Calibrate spatial PTD allocation and simultaneous load"]}
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"result":report["result"],"checks":checks},indent=2))
    return 0


if __name__=="__main__":
    raise SystemExit(main())
