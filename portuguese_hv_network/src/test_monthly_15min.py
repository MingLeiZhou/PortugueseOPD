"""Unit tests for the database-backed monthly 15-minute runner."""
import unittest
import tempfile
from pathlib import Path

import duckdb
import numpy as np

from run_monthly_15min import (
    DatabaseInputs,
    _case_from_interval,
    _create_output_schema,
    _store_complete_case,
    month_bounds,
)


class Monthly15MinuteTests(unittest.TestCase):
    def test_month_bounds_are_half_open(self):
        start, end = month_bounds("2025-10")
        self.assertEqual(str(start.date()), "2025-10-01")
        self.assertEqual(str(end.date()), "2025-11-01")

    def test_invalid_month_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM"):
            month_bounds("2025-1")

    def test_interval_case_has_no_daily_regime_selection(self):
        interval = {
            "case_id": "PT60_MONTH_20251001_0000_0100",
            "local_date": "2025-10-01",
            "timestamp_utc": "2025-09-30T23:00:00+00:00",
            "eredes_alignment_status": "UNIQUE",
        }
        case = _case_from_interval(interval, Path("/tmp/model.npz"))
        self.assertEqual(case["analysis_role"], "FULL_15MIN_MONTHLY_STEADY_STATE")
        self.assertNotIn("selection_rule", case)
        self.assertNotIn("selection_snapshot_kind", case)

    def test_boundary_weather_uses_nearest_hour_with_explicit_status(self):
        connection = duckdb.connect(":memory:")
        connection.execute("ATTACH ':memory:' AS source")
        connection.execute("""
            CREATE TABLE source.main.weather_hourly (
                time VARCHAR, location VARCHAR, temperature_2m DOUBLE,
                wind_speed_10m DOUBLE, shortwave_radiation DOUBLE
            )
        """)
        connection.executemany(
            "INSERT INTO source.main.weather_hourly VALUES (?, ?, ?, ?, ?)",
            [("2025-05-01T00:00", location, 12.0, 5.0, 0.0)
             for location in ("north", "center", "south")],
        )
        inputs = DatabaseInputs(connection, Path("source.duckdb"))
        weather = inputs.weather({"timestamp_utc": "2025-04-30T23:00:00+00:00"})
        self.assertEqual(weather["weather_alignment_status"], "NEAREST_AVAILABLE_BOUNDARY_HOUR")
        self.assertEqual(weather["weather_sample_offset_minutes"], 60)
        connection.close()

    def test_dst_fold_snapshot_averages_unidentified_duplicate_observations(self):
        connection = duckdb.connect(":memory:")
        connection.execute("ATTACH ':memory:' AS source")
        connection.execute("""
            CREATE TABLE source.main.eredes_load (
                codigo_subestacao VARCHAR, subestacao VARCHAR, energia DOUBLE,
                dataset VARCHAR, data DATE, hora VARCHAR
            )
        """)
        connection.executemany(
            "INSERT INTO source.main.eredes_load VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("A", "Alpha", 100.0, "part", "2025-10-26", "01:15"),
                ("A", "Alpha", 140.0, "part", "2025-10-26", "01:15"),
                ("B", "Beta", 80.0, "part", "2025-10-26", "01:15"),
            ],
        )
        inputs = DatabaseInputs(connection, Path("source.duckdb"))
        snapshot = inputs.load_snapshot({
            "eredes_source_date": "2025-10-26",
            "eredes_source_hour": "01:15",
            "eredes_alignment_status": "AMBIGUOUS_DST_FOLD",
        })
        values = dict(zip(snapshot.codigo_subestacao, snapshot.energia))
        self.assertEqual(values, {"A": 120.0, "B": 80.0})
        self.assertEqual(len(snapshot), 2)
        connection.close()

    def test_compact_arrays_have_stable_sorted_device_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            spatial = root / "state.npz"
            np.savez(
                spatial,
                bus_id=np.array(["bus-2", "bus-1"]),
                vm_pu=np.array([1.02, 0.99]),
                line_id=np.array(["line-2", "line-1"]),
                loading_percent=np.array([42.0, 21.0]),
            )
            connection = duckdb.connect(str(root / "compact.duckdb"))
            _create_output_schema(connection)
            interval = {
                "case_id": "CASE_1",
                "local_date": "2025-10-01",
                "source_index": 0,
                "eredes_alignment_status": "UNIQUE",
                "timestamp_local": "2025-10-01T00:00:00+01:00",
                "timestamp_utc": "2025-09-30T23:00:00+00:00",
            }
            result = {
                "converged": True,
                "observed_load_mw": 1.0,
                "observed_generation_total_mw": 1.0,
                "observed_net_import_mw": 0.0,
                "model_net_import_mw": 0.0,
                "vm_pu_min": 0.99,
                "vm_pu_max": 1.02,
                "maximum_line_loading_percent": 42.0,
                "lines_over_100_percent": 0,
                "maximum_transformer_loading_percent": 10.0,
                "model_losses_percent_of_load": 1.0,
            }
            _store_complete_case(
                connection, "RUN", interval, result, 0.1, spatial,
                [("LOSS", [{"loss": 1.0}])],
            )
            self.assertEqual(
                connection.execute("""
                    SELECT bus_id, vm_pu FROM monthly_model.bus_states_expanded
                    ORDER BY bus_id
                """).fetchall(),
                [("bus-1", 0.99), ("bus-2", 1.02)],
            )
            self.assertEqual(
                connection.execute("""
                    SELECT length(bus_vm_pu), length(line_loading_percent)
                    FROM monthly_model.state_arrays
                """).fetchone(),
                (2, 2),
            )
            connection.close()


if __name__ == "__main__":
    unittest.main()
