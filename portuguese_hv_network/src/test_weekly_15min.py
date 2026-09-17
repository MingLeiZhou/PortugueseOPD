"""Unit tests for the full 15-minute range runner."""
import unittest

from run_weekly_15min import interval_schedule


class Weekly15MinuteTests(unittest.TestCase):
    def test_two_ordinary_days_have_192_intervals(self):
        rows = interval_schedule("2025-07-07", "2025-07-08")
        self.assertEqual(len(rows), 192)
        self.assertEqual(len({row["case_id"] for row in rows}), 192)

    def test_spring_dst_day_has_92_intervals(self):
        self.assertEqual(len(interval_schedule("2025-03-30", "2025-03-30")), 92)

    def test_autumn_dst_day_has_100_intervals(self):
        self.assertEqual(len(interval_schedule("2025-10-26", "2025-10-26")), 100)


if __name__ == "__main__":
    unittest.main()
