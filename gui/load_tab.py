from __future__ import annotations

import csv
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any, Callable

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from matplotlib.ticker import MaxNLocator

from gpp3323 import GPP3323Client, LoadVoltagePresentError, Measurement
from gui.monitor_tab import Sample, engineering_scale, format_engineering


class LoadTab(ttk.Frame):
    MODE_INFO = {
        "CC": ("負載電流", "A", "0 – 3.2 A", "0.1000"),
        "CV": ("負載電壓", "V", "1.5 – 33 V", "5.000"),
        "CR": ("負載電阻", "Ω", "1 – 1000 Ω", "100"),
    }

    def __init__(self, parent: tk.Misc, client_getter: Callable[[], GPP3323Client | None],
                 run_io: Callable[..., None], config: dict[str, Any]) -> None:
        super().__init__(parent, padding=10)
        self.client_getter, self.run_io = client_getter, run_io
        self.channel_var, self.mode_var = tk.IntVar(value=1), tk.StringVar(value="CC")
        self.value_var = tk.StringVar(value="0.1000")
        self.value_label_var, self.range_var = tk.StringVar(), tk.StringVar()
        self.status_var = tk.StringVar(value="模式：—　LOAD INPUT：—")
        self.latest_var = tk.StringVar(value="尚無量測資料")
        self.interval_var = tk.StringVar(value=str(config.get("load_sample_interval", 1.0)))
        self.max_points_var = tk.StringVar(value=str(config.get("load_max_points", 3600)))
        self.output_states = {1: False, 2: False}
        self.samples: list[Sample] = []
        self._connected = self._running = False
        self._stop_event = threading.Event()
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.controls: list[tk.Widget] = []
        self._build_controls()
        self._build_plot()
        self._mode_changed(False)
        self.after(100, self._poll_queue)
        self.set_connected(False)

    def _build_controls(self) -> None:
        settings = ttk.LabelFrame(self, text="電子負載設定", padding=10)
        settings.pack(fill="x", pady=(0, 8))
        ttk.Label(settings, text="僅限 CH1 / CH2。設定不會自動啟用負載；啟用前請確認極性、外部電源及單 Channel 50 W 限制。",
                  foreground="#9a4b00").grid(row=0, column=0, columnspan=11, sticky="w", pady=(0, 8))
        ttk.Label(settings, text="Channel").grid(row=1, column=0, padx=(0, 4))
        for column, channel in enumerate((1, 2), start=1):
            button = ttk.Radiobutton(settings, text=f"CH{channel}", value=channel,
                                     variable=self.channel_var, command=self.read_settings)
            button.grid(row=1, column=column, padx=3)
            self.controls.append(button)
        ttk.Label(settings, text="模式").grid(row=1, column=3, padx=(16, 4))
        mode = ttk.Combobox(settings, textvariable=self.mode_var, values=("CC", "CV", "CR"),
                            state="readonly", width=6)
        mode.grid(row=1, column=4)
        mode.bind("<<ComboboxSelected>>", lambda _event: self._mode_changed())
        self.controls.append(mode)
        ttk.Label(settings, textvariable=self.value_label_var).grid(row=1, column=5, padx=(16, 4))
        value = ttk.Entry(settings, textvariable=self.value_var, width=11)
        value.grid(row=1, column=6)
        ttk.Label(settings, textvariable=self.range_var, foreground="#666").grid(row=1, column=7, padx=5)
        apply_button = ttk.Button(settings, text="套用模式 / 設定", command=self.apply_settings)
        apply_button.grid(row=1, column=8, padx=(14, 4))
        read_button = ttk.Button(settings, text="讀回", command=self.read_settings)
        read_button.grid(row=1, column=9, padx=4)
        self.input_button = ttk.Button(settings, text="LOAD INPUT ON", command=self.toggle_input)
        self.input_button.grid(row=1, column=10, padx=(12, 0))
        self.controls.extend([value, apply_button, read_button, self.input_button])
        ttk.Label(settings, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).grid(
            row=2, column=0, columnspan=11, sticky="w", pady=(10, 0))

        monitor = ttk.Frame(self)
        monitor.pack(fill="x", pady=(0, 5))
        ttk.Label(monitor, text="取樣週期 (s)").pack(side="left")
        interval = ttk.Entry(monitor, textvariable=self.interval_var, width=7)
        interval.pack(side="left", padx=(4, 14))
        ttk.Label(monitor, text="保留點數").pack(side="left")
        max_points = ttk.Entry(monitor, textvariable=self.max_points_var, width=8)
        max_points.pack(side="left", padx=4)
        self.start_button = ttk.Button(monitor, text="開始曲線", command=self.start)
        self.start_button.pack(side="left", padx=(14, 4))
        self.stop_button = ttk.Button(monitor, text="停止", command=self.stop)
        self.stop_button.pack(side="left", padx=4)
        self.clear_button = ttk.Button(monitor, text="清除", command=self.clear)
        self.clear_button.pack(side="left", padx=4)
        self.export_button = ttk.Button(monitor, text="匯出 CSV", command=self.export_csv)
        self.export_button.pack(side="right")
        self.controls.extend([interval, max_points, self.start_button, self.stop_button])
        ttk.Label(self, textvariable=self.latest_var, anchor="w").pack(fill="x", pady=(0, 4))

    def _build_plot(self) -> None:
        self.figure = Figure(figsize=(9, 5.5), dpi=100, constrained_layout=True)
        self.voltage_axis = self.figure.add_subplot(311)
        self.current_axis = self.figure.add_subplot(312, sharex=self.voltage_axis)
        self.power_axis = self.figure.add_subplot(313, sharex=self.voltage_axis)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self._redraw()

    def _client(self) -> GPP3323Client:
        client = self.client_getter()
        if not client:
            raise RuntimeError("設備尚未連線")
        return client

    def _mode_changed(self, reset_value: bool = True) -> None:
        label, unit, range_text, default = self.MODE_INFO[self.mode_var.get()]
        self.value_label_var.set(f"{label} ({unit})")
        self.range_var.set(range_text)
        if reset_value:
            self.value_var.set(default)

    def apply_settings(self) -> None:
        try:
            value = float(self.value_var.get())
        except ValueError:
            messagebox.showerror("Load Mode", "設定值格式不正確", parent=self)
            return
        channel, mode = self.channel_var.get(), self.mode_var.get()

        def operation() -> tuple[str, str, bool]:
            client = self._client()
            error = client.configure_load(channel, mode, value)
            return error, client.get_channel_mode(channel), client.get_output(channel)

        def success(result: tuple[str, str, bool]) -> None:
            error, actual_mode, enabled = result
            self._update_status(actual_mode, enabled)
            messagebox.showinfo("Load Mode", f"CH{channel} {mode} 設定已套用\n設備狀態：{error}", parent=self)

        def failure(exc: Exception) -> None:
            if isinstance(exc, LoadVoltagePresentError):
                messagebox.showwarning("無法切換 Load Mode", str(exc), parent=self)
            else:
                messagebox.showerror("Load Mode", str(exc), parent=self)

        self.run_io(operation, success, failure, message=f"正在檢查 CH{channel} 端子電壓…")

    def read_settings(self) -> None:
        if not self._connected:
            return
        channel, selected_mode = self.channel_var.get(), self.mode_var.get()

        def operation() -> tuple[str, str, float, bool]:
            client = self._client()
            actual = client.get_channel_mode(channel)
            mode = next((item for item in ("CV", "CC", "CR") if item in actual.upper()), selected_mode)
            return actual, mode, client.get_load_setting(channel, mode), client.get_output(channel)

        def success(result: tuple[str, str, float, bool]) -> None:
            actual, mode, value, enabled = result
            self.mode_var.set(mode)
            self._mode_changed(False)
            self.value_var.set(f"{value:.4f}" if mode == "CC" else f"{value:.3f}")
            self._update_status(actual, enabled)

        self.run_io(operation, success, message=f"正在讀取 CH{channel} Load 設定…")

    def toggle_input(self) -> None:
        channel = self.channel_var.get()
        target = not self.output_states[channel]
        if target and not messagebox.askyesno(
            "確認啟用電子負載",
            f"即將啟用 CH{channel} 電子負載輸入。\n模式：{self.mode_var.get()}　設定：{self.value_var.get()}\n\n"
            "請確認接線極性、外部電源限制，且消耗功率不超過 50 W。", parent=self):
            return

        def operation() -> tuple[str, bool]:
            client = self._client()
            client.set_output(channel, target)
            return client.get_channel_mode(channel), client.get_output(channel)

        self.run_io(operation, lambda result: self._update_status(*result),
                    message=f"正在{'啟用' if target else '停用'} CH{channel} 電子負載…")

    def _update_status(self, mode: str, enabled: bool) -> None:
        channel = self.channel_var.get()
        self.output_states[channel] = enabled
        self.status_var.set(f"CH{channel} 模式：{mode}　LOAD INPUT：{'ON' if enabled else 'OFF'}")
        self.input_button.configure(text="LOAD INPUT OFF" if enabled else "LOAD INPUT ON")

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            self.stop()
            self.output_states = {1: False, 2: False}
            self.status_var.set("模式：—　LOAD INPUT：—")
        for widget in self.controls:
            try:
                widget.configure(state="readonly" if isinstance(widget, ttk.Combobox) and connected
                                 else "normal" if connected else "disabled")
            except tk.TclError:
                pass
        self.clear_button.configure(state="normal")
        self.export_button.configure(state="normal" if self.samples else "disabled")
        self._update_button_states()

    def start(self) -> None:
        if self._running:
            return
        client = self.client_getter()
        if not client:
            messagebox.showerror("Load 曲線", "設備尚未連線", parent=self)
            return
        try:
            interval, max_points = float(self.interval_var.get()), int(self.max_points_var.get())
            if not 0.2 <= interval <= 3600:
                raise ValueError("取樣週期必須介於 0.2 至 3600 秒")
            if not 10 <= max_points <= 100000:
                raise ValueError("保留點數必須介於 10 至 100000")
        except ValueError as exc:
            messagebox.showerror("Load 曲線", str(exc), parent=self)
            return
        channel = self.channel_var.get()
        self._running = True
        self._stop_event.clear()
        start_time = time.monotonic()

        def worker() -> None:
            next_time = time.monotonic()
            while not self._stop_event.is_set():
                try:
                    reading: Measurement = client.measure(channel)
                except Exception as exc:
                    self._queue.put(("error", exc))
                    self._stop_event.set()
                    break
                sample = Sample(datetime.now(), time.monotonic() - start_time, channel,
                                reading.voltage, reading.current, reading.power)
                self._queue.put(("sample", (sample, max_points)))
                next_time += interval
                self._stop_event.wait(max(0.0, next_time - time.monotonic()))
            self._queue.put(("stopped", None))

        threading.Thread(target=worker, daemon=True).start()
        self._update_button_states()

    def stop(self) -> None:
        self._stop_event.set()

    def clear(self) -> None:
        self.samples.clear()
        self.latest_var.set("尚無量測資料")
        self.export_button.configure(state="disabled")
        self._redraw()

    def _poll_queue(self) -> None:
        changed = False
        try:
            while True:
                event, payload = self._queue.get_nowait()
                if event == "sample":
                    sample, max_points = payload  # type: ignore[misc]
                    self.samples.append(sample)
                    if len(self.samples) > max_points:
                        del self.samples[:len(self.samples) - max_points]
                    self.latest_var.set(f"{sample.timestamp:%Y-%m-%d %H:%M:%S}  CH{sample.channel}　"
                                        f"{format_engineering(sample.voltage, 'V')}　"
                                        f"{format_engineering(sample.current, 'A')}　{sample.power:.5g} W")
                    changed = True
                elif event == "error":
                    messagebox.showerror("Load 曲線中斷", str(payload), parent=self)
                elif event == "stopped":
                    self._running = False
                    self._update_button_states()
        except queue.Empty:
            pass
        if changed:
            self.export_button.configure(state="normal")
            self._redraw()
        self.after(100, self._poll_queue)

    def _update_button_states(self) -> None:
        self.start_button.configure(state="disabled" if self._running or not self._connected else "normal")
        self.stop_button.configure(state="normal" if self._running else "disabled")

    def _redraw(self) -> None:
        axes = (self.voltage_axis, self.current_axis, self.power_axis)
        for axis in axes:
            axis.clear()
        x = [sample.elapsed for sample in self.samples]
        vf, vu = engineering_scale([sample.voltage for sample in self.samples], "V")
        cf, cu = engineering_scale([sample.current for sample in self.samples], "A")
        if self.samples:
            self.voltage_axis.plot(x, [sample.voltage * vf for sample in self.samples], color="#0b4f9c")
            self.current_axis.plot(x, [sample.current * cf for sample in self.samples], color="#d87300")
            self.power_axis.plot(x, [sample.power for sample in self.samples], color="#198754")
        self.voltage_axis.set_ylabel(f"Voltage ({vu})")
        self.current_axis.set_ylabel(f"Current ({cu})")
        self.power_axis.set_ylabel("Power (W)")
        self.power_axis.set_xlabel("Elapsed time (s)")
        for axis in axes:
            axis.grid(True, alpha=0.25)
            axis.ticklabel_format(axis="y", style="plain", useOffset=False)
            axis.yaxis.set_major_locator(MaxNLocator(nbins=5))
            axis.margins(y=0.08)
        self.canvas.draw_idle()

    def export_csv(self) -> None:
        if not self.samples:
            messagebox.showinfo("匯出 CSV", "目前沒有資料", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self, title="匯出 Load 量測資料",
            initialdir=str(Path(__file__).resolve().parents[1] / "data"),
            initialfile=f"gpp3323_load_{datetime.now():%Y%m%d_%H%M%S}.csv",
            defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["timestamp", "elapsed_s", "channel", "voltage_V", "current_A", "power_W"])
                for sample in self.samples:
                    writer.writerow([sample.timestamp.isoformat(timespec="milliseconds"), f"{sample.elapsed:.3f}",
                                     sample.channel, f"{sample.voltage:.6f}", f"{sample.current:.6f}",
                                     f"{sample.power:.6f}"])
        except OSError as exc:
            messagebox.showerror("匯出 CSV", str(exc), parent=self)
        else:
            messagebox.showinfo("匯出 CSV", f"已儲存：\n{path}", parent=self)

    def current_config(self) -> dict[str, float | int]:
        try:
            interval = float(self.interval_var.get())
        except ValueError:
            interval = 1.0
        try:
            max_points = int(self.max_points_var.get())
        except ValueError:
            max_points = 3600
        return {"load_sample_interval": interval, "load_max_points": max_points}

    def any_input_on(self) -> bool:
        return any(self.output_states.values())
