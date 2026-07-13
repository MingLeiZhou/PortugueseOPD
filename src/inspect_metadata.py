import argparse

import config
from utils import (
    ensure_directories,
    fetch_metadata,
    fetch_paginated_records,
    get_logger,
    metadata_summary,
    metadata_url,
    seed_sources,
    test_export_formats,
    utc_now,
    write_json,
    write_text,
    markdown_table,
)


def expand_load_index(sources, logger):
    dataset_id = "diagrama-de-carga-de-subestacao"
    source = sources.get(dataset_id, {})
    try:
        records, _ = fetch_paginated_records(
            dataset_id,
            max_records=20,
            api_key=source.get("api_key"),
            logger=logger,
        )
    except Exception as exc:
        logger.warning("Could not expand load diagram index: %s", exc)
        return sources
    for record in records:
        split_id = record.get("dataset_identifier")
        if split_id:
            entry = sources.setdefault(
                split_id,
                {"dataset_id": split_id, "sources": [], "api_key": None, "contexts": []},
            )
            entry["sources"].append(f"derived_from:{dataset_id}")
    return sources


def build_inventory_report(catalog, errors):
    rows = []
    for dataset_id, entry in sorted(catalog.items()):
        rows.append(
            {
                "Dataset": dataset_id,
                "Title": entry.get("title"),
                "Records": entry.get("records_count"),
                "Visibility": entry.get("visibility"),
                "Geometry": ",".join(
                    entry.get("geographic_coverage", {}).get("geometry_types") or []
                ),
                "Fields": len(entry.get("fields", [])),
                "Requires page key": entry.get("requires_page_api_key"),
            }
        )
    text = [
        "# 00 Dataset Inventory",
        "",
        f"Generated: {utc_now()}",
        "",
        "Scope: Step 1 only. This report inventories and inspects E-REDES datasets; it does not build a power-flow model or infer missing electrical parameters.",
        "",
        "## Valid datasets",
        markdown_table(
            rows,
            ["Dataset", "Title", "Records", "Visibility", "Geometry", "Fields", "Requires page key"],
        ),
    ]
    if errors:
        text.extend(
            [
                "## Non-resolving or inaccessible dataset references",
                markdown_table(errors, ["Dataset", "Status", "Sources"]),
            ]
        )
    write_text(config.REPORTS_DIR / "00_dataset_inventory.md", "\n".join(text))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-export-test", action="store_true")
    args = parser.parse_args()
    ensure_directories()
    logger = get_logger("inspect_metadata")
    logger.info("Starting metadata inspection at %s", utc_now())

    sources = expand_load_index(seed_sources(logger), logger)
    write_json(config.METADATA_DIR / "discovered_sources.json", sources)

    catalog = {}
    errors = []
    for dataset_id, source in sorted(sources.items()):
        api_key = source.get("api_key")
        status, metadata = fetch_metadata(dataset_id, api_key=api_key)
        if status != 200 or metadata is None:
            errors.append(
                {
                    "Dataset": dataset_id,
                    "Status": status,
                    "Sources": ",".join(sorted(set(source.get("sources", [])))),
                }
            )
            logger.warning("Metadata failed for %s with status %s", dataset_id, status)
            continue
        export_formats = {} if args.no_export_test else test_export_formats(dataset_id, api_key)
        summary = metadata_summary(
            dataset_id,
            metadata,
            source,
            export_formats,
            metadata_url(dataset_id),
        )
        catalog[dataset_id] = summary
        write_json(config.METADATA_DIR / f"{dataset_id}_metadata.json", metadata)
        logger.info(
            "Metadata saved for %s: %s records",
            dataset_id,
            summary.get("records_count"),
        )

    write_json(config.METADATA_DIR / "dataset_catalog.json", catalog)
    write_json(config.METADATA_DIR / "metadata_errors.json", errors)
    build_inventory_report(catalog, errors)
    logger.info("Metadata inspection complete: %s valid datasets, %s errors", len(catalog), len(errors))


if __name__ == "__main__":
    main()
