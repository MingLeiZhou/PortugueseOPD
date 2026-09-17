#!/usr/bin/env python3
"""Archive selected E-REDES evidence datasets and register them in PT60 DuckDB.

The script performs no power-flow modelling.  It preserves the downloaded
Parquet files, records hashes and source URLs in a JSON manifest, and imports
the latest verified snapshot of each dataset into ``raw_eredes_aux``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import requests


API_BASE = "https://e-redes.opendatasoft.com/api/explore/v2.1/catalog/datasets"
SOURCE_TIMEZONE = "Europe/Lisbon"


CORE_DATASETS: dict[str, dict[str, str]] = {
    "caracteristicas-da-rede": {
        "role": "electrical_parameter_validation",
        "description": "Substation ratings, voltage ratios and short-circuit context.",
    },
    "carga-na-subestacao": {
        "role": "substation_capacity_validation",
        "description": "Reference substation loads and capacity restrictions.",
    },
    "capacidade-rececao-rnd": {
        "role": "generation_hosting_context",
        "description": "RND generation hosting capacity by substation and area.",
    },
    "3-consumos-faturados-por-municipio-ultimos-10-anos": {
        "role": "spatial_energy_validation",
        "description": "Monthly billed energy by municipality and voltage level.",
    },
    "20-caracterizacao-pes-contrato-ativo": {
        "role": "load_allocation_context",
        "description": "Active consumption points by location, installation and voltage level.",
    },
    "potencia-contratada-contratos-ativos-municipio": {
        "role": "load_allocation_context",
        "description": "Contracted power and active contracts by location.",
    },
    "consumo-total-nacional": {
        "role": "cross_source_system_validation",
        "description": "National consumption split by voltage level.",
    },
    "energia-produzida-total-nacional": {
        "role": "cross_source_system_validation",
        "description": "National produced energy aggregates.",
    },
    "energia-injetada-na-rede-de-distribuicao": {
        "role": "cross_source_system_validation",
        "description": "Energy injected into the distribution network by origin.",
    },
    "qualidade_energia_fenomenoscontinuos-final": {
        "role": "held_out_voltage_context",
        "description": "Voltage-quality monitoring at HV/MV and MV/LV interfaces.",
    },
    "12-continuidade-de-servico-indicadores-gerais-de-continuidade-de-servico": {
        "role": "risk_outcome_context",
        "description": "Municipal SAIFI, SAIDI, MAIFI, TIEPI and energy-not-supplied indicators.",
    },
    "network-scheduling-work": {
        "role": "outage_scenario_context",
        "description": "Scheduled distribution-network maintenance windows.",
    },
    "8-total-upac-mensal": {
        "role": "embedded_generation_context",
        "description": "Monthly UPAC count and installed capacity by location and voltage.",
    },
    "energia_injectada_upac": {
        "role": "embedded_generation_context",
        "description": "Energy injected by self-consumption production units.",
    },
}


EXTENDED_DATASETS: dict[str, dict[str, str]] = {
    "consumos_horario_codigo_postal": {
        "role": "fine_spatial_load_context",
        "description": "Hourly consumption by four-digit postal code.",
    },
    "clientes-por-escalao-de-potencia": {
        "role": "consumer_structure_context",
        "description": "Customers grouped by contracted or maximum-demand bands.",
    },
    "consumos-faturados-por-periodo-tarifario": {
        "role": "consumer_structure_context",
        "description": "Municipal consumption by tariff period.",
    },
    "postos_carregamento_ves": {
        "role": "future_demand_context",
        "description": "EV charging connection points and admissible connection power.",
    },
    "consumo_horario_mobilidade_eletrica": {
        "role": "future_demand_context",
        "description": "Hourly public electric-mobility consumption.",
    },
    "previsao-de-consumo": {
        "role": "future_scenario_context",
        "description": "E-REDES consumption information and forecast.",
    },
    "25-plr-producao-renovavel": {
        "role": "future_generation_context",
        "description": "New renewable generation grid connections and connection power.",
    },
    "26-centrais": {
        "role": "future_generation_context",
        "description": "New UPAC installations and connection power.",
    },
    "postos-transformacao-distribuicao": {
        "role": "downstream_network_context",
        "description": "MV/LV secondary-substation location, installed power and utilization.",
    },
}


def utc_now() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat()


def configure_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("pt60_eredes_auxiliary")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def table_name(dataset_id: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "_", dataset_id.lower()).strip("_")
    if not name or name[0].isdigit():
        name = "dataset_" + name
    return name


def selected_datasets(profile: str, explicit: list[str]) -> dict[str, dict[str, str]]:
    if explicit:
        known = {**CORE_DATASETS, **EXTENDED_DATASETS}
        return {
            dataset_id: known.get(dataset_id, {
                "role": "user_selected_context",
                "description": "Dataset explicitly selected on the command line.",
            })
            for dataset_id in explicit
        }
    datasets = dict(CORE_DATASETS)
    if profile == "extended":
        datasets.update(EXTENDED_DATASETS)
    return datasets


def request_stream(
    session: requests.Session, url: str, destination: Path,
    backoff: float, logger: logging.Logger,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    for attempt in range(1, 5):
        try:
            with session.get(
                url,
                params={
                    "lang": "pt",
                    "timezone": SOURCE_TIMEZONE,
                    "parquet_compression": "zstd",
                },
                headers={"User-Agent": "PT60 E-REDES evidence archiver/1.0"},
                stream=True,
                timeout=(30, 1800),
            ) as response:
                response.raise_for_status()
                downloaded = 0
                next_report = 64 * 2**20
                with partial.open("wb") as handle:
                    for chunk in response.iter_content(8 * 1024 * 1024):
                        if not chunk:
                            continue
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if downloaded >= next_report:
                            logger.info("  downloaded %.1f MiB", downloaded / 2**20)
                            next_report += 64 * 2**20
            partial.replace(destination)
            return
        except (requests.RequestException, OSError) as exc:
            partial.unlink(missing_ok=True)
            if attempt == 4:
                raise
            cooldown = backoff * attempt
            logger.warning(
                "Request failed (%s); retry %d/4 after %.0fs",
                type(exc).__name__, attempt + 1, cooldown,
            )
            time.sleep(cooldown)


def parquet_stats(path: Path) -> tuple[int, list[str]]:
    connection = duckdb.connect(":memory:")
    try:
        sql_path = str(path).replace("'", "''")
        rows = int(connection.execute(
            f"SELECT count(*) FROM read_parquet('{sql_path}')"
        ).fetchone()[0])
        columns = [row[0] for row in connection.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{sql_path}')"
        ).fetchall()]
    finally:
        connection.close()
    if rows == 0:
        raise ValueError(f"Downloaded Parquet contains no rows: {path}")
    return rows, columns


def load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return list(payload.get("files", []))


def write_manifest(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(path.suffix + ".part")
    partial.write_text(
        json.dumps({"updated_at_utc": utc_now(), "files": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    partial.replace(path)


def download_datasets(
    archive_root: Path, manifest_path: Path,
    datasets: dict[str, dict[str, str]], refresh: bool,
    delay: float, backoff: float, logger: logging.Logger,
) -> list[dict[str, Any]]:
    manifest = load_manifest(manifest_path)
    session = requests.Session()
    for index, (dataset_id, config) in enumerate(datasets.items(), start=1):
        url = f"{API_BASE}/{dataset_id}/exports/parquet"
        existing = sorted(
            (row for row in manifest if row.get("dataset_id") == dataset_id),
            key=lambda row: row.get("checked_at_utc", ""),
        )
        cached_record = existing[-1] if existing and not refresh else None
        cached_path = None
        if cached_record:
            candidate = Path(cached_record["relative_path"])
            cached_path = candidate if candidate.is_absolute() else manifest_path.parent / candidate
            if not cached_path.exists():
                cached_record = None
        snapshot_stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%dT%H%M%SZ")
        destination = (
            cached_path if cached_record else
            archive_root / f"dataset={dataset_id}" / f"snapshot={snapshot_stamp}.parquet"
        )
        cached = cached_record is not None
        logger.info(
            "[%02d/%02d] %s %s", index, len(datasets), dataset_id,
            "CACHE" if cached else "DOWNLOAD",
        )
        if not cached:
            request_stream(session, url, destination, backoff, logger)
        rows, columns = parquet_stats(destination)
        record = {
            "source": "E-REDES",
            "dataset_id": dataset_id,
            "table_name": table_name(dataset_id),
            "role": config["role"],
            "description": config["description"],
            "source_url": url,
            "license": "CC BY 4.0",
            "snapshot_date_utc": snapshot_stamp,
            "checked_at_utc": utc_now(),
            "relative_path": str(
                destination.relative_to(manifest_path.parent)
                if destination.is_relative_to(manifest_path.parent)
                else destination.resolve()
            ),
            "bytes": destination.stat().st_size,
            "sha256": sha256(destination),
            "row_count": rows,
            "column_names": columns,
            "cached": cached,
        }
        if cached_record:
            manifest[manifest.index(cached_record)] = record
        else:
            manifest.append(record)
        write_manifest(
            manifest_path,
            sorted(manifest, key=lambda row: (row["dataset_id"], row.get("checked_at_utc", ""))),
        )
        logger.info(
            "  rows=%d columns=%d size=%.2f MiB sha256=%s",
            rows, len(columns), destination.stat().st_size / 2**20, record["sha256"][:12],
        )
        if delay and not cached:
            time.sleep(delay)
    return sorted(manifest, key=lambda row: (row["dataset_id"], row.get("checked_at_utc", "")))


def latest_selected_records(
    manifest: list[dict[str, Any]], datasets: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    selected = []
    for dataset_id in datasets:
        matches = [row for row in manifest if row.get("dataset_id") == dataset_id]
        if not matches:
            raise FileNotFoundError(
                f"No archived snapshot for {dataset_id}; run with --phase download or --phase all first"
            )
        selected.append(max(matches, key=lambda row: row.get("checked_at_utc", "")))
    return selected


def import_into_duckdb(
    database: Path, manifest_root: Path, records: list[dict[str, Any]],
    logger: logging.Logger,
) -> None:
    if not database.exists():
        raise FileNotFoundError(f"PT60 database does not exist: {database}")
    connection = duckdb.connect(str(database))
    try:
        connection.execute("CREATE SCHEMA IF NOT EXISTS raw_eredes_aux")
        connection.execute("CREATE SCHEMA IF NOT EXISTS provenance")
        connection.execute("CREATE SCHEMA IF NOT EXISTS validation")
        connection.execute("BEGIN TRANSACTION")
        imported_at = utc_now()
        catalog_rows = []
        for index, record in enumerate(records, start=1):
            path = manifest_root / record["relative_path"]
            if not path.exists():
                raise FileNotFoundError(path)
            actual_hash = sha256(path)
            if actual_hash != record["sha256"]:
                raise ValueError(f"SHA-256 mismatch for {path}")
            sql_path = str(path).replace("'", "''")
            table = record["table_name"]
            constants = {
                "dataset_id": record["dataset_id"],
                "source_url": record["source_url"],
                "snapshot_sha256": record["sha256"],
                "archived_at_utc": record["checked_at_utc"],
            }
            escaped = {key: str(value).replace("'", "''") for key, value in constants.items()}
            connection.execute(f"""
                CREATE OR REPLACE TABLE raw_eredes_aux.{table} AS
                SELECT *,
                       '{escaped['dataset_id']}'::VARCHAR AS _pt60_dataset_id,
                       '{escaped['source_url']}'::VARCHAR AS _pt60_source_url,
                       '{escaped['snapshot_sha256']}'::VARCHAR AS _pt60_snapshot_sha256,
                       '{escaped['archived_at_utc']}'::TIMESTAMPTZ AS _pt60_archived_at_utc
                FROM read_parquet('{sql_path}')
            """)
            imported_rows = int(connection.execute(
                f"SELECT count(*) FROM raw_eredes_aux.{table}"
            ).fetchone()[0])
            if imported_rows != int(record["row_count"]):
                raise ValueError(
                    f"Row-count mismatch for {record['dataset_id']}: "
                    f"manifest={record['row_count']} imported={imported_rows}"
                )
            catalog_rows.append({
                **record,
                "column_names": json.dumps(record["column_names"], ensure_ascii=False),
                "absolute_path": str(path.resolve()),
                "imported_at_utc": imported_at,
            })
            logger.info(
                "[IMPORT %02d/%02d] raw_eredes_aux.%s rows=%d",
                index, len(records), table, imported_rows,
            )
        frame = pd.DataFrame(catalog_rows)
        connection.register("eredes_auxiliary_catalog_frame", frame)
        connection.execute("""
            CREATE OR REPLACE TABLE provenance.eredes_auxiliary_files AS
            SELECT * FROM eredes_auxiliary_catalog_frame
        """)
        connection.execute("""
            CREATE OR REPLACE VIEW validation.eredes_auxiliary_catalog AS
            SELECT dataset_id, table_name, role, description, source_url,
                   license, snapshot_date_utc, checked_at_utc, row_count,
                   bytes, sha256, column_names
            FROM provenance.eredes_auxiliary_files
            ORDER BY dataset_id
        """)
        connection.execute("COMMIT")
        connection.execute("CHECKPOINT")
    except Exception:
        try:
            connection.execute("ROLLBACK")
        except duckdb.Error:
            pass
        raise
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True, help="Existing PT60 DuckDB file")
    parser.add_argument(
        "--archive-root", type=Path,
        help="Archive root; defaults to DATABASE_PARENT/parquet/eredes_auxiliary",
    )
    parser.add_argument("--profile", choices=("core", "extended"), default="core")
    parser.add_argument(
        "--dataset", action="append", default=[],
        help="Archive only this dataset ID; repeat for multiple datasets",
    )
    parser.add_argument("--phase", choices=("all", "download", "build"), default="all")
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--request-delay", type=float, default=0.5)
    parser.add_argument("--rate-limit-backoff", type=float, default=30.0)
    args = parser.parse_args()

    database = args.database.expanduser().resolve()
    archive_root = (
        args.archive_root.expanduser().resolve()
        if args.archive_root
        else database.parent / "parquet" / "eredes_auxiliary"
    )
    manifest_path = database.parent / "eredes_auxiliary_manifest.json"
    logger = configure_logging(database.parent / "eredes_auxiliary.log")
    datasets = selected_datasets(args.profile, args.dataset)
    logger.info(
        "E-REDES auxiliary archive started: profile=%s datasets=%d phase=%s",
        args.profile, len(datasets), args.phase,
    )
    started = time.monotonic()
    if args.phase in {"all", "download"}:
        manifest = download_datasets(
            archive_root, manifest_path, datasets, args.refresh,
            args.request_delay, args.rate_limit_backoff, logger,
        )
    else:
        manifest = load_manifest(manifest_path)
    records = latest_selected_records(manifest, datasets)
    if args.phase in {"all", "build"}:
        import_into_duckdb(database, manifest_path.parent, records, logger)
    logger.info(
        "COMPLETE duration=%.1f min datasets=%d archived=%.2f MiB database=%s",
        (time.monotonic() - started) / 60,
        len(records), sum(int(row["bytes"]) for row in records) / 2**20,
        database,
    )


if __name__ == "__main__":
    main()
