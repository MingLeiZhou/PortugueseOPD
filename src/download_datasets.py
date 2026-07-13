import argparse
import json

import pandas as pd

import config
from utils import (
    ensure_directories,
    export_url,
    fetch_records_v2,
    fetch_paginated_records,
    get_logger,
    load_catalog,
    load_sources,
    records_to_frame,
    request_url,
    save_dataframe,
    utc_now,
    write_json,
)


def download_stream(url, path, api_key, logger, overwrite=False):
    if path.exists() and not overwrite:
        logger.info("Keeping existing file: %s", path)
        return "exists"
    response = request_url(url, api_key=api_key, stream=True)
    if response.status_code != 200:
        logger.warning("Download failed %s status=%s", url, response.status_code)
        return f"status_{response.status_code}"
    tmp_path = path.with_suffix(path.suffix + ".part")
    with tmp_path.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                handle.write(chunk)
    tmp_path.replace(path)
    logger.info("Downloaded %s", path)
    return "downloaded"


def save_sample(dataset_id, source, logger, overwrite=False):
    csv_path = config.SAMPLES_DIR / f"{dataset_id}_sample.csv"
    json_path = config.SAMPLES_DIR / f"{dataset_id}_sample.json"
    if csv_path.exists() and json_path.exists() and not overwrite:
        return {"sample_status": "exists", "sample_rows": len(pd.read_csv(csv_path))}
    records, total_count = fetch_paginated_records(
        dataset_id,
        max_records=config.SAMPLE_RECORDS,
        api_key=source.get("api_key"),
        logger=logger,
    )
    df = records_to_frame(records)
    save_dataframe(df, csv_path)
    write_json(json_path, [dict(row) for row in records])
    logger.info("Sample saved for %s: %s rows of %s", dataset_id, len(df), total_count)
    return {"sample_status": "downloaded", "sample_rows": len(df), "api_total_count": total_count}


def paginated_full_download(dataset_id, source, total_count, logger, overwrite=False):
    csv_path = config.RAW_DIR / f"{dataset_id}.csv"
    json_path = config.RAW_DIR / f"{dataset_id}.json"
    checkpoint_path = config.RAW_DIR / f"{dataset_id}.checkpoint.json"
    if csv_path.exists() and json_path.exists() and not overwrite:
        return "exists"
    offset = 0
    header_written = False
    first_json = True
    if checkpoint_path.exists() and not overwrite:
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        offset = int(checkpoint.get("offset", 0))
        header_written = offset > 0 and csv_path.exists()
        first_json = offset == 0
    mode = "a" if offset else "w"
    with csv_path.open(mode, encoding="utf-8", newline="") as csv_handle, json_path.open(
        "a" if offset else "w", encoding="utf-8"
    ) as json_handle:
        if offset == 0:
            json_handle.write("[\n")
        while offset < total_count:
            status, payload = fetch_records_v2(
                dataset_id,
                limit=config.PAGE_SIZE,
                offset=offset,
                api_key=source.get("api_key"),
            )
            if status != 200 or payload is None:
                raise RuntimeError(f"Pagination failed for {dataset_id} at offset {offset}: {status}")
            records = payload.get("results", [])
            if not records:
                break
            df = records_to_frame(records)
            df.to_csv(csv_handle, index=False, header=not header_written)
            header_written = True
            for record in records:
                if not first_json:
                    json_handle.write(",\n")
                json.dump(record, json_handle, ensure_ascii=False, default=str)
                first_json = False
            offset += len(records)
            write_json(checkpoint_path, {"dataset_id": dataset_id, "offset": offset})
            logger.info("Full pagination %s: %s/%s", dataset_id, offset, total_count)
        json_handle.write("\n]\n")
    checkpoint_path.unlink(missing_ok=True)
    return "paginated"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples-only", action="store_true")
    parser.add_argument("--force-large", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--full-limit", type=int, default=config.FULL_DOWNLOAD_RECORD_LIMIT)
    args = parser.parse_args()

    ensure_directories()
    logger = get_logger("download_datasets")
    catalog = load_catalog()
    sources = load_sources()
    manifest = {
        "generated_at": utc_now(),
        "full_download_record_limit": args.full_limit,
        "force_large": args.force_large,
        "datasets": {},
        "reproducibility_note": "Raw files are not overwritten unless --overwrite is set. Large datasets are skipped by default and can be downloaded with --force-large.",
    }

    for dataset_id, meta in sorted(catalog.items()):
        source = sources.get(dataset_id, {})
        result = {
            "records_expected": meta.get("records_count"),
            "sample": save_sample(dataset_id, source, logger, args.overwrite),
        }
        if args.samples_only:
            result["raw_status"] = "samples_only"
            manifest["datasets"][dataset_id] = result
            continue
        total = int(meta.get("records_count") or 0)
        if total > args.full_limit and not args.force_large:
            result["raw_status"] = "skipped_large"
            result["skip_reason"] = f"{total} records exceeds limit {args.full_limit}"
            logger.info("Skipping full download for %s: %s records", dataset_id, total)
            manifest["datasets"][dataset_id] = result
            continue
        if total > args.full_limit and args.force_large:
            result["raw_status"] = paginated_full_download(
                dataset_id, source, total, logger, args.overwrite
            )
            manifest["datasets"][dataset_id] = result
            continue
        csv_status = download_stream(
            export_url(dataset_id, "csv"),
            config.RAW_DIR / f"{dataset_id}.csv",
            source.get("api_key"),
            logger,
            args.overwrite,
        )
        json_status = download_stream(
            export_url(dataset_id, "json"),
            config.RAW_DIR / f"{dataset_id}.json",
            source.get("api_key"),
            logger,
            args.overwrite,
        )
        geojson_status = "not_geographic"
        if meta.get("geographic_coverage", {}).get("geometry_types") or any(
            field in meta.get("field_types", {}).values() for field in ["geo_point_2d", "geo_shape"]
        ):
            geojson_status = download_stream(
                export_url(dataset_id, "geojson"),
                config.RAW_DIR / f"{dataset_id}.geojson",
                source.get("api_key"),
                logger,
                args.overwrite,
            )
        result["raw_status"] = {
            "csv": csv_status,
            "json": json_status,
            "geojson": geojson_status,
        }
        manifest["datasets"][dataset_id] = result

    write_json(config.METADATA_DIR / "download_manifest.json", manifest)
    logger.info("Download run complete")


if __name__ == "__main__":
    main()
