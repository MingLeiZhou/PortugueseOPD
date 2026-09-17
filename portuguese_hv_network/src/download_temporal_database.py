#!/usr/bin/env python3
"""Download public PT60 time-series evidence and build a DuckDB catalog; no power flow."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import logging
import shutil
import time
from calendar import monthrange
from pathlib import Path
from typing import Any, Iterable

import duckdb
import pandas as pd
import requests

from common import PROJECT, utc_now, write_json
from run_temporal_validation import (
    EREDES_API_BASE,
    EREDES_LOAD_PARTITIONS,
    PUBLIC_DATA_SESSION,
    REN_RNT_BALANCE_CSV,
    REN_URL,
    SOURCE_ZONE,
    WEATHER_POINTS,
    WEATHER_URL,
)
from time_alignment import eredes_wall_label


DEFAULT_ROOT = PROJECT / "outputs" / "temporal_database"
DEFAULT_STATIC_RELEASE = PROJECT.parent / "data" / "releases" / "PT60-v2.0.0"


def _configure_logging(root: Path) -> logging.Logger:
    logger = logging.getLogger("pt60_temporal_database")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(root / "download.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def _months(start_date: str, end_date: str) -> Iterable[tuple[pd.Timestamp, pd.Timestamp]]:
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    cursor = start
    while cursor <= end:
        month_end = pd.Timestamp(cursor.year, cursor.month, monthrange(cursor.year, cursor.month)[1])
        yield cursor, min(end, month_end)
        cursor = month_end + pd.Timedelta(days=1)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _request_bytes(
    url: str, params: dict[str, Any], logger: logging.Logger,
    backoff: float, timeout: float = 900,
) -> bytes:
    for attempt in range(1, 5):
        try:
            response = PUBLIC_DATA_SESSION.get(
                url, params=params,
                headers={"User-Agent": "PT60 public temporal database/0.1"},
                timeout=timeout,
            )
            response.raise_for_status()
            return response.content
        except requests.RequestException as exc:
            if attempt == 4:
                raise
            cooldown = backoff * attempt
            logger.warning("Request failed (%s); retry %d/4 after %.0fs", type(exc).__name__, attempt + 1, cooldown)
            time.sleep(cooldown)
    raise AssertionError("unreachable")


def _atomic_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_bytes(content)
    partial.replace(path)


def _manifest_row(
    root: Path, path: Path, source: str, dataset: str,
    start: str, end: str, url: str, rows: int, cached: bool,
) -> dict[str, Any]:
    return {
        "source": source,
        "dataset": dataset,
        "period_start": start,
        "period_end": end,
        "relative_path": str(path.relative_to(root)),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "row_count": rows,
        "source_url": url,
        "cached": cached,
        "checked_at_utc": utc_now(),
    }


def download_eredes(
    root: Path, start_date: str, end_date: str, refresh: bool,
    delay: float, backoff: float, logger: logging.Logger,
) -> list[dict[str, Any]]:
    # E-REDES labels energy at interval end, so the final 23:45 model interval
    # is represented by 00:00 on the following source date.
    source_end = str((pd.Timestamp(end_date) + pd.Timedelta(days=1)).date())
    chunks = list(_months(start_date, source_end))
    total = len(chunks) * len(EREDES_LOAD_PARTITIONS)
    manifest: list[dict[str, Any]] = []
    position = 0
    for chunk_start, chunk_end in chunks:
        for dataset in EREDES_LOAD_PARTITIONS:
            position += 1
            start = str(chunk_start.date())
            end = str(chunk_end.date())
            path = (
                root / "parquet" / "eredes_load"
                / f"year={chunk_start.year}" / f"month={chunk_start.month:02d}"
                / f"dataset={dataset}" / f"{start}_{end}.parquet"
            )
            cached = path.exists() and not refresh
            url = f"{EREDES_API_BASE}/{dataset}/exports/parquet"
            if not cached:
                content = _request_bytes(
                    url,
                    {
                        "where": f"data >= date'{start}' and data <= date'{end}'",
                        "timezone": SOURCE_ZONE,
                        "parquet_compression": "zstd",
                    },
                    logger, backoff,
                )
                _atomic_bytes(path, content)
                if delay:
                    time.sleep(delay)
            rows = len(pd.read_parquet(path, columns=["data"]))
            manifest.append(_manifest_row(root, path, "E-REDES", dataset, start, end, url, rows, cached))
            logger.info("[E-REDES %03d/%03d] %s %s..%s rows=%d size=%.2f MiB %s", position, total, dataset, start, end, rows, path.stat().st_size / 2**20, "CACHE" if cached else "DOWNLOAD")
    return manifest


def _ren_month_frame(
    start: pd.Timestamp, end: pd.Timestamp, logger: logging.Logger,
    delay: float, backoff: float,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    dates = pd.date_range(start, end, freq="D")
    for position, date in enumerate(dates, start=1):
        date_text = str(date.date())
        content = _request_bytes(REN_URL, {"date": date_text, "culture": "en-US"}, logger, backoff, 180)
        payload = json.loads(content)
        categories = payload["xAxis"]["categories"]
        for series in payload["series"]:
            values = series.get("data", [])
            if len(values) != len(categories):
                raise ValueError(f"REN {date_text} series {series.get('name')} length mismatch")
            rows.extend({
                "source_date": date.date(),
                "source_index": index,
                "source_time_local": f"{date_text} {label}",
                "series_name": str(series["name"]),
                "value_mw": value,
            } for index, (label, value) in enumerate(zip(categories, values)))
        logger.info("[REN %02d/%02d] %s intervals=%d", position, len(dates), date_text, len(categories))
        if delay:
            time.sleep(delay)
    return pd.DataFrame(rows)


def download_ren(
    root: Path, start_date: str, end_date: str, refresh: bool,
    delay: float, backoff: float, logger: logging.Logger,
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    chunks = list(_months(start_date, end_date))
    for position, (start, end) in enumerate(chunks, start=1):
        start_text, end_text = str(start.date()), str(end.date())
        path = root / "parquet" / "ren_dispatch" / f"year={start.year}" / f"month={start.month:02d}" / f"{start_text}_{end_text}.parquet"
        cached = path.exists() and not refresh
        if not cached:
            frame = _ren_month_frame(start, end, logger, delay, backoff)
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False, compression="zstd")
        rows = len(pd.read_parquet(path, columns=["source_date"]))
        manifest.append(_manifest_row(root, path, "REN", "ElectricityProductionBreakdownDaily", start_text, end_text, REN_URL, rows, cached))
        logger.info("[REN MONTH %02d/%02d] %s..%s rows=%d size=%.2f MiB %s", position, len(chunks), start_text, end_text, rows, path.stat().st_size / 2**20, "CACHE" if cached else "DOWNLOAD")
    return manifest


def download_weather(
    root: Path, start_date: str, end_date: str, refresh: bool,
    backoff: float, logger: logging.Logger,
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for position, (name, (latitude, longitude)) in enumerate(WEATHER_POINTS.items(), start=1):
        path = root / "parquet" / "weather" / f"location={name}" / f"{start_date}_{end_date}.parquet"
        cached = path.exists() and not refresh
        if not cached:
            content = _request_bytes(
                WEATHER_URL,
                {
                    "latitude": latitude, "longitude": longitude,
                    "start_date": start_date, "end_date": end_date,
                    "hourly": "temperature_2m,wind_speed_10m,shortwave_radiation",
                    "timezone": "UTC",
                }, logger, backoff, 300,
            )
            payload = json.loads(content)
            frame = pd.DataFrame(payload["hourly"])
            frame["location"] = name
            frame["latitude"] = latitude
            frame["longitude"] = longitude
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False, compression="zstd")
        rows = len(pd.read_parquet(path, columns=["time"]))
        manifest.append(_manifest_row(root, path, "Open-Meteo", "Historical Weather API", start_date, end_date, WEATHER_URL, rows, cached))
        logger.info("[WEATHER %d/%d] %s rows=%d size=%.2f MiB %s", position, len(WEATHER_POINTS), name, rows, path.stat().st_size / 2**20, "CACHE" if cached else "DOWNLOAD")
    return manifest


def download_rnt(
    root: Path, start_date: str, end_date: str, refresh: bool,
    backoff: float, logger: logging.Logger,
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    chunks = list(_months(start_date, end_date))
    for position, (_, end) in enumerate(chunks, start=1):
        month_end = end + pd.offsets.MonthEnd(0)
        benchmark = str(month_end.date())
        path = root / "parquet" / "ren_rnt_balance" / f"year={month_end.year}" / f"month={month_end.month:02d}" / f"balance_{benchmark}.parquet"
        cached = path.exists() and not refresh
        if not cached:
            content = _request_bytes(
                REN_RNT_BALANCE_CSV,
                {"startDateString": benchmark, "endDateString": benchmark, "culture": "en-GB"},
                logger, backoff, 180,
            )
            wide = pd.read_csv(io.BytesIO(content), sep=";", encoding="utf-8-sig", dtype=str)
            wide = wide.rename(columns={wide.columns[0]: "row_label"})
            frame = wide.melt(id_vars="row_label", var_name="column_label", value_name="value_text")
            frame["benchmark_date"] = month_end.date()
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(path, index=False, compression="zstd")
        rows = len(pd.read_parquet(path, columns=["row_label"]))
        manifest.append(_manifest_row(root, path, "REN", "RNT physical balance", benchmark, benchmark, REN_RNT_BALANCE_CSV, rows, cached))
        logger.info("[RNT %02d/%02d] %s rows=%d %s", position, len(chunks), benchmark, rows, "CACHE" if cached else "DOWNLOAD")
    return manifest


def _interval_calendar(start_date: str, end_date: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for date in pd.date_range(start_date, end_date, freq="D"):
        local_start = date.tz_localize(SOURCE_ZONE)
        local_end = local_start + pd.DateOffset(days=1)
        for index, local in enumerate(pd.date_range(local_start, local_end, freq="15min", inclusive="left")):
            utc = local.tz_convert("UTC")
            eredes_date, eredes_hour, ambiguous = eredes_wall_label(utc)
            alignment_status = "AMBIGUOUS_DST_FOLD" if ambiguous else "UNIQUE"
            rows.append({
                "local_date": date.date(), "source_index": index,
                "timestamp_local": local.isoformat(), "timestamp_utc": utc.isoformat(),
                "eredes_source_date": eredes_date, "eredes_source_hour": eredes_hour,
                "eredes_alignment_status": alignment_status,
            })
    return pd.DataFrame(rows)


def _sql_path(path: Path) -> str:
    """Return a safely quoted path fragment for DuckDB SQL."""
    return str(path.resolve()).replace("'", "''")


def archive_static_raw_files(
    root: Path,
    release: Path,
    logger: logging.Logger,
) -> tuple[Path, pd.DataFrame]:
    """Copy immutable static inputs beside the database and catalog each byte stream."""
    manifest_path = release / "provenance" / "source_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    archive = root / "raw_static"
    records: list[dict[str, Any]] = []
    canonical_paths: set[str] = set()

    for record in payload.get("records", []):
        local_path = Path(str(record["local_path"]))
        source = PROJECT / local_path
        relative = local_path.relative_to(Path("data/raw"))
        destination = archive / relative
        if not source.is_file():
            raise FileNotFoundError(f"Static raw source is missing: {source}")
        if not destination.is_file() or destination.stat().st_size != source.stat().st_size:
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_suffix(destination.suffix + ".part")
            shutil.copy2(source, partial)
            partial.replace(destination)
        canonical_paths.add(str(relative))
        records.append({
            "artifact_id": f"{record['source_id']}:{record['sha256'][:12]}",
            "source_id": record["source_id"],
            "role": record["role"],
            "source_url": record["url"],
            "original_local_path": str(source),
            "archive_relative_path": str(Path("raw_static") / relative),
            "file_format": destination.suffix.lower().lstrip("."),
            "bytes": destination.stat().st_size,
            "sha256": record["sha256"],
            "artifact_class": "PRIMARY_SOURCE",
            "parent_artifact_id": None,
        })

    # These deterministic extracts are the exact machine-readable OSM records
    # consumed by the topology pipeline. The original PBF remains the primary
    # source; the extracts make record-level provenance queryable in DuckDB.
    pbf = next(row for row in records if row["source_id"] == "geofabrik-portugal-osm-pbf")
    supplements = [
        ("osm/portugal_power_osm.json", "osm-power-elements-extract", "complete power-element extract consumed by topology construction"),
        ("osm/generation_elements.json", "osm-generation-elements-extract", "generation subset extracted from the OSM power elements"),
        ("osm/transformer_elements.json", "osm-transformer-elements-extract", "transformer subset extracted from the OSM power elements"),
    ]
    for relative_text, source_id, role in supplements:
        if relative_text in canonical_paths:
            continue
        relative = Path(relative_text)
        source = PROJECT / "data" / "raw" / relative
        destination = archive / relative
        if not source.is_file():
            raise FileNotFoundError(f"Static OSM extract is missing: {source}")
        digest = _sha256(source)
        if not destination.is_file() or destination.stat().st_size != source.stat().st_size:
            destination.parent.mkdir(parents=True, exist_ok=True)
            partial = destination.with_suffix(destination.suffix + ".part")
            shutil.copy2(source, partial)
            partial.replace(destination)
        records.append({
            "artifact_id": f"{source_id}:{digest[:12]}",
            "source_id": source_id,
            "role": role,
            "source_url": pbf["source_url"],
            "original_local_path": str(source),
            "archive_relative_path": str(Path("raw_static") / relative),
            "file_format": "json",
            "bytes": destination.stat().st_size,
            "sha256": digest,
            "artifact_class": "DETERMINISTIC_EXTRACT",
            "parent_artifact_id": pbf["artifact_id"],
        })

    frame = pd.DataFrame(records)
    logger.info(
        "Static raw archive ready: files=%d size=%.2f MiB path=%s",
        len(frame), frame["bytes"].sum() / 2**20, archive,
    )
    return archive, frame


def import_raw_static_tables(
    con: duckdb.DuckDBPyConnection,
    archive: Path,
    artifacts: pd.DataFrame,
    logger: logging.Logger,
) -> dict[str, int]:
    """Load source-separated, unharmonized static records into raw schemas."""
    for schema in ("provenance", "raw_eredes", "raw_dgeg", "raw_osm", "raw_reference", "raw_documents"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    con.register("raw_artifact_frame", artifacts)
    con.execute("CREATE OR REPLACE TABLE provenance.raw_files AS SELECT * FROM raw_artifact_frame")

    def csv_reader(relative: str) -> str:
        return (
            f"read_csv_auto('{_sql_path(archive / relative)}', header=true, "
            "sample_size=-1, nullstr='', ignore_errors=false)"
        )

    def geojson_features(relative: str) -> str:
        return (
            f"(SELECT unnest(features) AS feature FROM read_json_auto("
            f"'{_sql_path(archive / relative)}', maximum_object_size=1000000000))"
        )

    eredes_geo = {
        "rede_at_teste": ("eredes/rede-at-teste.geojson", "feature.properties.id"),
        "substations_at_2025": ("eredes/se-at_2025.geojson", "feature.properties.codigo"),
        "delivery_points_at_2025": ("eredes/pc-at_2025.geojson", "feature.properties.codigo"),
    }
    for table, (relative, feature_key) in eredes_geo.items():
        con.execute(f"""
            CREATE OR REPLACE TABLE raw_eredes.{table} AS
            SELECT row_number() OVER () AS raw_record_id,
                   {feature_key} AS feature_id, feature.geometry,
                   feature.properties, feature AS raw_feature
            FROM {geojson_features(relative)}
        """)

    eredes_csv = {
        "network_characteristics": "eredes/caracteristicas-da-rede.csv",
        "reception_capacity": "eredes/capacidade-rececao-rnd.csv",
        "substation_capacity": "eredes/carga-na-subestacao.csv",
        "distribution_transformer_points": "eredes/postos-transformacao-distribuicao.csv",
        "load_snapshot": "eredes/load_snapshot.csv",
        "pdird_at_circuits": "eredes/pdird/pdird_2025_at_circuits.csv",
        "pdird_selected_matches": "eredes/pdird/pdird_pt60_selected_circuit_matches.csv",
    }
    for table, relative in eredes_csv.items():
        con.execute(f"""
            CREATE OR REPLACE TABLE raw_eredes.{table} AS
            SELECT row_number() OVER () AS raw_record_id, * FROM {csv_reader(relative)}
        """)

    for layer in ("CH", "CE", "CS", "CC", "CT"):
        con.execute(f"""
            CREATE OR REPLACE TABLE raw_dgeg.generation_{layer.lower()} AS
            SELECT row_number() OVER () AS raw_record_id,
                   feature.id AS feature_id, feature.geometry,
                   feature.properties, feature AS raw_feature
            FROM {geojson_features(f'dgeg/generation_{layer}.geojson')}
        """)

    osm_path = archive / "osm" / "portugal_power_osm.json"
    con.execute(f"""
        CREATE OR REPLACE TABLE raw_osm.power_elements AS
        SELECT row_number() OVER () AS raw_record_id,
               element.type AS osm_type, element.id AS osm_id,
               element.lat, element.lon, element.center, element.tags,
               element.nodes, element.geometry, element.members
        FROM (
            SELECT unnest(elements) AS element
            FROM read_json_auto('{_sql_path(osm_path)}', maximum_object_size=1000000000)
        )
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE raw_reference.portugal_boundary AS
        SELECT row_number() OVER () AS raw_record_id,
               feature.properties.CNTR_ID AS feature_id, feature.geometry,
               feature.properties, feature AS raw_feature
        FROM {geojson_features('reference/portugal_gisco_2024.geojson')}
    """)
    con.execute("""
        CREATE OR REPLACE TABLE raw_documents.documents AS
        SELECT artifact_id, source_id, role, source_url,
               archive_relative_path, file_format, bytes, sha256
        FROM provenance.raw_files
        WHERE file_format = 'pdf'
    """)
    lineage = pd.DataFrame([
        ("grid.lines", "raw_eredes.rede_at_teste", "rede-at-teste", "source_line_id / feature properties.id"),
        ("grid.lines", "raw_osm.power_elements", "geofabrik-portugal-osm-pbf", "osm_way_id / osm_id"),
        ("grid.transformers", "raw_osm.power_elements", "geofabrik-portugal-osm-pbf", "source_id / osm_type / osm_id"),
        ("grid.facilities", "raw_eredes.substations_at_2025", "se-at_2025", "facility code and spatial match"),
        ("grid.facilities", "raw_eredes.delivery_points_at_2025", "pc-at_2025", "facility code and spatial match"),
        ("grid.generators", "raw_dgeg.generation_ch", "dgeg-generation-ch", "source_id and spatial match"),
        ("grid.generators", "raw_dgeg.generation_ce", "dgeg-generation-ce", "source_id and spatial match"),
        ("grid.generators", "raw_dgeg.generation_cs", "dgeg-generation-cs", "source_id and spatial match"),
        ("grid.generators", "raw_dgeg.generation_cc", "dgeg-generation-cc", "source_id and spatial match"),
        ("grid.generators", "raw_dgeg.generation_ct", "dgeg-generation-ct", "source_id and spatial match"),
        ("grid.lines", "raw_eredes.pdird_at_circuits", "eredes-pdird-2025-at-circuits-parsed", "parameter_match_designation"),
    ], columns=["derived_table", "raw_table", "source_id", "join_rule"])
    con.register("raw_lineage_frame", lineage)
    con.execute("CREATE OR REPLACE TABLE provenance.table_lineage AS SELECT * FROM raw_lineage_frame")
    con.execute("""
        CREATE OR REPLACE VIEW provenance.raw_record_locator AS
        SELECT 'line' AS entity_type, l.line_id AS entity_id,
               'raw_eredes.rede_at_teste' AS raw_table,
               r.raw_record_id, r.feature_id AS raw_source_key
        FROM grid.lines l
        JOIN raw_eredes.rede_at_teste r
          ON split_part(l.source_line_id, ':', 2) = r.feature_id
        WHERE l.source = 'E-REDES'
        UNION ALL
        SELECT 'line', l.line_id, 'raw_osm.power_elements',
               r.raw_record_id, r.osm_type || ':' || r.osm_id::VARCHAR
        FROM grid.lines l
        JOIN raw_osm.power_elements r
          ON try_cast(l.osm_way_id AS BIGINT) = r.osm_id AND r.osm_type = 'way'
        UNION ALL
        SELECT 'transformer', t.transformer_id, 'raw_osm.power_elements',
               r.raw_record_id, r.osm_type || ':' || r.osm_id::VARCHAR
        FROM grid.transformers t
        JOIN raw_osm.power_elements r
          ON split_part(t.source_id, ':', 2) = r.osm_type
         AND try_cast(split_part(t.source_id, ':', 3) AS BIGINT) = r.osm_id
        WHERE t.source_id LIKE 'OSM:%'
        UNION ALL
        SELECT 'facility', f.facility_id, 'raw_eredes.substations_at_2025',
               r.raw_record_id, r.feature_id
        FROM grid.facilities f
        JOIN raw_eredes.substations_at_2025 r ON f.code = r.feature_id
        WHERE f.source = 'E-REDES'
        UNION ALL
        SELECT 'facility', f.facility_id, 'raw_eredes.delivery_points_at_2025',
               r.raw_record_id, r.feature_id
        FROM grid.facilities f
        JOIN raw_eredes.delivery_points_at_2025 r ON f.code = r.feature_id
        WHERE f.source = 'E-REDES'
        UNION ALL
        SELECT 'facility', f.facility_id, 'raw_osm.power_elements',
               r.raw_record_id, r.osm_type || ':' || r.osm_id::VARCHAR
        FROM grid.facilities f
        JOIN raw_osm.power_elements r
          ON split_part(f.facility_id, ':', 2) = r.osm_type
         AND try_cast(split_part(f.facility_id, ':', 3) AS BIGINT) = r.osm_id
        WHERE f.source = 'OpenStreetMap'
        UNION ALL
        SELECT 'generator', g.generator_id, 'raw_osm.power_elements',
               r.raw_record_id, r.osm_type || ':' || r.osm_id::VARCHAR
        FROM grid.generators g
        JOIN raw_osm.power_elements r
          ON split_part(g.source_id, ':', 2) = r.osm_type
         AND try_cast(split_part(g.source_id, ':', 3) AS BIGINT) = r.osm_id
        WHERE g.source_id LIKE 'OSM:%'
        UNION ALL
        SELECT 'generator', g.generator_id, 'raw_dgeg.generation_ce',
               r.raw_record_id, r.properties.processo::VARCHAR
        FROM grid.generators g
        JOIN raw_dgeg.generation_ce r
          ON try_cast(split_part(g.source_id, ':', 3) AS BIGINT) = r.properties.processo
        WHERE g.source_id LIKE 'DGEG:CE:%'
        UNION ALL
        SELECT 'generator', g.generator_id, 'raw_dgeg.generation_cs',
               r.raw_record_id, r.properties.processo::VARCHAR
        FROM grid.generators g
        JOIN raw_dgeg.generation_cs r
          ON try_cast(split_part(g.source_id, ':', 3) AS BIGINT) = r.properties.processo
        WHERE g.source_id LIKE 'DGEG:CS:%'
    """)
    con.execute("""
        CREATE OR REPLACE VIEW provenance.traceability_summary AS
        WITH entities AS (
            SELECT 'line' AS entity_type, line_id AS entity_id FROM grid.lines
            UNION ALL SELECT 'transformer', transformer_id FROM grid.transformers
            UNION ALL SELECT 'facility', facility_id FROM grid.facilities
            UNION ALL SELECT 'generator', generator_id FROM grid.generators
        ), traced AS (
            SELECT entity_type, count(DISTINCT entity_id) AS traced_entities
            FROM provenance.raw_record_locator GROUP BY entity_type
        )
        SELECT e.entity_type, count(*) AS total_entities,
               coalesce(t.traced_entities, 0) AS traced_entities,
               round(100.0 * coalesce(t.traced_entities, 0) / count(*), 2) AS traced_percent
        FROM entities e LEFT JOIN traced t USING (entity_type)
        GROUP BY e.entity_type, t.traced_entities
    """)

    counts: dict[str, int] = {}
    for schema in ("raw_eredes", "raw_dgeg", "raw_osm", "raw_reference", "raw_documents"):
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = ? AND table_type = 'BASE TABLE'",
            [schema],
        ).fetchall()
        for (table,) in tables:
            key = f"{schema}.{table}"
            counts[key] = con.execute(f"SELECT count(*) FROM {key}").fetchone()[0]
    logger.info(
        "Raw static tables imported: tables=%d records=%d OSM_elements=%d",
        len(counts), sum(counts.values()), counts["raw_osm.power_elements"],
    )
    return counts


def import_static_network_model(
    con: duckdb.DuckDBPyConnection,
    release: Path,
    logger: logging.Logger,
) -> dict[str, int]:
    """Import the versioned PT60 network, geometry, scenario, and provenance tables."""
    release = release.resolve()
    release_manifest_path = release / "manifest.json"
    source_manifest_path = release / "provenance" / "source_manifest.json"
    required = [
        release_manifest_path,
        source_manifest_path,
        release / "topology" / "facilities.csv",
        release / "topology" / "buses.csv",
        release / "topology" / "lines.csv",
        release / "topology" / "transformers.csv",
        release / "scenario" / "generators.csv",
        release / "scenario" / "loads.csv",
        release / "scenario" / "boundaries.csv",
    ]
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise FileNotFoundError("Static release is incomplete: " + ", ".join(missing))

    release_manifest = json.loads(release_manifest_path.read_text(encoding="utf-8"))
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    model_id = f"{release_manifest['dataset']}-{release_manifest['version']}"
    model_literal = model_id.replace("'", "''")

    for schema in ("grid", "geo", "scenario", "provenance"):
        con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")

    voltage_levels = json.dumps(release_manifest.get("voltage_levels_kv", []))
    model_frame = pd.DataFrame([{
        "model_id": model_id,
        "dataset": release_manifest.get("dataset"),
        "version": release_manifest.get("version"),
        "title": release_manifest.get("title"),
        "generated_at_utc": release_manifest.get("generated_at_utc"),
        "voltage_levels_kv_json": voltage_levels,
        "release_path": str(release),
    }])
    source_frame = pd.DataFrame(source_manifest.get("records", []))
    source_frame.insert(0, "model_id", model_id)
    con.register("static_model_frame", model_frame)
    con.register("static_source_frame", source_frame)
    con.execute("CREATE OR REPLACE TABLE grid.network_models AS SELECT * FROM static_model_frame")
    con.execute("CREATE OR REPLACE TABLE provenance.source_artifacts AS SELECT * FROM static_source_frame")

    csv = lambda relative: _sql_path(release / relative)
    reader = lambda relative: f"read_csv_auto('{csv(relative)}', header=true, sample_size=-1, nullstr='')"

    def optional_column(relative: str, name: str, sql_type: str = "VARCHAR") -> str:
        """Select a release field when present, otherwise provide a typed NULL.

        PT60 release schemas are append-only, so importers must continue to read
        frozen older releases after optional provenance fields are introduced.
        """
        columns = set(pd.read_csv(release / relative, nrows=0).columns)
        if name in columns:
            return f'CAST("{name}" AS {sql_type}) AS "{name}"'
        return f'NULL::{sql_type} AS "{name}"'

    con.execute(f"""
        CREATE OR REPLACE TABLE grid.facilities AS
        SELECT '{model_literal}' AS model_id, * FROM {reader('topology/facilities.csv')}
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE grid.buses AS
        SELECT '{model_literal}' AS model_id, * FROM {reader('topology/buses.csv')}
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE grid.lines AS
        SELECT '{model_literal}' AS model_id, * EXCLUDE (geometry_json)
        FROM {reader('topology/lines.csv')}
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE grid.transformers AS
        SELECT '{model_literal}' AS model_id, * FROM {reader('topology/transformers.csv')}
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE geo.line_geometries AS
        SELECT '{model_literal}' AS model_id, line_id, source_line_id, source,
               'EPSG:4326' AS crs, geometry_json
        FROM {reader('topology/lines.csv')}
    """)
    con.execute("""
        CREATE OR REPLACE TABLE geo.facility_geometries AS
        SELECT model_id, facility_id, 'EPSG:4326' AS crs, lon, lat, source
        FROM grid.facilities
    """)

    con.execute(f"""
        CREATE OR REPLACE TABLE grid.generators AS
        SELECT '{model_literal}' AS model_id,
               generator_id, source_id, osm_power_type, name, normalized_name,
               generation_source, nameplate_mw, bus_id, match_distance_m,
               source_status, lon, lat, bus_voltage_kv, bus_assignment_rule,
               tags_json, hierarchy_dedup_status,
               {optional_column('scenario/generators.csv', 'available_from_utc')},
               {optional_column('scenario/generators.csv', 'asset_evidence_source')},
               voltage_control_mode
        FROM {reader('scenario/generators.csv')}
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE grid.load_points AS
        SELECT '{model_literal}' AS model_id,
               load_id, bus_id, substation_name, facility_code,
               source_status, network_connection_status
        FROM {reader('scenario/loads.csv')}
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE grid.interconnectors AS
        SELECT '{model_literal}' AS model_id,
               component_index, bus_id, voltage_kv, interconnector_name,
               {optional_column('scenario/boundaries.csv', 'circuit_count', 'BIGINT')},
               boundary_basis
        FROM {reader('scenario/boundaries.csv')}
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE scenario.generator_operating_points AS
        SELECT '{model_literal}' AS model_id,
               generator_id, p_mw, q_mvar, dispatch_fraction, dispatch_status,
               dispatch_target_source, bus_generation_nameplate_mw
        FROM {reader('scenario/generators.csv')}
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE scenario.load_operating_points AS
        SELECT '{model_literal}' AS model_id,
               load_id, bus_id, timestamp, p_mw, q_mvar, observed_p_mw,
               ren_residual_p_mw, power_factor, reactive_power_status,
               in_service_scenario
        FROM {reader('scenario/loads.csv')}
    """)
    con.execute(f"""
        CREATE OR REPLACE TABLE scenario.boundary_operating_points AS
        SELECT '{model_literal}' AS model_id, component_index, bus_id,
               {optional_column('scenario/boundaries.csv', 'snapshot_timestamp_utc')},
               boundary_basis
        FROM {reader('scenario/boundaries.csv')}
    """)
    con.execute("""
        CREATE OR REPLACE VIEW grid.branches AS
        SELECT model_id, line_id AS branch_id, 'line' AS branch_type,
               from_bus, to_bus, voltage_kv AS from_voltage_kv,
               voltage_kv AS to_voltage_kv, in_service
        FROM grid.lines
        UNION ALL
        SELECT model_id, transformer_id, 'transformer', hv_bus, lv_bus,
               hv_kv, lv_kv, true
        FROM grid.transformers
    """)
    con.execute("""
        CREATE OR REPLACE TABLE provenance.entity_evidence AS
        SELECT model_id, 'facility' AS entity_type, facility_id AS entity_id,
               source, source_status, NULL::VARCHAR AS parameter_status,
               NULL::VARCHAR AS parameter_source
        FROM grid.facilities
        UNION ALL
        SELECT model_id, 'bus', bus_id, source, source_status, NULL, NULL
        FROM grid.buses
        UNION ALL
        SELECT model_id, 'line', line_id, source, source_status,
               parameter_status, parameter_source
        FROM grid.lines
        UNION ALL
        SELECT model_id, 'transformer', transformer_id, source_id,
               source_status, parameter_status, source_id
        FROM grid.transformers
        UNION ALL
        SELECT model_id, 'generator', generator_id, source_id,
               source_status, NULL, asset_evidence_source
        FROM grid.generators
    """)
    con.execute("""
        CREATE OR REPLACE VIEW grid.model_summary AS
        SELECT model_id, 'facilities' AS entity_type, count(*) AS row_count FROM grid.facilities GROUP BY model_id
        UNION ALL SELECT model_id, 'buses', count(*) FROM grid.buses GROUP BY model_id
        UNION ALL SELECT model_id, 'lines', count(*) FROM grid.lines GROUP BY model_id
        UNION ALL SELECT model_id, 'transformers', count(*) FROM grid.transformers GROUP BY model_id
        UNION ALL SELECT model_id, 'generators', count(*) FROM grid.generators GROUP BY model_id
        UNION ALL SELECT model_id, 'load_points', count(*) FROM grid.load_points GROUP BY model_id
        UNION ALL SELECT model_id, 'interconnectors', count(*) FROM grid.interconnectors GROUP BY model_id
    """)

    counts = dict(con.execute("SELECT entity_type, row_count FROM grid.model_summary").fetchall())
    logger.info(
        "Static network imported: model=%s facilities=%d buses=%d lines=%d transformers=%d generators=%d",
        model_id, counts["facilities"], counts["buses"], counts["lines"],
        counts["transformers"], counts["generators"],
    )
    return counts


def build_database(
    root: Path, manifest: list[dict[str, Any]], start_date: str,
    end_date: str, logger: logging.Logger, static_release: Path = DEFAULT_STATIC_RELEASE,
) -> Path:
    database = root / "pt60_public_timeseries.duckdb"
    raw_archive, raw_artifacts = archive_static_raw_files(root, static_release, logger)
    # Build a fresh file and replace the catalog only after success. Repeated
    # CREATE OR REPLACE operations in an existing DuckDB retain obsolete pages
    # and can nearly double the file on each rebuild.
    building = database.with_suffix(database.suffix + ".building")
    building.unlink(missing_ok=True)
    con = duckdb.connect(str(building))
    try:
        manifest_frame = pd.DataFrame(manifest)
        calendar = _interval_calendar(start_date, end_date)
        con.register("manifest_frame", manifest_frame)
        con.register("calendar_frame", calendar)
        con.execute("CREATE OR REPLACE TABLE dataset_files AS SELECT * FROM manifest_frame")
        con.execute("CREATE OR REPLACE TABLE interval_calendar AS SELECT * FROM calendar_frame")
        import_static_network_model(con, static_release, logger)
        import_raw_static_tables(con, raw_archive, raw_artifacts, logger)
        sources = {
            # Explicit non-dot filename prefixes exclude AppleDouble ``._*``
            # files created when the data lake lives on an exFAT SD card.
            "eredes_load": root / "parquet" / "eredes_load" / "year=*" / "month=*" / "dataset=*" / "[0-9]*.parquet",
            "ren_dispatch": root / "parquet" / "ren_dispatch" / "year=*" / "month=*" / "[0-9]*.parquet",
            "weather_hourly": root / "parquet" / "weather" / "location=*" / "[0-9]*.parquet",
            "ren_rnt_balance": root / "parquet" / "ren_rnt_balance" / "year=*" / "month=*" / "balance_*.parquet",
        }
        for table, pattern in sources.items():
            sql_path = str(pattern).replace("'", "''")
            con.execute(f"CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_parquet('{sql_path}', union_by_name=true, hive_partitioning=true)")
        con.execute("""
            CREATE OR REPLACE VIEW eredes_snapshot_totals AS
            SELECT data AS source_date, hora AS source_hour,
                   sum(try_cast(energia AS DOUBLE)) / 250.0 AS evidenced_load_mw,
                   count(*) AS source_record_count
            FROM eredes_load GROUP BY data, hora
        """)
        con.execute("""
            CREATE OR REPLACE VIEW coverage_summary AS
            SELECT 'E-REDES' AS source, min(data::DATE) AS first_date, max(data::DATE) AS last_date, count(*) AS rows FROM eredes_load
            UNION ALL SELECT 'REN', min(source_date), max(source_date), count(*) FROM ren_dispatch
            UNION ALL SELECT 'Open-Meteo', min(time::TIMESTAMP)::DATE, max(time::TIMESTAMP)::DATE, count(*) FROM weather_hourly
            UNION ALL SELECT 'REN RNT', min(benchmark_date), max(benchmark_date), count(*) FROM ren_rnt_balance
        """)
        con.execute("CHECKPOINT")
    finally:
        con.close()
    building.replace(database)
    logger.info("DuckDB built: %s (%.2f MiB)", database, database.stat().st_size / 2**20)
    return database


def _tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument(
        "--static-release", type=Path, default=DEFAULT_STATIC_RELEASE,
        help="Versioned PT60 release whose static network model is imported into DuckDB",
    )
    parser.add_argument("--phase", choices=("all", "download", "build"), default="all")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--request-delay", type=float, default=0.25)
    parser.add_argument("--rate-limit-backoff", type=float, default=60.0)
    args = parser.parse_args()
    if pd.Timestamp(args.end_date) < pd.Timestamp(args.start_date):
        parser.error("end date must be on or after start date")
    args.data_root.mkdir(parents=True, exist_ok=True)
    logger = _configure_logging(args.data_root)
    started = time.monotonic()
    logger.info("PT60 download-only database: %s through %s; root=%s; phase=%s", args.start_date, args.end_date, args.data_root, args.phase)
    manifest_path = args.data_root / "manifest.json"
    manifest: list[dict[str, Any]] = []
    if args.phase in {"all", "download"}:
        manifest.extend(download_eredes(args.data_root, args.start_date, args.end_date, args.refresh, args.request_delay, args.rate_limit_backoff, logger))
        write_json(manifest_path, {"files": manifest})
        manifest.extend(download_ren(args.data_root, args.start_date, args.end_date, args.refresh, args.request_delay, args.rate_limit_backoff, logger))
        write_json(manifest_path, {"files": manifest})
        manifest.extend(download_weather(args.data_root, args.start_date, args.end_date, args.refresh, args.rate_limit_backoff, logger))
        write_json(manifest_path, {"files": manifest})
        manifest.extend(download_rnt(args.data_root, args.start_date, args.end_date, args.refresh, args.rate_limit_backoff, logger))
        write_json(manifest_path, {"files": manifest})
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))["files"]
    database = None
    if args.phase in {"all", "build"}:
        database = build_database(
            args.data_root, manifest, args.start_date, args.end_date, logger,
            static_release=args.static_release,
        )
    summary = {
        "created_at_utc": utc_now(), "start_date": args.start_date, "end_date": args.end_date,
        "phase": args.phase, "duration_seconds": time.monotonic() - started,
        "file_count": len(manifest), "data_root": str(args.data_root.resolve()),
        "database": str(database.resolve()) if database else None,
        "total_bytes": _tree_bytes(args.data_root),
    }
    write_json(args.data_root / "summary.json", summary)
    logger.info("COMPLETE duration=%.1f min files=%d total=%.2f MiB", summary["duration_seconds"] / 60, len(manifest), summary["total_bytes"] / 2**20)


if __name__ == "__main__":
    main()
