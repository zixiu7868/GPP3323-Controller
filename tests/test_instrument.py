import unittest

from gpp3323 import GPP3323Client
from tests.fake_instrument import FakeGPP3323


class InstrumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fake = FakeGPP3323().start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fake.stop()

    def setUp(self) -> None:
        host, port = self.fake.address
        self.client = GPP3323Client(host, port, timeout=1)
        self.client.connect()
        self.client.all_outputs_off()

    def tearDown(self) -> None:
        self.client.disconnect()

    def test_identity(self) -> None:
        self.assertIn("GPP-3323", self.client.identity)

    def test_set_read_and_measure_channel_1(self) -> None:
        self.client.set_voltage(1, 5.0)
        self.client.set_current(1, 0.5)
        self.client.set_output(1, True)
        self.assertAlmostEqual(self.client.get_voltage_setting(1), 5.0)
        self.assertAlmostEqual(self.client.get_current_setting(1), 0.5)
        self.assertTrue(self.client.get_output(1))
        reading = self.client.measure(1)
        self.assertAlmostEqual(reading.voltage, 5.0)
        self.assertAlmostEqual(reading.current, 0.5)
        self.assertAlmostEqual(reading.power, 2.5)

    def test_channel_3_restrictions(self) -> None:
        self.client.set_voltage(3, 3.3)
        with self.assertRaises(ValueError):
            self.client.set_voltage(3, 4.0)
        with self.assertRaises(ValueError):
            self.client.set_current(3, 1.0)

    def test_range_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.client.set_voltage(1, 33.0)
        with self.assertRaises(ValueError):
            self.client.set_current(2, 3.1)


if __name__ == "__main__":
    unittest.main()
