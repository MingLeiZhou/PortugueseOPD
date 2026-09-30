#!/usr/bin/env python3
"""Rebuild the >=60 kV network with the REN Annex B/D reconciliation and report
the result against the official REN statistics (Caracterização da RNT, 31-12-2025).

Usage:  python3 src/run_ren_reconciliation.py [--skip-build]
Writes: outputs/validation/ren_reconciliation_report.json
Stops on the first failing step (each step is an existing pipeline script).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time

import pandas as pd

from common import PROJECT, RAW, TABLES, VALIDATION, utc_now, write_json

STEPS = [
    "build_topology.py", "build_transformers.py", "add_demand_generation.py", "assign_parameters.py", "apply_static_model_corrections.py",
    "audit_grid_coverage.py", "audit_circuits_components.py", "build_pandapower.py",
    "validate_model.py", "validate_powerflow_scenarios.py",
]
REN_CIRCUIT_KM = {400: 3465.2, 220: 3916.1, 150: 2513.9}


def run(script: str) -> float:
    start = time.time()
    print(f"$ {script}", flush=True)
    subprocess.run([sys.executable, str(PROJECT / "src" / script)], check=True, stdout=subprocess.DEVNULL)
    return round(time.time() - start, 1)


def mainland_circuit_km() -> dict[str, dict[str, float]]:
    from pyproj import Transformer
    from shapely.geometry import LineString, shape
    from shapely.ops import transform
    project = Transformer.from_crs(4326, 3763, always_xy=True).transform
    boundary = json.loads((RAW / "reference" / "portugal_gisco_2024.geojson").read_text(encoding="utf-8"))
    polygons = []
    for feature in boundary["features"]:
        geometry = shape(feature["geometry"])
        polygons += list(geometry.geoms) if geometry.geom_type == "MultiPolygon" else [geometry]
    mainland = transform(project, max(polygons, key=lambda polygon: polygon.area))
    lines = pd.read_csv(TABLES / "lines.csv", low_memory=False)
    lines = lines[lines["voltage_kv"].isin(REN_CIRCUIT_KM) & lines["in_service"].astype(str).str.lower().isin(["true", "1"])]
    lines["km"] = [transform(project, LineString(json.loads(g))).intersection(mainland).length / 1000 for g in lines["geometry_json"]]
    lines["circuit_km"] = lines["km"] * lines["parallel"]
    out = {}
    for voltage, official in REN_CIRCUIT_KM.items():
        model = float(lines.loc[lines["voltage_kv"] == voltage, "circuit_km"].sum())
        out[str(voltage)] = {"model_circuit_km": round(model, 1), "ren_circuit_km": official, "diff_percent": round((model / official - 1) * 100, 2)}
    return out


def transformer_mva() -> dict[str, dict[str, float]]:
    ren = pd.read_csv(RAW / "ren" / "ren_transformers_2025-12-31.csv", sep=";", dtype=str)
    ren["mva"] = ren["mva"].map(lambda v: (lambda m: int(m[1]) * float(m[2]) if m else float(v))(re.match(r"^(\d+)x(\d+)$", v)))
    model = pd.read_csv(TABLES / "transformers_topology.csv")
    model["mva"] = model["sn_mva"] * model["parallel"].fillna(1)
    model["pair"] = model["hv_kv"].astype(int).astype(str) + "/" + model["lv_kv"].astype(int).astype(str)
    rnt = model[model["source_status"] == "REN_ANNEX_D_UNIT"]
    out = {}
    for pair, official in ren.groupby("kv")["mva"].sum().items():
        out[pair] = {"model_rnt_mva": float(rnt.loc[rnt["pair"] == pair, "mva"].sum()), "ren_mva": float(official),
                     "model_non_rnt_mva": float(model.loc[(model["pair"] == pair) & (model["source_status"] != "REN_ANNEX_D_UNIT"), "mva"].sum())}
    return out


def transformer_hv_lv_max_km() -> float:
    """Guard against transformers joining buses of different places (e.g. same-name facilities)."""
    import math
    b = pd.read_csv(TABLES / "buses.csv").set_index("bus_id")
    t = pd.read_csv(TABLES / "transformers_topology.csv")
    def km(a, c):
        (x1, y1), (x2, y2) = b.loc[a, ["lon", "lat"]], b.loc[c, ["lon", "lat"]]
        return math.hypot((x1 - x2) * 111.32 * math.cos(math.radians(y1)), (y1 - y2) * 110.54)
    d = max(km(h, l) for h, l in zip(t["hv_bus"], t["lv_bus"]))
    if d > 10.0:
        raise SystemExit(f"transformer joins buses {d:.1f} km apart; check facility matching")
    return round(d, 2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="only recompute the report from existing tables")
    args = parser.parse_args()
    timings = {} if args.skip_build else {step: run(step) for step in STEPS}
    power_flow = json.loads((PROJECT / "outputs" / "power_flow" / "power_flow_summary.json").read_text())
    report = {
        "generated_at": utc_now(),
        "source": "REN Caracterização da RNT 31-12-2025, Annex B (lines) and Annex D (transformers)",
        "step_seconds": timings,
        "circuit_km_mainland": mainland_circuit_km(),
        "transformer_mva": transformer_mva(),
        "line_corrections_applied": len(pd.read_csv(TABLES / "ren_line_corrections_applied.csv")),
        "static_model_corrections": pd.read_csv(TABLES / "static_model_corrections_applied.csv")["status"].value_counts().to_dict(),
        "transformer_actions": pd.read_csv(TABLES / "ren_annex_d_transformer_reconciliation.csv")["action"].value_counts().to_dict(),
        "transformer_hv_lv_max_distance_km": transformer_hv_lv_max_km(),
        "base_case": {key: power_flow.get(key) for key in ("converged", "topological_components", "vm_pu_min", "vm_pu_max", "line_loading_percent_max", "trafo_loading_percent_max", "losses_p_mw", "total_load_p_mw")},
    }
    write_json(VALIDATION / "ren_reconciliation_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
