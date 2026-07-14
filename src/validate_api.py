import config
from utils import (
    ensure_directories,
    fetch_metadata,
    fetch_records_v1,
    fetch_records_v2,
    field_detection,
    get_logger,
    load_sources,
    metadata_url,
    records_v1_url,
    records_v2_url,
    seed_sources,
    test_export_formats,
    utc_now,
    write_json,
    write_text,
    markdown_table,
)


def status_without_key(dataset_id):
    status, _ = fetch_metadata(dataset_id, api_key=None)
    return status


def governance_metadata(metadata):
    metas = metadata.get("metas", {}) if isinstance(metadata, dict) else {}
    default = metas.get("default", {}) if isinstance(metas, dict) else {}
    if not isinstance(default, dict):
        default = {}
    license_value = default.get("license") or metadata.get("license") or ""
    license_url = default.get("license_url") or metadata.get("license_url") or ""
    attribution = default.get("attributions") or default.get("attribution") or ""
    attribution_url = default.get("attribution_url") or ""
    license_present = bool(str(license_value).strip() or str(license_url).strip())
    return {
        "dataset_title": default.get("title") or metadata.get("dataset_id") or "",
        "publisher": default.get("publisher") or "E-REDES",
        "license_observed": license_value,
        "license_url_observed": license_url,
        "attribution_observed": attribution,
        "attribution_url_observed": attribution_url,
        "license_metadata_status": "EXPLICIT" if license_present else "MISSING",
        "redistribution_gate": "SOURCE_TERMS_PRESENT_REVIEW_REQUIRED" if license_present else "BLOCKED_MISSING_DATASET_LICENSE",
    }


def validate_dataset(dataset_id, source, logger):
    api_key = source.get("api_key")
    item = {
        "dataset_id": dataset_id,
        "source_refs": sorted(set(source.get("sources", []))),
        "requires_page_api_key": bool(api_key),
        "metadata_url": metadata_url(dataset_id),
        "v2_records_url": records_v2_url(dataset_id),
        "v1_records_url": records_v1_url(),
        "checked_at": utc_now(),
    }
    item["metadata_status_without_key"] = status_without_key(dataset_id)
    metadata_status, metadata = fetch_metadata(dataset_id, api_key=api_key)
    item["metadata_status"] = metadata_status
    if metadata_status != 200 or metadata is None:
        item["error"] = "metadata endpoint failed"
        return item
    item.update(governance_metadata(metadata))
    item["portal_dataset_url"] = f"{config.BASE_URL}/explore/dataset/{dataset_id}/"

    v2_status, v2_payload = fetch_records_v2(dataset_id, limit=1, offset=0, api_key=api_key)
    item["v2_records_status"] = v2_status
    item["v2_total_count"] = v2_payload.get("total_count") if v2_payload else None
    item["v2_sample_count"] = len(v2_payload.get("results", [])) if v2_payload else 0

    v1_status, v1_payload = fetch_records_v1(dataset_id, rows=1, start=0, api_key=api_key)
    item["v1_records_status"] = v1_status
    item["v1_total_count"] = v1_payload.get("nhits") if v1_payload else None
    item["v1_sample_count"] = len(v1_payload.get("records", [])) if v1_payload else 0

    page_status, page_payload = fetch_records_v2(dataset_id, limit=1, offset=1, api_key=api_key)
    item["pagination_status"] = page_status
    item["pagination_second_record_available"] = bool(
        page_payload and page_payload.get("results")
    )

    item["export_formats"] = test_export_formats(dataset_id, api_key)
    item.update(field_detection(metadata, v2_payload.get("results", []) if v2_payload else []))
    logger.info(
        "Validated %s: meta=%s v2=%s v1=%s total=%s",
        dataset_id,
        metadata_status,
        v2_status,
        v1_status,
        item.get("v2_total_count"),
    )
    return item


def build_report(results):
    rows = []
    for item in sorted(results, key=lambda row: row["dataset_id"]):
        formats = [
            fmt for fmt, detail in item.get("export_formats", {}).items() if detail.get("available")
        ]
        rows.append(
            {
                "Dataset": item["dataset_id"],
                "Meta": item.get("metadata_status"),
                "Meta no key": item.get("metadata_status_without_key"),
                "V2": item.get("v2_records_status"),
                "V1": item.get("v1_records_status"),
                "Records": item.get("v2_total_count") or item.get("v1_total_count"),
                "Exports": ",".join(formats),
                "Geom": item.get("has_geometry"),
                "Date": item.get("has_date_time"),
                "Voltage": item.get("has_voltage"),
                "Substation": item.get("has_substation_identifier"),
                "License": item.get("license_metadata_status"),
                "Redistribution": item.get("redistribution_gate"),
            }
        )
    text = [
        "# 01 API Validation",
        "",
        f"Generated: {utc_now()}",
        "",
        "Both OpenDataSoft API v2.1 and v1 record endpoints were tested. Restricted RND page layers were retried with the public API key embedded in the RND page widgets.",
        "",
        markdown_table(
            rows,
            [
                "Dataset",
                "Meta",
                "Meta no key",
                "V2",
                "V1",
                "Records",
                "Exports",
                "Geom",
                "Date",
                "Voltage",
                "Substation",
                "License",
                "Redistribution",
            ],
        ),
        "",
        "Reproducibility note: API URLs, timestamps, HTTP statuses, export-format probes, and detected field classes are saved in `data/metadata/api_validation.json`.",
    ]
    write_text(config.REPORTS_DIR / "01_api_validation.md", "\n".join(text))


def main():
    ensure_directories()
    logger = get_logger("validate_api")
    sources = load_sources() or seed_sources(logger)
    results = []
    for dataset_id, source in sorted(sources.items()):
        results.append(validate_dataset(dataset_id, source, logger))
    write_json(config.METADATA_DIR / "api_validation.json", results)
    build_report(results)
    logger.info("API validation complete for %s dataset references", len(results))


if __name__ == "__main__":
    main()
