import unittest
from unittest.mock import patch

from gpp3323 import GPP3323Client
from gpp3323.load_control import apply_settings, read_state, set_inputs
from tests.fake_instrument import FakeGPP3323


class LoadControlTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGPP3323().start()
        self.client = GPP3323Client(*self.fake.address, timeout=1)
        self.client.connect()

    def tearDown(self):
        self.client.disconnect()
        self.fake.stop()

    def prepare(self):
        result = apply_settings(self.client, {1: ('CC', .015), 2: ('CC', .02)})
        self.assertFalse(result.errors)
        return result

    def test_independent_settings_and_inputs(self):
        result = self.prepare()
        self.assertEqual(result.completed, {1, 2})
        self.assertEqual(result.states[1].value, .015)
        self.assertEqual(result.states[2].value, .02)
        self.assertFalse(any(self.fake.output.values()))
        self.assertFalse(set_inputs(self.client, (1,), True).errors)
        self.assertTrue(self.client.get_output(1))
        self.assertFalse(self.client.get_output(2))
        self.assertFalse(set_inputs(self.client, (1, 2), True).errors)
        self.assertTrue(self.client.get_output(2))
        self.assertFalse(set_inputs(self.client, (1, 2), False).errors)
        self.assertFalse(self.client.get_output(1))
        self.assertFalse(self.client.get_output(2))

    def test_synced_setting_and_readback(self):
        result = apply_settings(self.client, {1: ('CR', 150), 2: ('CR', 150)})
        self.assertFalse(result.errors)
        self.assertEqual([result.states[c].value for c in (1, 2)], [150, 150])
        self.assertIsNotNone(read_state(self.client, 1, measure=True).measurement)

    def test_preflight_block_prevents_all_setting_writes(self):
        self.fake.external_voltage[2] = 3
        result = apply_settings(self.client, {1: ('CC', .015), 2: ('CC', .02)})
        self.assertIn(2, result.errors)
        self.assertEqual(result.completed, set())
        self.assertEqual(self.fake.mode[1], 'INDEPENDENT')
        self.assertEqual(self.fake.current[1], 0)
        result = apply_settings(self.client, {1: ('CC', .015), 2: ('CC', float('nan'))})
        self.assertIn(2, result.errors)
        self.assertEqual(self.fake.current[1], 0)

    def test_apply_failure_reports_partial_completion(self):
        original = self.client.configure_load
        def fail_second(channel, mode, value):
            if channel == 2:
                raise RuntimeError('write failed')
            return original(channel, mode, value)
        with patch.object(self.client, 'configure_load', side_effect=fail_second):
            result = apply_settings(self.client, {1: ('CC', .015), 2: ('CC', .02)})
        self.assertEqual(result.completed, {1})
        self.assertIn(2, result.errors)
        self.assertEqual(self.client.get_load_setting(1, 'CC'), .015)

    def test_readback_mismatch_is_not_success(self):
        with patch.object(self.client, 'get_load_setting', return_value=.01):
            result = apply_settings(self.client, {1: ('CC', .015)})
        self.assertIn(1, result.errors)
        self.assertFalse(result.completed)

    def test_on_preflight_rejects_power_mode_without_enabling_other_channel(self):
        apply_settings(self.client, {1: ('CC', .015)})
        result = set_inputs(self.client, (1, 2), True)
        self.assertIn(2, result.errors)
        self.assertFalse(self.client.get_output(1))

    def test_failed_on_attempt_rolls_back_even_if_command_reached_device(self):
        self.prepare()
        original = self.client.enable_load_input
        def fail_second(channel):
            result = original(channel)
            if channel == 2:
                raise RuntimeError('response lost after ON')
            return result
        with patch.object(self.client, 'enable_load_input', side_effect=fail_second):
            result = set_inputs(self.client, (1, 2), True)
        self.assertIn(2, result.errors)
        self.assertEqual(set(result.rollback), {1, 2})
        self.assertFalse(self.client.get_output(1))
        self.assertFalse(self.client.get_output(2))

    def test_rollback_preserves_preexisting_on_channel(self):
        self.prepare()
        self.client.enable_load_input(1)
        with patch.object(self.client, 'enable_load_input', side_effect=RuntimeError('failed')):
            result = set_inputs(self.client, (1, 2), True)
        self.assertTrue(self.client.get_output(1))
        self.assertNotIn(1, result.rollback)
        self.assertFalse(self.client.get_output(2))

    def test_off_attempts_other_channel_after_one_failure(self):
        self.prepare()
        set_inputs(self.client, (1, 2), True)
        original = self.client.set_output
        def fail_first(channel, enabled):
            if channel == 1:
                raise RuntimeError('first channel failed')
            return original(channel, enabled)
        with patch.object(self.client, 'set_output', side_effect=fail_first):
            result = set_inputs(self.client, (1, 2), False)
        self.assertIn(1, result.errors)
        self.assertIn(2, result.completed)
        self.assertFalse(self.client.get_output(2))

    def test_load_off_does_not_switch_power_mode_output(self):
        self.client.set_output(1, True)
        result = set_inputs(self.client, (1, 2), False)
        self.assertIn(1, result.errors)
        self.assertTrue(self.client.get_output(1))
        self.assertIn(2, result.completed)
