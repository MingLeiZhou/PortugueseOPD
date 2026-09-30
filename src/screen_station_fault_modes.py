#!/usr/bin/env python3
"""Screen public station max/min fault pairs for one- and two-unit scenarios.

This is an impedance-magnitude screen, not an IEC 60909 fault calculation.
The public maximum MV fault value calibrates an equivalent bridge impedance;
the public minimum value is held out. Unit counts remain hypotheses.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path

import duckdb

ROOT=Path(__file__).resolve().parents[1]
DATABASE=ROOT/"output/all_voltage/all_voltage_staging.duckdb"
CHARACTERISTICS=ROOT/"portuguese_hv_network/data/raw/eredes/caracteristicas-da-rede.csv"
OUT=ROOT/"output/all_voltage/station_fault_screen"
MV={6000,10000,15000,20000,30000}


def main()->int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database",type=Path,default=DATABASE)
    parser.add_argument("--characteristics",type=Path,default=CHARACTERISTICS)
    parser.add_argument("--output",type=Path,default=OUT)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    with duckdb.connect(str(args.database),read_only=True) as con:
        station_keys={(str(code),int(voltage)) for code,voltage in con.execute(
            "SELECT station_code,mv_voltage_v FROM candidate.station_sources").fetchall()}
    public={}
    with args.characteristics.open(newline="",encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle,delimiter=";"):
            nums=[int(x) for x in re.findall(r"\d+",row["relacao_de_transformacao_at_mt"] or "")]
            mv=next((v for v in reversed(nums) if v*1000 in MV),None)
            if mv is not None:
                public[(row["codigo_da_instalacao"],mv*1000)]=row
    rows=[]
    excluded=[]
    categories=Counter()
    for code,voltage in sorted(station_keys):
        key=(code,voltage)
        record=public.get(key)
        if record is None:
            excluded.append({"station_code":code,"mv_voltage_v":voltage,"reason":"NO_MATCHING_PUBLIC_ROW"})
            continue
        try:
            s=float(record["potencia_instalada"])
            at_max=float(record["potencia_de_curto_circuito_maxima_at"])
            at_min=float(record["potencia_de_curto_circuito_minima_at"])
            mv_max=float(record["potencia_de_curto_circuito_maxima_mt"])
            mv_min=float(record["potencia_de_curto_circuito_minima_mt"])
        except (ValueError,TypeError):
            excluded.append({"station_code":code,"mv_voltage_v":voltage,"reason":"NONNUMERIC_PUBLIC_FAULT_OR_CAPACITY"})
            continue
        if min(s,at_max,at_min,mv_max,mv_min)<=0:
            excluded.append({"station_code":code,"mv_voltage_v":voltage,"reason":"NONPOSITIVE_PUBLIC_FAULT_OR_CAPACITY"})
            continue
        # Per-unit impedance on the total public installed-MVA base. This
        # magnitude subtraction assumes a common reactance angle.
        bridge_inverse_mva=1/mv_max-1/at_max
        if bridge_inverse_mva<=0:
            excluded.append({"station_code":code,"mv_voltage_v":voltage,"reason":"MV_MAX_NOT_BELOW_AT_MAX"})
            continue
        equivalent_vk_percent=100*s*bridge_inverse_mva
        errors={}
        for unit_count in (1,2):
            predicted_min=1/(1/at_min+unit_count*bridge_inverse_mva)
            error=100*(predicted_min/mv_min-1)
            errors[unit_count]=error
            rows.append({"station_code":code,"mv_voltage_v":voltage,
                         "public_installed_mva":s,"assumed_total_units":unit_count,
                         "assumed_active_units_min":1,
                         "calibrated_max_equivalent_vk_percent":equivalent_vk_percent,
                         "public_max_at_mva":at_max,"public_max_mv_mva":mv_max,
                         "public_min_at_mva":at_min,"public_min_mv_mva":mv_min,
                         "predicted_min_mv_mva":predicted_min,
                         "held_out_min_error_percent":error,
                         "within_10_percent":abs(error)<=10,
                         "method_status":"REACTANCE_MAGNITUDE_SCREEN_ASSUMED_UNIT_COUNT"})
        categories[(abs(errors[1])<=10,abs(errors[2])<=10)]+=1
    with (args.output/"mode_screen.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    with (args.output/"excluded.csv").open("w",newline="",encoding="utf-8") as handle:
        writer=csv.DictWriter(handle,fieldnames=["station_code","mv_voltage_v","reason"])
        writer.writeheader();writer.writerows(excluded)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("CREATE OR REPLACE TABLE study.station_fault_mode_screen AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(args.output/"mode_screen.csv")])
        con.execute("CREATE OR REPLACE TABLE study.station_fault_mode_exclusions AS SELECT * FROM read_csv_auto(?,header=true)",
                    [str(args.output/"excluded.csv")])
    checks={"station_voltage_records":len(station_keys),"screened_stations":len(rows)//2,
            "excluded_stations":len(excluded),"single_only_within_10_percent":categories[(True,False)],
            "two_unit_only_within_10_percent":categories[(False,True)],
            "both_within_10_percent":categories[(True,True)],
            "neither_within_10_percent":categories[(False,False)]}
    errors=[]
    if checks["screened_stations"]+len(excluded)!=len(station_keys):
        errors.append("Station-voltage coverage mismatch")
    if sum(categories.values())!=checks["screened_stations"]:
        errors.append("Category coverage mismatch")
    report={"result":"PASS" if not errors else "FAIL","checks":checks,
            "exclusion_reasons":dict(Counter(r["reason"] for r in excluded)),
            "errors":errors,
            "scope":"Cross-station reactance-magnitude screen; max public fault calibrates bridge, min public fault held out",
            "limitations":["A 10% match does not identify physical transformer count",
                           "Reactance-angle equality and a single active minimum-mode unit are assumptions",
                           "IEC voltage factors, network switching and transformer winding detail are omitted"]}
    (args.output/"validation.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 1 if errors else 0


if __name__=="__main__":
    raise SystemExit(main())
