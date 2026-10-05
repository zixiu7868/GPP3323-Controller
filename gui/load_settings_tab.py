from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from gpp3323.i18n import tr
from gpp3323.load_control import LoadResult, apply_settings, read_state, set_inputs, validate_setting
from gui.curve_panel import format_power
from gui.monitor_tab import format_engineering


class LoadSettingsTab(ttk.Frame):
    MODE_INFO = {'CC': ('A', '0–3.2 A', '0.1000'),
                 'CV': ('V', '1.5–33 V', '5.000'), 'CR': ('Ω', '1–1000 Ω', '100')}

    def __init__(self, parent, client_getter, active_channels, stop_channel, config, on_state=lambda *_: None):
        super().__init__(parent, padding=12)
        self.client_getter = client_getter
        self.active_channels, self.stop_channel, self.on_state = active_channels, stop_channel, on_state
        self.connected = False
        self._commands = 0
        self._refreshing = False
        self._off_pending = False
        self._epoch = 0
        self._generation = 0
        self._mirroring = False
        self._next_refresh = 0.0
        self.events = queue.Queue()
        self.actual = {}
        self.modes, self.values, self.units, self.statuses, self.readings = {}, {}, {}, {}, {}
        self.channel_controls = {}
        self.sync = tk.BooleanVar(value=bool(config.get('load_sync_settings', False)))
        self.source = tk.StringVar(value=str(config.get('load_sync_source', 'CH1')))
        if self.source.get() not in ('CH1', 'CH2'):
            self.source.set('CH1')
        self.result_var = tk.StringVar(value=tr('設定不會自動啟用負載；ON/OFF 可獨立操作。'))
        ttk.Label(self, text=tr('僅限 CH1 / CH2。設定不會自動啟用負載；啟用前請確認極性、外部電源及單 Channel 50 W 限制。'),
                  wraplength=950, foreground='#9a4b00').pack(fill='x', pady=(0, 12))
        cards = ttk.Frame(self)
        cards.pack(fill='x')
        for channel in (1, 2):
            self._build_channel(cards, channel, config)
        shared = ttk.LabelFrame(self, text=tr('共同控制'), padding=12)
        shared.pack(fill='x', pady=12)
        self.sync_button = ttk.Checkbutton(shared, text=tr('同步設定 CH1 / CH2'), variable=self.sync,
                                          command=self._sync_changed)
        self.sync_button.grid(row=0, column=0, sticky='w')
        ttk.Label(shared, text=tr('同步來源：')).grid(row=0, column=1, padx=(16, 4))
        self.source_box = ttk.Combobox(shared, textvariable=self.source, values=('CH1', 'CH2'), state='readonly', width=6)
        self.source_box.grid(row=0, column=2)
        self.source_box.bind('<<ComboboxSelected>>', lambda _: self._sync_changed())
        self.copy_buttons = []
        for column, (source, target) in enumerate(((1, 2), (2, 1)), 3):
            button = ttk.Button(shared, text=f'CH{source} → CH{target}', command=lambda s=source, t=target: self.copy(s, t))
            button.grid(row=0, column=column, padx=8)
            self.copy_buttons.append(button)
        self.apply_both = ttk.Button(shared, text=tr('套用兩通道'), command=lambda: self.apply((1, 2)))
        self.apply_both.grid(row=1, column=0, sticky='w', pady=(12, 0))
        self.on_both = ttk.Button(shared, text=tr('兩通道 ON'), command=lambda: self.set_enabled((1, 2), True))
        self.on_both.grid(row=1, column=1, columnspan=2, pady=(12, 0))
        self.off_both = ttk.Button(shared, text=tr('兩通道 OFF'), command=lambda: self.set_enabled((1, 2), False))
        self.off_both.grid(row=1, column=3, pady=(12, 0))
        self.read_both = ttk.Button(shared, text=tr('讀回兩通道'), command=lambda: self.read((1, 2)))
        self.read_both.grid(row=1, column=4, pady=(12, 0))
        ttk.Label(shared, text=tr('共同 ON/OFF 依序發送指令，會有短暫時間差。'), wraplength=900).grid(
            row=2, column=0, columnspan=5, sticky='w', pady=(12, 0))
        ttk.Label(self, textvariable=self.result_var, wraplength=950, justify='left').pack(fill='x')
        self._sync_changed()
        self._update_controls()
        self.after(100, self._poll)

    @property
    def busy(self):
        return self._commands > 0

    def _build_channel(self, parent, channel, config):
        card = ttk.LabelFrame(parent, text=f'CH{channel}', padding=14)
        card.pack(side='left', fill='both', expand=True, padx=(0, 8))
        mode = config.get(f'load_ch{channel}_mode', 'CC')
        if mode not in self.MODE_INFO:
            mode = 'CC'
        self.modes[channel] = tk.StringVar(value=mode)
        self.values[channel] = tk.StringVar(value=str(config.get(f'load_ch{channel}_value', self.MODE_INFO[mode][2])))
        self.units[channel] = tk.StringVar()
        self.statuses[channel] = tk.StringVar(value=tr('尚未讀回設備狀態'))
        self.readings[channel] = tk.StringVar(value=tr('尚無量測資料'))
        ttk.Label(card, text=tr('模式')).grid(row=0, column=0, sticky='w')
        box = ttk.Combobox(card, textvariable=self.modes[channel], values=('CC', 'CV', 'CR'), width=7, state='readonly')
        box.grid(row=0, column=1, sticky='w', padx=8)
        box.bind('<<ComboboxSelected>>', lambda _: self._mode_changed(channel))
        ttk.Label(card, text=tr('設定值')).grid(row=1, column=0, sticky='w', pady=10)
        entry = ttk.Entry(card, textvariable=self.values[channel], width=12)
        entry.grid(row=1, column=1, padx=8)
        ttk.Label(card, textvariable=self.units[channel]).grid(row=1, column=2, sticky='w')
        apply = ttk.Button(card, text=tr('套用模式 / 設定'), command=lambda: self.apply((channel,)))
        apply.grid(row=2, column=0, columnspan=2, sticky='w', pady=5)
        read = ttk.Button(card, text=tr('讀回設定'), command=lambda: self.read((channel,)))
        read.grid(row=2, column=2, sticky='w')
        on = ttk.Button(card, text='ON', command=lambda: self.set_enabled((channel,), True))
        on.grid(row=3, column=0, sticky='w', pady=10)
        off = ttk.Button(card, text='OFF', command=lambda: self.set_enabled((channel,), False))
        off.grid(row=3, column=1, sticky='w', padx=8)
        ttk.Label(card, textvariable=self.statuses[channel], wraplength=420, justify='left').grid(
            row=4, column=0, columnspan=3, sticky='w', pady=8)
        ttk.Label(card, textvariable=self.readings[channel], wraplength=420).grid(
            row=5, column=0, columnspan=3, sticky='w')
        self.channel_controls[channel] = {'mode': box, 'value': entry, 'apply': apply, 'read': read, 'on': on, 'off': off}
        self.modes[channel].trace_add('write', lambda *_: self._edited(channel))
        self.values[channel].trace_add('write', lambda *_: self._edited(channel))
        self.units[channel].set(self.MODE_INFO[mode][1])

    def _edited(self, channel):
        self.units[channel].set(self.MODE_INFO[self.modes[channel].get()][1])
        if not self._mirroring and self.sync.get() and channel == int(self.source.get()[-1]):
            self.copy(channel, 3 - channel)
        self._show_state(channel)

    def _mode_changed(self, channel):
        self.values[channel].set(self.MODE_INFO[self.modes[channel].get()][2])

    def copy(self, source, target):
        if self.busy or target in self.active_channels():
            return
        self._mirroring = True
        try:
            self.modes[target].set(self.modes[source].get())
            self.values[target].set(self.values[source].get())
        finally:
            self._mirroring = False
        if self.sync.get():
            self.source.set(f'CH{source}')

    def _sync_changed(self):
        if self.sync.get():
            source = int(self.source.get()[-1])
            self.copy(source, 3 - source)
        self._update_controls()

    def set_connected(self, connected):
        self.connected = connected
        self._epoch += 1
        self.actual.clear()
        self._next_refresh = 0.0
        self._update_controls()
        for channel in (1, 2):
            self.readings[channel].set(tr('尚無量測資料'))
            self._show_state(channel)

    def _show_state(self, channel):
        state = self.actual.get(channel)
        if state is None:
            self.statuses[channel].set(tr('尚未讀回設備狀態'))
            return
        actual_value = '—'
        if state.value is not None:
            mode = state.mode.strip().upper()[:2]
            unit = self.MODE_INFO[mode][0]
            actual_value = format_engineering(state.value, unit) if mode != 'CR' else f'{state.value:.6g} Ω'
        try:
            matches = state.value is not None and self.modes[channel].get() == state.mode.strip().upper()[:2] and abs(
                float(self.values[channel].get()) - state.value) < 1e-7
        except ValueError:
            matches = False
        draft = tr('已套用') if matches else tr('尚未套用')
        if channel in self.active_channels():
            draft = tr('測試中：設定已鎖定')
        self.statuses[channel].set(tr('設備：{0}，設定值 {1}，輸入 {2}\n編輯值：{3}',
                                       state.mode, actual_value, 'ON' if state.enabled else 'OFF', draft))
        if state.measurement:
            reading = state.measurement
            self.readings[channel].set(f'{format_engineering(reading.voltage, "V")}  '
                                      f'{format_engineering(reading.current, "A")}  '
                                      f'P (V×I): {format_power(reading.voltage * reading.current)}')

    def _update_controls(self):
        active = self.active_channels()
        synced_target = 3 - int(self.source.get()[-1]) if self.sync.get() else None
        for channel, controls in self.channel_controls.items():
            editable = not self.busy and channel not in active and channel != synced_target
            for key, widget in controls.items():
                allowed = self.connected and (editable if key in ('mode', 'value', 'apply') else
                          not self._off_pending if key == 'off' else not self.busy and channel not in active)
                widget.configure(state=('readonly' if key == 'mode' else 'normal') if allowed else 'disabled')
        common = self.connected and not self.busy and not active
        self.apply_both.configure(state='normal' if common else 'disabled')
        self.on_both.configure(state='normal' if common else 'disabled')
        self.off_both.configure(state='normal' if self.connected and not self._off_pending else 'disabled')
        self.read_both.configure(state='normal' if self.connected and not self.busy and not active else 'disabled')
        self.sync_button.configure(state='normal' if not self.busy and not active else 'disabled')
        self.source_box.configure(state='readonly' if not self.busy and not active else 'disabled')
        for button in self.copy_buttons:
            button.configure(state='normal' if not self.busy and not active else 'disabled')

    def _submit(self, operation, *, command=True, adopt=False, off=False):
        client = self.client_getter()
        if not self.connected or client is None:
            return
        epoch = self._epoch
        if command:
            self._generation += 1
            self._commands += 1
            self.result_var.set(tr('執行中…'))
        else:
            self._refreshing = True
        if off:
            self._off_pending = True
        generation = self._generation
        self._update_controls()

        def worker():
            try:
                result = operation(client)
            except Exception as exc:
                result = LoadResult(errors={1: str(exc), 2: str(exc)})
            self.events.put((epoch, generation, result, command, adopt, off))
        threading.Thread(target=worker, daemon=True).start()

    def apply(self, channels):
        if self.busy or set(channels) & self.active_channels():
            return
        if self.sync.get():
            channels = (1, 2)
            if self.active_channels():
                return
        try:
            settings = {c: (self.modes[c].get(), validate_setting(self.modes[c].get(), float(self.values[c].get()))) for c in channels}
        except ValueError as exc:
            messagebox.showerror(tr('電子負載設定'), str(exc), parent=self)
            return
        self._submit(lambda client: apply_settings(client, settings))

    def set_enabled(self, channels, enabled):
        if enabled:
            if self.busy or set(channels) & self.active_channels():
                return
            if not messagebox.askyesno(tr('確認啟用電子負載'), tr('即將啟用 {0}。請確認接線極性、外部電源及每通道 50 W 限制。',
                                                    ' / '.join(f'CH{c}' for c in channels)), parent=self):
                return
        else:
            if self._off_pending:
                return
            for channel in channels:
                self.stop_channel(channel)
        self._submit(lambda client: set_inputs(client, channels, enabled), off=not enabled)

    def read(self, channels, adopt=True):
        if self.busy:
            return
        self._submit(lambda client: self._read_result(client, channels), adopt=adopt)

    @staticmethod
    def _read_result(client, channels):
        result = LoadResult()
        for channel in channels:
            try:
                result.states[channel] = read_state(client, channel, measure=True)
                result.completed.add(channel)
            except Exception as exc:
                result.errors[channel] = str(exc)
        return result

    def _poll(self):
        while not self.events.empty():
            epoch, generation, result, command, adopt, off = self.events.get_nowait()
            if command:
                self._commands -= 1
            else:
                self._refreshing = False
            if off:
                self._off_pending = False
            if epoch != self._epoch or generation != self._generation:
                continue
            self.actual.update(result.states)
            # Explicit readback can update drafts; background refresh never does.
            if adopt:
                self._mirroring = True
                try:
                    for channel, state in result.states.items():
                        if state.value is not None and channel not in self.active_channels():
                            self.modes[channel].set(state.mode.strip().upper()[:2])
                            self.values[channel].set(f'{state.value:.6g}')
                finally:
                    self._mirroring = False
                if self.sync.get():
                    self.copy(int(self.source.get()[-1]), 3 - int(self.source.get()[-1]))
            for channel, state in result.states.items():
                self.on_state(channel, state)
            if command or result.errors:
                lines = []
                for channel in sorted(set(result.states) | set(result.completed) | set(result.errors) | set(result.rollback)):
                    detail = result.errors.get(channel, tr('操作完成') if channel in result.completed else tr('未執行'))
                    lines.append(f'CH{channel}: {detail}')
                    if channel in result.rollback:
                        lines.append(f'CH{channel}: {result.rollback[channel]}')
                self.result_var.set('\n'.join(lines))
            self._next_refresh = time.monotonic() + 2
        for channel in (1, 2):
            self._show_state(channel)
        self._update_controls()
        if self.connected and not self.busy and not self._refreshing and time.monotonic() >= self._next_refresh:
            self._submit(lambda client: self._read_result(client, (1, 2)), command=False)
        self.after(100, self._poll)

    def any_input_on(self):
        return any(state.enabled and state.value is not None for state in self.actual.values())

    def current_config(self):
        result = {'load_sync_settings': self.sync.get(), 'load_sync_source': self.source.get()}
        for channel in (1, 2):
            result[f'load_ch{channel}_mode'] = self.modes[channel].get()
            result[f'load_ch{channel}_value'] = self.values[channel].get()
        return result
