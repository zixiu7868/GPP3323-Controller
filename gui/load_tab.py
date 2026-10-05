from __future__ import annotations

from gpp3323.i18n import tr

import ctypes
import math
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from gui.curve_panel import CurvePanel, format_power
from gui.load_data import suggested_name, export_dataset, validate_name
from PIL import ImageGrab

from gpp3323 import GPP3323Client, Measurement
from gui.monitor_tab import Sample, format_engineering


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
        return tr('{0} 秒', f'{seconds:.0f}')
    if seconds < 3600.0:
        return tr('{0} 分鐘', f'{seconds / 60.0:.1f}')
    if seconds < 86400.0:
        return tr('{0} 小時', f'{seconds / 3600.0:.2f}')
    return tr('{0} 天', f'{seconds / 86400.0:.2f}')


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
    def __init__(self, parent: tk.Misc, client_getter: Callable[[], GPP3323Client | None],
                 run_io: Callable[..., None], config: dict[str, Any],
                 open_settings=lambda: None, hardware_busy=lambda: False, on_running=lambda *_: None) -> None:
        super().__init__(parent, padding=10)
        self.client_getter, self.run_io = client_getter, run_io
        self.open_settings, self.hardware_busy, self.on_running = open_settings, hardware_busy, on_running
        self.active_channel = None
        self.channel_var, self.mode_var = tk.IntVar(value=1), tk.StringVar(value="CC")
        self.status_var = tk.StringVar(value=tr('模式：—\u3000LOAD INPUT：—'))
        self.latest_var = tk.StringVar(value=tr('尚無量測資料'))
        self.elapsed_var = tk.StringVar(value=tr('目前測試時間：0.00 min'))
        self.estimate_var = tk.StringVar(value=tr('每 30 秒估算至 2.0 V：尚未計算'))
        self.capacity_var = tk.StringVar(value=tr('CC 至 2.0 V 容量：尚未計算'))
        self.interval_var = tk.StringVar(value=str(config.get("load_sample_interval", 1.0)))
        saved_stop_mode = config.get("load_stop_mode", "duration")
        self.stop_mode_var = tk.StringVar(
            value=tr('測試時間') if saved_stop_mode == "duration" else tr('截止電壓')
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
        self._stop_mode_changed(False)
        self.after(100, self._poll_queue)
        self.set_connected(False)

    def _build_controls(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x", pady=(0, 8))
        settings = ttk.LabelFrame(top, text=tr('測試通道'), padding=10)
        settings.pack(side="left", fill="both", expand=True)
        ttk.Label(settings, text=tr('負載模式與設定值請在「電子負載設定」TAB 操作。')).pack(anchor="w")
        row = ttk.Frame(settings)
        row.pack(fill="x", pady=8)
        ttk.Label(row, text="Channel").pack(side="left")
        for channel in (1, 2):
            button = ttk.Radiobutton(row, text=f"CH{channel}", value=channel,
                                     variable=self.channel_var, command=self._selected_channel_changed)
            button.pack(side="left", padx=5)
            self.controls.append(button)
        ttk.Button(row, text=tr('電子負載設定'), command=self.open_settings).pack(side="right")
        ttk.Label(settings, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(anchor="w")

        notes = ttk.LabelFrame(top, text=tr('備註'), padding=6)
        notes.pack(side="right", fill="both", padx=(8, 0))
        self.notes_text = tk.Text(notes, width=32, height=5, wrap="word", undo=True)
        self.notes_text.pack(fill="both", expand=True)
        self.notes_text.insert("1.0", self.initial_notes)

        monitor = ttk.Frame(self)
        monitor.pack(fill="x", pady=(0, 5))
        ttk.Label(monitor, text=tr('取樣週期 (s)')).pack(side="left")
        interval = ttk.Entry(monitor, textvariable=self.interval_var, width=7)
        interval.pack(side="left", padx=(4, 14))
        ttk.Label(monitor, text=tr('停止條件')).pack(side="left")
        stop_mode = ttk.Combobox(
            monitor, textvariable=self.stop_mode_var,
            values=(tr('測試時間'), tr('截止電壓')), state="readonly", width=14,
        )
        stop_mode.pack(side="left", padx=4)
        stop_mode.bind("<<ComboboxSelected>>", lambda _event: self._stop_mode_changed())
        stop_value = ttk.Entry(monitor, textvariable=self.stop_value_var, width=9)
        stop_value.pack(side="left", padx=(4, 2))
        ttk.Label(monitor, textvariable=self.stop_unit_var).pack(side="left")
        self.start_button = ttk.Button(monitor, text=tr('開始曲線'), command=self.start)
        self.start_button.pack(side="left", padx=(14, 4))
        self.stop_button = ttk.Button(monitor, text=tr('停止'), command=self.stop)
        self.stop_button.pack(side="left", padx=4)
        self.clear_button = ttk.Button(monitor, text=tr('清除'), command=self.clear)
        self.clear_button.pack(side="left", padx=4)
        self.export_button = ttk.Button(monitor, text=tr('匯出 CSV'), command=self.export_csv)
        self.export_button.pack(side="right")
        self.screenshot_button = ttk.Button(monitor, text=tr('程式截圖'), command=self.save_screenshot)
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
        self.curve_panel = CurvePanel(self)
        self.curve_panel.pack(fill="both", expand=True)
        self._redraw()

    def _selected_channel_changed(self):
        self.status_var.set(tr('設備狀態請見「電子負載設定」TAB'))

    def _stop_mode_changed(self, reset_value: bool = True) -> None:
        if self.stop_mode_var.get() == tr('測試時間'):
            self.stop_unit_var.set(tr('分鐘'))
            if reset_value:
                self.stop_value_var.set("60")
        else:
            self.stop_unit_var.set(tr('V（電壓下降至此值時停止）'))
            if reset_value:
                self.stop_value_var.set("3.0")

    def _update_status(self, mode: str, enabled: bool) -> None:
        channel = self.channel_var.get()
        self.mode_var.set(mode.strip().upper()[:2])
        self.output_states[channel] = enabled
        self.status_var.set(tr('CH{0} 模式：{1}\u3000LOAD INPUT：{2}', f'{channel}', f'{mode}', f"{('ON' if enabled else 'OFF')}"))

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            self.stop()
            self.output_states = {1: False, 2: False}
            self.status_var.set(tr('模式：—\u3000LOAD INPUT：—'))
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
        if self.hardware_busy():
            messagebox.showerror(tr('Load 曲線'), tr('請等待負載設定操作或其他測試完成'), parent=self)
            return
        client = self.client_getter()
        if not client:
            messagebox.showerror(tr('Load 曲線'), tr('設備尚未連線'), parent=self)
            return
        try:
            interval = float(self.interval_var.get())
            stop_target = float(self.stop_value_var.get())
            if not 0.2 <= interval <= 3600:
                raise ValueError(tr('取樣週期必須介於 0.2 至 3600 秒'))
            stop_mode = "duration" if self.stop_mode_var.get() == tr('測試時間') else "cutoff"
            if stop_mode == "duration" and not 0.1 <= stop_target <= 10080:
                raise ValueError(tr('測試時間必須介於 0.1 至 10080 分鐘（7 天）'))
            if stop_mode == "cutoff" and not 0.0 <= stop_target <= 33.0:
                raise ValueError(tr('截止電壓必須介於 0 至 33 V'))
        except ValueError as exc:
            messagebox.showerror(tr('Load 曲線'), str(exc), parent=self)
            return
        stop_target_seconds = stop_target * 60.0 if stop_mode == "duration" else stop_target
        channel = self.channel_var.get()
        self._running = True
        self.active_channel = channel
        self.on_running(True)
        self._stop_event.clear()
        self.elapsed_var.set(tr('目前測試時間：0.00 min'))
        self.estimate_var.set(tr('每 30 秒估算至 2.0 V：計算中…'))
        self.capacity_var.set(tr('CC 至 2.0 V 容量：計算中…'))
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
                with client._lock:
                    if self._stop_event.is_set():
                        self._queue.put(("stopped", None))
                        return
                    mode, enabled = client.enable_load_input(channel)
                    if not enabled:
                        raise RuntimeError(tr('無法確認 Load Input 已啟用'))
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
                            tr('已達測試時間 {0} 分鐘', f'{stop_target:g}')
                            if stop_mode == "duration"
                            else tr('電壓已降至截止值 {0} V', f'{stop_target:g}')
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

    def stop_channel(self, channel):
        if self._running and self.active_channel == channel:
            self.stop()

    def clear(self) -> None:
        self.samples.clear()
        self.latest_var.set(tr('尚無量測資料'))
        self.elapsed_var.set(tr('目前測試時間：0.00 min'))
        self.estimate_var.set(tr('每 30 秒估算至 2.0 V：尚未計算'))
        self.capacity_var.set(tr('CC 至 2.0 V 容量：尚未計算'))
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
                    self.elapsed_var.set(tr('目前測試時間：{0} min', f'{sample.elapsed / 60.0:.2f}'))
                    self.latest_var.set(f"{sample.timestamp:%Y-%m-%d %H:%M:%S}  CH{sample.channel}　"
                                        f"{format_engineering(sample.voltage, 'V')}　"
                                        f"{format_engineering(sample.current, 'A')}　P (V×I): {format_power(sample.calculated_power)}")
                    changed = True
                elif event == "error":
                    messagebox.showerror(tr('Load 曲線中斷'), str(payload), parent=self)
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
                        tr('Load 測試完成'), tr('{0}\nCH{1} Load Input 已關閉。', f'{reason}', f'{channel}'), parent=self
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
                            tr('{0} 更新｜至 2.0 V：電壓無下降趨勢，無法預估', f'{update_time}')
                        )
                        self.capacity_var.set(tr('CC 至 2.0 V 容量：等待有效時間預估'))
                    else:
                        estimated_seconds = float(estimate)
                        remaining_seconds = max(0.0, estimated_seconds - float(estimate_elapsed))
                        self.estimate_var.set(
                            tr('{0} 更新｜至 2.0 V：總時間約 {1}｜剩餘約 {2}', f'{update_time}', f'{format_estimated_time(estimated_seconds)}', f'{format_estimated_time(remaining_seconds)}')
                        )
                        if not str(active_mode).strip().upper().startswith("CC"):
                            self.capacity_var.set(tr('至 2.0 V 容量：僅支援 CC Load Mode'))
                        elif total_capacity is None or remaining_capacity is None or average_current is None:
                            self.capacity_var.set(tr('CC 至 2.0 V 容量：無有效電流資料'))
                        else:
                            self.capacity_var.set(
                                tr('CC 至 2.0 V：總容量約 {0} mAh｜剩餘約 {1} mAh｜平均電流 {2} mA', f'{float(total_capacity):.2f}', f'{float(remaining_capacity):.2f}', f'{float(average_current) * 1000.0:.2f}')
                            )
                elif event == "stopped":
                    self._running = False
                    self.active_channel = None
                    self.on_running(False)
                    self._update_button_states()
        except queue.Empty:
            pass
        if changed:
            self.export_button.configure(state="normal")
            self._redraw()
        self.after(100, self._poll_queue)

    def _update_button_states(self) -> None:
        for widget in self.controls:
            if widget not in (self.start_button, self.stop_button):
                widget.configure(state=("readonly" if isinstance(widget, ttk.Combobox) else "normal")
                                 if self._connected and not self._running else "disabled")
        self.start_button.configure(state="disabled" if self._running or not self._connected else "normal")
        self.stop_button.configure(state="normal" if self._running else "disabled")

    def _redraw(self) -> None:
        self.curve_panel.set_series([
            (f"CH{channel}", [s for s in self.samples if s.channel == channel])
            for channel in sorted({s.channel for s in self.samples})
        ])

    def export_csv(self) -> None:
        if not self.samples:
            messagebox.showinfo(tr('匯出 CSV'), tr('目前沒有資料'), parent=self)
            return
        # Snapshot before modal dialogs: live sampling may continue while naming.
        samples = list(self.samples)
        notes = self.notes_text.get("1.0", "end-1c")
        parent = filedialog.askdirectory(
            parent=self, title=tr('選擇測試資料夾的存放位置'),
            initialdir=str(Path(__file__).resolve().parents[1] / "data"))
        if not parent:
            return
        name = suggested_name(notes, datetime.now())
        while True:
            name = simpledialog.askstring(
                tr('匯出 CSV'), tr('確認或修改資料夾名稱（電流後的數字為測試次數）：'),
                initialvalue=name, parent=self)
            if name is None:
                return
            try:
                validate_name(name)
            except ValueError as exc:
                messagebox.showerror(tr('資料夾名稱無效'), str(exc), parent=self)
                continue
            break
        try:
            directory = export_dataset(Path(parent), name, samples, notes)
        except (OSError, ValueError) as exc:
            messagebox.showerror(tr('匯出 CSV'), str(exc), parent=self)
        else:
            messagebox.showinfo(tr('匯出 CSV'), tr('已儲存：\n{0}', f'{directory}'), parent=self)

    def save_screenshot(self) -> None:
        directory = Path(__file__).resolve().parents[1] / "data"
        path = next_screenshot_path(directory, datetime.now())
        try:
            window = self.winfo_toplevel()
            window.update_idletasks()
            root_handle = ctypes.windll.user32.GetAncestor(window.winfo_id(), 2)
            if not root_handle:
                raise OSError(tr('無法取得程式視窗'))
            screenshot = ImageGrab.grab(window=root_handle)
            screenshot.save(path, "PNG")
        except (OSError, ValueError) as exc:
            messagebox.showerror(tr('程式截圖'), str(exc), parent=self)
        else:
            messagebox.showinfo(tr('程式截圖'), tr('已儲存：\n{0}', f'{path}'), parent=self)

    def current_config(self) -> dict[str, float | int | str]:
        try:
            interval = float(self.interval_var.get())
        except ValueError:
            interval = 1.0
        stop_mode = "duration" if self.stop_mode_var.get() == tr('測試時間') else "cutoff"
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
