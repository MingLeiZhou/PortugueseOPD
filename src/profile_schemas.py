import config
from utils import (
    ensure_directories,
    get_logger,
    load_catalog,
    load_dataset_frame,
    markdown_table,
    profile_column,
    save_dataframe,
    utc_now,
    write_text,
)

import pandas as pd


def main():
    ensure_directories()
    logger = get_logger("profile_schemas")
    catalog = load_catalog()
    report_rows = []
    for dataset_id, meta in sorted(catalog.items()):
        df, source_path = load_dataset_frame(dataset_id)
        if df.empty:
            logger.warning("No local table available for schema profile: %s", dataset_id)
            continue
        official_types = meta.get("field_types", {})
        rows = [
            profile_column(df, column, official_types.get(column))
            for column in df.columns
        ]
        profile_df = pd.DataFrame(rows)
        save_dataframe(profile_df, config.METADATA_DIR / f"{dataset_id}_schema_profile.csv")
        report_rows.append(
            {
                "Dataset": dataset_id,
                "Rows inspected": len(df),
                "Source": source_path,
                "Columns": len(df.columns),
                "Key-like": int(profile_df["looks_like_key_field"].sum()),
                "Location": int(profile_df["looks_like_location_field"].sum()),
                "Electrical": int(profile_df["looks_like_grid_electrical_field"].sum()),
                "High missing fields": int((profile_df["missing_value_pct"] > 50).sum()),
            }
        )
        logger.info("Profiled %s from %s", dataset_id, source_path)

    text = [
        "# 02 Schema Profile",
        "",
        f"Generated: {utc_now()}",
        "",
        "Profiles are saved as `data/metadata/{dataset_id}_schema_profile.csv`. Raw exports are used where present; otherwise the first 1000-record sample is used.",
        "",
        markdown_table(
            report_rows,
            [
                "Dataset",
                "Rows inspected",
                "Source",
                "Columns",
                "Key-like",
                "Location",
                "Electrical",
                "High missing fields",
            ],
        ),
    ]
    write_text(config.REPORTS_DIR / "02_schema_profile.md", "\n".join(text))
    logger.info("Schema profiling complete")


if __name__ == "__main__":
    main()
