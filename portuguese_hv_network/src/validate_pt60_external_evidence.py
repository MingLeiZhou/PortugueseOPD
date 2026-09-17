#!/usr/bin/env python3
"""Cross-validate PT60 temporal models with archived E-REDES evidence.

The source database and all monthly model databases are opened read-only.  The
script produces a separate validation database, CSV extracts, publication-size
figures, a figure manifest, and a Markdown report.

Validation domains
------------------
1. Load scale: REN load used by PT60 vs E-REDES national consumption.
2. Temporal shape: 15-minute, daily, monthly, and time-of-day agreement.
3. Generation/balance: REN generation vs E-REDES production and AC closure.
4. Spatial allocation: PT60 substation profiles vs municipal billed energy and
   the independent E-REDES seasonal substation-load reference.

The script deliberately calls these cross-source diagnostics, not ground-truth
accuracy estimates.  Scope differences and evidence dependence are recorded in
the output tables and report.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import logging
import math
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import duckdb
import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_ROOT = Path("/Volumes/Transcend/PT60_public_data_2025-05-01_2026-03-24")
DEFAULT_OUTPUT = Path("portuguese_hv_network/outputs/external_evidence_validation")
OKABE_ITO = {
    "black": "#000000",
    "orange": "#E69F00",
    "sky": "#56B4E9",
    "green": "#009E73",
    "blue": "#0072B2",
    "red": "#D55E00",
    "purple": "#CC79A7",
    "gray": "#777777",
}
CAPACITY_CODE_ALIASES = {
    "1107S5901900": "1107P5728100",
    "1512S5031200": "1512P5903900",
    "1808S5009500": "1808P5020500",
}


@dataclass(frozen=True)
class Paths:
    source_database: Path
    monthly_root: Path
    auxiliary_manifest: Path
    output_dir: Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate PT60 against archived E-REDES auxiliary evidence."
    )
    parser.add_argument(
        "--source-database",
        type=Path,
        default=DEFAULT_ROOT / "pt60_public_timeseries.duckdb",
    )
    parser.add_argument(
        "--monthly-root", type=Path, default=DEFAULT_ROOT / "monthly_models"
    )
    parser.add_argument(
        "--auxiliary-manifest",
        type=Path,
        default=DEFAULT_ROOT / "eredes_auxiliary_manifest.json",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--months",
        nargs="*",
        help="Optional YYYY-MM subset. Default: use every compact monthly database.",
    )
    parser.add_argument(
        "--dpi", type=int, default=300, help="PNG preview resolution (default: 300)."
    )
    return parser.parse_args()


def configure_logging(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("pt60_external_validation")
    logger.handlers.clear()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    file_handler = logging.FileHandler(output_dir / "execution.log", encoding="utf-8")
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


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"{label} not found: {path}")


def month_from_path(path: Path) -> str:
    match = re.search(r"pt60_models_(\d{4}-\d{2})\.compact\.duckdb$", path.name)
    if not match:
        raise ValueError(f"Unexpected monthly database name: {path}")
    return match.group(1)


def discover_monthly_databases(root: Path, months: list[str] | None) -> list[Path]:
    candidates = sorted(
        path for path in root.glob("*/pt60_models_*.compact.duckdb")
        if not path.name.startswith("._")
    )
    if months:
        requested = set(months)
        invalid = sorted(month for month in requested if not re.fullmatch(r"\d{4}-\d{2}", month))
        if invalid:
            raise ValueError(f"Invalid --months values: {invalid}")
        candidates = [path for path in candidates if month_from_path(path) in requested]
        missing = requested - {month_from_path(path) for path in candidates}
        if missing:
            raise FileNotFoundError(f"No compact monthly database for: {sorted(missing)}")
    if not candidates:
        raise FileNotFoundError(f"No compact monthly databases below {root}")
    return candidates


def read_model_cases(paths: list[Path], logger: logging.Logger) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        connection = duckdb.connect(str(path), read_only=True)
        try:
            connection.execute("SET TimeZone='UTC'")
            manifest = connection.execute("""
                SELECT month, expected_case_count, completed_case_count,
                       failed_case_count, status
                FROM monthly_model.run_manifest
                ORDER BY updated_at_utc DESC LIMIT 1
            """).fetchone()
            frame = connection.execute("""
                SELECT case_id, timestamp_utc, local_date, status, converged,
                       observed_load_mw, observed_generation_total_mw,
                       observed_net_import_mw, model_net_import_mw,
                       model_losses_percent_of_load,
                       maximum_line_loading_percent,
                       maximum_transformer_loading_percent
                FROM monthly_model.cases
                ORDER BY timestamp_utc
            """).fetchdf()
        finally:
            connection.close()
        if manifest is None:
            raise ValueError(f"Missing monthly_model.run_manifest in {path}")
        month, expected, complete, failed, run_status = manifest
        actual_complete = int((frame["status"] == "COMPLETE").sum())
        if actual_complete != int(complete):
            raise ValueError(
                f"Manifest/case mismatch for {month}: {complete} vs {actual_complete}"
            )
        logger.info(
            "Model month %s: cases=%d complete=%d failed=%d status=%s",
            month, int(expected), int(complete), int(failed), run_status,
        )
        frame["source_month"] = str(month)
        frame["source_database"] = str(path.resolve())
        frames.append(frame)
    cases = pd.concat(frames, ignore_index=True)
    cases["timestamp_utc"] = pd.to_datetime(cases["timestamp_utc"], utc=True)
    if cases["case_id"].duplicated().any() or cases["timestamp_utc"].duplicated().any():
        raise ValueError("Monthly databases contain duplicate case IDs or timestamps")
    cases = cases[(cases["status"] == "COMPLETE") & cases["converged"].fillna(False)].copy()
    if cases.empty:
        raise ValueError("No complete converged PT60 cases found")
    return cases.sort_values("timestamp_utc").reset_index(drop=True)


def query_frame(connection: duckdb.DuckDBPyConnection, sql: str) -> pd.DataFrame:
    return connection.execute(sql).fetchdf()


def read_external_timeseries(connection: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    # The Opendatasoft TIMESTAMPTZ export collapses some daylight-saving wall
    # labels onto the same instant.  Align through PT60's audited local-label
    # calendar instead.  Repeated autumn labels have no fold marker in the
    # public source, so their values are averaged and assigned to both folds,
    # matching the monthly model's declared DST policy.
    frame = query_frame(connection, """
        WITH consumption AS (
            SELECT date, time,
                   avg(total) / 250.0 AS external_load_mw,
                   avg(bt) / 250.0 AS external_bt_mw,
                   avg(mt) / 250.0 AS external_mt_mw,
                   avg("at") / 250.0 AS external_at_mw,
                   avg(mat) / 250.0 AS external_mat_mw,
                   count(*) AS external_consumption_source_rows
            FROM raw_eredes_aux.consumo_total_nacional
            GROUP BY date, time
        ), production AS (
            SELECT date, time,
                   avg(total) / 250.0 AS external_production_mw,
                   avg(dgm) / 250.0 AS external_dgm_mw,
                   avg(pre) / 250.0 AS external_pre_mw,
                   count(*) AS external_production_source_rows
            FROM raw_eredes_aux.energia_produzida_total_nacional
            GROUP BY date, time
        ), injection AS (
            SELECT date, time,
                   avg(rede_dist) / 250.0 AS external_distribution_injection_mw,
                   avg(cogeracao) / 250.0 AS external_cogeneration_mw,
                   avg(eolica) / 250.0 AS external_wind_mw,
                   avg(fotovoltaica) / 250.0 AS external_solar_mw,
                   avg(hidrica) / 250.0 AS external_hydro_mw,
                   avg(outras_tecnologias) / 250.0 AS external_other_mw,
                   count(*) AS external_injection_source_rows
            FROM raw_eredes_aux.energia_injetada_na_rede_de_distribuicao
            GROUP BY date, time
        )
        SELECT CAST(calendar.timestamp_utc AS TIMESTAMPTZ) AS timestamp_utc,
               calendar.eredes_alignment_status AS external_alignment_status,
               consumption.* EXCLUDE (date, time),
               production.* EXCLUDE (date, time),
               injection.* EXCLUDE (date, time)
        FROM main.interval_calendar AS calendar
        LEFT JOIN consumption
          ON consumption.date = CAST(calendar.eredes_source_date AS DATE)
         AND consumption.time = substr(calendar.eredes_source_hour, 1, 5)
        LEFT JOIN production
          ON production.date = CAST(calendar.eredes_source_date AS DATE)
         AND production.time = substr(calendar.eredes_source_hour, 1, 5)
        LEFT JOIN injection
          ON injection.date = CAST(calendar.eredes_source_date AS DATE)
         AND injection.time = substr(calendar.eredes_source_hour, 1, 5)
        ORDER BY timestamp_utc
    """)
    frame["timestamp_utc"] = pd.to_datetime(frame["timestamp_utc"], utc=True)
    if frame["timestamp_utc"].duplicated().any():
        raise ValueError("Audited interval calendar produced duplicate external timestamps")
    return frame


def read_municipality_comparison(
    connection: duckdb.DuckDBPyConnection, start: pd.Timestamp, end: pd.Timestamp,
) -> pd.DataFrame:
    frame = connection.execute("""
        WITH model AS (
            SELECT date_trunc('month', data)::DATE AS month,
                   codigo_concelho AS municipality_code,
                   min(concelho) AS municipality_name,
                   sum(energia) AS pt60_substation_energy_kwh
            FROM main.eredes_load
            WHERE data >= ? AND data <= ?
              AND regexp_matches(codigo_concelho, '^[0-9]{4}$')
            GROUP BY 1, 2
        ), billed AS (
            SELECT data AS month, coddistritoconcelho AS municipality_code,
                   min(concelho) AS billed_municipality_name,
                   sum(energia_ativa_kwh) AS billed_energy_kwh
            FROM raw_eredes_aux.dataset_3_consumos_faturados_por_municipio_ultimos_10_anos
            WHERE data >= date_trunc('month', ?::DATE)
              AND data <= date_trunc('month', ?::DATE)
              AND regexp_matches(coddistritoconcelho, '^[0-9]{4}$')
            GROUP BY 1, 2
        )
        SELECT coalesce(model.month, billed.month) AS month,
               coalesce(model.municipality_code, billed.municipality_code) AS municipality_code,
               coalesce(model.municipality_name, billed.billed_municipality_name) AS municipality_name,
               model.pt60_substation_energy_kwh,
               billed.billed_energy_kwh,
               model.pt60_substation_energy_kwh IS NOT NULL AS represented_by_pt60_substation,
               billed.billed_energy_kwh IS NOT NULL AS present_in_billed_dataset
        FROM model
        FULL OUTER JOIN billed USING (month, municipality_code)
        ORDER BY month, municipality_code
    """, [start.date(), end.date(), start.date(), end.date()]).fetchdf()
    frame["month"] = pd.to_datetime(frame["month"])
    for column in ("pt60_substation_energy_kwh", "billed_energy_kwh"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["matched"] = frame["represented_by_pt60_substation"] & frame["present_in_billed_dataset"]
    matched = frame[frame["matched"]].copy()
    model_denominator = matched.groupby("month")["pt60_substation_energy_kwh"].transform("sum")
    billed_denominator = matched.groupby("month")["billed_energy_kwh"].transform("sum")
    frame["pt60_share_within_matched"] = np.nan
    frame["billed_share_within_matched"] = np.nan
    frame.loc[matched.index, "pt60_share_within_matched"] = (
        matched["pt60_substation_energy_kwh"] / model_denominator
    )
    frame.loc[matched.index, "billed_share_within_matched"] = (
        matched["billed_energy_kwh"] / billed_denominator
    )
    return frame


def read_substation_comparison(
    connection: duckdb.DuckDBPyConnection, start: pd.Timestamp, end: pd.Timestamp,
) -> pd.DataFrame:
    profiles = connection.execute("""
        SELECT codigo_subestacao AS profile_code,
               min(subestacao) AS substation_name,
               CASE WHEN month(data) BETWEEN 5 AND 9 THEN 'Summer' ELSE 'Winter' END AS season,
               max(energia / 250.0) AS pt60_observed_peak_mw,
               avg(energia / 250.0) AS pt60_observed_mean_mw,
               count(*) AS interval_count
        FROM main.eredes_load
        WHERE data >= ? AND data <= ?
        GROUP BY 1, 3
    """, [start.date(), end.date()]).fetchdf()
    reference = query_frame(connection, """
        SELECT codigo_da_instalacao AS reference_code,
               min(nome) AS reference_name,
               CASE WHEN lower(inverno_verao) LIKE 'ver%' THEN 'Summer' ELSE 'Winter' END AS season,
               avg(carga_natural) AS reference_natural_load_mw,
               avg(potencia_instalada) AS installed_capacity_mva,
               count(*) AS reference_row_count
        FROM raw_eredes_aux.carga_na_subestacao
        WHERE TRY_CAST(ano AS INTEGER) = 2025
        GROUP BY 1, 3
    """)
    profiles["join_code"] = profiles["profile_code"].replace(CAPACITY_CODE_ALIASES)
    comparison = profiles.merge(
        reference, left_on=["join_code", "season"], right_on=["reference_code", "season"],
        how="outer", validate="many_to_one", indicator=True,
    )
    comparison["matched"] = comparison["_merge"] == "both"
    comparison.drop(columns=["_merge"], inplace=True)
    return comparison.sort_values(["season", "join_code"], na_position="last").reset_index(drop=True)


def finite_pairs(frame: pd.DataFrame, left: str, right: str) -> pd.DataFrame:
    values = frame[[left, right]].replace([np.inf, -np.inf], np.nan).dropna()
    return values.astype(float)


def paired_statistics(
    frame: pd.DataFrame, model_column: str, reference_column: str,
) -> dict[str, float | int]:
    pairs = finite_pairs(frame, model_column, reference_column)
    if len(pairs) < 2:
        return {"n": len(pairs)}
    model = pairs[model_column]
    reference = pairs[reference_column]
    error = model - reference
    nonzero = reference.abs() > 1e-12
    return {
        "n": int(len(pairs)),
        "model_mean": float(model.mean()),
        "reference_mean": float(reference.mean()),
        "mean_ratio": float(model.mean() / reference.mean()) if reference.mean() else math.nan,
        "bias": float(error.mean()),
        "mae": float(error.abs().mean()),
        "rmse": float(np.sqrt(np.mean(np.square(error)))),
        "nrmse_reference_mean": float(np.sqrt(np.mean(np.square(error))) / abs(reference.mean())) if reference.mean() else math.nan,
        "mape_percent": float((error[nonzero].abs() / reference[nonzero].abs()).mean() * 100.0),
        "pearson_r": float(model.corr(reference, method="pearson")),
        "spearman_rho": float(model.rank(method="average").corr(reference.rank(method="average"))),
    }


def add_metrics(
    rows: list[dict[str, Any]], domain: str, stats: dict[str, Any],
    units: dict[str, str], evidence_level: str, comparison: str,
) -> None:
    for metric, value in stats.items():
        rows.append({
            "domain": domain,
            "metric": metric,
            "value": None if pd.isna(value) else float(value),
            "unit": units.get(metric, "dimensionless"),
            "sample_count": int(stats.get("n", 0)),
            "evidence_level": evidence_level,
            "comparison": comparison,
        })


def compute_results(
    cases: pd.DataFrame,
    external: pd.DataFrame,
    municipality: pd.DataFrame,
    substation: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    timeseries = cases.merge(external, on="timestamp_utc", how="left", validate="one_to_one")
    timeseries["model_losses_mw"] = (
        timeseries["observed_load_mw"] * timeseries["model_losses_percent_of_load"] / 100.0
    )
    timeseries["model_ac_balance_closure_mw"] = (
        timeseries["observed_generation_total_mw"]
        + timeseries["model_net_import_mw"]
        - timeseries["observed_load_mw"]
        - timeseries["model_losses_mw"]
    )
    timeseries["ren_input_balance_residual_mw"] = (
        timeseries["observed_generation_total_mw"]
        + timeseries["observed_net_import_mw"]
        - timeseries["observed_load_mw"]
    )
    timeseries["external_balance_gap_mw"] = (
        timeseries["external_production_mw"] - timeseries["external_load_mw"]
    )
    timeseries["external_injection_identity_error_mw"] = (
        timeseries["external_pre_mw"] - timeseries["external_distribution_injection_mw"]
    )
    timeseries["local_time"] = timeseries["timestamp_utc"].dt.tz_convert("Europe/Lisbon")
    timeseries["local_date"] = timeseries["local_time"].dt.date
    timeseries["month"] = timeseries["local_time"].dt.tz_localize(None).dt.to_period("M").astype(str)
    timeseries["minute_of_day"] = timeseries["local_time"].dt.hour * 60 + timeseries["local_time"].dt.minute

    rows: list[dict[str, Any]] = []
    mw_units = {
        "n": "intervals", "model_mean": "MW", "reference_mean": "MW",
        "mean_ratio": "ratio", "bias": "MW", "mae": "MW", "rmse": "MW",
        "nrmse_reference_mean": "ratio", "mape_percent": "%",
        "pearson_r": "correlation", "spearman_rho": "correlation",
    }
    add_metrics(
        rows, "load_scale_15min",
        paired_statistics(timeseries, "observed_load_mw", "external_load_mw"),
        mw_units, "CROSS_SOURCE",
        "PT60 REN load including storage vs E-REDES national consumption; PT60 minus E-REDES",
    )
    add_metrics(
        rows, "generation_scale_15min",
        paired_statistics(timeseries, "observed_generation_total_mw", "external_production_mw"),
        mw_units, "CROSS_SOURCE_SCOPE_DIFFERENT",
        "PT60 REN generation vs E-REDES production/accounting total; PT60 minus E-REDES",
    )

    daily = timeseries.groupby("local_date", as_index=False).agg(
        pt60_load_mw=("observed_load_mw", "mean"),
        external_load_mw=("external_load_mw", "mean"),
        pt60_generation_mw=("observed_generation_total_mw", "mean"),
        external_production_mw=("external_production_mw", "mean"),
        model_ac_balance_closure_mw=("model_ac_balance_closure_mw", "mean"),
        external_balance_gap_mw=("external_balance_gap_mw", "mean"),
        intervals=("case_id", "count"),
        external_load_intervals=("external_load_mw", "count"),
    )
    add_metrics(
        rows, "load_temporal_daily",
        paired_statistics(daily, "pt60_load_mw", "external_load_mw"),
        mw_units, "CROSS_SOURCE",
        "Daily mean PT60 REN load vs E-REDES national consumption",
    )
    add_metrics(
        rows, "generation_temporal_daily",
        paired_statistics(daily, "pt60_generation_mw", "external_production_mw"),
        mw_units, "CROSS_SOURCE_SCOPE_DIFFERENT",
        "Daily mean PT60 REN generation vs E-REDES production/accounting total",
    )

    matched_municipality = municipality[municipality["matched"]].copy()
    spatial_units = {
        "n": "municipality-months", "model_mean": "share", "reference_mean": "share",
        "mean_ratio": "ratio", "bias": "share", "mae": "share", "rmse": "share",
        "nrmse_reference_mean": "ratio", "mape_percent": "%",
        "pearson_r": "correlation", "spearman_rho": "correlation",
    }
    add_metrics(
        rows, "spatial_municipality_monthly_share",
        paired_statistics(matched_municipality, "pt60_share_within_matched", "billed_share_within_matched"),
        spatial_units, "CROSS_DATASET_SHARED_OPERATOR_WEAK_PROXY",
        "Substation-location energy share vs billed municipal energy share among matched codes",
    )
    billed_total = municipality["billed_energy_kwh"].sum(skipna=True)
    billed_matched = municipality.loc[municipality["matched"], "billed_energy_kwh"].sum(skipna=True)
    rows.append({
        "domain": "spatial_municipality_coverage", "metric": "billed_energy_coverage_percent",
        "value": float(100.0 * billed_matched / billed_total) if billed_total else None,
        "unit": "%", "sample_count": int(municipality["matched"].sum()),
        "evidence_level": "COVERAGE_DIAGNOSTIC",
        "comparison": "Share of billed municipal energy in municipality-months represented by PT60 substation locations",
    })

    substation_matched = substation[substation["matched"]].copy()
    substation_units = dict(mw_units)
    substation_units["n"] = "substation-seasons"
    add_metrics(
        rows, "spatial_substation_seasonal_load",
        paired_statistics(substation_matched, "pt60_observed_peak_mw", "reference_natural_load_mw"),
        substation_units, "CROSS_DATASET_SHARED_OPERATOR",
        "PT60 profile peak vs E-REDES carga-na-subestacao natural load",
    )
    reference_count = int(substation["reference_code"].notna().sum())
    rows.append({
        "domain": "spatial_substation_coverage", "metric": "reference_row_coverage_percent",
        "value": float(100.0 * substation["matched"].sum() / reference_count) if reference_count else None,
        "unit": "%", "sample_count": int(substation["matched"].sum()),
        "evidence_level": "COVERAGE_DIAGNOSTIC",
        "comparison": "Seasonal E-REDES reference rows matched to PT60 substation profile codes",
    })

    for metric, column in (
        ("model_ac_closure_mae_mw", "model_ac_balance_closure_mw"),
        ("ren_input_balance_mae_mw", "ren_input_balance_residual_mw"),
        ("eredes_accounting_gap_mae_mw", "external_balance_gap_mw"),
        ("eredes_injection_identity_mae_mw", "external_injection_identity_error_mw"),
    ):
        values = timeseries[column].replace([np.inf, -np.inf], np.nan).dropna()
        rows.append({
            "domain": "generation_balance", "metric": metric,
            "value": float(values.abs().mean()) if len(values) else None,
            "unit": "MW", "sample_count": int(len(values)),
            "evidence_level": "PHYSICAL_CLOSURE" if metric == "model_ac_closure_mae_mw" else "SOURCE_CONSISTENCY",
            "comparison": column,
        })

    metrics = pd.DataFrame(rows)
    monthly = timeseries.groupby("month", as_index=False).agg(
        intervals=("case_id", "count"),
        external_load_intervals=("external_load_mw", "count"),
        pt60_load_mean_mw=("observed_load_mw", "mean"),
        external_load_mean_mw=("external_load_mw", "mean"),
        pt60_generation_mean_mw=("observed_generation_total_mw", "mean"),
        external_production_mean_mw=("external_production_mw", "mean"),
        observed_net_import_mean_mw=("observed_net_import_mw", "mean"),
        model_net_import_mean_mw=("model_net_import_mw", "mean"),
        model_ac_closure_mae_mw=("model_ac_balance_closure_mw", lambda value: value.abs().mean()),
        external_balance_gap_mae_mw=("external_balance_gap_mw", lambda value: value.abs().mean()),
    )
    monthly["load_mean_ratio_pt60_to_external"] = (
        monthly["pt60_load_mean_mw"] / monthly["external_load_mean_mw"]
    )
    monthly["generation_mean_ratio_pt60_to_external"] = (
        monthly["pt60_generation_mean_mw"] / monthly["external_production_mean_mw"]
    )
    return timeseries, daily, monthly, metrics


def configure_plot_style() -> None:
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 8,
        "axes.labelsize": 9,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "axes.linewidth": 0.8,
        "lines.linewidth": 1.1,
        "pdf.fonttype": 42,
        "ps.fonttype": 42,
        "savefig.bbox": "tight",
    })


def save_figure(fig: plt.Figure, stem: Path, dpi: int) -> list[Path]:
    outputs = []
    for suffix in (".pdf", ".svg", ".png"):
        path = stem.with_suffix(suffix)
        fig.savefig(path, dpi=dpi if suffix == ".png" else None, facecolor="white")
        outputs.append(path)
    plt.close(fig)
    return outputs


def plot_load_temporal(timeseries: pd.DataFrame, daily: pd.DataFrame, stem: Path, dpi: int) -> list[Path]:
    fig, axes = plt.subplots(2, 1, figsize=(7.0, 5.0), constrained_layout=True)
    daily_plot = daily.dropna(subset=["external_load_mw"])
    dates = pd.to_datetime(daily_plot["local_date"])
    axes[0].plot(dates, daily_plot["pt60_load_mw"], color=OKABE_ITO["blue"], label="PT60 / REN input")
    axes[0].plot(dates, daily_plot["external_load_mw"], color=OKABE_ITO["orange"], linestyle="--", label="E-REDES auxiliary")
    axes[0].set_ylabel("Daily mean load (MW)")
    axes[0].legend(frameon=False, ncol=2)
    axes[0].grid(axis="y", color="#dddddd", linewidth=0.6)
    axes[0].xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    axes[0].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    axes[0].tick_params(axis="x", rotation=30)

    profile = timeseries.dropna(subset=["external_load_mw"]).groupby("minute_of_day", as_index=False).agg(
        pt60=("observed_load_mw", "mean"), external=("external_load_mw", "mean")
    )
    profile["pt60"] /= profile["pt60"].mean()
    profile["external"] /= profile["external"].mean()
    hour = profile["minute_of_day"] / 60.0
    axes[1].plot(hour, profile["pt60"], color=OKABE_ITO["blue"], label="PT60 / REN input")
    axes[1].plot(hour, profile["external"], color=OKABE_ITO["orange"], linestyle="--", label="E-REDES auxiliary")
    axes[1].axhline(1.0, color="#aaaaaa", linewidth=0.7)
    axes[1].set(xlabel="Local time (hour)", ylabel="Mean-normalized load")
    axes[1].set_xticks(np.arange(0, 25, 4))
    axes[1].grid(axis="y", color="#dddddd", linewidth=0.6)
    axes[0].text(-0.08, 1.04, "(a)", transform=axes[0].transAxes, fontweight="bold", fontsize=10)
    axes[1].text(-0.08, 1.04, "(b)", transform=axes[1].transAxes, fontweight="bold", fontsize=10)
    return save_figure(fig, stem, dpi)


def plot_generation_balance(timeseries: pd.DataFrame, daily: pd.DataFrame, stem: Path, dpi: int) -> list[Path]:
    fig, axes = plt.subplots(2, 1, figsize=(7.0, 5.0), constrained_layout=True)
    daily_plot = daily.dropna(subset=["external_production_mw"])
    dates = pd.to_datetime(daily_plot["local_date"])
    axes[0].plot(dates, daily_plot["pt60_generation_mw"], color=OKABE_ITO["green"], label="PT60 / REN generation")
    axes[0].plot(dates, daily_plot["external_production_mw"], color=OKABE_ITO["purple"], linestyle="--", label="E-REDES production total")
    axes[0].set_ylabel("Daily mean (MW)")
    axes[0].legend(frameon=False, ncol=2)
    axes[0].grid(axis="y", color="#dddddd", linewidth=0.6)
    axes[0].xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    axes[0].xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    axes[0].tick_params(axis="x", rotation=30)

    columns = [
        ("model_ac_balance_closure_mw", "AC closure", OKABE_ITO["blue"]),
        ("ren_input_balance_residual_mw", "REN input residual", OKABE_ITO["orange"]),
        ("external_balance_gap_mw", "E-REDES accounting gap", OKABE_ITO["purple"]),
    ]
    values = [timeseries[column].dropna().to_numpy() for column, _, _ in columns]
    labels = [label for _, label, _ in columns]
    boxes = axes[1].boxplot(values, tick_labels=labels, showfliers=False, patch_artist=True, widths=0.55)
    for box, (_, _, color) in zip(boxes["boxes"], columns):
        box.set_facecolor(color)
        box.set_alpha(0.75)
    axes[1].axhline(0.0, color=OKABE_ITO["black"], linewidth=0.8)
    axes[1].set_ylabel("Balance residual (MW)")
    axes[1].grid(axis="y", color="#dddddd", linewidth=0.6)
    axes[0].text(-0.08, 1.04, "(a)", transform=axes[0].transAxes, fontweight="bold", fontsize=10)
    axes[1].text(-0.08, 1.04, "(b)", transform=axes[1].transAxes, fontweight="bold", fontsize=10)
    return save_figure(fig, stem, dpi)


def plot_spatial(municipality: pd.DataFrame, substation: pd.DataFrame, stem: Path, dpi: int) -> list[Path]:
    fig, axes = plt.subplots(1, 2, figsize=(7.0, 3.2), constrained_layout=True)
    municipal = municipality[municipality["matched"]].dropna(
        subset=["pt60_share_within_matched", "billed_share_within_matched"]
    )
    axes[0].scatter(
        municipal["billed_share_within_matched"] * 100.0,
        municipal["pt60_share_within_matched"] * 100.0,
        s=7, alpha=0.28, color=OKABE_ITO["blue"], edgecolors="none",
    )
    maximum = max(
        municipal["billed_share_within_matched"].max(),
        municipal["pt60_share_within_matched"].max(),
    ) * 100.0
    axes[0].plot([0, maximum], [0, maximum], color=OKABE_ITO["black"], linestyle="--", linewidth=0.8)
    axes[0].set(xlabel="Billed municipal share (%)", ylabel="PT60 substation-location share (%)")
    axes[0].grid(color="#dddddd", linewidth=0.5)

    stations = substation[substation["matched"]].dropna(
        subset=["reference_natural_load_mw", "pt60_observed_peak_mw"]
    )
    for season, marker, color in (("Summer", "o", OKABE_ITO["orange"]), ("Winter", "s", OKABE_ITO["sky"])):
        subset = stations[stations["season"] == season]
        axes[1].scatter(
            subset["reference_natural_load_mw"], subset["pt60_observed_peak_mw"],
            s=13, alpha=0.55, marker=marker, color=color, edgecolors="none", label=season,
        )
    maximum = max(stations["reference_natural_load_mw"].max(), stations["pt60_observed_peak_mw"].max())
    axes[1].plot([0, maximum], [0, maximum], color=OKABE_ITO["black"], linestyle="--", linewidth=0.8)
    axes[1].set(xlabel="E-REDES natural load reference (MW)", ylabel="PT60 profile peak (MW)")
    axes[1].legend(frameon=False)
    axes[1].grid(color="#dddddd", linewidth=0.5)
    axes[0].text(-0.15, 1.04, "(a)", transform=axes[0].transAxes, fontweight="bold", fontsize=10)
    axes[1].text(-0.15, 1.04, "(b)", transform=axes[1].transAxes, fontweight="bold", fontsize=10)
    return save_figure(fig, stem, dpi)


def write_csv(frame: pd.DataFrame, path: Path) -> None:
    frame.to_csv(path, index=False, date_format="%Y-%m-%dT%H:%M:%S%z")


def write_validation_database(
    output: Path,
    frames: dict[str, pd.DataFrame],
    metadata: pd.DataFrame,
) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=output.stem + ".", suffix=".tmp.duckdb", dir=output.parent)
    os.close(handle)
    temp = Path(temp_name)
    temp.unlink()
    try:
        connection = duckdb.connect(str(temp))
        connection.execute("CREATE SCHEMA validation")
        connection.register("_metadata", metadata)
        connection.execute("CREATE TABLE validation.run_metadata AS SELECT * FROM _metadata")
        for table_name, frame in frames.items():
            connection.register("_frame", frame)
            connection.execute(f'CREATE TABLE validation."{table_name}" AS SELECT * FROM _frame')
            connection.unregister("_frame")
        connection.execute("CHECKPOINT")
        connection.close()
        os.replace(temp, output)
    finally:
        temp.unlink(missing_ok=True)


def metric_value(metrics: pd.DataFrame, domain: str, metric: str) -> float | None:
    rows = metrics[(metrics["domain"] == domain) & (metrics["metric"] == metric)]["value"]
    if rows.empty or pd.isna(rows.iloc[0]):
        return None
    return float(rows.iloc[0])


def fmt(value: float | None, digits: int = 3) -> str:
    return "NA" if value is None or not math.isfinite(value) else f"{value:.{digits}f}"


def write_report(
    path: Path,
    paths: Paths,
    monthly_paths: list[Path],
    timeseries: pd.DataFrame,
    municipality: pd.DataFrame,
    substation: pd.DataFrame,
    metrics: pd.DataFrame,
) -> None:
    load_n = int(metric_value(metrics, "load_scale_15min", "n") or 0)
    generation_n = int(metric_value(metrics, "generation_scale_15min", "n") or 0)
    report = f"""# PT60 external-evidence validation

