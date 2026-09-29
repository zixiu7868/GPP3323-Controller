import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from gui.load_tab import evaluate_stop_condition, next_screenshot_path


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


if __name__ == "__main__":
    unittest.main()
