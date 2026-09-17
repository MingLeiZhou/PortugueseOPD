#!/usr/bin/env python3
"""Build one PT60 AC scenario from normalized public-data inputs.

The module is both an importable interface (`build_from_public_data`) and a
command-line program.  It deliberately accepts aggregate public operating
quantities and substation measurements; it does not imply access to SCADA,
breaker states, state-estimator results, or per-circuit telemetry.
"""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pandapower as pp

from common import PROJECT, sha256, utc_now, write_json
from run_temporal_validation import GENERATION_GROUPS, run_case


DEFAULT_MODEL = PROJECT / "outputs" / "model" / "portuguese_hv_candidate.json"
DEFAULT_GENERATORS = PROJECT / "outputs" / "tables" / "generators.csv"
SCHEMA_PATH = PROJECT / "config" / "public_input_schema.json"
INTERCONNECTOR_COMMISSIONING = pd.Timestamp("2026-07-02T00:00:00+00:00")


def _required(mapping: dict[str, Any], keys: set[str], context: str) -> None:
    missing = sorted(keys - set(mapping))
    if missing:
        raise ValueError(f"{context} missing required fields: {missing}")


def _nonnegative(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a JSON number")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return result


def _aware_timestamp(value: Any, label: str) -> pd.Timestamp:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty date-time string")
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be a valid date-time string") from exc
    if timestamp.tzinfo is None:
        raise ValueError(f"{label} must contain an explicit UTC offset")
    return timestamp


def validate_public_input(spec: dict[str, Any], loads: pd.DataFrame) -> None:
    _required(spec, {"schema_version", "case_id", "timestamp_utc", "national", "sources"}, "scenario.json")
    if spec["schema_version"] != "1.0":
        raise ValueError("Unsupported schema_version; expected 1.0")
    if not isinstance(spec["case_id"], str) or not spec["case_id"].strip():
        raise ValueError("case_id must be a non-empty string")
    timestamp = _aware_timestamp(spec["timestamp_utc"], "timestamp_utc")
    _aware_timestamp(spec.get("load_profile_timestamp_utc", timestamp.isoformat()), "load_profile_timestamp_utc")
    load_profile_mode = spec.get("load_profile_mode", "SUPPLIED_PUBLIC_DATA")
    if not isinstance(load_profile_mode, str) or load_profile_mode not in {
        "SUPPLIED_PUBLIC_DATA",
        "SUPPLIED_PUBLIC_DATA_SEASONAL_PROXY",
    }:
        raise ValueError(
            "load_profile_mode must be SUPPLIED_PUBLIC_DATA or "
            "SUPPLIED_PUBLIC_DATA_SEASONAL_PROXY"
        )
    if "rating_factor_relative_to_static_summer" in spec:
        rating_factor = spec["rating_factor_relative_to_static_summer"]
        if isinstance(rating_factor, bool) or not isinstance(rating_factor, (int, float)):
            raise ValueError("rating_factor_relative_to_static_summer must be a JSON number")
        if not math.isfinite(float(rating_factor)) or float(rating_factor) <= 0.0:
            raise ValueError("rating_factor_relative_to_static_summer must be a finite positive number")
    if "new_interconnector_in_service" in spec and not isinstance(spec["new_interconnector_in_service"], bool):
        raise ValueError("new_interconnector_in_service must be a boolean")
    if "rnt_loss_benchmark_date" in spec:
        benchmark_date = spec["rnt_loss_benchmark_date"]
        if not isinstance(benchmark_date, str):
            raise ValueError("rnt_loss_benchmark_date must be an ISO date string")
        try:
            date.fromisoformat(benchmark_date)
        except ValueError as exc:
            raise ValueError("rnt_loss_benchmark_date must be an ISO date string") from exc
    national = spec["national"]
    if not isinstance(national, dict):
        raise ValueError("national must be an object")
    _required(
        national,
        {
            "consumption_mw", "consumption_plus_storage_mw", "pumping_mw",
            "battery_consumption_mw", "import_mw", "export_mw",
            "generation_by_source_mw", "rnt_monthly_loss_percent",
        },
        "national",
    )
    for key in (
        "consumption_mw", "consumption_plus_storage_mw", "pumping_mw",
        "battery_consumption_mw", "import_mw", "export_mw",
        "rnt_monthly_loss_percent",
    ):
        _nonnegative(national[key], f"national.{key}")
    expected_total = float(national["consumption_mw"]) + float(national["pumping_mw"]) + float(national["battery_consumption_mw"])
    if not math.isclose(expected_total, float(national["consumption_plus_storage_mw"]), abs_tol=0.11):
        raise ValueError(
            "consumption_plus_storage_mw must equal consumption_mw + pumping_mw + "
            "battery_consumption_mw within 0.11 MW"
        )
    generation = national["generation_by_source_mw"]
    if not isinstance(generation, dict):
        raise ValueError("national.generation_by_source_mw must be an object")
    unknown = sorted(set(generation) - set(GENERATION_GROUPS))
    if unknown:
        raise ValueError(f"Unknown generation source names: {unknown}")
    for name in GENERATION_GROUPS:
        _nonnegative(generation.get(name, 0.0), f"generation_by_source_mw.{name}")
    _required(loads.columns.to_series().to_dict(), {"facility_code", "substation_name", "p_mw"}, "loads.csv")
    facility_codes = loads["facility_code"]
    if (
        facility_codes.isna().any()
        or facility_codes.astype(str).str.strip().eq("").any()
        or facility_codes.astype(str).duplicated().any()
    ):
        raise ValueError("loads.csv facility_code values must be non-empty and unique")
    p = pd.to_numeric(loads["p_mw"], errors="raise")
    status = loads.get("observation_status", pd.Series("OBSERVED", index=loads.index)).fillna("OBSERVED")
    if not status.isin(["OBSERVED", "MISSING", "PARTIAL_OBSERVATION"]).all():
        raise ValueError("Unknown loads.csv observation_status")
    missing = status.eq("MISSING")
    if not p.loc[~missing].map(math.isfinite).all() or not p.loc[missing].isna().all():
        raise ValueError("Observed p_mw must be finite; explicitly MISSING p_mw must be blank")
    if float(p.sum()) > float(national["consumption_mw"]) + 1e-6:
        raise ValueError("Sum of loads.csv p_mw exceeds national consumption_mw")
    if "q_mvar" in loads:
        q = pd.to_numeric(loads["q_mvar"], errors="raise")
        if not q.loc[~missing].map(math.isfinite).all() or not q.loc[missing].isna().all():
            raise ValueError("Observed q_mvar must be finite; MISSING q_mvar must be blank")
    if "independent_cross_border" in spec:
        independent = spec["independent_cross_border"]
        if not isinstance(independent, dict):
            raise ValueError("independent_cross_border must be an object")
        _required(independent, {"net_import_mw", "source_url"}, "independent_cross_border")
        value = independent["net_import_mw"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            raise ValueError("independent_cross_border.net_import_mw must be finite")
        if not isinstance(independent["source_url"], str):
            raise ValueError("independent_cross_border.source_url must be a string")
    if "weather" in spec and not isinstance(spec["weather"], dict):
        raise ValueError("weather must be an object")
    sources = spec["sources"]
    if not isinstance(sources, dict):
        raise ValueError("sources must be an object")
    _required(sources, {"national_balance", "substation_load"}, "sources")
    if any(not isinstance(value, str) for value in sources.values()):
        raise ValueError("sources values must be strings")


def _case_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    timestamp = pd.Timestamp(spec["timestamp_utc"])
    month = timestamp.month
    default_factor = 1.15 if month in {12, 1, 2} else 1.10 if month in {3, 4, 10, 11} else 1.0
    interconnector = bool(spec.get("new_interconnector_in_service", timestamp >= INTERCONNECTOR_COMMISSIONING))
    benchmark_date = str(spec.get("rnt_loss_benchmark_date", (timestamp + pd.offsets.MonthEnd(0)).date()))
    return {
        "case_id": str(spec["case_id"]),
        "timestamp_utc": timestamp.isoformat(),
        "rating_mode": str(spec.get("rating_mode", "PUBLIC_INPUT_SEASONAL_STATIC_PROXY")),
        "rating_factor_relative_to_static_summer": float(spec.get("rating_factor_relative_to_static_summer", default_factor)),
        "new_interconnector_in_service": interconnector,
        "external_boundary_buses": 8 if interconnector else 7,
        "physical_cross_border_circuits": 10 if interconnector else 9,
        "load_profile_mode": str(spec.get("load_profile_mode", "SUPPLIED_PUBLIC_DATA")),
        "load_profile_timestamp_utc": pd.Timestamp(spec.get("load_profile_timestamp_utc", timestamp)).isoformat(),
        "rnt_loss_benchmark_date": benchmark_date,
        "installed_capacity_benchmark": timestamp.strftime("%Y-%m"),
        "analysis_role": "CALLER_SUPPLIED_PUBLIC_SCENARIO",
    }


def _observation_from_spec(spec: dict[str, Any]) -> dict[str, Any]:
    national = spec["national"]
    generation = {name: float(national["generation_by_source_mw"].get(name, 0.0)) for name in GENERATION_GROUPS}
    return {
        "source_url": str(spec["sources"].get("national_balance", "caller-supplied-public-data")),
        "load_mw": float(national["consumption_mw"]),
        "load_plus_storage_mw": float(national["consumption_plus_storage_mw"]),
        "pumping_mw": float(national["pumping_mw"]),
        "battery_consumption_mw": float(national["battery_consumption_mw"]),
        "import_mw": float(national["import_mw"]),
        "export_mw": float(national["export_mw"]),
        "net_import_mw": float(national["import_mw"]) - float(national["export_mw"]),
        "generation_by_source_mw": generation,
        "generation_total_mw": sum(generation.values()),
    }


def _snapshot_from_loads(loads: pd.DataFrame) -> pd.DataFrame:
    result = pd.DataFrame({
        "codigo_subestacao": loads["facility_code"].astype(str),
        "subestacao": loads["substation_name"].astype(str),
        "energia": pd.to_numeric(loads["p_mw"], errors="raise") * 250.0,
    })
    if "q_mvar" in loads:
        result["q_mvar"] = pd.to_numeric(loads["q_mvar"], errors="raise")
    return result


def _export_solved_tables(solved_path: Path, output_dir: Path) -> None:
    net = pp.from_json(solved_path)
    bus = net.bus.copy()
    for column in net.res_bus.columns:
        bus[f"result_{column}"] = net.res_bus[column]
    bus.to_csv(output_dir / "bus_results.csv", index_label="pandapower_index")
    line = net.line.copy()
    for column in net.res_line.columns:
        line[f"result_{column}"] = net.res_line[column]
    line.to_csv(output_dir / "line_results.csv", index_label="pandapower_index")
    transformer = net.trafo.copy()
    for column in net.res_trafo.columns:
        transformer[f"result_{column}"] = net.res_trafo[column]
    transformer.to_csv(output_dir / "transformer_results.csv", index_label="pandapower_index")
    generator_frames: list[pd.DataFrame] = []
    for element, result_name in (("gen", "res_gen"), ("sgen", "res_sgen")):
        table = net[element].copy()
        result_table = net[result_name]
        for column in result_table.columns:
            table[f"result_{column}"] = result_table[column]
        table["pandapower_element"] = element
        generator_frames.append(table)
    pd.concat(generator_frames, ignore_index=False).to_csv(
        output_dir / "generator_results.csv", index_label="pandapower_index"
    )


def build_from_public_data(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    model_path: str | Path = DEFAULT_MODEL,
    generators_path: str | Path = DEFAULT_GENERATORS,
) -> dict[str, Any]:
    """Validate normalized public inputs, solve one AC case, and export results."""
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    scenario_path = input_dir / "scenario.json"
    loads_path = input_dir / "loads.csv"
    if not scenario_path.exists() or not loads_path.exists():
        raise FileNotFoundError("Input directory must contain scenario.json and loads.csv")
    spec = json.loads(scenario_path.read_text(encoding="utf-8"))
    loads = pd.read_csv(loads_path, dtype={"facility_code": str})
    validate_public_input(spec, loads)
    generators = pd.read_csv(generators_path, low_memory=False)
    dispatch_path = input_dir / "generator_dispatch.csv"
    dispatch = pd.read_csv(dispatch_path) if dispatch_path.exists() else None
    storage_path = input_dir / "storage_loads.csv"
    storage = pd.read_csv(storage_path, dtype={"bus_id": str}) if storage_path.exists() else None
    if storage is not None:
        _required(storage.columns.to_series().to_dict(), {"storage_type", "bus_id", "p_mw"}, "storage_loads.csv")
    line_override_path = input_dir / "line_overrides.csv"
    line_overrides = pd.read_csv(line_override_path, dtype={"line_id": str}) if line_override_path.exists() else None
    case = _case_from_spec(spec)
    observation = _observation_from_spec(spec)
    weather = dict(spec.get("weather", {"status": "NOT_SUPPLIED_NOT_USED_FOR_STATIC_RATING"}))
    loss = {
        "benchmark_date": case["rnt_loss_benchmark_date"],
        "loss_percent": float(spec["national"]["rnt_monthly_loss_percent"]),
        "source_url": str(spec["sources"].get("rnt_monthly_loss", "caller-supplied-public-data")),
        "scope": "Caller-supplied REN RNT monthly physical balance",
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    result, source_rows, hotspot_rows, load_rows, border_rows, boundary_rows, loss_row = run_case(
        case,
        generators,
        refresh=False,
        observation_override=observation,
        load_snapshot_override=_snapshot_from_loads(loads),
        weather_override=weather,
        rnt_loss_override=loss,
        generator_dispatch_override=dispatch,
        storage_load_override=storage,
        line_parameter_overrides=line_overrides,
        independent_cross_border_override=spec.get("independent_cross_border"),
        model_input=Path(model_path),
        output_dir=output_dir,
    )
    _export_solved_tables(output_dir / f"{case['case_id']}_solved.json", output_dir)
    pd.DataFrame(source_rows).to_csv(output_dir / "generation_source_balance.csv", index=False)
    pd.DataFrame(hotspot_rows).to_csv(output_dir / "line_loading_hotspots.csv", index=False)
    pd.DataFrame(load_rows).to_csv(output_dir / "load_mapping_audit.csv", index=False)
    pd.DataFrame(border_rows).to_csv(output_dir / "cross_border_evidence.csv", index=False)
    pd.DataFrame(boundary_rows).to_csv(output_dir / "modeled_boundary_flows.csv", index=False)
    pd.DataFrame([loss_row]).to_csv(output_dir / "loss_comparison.csv", index=False)
    write_json(output_dir / "summary.json", {key: value for key, value in result.items() if key not in {"weather", "ren_observation"}})
    inputs = [scenario_path, loads_path]
    inputs += [path for path in (dispatch_path, storage_path, line_override_path) if path.exists()]
    manifest = {
        "generated_at": utc_now(),
        "interface": "PT60 normalized public input v1.0",
        "case_id": case["case_id"],
        "inputs": [{"path": str(path), "sha256": sha256(path), "bytes": path.stat().st_size} for path in inputs],
        "model_input": {"path": str(model_path), "sha256": sha256(Path(model_path))},
        "generator_inventory": {"path": str(generators_path), "sha256": sha256(Path(generators_path))},
        "outputs": sorted(path.name for path in output_dir.iterdir() if path.is_file()),
        "scope": "PUBLIC_DATA_AC_BENCHMARK_NOT_OPERATOR_DIGITAL_TWIN",
        "unsupported_claims": [
            "real-time breaker or busbar state",
            "operator state-estimator voltages and phase angles",
            "per-circuit observed interconnector flow",
            "protection, reserves, AGC, and remedial actions",
            "dynamic line ratings and real-time transformer tap positions",
        ],
    }
    write_json(output_dir / "run_manifest.json", manifest)
    return {"summary": result, "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    parser.add_argument("--generators", type=Path, default=DEFAULT_GENERATORS)
    args = parser.parse_args()
    result = build_from_public_data(args.input_dir, args.output_dir, model_path=args.model, generators_path=args.generators)
    print(json.dumps({
        "case_id": result["summary"]["case_id"],
        "converged": result["summary"]["converged"],
        "maximum_line_loading_percent": result["summary"]["maximum_line_loading_percent"],
        "vm_pu_min": result["summary"]["vm_pu_min"],
        "output_dir": str(args.output_dir),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
