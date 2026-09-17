"""Tests for download-only temporal database helpers."""
import logging
import unittest

import duckdb

from download_temporal_database import (
    DEFAULT_STATIC_RELEASE,
    _interval_calendar,
    _months,
    import_static_network_model,
)


class TemporalDatabaseTests(unittest.TestCase):
    def test_month_chunks_are_inclusive_and_non_overlapping(self):
        chunks = list(_months("2025-05-15", "2025-07-02"))
        self.assertEqual([(str(a.date()), str(b.date())) for a, b in chunks], [
            ("2025-05-15", "2025-05-31"),
            ("2025-06-01", "2025-06-30"),
            ("2025-07-01", "2025-07-02"),
        ])

    def test_calendar_handles_dst_days(self):
        self.assertEqual(len(_interval_calendar("2025-03-30", "2025-03-30")), 92)
        autumn = _interval_calendar("2025-10-26", "2025-10-26")
        self.assertEqual(len(autumn), 100)
        self.assertGreater((autumn.eredes_alignment_status == "AMBIGUOUS_DST_FOLD").sum(), 0)
        ambiguous = autumn[autumn.eredes_alignment_status == "AMBIGUOUS_DST_FOLD"]
        self.assertFalse(ambiguous.eredes_source_date.isna().any())
        self.assertFalse(ambiguous.eredes_source_hour.isna().any())

    def test_static_release_import_has_complete_branch_endpoints(self):
        con = duckdb.connect(":memory:")
        try:
            counts = import_static_network_model(
                con, DEFAULT_STATIC_RELEASE, logging.getLogger("static-import-test")
            )
            self.assertEqual(counts["buses"], 3783)
            self.assertEqual(counts["lines"], 4943)
            self.assertEqual(counts["transformers"], 228)
            orphan_lines = con.execute("""
                SELECT count(*)
                FROM grid.lines l
                LEFT JOIN grid.buses a ON l.from_bus = a.bus_id
                LEFT JOIN grid.buses b ON l.to_bus = b.bus_id
                WHERE a.bus_id IS NULL OR b.bus_id IS NULL
            """).fetchone()[0]
            orphan_transformers = con.execute("""
                SELECT count(*)
                FROM grid.transformers t
                LEFT JOIN grid.buses a ON t.hv_bus = a.bus_id
                LEFT JOIN grid.buses b ON t.lv_bus = b.bus_id
                WHERE a.bus_id IS NULL OR b.bus_id IS NULL
            """).fetchone()[0]
            self.assertEqual(orphan_lines, 0)
            self.assertEqual(orphan_transformers, 0)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
