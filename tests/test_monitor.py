import unittest
from datetime import datetime

from gui.monitor_tab import (
    Sample,
    calculate_statistics,
    engineering_scale,
    format_engineering,
)


class MonitorHelpersTests(unittest.TestCase):
    def test_voltage_uses_millivolts_below_one_volt(self) -> None:
        self.assertEqual(engineering_scale([0.0001, 0.0002], "V"), (1000.0, "mV"))
        self.assertEqual(format_engineering(0.0002, "V"), "0.2 mV")

    def test_current_uses_milliamps_up_to_one_amp(self) -> None:
        self.assertEqual(engineering_scale([0.076, 0.084], "A"), (1000.0, "mA"))
        self.assertEqual(format_engineering(0.08, "A"), "80 mA")
        self.assertEqual(engineering_scale([1.01], "A"), (1.0, "A"))

    def test_channel_statistics(self) -> None:
        now = datetime.now()
        samples = [
            Sample(now, 0.0, 1, 1.0, 0.1, 0.1),
            Sample(now, 1.0, 1, 2.0, 0.2, 0.4),
            Sample(now, 2.0, 1, 3.0, 0.3, 0.9),
        ]
        stats = calculate_statistics(samples)[1]
        self.assertEqual(stats.latest_voltage, 3.0)
        self.assertEqual(stats.voltage_min, 1.0)
        self.assertEqual(stats.voltage_max, 3.0)
        self.assertEqual(stats.voltage_average, 2.0)
        self.assertAlmostEqual(stats.current_average, 0.2)


if __name__ == "__main__":
    unittest.main()
