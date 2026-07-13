import config
from utils import (
    candidate_key_columns,
    count_csv_rows,
    ensure_directories,
    get_logger,
    load_catalog,
    load_dataset_frame,
    markdown_table,
    name_matches,
    utc_now,
    write_json,
    write_text,
)

import pandas as pd


def missing_rate(df, columns):
    existing = [column for column in columns if column in df.columns]
    if not existing or df.empty:
        return None
    return round(100 * df[existing].isna().all(axis=1).sum() / len(df), 4)


def temporal_coverage(df):
    coverage = {}
    for column in df.columns:
        if name_matches(column, config.DATE_KEYWORDS):
            parsed = pd.to_datetime(df[column], errors="coerce", utc=True)
            if parsed.notna().any():
                coverage[column] = {"min": str(parsed.min()), "max": str(parsed.max())}
    return coverage


def geographic_coverage(df):
    result = {}
    for label, keywords in {
        "district": ("distrito", "district", "dis_name"),
        "municipality": ("concelho", "municip", "con_name"),
        "parish": ("freguesia", "parish"),
    }.items():
        for column in df.columns:
            if name_matches(column, keywords):
                result[column] = {
                    "unique": int(df[column].nunique(dropna=True)),
                    "examples": df[column].dropna().astype(str).drop_duplicates().head(10).tolist(),
                    "category": label,
                }
                break
    return result


def quality_report(dataset_id, meta):
    df, source_path = load_dataset_frame(dataset_id)
    raw_path = config.RAW_DIR / f"{dataset_id}.csv"
    downloaded = count_csv_rows(raw_path) if raw_path.exists() else len(df)
    expected = int(meta.get("records_count") or 0)
    field_missing = {
        column: round(float(df[column].isna().mean() * 100), 4) for column in df.columns
    } if not df.empty else {}
    keys = candidate_key_columns(df) if not df.empty else []
    duplicate_keys = {}
    for column in keys:
        duplicate_keys[column] = int(df[column].duplicated().sum())
    geometry_cols = [
        column
        for column in df.columns
        if name_matches(column, ("geo", "coordenad", "lat", "lon"))
    ]
    date_cols = [column for column in df.columns if name_matches(column, config.DATE_KEYWORDS)]
    voltage_cols = [column for column in df.columns if name_matches(column, config.VOLTAGE_KEYWORDS)]
    substation_cols = [
        column for column in df.columns if name_matches(column, config.SUBSTATION_KEYWORDS)
    ]
    return {
        "dataset_id": dataset_id,
        "source_path": source_path,
        "total_records_expected_by_api": expected,
        "total_records_downloaded_or_inspected": int(downloaded),
        "download_completeness_pct": round(100 * downloaded / expected, 4) if expected else None,
        "sample_only": not raw_path.exists(),
        "missing_value_rate_by_field": field_missing,
        "duplicate_rows": int(df.duplicated().sum()) if not df.empty else None,
        "duplicate_potential_primary_keys": duplicate_keys,
        "missing_geometry_rate": missing_rate(df, geometry_cols),
        "missing_date_time_rate": missing_rate(df, date_cols),
        "missing_voltage_level_rate": missing_rate(df, voltage_cols),
        "missing_substation_code_name_rate": missing_rate(df, substation_cols),
        "geographic_coverage": geographic_coverage(df),
        "temporal_coverage": temporal_coverage(df),
    }


def main():
    ensure_directories()
    logger = get_logger("check_completeness")
    catalog = load_catalog()
    report_rows = []
    for dataset_id, meta in sorted(catalog.items()):
        report = quality_report(dataset_id, meta)
        write_json(config.METADATA_DIR / f"{dataset_id}_quality_report.json", report)
        report_rows.append(
            {
                "Dataset": dataset_id,
                "Expected": report["total_records_expected_by_api"],
                "Downloaded/inspected": report["total_records_downloaded_or_inspected"],
                "Completeness %": report["download_completeness_pct"],
                "Sample only": report["sample_only"],
                "Duplicate rows": report["duplicate_rows"],
                "Missing geometry %": report["missing_geometry_rate"],
                "Missing date/time %": report["missing_date_time_rate"],
                "Missing voltage %": report["missing_voltage_level_rate"],
                "Missing substation %": report["missing_substation_code_name_rate"],
            }
        )
        logger.info("Quality report saved for %s", dataset_id)

    text = [
        "# 03 Data Completeness Report",
        "",
        f"Generated: {utc_now()}",
        "",
        "Completeness is measured against the API record count. Datasets above the configured full-download limit are marked as sample-only unless explicitly downloaded with `python src/download_datasets.py --force-large`.",
        "",
        markdown_table(
            report_rows,
            [
                "Dataset",
                "Expected",
                "Downloaded/inspected",
                "Completeness %",
                "Sample only",
                "Duplicate rows",
                "Missing geometry %",
                "Missing date/time %",
                "Missing voltage %",
                "Missing substation %",
            ],
        ),
    ]
    write_text(config.REPORTS_DIR / "03_data_completeness_report.md", "\n".join(text))
    logger.info("Completeness checks complete")


if __name__ == "__main__":
    main()