Generated from read-only databases. This report is a cross-source diagnostic, not an
operator ground-truth certification of the reconstructed network.

## Scope

- PT60 model cases: {len(timeseries):,} complete converged 15-minute cases
- Covered model interval: {timeseries['timestamp_utc'].min().isoformat()} to {timeseries['timestamp_utc'].max().isoformat()}
- Compact monthly databases: {len(monthly_paths)}
- Paired national load intervals: {load_n:,}
- Paired national generation intervals: {generation_n:,}
- Matched municipality-months: {int(municipality['matched'].sum()):,}
- Matched substation-seasons: {int(substation['matched'].sum()):,}

## Headline diagnostics

| Domain | Metric | Result |
|---|---|---:|
| Load scale | PT60 / E-REDES mean ratio | {fmt(metric_value(metrics, 'load_scale_15min', 'mean_ratio'))} |
| Load scale | 15-minute Pearson r | {fmt(metric_value(metrics, 'load_scale_15min', 'pearson_r'))} |
| Time trend | Daily Pearson r | {fmt(metric_value(metrics, 'load_temporal_daily', 'pearson_r'))} |
| Time trend | Daily normalized RMSE | {fmt(metric_value(metrics, 'load_temporal_daily', 'nrmse_reference_mean'))} |
| Generation | PT60 / E-REDES mean ratio | {fmt(metric_value(metrics, 'generation_scale_15min', 'mean_ratio'))} |
| Generation | 15-minute Pearson r | {fmt(metric_value(metrics, 'generation_scale_15min', 'pearson_r'))} |
| AC balance | Mean absolute closure residual | {fmt(metric_value(metrics, 'generation_balance', 'model_ac_closure_mae_mw'))} MW |
| Municipal spatial proxy | Spearman rho | {fmt(metric_value(metrics, 'spatial_municipality_monthly_share', 'spearman_rho'))} |
| Municipal spatial proxy | Billed-energy coverage | {fmt(metric_value(metrics, 'spatial_municipality_coverage', 'billed_energy_coverage_percent'), 1)}% |
| Substation load | Spearman rho | {fmt(metric_value(metrics, 'spatial_substation_seasonal_load', 'spearman_rho'))} |
| Substation load | Reference-row coverage | {fmt(metric_value(metrics, 'spatial_substation_coverage', 'reference_row_coverage_percent'), 1)}% |

