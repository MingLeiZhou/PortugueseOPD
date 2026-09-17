#!/usr/bin/env python3
"""Prefetch and solve every 15-minute PT60 snapshot in an inclusive date range."""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from common import PROJECT, read_json, utc_now, write_json
from pt60_public_model import INTERCONNECTOR_COMMISSIONING
from temporal_runner_common import configure_logging, format_duration, inclusive_dates
from run_temporal_validation import (
    EREDES_API_BASE,
    EREDES_LOAD_PARTITIONS,
    GENERATOR_INPUT,
    PUBLIC_DATA_SESSION,
    RAW_EREDES,
    RAW_REN,
    RAW_WEATHER,
    SOURCE_ZONE,
    WEATHER_POINTS,
    ren_observation,
    ren_rnt_loss_benchmark,
    run_case,
    weather_observation,
)
from time_alignment import eredes_source_label, source_time_metadata


DEFAULT_OUTPUT = PROJECT / "outputs" / "weekly_15min"


def interval_schedule(start_date: str, end_date: str) -> list[dict[str, Any]]:
    """Return every local 15-minute interval start, including DST day lengths."""
    rows: list[dict[str, Any]] = []
    for local_date in inclusive_dates(start_date, end_date):
        start = pd.Timestamp(local_date).tz_localize(SOURCE_ZONE)
        end = start + pd.DateOffset(days=1)
        for source_index, local_timestamp in enumerate(
            pd.date_range(start, end, freq="15min", inclusive="left")
        ):
            utc_timestamp = local_timestamp.tz_convert("UTC")
            rows.append({
                "local_date": local_date,
                "source_index": source_index,
                "timestamp_local": local_timestamp.isoformat(),
                "timestamp_utc": utc_timestamp.isoformat(),
                "case_id": f"PT60_15MIN_{local_timestamp.strftime('%Y%m%d_%H%M_%z')}",
            })
    return rows


def _month_settings(local_date: str) -> tuple[float, str]:
    month = pd.Timestamp(local_date).month
    factor = 1.15 if month in {12, 1, 2} else 1.10 if month in {3, 4, 10, 11} else 1.0
    season = "SUMMER" if 5 <= month <= 9 else "WINTER"
    return factor, season


def _case_from_interval(row: dict[str, Any], output: Path) -> dict[str, Any]:
    timestamp = pd.Timestamp(row["timestamp_utc"])
    local_date = pd.Timestamp(row["local_date"])
    factor, season = _month_settings(row["local_date"])
    interconnector = bool(timestamp >= INTERCONNECTOR_COMMISSIONING)
    return {
        "case_id": row["case_id"],
        "timestamp_utc": timestamp.isoformat(),
        "load_profile_timestamp_utc": timestamp.isoformat(),
        "load_profile_mode": "SYNCHRONIZED_EREDES",
        "rating_mode": "FULL_15MIN_SEASONAL_STATIC_PROXY",
        "rating_factor_relative_to_static_summer": factor,
        "pdirt_reference_season": season,
        "new_interconnector_in_service": interconnector,
        "external_boundary_buses": 8 if interconnector else 7,
        "physical_cross_border_circuits": 10 if interconnector else 9,
        "rnt_loss_benchmark_date": str((local_date + pd.offsets.MonthEnd(0)).date()),
        "installed_capacity_benchmark": local_date.strftime("%Y-%m"),
        "analysis_role": "FULL_15MIN_CONTINUOUS_WEEK_STEADY_STATE",
        "skip_optional_external_acquisition": True,
        "spatial_result_path": str(output / "device_results" / f"{row['case_id']}.npz"),
    }


def _eredes_cache(timestamp: pd.Timestamp) -> Path:
    return RAW_EREDES / "aligned_v2" / f"load_{timestamp.strftime('%Y-%m-%d_%H%M')}.json"


def _bulk_date_range(schedule: list[dict[str, Any]]) -> tuple[str, str]:
    source_dates = [eredes_source_label(pd.Timestamp(row["timestamp_utc"]))[0] for row in schedule]
    return min(source_dates), max(source_dates)


def _bulk_cache_path(dataset: str, schedule: list[dict[str, Any]]) -> Path:
    start, end = _bulk_date_range(schedule)
    return RAW_EREDES / "bulk_15min" / f"{dataset}_{start}_{end}.parquet"


