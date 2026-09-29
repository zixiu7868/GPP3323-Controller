import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from gui.load_tab import (
    estimate_remaining_capacity_mah,
    estimate_time_to_voltage,
    evaluate_stop_condition,
    format_estimated_time,
    next_screenshot_path,
)


class LoadStopConditionTests(unittest.TestCase):
    def test_duration_stops_at_target_time(self) -> None:
        self.assertEqual(evaluate_stop_condition("duration", 10.0, 9.9, 5.0, False), (False, False))
        self.assertEqual(evaluate_stop_condition("duration", 10.0, 10.0, 5.0, False), (True, False))

    def test_cutoff_waits_for_voltage_above_target(self) -> None:
        self.assertEqual(evaluate_stop_condition("cutoff", 3.0, 0.0, 0.0, False), (False, False))
        self.assertEqual(evaluate_stop_condition("cutoff", 3.0, 1.0, 4.2, False), (False, True))
        self.assertEqual(evaluate_stop_condition("cutoff", 3.0, 2.0, 3.0, True), (True, True))

    def test_screenshot_filename_uses_time_and_sequence(self) -> None:
        timestamp = datetime(2026, 8, 28, 18, 30, 45)
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            first = next_screenshot_path(directory, timestamp)
            self.assertEqual(first.name, "gpp3323_screen_20260828_183045_001.png")
            first.touch()
            second = next_screenshot_path(directory, timestamp)
            self.assertEqual(second.name, "gpp3323_screen_20260828_183045_002.png")

    def test_estimates_time_to_two_volts_after_thirty_seconds(self) -> None:
        samples = [(0.0, 4.0), (10.0, 3.8), (20.0, 3.6), (30.0, 3.4)]
        estimate = estimate_time_to_voltage(samples)
        self.assertIsNotNone(estimate)
        self.assertAlmostEqual(estimate or 0.0, 100.0)

    def test_estimate_waits_for_thirty_seconds(self) -> None:
        self.assertIsNone(estimate_time_to_voltage([(0.0, 4.0), (29.9, 3.5)]))

    def test_estimate_rejects_flat_or_rising_voltage(self) -> None:
        self.assertIsNone(estimate_time_to_voltage([(0.0, 3.0), (30.0, 3.0)]))
        self.assertIsNone(estimate_time_to_voltage([(0.0, 3.0), (30.0, 3.1)]))

    def test_formats_estimated_time(self) -> None:
        self.assertEqual(format_estimated_time(45.0), "45 秒")
        self.assertEqual(format_estimated_time(90.0), "1.5 分鐘")
        self.assertEqual(format_estimated_time(7200.0), "2.00 小時")

    def test_estimates_cc_capacity_remaining_to_cutoff(self) -> None:
        capacity = estimate_remaining_capacity_mah(
            estimated_total_seconds=7200.0,
            elapsed_seconds=1800.0,
            measured_currents=[0.0098, 0.0100, 0.0102],
        )
        self.assertIsNotNone(capacity)
        self.assertAlmostEqual(capacity or 0.0, 15.0)

    def test_capacity_estimate_requires_nonzero_current(self) -> None:
        self.assertIsNone(estimate_remaining_capacity_mah(3600.0, 30.0, [0.0, 0.0]))


if __name__ == "__main__":
    unittest.main()
