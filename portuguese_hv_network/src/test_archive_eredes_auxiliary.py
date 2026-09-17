"""Tests for the E-REDES auxiliary evidence archiver."""
import json
import tempfile
import unittest
from pathlib import Path

import duckdb
import pandas as pd

from archive_eredes_auxiliary import (
    CORE_DATASETS,
    EXTENDED_DATASETS,
    import_into_duckdb,
    selected_datasets,
    table_name,
)


class EredesAuxiliaryArchiveTests(unittest.TestCase):
    def test_table_names_are_valid_and_stable(self):
        self.assertEqual(table_name("8-total-upac-mensal"), "dataset_8_total_upac_mensal")
        self.assertEqual(table_name("energia_injectada_upac"), "energia_injectada_upac")

    def test_profiles_and_explicit_selection(self):
        core = selected_datasets("core", [])
        extended = selected_datasets("extended", [])
        self.assertEqual(core, CORE_DATASETS)
        self.assertEqual(set(extended), set(CORE_DATASETS) | set(EXTENDED_DATASETS))
        explicit = selected_datasets("core", ["custom-dataset"])
        self.assertEqual(list(explicit), ["custom-dataset"])

    def test_import_preserves_rows_and_provenance(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "parquet" / "eredes_auxiliary" / "dataset=example"
            archive.mkdir(parents=True)
            parquet = archive / "snapshot=2026-09-16.parquet"
            pd.DataFrame({"region": ["A", "B"], "value": [1.0, 2.0]}).to_parquet(parquet, index=False)
            import hashlib
            digest = hashlib.sha256(parquet.read_bytes()).hexdigest()
            database = root / "pt60.duckdb"
            duckdb.connect(str(database)).close()
            record = {
                "source": "E-REDES",
                "dataset_id": "example",
                "table_name": "example",
                "role": "test",
                "description": "test dataset",
                "source_url": "https://example.invalid/exports/parquet",
                "license": "CC BY 4.0",
                "snapshot_date_utc": "2026-09-16",
                "checked_at_utc": "2026-09-16T12:00:00+00:00",
                "relative_path": str(parquet.relative_to(root)),
                "bytes": parquet.stat().st_size,
                "sha256": digest,
                "row_count": 2,
                "column_names": ["region", "value"],
                "cached": False,
            }
            import logging
            import_into_duckdb(database, root, [record], logging.getLogger("test"))
            connection = duckdb.connect(str(database), read_only=True)
            try:
                self.assertEqual(
                    connection.execute("SELECT count(*) FROM raw_eredes_aux.example").fetchone()[0], 2
                )
                catalog = connection.execute(
                    "SELECT dataset_id, row_count FROM validation.eredes_auxiliary_catalog"
                ).fetchone()
                self.assertEqual(catalog, ("example", 2))
                columns = connection.execute(
                    "SELECT column_names FROM provenance.eredes_auxiliary_files"
                ).fetchone()[0]
                self.assertEqual(json.loads(columns), ["region", "value"])
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
