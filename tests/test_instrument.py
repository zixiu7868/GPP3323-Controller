import socket
import unittest

from gpp3323 import GPP3323Client, LoadVoltagePresentError, ResponseTimeoutError
from tests.fake_instrument import FakeGPP3323


class InstrumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fake = FakeGPP3323().start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.fake.stop()

    def setUp(self) -> None:
        self.fake.mode = {1: "INDEPENDENT", 2: "INDEPENDENT"}
        self.fake.external_voltage = {1: 0.0, 2: 0.0, 3: 0.0}
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

    def test_measure_retries_wait_once_without_resending_command(self) -> None:
        class DelayedResponseSocket:
            def __init__(self) -> None:
                self.sent: list[bytes] = []
                self.recv_count = 0

            def sendall(self, data: bytes) -> None:
                self.sent.append(data)

            def recv(self, _size: int) -> bytes:
                self.recv_count += 1
                if self.recv_count == 1:
                    raise socket.timeout
                return b"5.00000,0.10000,0.50000\n"

        client = GPP3323Client(timeout=3)
        delayed_socket = DelayedResponseSocket()
        client._socket = delayed_socket  # type: ignore[assignment]
        notices: list[tuple[int, int]] = []

        reading = client.measure(
            1,
            timeout_retries=1,
            on_timeout=lambda attempt, total: notices.append((attempt, total)),
        )

        self.assertEqual(delayed_socket.sent, [b":MEASure1:ALL?\n"])
        self.assertEqual(notices, [(1, 1)])
        self.assertAlmostEqual(reading.power, 0.5)

    def test_measure_raises_after_one_timeout_retry(self) -> None:
        class NoResponseSocket:
            def __init__(self) -> None:
                self.sent: list[bytes] = []

            def sendall(self, data: bytes) -> None:
                self.sent.append(data)

            def recv(self, _size: int) -> bytes:
                raise socket.timeout

        client = GPP3323Client(timeout=3)
        no_response_socket = NoResponseSocket()
        client._socket = no_response_socket  # type: ignore[assignment]

        with self.assertRaises(ResponseTimeoutError):
            client.measure(1, timeout_retries=1)

        self.assertEqual(no_response_socket.sent, [b":MEASure1:ALL?\n"])

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

    def test_configure_and_read_load_modes(self) -> None:
        self.assertEqual(self.client.configure_load(1, "CC", 1.25), '0,"No error"')
        self.assertEqual(self.client.get_channel_mode(1), "CC LOAD")
        self.assertAlmostEqual(self.client.get_load_setting(1, "CC"), 1.25)
        self.client.configure_load(2, "CV", 12.5)
        self.assertAlmostEqual(self.client.get_load_setting(2, "CV"), 12.5)
        self.client.configure_load(1, "CR", 220)
        self.assertAlmostEqual(self.client.get_load_setting(1, "CR"), 220)

    def test_load_validation(self) -> None:
        with self.assertRaises(ValueError):
            self.client.set_load_mode(3, "CC")
        with self.assertRaises(ValueError):
            self.client.configure_load(1, "CP", 1)
        with self.assertRaises(ValueError):
            self.client.set_load_voltage(1, 1.49)
        with self.assertRaises(ValueError):
            self.client.set_load_current(1, 3.21)
        with self.assertRaises(ValueError):
            self.client.set_load_resistance(2, 1001)

    def test_load_mode_is_blocked_when_terminal_has_voltage(self) -> None:
        mode_before = self.client.get_channel_mode(1)
        self.fake.external_voltage[1] = 5.0
        try:
            with self.assertRaises(LoadVoltagePresentError) as context:
                self.client.configure_load(1, "CC", 0.5)
            self.assertAlmostEqual(context.exception.voltage, 5.0)
            self.assertEqual(self.client.get_channel_mode(1), mode_before)
        finally:
            self.fake.external_voltage[1] = 0.0

    def test_load_input_startup_off_and_explicit_enable(self) -> None:
        self.client.configure_load(1, "CC", 0.5)
        self.client.set_output(1, True)
        states = self.client.ensure_load_inputs_off()
        self.assertEqual(states[1], ("CC LOAD", False))
        self.assertFalse(self.client.get_output(1))
        self.assertEqual(self.client.enable_load_input(1), ("CC LOAD", True))
        self.assertTrue(self.client.get_output(1))

    def test_same_load_mode_updates_with_external_voltage(self) -> None:
        for mode, value in (("CC", 0.7), ("CV", 4.0), ("CR", 200.0)):
            for enabled in (False, True):
                with self.subTest(mode=mode, enabled=enabled):
                    self.fake.mode[1] = f"{mode} LOAD"
                    self.fake.external_voltage[1] = 5.0
                    self.client.set_output(1, enabled)
                    self.client.configure_load(1, mode, value)
                    self.assertAlmostEqual(self.client.get_load_setting(1, mode), value)
                    self.assertEqual(self.client.get_output(1), enabled)

    def test_different_load_mode_still_checks_voltage(self) -> None:
        self.fake.mode[1] = "CC LOAD"
        self.fake.external_voltage[1] = 5.0
        with self.assertRaises(LoadVoltagePresentError):
            self.client.configure_load(1, "CV", 4.0)
        self.assertEqual(self.client.get_channel_mode(1), "CC LOAD")

    def test_load_input_enable_requires_load_mode(self) -> None:
        with self.assertRaisesRegex(Exception, "不是 Load Mode"):
            self.client.enable_load_input(2)

    def test_load_mode_reply_formats(self) -> None:
        for reply in ("CC", "CV", "CR", "CC LOAD", "CV_Load", "cr-load", "CCLOAD"):
            with self.subTest(reply=reply):
                self.assertTrue(self.client.is_load_mode(reply))
        for reply in ("INDEPENDENT", "SERIES", "PARALLEL", ""):
            with self.subTest(reply=reply):
                self.assertFalse(self.client.is_load_mode(reply))


if __name__ == "__main__":
    unittest.main()