## Interpretation boundaries

1. **Load-scale and temporal tests are cross-source.** PT60's national target is REN
   consumption including storage; the comparison is E-REDES national distributed
   consumption. Agreement supports scale and shape plausibility, while disagreement may
   reflect scope as well as model error.
2. **Municipal comparison is a weak spatial proxy.** PT60 assigns measured substation
   profiles at substation locations. A substation municipality is not necessarily its
   complete service territory. Municipal billed energy is therefore a useful diagnostic,
   not node-level ground truth.
3. **Substation comparison shares the same operator but uses a different dataset.** The
   15-minute load diagrams and the seasonal `carga-na-subestacao` table are distinct
   publications, so this is cross-dataset corroboration rather than fully independent
   validation.
4. **Generation scopes differ.** REN generation covers the national system; E-REDES
   production/injection tables describe distribution accounting. Correlation and balance
   are more defensible than treating their magnitudes as identical quantities.
5. **AC closure is an internal physical check.** It tests whether generation, modeled
   boundary exchange, load, and modeled losses reconcile; it does not independently prove
   branch topology or electrical parameters.

## Unit conversion

E-REDES national 15-minute energy values are converted to average MW using
`MW = kWh / 0.25 h / 1000 = kWh / 250`. No smoothing or outlier deletion is applied.
Missing external timestamps remain missing and are excluded pairwise from metrics.

