from __future__ import annotations

from gpp3323.i18n import tr, set_language

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from gpp3323 import GPP3323Client
from gui.channel_tab import ChannelTab
from gui.connection_tab import ConnectionTab
from gui.load_tab import LoadTab
from gui.cc_step_tab import CCStepTab
from gui.monitor_tab import MonitorTab
from gui.review_tab import ReviewTab
from gui.load_settings_tab import LoadSettingsTab


class MainWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.geometry("1180x780")
        self.minsize(980, 680)
        self.state("zoomed")
        self.client: GPP3323Client | None = None
        self.config_path = Path(__file__).resolve().parents[1] / "config.json"
        self.config_data = self._load_config()
        self.active_language = self.config_data.get('language', 'zh-TW')
        if self.active_language not in ('zh-TW', 'en'):
            self.active_language = 'zh-TW'
        set_language(self.active_language)
        self.title(tr('GW Instek GPP-3323 控制與監測'))

        language_bar = ttk.Frame(self, padding=(10, 4))
        language_bar.pack(fill='x')
        ttk.Label(language_bar, text='顯示語言 / Language').pack(side='left')
        self.language_var = tk.StringVar(value='English' if self.active_language == 'en' else '繁體中文')
        language_box = ttk.Combobox(language_bar, textvariable=self.language_var,
                                    values=('繁體中文', 'English'), state='readonly', width=14)
        language_box.pack(side='left', padx=8)
        language_box.bind('<<ComboboxSelected>>', self._choose_language)
        self.language_notice = tk.StringVar()
        ttk.Label(language_bar, textvariable=self.language_notice).pack(side='left')

        style = ttk.Style(self)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("Status.TLabel", padding=(8, 4))

        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=10, pady=(10, 4))

        self.connection_tab = ConnectionTab(
            notebook,
            self.config_data,
            self._connected,
            self._disconnected,
            self.save_config,
        )
        self.channel_tab = ChannelTab(notebook, self.get_client, self.run_io)
        self.monitor_tab = MonitorTab(notebook, self.get_client, self.config_data)
        self.load_settings_tab = LoadSettingsTab(notebook, self.get_client, self._active_load_channels,
                                                self._stop_load_channel, self.config_data, self._load_state_changed)
        self.load_tab = LoadTab(notebook, self.get_client, self.run_io, self.config_data,
                                open_settings=lambda: self.notebook.select(self.load_settings_tab),
                                hardware_busy=lambda: self.load_settings_tab.busy or self.cc_step_tab.running,
                                on_running=self._load_running)
        self.cc_step_tab = CCStepTab(notebook, self.get_client, self.run_io, self.config_data,
                                    self._step_running, lambda: self.load_tab._running or self.load_settings_tab.busy)
        self.review_tab = ReviewTab(notebook)
        self.notebook = notebook
        notebook.add(self.connection_tab, text=tr('  設備連線  '))
        notebook.add(self.channel_tab, text=tr('  Channel 設定  '))
        notebook.add(self.monitor_tab, text=tr('  連續量測  '))
        notebook.add(self.load_settings_tab, text=tr('電子負載設定'))
        notebook.add(self.load_tab, text="  Load Mode  ")
        notebook.add(self.cc_step_tab, text=tr('  CC 階梯測試  '))

        notebook.add(self.review_tab, text=tr('  曲線 Review  '))

        self.status_var = tk.StringVar(value=tr('未連線'))
        ttk.Separator(self).pack(fill="x")
        ttk.Label(
            self, textvariable=self.status_var, style="Status.TLabel", anchor="w"
        ).pack(fill="x")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _choose_language(self, _event=None) -> None:
        language = 'en' if self.language_var.get() == 'English' else 'zh-TW'
        self.save_config({'language': language})
        self.language_notice.set(
            '重新啟動後套用 / Applies after restart' if language != self.active_language else '')

    def _load_config(self) -> dict[str, Any]:
        defaults = {
            "language": "zh-TW",
            "host": "10.0.0.123",
            "port": 1026,
            "timeout": 3.0,
            "sample_interval": 1.0,
            "max_points": 3600,
            "load_sample_interval": 1.0,
            "load_stop_mode": "duration",
            "load_test_duration_minutes": 60.0,
            "load_cutoff_voltage": 3.0,
            "load_notes": "",
        }
        try:
            loaded = json.loads(self.config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                defaults.update(loaded)
        except (OSError, ValueError):
            pass
        return defaults

    def save_config(self, updates: dict[str, Any]) -> None:
        self.config_data.update(updates)
        try:
            self.config_path.write_text(
                json.dumps(self.config_data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            self.status_var.set(tr('設定檔儲存失敗：{0}', f'{exc}'))

    def get_client(self) -> GPP3323Client | None:
        return self.client if self.client and self.client.connected else None

    def _step_running(self, running: bool) -> None:
        for tab in (self.channel_tab, self.load_tab):
            self.notebook.tab(tab, state="disabled" if running else "normal")
        self.load_settings_tab._update_controls()

    def _load_running(self, running: bool) -> None:
        self.notebook.tab(self.channel_tab, state='disabled' if running else 'normal')
        self.load_settings_tab._update_controls()

    def _active_load_channels(self):
        active = set()
        load = getattr(self, 'load_tab', None)
        step = getattr(self, 'cc_step_tab', None)
        if load is not None and load._running:
            active.add(load.active_channel)
        if step is not None and step.running:
            active.add(step.run_channel)
        return active

    def _stop_load_channel(self, channel):
        self.load_tab.stop_channel(channel)
        if self.cc_step_tab.running and self.cc_step_tab.run_channel == channel:
            self.cc_step_tab.stop()

    def _load_state_changed(self, channel, state):
        load = self.load_tab
        self.channel_tab._set_output_display(channel, state.enabled)
        is_load = GPP3323Client.is_load_mode(state.mode)
        load.output_states[channel] = state.enabled and is_load
        if channel == load.channel_var.get():
            load.mode_var.set(state.mode.strip().upper()[:2])
            load._update_status(state.mode, state.enabled and is_load)

    def _connected(self, client: GPP3323Client) -> None:
        if self.client and self.client is not client:
            self.client.disconnect()
        self.client = client
        self.channel_tab.set_connected(True)
        self.monitor_tab.set_connected(True)
        self.load_tab.set_connected(True)
        self.cc_step_tab.set_connected(True)
        self.status_var.set(tr('已連線：{0}', f'{client.identity}'))
        self.load_settings_tab.set_connected(True)
        self.load_settings_tab._submit(self._initialize_load_state)
        self.channel_tab.refresh_all()

    @staticmethod
    def _initialize_load_state(client):
        client.ensure_load_inputs_off()
        return LoadSettingsTab._read_result(client, (1, 2))

    def _disconnected(self) -> None:
        self.cc_step_tab.shutdown()
        self.monitor_tab.stop()
        self.load_tab.stop()
        if self.client:
            self.client.disconnect()
        self.client = None
        self.channel_tab.set_connected(False)
        self.monitor_tab.set_connected(False)
        self.load_tab.set_connected(False)
        self.load_settings_tab.set_connected(False)
        self.cc_step_tab.set_connected(False)
        self.status_var.set(tr('未連線'))

    def run_io(
        self,
        operation: Callable[[], Any],
        success: Callable[[Any], None] | None = None,
        failure: Callable[[Exception], None] | None = None,
        message: str | None = None,
    ) -> None:
        self.status_var.set(message if message is not None else tr('執行中…'))

        def worker() -> None:
            try:
                result = operation()
            except Exception as exc:
                self.after(0, lambda exc=exc: self._io_failed(exc, failure))
            else:
                self.after(0, lambda: self._io_succeeded(result, success))

        threading.Thread(target=worker, daemon=True).start()

    def _io_succeeded(
        self, result: Any, callback: Callable[[Any], None] | None
    ) -> None:
        self.status_var.set(tr('操作完成'))
        if callback:
            callback(result)

    def _io_failed(
        self, exc: Exception, callback: Callable[[Exception], None] | None
    ) -> None:
        self.status_var.set(tr('操作失敗：{0}', f'{exc}'))
        if callback:
            callback(exc)
        else:
            messagebox.showerror("GPP-3323", str(exc), parent=self)

    def _on_close(self) -> None:
        self.cc_step_tab.shutdown()
        self.monitor_tab.stop()
        self.load_tab.stop()
        output_on = (self.channel_tab.any_output_on() or self.load_tab.any_input_on()
                     or self.load_settings_tab.any_input_on())
        if self.get_client() and output_on:
            choice = messagebox.askyesnocancel(
                tr('輸出或負載仍開啟'),
                tr('偵測到至少一個 Channel 輸出或 Load Input 仍為 ON。\n\n選擇「是」：關閉全部輸出後離開\n選擇「否」：保持輸出並離開\n選擇「取消」：返回程式'),
                parent=self,
            )
            if choice is None:
                return
            if choice:
                try:
                    self.client.all_outputs_off()  # type: ignore[union-attr]
                except Exception as exc:
                    if not messagebox.askyesno(
                        tr('關閉輸出失敗'),
                        tr('無法確認輸出已關閉：{0}\n仍要離開嗎？', f'{exc}'),
                        parent=self,
                    ):
                        return
        self.save_config({
            **self.monitor_tab.current_config(),
            **self.load_tab.current_config(),
            **self.load_settings_tab.current_config(),
            **self.cc_step_tab.current_config(),
        })
        if self.client:
            self.client.disconnect()
        self.destroy()