def _download_bulk_partition(
    dataset: str, schedule: list[dict[str, Any]], refresh: bool,
    rate_limit_backoff: float, logger: logging.Logger,
) -> tuple[Path, bool]:
    cache = _bulk_cache_path(dataset, schedule)
    if cache.exists() and not refresh:
        return cache, True
    start, end = _bulk_date_range(schedule)
    url = f"{EREDES_API_BASE}/{dataset}/exports/parquet"
    params = {
        "where": f"data >= date'{start}' and data <= date'{end}'",
        "timezone": SOURCE_ZONE,
        "parquet_compression": "zstd",
    }
    for attempt in range(1, 5):
        try:
            response = PUBLIC_DATA_SESSION.get(
                url, params=params,
                headers={"User-Agent": "PT60 weekly bulk prefetch/0.1"}, timeout=600,
            )
            response.raise_for_status()
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(response.content)
            return cache, False
        except requests.RequestException as exc:
            if attempt == 4:
                raise
            cooldown = rate_limit_backoff * attempt
            logger.warning(
                "[BULK %s] request failed (%s); cooldown %s before retry %d/4",
                dataset, type(exc).__name__, format_duration(cooldown), attempt + 1,
            )
            time.sleep(cooldown)
    raise AssertionError("unreachable")


def _prepare_eredes_snapshot_caches(
    schedule: list[dict[str, Any]], refresh: bool, request_delay: float,
    rate_limit_backoff: float, logger: logging.Logger,
) -> dict[str, Any]:
    frames: list[pd.DataFrame] = []
    bulk_files: list[Path] = []
    bulk_cache_hits = 0
    logger.info(
        "Downloading E-REDES as %d weekly partition exports instead of per-snapshot API calls",
        len(EREDES_LOAD_PARTITIONS),
    )
    for position, dataset in enumerate(EREDES_LOAD_PARTITIONS, start=1):
        path, existed = _download_bulk_partition(
            dataset, schedule, refresh, rate_limit_backoff, logger
        )
        bulk_files.append(path)
        bulk_cache_hits += int(existed)
        frame = pd.read_parquet(path)
        required = {"data", "hora", "codigo_subestacao", "subestacao", "energia"}
        missing = required - set(frame.columns)
        if missing:
            raise ValueError(f"Bulk E-REDES export {dataset} missing columns: {sorted(missing)}")
        frame["dataset_partition"] = dataset
        frame["_source_date"] = pd.to_datetime(frame["data"], errors="raise").dt.strftime("%Y-%m-%d")
        frame["_source_hour"] = frame["hora"].astype(str).str.slice(0, 5)
        frames.append(frame)
        logger.info(
            "[BULK %d/%d] %s rows=%d size=%s source=%s",
            position, len(EREDES_LOAD_PARTITIONS), dataset, len(frame),
            _format_bytes(path.stat().st_size), "CACHE" if existed else "DOWNLOAD",
        )
        if not existed and request_delay > 0:
            time.sleep(request_delay)

    combined = pd.concat(frames, ignore_index=True)
    groups = {
        key: group.drop(columns=["_source_date", "_source_hour"])
        for key, group in combined.groupby(["_source_date", "_source_hour"], sort=False)
    }
    snapshot_hits = 0
    snapshot_prepared = 0
    phase_started = time.monotonic()
    for position, row in enumerate(schedule, start=1):
        timestamp = pd.Timestamp(row["timestamp_utc"])
        cache = _eredes_cache(timestamp)
        existed = cache.exists() and not refresh
        if existed:
            metadata = read_json(cache)["metadata"]
            snapshot_hits += 1
        else:
            source_key = eredes_source_label(timestamp)
            snapshot = groups.get(source_key)
            if snapshot is None or snapshot.empty:
                raise RuntimeError(f"Bulk E-REDES export has no records for source label {source_key}")
            partition_counts = snapshot["dataset_partition"].value_counts().to_dict()
            missing_partitions = [name for name in EREDES_LOAD_PARTITIONS if partition_counts.get(name, 0) == 0]
            if missing_partitions:
                raise RuntimeError(f"E-REDES source label {source_key} missing partitions: {missing_partitions}")
            metadata = {
                **source_time_metadata(timestamp),
                "source": "E-REDES Open Data weekly bulk exports",
                "timestamp_utc": timestamp.isoformat(),
                "interval_minutes": 15,
                "conversion": "p_mw = energia_kwh / 0.25 h / 1000 = energia_kwh / 250",
                "partition_counts": partition_counts,
                "record_count": len(snapshot),
                "bulk_source_files": [str(path) for path in bulk_files],
            }
            write_json(cache, {
                "metadata": metadata,
                "records": json.loads(snapshot.to_json(orient="records")),
            })
            snapshot_prepared += 1
        elapsed = time.monotonic() - phase_started
        eta = elapsed / position * (len(schedule) - position)
        logger.info(
            "[PREPARE %04d/%04d | %5.1f%%] %s records=%d source=%s ETA=%s",
            position, len(schedule), 100.0 * position / len(schedule), row["timestamp_local"],
            metadata["record_count"], "CACHE" if existed else "BULK",
            format_duration(eta),
        )
    return {
        "bulk_file_count": len(bulk_files),
        "bulk_cache_hits": bulk_cache_hits,
        "bulk_downloads": len(bulk_files) - bulk_cache_hits,
        "bulk_bytes": sum(path.stat().st_size for path in bulk_files),
        "snapshot_cache_hits": snapshot_hits,
        "snapshot_caches_prepared": snapshot_prepared,
    }


