#!/usr/bin/env python3
"""Run unbalanced three-phase PF and max/min three-phase fault pilot studies.

The pilot uses public E-REDES 60/MV station rating and short-circuit values.
Line zero-sequence parameters, transformer vector groups, R/X ratio and
phase assignments remain explicit engineering assumptions.
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

ROOT = Path(__file__).resolve().parents[1]
STAGING = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
PILOT = ROOT / "output/feasibility/lv_pilot/pilot_network.json"
CHARACTERISTICS = ROOT / "portuguese_hv_network/data/raw/eredes/caracteristicas-da-rede.csv"
OUT = ROOT / "output/all_voltage/studies"


def source_record(path: Path, code: str) -> dict:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        matches = [row for row in csv.DictReader(handle, delimiter=";")
                   if row["codigo_da_instalacao"] == code and row["relacao_de_transformacao_at_mt"].startswith("60/")]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one public 60/MV source record for {code}; got {len(matches)}")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=STAGING)
    parser.add_argument("--pilot", type=Path, default=PILOT)
    parser.add_argument("--characteristics", type=Path, default=CHARACTERISTICS)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    net = pp.from_json(str(args.pilot))
    source_code = str(net.bus.at[0, "name"]).split(":")[-2]
    record = source_record(args.characteristics, source_code)
    rated_mva = float(record["potencia_instalada"])
    max_at = float(record["potencia_de_curto_circuito_maxima_at"])
    min_at = float(record["potencia_de_curto_circuito_minima_at"])
    public_max_mv = float(record["potencia_de_curto_circuito_maxima_mt"])
    public_min_mv = float(record["potencia_de_curto_circuito_minima_mt"])
    mv_kv = float(net.bus.at[1, "vn_kv"])
    net.trafo.at[0, "sn_mva"] = rated_mva
    net.trafo.at[0, "vk_percent"] = 10.0  # proxy tested against max public Ssc, not independently measured
    net.ext_grid["s_sc_max_mva"] = max_at
    net.ext_grid["s_sc_min_mva"] = min_at
    net.ext_grid["rx_max"] = 0.1
    net.ext_grid["rx_min"] = 0.1
    net.ext_grid["x0x_max"] = 1.0
    net.ext_grid["r0x0_max"] = 0.1
    net.line["r0_ohm_per_km"] = net.line["r_ohm_per_km"] * 3
    net.line["x0_ohm_per_km"] = net.line["x_ohm_per_km"] * 3
    net.line["c0_nf_per_km"] = net.line["c_nf_per_km"]
    net.line["endtemp_degree"] = 80.0
    net.trafo["vk0_percent"] = net.trafo["vk_percent"]
    net.trafo["vkr0_percent"] = net.trafo["vkr_percent"]
    net.trafo["mag0_percent"] = 100.0
    net.trafo["mag0_rx"] = 0.0
    net.trafo["si0_hv_partial"] = 0.9
    net.trafo.loc[0, "vector_group"] = "YNyn"
    net.trafo.loc[net.trafo.index > 0, "vector_group"] = "Dyn"
    with duckdb.connect(str(args.database), read_only=True) as con:
        shares = {(code, phase): float(share) for code, phase, share in con.execute(
            "SELECT ptd_code, phase, sum(ptd_total_share) FROM phase.lv_load_shares GROUP BY 1,2").fetchall()}
    for _, load in net.load.iterrows():
        code = str(load["name"]).split()[2].split(":")[0]
        weights = [shares[(code, phase)] for phase in "ABC"]
        pp.create_asymmetric_load(net, int(load["bus"]),
                                  p_a_mw=float(load["p_mw"]) * weights[0],
                                  p_b_mw=float(load["p_mw"]) * weights[1],
                                  p_c_mw=float(load["p_mw"]) * weights[2],
                                  q_a_mvar=float(load["q_mvar"]) * weights[0],
                                  q_b_mvar=float(load["q_mvar"]) * weights[1],
                                  q_c_mvar=float(load["q_mvar"]) * weights[2])
    net.load["in_service"] = False
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        pp.runpp_3ph(net, numba=False, max_iteration=30)
    phase_results = net.res_bus_3ph.copy()
    phase_results["bus_id"] = net.bus["name"]
    phase_results.to_csv(args.output / "pilot_bus_3ph.csv", index=False)
    lv_indexes = net.bus.index[net.bus.vn_kv == 0.4]
    voltages = phase_results.loc[lv_indexes, ["vm_a_pu", "vm_b_pu", "vm_c_pu"]].to_numpy().ravel()
    max_unbalance = float(phase_results.loc[lv_indexes, "unbalance_percent"].max())
    fault_rows = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        for case, public in (("max", public_max_mv), ("min", public_min_mv)):
            sc.calc_sc(net, case=case, fault="3ph", branch_results=False)
            ikss = float(net.res_bus_sc.at[1, "ikss_ka"])
            simulated_mva = math.sqrt(3) * mv_kv * ikss
            fault_rows.append({"case": case, "source_station_code": source_code,
                               "mv_voltage_kv": mv_kv, "ikss_mv_ka": ikss,
                               "simulated_mv_short_circuit_mva": simulated_mva,
                               "public_mv_short_circuit_mva": public,
                               "relative_error_percent": 100 * (simulated_mva - public) / public,
                               "parameter_status": "PUBLIC_SOURCE_RATING_AND_SSC_WITH_PROXY_RX_VK_AND_TEMPERATURE"})
    with (args.output / "pilot_fault_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fault_rows[0]))
        writer.writeheader()
        writer.writerows(fault_rows)
    checks = {"three_phase_pf_converged": bool(net.converged),
              "lv_phase_voltage_min_pu": float(voltages.min()),
              "lv_phase_voltage_max_pu": float(voltages.max()),
              "max_lv_voltage_unbalance_percent": max_unbalance,
              "public_source_transformer_mva": rated_mva,
              "max_fault_relative_error_percent": fault_rows[0]["relative_error_percent"],
              "min_fault_relative_error_percent": fault_rows[1]["relative_error_percent"]}
    errors = []
    if not checks["three_phase_pf_converged"] or not 0.9 <= checks["lv_phase_voltage_min_pu"]:
        errors.append("Three-phase PF failed or LV voltage below pilot bound")
    if checks["lv_phase_voltage_max_pu"] > 1.1:
        errors.append("LV voltage above pilot bound")
    if any(not math.isfinite(row["ikss_mv_ka"]) or row["ikss_mv_ka"] <= 0 for row in fault_rows):
        errors.append("Fault calculation invalid")
    independent_fault_gap = abs(checks["min_fault_relative_error_percent"]) > 10
    status = "FAIL" if errors else ("PARTIAL" if independent_fault_gap else "PASS")
    report = {"result": status, "checks": checks, "errors": errors,
              "unresolved": ["Minimum short-circuit case differs by more than 10% from public station value"] if independent_fault_gap else [],
              "scope": "Single 3-PTD pilot; three-phase sequence solver, not explicit neutral-current validation or nationwide PF"}
    (args.output / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("CREATE OR REPLACE TABLE study.pilot_bus_3ph AS SELECT * FROM read_csv_auto(?, header=true)",
                    [str(args.output / "pilot_bus_3ph.csv")])
        con.execute("CREATE OR REPLACE TABLE study.pilot_fault_comparison AS SELECT * FROM read_csv_auto(?, header=true)",
                    [str(args.output / "pilot_fault_comparison.csv")])
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
