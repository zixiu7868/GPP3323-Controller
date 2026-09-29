from __future__ import annotations

import csv
import ctypes
import math
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
from PIL import ImageGrab

from gpp3323 import GPP3323Client, LoadVoltagePresentError, Measurement
from gui.monitor_tab import Sample, engineering_scale, format_engineering


ESTIMATION_WINDOW_SECONDS = 30.0
ESTIMATION_TARGET_VOLTAGE = 2.0


def estimate_time_to_voltage(
    samples: list[tuple[float, float]],
    target_voltage: float = ESTIMATION_TARGET_VOLTAGE,
    minimum_elapsed: float = ESTIMATION_WINDOW_SECONDS,
) -> float | None:
    """Estimate total elapsed seconds to a target voltage using linear regression."""
    valid = [
        (elapsed, voltage)
        for elapsed, voltage in samples
        if math.isfinite(elapsed) and math.isfinite(voltage) and elapsed >= 0.0
    ]
    if len(valid) < 2 or valid[-1][0] < minimum_elapsed:
        return None

    mean_elapsed = sum(item[0] for item in valid) / len(valid)
    mean_voltage = sum(item[1] for item in valid) / len(valid)
    elapsed_variance = sum((item[0] - mean_elapsed) ** 2 for item in valid)
    if elapsed_variance <= 0.0:
        return None
    slope = sum(
        (elapsed - mean_elapsed) * (voltage - mean_voltage)
        for elapsed, voltage in valid
    ) / elapsed_variance
    if slope >= -1e-9:
        return None

    intercept = mean_voltage - slope * mean_elapsed
    estimated_elapsed = (target_voltage - intercept) / slope
    if not math.isfinite(estimated_elapsed) or estimated_elapsed < 0.0:
        return None
    if estimated_elapsed < valid[-1][0] and valid[-1][1] > target_voltage:
        return None
    return estimated_elapsed


def format_estimated_time(seconds: float) -> str:
    """Format an estimated elapsed time for the load-test status line."""
    if seconds < 60.0:
        return f"{seconds:.0f} 秒"
    if seconds < 3600.0:
        return f"{seconds / 60.0:.1f} 分鐘"
    if seconds < 86400.0:
        return f"{seconds / 3600.0:.2f} 小時"
    return f"{seconds / 86400.0:.2f} 天"


def average_measured_current_amps(measured_currents: list[float]) -> float | None:
    """Return the average magnitude of finite, non-zero measured currents."""
    valid_currents = [
        abs(current)
        for current in measured_currents
        if math.isfinite(current) and abs(current) > 1e-9
    ]
    if not valid_currents:
        return None
    return sum(valid_currents) / len(valid_currents)


def estimate_capacity_mah(duration_seconds: float, measured_currents: list[float]) -> float | None:
    """Estimate CC capacity for a duration using the average measured current."""
    average_current = average_measured_current_amps(measured_currents)
    if average_current is None:
        return None
    return average_current * max(0.0, duration_seconds) / 3600.0 * 1000.0


def estimate_remaining_capacity_mah(
    estimated_total_seconds: float,
    elapsed_seconds: float,
    measured_currents: list[float],
) -> float | None:
    """Estimate CC capacity remaining until the predicted cutoff time."""
    remaining_seconds = max(0.0, estimated_total_seconds - elapsed_seconds)
    return estimate_capacity_mah(remaining_seconds, measured_currents)


def evaluate_stop_condition(
    mode: str,
    target: float,
    elapsed: float,
    voltage: float,
    cutoff_armed: bool,
) -> tuple[bool, bool]:
    """Return (should_stop, cutoff_armed) for a load test sample."""
    if mode == "duration":
        return elapsed >= target, cutoff_armed
    cutoff_armed = cutoff_armed or voltage > target
    return cutoff_armed and voltage <= target, cutoff_armed


