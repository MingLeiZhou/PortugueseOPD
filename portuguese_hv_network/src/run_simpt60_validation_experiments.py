#!/usr/bin/env python3
"""Run the four claim-linked SimPT60 validation experiments.

The source DuckDB is opened read-only.  The suite writes only to ``--output-dir``
and is restartable: every solved sensitivity case is stored as an independent
JSON/NPZ artifact and reused on a subsequent run.

Experiments
-----------
1. Full 60--400 kV topology, coverage, connectivity, and parameter evidence.
2. Electrical-parameter sensitivity on stratified monthly operating states.
3. Load, generation, and border spatial-allocation sensitivity on the same states.
4. Monthly external-validation diagnostics and cluster/block-bootstrap intervals.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
import time
from pathlib import Path
from typing import Any, Iterable

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd
import pandapower as pp
from scipy import stats

from common import PROJECT, utc_now
from run_monthly_15min import DatabaseInputs, _case_from_interval
from run_temporal_validation import (
    GENERATION_GROUPS,
    GENERATOR_INPUT,
    MODEL_INPUT,
    available_asset_mask,
    run_case,
)


DEFAULT_DATABASE = Path(
    "/Volumes/Transcend/PT60_public_data_2025-05-01_2026-03-24/"
    "pt60_public_timeseries.duckdb"
)
DEFAULT_EXTERNAL = PROJECT / "outputs" / "external_evidence_validation"
DEFAULT_OUTPUT = PROJECT / "outputs" / "validation_experiments"
VOLTAGES = [60, 130, 150, 220, 400]
SEED = 20260916
_INPUTS: DatabaseInputs | None = None
_GENERATORS: pd.DataFrame | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--external-validation-dir", type=Path, default=DEFAULT_EXTERNAL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--bootstrap-replicates", type=int, default=1000)
    parser.add_argument(
        "--max-timestamps", type=int,
        help="Pilot limit after stratified selection; omit for all 22 target states.",
    )
    parser.add_argument("--skip-modeling", action="store_true")
    return parser.parse_args()


def sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return value.isoformat()
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(clean(payload), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def dataframe_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def finite_pair(frame: pd.DataFrame, left: str, right: str) -> pd.DataFrame:
    result = frame[[left, right]].apply(pd.to_numeric, errors="coerce")
    return result[np.isfinite(result[left]) & np.isfinite(result[right])]


def pair_metrics(frame: pd.DataFrame, left: str, right: str) -> dict[str, float | int]:
    pair = finite_pair(frame, left, right)
    if len(pair) < 2:
        return {"n": len(pair)}
    x = pair[left].to_numpy(float)
    y = pair[right].to_numpy(float)
    error = x - y
    return {
        "n": int(len(pair)),
        "left_mean": float(x.mean()),
        "right_mean": float(y.mean()),
        "mean_ratio": float(x.mean() / y.mean()) if abs(y.mean()) > 1e-12 else float("nan"),
        "bias": float(error.mean()),
        "mae": float(np.abs(error).mean()),
        "rmse": float(np.sqrt(np.mean(error ** 2))),
        "nrmse_right_mean": float(np.sqrt(np.mean(error ** 2)) / abs(y.mean())) if abs(y.mean()) > 1e-12 else float("nan"),
        "pearson": float(stats.pearsonr(x, y).statistic),
        "spearman": float(stats.spearmanr(x, y).statistic),
    }


def run_topology_experiment(database: Path, output: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    connection = duckdb.connect(str(database), read_only=True)
    buses = connection.execute("SELECT * FROM grid.buses").fetchdf()
    lines = connection.execute("SELECT * FROM grid.lines").fetchdf()
    transformers = connection.execute("SELECT * FROM grid.transformers").fetchdf()
    interconnectors = connection.execute("SELECT * FROM grid.interconnectors").fetchdf()
    connection.close()

    active_lines = lines[lines["in_service"].fillna(False)].copy()
    # Connectivity must honor both element and endpoint service state.  The
    # normalized database preserves inactive buses and their source lines for
    # traceability, while the pandapower model applies the executable state.
    net = pp.from_json(MODEL_INPUT)
    graph = nx.MultiGraph()
    active_bus_indices = set(net.bus.index[net.bus["in_service"].fillna(False)].astype(int))
    graph.add_nodes_from(str(net.bus.loc[index, "bus_id"]) for index in active_bus_indices)
    for row in net.line[net.line["in_service"].fillna(False)].itertuples():
        if int(row.from_bus) in active_bus_indices and int(row.to_bus) in active_bus_indices:
            graph.add_edge(
                str(net.bus.loc[int(row.from_bus), "bus_id"]),
                str(net.bus.loc[int(row.to_bus), "bus_id"]),
                key=str(row.line_id),
            )
    for row in net.trafo[net.trafo["in_service"].fillna(False)].itertuples():
        if int(row.hv_bus) in active_bus_indices and int(row.lv_bus) in active_bus_indices:
            graph.add_edge(
                str(net.bus.loc[int(row.hv_bus), "bus_id"]),
                str(net.bus.loc[int(row.lv_bus), "bus_id"]),
                key=str(row.name),
            )
    active_nodes = set(graph.nodes)
    active_graph = graph.copy()
    component_sizes = sorted((len(c) for c in nx.connected_components(nx.Graph(active_graph))), reverse=True)
    simple = nx.Graph(active_graph)
    degree = np.asarray([value for _, value in simple.degree()], dtype=float)
    cycle_rank = simple.number_of_edges() - simple.number_of_nodes() + nx.number_connected_components(simple)

    rows: list[dict[str, Any]] = []
    for voltage in VOLTAGES:
        line_group = lines[pd.to_numeric(lines["voltage_kv"], errors="coerce").eq(voltage)]
        active_line_group = active_lines[pd.to_numeric(active_lines["voltage_kv"], errors="coerce").eq(voltage)]
        bus_group = buses[pd.to_numeric(buses["voltage_kv"], errors="coerce").eq(voltage)]
        parallel = pd.to_numeric(line_group["parallel"], errors="coerce").fillna(1).clip(lower=1)
        length = pd.to_numeric(line_group["length_km"], errors="coerce").fillna(0)
        active_parallel = pd.to_numeric(active_line_group["parallel"], errors="coerce").fillna(1).clip(lower=1)
        active_length = pd.to_numeric(active_line_group["length_km"], errors="coerce").fillna(0)
        rows.append({
            "voltage_kv": voltage,
            "buses": int(len(bus_group)),
            "lines": int(len(line_group)),
            "active_lines": int(len(active_line_group)),
            "route_km": float(length.sum()),
            "circuit_km": float((length * parallel).sum()),
            "active_route_km": float(active_length.sum()),
            "active_circuit_km": float((active_length * active_parallel).sum()),
            "median_line_length_km": float(length.median()),
            "p95_line_length_km": float(length.quantile(0.95)),
        })
    coverage = pd.DataFrame(rows)

    reference_path = Path("data/releases/PT60-v2.0.0/validation/voltage_coverage.csv")
    if reference_path.exists():
        reference = pd.read_csv(reference_path)
        keep = [
            "voltage_kv", "topology_primary_source", "osm_relation_circuit_km",
            "osm_wiki_expected_or_operator_km", "ren_2025_context_km", "interpretation",
        ]
        coverage = coverage.merge(reference[keep], on="voltage_kv", how="left")
        coverage["comparison_reference_km"] = coverage["ren_2025_context_km"].fillna(
            coverage["osm_wiki_expected_or_operator_km"]
        )
        coverage["reference_role"] = np.where(
            coverage["ren_2025_context_km"].notna(),
            "REN_2025_OFFICIAL_CONTEXT", "OSM_CONTEXT_NOT_INDEPENDENT",
        )
        # REN's contextual totals and the E-REDES/OSM retained-topology checks
        # are compared with represented route length, not a parallel-weighted
        # circuit-km total.  Both quantities remain published in the table.
        coverage["model_to_reference_ratio"] = coverage["route_km"] / coverage["comparison_reference_km"]

    evidence_rows: list[dict[str, Any]] = []
    for voltage, group in lines.groupby("voltage_kv", dropna=False):
        for field in ["r_status", "x_status", "c_status", "max_i_status", "parameter_status"]:
            counts = group[field].fillna("MISSING").astype(str).value_counts()
            for status, count in counts.items():
                evidence_rows.append({
                    "voltage_kv": voltage,
                    "field": field,
                    "status": status,
                    "count": int(count),
                    "fraction": float(count / len(group)),
                })
    evidence = pd.DataFrame(evidence_rows)
    topology_summary = {
        "generated_at": utc_now(),
        "database": str(database),
        "database_sha256": sha256(database),
        "buses": int(len(buses)),
        "active_lines": int(len(active_lines)),
        "transformers": int(len(transformers)),
        "interconnectors": int(len(interconnectors)),
        "active_nodes": int(simple.number_of_nodes()),
        "active_edges_simple": int(simple.number_of_edges()),
        "active_edges_multigraph": int(active_graph.number_of_edges()),
        "connected_components": int(nx.number_connected_components(simple)),
        "largest_component_nodes": int(component_sizes[0]) if component_sizes else 0,
        "largest_component_fraction": float(component_sizes[0] / simple.number_of_nodes()) if component_sizes else 0.0,
        "isolated_database_buses": int(len(buses) - len(active_nodes)),
        "cycle_rank_simple_graph": int(cycle_rank),
        "degree_median": float(np.median(degree)),
        "degree_p95": float(np.quantile(degree, 0.95)),
        "degree_max": int(degree.max()),
        "claim_boundary": (
            "Connectivity and public-source coverage are structural checks; they do not estimate "
            "device-level topology precision against an operator model."
        ),
    }
    dataframe_csv(coverage, output / "topology_coverage_by_voltage.csv")
    dataframe_csv(evidence, output / "parameter_evidence_by_voltage.csv")
    dataframe_csv(
        pd.DataFrame({"component_rank": np.arange(1, len(component_sizes) + 1), "nodes": component_sizes}),
        output / "connected_components.csv",
    )
    write_json(output / "topology_summary.json", topology_summary)
    return coverage, evidence, topology_summary


def selected_intervals(database: Path, limit: int | None) -> list[dict[str, Any]]:
    connection = duckdb.connect(str(database), read_only=True)
    frame = connection.execute("""
        WITH dispatch AS (
            SELECT source_date, source_index,
                   max(value_mw) FILTER (WHERE series_name = 'Consumption + Storage') AS load_mw,
                   sum(value_mw) FILTER (WHERE series_name IN ('Wind', 'Solar')) AS wind_solar_mw
            FROM main.ren_dispatch
            GROUP BY source_date, source_index
        )
        SELECT c.local_date, c.source_index, c.timestamp_local, c.timestamp_utc,
               c.eredes_source_date, c.eredes_source_hour, c.eredes_alignment_status,
               d.load_mw, d.wind_solar_mw
        FROM main.interval_calendar c
        JOIN dispatch d
          ON d.source_date = c.local_date
         AND d.source_index = c.source_index
        ORDER BY c.timestamp_utc
    """).fetchdf()
    connection.close()
    frame["month"] = pd.to_datetime(frame["local_date"]).dt.strftime("%Y-%m")
    selected: list[pd.Series] = []
    for _, group in frame.groupby("month", sort=True):
        selected.append(group.loc[group["load_mw"].idxmax()].copy())
        selected[-1]["selection_role"] = "MONTHLY_MAX_SYSTEM_LOAD"
        selected.append(group.loc[group["wind_solar_mw"].idxmax()].copy())
        selected[-1]["selection_role"] = "MONTHLY_MAX_WIND_PLUS_SOLAR"
    result_frame = pd.DataFrame(selected).drop_duplicates("timestamp_utc", keep="first").sort_values("timestamp_utc")
    if limit is not None:
        result_frame = result_frame.head(limit)
    rows: list[dict[str, Any]] = []
    for row in result_frame.to_dict("records"):
        local = pd.Timestamp(row["timestamp_local"])
        rows.append({
            "local_date": str(pd.Timestamp(row["local_date"]).date()),
            "source_index": int(row["source_index"]),
            "timestamp_local": local.isoformat(),
            "timestamp_utc": pd.Timestamp(row["timestamp_utc"]).isoformat(),
            "eredes_source_date": str(row["eredes_source_date"]),
            "eredes_source_hour": str(row["eredes_source_hour"])[:5],
            "eredes_alignment_status": str(row["eredes_alignment_status"]),
            "case_id": f"PT60_VAL_{local.strftime('%Y%m%d_%H%M_%z')}",
            "selection_role": str(row["selection_role"]),
            "selection_load_mw": float(row["load_mw"]),
            "selection_wind_solar_mw": float(row["wind_solar_mw"]),
        })
    return rows


def build_model_variants(output: Path) -> dict[str, Path]:
    model_dir = output / "model_variants"
    model_dir.mkdir(parents=True, exist_ok=True)
    variants = {
        "BASELINE": {},
        "PARAM_IMPEDANCE_LOW": {"line_r_x": 0.8, "line_c": 1.2},
        "PARAM_IMPEDANCE_HIGH": {"line_r_x": 1.2, "line_c": 0.8},
        "PARAM_RATING_LOW": {"line_rating": 0.8},
        "PARAM_RATING_HIGH": {"line_rating": 1.2},
        "PARAM_TRANSFORMER_CONSERVATIVE": {"trafo_sn": 0.8, "trafo_z": 1.2},
        "PARAM_TRANSFORMER_OPTIMISTIC": {"trafo_sn": 1.2, "trafo_z": 0.8},
    }
    paths: dict[str, Path] = {"BASELINE": MODEL_INPUT}
    for name, settings in variants.items():
        if name == "BASELINE":
            continue
        path = model_dir / f"{name.lower()}.json"
        if path.exists():
            paths[name] = path
            continue
        net = pp.from_json(MODEL_INPUT)
        if "line_r_x" in settings:
            factor = settings["line_r_x"]
            net.line["r_ohm_per_km"] *= factor
            net.line["x_ohm_per_km"] *= factor
            net.line["c_nf_per_km"] *= settings["line_c"]
        if "line_rating" in settings:
            net.line["max_i_ka"] *= settings["line_rating"]
        if "trafo_sn" in settings:
            net.trafo["sn_mva"] *= settings["trafo_sn"]
            net.trafo["vk_percent"] *= settings["trafo_z"]
            net.trafo["vkr_percent"] *= settings["trafo_z"]
        pp.to_json(net, path)
        paths[name] = path
    write_json(output / "parameter_scenarios.json", variants)
    return paths


def experiment_tasks(intervals: list[dict[str, Any]], models: dict[str, Path], output: Path) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for interval in intervals:
        for variant, model in models.items():
            tasks.append({
                "variant": variant,
                "interval": interval,
                "model_input": str(model),
                "residual_allocation_mode": "PDIRT",
                "generation_allocation_mode": "DEFAULT",
                "boundary_allocation_mode": "VOLTAGE_X_CIRCUITS",
                "output": str(output),
            })
        for variant, load_mode, generation_mode, boundary_mode in [
            ("SPATIAL_LOAD_UNIFORM", "UNIFORM_PDE", "DEFAULT", "VOLTAGE_X_CIRCUITS"),
            ("SPATIAL_LOAD_CAPACITY", "CAPACITY_PDE", "DEFAULT", "VOLTAGE_X_CIRCUITS"),
            ("SPATIAL_GENERATION_CAPACITY", "PDIRT", "CAPACITY_PROPORTIONAL", "VOLTAGE_X_CIRCUITS"),
            ("SPATIAL_BOUNDARY_UNIFORM", "PDIRT", "DEFAULT", "UNIFORM"),
            ("SPATIAL_BOUNDARY_VOLTAGE", "PDIRT", "DEFAULT", "VOLTAGE"),
        ]:
            tasks.append({
                "variant": variant,
                "interval": interval,
                "model_input": str(models["BASELINE"]),
                "residual_allocation_mode": load_mode,
                "generation_allocation_mode": generation_mode,
                "boundary_allocation_mode": boundary_mode,
                "output": str(output),
            })
    return tasks


def task_path(task: dict[str, Any]) -> Path:
    output = Path(task["output"])
    case_id = task["interval"]["case_id"]
    return output / "raw_cases" / task["variant"] / f"{case_id}.json"


def worker_init(database: str) -> None:
    global _INPUTS, _GENERATORS
    path = Path(database)
    connection = duckdb.connect(":memory:")
    connection.execute(f"ATTACH {sql_string(path.resolve())} AS source (READ_ONLY)")
    _INPUTS = DatabaseInputs(connection, path)
    _GENERATORS = pd.read_csv(GENERATOR_INPUT, low_memory=False)


def capacity_dispatch_override(
    generators: pd.DataFrame, observation: dict[str, Any], timestamp: pd.Timestamp,
) -> pd.DataFrame:
    available = available_asset_mask(generators, timestamp)
    assigned = generators["bus_id"].fillna("").astype(str).ne("") & available
    rows: list[dict[str, Any]] = []
    for source, aliases in GENERATION_GROUPS.items():
        mask = assigned & generators["generation_source"].fillna("").astype(str).str.lower().isin(aliases)
        subset = generators.loc[mask, ["generator_id", "nameplate_mw"]].copy()
        capacity = pd.to_numeric(subset["nameplate_mw"], errors="coerce").fillna(0).clip(lower=0)
        target = float(observation["generation_by_source_mw"].get(source, 0.0))
        assigned_target = min(target, float(capacity.sum()))
        if assigned_target <= 0 or float(capacity.sum()) <= 0:
            power = np.zeros(len(subset))
        else:
            power = capacity.to_numpy(float) * assigned_target / float(capacity.sum())
        rows.extend(
            {"generator_id": str(identifier), "p_mw": float(p_mw), "dispatch_mode": "FIXED"}
            for identifier, p_mw in zip(subset["generator_id"], power)
        )
    return pd.DataFrame(rows, columns=["generator_id", "p_mw", "dispatch_mode"])


def solve_task(task: dict[str, Any]) -> dict[str, Any]:
    if _INPUTS is None or _GENERATORS is None:
        raise RuntimeError("worker is not initialized")
    final_path = task_path(task)
    if final_path.exists():
        return json.loads(final_path.read_text(encoding="utf-8"))
    interval = task["interval"]
    state_path = final_path.with_suffix(".npz")
    case = _case_from_interval(interval, state_path)
    case["case_id"] = f"{interval['case_id']}__{task['variant']}"
    case["residual_allocation_mode"] = task["residual_allocation_mode"]
    case["boundary_allocation_mode"] = task["boundary_allocation_mode"]
    started = time.monotonic()
    try:
        observation = _INPUTS.observation(interval)
        snapshot = _INPUTS.load_snapshot(interval)
        weather = _INPUTS.weather(interval)
        rnt_loss = _INPUTS.rnt_loss(case)
        dispatch = None
        if task["generation_allocation_mode"] == "CAPACITY_PROPORTIONAL":
            dispatch = capacity_dispatch_override(_GENERATORS, observation, pd.Timestamp(interval["timestamp_utc"]))
        result, _, hotspots, _, _, _, _ = run_case(
            case,
            _GENERATORS,
            False,
            save_solved=False,
            observation_override=observation,
            load_snapshot_override=snapshot,
            weather_override=weather,
            rnt_loss_override=rnt_loss,
            generator_dispatch_override=dispatch,
            model_input=Path(task["model_input"]),
            output_dir=Path(task["output"]),
        )
        payload = {
            "status": "COMPLETE",
            "variant": task["variant"],
            "interval": interval,
            "elapsed_seconds": time.monotonic() - started,
            "model_input": task["model_input"],
            "residual_allocation_mode": task["residual_allocation_mode"],
            "generation_allocation_mode": task["generation_allocation_mode"],
            "boundary_allocation_mode": task["boundary_allocation_mode"],
            "result": result,
            "top_lines": hotspots,
            "state_path": str(state_path),
        }
    except Exception as exc:
        if state_path.exists():
            state_path.unlink()
        payload = {
            "status": "FAILED",
            "variant": task["variant"],
            "interval": interval,
            "elapsed_seconds": time.monotonic() - started,
            "error": f"{type(exc).__name__}: {exc}",
        }
    write_json(final_path, payload)
    return payload


def run_modeling(tasks: list[dict[str, Any]], database: Path, workers: int) -> list[dict[str, Any]]:
    completed: list[dict[str, Any]] = []
    pending = [task for task in tasks if not task_path(task).exists()]
    for task in tasks:
        if task_path(task).exists():
            completed.append(json.loads(task_path(task).read_text(encoding="utf-8")))
    print(f"Sensitivity execution: total={len(tasks)} reusable={len(completed)} pending={len(pending)} workers={workers}")
    if pending:
        with ProcessPoolExecutor(max_workers=workers, initializer=worker_init, initargs=(str(database),)) as executor:
            future_map = {executor.submit(solve_task, task): task for task in pending}
            for index, future in enumerate(as_completed(future_map), 1):
                result = future.result()
                completed.append(result)
                print(
                    f"[{index:04d}/{len(pending):04d}] {result['variant']} "
                    f"{result['interval']['timestamp_utc']} {result['status']} "
                    f"{result.get('elapsed_seconds', 0):.1f}s",
                    flush=True,
                )
    return completed


def state_comparison(candidate: dict[str, Any], baseline: dict[str, Any]) -> tuple[float, float]:
    with np.load(candidate["state_path"]) as current, np.load(baseline["state_path"]) as base:
        current_ids = current["line_id"].astype(str)
        base_ids = base["line_id"].astype(str)
        if not np.array_equal(current_ids, base_ids):
            raise ValueError("Line order differs between sensitivity states")
        x = base["loading_percent"].astype(float)
        y = current["loading_percent"].astype(float)
        finite = np.isfinite(x) & np.isfinite(y)
        rho = float(stats.spearmanr(x[finite], y[finite]).statistic)
        k = min(20, int(finite.sum()))
        valid_indices = np.flatnonzero(finite)
        top_x = set(valid_indices[np.argsort(x[finite])[-k:]])
        top_y = set(valid_indices[np.argsort(y[finite])[-k:]])
        jaccard = len(top_x & top_y) / len(top_x | top_y)
        return rho, float(jaccard)


def analyze_sensitivity(payloads: list[dict[str, Any]], output: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    good = [row for row in payloads if row.get("status") == "COMPLETE"]
    lookup = {(row["interval"]["case_id"], row["variant"]): row for row in good}
    records: list[dict[str, Any]] = []
    for row in good:
        if row["variant"] == "BASELINE":
            continue
        case_id = row["interval"]["case_id"]
        baseline = lookup.get((case_id, "BASELINE"))
        if baseline is None:
            continue
        result = row["result"]
        base = baseline["result"]
        rho, jaccard = state_comparison(row, baseline)
        records.append({
            "case_id": case_id,
            "timestamp_utc": row["interval"]["timestamp_utc"],
            "selection_role": row["interval"]["selection_role"],
            "variant": row["variant"],
            "experiment": "PARAMETER" if row["variant"].startswith("PARAM_") else "SPATIAL_ALLOCATION",
            "converged": bool(result["converged"]),
            "line_loading_rank_spearman": rho,
            "top20_line_jaccard": jaccard,
            "delta_vm_min_pu": float(result["vm_pu_min"] - base["vm_pu_min"]),
            "delta_vm_max_pu": float(result["vm_pu_max"] - base["vm_pu_max"]),
            "delta_max_line_loading_pp": float(result["maximum_line_loading_percent"] - base["maximum_line_loading_percent"]),
            "delta_max_trafo_loading_pp": float(result["maximum_transformer_loading_percent"] - base["maximum_transformer_loading_percent"]),
            "delta_loss_percent_of_load": float(result["model_losses_percent_of_load"] - base["model_losses_percent_of_load"]),
            "delta_model_net_import_mw": float(result["model_net_import_mw"] - base["model_net_import_mw"]),
        })
    cases = pd.DataFrame(records)
    summary_rows: list[dict[str, Any]] = []
    for (experiment, variant), group in cases.groupby(["experiment", "variant"]):
        summary_rows.append({
            "experiment": experiment,
            "variant": variant,
            "cases": int(len(group)),
            "convergence_rate": float(group["converged"].mean()),
            "line_rank_spearman_median": float(group["line_loading_rank_spearman"].median()),
            "line_rank_spearman_min": float(group["line_loading_rank_spearman"].min()),
            "top20_jaccard_median": float(group["top20_line_jaccard"].median()),
            "top20_jaccard_min": float(group["top20_line_jaccard"].min()),
            "max_abs_delta_vm_pu": float(group[["delta_vm_min_pu", "delta_vm_max_pu"]].abs().to_numpy().max()),
            "median_abs_delta_max_line_loading_pp": float(group["delta_max_line_loading_pp"].abs().median()),
            "max_abs_delta_max_line_loading_pp": float(group["delta_max_line_loading_pp"].abs().max()),
            "median_abs_delta_loss_percent": float(group["delta_loss_percent_of_load"].abs().median()),
            "max_abs_delta_model_net_import_mw": float(group["delta_model_net_import_mw"].abs().max()),
        })
    summary = pd.DataFrame(summary_rows)
    dataframe_csv(cases, output / "sensitivity_case_results.csv")
    dataframe_csv(summary, output / "sensitivity_summary.csv")
    return cases, summary


def block_bootstrap_ci(
    frame: pd.DataFrame,
    left: str,
    right: str,
    block: str,
    replicates: int,
    seed: int,
) -> list[dict[str, Any]]:
    pair = frame[[block, left, right]].copy()
    pair[[left, right]] = pair[[left, right]].apply(pd.to_numeric, errors="coerce")
    pair = pair[np.isfinite(pair[left]) & np.isfinite(pair[right])]
    groups = [group[[left, right]].to_numpy(float) for _, group in pair.groupby(block)]
    rng = np.random.default_rng(seed)
    values: dict[str, list[float]] = {name: [] for name in ["mean_ratio", "mae", "rmse", "pearson"]}
    for _ in range(replicates):
        sample = np.concatenate([groups[index] for index in rng.integers(0, len(groups), len(groups))])
        x, y = sample[:, 0], sample[:, 1]
        error = x - y
        values["mean_ratio"].append(float(x.mean() / y.mean()))
        values["mae"].append(float(np.abs(error).mean()))
        values["rmse"].append(float(np.sqrt(np.mean(error ** 2))))
        values["pearson"].append(float(np.corrcoef(x, y)[0, 1]))
    result: list[dict[str, Any]] = []
    point = pair_metrics(pair, left, right)
    for metric, samples in values.items():
        result.append({
            "metric": metric,
            "estimate": point[metric],
            "ci_lower": float(np.quantile(samples, 0.025)),
            "ci_upper": float(np.quantile(samples, 0.975)),
            "bootstrap_replicates": replicates,
            "independent_blocks": len(groups),
            "observations": len(pair),
        })
    return result


def cluster_spearman_ci(
    frame: pd.DataFrame,
    left: str,
    right: str,
    cluster: str,
    replicates: int,
    seed: int,
) -> dict[str, Any]:
    data = frame[[cluster, left, right]].copy()
    data[[left, right]] = data[[left, right]].apply(pd.to_numeric, errors="coerce")
    data = data[np.isfinite(data[left]) & np.isfinite(data[right])]
    groups = [group[[left, right]].to_numpy(float) for _, group in data.groupby(cluster)]
    rng = np.random.default_rng(seed)
    samples: list[float] = []
    for _ in range(replicates):
        sample = np.concatenate([groups[index] for index in rng.integers(0, len(groups), len(groups))])
        samples.append(float(stats.spearmanr(sample[:, 0], sample[:, 1]).statistic))
    return {
        "metric": "spearman",
        "estimate": float(stats.spearmanr(data[left], data[right]).statistic),
        "ci_lower": float(np.quantile(samples, 0.025)),
        "ci_upper": float(np.quantile(samples, 0.975)),
        "bootstrap_replicates": replicates,
        "independent_blocks": len(groups),
        "observations": len(data),
    }


def read_scope_matched_ren_series(database: Path) -> pd.DataFrame:
    """Read REN series needed for like-for-like external validation.

    The solved cases intentionally use ``Consumption + Storage`` as electrical
    load.  E-REDES national consumption, however, is comparable to REN
    ``Consumption`` rather than to pumping and battery charging.  Keep both
    quantities so the figure exposes this accounting boundary instead of
    hiding it in an aggregate error metric.
    """
    connection = duckdb.connect(str(database), read_only=True)
    try:
        frame = connection.execute("""
            SELECT CAST(calendar.timestamp_utc AS TIMESTAMPTZ) AS timestamp_utc,
                   max(dispatch.value_mw) FILTER (
                       WHERE dispatch.series_name = 'Consumption'
                   ) AS ren_consumption_mw,
                   max(dispatch.value_mw) FILTER (
                       WHERE dispatch.series_name = 'Pumping'
                   ) AS ren_pumping_mw,
                   max(dispatch.value_mw) FILTER (
                       WHERE dispatch.series_name = 'Consumption of Batteries'
                   ) AS ren_battery_consumption_mw,
                   max(dispatch.value_mw) FILTER (
                       WHERE dispatch.series_name = 'Wind'
                   ) AS ren_wind_mw,
                   max(dispatch.value_mw) FILTER (
                       WHERE dispatch.series_name = 'Solar'
                   ) AS ren_solar_mw,
                   max(dispatch.value_mw) FILTER (
                       WHERE dispatch.series_name = 'Hydro'
                   ) AS ren_hydro_mw
            FROM main.interval_calendar AS calendar
            JOIN main.ren_dispatch AS dispatch
              ON dispatch.source_date = calendar.local_date
             AND dispatch.source_index = calendar.source_index
            GROUP BY calendar.timestamp_utc
            ORDER BY timestamp_utc
        """).fetchdf()
    finally:
        connection.close()
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
    if frame["timestamp_utc"].duplicated().any():
        raise ValueError("REN scope-matched series contain duplicate timestamps")
    return frame


def run_statistical_experiment(
    database: Path, external: Path, output: Path, replicates: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    timeseries = pd.read_csv(external / "timeseries_15min.csv", low_memory=False)
    timeseries["timestamp_utc"] = pd.to_datetime(timeseries["timestamp_utc"], utc=True)
    ren = read_scope_matched_ren_series(database)
    timeseries = timeseries.merge(ren, on="timestamp_utc", how="left", validate="one_to_one")
    timeseries["ren_storage_load_mw"] = (
        timeseries["ren_pumping_mw"] + timeseries["ren_battery_consumption_mw"]
    )
    reconstruction_error = (
        timeseries["ren_consumption_mw"]
        + timeseries["ren_storage_load_mw"]
        - timeseries["observed_load_mw"]
    ).abs().max()
    if not np.isfinite(reconstruction_error) or reconstruction_error > 1e-6:
        raise ValueError(
            "REN load components do not reconstruct solved-case load: "
            f"max error={reconstruction_error} MW"
        )
    timeseries["month"] = timeseries["timestamp_utc"].dt.tz_convert("Europe/Lisbon").dt.strftime("%Y-%m")
    dataframe_csv(timeseries, output / "scope_matched_validation_timeseries.csv")
    monthly_rows: list[dict[str, Any]] = []
    for month, group in timeseries.groupby("month", sort=True):
        for domain, left, right in [
            ("LOAD_CONSUMPTION", "ren_consumption_mw", "external_load_mw"),
            ("LOAD_INCLUDING_STORAGE", "observed_load_mw", "external_load_mw"),
            ("WIND", "ren_wind_mw", "external_wind_mw"),
            ("SOLAR", "ren_solar_mw", "external_solar_mw"),
            ("HYDRO", "ren_hydro_mw", "external_hydro_mw"),
            (
                "GENERATION_TOTAL_SCOPE_MISMATCH",
                "observed_generation_total_mw",
                "external_production_mw",
            ),
        ]:
            monthly_rows.append({"month": month, "domain": domain, **pair_metrics(group, left, right)})
    monthly = pd.DataFrame(monthly_rows)
    cis: list[dict[str, Any]] = []
    for index, (domain, left, right) in enumerate([
        ("LOAD_CONSUMPTION", "ren_consumption_mw", "external_load_mw"),
        ("LOAD_INCLUDING_STORAGE", "observed_load_mw", "external_load_mw"),
        ("WIND", "ren_wind_mw", "external_wind_mw"),
        ("SOLAR", "ren_solar_mw", "external_solar_mw"),
        ("HYDRO", "ren_hydro_mw", "external_hydro_mw"),
    ]):
        rows = block_bootstrap_ci(timeseries, left, right, "local_date", replicates, SEED + index)
        cis.extend({"domain": domain, "resampling_unit": "LOCAL_DAY", **row} for row in rows)

    municipality = pd.read_csv(external / "municipality_comparison.csv")
    municipality = municipality[municipality["matched"].fillna(False)].copy()
    municipal_ci = cluster_spearman_ci(
        municipality,
        "pt60_share_within_matched",
        "billed_share_within_matched",
        "municipality_code",
        replicates,
        SEED + 10,
    )
    cis.append({"domain": "MUNICIPAL_SPATIAL", "resampling_unit": "MUNICIPALITY", **municipal_ci})

    substation = pd.read_csv(external / "substation_comparison.csv")
    substation = substation[substation["matched"].fillna(False)].copy()
    substation_ci = cluster_spearman_ci(
        substation,
        "pt60_observed_peak_mw",
        "reference_natural_load_mw",
        "profile_code",
        replicates,
        SEED + 11,
    )
    cis.append({"domain": "SUBSTATION_SPATIAL", "resampling_unit": "SUBSTATION", **substation_ci})
    ci_frame = pd.DataFrame(cis)
    dataframe_csv(monthly, output / "monthly_external_validation.csv")
    dataframe_csv(ci_frame, output / "validation_confidence_intervals.csv")
    return timeseries, monthly, ci_frame


def configure_plotting() -> None:
    plt.rcParams.update({
        "figure.dpi": 130,
        "savefig.dpi": 400,
        "font.size": 8,
        "axes.labelsize": 8,
        "xtick.labelsize": 7,
        "ytick.labelsize": 7,
        "legend.fontsize": 7,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
    })


def save_figure(fig: plt.Figure, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".pdf"), bbox_inches="tight")
    fig.savefig(stem.with_suffix(".png"), bbox_inches="tight", dpi=400)
    plt.close(fig)


def create_figures(
    coverage: pd.DataFrame,
    evidence: pd.DataFrame,
    sensitivity: pd.DataFrame,
    validation_timeseries: pd.DataFrame,
    monthly: pd.DataFrame,
    output: Path,
) -> list[Path]:
    configure_plotting()
    figures = output / "figures"
    generated: list[Path] = []

    fig, axes = plt.subplots(1, 2, figsize=(7.0, 2.8))
    x = np.arange(len(coverage))
    axes[0].bar(x - 0.18, coverage["route_km"], width=0.36, color="#0072B2", label="SimPT60 route-km")
    axes[0].bar(x + 0.18, coverage["comparison_reference_km"], width=0.36, color="#E69F00", hatch="//", label="Public reference")
    axes[0].set_xticks(x, coverage["voltage_kv"].astype(str))
    axes[0].set_xlabel("Nominal voltage (kV)")
    axes[0].set_ylabel("Represented length (km)")
    axes[0].legend(frameon=False)
    axes[0].text(-0.12, 1.03, "(a)", transform=axes[0].transAxes, fontweight="bold")
    proxy = evidence[evidence["field"].eq("parameter_status")].copy()
    pivot = proxy.pivot_table(index="voltage_kv", columns="status", values="fraction", aggfunc="sum", fill_value=0).reindex(VOLTAGES).fillna(0)
    bottom = np.zeros(len(pivot))
    colors = ["#0072B2", "#D55E00", "#009E73", "#CC79A7", "#56B4E9"]
    evidence_labels = {
        "PDIRD_CIRCUIT_PATH_MATCH_PARTIAL_SOURCE_BACKING": "Partial PDIRT support",
        "VOLTAGE_CLASS_ENGINEERING_PROXY": "Voltage-class proxy",
    }
    for color, column in zip(colors, pivot.columns):
        axes[1].bar(
            np.arange(len(pivot)), pivot[column], bottom=bottom, color=color,
            label=evidence_labels.get(str(column), str(column).replace("_", " ").title()),
        )
        bottom += pivot[column].to_numpy(float)
    axes[1].set_xticks(np.arange(len(pivot)), pivot.index.astype(int).astype(str))
    axes[1].set_ylim(0, 1)
    axes[1].set_xlabel("Nominal voltage (kV)")
    axes[1].set_ylabel("Fraction of lines")
    axes[1].legend(frameon=False, fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1))
    axes[1].text(-0.12, 1.03, "(b)", transform=axes[1].transAxes, fontweight="bold")
    fig.tight_layout()
    stem = figures / "fig09_topology_and_parameter_evidence"
    save_figure(fig, stem)
    generated.extend([stem.with_suffix(".pdf"), stem.with_suffix(".png")])

    summary = sensitivity.copy().sort_values(["experiment", "variant"])
    labels = summary["variant"].str.replace("PARAM_", "", regex=False).str.replace("SPATIAL_", "", regex=False).str.replace("_", " ")
    fig, axes = plt.subplots(1, 3, figsize=(7.0, 2.9))
    y = np.arange(len(summary))
    group_colors = np.where(summary["experiment"].eq("PARAMETER"), "#0072B2", "#D55E00")
    axes[0].scatter(summary["line_rank_spearman_median"], y, c=group_colors, marker="o", s=24)
    axes[0].hlines(y, summary["line_rank_spearman_min"], summary["line_rank_spearman_median"], colors=group_colors, lw=1)
    axes[0].set_xlabel("Line-loading rank correlation")
    axes[0].set_yticks(y, labels)
    axes[0].set_xlim(0.95, 1.001)
    axes[1].scatter(summary["top20_jaccard_median"], y, c=group_colors, marker="s", s=24)
    axes[1].hlines(y, summary["top20_jaccard_min"], summary["top20_jaccard_median"], colors=group_colors, lw=1)
    axes[1].set_xlabel("Top-20 line Jaccard")
    axes[1].set_yticks(y, [])
    axes[1].set_xlim(0, 1.01)
    axes[2].scatter(summary["max_abs_delta_max_line_loading_pp"], y, c=group_colors, marker="^", s=24)
    axes[2].set_xlabel("Maximum absolute change\nin peak line loading (pp)")
    axes[2].set_yticks(y, [])
    axes[2].scatter([], [], color="#0072B2", marker="^", label="Parameter")
    axes[2].scatter([], [], color="#D55E00", marker="^", label="Spatial allocation")
    axes[2].legend(frameon=False, loc="lower right", fontsize=6)
    for label, axis in zip(["(a)", "(b)", "(c)"], axes):
        axis.text(-0.14, 1.03, label, transform=axis.transAxes, fontweight="bold")
        axis.grid(axis="x", color="0.9", linewidth=0.5)
    fig.tight_layout()
    stem = figures / "fig10_parameter_and_spatial_sensitivity"
    save_figure(fig, stem)
    generated.extend([stem.with_suffix(".pdf"), stem.with_suffix(".png")])

    comparison = validation_timeseries.copy()
    comparison["local_day"] = comparison["timestamp_utc"].dt.tz_convert("Europe/Lisbon").dt.date
    daily = comparison.groupby("local_day", as_index=False).agg(
        simpt60_load_mw=("ren_consumption_mw", "mean"),
        public_load_mw=("external_load_mw", "mean"),
        simpt60_wind_mw=("ren_wind_mw", "mean"),
        public_wind_mw=("external_wind_mw", "mean"),
    )
    daily["local_day"] = pd.to_datetime(daily["local_day"])
    load_pair = pair_metrics(comparison, "ren_consumption_mw", "external_load_mw")
    wind_pair = pair_metrics(comparison, "ren_wind_mw", "external_wind_mw")

    fig, axes = plt.subplots(2, 2, figsize=(7.2, 4.8))
    time_panels = [
        (axes[0, 0], "simpt60_load_mw", "public_load_mw", "Daily mean load (MW)"),
        (axes[0, 1], "simpt60_wind_mw", "public_wind_mw", "Daily mean wind generation (MW)"),
    ]
    for axis, simpt60_column, public_column, ylabel in time_panels:
        axis.plot(
            daily["local_day"], daily[simpt60_column], color="#0072B2",
            linewidth=1.35, label="SimPT60 input",
        )
        axis.plot(
            daily["local_day"], daily[public_column], color="#D55E00",
            linewidth=1.05, linestyle="--", label="E-REDES public reference",
        )
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", color="0.9", linewidth=0.5)
        axis.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
        axis.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        axis.tick_params(axis="x", rotation=30)
    axes[0, 0].legend(frameon=False, ncol=2, fontsize=6.5, loc="upper left")

    scatter_panels = [
        (
            axes[1, 0], "external_load_mw", "ren_consumption_mw",
            "E-REDES public load (MW)", "SimPT60 load input (MW)", load_pair,
        ),
        (
            axes[1, 1], "external_wind_mw", "ren_wind_mw",
            "E-REDES public wind (MW)", "SimPT60 wind input (MW)", wind_pair,
        ),
    ]
    for axis, x_column, y_column, xlabel, ylabel, metrics in scatter_panels:
        pairs = finite_pair(comparison, y_column, x_column)
        axis.scatter(
            pairs[x_column], pairs[y_column], s=4, alpha=0.08,
            color="#0072B2", edgecolors="none", rasterized=True,
        )
        lower = min(float(pairs[x_column].min()), float(pairs[y_column].min()))
        upper = max(float(pairs[x_column].max()), float(pairs[y_column].max()))
        axis.plot([lower, upper], [lower, upper], color="0.2", linestyle="--", linewidth=0.9)
        axis.set_xlim(lower, upper)
        axis.set_ylim(lower, upper)
        axis.set_xlabel(xlabel)
        axis.set_ylabel(ylabel)
        axis.set_aspect("equal", adjustable="box")
        axis.grid(color="0.92", linewidth=0.45)
        axis.text(
            0.04, 0.95,
            f"n = {int(metrics['n']):,}\nr = {metrics['pearson']:.3f}\nmean ratio = {metrics['mean_ratio']:.3f}",
            transform=axis.transAxes, va="top", fontsize=7,
            bbox={"facecolor": "white", "edgecolor": "0.8", "boxstyle": "round,pad=0.25"},
        )
    for label, axis in zip(["(a)", "(b)", "(c)", "(d)"], axes.flat):
        axis.text(-0.14, 1.04, label, transform=axis.transAxes, fontweight="bold")
    fig.tight_layout()
    stem = figures / "fig14_public_vs_simpt60_comparison"
    save_figure(fig, stem)
    generated.extend([stem.with_suffix(".pdf"), stem.with_suffix(".png")])
    return generated


def write_report(
    output: Path,
    topology: dict[str, Any],
    coverage: pd.DataFrame,
    sensitivity: pd.DataFrame,
    monthly: pd.DataFrame,
    ci: pd.DataFrame,
    payloads: list[dict[str, Any]],
) -> None:
    failed = [row for row in payloads if row.get("status") != "COMPLETE"]
    lines = [
        "# SimPT60 four-experiment validation report",
        "",
        f"Generated: {utc_now()}",
        "",
        "## Experiment 1 — Full-network topology and evidence",
        "",
        f"- Buses: {topology['buses']:,}; active lines: {topology['active_lines']:,}; transformers: {topology['transformers']:,}.",
        f"- Active connected components: {topology['connected_components']}; largest-component coverage: {topology['largest_component_fraction']:.3%}.",
        f"- Simple-graph cycle rank: {topology['cycle_rank_simple_graph']:,}; median/p95 degree: {topology['degree_median']:.1f}/{topology['degree_p95']:.1f}.",
        "- The coverage comparison is contextual: 150/220/400 kV use REN totals; 60/130 kV use OSM context and are not independent validation.",
        "",
        "## Experiments 2–3 — Sensitivity",
        "",
        f"- Completed sensitivity cases: {len(payloads) - len(failed):,}/{len(payloads):,}; failed: {len(failed)}.",
    ]
    for row in sensitivity.to_dict("records"):
        lines.append(
            f"- `{row['variant']}` ({int(row['cases'])} states): convergence {row['convergence_rate']:.1%}; "
            f"median/min rank rho {row['line_rank_spearman_median']:.3f}/{row['line_rank_spearman_min']:.3f}; "
            f"median/min top-20 Jaccard {row['top20_jaccard_median']:.3f}/{row['top20_jaccard_min']:.3f}; "
            f"maximum peak-loading change {row['max_abs_delta_max_line_loading_pp']:.2f} pp."
        )
    lines.extend([
        "",
        "## Experiment 4 — External validation stability",
        "",
        "Monthly results are in `monthly_external_validation.csv`. Confidence intervals use local days as temporal blocks, municipalities as municipal clusters, and substations as substation clusters.",
        "",
        "| Domain | Metric | Estimate | 95% CI | Blocks |",
        "|---|---|---:|---:|---:|",
    ])
    for row in ci.to_dict("records"):
        lines.append(
            f"| {row['domain']} | {row['metric']} | {row['estimate']:.3f} | "
            f"[{row['ci_lower']:.3f}, {row['ci_upper']:.3f}] | {int(row['independent_blocks'])} |"
        )
    lines.extend([
        "",
        "## Interpretation boundary",
        "",
        "These experiments quantify structural consistency, public-source coverage, numerical robustness, and aggregate external agreement. They do not estimate device-level topology or branch-flow accuracy against an operator state estimator.",
        "",
    ])
    (output / "validation_report.md").write_text("\n".join(lines), encoding="utf-8")


def write_manifest(output: Path, figures: Iterable[Path]) -> None:
    rows = []
    source_map = {
        "fig09": [output / "topology_coverage_by_voltage.csv", output / "parameter_evidence_by_voltage.csv"],
        "fig10": [output / "sensitivity_summary.csv", output / "sensitivity_case_results.csv"],
        "fig14": [
            output / "monthly_external_validation.csv",
            output / "validation_confidence_intervals.csv",
            output / "scope_matched_validation_timeseries.csv",
        ],
    }
    claims = {
        "fig09": "Public network coverage is quantified by voltage while parameter evidence remains predominantly proxy-based.",
        "fig10": "Electrical and spatial assumptions have measurable effects whose impact on line-risk rankings is quantified.",
        "fig14": "Scope-matched SimPT60 load and wind inputs closely follow E-REDES public observations in daily trajectories and paired 15-minute values.",
    }
    for path in figures:
        if path.suffix != ".pdf":
            continue
        figure_id = path.stem.split("_")[0]
        sources = source_map[figure_id]
        rows.append({
            "figure_id": figure_id,
            "panel_id": "all",
            "claim": claims[figure_id],
            "source_data": ";".join(str(p) for p in sources),
            "source_sha256": ";".join(sha256(p) for p in sources),
            "generator": str(Path(__file__)),
            "output_file": str(path),
            "caption": "Generated deterministically from the four-experiment SimPT60 validation suite.",
            "uncertainty": "Block/cluster bootstrap is reported for external validation; deterministic sensitivity states have no sampling error bars.",
            "license_status": "Derived project artifacts; upstream source terms remain applicable.",
            "status": "validated",
            "notes": "No smoothing or manual value transcription.",
        })
    dataframe_csv(pd.DataFrame(rows), output / "figure_manifest.csv")


def main() -> None:
    args = parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not args.database.exists():
        raise FileNotFoundError(args.database)
    if not args.external_validation_dir.exists():
        raise FileNotFoundError(args.external_validation_dir)
    started = time.monotonic()
    print("Experiment 1/4: topology and evidence")
    coverage, evidence, topology = run_topology_experiment(args.database, output)
    intervals = selected_intervals(args.database, args.max_timestamps)
    dataframe_csv(pd.DataFrame(intervals), output / "selected_operating_states.csv")
    models = build_model_variants(output)
    tasks = experiment_tasks(intervals, models, output)
    payloads: list[dict[str, Any]] = []
    if not args.skip_modeling:
        print(f"Experiments 2–3/4: {len(intervals)} states, {len(tasks)} total solves")
        payloads = run_modeling(tasks, args.database, max(1, args.workers))
    else:
        for task in tasks:
            if task_path(task).exists():
                payloads.append(json.loads(task_path(task).read_text(encoding="utf-8")))
    expected = len(tasks)
    if len(payloads) != expected:
        raise RuntimeError(f"Sensitivity artifacts incomplete: {len(payloads)}/{expected}")
    sensitivity_cases, sensitivity_summary = analyze_sensitivity(payloads, output)
    print("Experiment 4/4: monthly diagnostics and bootstrap confidence intervals")
    validation_timeseries, monthly, ci = run_statistical_experiment(
        args.database, args.external_validation_dir, output, args.bootstrap_replicates
    )
    figures = create_figures(
        coverage, evidence, sensitivity_summary, validation_timeseries, monthly, output
    )
    write_manifest(output, figures)
    write_report(output, topology, coverage, sensitivity_summary, monthly, ci, payloads)
    manifest = {
        "generated_at": utc_now(),
        "database": str(args.database),
        "database_sha256": sha256(args.database),
        "model_input": str(MODEL_INPUT),
        "model_sha256": sha256(MODEL_INPUT),
        "selected_operating_states": len(intervals),
        "sensitivity_tasks": expected,
        "complete_tasks": sum(row.get("status") == "COMPLETE" for row in payloads),
        "failed_tasks": sum(row.get("status") != "COMPLETE" for row in payloads),
        "bootstrap_replicates": args.bootstrap_replicates,
        "seed": SEED,
        "workers": args.workers,
        "execution_mode": "REUSE_EXISTING_CASES" if args.skip_modeling else "SOLVE_AND_ANALYZE",
        "sum_case_solver_elapsed_seconds": sum(float(row.get("elapsed_seconds", 0.0)) for row in payloads),
        "analysis_run_wall_time_seconds": time.monotonic() - started,
    }
    write_json(output / "run_manifest.json", manifest)
    print(f"COMPLETE output={output} wall_time={manifest['analysis_run_wall_time_seconds']:.1f}s")


if __name__ == "__main__":
    main()
