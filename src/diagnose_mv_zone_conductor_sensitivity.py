#!/usr/bin/env python3
"""Screen failed MV zones against larger public E-REDES ACSR standards.

The original topology, PTD load and cable parameters remain unchanged.  Each
scenario replaces every overhead-line positive/zero-sequence series parameter
with one public conductor catalogue resistance plus an explicit geometry-class
X and inferred thermal rating.  A converged upgrade is a design sensitivity,
not evidence of the installed conductor or a validated operating point.
"""

from __future__ import annotations

import argparse
import csv
import json
import warnings
from pathlib import Path

import duckdb
import pandapower as pp

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "output/all_voltage/all_voltage_staging.duckdb"
BATCH = ROOT / "output/all_voltage/mv_zone_batch"
OUT = ROOT / "output/all_voltage/mv_zone_conductor_sensitivity"

# X and ampacity are engineering design values; R20 comes from the public
# DMA-C34-120/N catalogue and is converted to 75 C with alpha=0.00403/K.
DESIGNS = {
    "ACSR_160": ("136-AL1/22-ST1A", 0.320, 0.390),
    "ACSR_235": ("203-AL1/32-ST1A", 0.300, 0.510),
    "ACSR_325": ("264-AL1/62-ST1A", 0.290, 0.650),
}


def solve(net: pp.pandapowerNet) -> tuple[bool, float | None, float | None, float | None]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FutureWarning)
            pp.runpp(net, algorithm="nr", numba=False, max_iteration=100, init="flat")
        return (bool(net.converged), float(net.res_bus.vm_pu.min()),
                float(net.res_line.loading_percent.max()),
                float(net.res_trafo.loading_percent.max()))
    except pp.LoadflowNotConverged:
        return False, None, None, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--batch", type=Path, default=BATCH)
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    batch = json.loads((args.batch / "validation.json").read_text())
    failures = [(row["station_code"], row["component_id"]) for row in batch["failures"]]
    if not failures:
        raise SystemExit("No failed zones in batch report")
    with duckdb.connect(str(args.database), read_only=True) as con:
        catalog = dict(con.execute("""SELECT catalog_id,r20_ohm_per_km
            FROM parameter.mv_overhead_conductor_catalog
            WHERE catalog_id IN ('136-AL1/22-ST1A','203-AL1/32-ST1A','264-AL1/62-ST1A')""").fetchall())
    if len(catalog) != 3:
        raise RuntimeError("Required E-REDES ACSR catalogue rows are missing")

    rows: list[dict] = []
    for index, (station, component_id) in enumerate(failures, start=1):
        zone_id = f"{station}__{component_id.replace(':', '_')}"
        path = args.batch / zone_id / "unsolved_network.json"
        if not path.exists():
            raise FileNotFoundError(path)
        original = pp.from_json(str(path))
        voltage_kv = float(original.bus.vn_kv.max())
        overhead_count = int((original.line.type == "ol").sum())
        for design, (catalog_id, x1, imax) in DESIGNS.items():
            net = pp.from_json(str(path))
            mask = net.line.type == "ol"
            r75 = float(catalog[catalog_id]) * (1 + 0.00403 * (75 - 20))
            net.line.loc[mask, "r_ohm_per_km"] = r75
            net.line.loc[mask, "x_ohm_per_km"] = x1
            net.line.loc[mask, "max_i_ka"] = imax
            net.line.loc[mask, "r0_ohm_per_km"] = 3 * r75
            net.line.loc[mask, "x0_ohm_per_km"] = 3 * x1
            converged, min_v, max_line, max_trafo = solve(net)
            within = bool(converged and min_v is not None and min_v >= 0.9
                          and max_line <= 100 and max_trafo <= 100)
            rows.append({
                "station_code": station, "component_id": component_id,
                "zone_id": zone_id, "voltage_kv": voltage_kv,
                "scenario": design, "catalog_id": catalog_id,
                "overhead_line_count": overhead_count,
                "r75_ohm_per_km": r75, "x_ohm_per_km": x1,
                "max_i_ka_inferred": imax, "converged": converged,
                "min_voltage_pu": min_v,
                "max_line_loading_percent": max_line,
                "max_transformer_loading_percent": max_trafo,
                "within_design_limits": within,
                "evidence_status": "PUBLIC_R20_STANDARD_WITH_INFERRED_X_AMPACITY_ASSET_ASSIGNMENT",
            })
        print(f"{index}/{len(failures)} {zone_id}", flush=True)

    csv_path = args.output / "zone_conductor_sensitivity.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader(); writer.writerows(rows)
    with duckdb.connect(str(args.database)) as con:
        con.execute("CREATE SCHEMA IF NOT EXISTS study")
        con.execute("CREATE OR REPLACE TABLE study.mv_zone_conductor_sensitivity AS SELECT * FROM read_csv_auto(?,header=true)", [str(csv_path)])

    by_design = {}
    for design in DESIGNS:
        subset = [row for row in rows if row["scenario"] == design]
        by_design[design] = {
            "zones": len(subset),
            "converged": sum(row["converged"] for row in subset),
            "within_design_limits": sum(row["within_design_limits"] for row in subset),
        }
    still_failed = [
        {"station_code": station, "component_id": component_id}
        for station, component_id in failures
        if not any(row["station_code"] == station and row["component_id"] == component_id
                   and row["converged"] for row in rows)
    ]
    report = {
        "result": "PARTIAL",
        "checks": {
            "failed_zones_screened": len(failures),
            "sensitivity_rows": len(rows),
            "zones_converging_with_at_least_one_upgrade": len(failures)-len(still_failed),
            "zones_still_not_converged": len(still_failed),
        },
        "by_design": by_design,
        "zones_still_not_converged": still_failed,
        "scope": "Conductor design sensitivity for every failed source-distance MV zone",
        "interpretation": "Convergence after an upgrade shows parameter sensitivity only; it does not identify the installed conductor or validate the inferred feeder boundary.",
        "limitations": [
            "Topology, source-zone boundaries and PTD allocation remain inferred",
            "X, ampacity and zero-sequence ratios remain engineering assumptions",
            "All overhead segments in a zone receive one uniform sensitivity type",
            "Cable parameters, loads and transformer assignments remain fixed",
        ],
    }
    (args.output / "validation.json").write_text(json.dumps(report, indent=2)+"\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
