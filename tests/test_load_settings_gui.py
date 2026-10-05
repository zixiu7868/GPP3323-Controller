"""Windows GUI integration using only a loopback fake instrument."""
import time
import unittest
from unittest.mock import patch

from gpp3323 import GPP3323Client
from gpp3323.load_control import apply_settings
from gui.main_window import MainWindow
from tests.fake_instrument import FakeGPP3323


class LoadSettingsGuiTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeGPP3323().start()
        self.client = GPP3323Client(*self.fake.address, timeout=1)
        self.client.connect()
        with patch.object(MainWindow, '_load_config', return_value={}):
            self.app = MainWindow()
        self.app.withdraw()
        self.app.client = self.client
        self.tab = self.app.load_settings_tab
        self.tab.set_connected(True)
        self.tab._next_refresh = float('inf')
        self.app.load_tab.set_connected(True)
        self.app.cc_step_tab.set_connected(True)
        self.app.channel_tab.set_connected(True)

    def tearDown(self):
        self.app.load_tab.stop()
        self.app.cc_step_tab.stop()
        self.pump(lambda: not self.app.load_tab._running and not self.app.cc_step_tab.running and not self.tab.busy)
        self.tab.set_connected(False)
        self.client.disconnect()
        for callback in self.app.tk.splitlist(self.app.tk.call('after', 'info')):
            self.app.tk.call('after', 'cancel', callback)
        self.app.destroy()
        self.fake.stop()

    def pump(self, condition):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            self.app.update()
            if condition():
                return
            time.sleep(.01)
        self.fail('GUI operation did not complete')

    def test_sync_source_and_independent_setpoints(self):
        self.tab.values[1].set('.015')
        self.tab.values[2].set('.02')
        self.tab.apply((1, 2))
        self.pump(lambda: not self.tab.busy)
        self.assertEqual(self.tab.actual[1].value, .015)
        self.assertEqual(self.tab.actual[2].value, .02)
        self.assertFalse(self.fake.output[1])
        self.tab.sync.set(True)
        self.tab.source.set('CH2')
        self.tab._sync_changed()
        self.assertEqual(self.tab.values[1].get(), '.02')
        self.assertEqual(str(self.tab.channel_controls[1]['value'].cget('state')), 'disabled')
        self.tab.values[2].set('.025')
        self.assertEqual(self.tab.values[1].get(), '.025')
        self.tab.apply((2,))
        self.pump(lambda: not self.tab.busy)
        self.assertEqual(self.tab.actual[1].value, .025)
        self.assertEqual(self.tab.actual[2].value, .025)

    def test_background_readback_preserves_unapplied_draft(self):
        apply_settings(self.client, {1: ('CC', .015), 2: ('CC', .02)})
        self.tab.read((1, 2))
        self.pump(lambda: not self.tab.busy)
        self.tab.values[1].set('.03')
        self.tab._submit(lambda client: self.tab._read_result(client, (1, 2)), command=False)
        self.pump(lambda: not self.tab._refreshing)
        self.assertEqual(self.tab.values[1].get(), '.03')
        self.assertIn('尚未套用', self.tab.statuses[1].get())
        self.assertIn('mW', self.tab.readings[1].get())

    def test_test_channel_locked_but_other_channel_remains_independent(self):
        load = self.app.load_tab
        load._running = True
        load.active_channel = 1
        self.tab._update_controls()
        self.assertEqual(str(self.tab.channel_controls[1]['apply'].cget('state')), 'disabled')
        self.assertEqual(str(self.tab.channel_controls[1]['off'].cget('state')), 'normal')
        self.assertEqual(str(self.tab.channel_controls[2]['apply'].cget('state')), 'normal')
        self.assertEqual(str(self.tab.apply_both.cget('state')), 'disabled')
        self.assertEqual(str(self.tab.off_both.cget('state')), 'normal')
        self.tab.apply((1,))
        self.assertFalse(self.tab.busy)
        load._running = False
        load.active_channel = None

    def test_off_stops_selected_load_test_and_keeps_other_channel_on(self):
        apply_settings(self.client, {1: ('CC', .015), 2: ('CC', .02)})
        self.client.enable_load_input(2)
        load = self.app.load_tab
        self.fake.external_voltage[1] = 3
        load.interval_var.set('.2')
        load.start()
        self.pump(lambda: bool(load.samples))
        self.assertEqual(str(self.tab.channel_controls[1]['apply'].cget('state')), 'disabled')
        self.tab.set_enabled((1,), False)
        self.pump(lambda: not self.tab.busy and not load._running)
        self.assertFalse(self.client.get_output(1))
        self.assertTrue(self.client.get_output(2))
        self.assertTrue(load.samples)
        self.assertEqual(str(self.app.notebook.tab(self.app.channel_tab, 'state')), 'normal')

    def test_both_on_then_off_and_no_legacy_settings_in_load_tab(self):
        apply_settings(self.client, {1: ('CC', .015), 2: ('CC', .02)})
        with patch('gui.load_settings_tab.messagebox.askyesno', return_value=True):
            self.tab.set_enabled((1, 2), True)
        self.pump(lambda: not self.tab.busy)
        self.assertTrue(self.client.get_output(1))
        self.assertTrue(self.client.get_output(2))
        self.tab.set_enabled((1, 2), False)
        self.pump(lambda: not self.tab.busy)
        self.assertFalse(self.client.get_output(1))
        self.assertFalse(self.client.get_output(2))
        self.assertFalse(hasattr(self.app.load_tab, 'apply_settings'))

    def test_off_stops_cc_steps_and_unlocks_settings(self):
        apply_settings(self.client, {1: ('CC', .015)})
        step = self.app.cc_step_tab
        step.fields['start'].set('.015')
        step.fields['end'].set('.02')
        step.fields['count'].set('2')
        step.fields['seconds'].set('1')
        step.fields['interval'].set('.2')
        step.start()
        self.pump(lambda: bool(step.samples))
        self.assertEqual(str(self.tab.channel_controls[1]['apply'].cget('state')), 'disabled')
        self.tab.set_enabled((1,), False)
        self.pump(lambda: not self.tab.busy and not step.running)
        self.assertFalse(self.client.get_output(1))
        self.assertEqual(str(self.tab.channel_controls[1]['apply'].cget('state')), 'normal')
