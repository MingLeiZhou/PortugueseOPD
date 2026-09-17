#!/usr/bin/env python3
"""Build and store every 15-minute PT60 AC model for one calendar month.

All temporal inputs are read from an existing PT60 DuckDB database.  The
input database is always attached read-only. Results are staged on fast local
storage as compact per-case arrays and may be atomically published to a second
disk only after the complete database has been checkpointed and verified.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
import math
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

import duckdb
import numpy as np
import pandas as pd

from common import PROJECT, utc_now
from pt60_public_model import INTERCONNECTOR_COMMISSIONING
from run_temporal_validation import (
    GENERATION_GROUPS,
    GENERATOR_INPUT,
    MODEL_INPUT,
    SOURCE_ZONE,
    run_case,
)
from temporal_runner_common import configure_logging, format_duration
from time_alignment import eredes_wall_label, source_time_metadata


DEFAULT_OUTPUT_ROOT = PROJECT / "outputs" / "monthly_15min"
STATIC_SCHEMAS = ("grid", "geo", "provenance")
_WORKER_INPUTS: "DatabaseInputs | None" = None
_WORKER_GENERATORS: pd.DataFrame | None = None
_WORKER_OUTPUT_DIR: Path | None = None
_WORKER_TEMPORARY_DIR: Path | None = None
_PARENT_DEVICE_ORDER: tuple[list[str], list[str]] | None = None


def month_bounds(month: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Return the half-open local calendar range for a strict YYYY-MM value."""
    try:
        period = pd.Period(month, freq="M")
    except (TypeError, ValueError) as exc:
        raise ValueError("month must use YYYY-MM format") from exc
    if period.strftime("%Y-%m") != month:
        raise ValueError("month must use YYYY-MM format")
    start = pd.Timestamp(period.start_time.date())
    end = start + pd.offsets.MonthBegin(1)
    return start, end


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _clean_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _clean_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean_json(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, (pd.Timestamp, pd.Timedelta)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def _json(value: Any) -> str:
    return json.dumps(_clean_json(value), ensure_ascii=False, allow_nan=False, default=str)


def _connect_output(output_database: Path, source_database: Path) -> duckdb.DuckDBPyConnection:
    output_database.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(output_database))
    connection.execute(
        f"ATTACH {_sql_string(source_database.resolve())} AS source (READ_ONLY)"
    )
    return connection