def _required_cache_files(schedule: list[dict[str, Any]]) -> list[Path]:
    files: set[Path] = set()
    local_dates = {row["local_date"] for row in schedule}
    utc_dates = {pd.Timestamp(row["timestamp_utc"]).strftime("%Y-%m-%d") for row in schedule}
    benchmark_dates = {
        str((pd.Timestamp(local_date) + pd.offsets.MonthEnd(0)).date())
        for local_date in local_dates
    }
    files.update(RAW_REN / f"dispatch_{local_date}.json" for local_date in local_dates)
    files.update(_eredes_cache(pd.Timestamp(row["timestamp_utc"])) for row in schedule)
    files.update(RAW_REN / f"rnt_balance_{date}.csv" for date in benchmark_dates)
    for utc_date in utc_dates:
        files.update(RAW_WEATHER / f"open_meteo_{name.lower()}_{utc_date}.json" for name in WEATHER_POINTS)
    return sorted(files)


def _tree_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def _format_bytes(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024.0 or unit == "TiB":
            return f"{value:.1f} {unit}"
        value /= 1024.0
    raise AssertionError("unreachable")


def _prefetch(
    schedule: list[dict[str, Any]], refresh: bool, logger: logging.Logger,
    request_delay: float, rate_limit_backoff: float,
) -> dict[str, Any]:
    started = time.monotonic()
    local_dates = list(dict.fromkeys(row["local_date"] for row in schedule))
    logger.info("Prefetch 1/4: REN daily curves (%d days)", len(local_dates))
    for position, local_date in enumerate(local_dates, start=1):
        # Any interval on this local day loads and validates the complete daily curve.
        row = next(item for item in schedule if item["local_date"] == local_date)
        ren_observation(pd.Timestamp(row["timestamp_utc"]), refresh)
        logger.info("[REN %03d/%03d] %s cached", position, len(local_dates), local_date)

    logger.info("Prefetch 2/4: E-REDES weekly bulk exports and local snapshot preparation")
    eredes = _prepare_eredes_snapshot_caches(
        schedule, refresh, request_delay, rate_limit_backoff, logger
    )

    logger.info("Prefetch 3/4: weather context")
    seen_utc_dates: set[str] = set()
    for row in schedule:
        timestamp = pd.Timestamp(row["timestamp_utc"])
        utc_date = timestamp.strftime("%Y-%m-%d")
        if utc_date not in seen_utc_dates:
            weather_observation(timestamp, refresh)
            seen_utc_dates.add(utc_date)
            logger.info("[WEATHER %02d] %s cached", len(seen_utc_dates), utc_date)

    logger.info("Prefetch 4/4: REN monthly RNT loss benchmarks")
    benchmark_dates = sorted({
        str((pd.Timestamp(row["local_date"]) + pd.offsets.MonthEnd(0)).date())
        for row in schedule
    })
    for benchmark_date in benchmark_dates:
        ren_rnt_loss_benchmark(benchmark_date, refresh)
        logger.info("[RNT] %s cached", benchmark_date)

    required = _required_cache_files(schedule)
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise RuntimeError(f"Prefetch finished with {len(missing)} missing cache files; first: {missing[0]}")
    return {
        "duration_seconds": time.monotonic() - started,
        **eredes,
        "required_cache_file_count": len(required),
        "required_cache_bytes": sum(path.stat().st_size for path in required),
    }


def _offline_inputs(row: dict[str, Any], case: dict[str, Any]) -> tuple[dict, pd.DataFrame, dict, dict]:
    timestamp = pd.Timestamp(row["timestamp_utc"])
    required = _required_cache_files([row])
    missing = [path for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Offline solve requires prefetched cache file: {missing[0]}")
    observation = ren_observation(timestamp, False)
    payload = read_json(_eredes_cache(timestamp))
    snapshot = pd.DataFrame(payload["records"])
    weather = weather_observation(timestamp, False)
    rnt_loss = ren_rnt_loss_benchmark(case["rnt_loss_benchmark_date"], False)
    return observation, snapshot, weather, rnt_loss


def _load_results(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {str(row["case_id"]): row for row in pd.read_csv(path, low_memory=False).to_dict("records")}


def _save_results(path: Path, rows: dict[str, dict[str, Any]]) -> None:
    frame = pd.DataFrame(rows.values())
    if not frame.empty:
        frame = frame.sort_values("timestamp_utc")
    frame.to_csv(path, index=False)


def _solve(
    schedule: list[dict[str, Any]], output: Path, logger: logging.Logger,
) -> dict[str, Any]:
    started = time.monotonic()
    result_path = output / "case_results.csv"
    rows = _load_results(result_path)
    generators = pd.read_csv(GENERATOR_INPUT, low_memory=False)
    pending = [row for row in schedule if rows.get(row["case_id"], {}).get("status") != "COMPLETE"]
    logger.info(
        "Offline solve plan: %d total, %d reusable, %d pending",
        len(schedule), len(schedule) - len(pending), len(pending),
    )
    run_elapsed = 0.0
    processed = 0
    for position, interval in enumerate(schedule, start=1):
        existing = rows.get(interval["case_id"])
        if existing and existing.get("status") == "COMPLETE":
            logger.info("[SOLVE %04d/%04d | %5.1f%%] SKIP %s", position, len(schedule), 100.0 * position / len(schedule), interval["case_id"])
            continue
        case = _case_from_interval(interval, output)
        case_started = time.monotonic()
        logger.info(
            "[SOLVE %04d/%04d | %5.1f%%] START %s local=%s",
            position, len(schedule), 100.0 * position / len(schedule), case["case_id"], interval["timestamp_local"],
        )
        try:
            observation, snapshot, weather, rnt_loss = _offline_inputs(interval, case)
            input_path = output / "inputs" / f"{case['case_id']}.json"
            write_json(input_path, {
                "case": case,
                "observation": observation,
                "load_metadata": read_json(_eredes_cache(pd.Timestamp(interval["timestamp_utc"])))["metadata"],
                "snapshot": json.loads(snapshot.to_json(orient="records")),
            })
            result, *_ = run_case(
                case,
                generators,
                False,
                save_solved=False,
                observation_override=observation,
                load_snapshot_override=snapshot,
                weather_override=weather,
                rnt_loss_override=rnt_loss,
                output_dir=output,
            )
            elapsed = time.monotonic() - case_started
            row = {
                **interval,
                "status": "COMPLETE",
                "converged": result["converged"],
                "elapsed_seconds": elapsed,
                "observed_load_mw": result["observed_load_mw"],
                "observed_generation_total_mw": result["observed_generation_total_mw"],
                "observed_net_import_mw": result["observed_net_import_mw"],
                "model_net_import_mw": result["model_net_import_mw"],
                "vm_pu_min": result["vm_pu_min"],
                "vm_pu_max": result["vm_pu_max"],
                "maximum_line_loading_percent": result["maximum_line_loading_percent"],
                "lines_over_100_percent": result["lines_over_100_percent"],
                "maximum_transformer_loading_percent": result["maximum_transformer_loading_percent"],
                "model_losses_percent_of_load": result["model_losses_percent_of_load"],
                "unmapped_generation_residual_mw": result["unmapped_generation_residual_mw"],
                "eredes_profile_fraction_of_national_load": result["eredes_profile_fraction_of_national_load"],
                "error": "",
            }
        except Exception as exc:
            elapsed = time.monotonic() - case_started
            row = {
                **interval,
                "status": "FAILED",
                "converged": False,
                "elapsed_seconds": elapsed,
                "error": f"{type(exc).__name__}: {exc}",
            }
            logger.exception("[SOLVE %04d/%04d] FAILED %s", position, len(schedule), case["case_id"])
        rows[case["case_id"]] = row
        _save_results(result_path, rows)
        processed += 1
        run_elapsed += elapsed
        eta = run_elapsed / processed * max(0, len(pending) - processed)
        logger.info(
            "[SOLVE %04d/%04d | %5.1f%%] %s %s elapsed=%s ETA=%s",
            position, len(schedule), 100.0 * position / len(schedule), row["status"], case["case_id"],
            format_duration(elapsed), format_duration(eta),
        )
    frame = pd.read_csv(result_path, low_memory=False)
    requested = frame[frame["case_id"].isin({row["case_id"] for row in schedule})]
    complete = requested[requested["status"].eq("COMPLETE")]
    failures = requested[~requested["status"].eq("COMPLETE")]
    failures.to_csv(output / "failures.csv", index=False)
    return {
        "duration_seconds": time.monotonic() - started,
        "completed_case_count": int(len(complete)),
        "failed_case_count": int(len(failures)),
        "converged_case_count": int(complete["converged"].fillna(False).astype(bool).sum()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--phase", choices=("all", "prefetch", "solve"), default="all")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument(
        "--request-delay", type=float, default=1.0,
        help="Seconds to pause after each newly downloaded E-REDES bulk partition (default: 1.0)",
    )
    parser.add_argument(
        "--rate-limit-backoff", type=float, default=60.0,
        help="Initial cooldown in seconds after an E-REDES request failure (default: 60)",
    )
    args = parser.parse_args()
    if args.request_delay < 0 or args.rate_limit_backoff < 0:
        parser.error("request delays must be non-negative")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    logger, log_path = configure_logging(args.output_dir, "pt60.weekly_15min")
    schedule = interval_schedule(args.start_date, args.end_date)
    pd.DataFrame(schedule).to_csv(args.output_dir / "interval_schedule.csv", index=False)
    logger.info(
        "PT60 full 15-minute run: %s through %s, %d intervals, phase=%s",
        args.start_date, args.end_date, len(schedule), args.phase,
    )

    summary_path = args.output_dir / "timing_storage_summary.json"
    summary = read_json(summary_path) if summary_path.exists() else {}
    summary.update({
        "updated_at": utc_now(),
        "start_date": args.start_date,
        "end_date": args.end_date,
        "expected_case_count": len(schedule),
        "execution_log": log_path.name,
    })
    if args.phase in {"all", "prefetch"}:
        logger.info("PHASE A: public-input prefetch started")
        summary["prefetch"] = _prefetch(
            schedule, args.refresh, logger, args.request_delay, args.rate_limit_backoff
        )
        logger.info(
            "PHASE A complete: %s, cache=%s across %d files",
            format_duration(summary["prefetch"]["duration_seconds"]),
            _format_bytes(summary["prefetch"]["required_cache_bytes"]),
            summary["prefetch"]["required_cache_file_count"],
        )
        write_json(summary_path, summary)
    if args.phase in {"all", "solve"}:
        logger.info("PHASE B: offline AC modeling started")
        summary["solve"] = _solve(schedule, args.output_dir, logger)
        logger.info(
            "PHASE B complete: %s, complete=%d, converged=%d, failed=%d",
            format_duration(summary["solve"]["duration_seconds"]),
            summary["solve"]["completed_case_count"], summary["solve"]["converged_case_count"],
            summary["solve"]["failed_case_count"],
        )

    required = _required_cache_files(schedule)
    bulk_files = [_bulk_cache_path(dataset, schedule) for dataset in EREDES_LOAD_PARTITIONS]
    required_bytes = sum(path.stat().st_size for path in required if path.exists())
    bulk_bytes = sum(path.stat().st_size for path in bulk_files if path.exists())
    summary["updated_at"] = utc_now()
    summary["storage"] = {
        "required_raw_cache_file_count": sum(path.exists() for path in required),
        "required_raw_cache_bytes": required_bytes,
        "required_raw_cache_human": _format_bytes(required_bytes),
        "bulk_export_file_count": sum(path.exists() for path in bulk_files),
        "bulk_export_bytes": bulk_bytes,
        "bulk_export_human": _format_bytes(bulk_bytes),
        "total_experiment_cache_bytes": required_bytes + bulk_bytes,
        "total_experiment_cache_human": _format_bytes(required_bytes + bulk_bytes),
        "output_bytes": _tree_bytes(args.output_dir),
        "output_human": _format_bytes(_tree_bytes(args.output_dir)),
    }
    write_json(summary_path, summary)
    logger.info(
        "Storage: prepared snapshots/context=%s; bulk exports=%s; run output=%s",
        summary["storage"]["required_raw_cache_human"],
        summary["storage"]["bulk_export_human"], summary["storage"]["output_human"],
    )
    logger.info("Summary: %s", summary_path.resolve())
    if args.phase in {"all", "solve"} and summary.get("solve", {}).get("failed_case_count", 0):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
