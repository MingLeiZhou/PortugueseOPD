#!/usr/bin/env python3
"""Build and validate seven *synthetic* research layers from the frozen SimPT60 inputs.

This is a reproducible feasibility pilot, not an operator equipment inventory.
It reads the release database and writes seven CSV files plus an audit JSON.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import urllib.request
from collections import Counter
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/releases/SimPT60-2026.09.21-r1/bundle/inputs.duckdb"
OUT = ROOT / "output/feasibility/seven_module_pilot"
MARKET_URL = "https://servicebus.ren.pt/datahubapi/electricity/ElectricityMarketPricesDaily?culture=pt-PT&date=2025-05-01"


def rows(con: duckdb.DuckDBPyConnection, sql: str) -> list[dict]:
    result = con.execute(sql)
    names = [col[0] for col in result.description]
    return [dict(zip(names, record)) for record in result.fetchall()]


def write_csv(path: Path, records: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)


def build(con: duckdb.DuckDBPyConnection, market_payload: dict) -> tuple[dict[str, list[dict]], dict]:
    buses = rows(con, "SELECT bus_id, voltage_kv FROM grid.buses")
    lines = rows(con, "SELECT line_id, from_bus, to_bus, voltage_kv, max_i_ka, in_service FROM grid.lines")
    transformers = rows(con, "SELECT transformer_id, hv_bus, lv_bus, hv_kv, lv_kv, sn_mva FROM grid.transformers")
    generators = rows(con, "SELECT generator_id, bus_id, generation_source, nameplate_mw FROM grid.generators WHERE bus_id IS NOT NULL")
    bus_ids = {b["bus_id"] for b in buses}
    errors: list[str] = []
    if not buses or not lines:
        errors.append("Source grid has no buses or lines")

    terminals: list[dict] = []
    switches: list[dict] = []
    protection: list[dict] = []
    for branch_kind, branch_list, id_key, ends in (
        ("line", lines, "line_id", ("from_bus", "to_bus")),
        ("transformer", transformers, "transformer_id", ("hv_bus", "lv_bus")),
    ):
        for branch in branch_list:
            asset_id = branch[id_key]
            for side, field in enumerate(ends, start=1):
                bus_id = branch[field]
                terminal_id = f"TERM:{asset_id}:{side}"
                if bus_id not in bus_ids:
                    errors.append(f"Missing terminal bus: {terminal_id}")
                terminals.append(dict(terminal_id=terminal_id, asset_id=asset_id,
                                      asset_kind=branch_kind, bus_id=bus_id, side=side,
                                      provenance="DERIVED_FROM_GRID_TOPOLOGY"))
                switches.append(dict(switch_id=f"SW:{asset_id}:{side}", terminal_id=terminal_id,
                                     normal_state="closed" if branch_kind == "transformer" or branch["in_service"] else "open",
                                     equipment_status="SYNTHETIC_ABSTRACTION",
                                     provenance="DERIVED_OR_ASSUMED"))
                protection.append(dict(protection_id=f"PROT:{asset_id}:{side}", terminal_id=terminal_id,
                                       function="DISTANCE_TEMPLATE" if branch_kind == "line" else "TRANSFORMER_DIFFERENTIAL_TEMPLATE",
                                       pickup_setting=None, time_setting=None,
                                       settings_status="REQUIRES_FAULT_AND_SELECTIVITY_STUDY",
                                       provenance="SYNTHETIC_FUNCTION_TEMPLATE"))

    measurements = [dict(measurement_id=f"MEAS:V:{b['bus_id']}", target_id=b["bus_id"],
                         quantity="VOLTAGE_MAGNITUDE", unit="kV", sample_status="DEFINITION_ONLY",
                         provenance="SYNTHETIC_SENSOR") for b in buses]
    measurements += [dict(measurement_id=f"MEAS:P:{l['line_id']}", target_id=l["line_id"],
                          quantity="ACTIVE_POWER_FLOW", unit="MW", sample_status="DEFINITION_ONLY",
                          provenance="SYNTHETIC_SENSOR") for l in lines]

    inverters = []
    plant_transformers = []
    costs = []
    cost_archetypes = {"solar": 0.0, "wind": 0.0, "hydro": 12.0,
                       "gas": 65.0, "coal": 85.0, "biomass": 45.0}
    for gen in generators:
        gid = gen["generator_id"]
        technology = (gen["generation_source"] or "unknown").lower()
        capacity = gen["nameplate_mw"]
        if capacity is None or capacity <= 0:
            continue
        if any(x in technology for x in ("solar", "wind", "battery", "storage")):
            inverters.append(dict(inverter_id=f"INV:{gid}", generator_id=gid,
                                  rated_mva=round(float(capacity) * 1.1, 6),
                                  control_mode="PQ_TEMPLATE", model_status="TECHNOLOGY_ARCHETYPE",
                                  provenance="ASSUMED_FROM_GENERATOR_TYPE"))
        # An interface proposal is deliberately not connected into the frozen solved network.
        plant_transformers.append(dict(interface_id=f"GTI:{gid}", generator_id=gid,
                                       grid_bus_id=gen["bus_id"], rated_mva=round(float(capacity) * 1.1, 6),
                                       connection_status="PROPOSED_NOT_WIRED_INTO_BASE_MODEL",
                                       provenance="SYNTHETIC_INTERFACE_PLACEHOLDER"))
        value = next((v for key, v in cost_archetypes.items() if key in technology), 35.0)
        costs.append(dict(cost_id=f"COST:{gid}", generator_id=gid,
                          variable_eur_per_mwh=value, numeric_status="ILLUSTRATIVE_NOT_CALIBRATED",
                          provenance="SYNTHETIC_TECHNOLOGY_ARCHETYPE"))

    events = [dict(event_id=f"SCENARIO:N-1:{l['line_id']}", target_id=l["line_id"],
                   event_type="LINE_OUTAGE", probability=None,
                   historical_status="SYNTHETIC_SCENARIO",
                   provenance="DERIVED_FROM_GRID_LINE") for l in lines]
    pt_series = next((s.get("data", []) for s in market_payload.get("series", []) if s.get("name") == "PT"), [])
    market_prices = [dict(market_id=f"REN:PT:2025-05-01:{hour:02d}", date="2025-05-01",
                          market_hour_label=hour, price_eur_per_mwh=float(price),
                          provenance="PUBLIC_REN_MARKET_SERIES")
                     for hour, price in enumerate(pt_series, start=1) if price is not None]
    tables = {"terminals": terminals, "switches": switches, "protection": protection,
              "measurements": measurements, "inverters": inverters,
              "plant_transformers": plant_transformers, "costs": costs,
              "market_prices": market_prices, "fault_outage_scenarios": events}

    # Each logical module has its own checks, including links and physical bounds.
    terminal_ids = {r["terminal_id"] for r in terminals}
    gen_ids = {r["generator_id"] for r in generators}
    line_ids = {r["line_id"] for r in lines}
    for name, records, key in (
        ("terminals", terminals, "terminal_id"), ("switches", switches, "switch_id"),
        ("protection", protection, "protection_id"), ("measurements", measurements, "measurement_id"),
        ("inverters", inverters, "inverter_id"), ("plant_transformers", plant_transformers, "interface_id"),
        ("costs", costs, "cost_id"), ("fault_outage_scenarios", events, "event_id"),
        ("market_prices", market_prices, "market_id"),
    ):
        if len({r[key] for r in records}) != len(records):
            errors.append(f"Duplicate key in {name}")
    if any(r["terminal_id"] not in terminal_ids for r in switches + protection):
        errors.append("Switch/protection terminal foreign key failed")
    if any(r["generator_id"] not in gen_ids for r in inverters + plant_transformers + costs):
        errors.append("Generator foreign key failed")
    if any(r["target_id"] not in line_ids for r in events):
        errors.append("Scenario line foreign key failed")
    if any(r["rated_mva"] <= 0 for r in inverters + plant_transformers):
        errors.append("Nonpositive equipment rating")
    if any(r["variable_eur_per_mwh"] < 0 for r in costs):
        errors.append("Negative illustrative cost")
    if len(market_prices) != 24 or any(not math.isfinite(r["price_eur_per_mwh"]) for r in market_prices):
        errors.append("Missing or nonfinite public PT hourly market series")
    if len(terminals) != 2 * (len(lines) + len(transformers)):
        errors.append("Terminal cardinality mismatch")
    if len(events) != len(lines):
        errors.append("N-1 scenario coverage mismatch")

    audit = {
        "result": "PASS" if not errors else "FAIL",
        "scope": "Seven synthetic module prototypes; no operator equipment or historical event claim",
        "source_counts": {"buses": len(buses), "lines": len(lines), "transformers": len(transformers), "connected_generators": len(generators)},
        "module_counts": {"switches_and_terminals": len(switches), "protection": len(protection),
                          "measurements": len(measurements), "inverters": len(inverters),
                          "plant_transformers": len(plant_transformers), "cost_and_market": len(costs),
                          "fault_outage": len(events)},
        "public_market_price_rows": len(market_prices),
        "data_status": dict(Counter(r["provenance"] for records in tables.values() for r in records)),
        "validation_errors": errors,
        "remaining_gates": [
            "Calculate relay settings with a short-circuit and selectivity study",
            "Generate measurement time series from solved operating cases and independent noise assumptions",
            "Wire generator interface transformers into a new network version and rerun AC power flow",
            "Calibrate cost archetypes against documented public sources before economic-dispatch claims",
            "Run actual fault/outage simulations; scenario rows alone are not fault results",
        ],
    }
    return tables, audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DB)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--market-json", type=Path, default=None,
                        help="Saved REN daily market JSON; defaults to output/source_ren_market_2025-05-01.json")
    parser.add_argument("--fetch-market", action="store_true", help="Refresh the public REN market snapshot")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    market_path = args.market_json or args.output / "source_ren_market_2025-05-01.json"
    if args.fetch_market:
        request = urllib.request.Request(MARKET_URL, headers={"User-Agent": "SimPT-feasibility/1.0"})
        with urllib.request.urlopen(request, timeout=25) as response:
            payload = json.load(response)
        market_path.parent.mkdir(parents=True, exist_ok=True)
        market_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not market_path.exists():
        parser.error(f"Missing REN market snapshot: {market_path}; run once with --fetch-market")
    market_payload = json.loads(market_path.read_text(encoding="utf-8"))
    with duckdb.connect(str(args.database), read_only=True) as con:
        tables, audit = build(con, market_payload)
    for name, records in tables.items():
        if records:
            write_csv(args.output / f"{name}.csv", records, list(records[0]))
    (args.output / "validation.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": audit["result"], "module_counts": audit["module_counts"],
                      "remaining_gates": audit["remaining_gates"]}, ensure_ascii=False, indent=2))
    return 0 if audit["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