def next_screenshot_path(directory: Path, timestamp: datetime) -> Path:
    """Return the next timestamped screenshot path without overwriting a file."""
    directory.mkdir(parents=True, exist_ok=True)
    prefix = f"gpp3323_screen_{timestamp:%Y%m%d_%H%M%S}"
    sequence = 1
    while True:
        path = directory / f"{prefix}_{sequence:03d}.png"
        if not path.exists():
            return path
        sequence += 1


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
        self.elapsed_var = tk.StringVar(value="目前測試時間：0.00 min")
        self.estimate_var = tk.StringVar(value="每 30 秒估算至 2.0 V：尚未計算")
        self.capacity_var = tk.StringVar(value="CC 至 2.0 V 容量：尚未計算")
        self.interval_var = tk.StringVar(value=str(config.get("load_sample_interval", 1.0)))
        saved_stop_mode = config.get("load_stop_mode", "duration")
        self.stop_mode_var = tk.StringVar(
            value="測試時間" if saved_stop_mode == "duration" else "截止電壓"
        )
        self.stop_value_var = tk.StringVar(
            value=str(
                config.get(
                    "load_test_duration_minutes" if saved_stop_mode == "duration" else "load_cutoff_voltage",
                    60 if saved_stop_mode == "duration" else 3.0,
                )
            )
        )
        self.stop_unit_var = tk.StringVar()
        self.output_states = {1: False, 2: False}
        self.samples: list[Sample] = []
        self._connected = self._running = False
        self._stop_event = threading.Event()
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.controls: list[tk.Widget] = []
        self.initial_notes = str(config.get("load_notes", ""))
        self._build_controls()
        self._build_plot()
        self._mode_changed(False)
        self._stop_mode_changed(False)
        self.after(100, self._poll_queue)
        self.set_connected(False)

    def _build_controls(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 8))
        settings = ttk.LabelFrame(top, text="電子負載設定", padding=10)
        settings.pack(side="left", fill="both", expand=True)
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
        apply_button.grid(row=2, column=5, padx=(14, 4), pady=(10, 0))
        read_button = ttk.Button(settings, text="讀回", command=self.read_settings)
        read_button.grid(row=2, column=6, padx=4, pady=(10, 0))
        self.input_button = ttk.Button(settings, text="LOAD INPUT ON", command=self.toggle_input)
        self.input_button.grid(row=2, column=7, padx=(12, 0), pady=(10, 0))
        self.controls.extend([value, apply_button, read_button, self.input_button])
        ttk.Label(settings, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).grid(
            row=2, column=0, columnspan=5, sticky="w", pady=(10, 0))

        notes = ttk.LabelFrame(top, text="備註", padding=6)
        notes.pack(side="right", fill="both", padx=(8, 0))
        self.notes_text = tk.Text(notes, width=32, height=5, wrap="word", undo=True)
        self.notes_text.pack(fill="both", expand=True)
        self.notes_text.insert("1.0", self.initial_notes)

        monitor = ttk.Frame(self)
        monitor.pack(fill="x", pady=(0, 5))
        ttk.Label(monitor, text="取樣週期 (s)").pack(side="left")
        interval = ttk.Entry(monitor, textvariable=self.interval_var, width=7)
        interval.pack(side="left", padx=(4, 14))
        ttk.Label(monitor, text="停止條件").pack(side="left")
        stop_mode = ttk.Combobox(
            monitor, textvariable=self.stop_mode_var,
            values=("測試時間", "截止電壓"), state="readonly", width=9,
        )
        stop_mode.pack(side="left", padx=4)
        stop_mode.bind("<<ComboboxSelected>>", lambda _event: self._stop_mode_changed())
        stop_value = ttk.Entry(monitor, textvariable=self.stop_value_var, width=9)
        stop_value.pack(side="left", padx=(4, 2))
        ttk.Label(monitor, textvariable=self.stop_unit_var).pack(side="left")
        self.start_button = ttk.Button(monitor, text="開始曲線", command=self.start)
        self.start_button.pack(side="left", padx=(14, 4))
        self.stop_button = ttk.Button(monitor, text="停止", command=self.stop)
        self.stop_button.pack(side="left", padx=4)
        self.clear_button = ttk.Button(monitor, text="清除", command=self.clear)
        self.clear_button.pack(side="left", padx=4)
        self.export_button = ttk.Button(monitor, text="匯出 CSV", command=self.export_csv)
        self.export_button.pack(side="right")
        self.screenshot_button = ttk.Button(monitor, text="程式截圖", command=self.save_screenshot)
        self.screenshot_button.pack(side="right", padx=4)
        self.controls.extend([interval, stop_mode, stop_value, self.start_button, self.stop_button])
        status_line = ttk.Frame(self)
        status_line.pack(fill="x", pady=(0, 4))
        ttk.Label(status_line, textvariable=self.latest_var, anchor="w").pack(side="left", fill="x", expand=True)
        ttk.Label(status_line, textvariable=self.elapsed_var, anchor="e", font=("Segoe UI", 10, "bold")).pack(side="right")
        ttk.Label(self, textvariable=self.estimate_var, anchor="w", foreground="#0b4f9c",
                  font=("Segoe UI", 10, "bold")).pack(fill="x", pady=(0, 4))
        ttk.Label(self, textvariable=self.capacity_var, anchor="w", foreground="#198754",
                  font=("Segoe UI", 10, "bold")).pack(fill="x", pady=(0, 4))

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

    def _stop_mode_changed(self, reset_value: bool = True) -> None:
        if self.stop_mode_var.get() == "測試時間":
            self.stop_unit_var.set("分鐘")
            if reset_value:
                self.stop_value_var.set("60")
        else:
            self.stop_unit_var.set("V（電壓下降至此值時停止）")
            if reset_value:
                self.stop_value_var.set("3.0")

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

    def initialize_safe_state(self) -> None:
        """Ensure previously active load inputs are off after connecting."""
        def success(result: dict[int, tuple[str, bool]]) -> None:
            for channel, (_mode, enabled) in result.items():
                self.output_states[channel] = enabled
            channel = self.channel_var.get()
            mode, enabled = result[channel]
            self._update_status(mode, enabled)

        self.run_io(
            lambda: self._client().ensure_load_inputs_off(),
            success,
            message="正在確認 Load Input 已關閉…",
        )

    def start(self) -> None:
        if self._running:
            return
        client = self.client_getter()
        if not client:
            messagebox.showerror("Load 曲線", "設備尚未連線", parent=self)
            return
        try:
            interval = float(self.interval_var.get())
            stop_target = float(self.stop_value_var.get())
            if not 0.2 <= interval <= 3600:
                raise ValueError("取樣週期必須介於 0.2 至 3600 秒")
            stop_mode = "duration" if self.stop_mode_var.get() == "測試時間" else "cutoff"
            if stop_mode == "duration" and not 0.1 <= stop_target <= 10080:
                raise ValueError("測試時間必須介於 0.1 至 10080 分鐘（7 天）")
            if stop_mode == "cutoff" and not 0.0 <= stop_target <= 33.0:
                raise ValueError("截止電壓必須介於 0 至 33 V")
        except ValueError as exc:
            messagebox.showerror("Load 曲線", str(exc), parent=self)
            return
        stop_target_seconds = stop_target * 60.0 if stop_mode == "duration" else stop_target
        channel = self.channel_var.get()
        self._running = True
        self._stop_event.clear()
        self.elapsed_var.set("目前測試時間：0.00 min")
        self.estimate_var.set("每 30 秒估算至 2.0 V：計算中…")
        self.capacity_var.set("CC 至 2.0 V 容量：計算中…")
        start_time = time.monotonic()

        def worker() -> None:
            next_time = time.monotonic()
            cutoff_armed = False
            load_enabled = False
            active_mode = ""
            estimation_samples: list[tuple[float, float]] = []
            estimation_currents: list[float] = []
            next_estimation_elapsed = ESTIMATION_WINDOW_SECONDS
            try:
                mode, enabled = client.enable_load_input(channel)
            except Exception as exc:
                self._queue.put(("error", exc))
                self._stop_event.set()
            else:
                load_enabled = enabled
                active_mode = mode
                self._queue.put(("input_on", (channel, mode, enabled)))
            while not self._stop_event.is_set():
                try:
                    reading: Measurement = client.measure(channel)
                except Exception as exc:
                    self._queue.put(("error", exc))
                    self._stop_event.set()
                    break
                elapsed = time.monotonic() - start_time
                sample = Sample(datetime.now(), elapsed, channel,
                                reading.voltage, reading.current, reading.power)
                self._queue.put(("sample", sample))
                estimation_samples.append((elapsed, reading.voltage))
                estimation_currents.append(reading.current)
                if elapsed >= next_estimation_elapsed:
                    estimate = estimate_time_to_voltage(estimation_samples)
                    total_capacity = remaining_capacity = average_current = None
                    if estimate is not None and active_mode.strip().upper().startswith("CC"):
                        total_capacity = estimate_capacity_mah(
                            estimate, estimation_currents
                        )
                        remaining_capacity = estimate_remaining_capacity_mah(
                            estimate, elapsed, estimation_currents
                        )
                        average_current = average_measured_current_amps(estimation_currents)
                    self._queue.put(
                        (
                            "estimate",
                            (
                                elapsed,
                                estimate,
                                total_capacity,
                                remaining_capacity,
                                average_current,
                                active_mode,
                            ),
                        )
                    )
                    while next_estimation_elapsed <= elapsed:
                        next_estimation_elapsed += ESTIMATION_WINDOW_SECONDS
                should_stop, cutoff_armed = evaluate_stop_condition(
                    stop_mode, stop_target_seconds, elapsed, reading.voltage, cutoff_armed
                )
                if should_stop:
                    try:
                        client.set_output(channel, False)
                    except Exception as exc:
                        self._queue.put(("error", exc))
                    else:
                        reason = (
                            f"已達測試時間 {stop_target:g} 分鐘"
                            if stop_mode == "duration"
                            else f"電壓已降至截止值 {stop_target:g} V"
                        )
                        self._queue.put(("condition", (channel, reason)))
                    self._stop_event.set()
                    break
                next_time += interval
                self._stop_event.wait(max(0.0, next_time - time.monotonic()))
            if load_enabled:
                try:
                    client.set_output(channel, False)
                except Exception as exc:
                    self._queue.put(("error", exc))
                else:
                    self._queue.put(("input_off", (channel, active_mode)))
            self._queue.put(("stopped", None))

        threading.Thread(target=worker, daemon=True).start()
        self._update_button_states()

    def stop(self) -> None:
        self._stop_event.set()

    def clear(self) -> None:
        self.samples.clear()
        self.latest_var.set("尚無量測資料")
        self.elapsed_var.set("目前測試時間：0.00 min")
        self.estimate_var.set("每 30 秒估算至 2.0 V：尚未計算")
        self.capacity_var.set("CC 至 2.0 V 容量：尚未計算")
        self.export_button.configure(state="disabled")
        self._redraw()

    def _poll_queue(self) -> None:
        changed = False
        try:
            while True:
                event, payload = self._queue.get_nowait()
                if event == "sample":
                    sample = payload  # type: ignore[assignment]
                    self.samples.append(sample)
                    if len(self.samples) > 100000:
                        del self.samples[:len(self.samples) - 100000]
                    self.elapsed_var.set(f"目前測試時間：{sample.elapsed / 60.0:.2f} min")
                    self.latest_var.set(f"{sample.timestamp:%Y-%m-%d %H:%M:%S}  CH{sample.channel}　"
                                        f"{format_engineering(sample.voltage, 'V')}　"
                                        f"{format_engineering(sample.current, 'A')}　{sample.power:.5g} W")
                    changed = True
                elif event == "error":
                    messagebox.showerror("Load 曲線中斷", str(payload), parent=self)
                elif event == "input_on":
                    channel, mode, enabled = payload  # type: ignore[misc]
                    self.output_states[channel] = enabled
                    if channel == self.channel_var.get():
                        self._update_status(mode, enabled)
                elif event == "input_off":
                    channel, mode = payload  # type: ignore[misc]
                    self.output_states[channel] = False
                    if channel == self.channel_var.get():
                        self._update_status(mode, False)
                elif event == "condition":
                    channel, reason = payload  # type: ignore[misc]
                    self.output_states[channel] = False
                    if channel == self.channel_var.get():
                        self._update_status(self.mode_var.get() + " LOAD", False)
                    messagebox.showinfo(
                        "Load 測試完成", f"{reason}\nCH{channel} Load Input 已關閉。", parent=self
                    )
                elif event == "estimate":
                    (
                        estimate_elapsed,
                        estimate,
                        total_capacity,
                        remaining_capacity,
                        average_current,
                        active_mode,
                    ) = payload  # type: ignore[misc]
                    update_time = format_estimated_time(float(estimate_elapsed))
                    if estimate is None:
                        self.estimate_var.set(
                            f"{update_time} 更新｜至 2.0 V：電壓無下降趨勢，無法預估"
                        )
                        self.capacity_var.set("CC 至 2.0 V 容量：等待有效時間預估")
                    else:
                        estimated_seconds = float(estimate)
                        remaining_seconds = max(0.0, estimated_seconds - float(estimate_elapsed))
                        self.estimate_var.set(
                            f"{update_time} 更新｜至 2.0 V：總時間約 "
                            f"{format_estimated_time(estimated_seconds)}｜剩餘約 "
                            f"{format_estimated_time(remaining_seconds)}"
                        )
                        if not str(active_mode).strip().upper().startswith("CC"):
                            self.capacity_var.set("至 2.0 V 容量：僅支援 CC Load Mode")
                        elif total_capacity is None or remaining_capacity is None or average_current is None:
                            self.capacity_var.set("CC 至 2.0 V 容量：無有效電流資料")
                        else:
                            self.capacity_var.set(
                                f"CC 至 2.0 V：總容量約 {float(total_capacity):.2f} mAh｜"
                                f"剩餘約 {float(remaining_capacity):.2f} mAh｜"
                                f"平均電流 {float(average_current) * 1000.0:.2f} mA"
                            )
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

    def save_screenshot(self) -> None:
        directory = Path(__file__).resolve().parents[1] / "data"
        path = next_screenshot_path(directory, datetime.now())
        try:
            window = self.winfo_toplevel()
            window.update_idletasks()
            root_handle = ctypes.windll.user32.GetAncestor(window.winfo_id(), 2)
            if not root_handle:
                raise OSError("無法取得程式視窗")
            screenshot = ImageGrab.grab(window=root_handle)
            screenshot.save(path, "PNG")
        except (OSError, ValueError) as exc:
            messagebox.showerror("程式截圖", str(exc), parent=self)
        else:
            messagebox.showinfo("程式截圖", f"已儲存：\n{path}", parent=self)

    def current_config(self) -> dict[str, float | int | str]:
        try:
            interval = float(self.interval_var.get())
        except ValueError:
            interval = 1.0
        stop_mode = "duration" if self.stop_mode_var.get() == "測試時間" else "cutoff"
        try:
            stop_value = float(self.stop_value_var.get())
        except ValueError:
            stop_value = 60.0 if stop_mode == "duration" else 3.0
        result: dict[str, float | int | str] = {
            "load_sample_interval": interval,
            "load_stop_mode": stop_mode,
            "load_notes": self.notes_text.get("1.0", "end-1c"),
        }
        result["load_test_duration_minutes" if stop_mode == "duration" else "load_cutoff_voltage"] = stop_value
        return result

    def any_input_on(self) -> bool:
        return any(self.output_states.values())