## Inputs

- Source database: `{paths.source_database.resolve()}`
- Auxiliary manifest: `{paths.auxiliary_manifest.resolve()}`
- Monthly root: `{paths.monthly_root.resolve()}`

Detailed values are in `metrics.csv`; all joined 15-minute observations are in
`timeseries_15min.csv` and `pt60_external_validation.duckdb`.
"""
    path.write_text(report, encoding="utf-8")


def write_figure_manifest(
    path: Path, csv_hashes: dict[str, str], figures: dict[str, list[Path]], generator: Path,
) -> None:
    def relative_outputs(items: Iterable[Path]) -> str:
        root = path.parent.resolve()
        return ";".join(str(item.resolve().relative_to(root)) for item in items)

    fieldnames = [
        "figure_id", "panel_id", "claim", "source_data", "source_sha256", "generator",
        "output_file", "caption", "uncertainty", "license_status", "status", "notes",
    ]
    records = [
        {
            "figure_id": "fig01", "panel_id": "a-b",
            "claim": "PT60 national load has comparable scale and temporal shape to an archived E-REDES aggregate.",
            "source_data": "timeseries_15min.csv;daily_summary.csv",
            "source_sha256": f"{csv_hashes['timeseries_15min.csv']};{csv_hashes['daily_summary.csv']}",
            "generator": str(generator),
            "output_file": relative_outputs(figures["fig01"]),
            "caption": "Daily mean and mean-normalized time-of-day load profiles. PT60 uses REN observations; E-REDES is cross-source evidence. Missing timestamps are excluded pairwise.",
            "uncertainty": "Deterministic census of available intervals; no sampling confidence interval.",
            "license_status": "E-REDES attribution required; PT60 project terms apply.",
            "status": "generated",
            "notes": "Scope differs: REN national load including storage versus E-REDES distributed consumption.",
        },
        {
            "figure_id": "fig02", "panel_id": "a-b",
            "claim": "PT60 generation follows an external distribution-accounting series and its solved AC cases close physically.",
            "source_data": "timeseries_15min.csv;daily_summary.csv",
            "source_sha256": f"{csv_hashes['timeseries_15min.csv']};{csv_hashes['daily_summary.csv']}",
            "generator": str(generator),
            "output_file": relative_outputs(figures["fig02"]),
            "caption": "Daily generation series and balance-residual distributions. Boxes show median and IQR; whiskers use the Matplotlib 1.5-IQR rule and outliers are retained in metrics but not drawn.",
            "uncertainty": "Observed temporal variability; no independent-run uncertainty.",
            "license_status": "REN and E-REDES attribution required; PT60 project terms apply.",
            "status": "generated",
            "notes": "REN national generation and E-REDES distribution production have different accounting scopes.",
        },
        {
            "figure_id": "fig03", "panel_id": "a-b",
            "claim": "PT60 spatial load allocation is cross-checked at municipal and substation levels.",
            "source_data": "municipality_comparison.csv;substation_comparison.csv",
            "source_sha256": f"{csv_hashes['municipality_comparison.csv']};{csv_hashes['substation_comparison.csv']}",
            "generator": str(generator),
            "output_file": relative_outputs(figures["fig03"]),
            "caption": "Monthly municipal energy shares and seasonal substation load comparison. Dashed lines denote equality; points are municipality-months or substation-seasons.",
            "uncertainty": "Deterministic source records; no sampling confidence interval.",
            "license_status": "E-REDES attribution required; PT60 project terms apply.",
            "status": "generated",
            "notes": "Municipality of a substation is only a service-area proxy; evidence dependence is stated in the report.",
        },
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)


def main() -> None:
    args = parse_args()
    paths = Paths(
        source_database=args.source_database.expanduser(),
        monthly_root=args.monthly_root.expanduser(),
        auxiliary_manifest=args.auxiliary_manifest.expanduser(),
        output_dir=args.output_dir.expanduser(),
    )
    paths.output_dir.mkdir(parents=True, exist_ok=True)
    logger = configure_logging(paths.output_dir)
    started = time.monotonic()
    logger.info("PT60 external-evidence validation started")
    require_file(paths.source_database, "Source database")
    require_file(paths.auxiliary_manifest, "Auxiliary manifest")
    monthly_paths = discover_monthly_databases(paths.monthly_root, args.months)
    logger.info("Discovered %d compact monthly databases", len(monthly_paths))

    cases = read_model_cases(monthly_paths, logger)
    connection = duckdb.connect(str(paths.source_database), read_only=True)
    try:
        connection.execute("SET TimeZone='UTC'")
        external = read_external_timeseries(connection)
        start = cases["timestamp_utc"].min().tz_convert("Europe/Lisbon")
        end = cases["timestamp_utc"].max().tz_convert("Europe/Lisbon")
        municipality = read_municipality_comparison(connection, start, end)
        substation = read_substation_comparison(connection, start, end)
    finally:
        connection.close()

    timeseries, daily, monthly, metrics = compute_results(
        cases, external, municipality, substation
    )
    matched_load = int(timeseries["external_load_mw"].notna().sum())
    logger.info(
        "Aligned national evidence: model=%d load_pairs=%d (%.1f%%)",
        len(timeseries), matched_load, 100.0 * matched_load / len(timeseries),
    )
    logger.info(
        "Spatial evidence: municipality_months=%d matched=%d substation_seasons=%d matched=%d",
        len(municipality), int(municipality["matched"].sum()),
        len(substation), int(substation["matched"].sum()),
    )

    csv_frames = {
        "metrics.csv": metrics,
        "monthly_summary.csv": monthly,
        "daily_summary.csv": daily,
        "timeseries_15min.csv": timeseries,
        "municipality_comparison.csv": municipality,
        "substation_comparison.csv": substation,
    }
    for name, frame in csv_frames.items():
        write_csv(frame, paths.output_dir / name)

    metadata = pd.DataFrame([{
        "generated_at_utc": pd.Timestamp.now(tz="UTC"),
        "source_database": str(paths.source_database.resolve()),
        "source_database_bytes": paths.source_database.stat().st_size,
        "auxiliary_manifest": str(paths.auxiliary_manifest.resolve()),
        "auxiliary_manifest_sha256": sha256(paths.auxiliary_manifest),
        "monthly_database_count": len(monthly_paths),
        "model_case_count": len(cases),
        "paired_external_load_count": matched_load,
        "conversion_rule": "E-REDES 15-minute kWh / 250 = average MW",
        "missing_policy": "retain missing timestamps; pairwise exclusion in metrics",
        "outlier_policy": "no outlier removal",
    }])
    database_frames = {
        "metrics": metrics,
        "monthly_summary": monthly,
        "daily_summary": daily,
        "timeseries_15min": timeseries,
        "municipality_comparison": municipality,
        "substation_comparison": substation,
    }
    write_validation_database(
        paths.output_dir / "pt60_external_validation.duckdb", database_frames, metadata
    )

    configure_plot_style()
    figures_dir = paths.output_dir / "figures"
    figures_dir.mkdir(exist_ok=True)
    figures = {
        "fig01": plot_load_temporal(timeseries, daily, figures_dir / "fig01_load_scale_temporal", args.dpi),
        "fig02": plot_generation_balance(timeseries, daily, figures_dir / "fig02_generation_balance", args.dpi),
        "fig03": plot_spatial(municipality, substation, figures_dir / "fig03_spatial_validation", args.dpi),
    }
    csv_hashes = {name: sha256(paths.output_dir / name) for name in csv_frames}
    write_figure_manifest(
        paths.output_dir / "figure_manifest.csv", csv_hashes, figures, Path(__file__).resolve()
    )
    write_report(
        paths.output_dir / "validation_report.md", paths, monthly_paths,
        timeseries, municipality, substation, metrics,
    )
    summary = {
        "status": "COMPLETE",
        "duration_seconds": time.monotonic() - started,
        "model_cases": len(timeseries),
        "paired_load_intervals": matched_load,
        "months": [month_from_path(path) for path in monthly_paths],
        "output_database": str((paths.output_dir / "pt60_external_validation.duckdb").resolve()),
        "report": str((paths.output_dir / "validation_report.md").resolve()),
    }
    (paths.output_dir / "run_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "COMPLETE duration=%.1fs cases=%d load_pairs=%d output=%s",
        summary["duration_seconds"], len(timeseries), matched_load, paths.output_dir.resolve(),
    )


if __name__ == "__main__":
    main()