def _create_output_schema(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute("CREATE SCHEMA IF NOT EXISTS monthly_model")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS monthly_model.run_manifest (
            run_id VARCHAR PRIMARY KEY,
            month VARCHAR NOT NULL,
            source_database_path VARCHAR NOT NULL,
            source_database_sha256 VARCHAR NOT NULL,
            source_model_path VARCHAR NOT NULL,
            source_model_sha256 VARCHAR NOT NULL,
            generator_table_path VARCHAR NOT NULL,
            generator_table_sha256 VARCHAR NOT NULL,
            started_at_utc TIMESTAMPTZ NOT NULL,
            updated_at_utc TIMESTAMPTZ NOT NULL,
            status VARCHAR NOT NULL,
            expected_case_count BIGINT NOT NULL,
            completed_case_count BIGINT NOT NULL,
            failed_case_count BIGINT NOT NULL,
            description VARCHAR NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS monthly_model.cases (
            run_id VARCHAR NOT NULL,
            case_id VARCHAR PRIMARY KEY,
            local_date DATE NOT NULL,
            source_index BIGINT NOT NULL,
            eredes_alignment_status VARCHAR NOT NULL,
            timestamp_local TIMESTAMPTZ NOT NULL,
            timestamp_utc TIMESTAMPTZ NOT NULL,
            status VARCHAR NOT NULL,
            converged BOOLEAN,
            elapsed_seconds DOUBLE,
            observed_load_mw DOUBLE,
            observed_generation_total_mw DOUBLE,
            observed_net_import_mw DOUBLE,
            model_net_import_mw DOUBLE,
            vm_pu_min DOUBLE,
            vm_pu_max DOUBLE,
            maximum_line_loading_percent DOUBLE,
            lines_over_100_percent BIGINT,
            maximum_transformer_loading_percent DOUBLE,
            model_losses_percent_of_load DOUBLE,
            result_json JSON,
            error VARCHAR,
            updated_at_utc TIMESTAMPTZ NOT NULL
        )
    """)
    connection.execute("""
        ALTER TABLE monthly_model.cases
        ADD COLUMN IF NOT EXISTS eredes_alignment_status VARCHAR DEFAULT 'UNKNOWN'
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS monthly_model.bus_order (
            position BIGINT PRIMARY KEY,
            bus_id VARCHAR UNIQUE NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS monthly_model.line_order (
            position BIGINT PRIMARY KEY,
            line_id VARCHAR UNIQUE NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS monthly_model.state_arrays (
            case_id VARCHAR PRIMARY KEY,
            timestamp_utc TIMESTAMPTZ NOT NULL,
            bus_vm_pu DOUBLE[] NOT NULL,
            line_loading_percent DOUBLE[] NOT NULL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS monthly_model.audit_bundles (
            case_id VARCHAR PRIMARY KEY,
            records JSON NOT NULL
        )
    """)
    connection.execute("""
        CREATE OR REPLACE VIEW monthly_model.bus_states_expanded AS
        SELECT states.case_id, states.timestamp_utc, ordering.bus_id, values.vm_pu
        FROM monthly_model.state_arrays AS states,
             UNNEST(states.bus_vm_pu) WITH ORDINALITY AS values(vm_pu, position)
        JOIN monthly_model.bus_order AS ordering USING (position)
    """)
    connection.execute("""
        CREATE OR REPLACE VIEW monthly_model.line_states_expanded AS
        SELECT states.case_id, states.timestamp_utc, ordering.line_id,
               values.loading_percent
        FROM monthly_model.state_arrays AS states,
             UNNEST(states.line_loading_percent)
                 WITH ORDINALITY AS values(loading_percent, position)
        JOIN monthly_model.line_order AS ordering USING (position)
    """)


def _copy_static_schemas(connection: duckdb.DuckDBPyConnection) -> None:
    """Copy the compact static model and provenance once into the monthly DB."""
    rows = connection.execute("""
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_catalog = 'source'
          AND table_schema IN ('grid', 'geo', 'provenance')
        ORDER BY table_schema, table_name
    """).fetchall()
    for schema, table in rows:
        schema_sql = _quote_identifier(str(schema))
        table_sql = _quote_identifier(str(table))
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_sql}")
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {schema_sql}.{table_sql} "
            f"AS SELECT * FROM source.{schema_sql}.{table_sql}"
        )


def load_month_schedule(
    connection: duckdb.DuckDBPyConnection, month: str, max_intervals: int | None = None,
) -> list[dict[str, Any]]:
    start, end = month_bounds(month)
    frame = connection.execute("""
        SELECT local_date, source_index, timestamp_local, timestamp_utc,
               eredes_source_date, eredes_source_hour, eredes_alignment_status
        FROM source.main.interval_calendar
        WHERE local_date >= ? AND local_date < ?
        ORDER BY timestamp_utc
    """, [start.date(), end.date()]).fetchdf()
    if frame.empty:
        raise ValueError(f"No interval calendar rows exist for {month}")
    accepted_alignment = {"UNIQUE", "AMBIGUOUS_DST_FOLD"}
    if not set(frame["eredes_alignment_status"].astype(str)).issubset(accepted_alignment):
        counts = frame["eredes_alignment_status"].value_counts().to_dict()
        raise ValueError(f"Unsupported E-REDES alignments for {month}: {counts}")
    rows = frame.to_dict("records")
    if max_intervals is not None:
        rows = rows[:max_intervals]
    for row in rows:
        local = pd.Timestamp(row["timestamp_local"])
        row["local_date"] = str(pd.Timestamp(row["local_date"]).date())
        row["timestamp_local"] = local.isoformat()
        row["timestamp_utc"] = pd.Timestamp(row["timestamp_utc"]).isoformat()
        if (
            str(row["eredes_alignment_status"]) == "AMBIGUOUS_DST_FOLD"
            and (pd.isna(row["eredes_source_date"]) or pd.isna(row["eredes_source_hour"]))
        ):
            eredes_date, eredes_hour, ambiguous = eredes_wall_label(row["timestamp_utc"])
            if not ambiguous:
                raise ValueError("Calendar marks a non-ambiguous E-REDES label as a DST fold")
            row["eredes_source_date"] = eredes_date
            row["eredes_source_hour"] = eredes_hour
        else:
            row["eredes_source_date"] = str(row["eredes_source_date"])
            row["eredes_source_hour"] = str(row["eredes_source_hour"])[:5]
        row["case_id"] = f"PT60_MONTH_{local.strftime('%Y%m%d_%H%M_%z')}"
    return rows


def _case_from_interval(interval: dict[str, Any], spatial_path: Path) -> dict[str, Any]:
    timestamp = pd.Timestamp(interval["timestamp_utc"])
    local_date = pd.Timestamp(interval["local_date"])
    month = local_date.month
    factor = 1.15 if month in {12, 1, 2} else 1.10 if month in {3, 4, 10, 11} else 1.0
    season = "SUMMER" if 5 <= month <= 9 else "WINTER"
    interconnector = bool(timestamp >= INTERCONNECTOR_COMMISSIONING)
    return {
        "case_id": interval["case_id"],
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
        "analysis_role": "FULL_15MIN_MONTHLY_STEADY_STATE",
        "eredes_alignment_status": interval["eredes_alignment_status"],
        "eredes_load_resolution": (
            "AMBIGUOUS_DST_FOLD_MEAN_BY_SUBSTATION"
            if interval["eredes_alignment_status"] == "AMBIGUOUS_DST_FOLD"
            else "UNIQUE_WALL_CLOCK_LABEL"
        ),
        "skip_optional_external_acquisition": True,
        "spatial_result_path": str(spatial_path),
    }


class DatabaseInputs:
    """Read aligned public observations from the attached source database."""

    def __init__(self, connection: duckdb.DuckDBPyConnection, source_database: Path):
        self.connection = connection
        self.source_database = source_database

    def observation(self, interval: dict[str, Any]) -> dict[str, Any]:
        rows = self.connection.execute("""
            SELECT series_name, value_mw
            FROM source.main.ren_dispatch
            WHERE source_date = CAST(? AS DATE) AND source_index = ?
        """, [interval["local_date"], int(interval["source_index"])]).fetchall()
        values = {str(name): float(value) for name, value in rows if value is not None}
        required = {"Consumption", "Consumption + Storage", "Import", "Export"}
        missing = required - set(values)
        if missing:
            raise ValueError(f"REN interval is missing series: {sorted(missing)}")
        generation = {name: values.get(name, 0.0) for name in GENERATION_GROUPS}
        timestamp = pd.Timestamp(interval["timestamp_utc"])
        return {
            **source_time_metadata(timestamp),
            "source_url": f"duckdb:{self.source_database}#main.ren_dispatch",
            "load_mw": values["Consumption"],
            "load_plus_storage_mw": values["Consumption + Storage"],
            "pumping_mw": values.get("Pumping", 0.0),
            "battery_consumption_mw": values.get("Consumption of Batteries", 0.0),
            "import_mw": values["Import"],
            "export_mw": values["Export"],
            "net_import_mw": values["Import"] - values["Export"],
            "generation_by_source_mw": generation,
            "generation_total_mw": sum(generation.values()),
        }

    def load_snapshot(self, interval: dict[str, Any]) -> pd.DataFrame:
        if interval["eredes_alignment_status"] == "AMBIGUOUS_DST_FOLD":
            # E-REDES publishes both repeated-hour observations but its public
            # wall-clock label has no fold identifier. Preserve one facility
            # row by averaging its two indistinguishable observations rather
            # than summing them and doubling national substation demand.
            frame = self.connection.execute("""
                SELECT codigo_subestacao, min(subestacao) AS subestacao,
                       avg(energia) AS energia,
                       min(dataset) AS dataset_partition,
                       count(*) AS dst_fold_source_record_count
                FROM source.main.eredes_load
                WHERE data = CAST(? AS DATE) AND hora = ?
                GROUP BY codigo_subestacao
            """, [interval["eredes_source_date"], interval["eredes_source_hour"]]).fetchdf()
        else:
            frame = self.connection.execute("""
                SELECT codigo_subestacao, subestacao, energia,
                       dataset AS dataset_partition
                FROM source.main.eredes_load
                WHERE data = CAST(? AS DATE) AND hora = ?
            """, [interval["eredes_source_date"], interval["eredes_source_hour"]]).fetchdf()
        if frame.empty:
            raise ValueError(
                "No E-REDES rows for "
                f"{interval['eredes_source_date']} {interval['eredes_source_hour']}"
            )
        return frame

    def weather(self, interval: dict[str, Any]) -> dict[str, Any]:
        timestamp = pd.Timestamp(interval["timestamp_utc"])
        sample = (timestamp + pd.Timedelta(minutes=30)).floor("h")
        requested_label = sample.strftime("%Y-%m-%dT%H:00")
        label = requested_label
        frame = self.connection.execute("""
            SELECT location, temperature_2m, wind_speed_10m, shortwave_radiation
            FROM source.main.weather_hourly
            WHERE time = ?
        ORDER BY location
        """, [label]).fetchdf()
        if frame.empty:
            nearest = self.connection.execute("""
                SELECT time,
                       abs(epoch(CAST(time AS TIMESTAMP) - CAST(? AS TIMESTAMP))) AS delta_seconds
                FROM source.main.weather_hourly
                GROUP BY time
                ORDER BY delta_seconds, time
                LIMIT 1
            """, [requested_label]).fetchone()
            if nearest is None or float(nearest[1]) > 3600.0:
                raise ValueError(f"No weather rows within one hour of {requested_label}")
            label = str(nearest[0])
            frame = self.connection.execute("""
                SELECT location, temperature_2m, wind_speed_10m, shortwave_radiation
                FROM source.main.weather_hourly
                WHERE time = ?
                ORDER BY location
            """, [label]).fetchdf()
        actual_sample = pd.Timestamp(label, tz="UTC")
        offset_minutes = int((actual_sample - sample).total_seconds() / 60)
        records = [{
            "location": row["location"],
            "temperature_2m_c": float(row["temperature_2m"]),
            "wind_speed_10m_kmh": float(row["wind_speed_10m"]),
            "shortwave_radiation_w_m2": float(row["shortwave_radiation"]),
        } for row in frame.to_dict("records")]
        return {
            "source": "PT60 DuckDB weather_hourly; contextual weather, not line-level DLR telemetry",
            "requested_sample_hour_utc": sample.isoformat(),
            "sample_hour_utc": actual_sample.isoformat(),
            "weather_alignment_status": (
                "EXACT_HOURLY_SAMPLE" if offset_minutes == 0
                else "NEAREST_AVAILABLE_BOUNDARY_HOUR"
            ),
            "weather_sample_offset_minutes": offset_minutes,
            "locations": records,
            "mean_temperature_2m_c": sum(row["temperature_2m_c"] for row in records) / len(records),
            "mean_wind_speed_10m_kmh": sum(row["wind_speed_10m_kmh"] for row in records) / len(records),
        }

    def rnt_loss(self, case: dict[str, Any]) -> dict[str, Any]:
        monthly_label = pd.Timestamp(case["rnt_loss_benchmark_date"]).strftime("%b %y [GWh]")
        rows = self.connection.execute("""
            SELECT column_label, TRY_CAST(value_text AS DOUBLE)
            FROM source.main.ren_rnt_balance
            WHERE benchmark_date = CAST(? AS DATE)
              AND trim(row_label) = 'LOSSES [%]'
              AND column_label = ?
        """, [case["rnt_loss_benchmark_date"], monthly_label]).fetchall()
        values = [(label, value) for label, value in rows if value is not None]
        if len(values) != 1:
            raise ValueError(
                f"Expected one monthly RNT loss value for {case['rnt_loss_benchmark_date']}, "
                f"found {values}"
            )
        return {
            "benchmark_date": case["rnt_loss_benchmark_date"],
            "loss_percent": float(values[0][1]),
            "source_url": f"duckdb:{self.source_database}#main.ren_rnt_balance",
            "scope": "REN National Transmission Network monthly physical balance",
        }


def _worker_initialize(source_database: str, output_dir: str, temporary_dir: str) -> None:
    """Initialize one read-only modeling worker; DuckDB writing stays in the parent."""
    global _WORKER_INPUTS, _WORKER_GENERATORS, _WORKER_OUTPUT_DIR, _WORKER_TEMPORARY_DIR
    source_path = Path(source_database)
    connection = duckdb.connect(":memory:")
    connection.execute(f"ATTACH {_sql_string(source_path.resolve())} AS source (READ_ONLY)")
    _WORKER_INPUTS = DatabaseInputs(connection, source_path)
    _WORKER_GENERATORS = pd.read_csv(GENERATOR_INPUT, low_memory=False)
    _WORKER_OUTPUT_DIR = Path(output_dir)
    _WORKER_TEMPORARY_DIR = Path(temporary_dir)


def _worker_compute(interval: dict[str, Any]) -> dict[str, Any]:
    """Solve one interval and return a database-writer payload."""
    if any(value is None for value in (
        _WORKER_INPUTS, _WORKER_GENERATORS, _WORKER_OUTPUT_DIR, _WORKER_TEMPORARY_DIR
    )):
        raise RuntimeError("Monthly worker was not initialized")
    started = time.monotonic()
    spatial_path = _WORKER_TEMPORARY_DIR / f"{interval['case_id']}.npz"  # type: ignore[operator]
    case = _case_from_interval(interval, spatial_path)
    try:
        observation = _WORKER_INPUTS.observation(interval)  # type: ignore[union-attr]
        snapshot = _WORKER_INPUTS.load_snapshot(interval)  # type: ignore[union-attr]
        weather = _WORKER_INPUTS.weather(interval)  # type: ignore[union-attr]
        rnt_loss = _WORKER_INPUTS.rnt_loss(case)  # type: ignore[union-attr]
        outputs = run_case(
            case, _WORKER_GENERATORS, False, save_solved=False,
            observation_override=observation,
            load_snapshot_override=snapshot,
            weather_override=weather,
            rnt_loss_override=rnt_loss,
            output_dir=_WORKER_OUTPUT_DIR,
        )
        result, source_rows, hotspot_rows, load_rows, cross_rows, boundary_rows, loss_row = outputs
        return {
            "status": "COMPLETE",
            "interval": interval,
            "elapsed": time.monotonic() - started,
            "result": result,
            "spatial_path": str(spatial_path),
            "audit_groups": (
                ("GENERATION_SOURCE", source_rows),
                ("LINE_HOTSPOT", hotspot_rows),
                ("LOAD_ALLOCATION", load_rows),
                ("CROSS_BORDER", cross_rows),
                ("BOUNDARY", boundary_rows),
                ("LOSS", [loss_row]),
            ),
        }
    except Exception as exc:
        if spatial_path.exists():
            spatial_path.unlink()
        return {
            "status": "FAILED",
            "interval": interval,
            "elapsed": time.monotonic() - started,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _delete_case(connection: duckdb.DuckDBPyConnection, case_id: str) -> None:
    for table in ("state_arrays", "audit_bundles", "cases"):
        connection.execute(
            f"DELETE FROM monthly_model.{table} WHERE case_id = ?", [case_id]
        )


def _audit_rows(groups: Iterable[tuple[str, list[dict[str, Any]]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record_type, values in groups:
        rows.extend({
            "record_type": record_type,
            "record_index": index,
            "payload": value,
        } for index, value in enumerate(values))
    return rows


def _ensure_device_order(
    connection: duckdb.DuckDBPyConnection,
    bus_ids: list[str],
    line_ids: list[str],
) -> None:
    """Store stable array positions once and reject incompatible model layouts."""
    global _PARENT_DEVICE_ORDER
    if _PARENT_DEVICE_ORDER is not None:
        if _PARENT_DEVICE_ORDER != (bus_ids, line_ids):
            raise ValueError("Device order changed between solved cases")
        return
    for table, id_column, identifiers in (
        ("bus_order", "bus_id", bus_ids),
        ("line_order", "line_id", line_ids),
    ):
        current = [str(row[0]) for row in connection.execute(
            f"SELECT {id_column} FROM monthly_model.{table} ORDER BY position"
        ).fetchall()]
        if not current:
            connection.executemany(
                f"INSERT INTO monthly_model.{table} VALUES (?, ?)",
                [(position, identifier) for position, identifier in enumerate(identifiers, 1)],
            )
        elif current != identifiers:
            raise ValueError(f"{table} does not match the current static model")
    _PARENT_DEVICE_ORDER = (bus_ids, line_ids)


def _store_complete_case(
    connection: duckdb.DuckDBPyConnection,
    run_id: str,
    interval: dict[str, Any],
    result: dict[str, Any],
    elapsed: float,
    spatial_path: Path,
    audit_groups: Iterable[tuple[str, list[dict[str, Any]]]],
) -> None:
    with np.load(spatial_path) as spatial:
        bus_pairs = sorted(
            (str(bus_id), float(vm))
            for bus_id, vm in zip(spatial["bus_id"], spatial["vm_pu"])
        )
        line_pairs = sorted(
            (str(line_id), float(value))
            for line_id, value in zip(spatial["line_id"], spatial["loading_percent"])
        )
    timestamp = interval["timestamp_utc"]
    bus_ids = [identifier for identifier, _ in bus_pairs]
    line_ids = [identifier for identifier, _ in line_pairs]
    bus_values = [value if math.isfinite(value) else None for _, value in bus_pairs]
    line_values = [value if math.isfinite(value) else None for _, value in line_pairs]
    audits = _audit_rows(audit_groups)
    connection.execute("BEGIN TRANSACTION")
    try:
        _delete_case(connection, interval["case_id"])
        _ensure_device_order(connection, bus_ids, line_ids)
        connection.execute("""
            INSERT INTO monthly_model.cases (
                run_id, case_id, local_date, source_index, eredes_alignment_status,
                timestamp_local, timestamp_utc, status, converged, elapsed_seconds,
                observed_load_mw, observed_generation_total_mw, observed_net_import_mw,
                model_net_import_mw, vm_pu_min, vm_pu_max,
                maximum_line_loading_percent, lines_over_100_percent,
                maximum_transformer_loading_percent, model_losses_percent_of_load,
                result_json, error, updated_at_utc
            ) VALUES (
                ?, ?, CAST(? AS DATE), ?, ?, CAST(? AS TIMESTAMPTZ), CAST(? AS TIMESTAMPTZ),
                'COMPLETE', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS JSON), '',
                CAST(? AS TIMESTAMPTZ)
            )
        """, [
            run_id, interval["case_id"], interval["local_date"], interval["source_index"],
            interval["eredes_alignment_status"], interval["timestamp_local"], timestamp,
            bool(result["converged"]), elapsed,
            result["observed_load_mw"], result["observed_generation_total_mw"],
            result["observed_net_import_mw"], result["model_net_import_mw"],
            result["vm_pu_min"], result["vm_pu_max"], result["maximum_line_loading_percent"],
            result["lines_over_100_percent"], result["maximum_transformer_loading_percent"],
            result["model_losses_percent_of_load"], _json(result), utc_now(),
        ])
        connection.execute(
            "INSERT INTO monthly_model.state_arrays VALUES (?, CAST(? AS TIMESTAMPTZ), ?, ?)",
            [interval["case_id"], timestamp, bus_values, line_values],
        )
        connection.execute(
            "INSERT INTO monthly_model.audit_bundles VALUES (?, CAST(? AS JSON))",
            [interval["case_id"], _json(audits)],
        )
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def _store_failed_case(
    connection: duckdb.DuckDBPyConnection, run_id: str,
    interval: dict[str, Any], elapsed: float, error: str,
) -> None:
    connection.execute("BEGIN TRANSACTION")
    try:
        _delete_case(connection, interval["case_id"])
        connection.execute("""
            INSERT INTO monthly_model.cases (
                run_id, case_id, local_date, source_index, eredes_alignment_status,
                timestamp_local, timestamp_utc,
                status, converged, elapsed_seconds, error, updated_at_utc
            ) VALUES (?, ?, CAST(? AS DATE), ?, ?, CAST(? AS TIMESTAMPTZ),
                      CAST(? AS TIMESTAMPTZ), 'FAILED', FALSE, ?, ?, CAST(? AS TIMESTAMPTZ))
        """, [
            run_id, interval["case_id"], interval["local_date"], interval["source_index"],
            interval["eredes_alignment_status"], interval["timestamp_local"],
            interval["timestamp_utc"], elapsed, error, utc_now(),
        ])
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def _update_manifest(
    connection: duckdb.DuckDBPyConnection, run_id: str, month: str,
    source_database: Path, expected: int, started_at: str, status: str,
) -> tuple[int, int]:
    start, end = month_bounds(month)
    completed, failed = connection.execute("""
        SELECT count(*) FILTER (WHERE status = 'COMPLETE'),
               count(*) FILTER (WHERE status = 'FAILED')
        FROM monthly_model.cases
        WHERE local_date >= ? AND local_date < ?
    """, [start.date(), end.date()]).fetchone()
    previous = connection.execute("""
        SELECT source_database_path, source_database_sha256,
               source_model_path, source_model_sha256,
               generator_table_path, generator_table_sha256
        FROM monthly_model.run_manifest WHERE run_id = ?
    """, [run_id]).fetchone()
    fingerprints = previous or (
        str(source_database.resolve()), _sha256(source_database),
        str(MODEL_INPUT.resolve()), _sha256(MODEL_INPUT),
        str(GENERATOR_INPUT.resolve()), _sha256(GENERATOR_INPUT),
    )
    connection.execute("DELETE FROM monthly_model.run_manifest WHERE run_id = ?", [run_id])
    connection.execute("""
        INSERT INTO monthly_model.run_manifest VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, CAST(? AS TIMESTAMPTZ), CAST(? AS TIMESTAMPTZ),
            ?, ?, ?, ?, ?
        )
    """, [
        run_id, month, *fingerprints,
        started_at, utc_now(), status, expected, int(completed), int(failed),
        "All 15-minute PT60 models; compact ordered state arrays; no peak/median/valley sampling.",
    ])
    return int(completed), int(failed)


def _import_normalized_resume(
    connection: duckdb.DuckDBPyConnection,
    legacy_database: Path,
    month: str,
    logger: Any,
) -> int:
    """Convert completed rows from the former normalized layout into arrays."""
    if not legacy_database.is_file():
        raise FileNotFoundError(f"legacy resume database does not exist: {legacy_database}")
    connection.execute(
        f"ATTACH {_sql_string(legacy_database.resolve())} AS legacy (READ_ONLY)"
    )
    try:
        required = {"cases", "bus_states", "line_states", "audit_records"}
        present = {str(row[0]) for row in connection.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_catalog = 'legacy' AND table_schema = 'monthly_model'
        """).fetchall()}
        missing = required - present
        if missing:
            raise ValueError(f"Legacy database is missing normalized tables: {sorted(missing)}")
        start, end = month_bounds(month)
        connection.execute("""
            CREATE OR REPLACE TEMP TABLE legacy_import_ids AS
            SELECT legacy_cases.case_id
            FROM legacy.monthly_model.cases AS legacy_cases
            WHERE legacy_cases.status = 'COMPLETE'
              AND legacy_cases.local_date >= ? AND legacy_cases.local_date < ?
              AND NOT EXISTS (
                  SELECT 1 FROM monthly_model.cases AS local_cases
                  WHERE local_cases.case_id = legacy_cases.case_id
                    AND local_cases.status = 'COMPLETE'
              )
        """, [start.date(), end.date()])
        import_count = int(connection.execute(
            "SELECT count(*) FROM legacy_import_ids"
        ).fetchone()[0])
        if import_count == 0:
            logger.info("Legacy normalized resume: no additional complete cases to import")
            return 0
        logger.info(
            "Legacy normalized resume: converting %d complete cases from %s",
            import_count, legacy_database.resolve(),
        )
        connection.execute("BEGIN TRANSACTION")
        try:
            if connection.execute(
                "SELECT count(*) FROM monthly_model.bus_order"
            ).fetchone()[0] == 0:
                connection.execute("""
                    INSERT INTO monthly_model.bus_order
                    SELECT row_number() OVER (ORDER BY bus_id), bus_id
                    FROM (
                        SELECT DISTINCT states.bus_id
                        FROM legacy.monthly_model.bus_states AS states
                        JOIN legacy_import_ids USING (case_id)
                    )
                    ORDER BY bus_id
                """)
            if connection.execute(
                "SELECT count(*) FROM monthly_model.line_order"
            ).fetchone()[0] == 0:
                connection.execute("""
                    INSERT INTO monthly_model.line_order
                    SELECT row_number() OVER (ORDER BY line_id), line_id
                    FROM (
                        SELECT DISTINCT states.line_id
                        FROM legacy.monthly_model.line_states AS states
                        JOIN legacy_import_ids USING (case_id)
                    )
                    ORDER BY line_id
                """)
            connection.execute("""
                INSERT INTO monthly_model.cases
                SELECT legacy_cases.*
                FROM legacy.monthly_model.cases AS legacy_cases
                JOIN legacy_import_ids USING (case_id)
            """)
            connection.execute("""
                INSERT INTO monthly_model.state_arrays
                WITH buses AS (
                    SELECT states.case_id, list(states.vm_pu ORDER BY states.bus_id) AS values
                    FROM legacy.monthly_model.bus_states AS states
                    JOIN legacy_import_ids USING (case_id)
                    GROUP BY states.case_id
                ), lines AS (
                    SELECT states.case_id,
                           list(states.loading_percent ORDER BY states.line_id) AS values
                    FROM legacy.monthly_model.line_states AS states
                    JOIN legacy_import_ids USING (case_id)
                    GROUP BY states.case_id
                )
                SELECT legacy_cases.case_id, legacy_cases.timestamp_utc,
                       buses.values, lines.values
                FROM legacy.monthly_model.cases AS legacy_cases
                JOIN legacy_import_ids USING (case_id)
                JOIN buses USING (case_id)
                JOIN lines USING (case_id)
            """)
            connection.execute("""
                INSERT INTO monthly_model.audit_bundles
                SELECT records.case_id,
                       to_json(list(struct_pack(
                           record_type := records.record_type,
                           record_index := records.record_index,
                           payload := records.payload
                       ) ORDER BY records.record_type, records.record_index))
                FROM legacy.monthly_model.audit_records AS records
                JOIN legacy_import_ids USING (case_id)
                GROUP BY records.case_id
            """)
            connection.execute("""
                INSERT INTO monthly_model.run_manifest
                SELECT manifest.* FROM legacy.monthly_model.run_manifest AS manifest
                WHERE manifest.month = ?
                  AND NOT EXISTS (
                      SELECT 1 FROM monthly_model.run_manifest AS local_manifest
                      WHERE local_manifest.run_id = manifest.run_id
                  )
            """, [month])
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        logger.info("Legacy normalized resume conversion complete: imported=%d", import_count)
        return import_count
    finally:
        connection.execute("DETACH legacy")


def _database_counts(path: Path, month: str) -> tuple[int, int, int]:
    connection = duckdb.connect(str(path), read_only=True)
    try:
        start, end = month_bounds(month)
        cases, arrays, audits = connection.execute("""
            SELECT
              (SELECT count(*) FROM monthly_model.cases
               WHERE local_date >= ? AND local_date < ? AND status = 'COMPLETE'),
              (SELECT count(*) FROM monthly_model.state_arrays),
              (SELECT count(*) FROM monthly_model.audit_bundles)
        """, [start.date(), end.date()]).fetchone()
        return int(cases), int(arrays), int(audits)
    finally:
        connection.close()


def _publish_database(
    local_database: Path,
    publish_database: Path,
    month: str,
    overwrite: bool,
    logger: Any,
) -> None:
    """Bulk-copy a closed stage into a compact temporary DB, verify, and rename."""
    local_resolved = local_database.resolve()
    publish_resolved = publish_database.resolve()
    if local_resolved == publish_resolved:
        raise ValueError("Local staging database and publish database must be different")
    if publish_database.exists() and not overwrite:
        raise FileExistsError(
            f"publish target already exists (use --overwrite-publish): {publish_database}"
        )
    publish_database.parent.mkdir(parents=True, exist_ok=True)
    temporary = publish_database.with_name(publish_database.name + ".part")
    if temporary.exists():
        temporary.unlink()
    logger.info(
        "Bulk-converting closed staging arrays into final database: local=%s destination=%s",
        local_resolved, publish_resolved,
    )
    copier = duckdb.connect(":memory:")
    try:
        copier.execute(
            f"ATTACH {_sql_string(local_resolved)} AS local_stage (READ_ONLY)"
        )
        copier.execute(
            f"ATTACH {_sql_string(temporary.resolve())} AS final_database"
        )
        copier.execute("COPY FROM DATABASE local_stage TO final_database")
        copier.execute("CHECKPOINT final_database")
        copier.execute("DETACH final_database")
        copier.execute("DETACH local_stage")
    finally:
        copier.close()
    source_counts = _database_counts(local_database, month)
    copied_counts = _database_counts(temporary, month)
    if source_counts != copied_counts:
        temporary.unlink(missing_ok=True)
        raise IOError(
            f"Published database verification failed: source={source_counts}, copy={copied_counts}"
        )
    temporary.replace(publish_database)
    logger.info(
        "Publish complete: cases=%d arrays=%d audits=%d size=%.1f MiB",
        *copied_counts, publish_database.stat().st_size / 2**20,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Complete PT60 input DuckDB")
    parser.add_argument("--month", required=True, help="Europe/Lisbon calendar month, YYYY-MM")
    parser.add_argument("--output-dir", type=Path, help="Run directory; defaults below outputs/monthly_15min")
    parser.add_argument(
        "--output-database", type=Path,
        help="Local SSD staging DuckDB path (compact arrays; defaults inside output-dir)",
    )
    parser.add_argument(
        "--resume-from-normalized", type=Path,
        help="Read-only former monthly DB whose completed normalized cases should be converted",
    )
    parser.add_argument(
        "--publish-database", type=Path,
        help="Final DuckDB path on another disk; bulk-created only after a complete run",
    )
    parser.add_argument(
        "--overwrite-publish", action="store_true",
        help="Allow replacement of an existing final publish database",
    )
    parser.add_argument("--max-intervals", type=int, help="Limit intervals for a smoke test")
    parser.add_argument("--workers", type=int, default=1, help="Parallel solver processes (default: 1)")
    parser.add_argument(
        "--manifest-every", type=int, default=16,
        help="Refresh run counters after this many written cases (default: 16)",
    )
    parser.add_argument("--rerun-complete", action="store_true", help="Recompute completed intervals")
    parser.add_argument("--fail-fast", action="store_true", help="Stop after the first failed interval")
    args = parser.parse_args()
    try:
        month_bounds(args.month)
    except ValueError as exc:
        parser.error(str(exc))
    if args.max_intervals is not None and args.max_intervals < 1:
        parser.error("--max-intervals must be positive")
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.manifest_every < 1:
        parser.error("--manifest-every must be positive")
    if not args.database.is_file():
        parser.error(f"database does not exist: {args.database}")
    if args.resume_from_normalized is not None and not args.resume_from_normalized.is_file():
        parser.error(f"resume database does not exist: {args.resume_from_normalized}")
    for required in (MODEL_INPUT, GENERATOR_INPUT):
        if not required.is_file():
            parser.error(f"required static model input does not exist: {required}")

    output_dir = args.output_dir or DEFAULT_OUTPUT_ROOT / args.month
    output_database = args.output_database or output_dir / f"pt60_models_{args.month}.compact.duckdb"
    output_dir.mkdir(parents=True, exist_ok=True)
    logger, log_path = configure_logging(output_dir, "pt60.monthly_15min")
    started_at = utc_now()
    started = time.monotonic()
    run_id = f"PT60_MONTH_{args.month.replace('-', '')}"

    connection = _connect_output(output_database, args.database)
    _create_output_schema(connection)
    _copy_static_schemas(connection)
    schedule = load_month_schedule(connection, args.month, args.max_intervals)
    expected = len(schedule)
    if args.resume_from_normalized is not None:
        _import_normalized_resume(
            connection, args.resume_from_normalized, args.month, logger,
        )
    _update_manifest(connection, run_id, args.month, args.database, expected, started_at, "RUNNING")
    logger.info("PT60 monthly full-resolution modeling started")
    logger.info("Month=%s intervals=%d database=%s", args.month, expected, args.database.resolve())
    logger.info("Local SSD staging database=%s", output_database.resolve())
    logger.info("Storage layout=one row/case with bus and line DOUBLE[] arrays")
    logger.info("Source database is attached READ_ONLY; no downloads or source writes")
    if args.publish_database is not None:
        logger.info("Final publish database=%s", args.publish_database.resolve())
    logger.info("Log=%s", log_path.resolve())

    completed_this_session = 0
    failures_this_session = 0
    skipped = 0
    with tempfile.TemporaryDirectory(prefix="pt60-monthly-", dir=output_dir) as temporary:
        temporary_dir = Path(temporary)
        pending: list[dict[str, Any]] = []
        for interval in schedule:
            existing = connection.execute(
                "SELECT status FROM monthly_model.cases WHERE case_id = ?", [interval["case_id"]]
            ).fetchone()
            if existing and existing[0] == "COMPLETE" and not args.rerun_complete:
                skipped += 1
                continue
            pending.append(interval)
        logger.info(
            "Execution plan: total=%d reusable=%d pending=%d workers=%d",
            expected, skipped, len(pending), args.workers,
        )
        batch_started = time.monotonic()
        with ProcessPoolExecutor(
            max_workers=args.workers,
            initializer=_worker_initialize,
            initargs=(str(args.database), str(output_dir), str(temporary_dir)),
        ) as executor:
            futures = {executor.submit(_worker_compute, interval): interval for interval in pending}
            for position, future in enumerate(as_completed(futures), start=1):
                interval = futures[future]
                try:
                    payload = future.result()
                except Exception as exc:
                    payload = {
                        "status": "FAILED", "interval": interval, "elapsed": 0.0,
                        "error": f"WorkerFailure: {type(exc).__name__}: {exc}",
                    }
                elapsed = float(payload["elapsed"])
                if payload["status"] == "COMPLETE":
                    result = payload["result"]
                    spatial_path = Path(payload["spatial_path"])
                    try:
                        _store_complete_case(
                            connection, run_id, interval, result, elapsed, spatial_path,
                            payload["audit_groups"],
                        )
                    finally:
                        if spatial_path.exists():
                            spatial_path.unlink()
                    completed_this_session += 1
                    state = "COMPLETE"
                    detail = (
                        f"converged={result['converged']} "
                        f"max_line={result['maximum_line_loading_percent']:.1f}%"
                    )
                else:
                    error = str(payload["error"])
                    _store_failed_case(connection, run_id, interval, elapsed, error)
                    failures_this_session += 1
                    state = "FAILED"
                    detail = error
                finished = completed_this_session + failures_this_session
                wall_elapsed = time.monotonic() - batch_started
                rate = finished / wall_elapsed if wall_elapsed > 0 else 0.0
                remaining = max(0, len(pending) - finished)
                eta = remaining / rate if rate > 0 else 0.0
                logger.info(
                    "[RESULT %04d/%04d | %5.1f%%] %s %s %s solve=%s ETA=%s",
                    position, len(pending), 100.0 * position / max(1, len(pending)),
                    interval["case_id"], state, detail,
                    format_duration(elapsed), format_duration(eta),
                )
                if finished % args.manifest_every == 0:
                    _update_manifest(
                        connection, run_id, args.month, args.database,
                        expected, started_at, "RUNNING",
                    )
                if state == "FAILED" and args.fail_fast:
                    for pending_future in futures:
                        pending_future.cancel()
                    break

    completed, failed = _update_manifest(
        connection, run_id, args.month, args.database, expected, started_at,
        "COMPLETE" if failed_cases(connection, args.month) == 0 and completed_cases(connection, args.month) >= expected else "INCOMPLETE",
    )
    connection.execute("CHECKPOINT")
    connection.close()
    logger.info(
        "Run finished: complete=%d/%d failed=%d wall_time=%s size=%.1f MiB",
        completed, expected, failed, format_duration(time.monotonic() - started),
        output_database.stat().st_size / 2**20,
    )
    ready_to_publish = (
        args.max_intervals is None and failed == 0 and completed >= expected
    )
    if args.publish_database is not None:
        if ready_to_publish:
            _publish_database(
                output_database, args.publish_database, args.month,
                args.overwrite_publish, logger,
            )
        else:
            logger.warning(
                "Publish skipped because the full month is not complete: complete=%d/%d failed=%d",
                completed, expected, failed,
            )
    if failed:
        raise SystemExit(1)


def completed_cases(connection: duckdb.DuckDBPyConnection, month: str) -> int:
    start, end = month_bounds(month)
    return int(connection.execute("""
        SELECT count(*) FROM monthly_model.cases
        WHERE local_date >= ? AND local_date < ? AND status = 'COMPLETE'
    """, [start.date(), end.date()]).fetchone()[0])


def failed_cases(connection: duckdb.DuckDBPyConnection, month: str) -> int:
    start, end = month_bounds(month)
    return int(connection.execute("""
        SELECT count(*) FROM monthly_model.cases
        WHERE local_date >= ? AND local_date < ? AND status = 'FAILED'
    """, [start.date(), end.date()]).fetchone()[0])


if __name__ == "__main__":
    main()
