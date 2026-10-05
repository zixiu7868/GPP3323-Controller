import unittest
import threading
from unittest.mock import Mock

from gpp3323 import Measurement
from gui.cc_step_tab import build_steps, run_steps


class FakeTime:
    def __init__(self):
        self.now = 0
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, seconds):
        self.now += seconds
        return self.stopped


class StepTests(unittest.TestCase):
    def test_endpoints_and_descending(self):
        self.assertEqual(build_steps(0, 1, 3, 2, 0.5), [0, 0.5, 1])
        self.assertEqual(build_steps(1, 0, 3, 2, 0.5), [1, 0.5, 0])

    def test_invalid_parameters(self):
        for args in [(float('nan'), 1, 3, 2, .5), (0, 4, 3, 2, .5),
                     (0, 1, 1, 2, .5), (0, 1, 3, 0, .5), (0, 1, 3, 2, 3)]:
            with self.assertRaises(ValueError):
                build_steps(*args)

    def run_case(self, failure=False, cancel=False, mode="CC"):
        clock = FakeTime()
        client = Mock()
        client._lock = threading.RLock()
        client.get_channel_mode.return_value = mode
        client.is_load_mode.return_value = mode == "CC"
        client.enable_load_input.return_value = ("CC", True)
        client.measure.return_value = Measurement(5, .1, .5)
        if failure:
            client.measure.side_effect = RuntimeError("connection lost")
        events = []
        def emit(kind, value):
            events.append((kind, value))
            if cancel and kind == "sample":
                clock.stopped = True
        run_steps(client, 1, [.1, .2, .3], 2, .5, clock, emit, lambda: clock.now)
        return client, events, clock

    def test_dwell_and_cleanup(self):
        client, events, clock = self.run_case()
        self.assertEqual([v[0] for k, v in events if k == "step"], [0, 2, 4])
        self.assertEqual(clock.now, 6)
        self.assertEqual([c.args[1] for c in client.set_load_current.call_args_list], [.1, .2, .3])
        client.set_output.assert_called_with(1, False)

    def test_error_and_cancel_cleanup(self):
        for kwargs in ({"failure": True}, {"cancel": True}):
            client, events, _ = self.run_case(**kwargs)
            client.set_output.assert_called_with(1, False)
            self.assertEqual(client.set_load_current.call_count, 1)
            self.assertEqual(events[-1], ("done", None))

    def test_wrong_mode_never_enables(self):
        client, events, _ = self.run_case(mode="CV")
        client.enable_load_input.assert_not_called()
        self.assertTrue(any(k == "error" for k, _ in events))
