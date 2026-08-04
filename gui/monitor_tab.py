from __future__ import annotations

import csv
import queue
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable
from typing import Any

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure

from gpp3323 import GPP3323Client, Measurement


@dataclass(frozen=True)
class Sample:
    timestamp: datetime
    elapsed: float
    channel: int
    voltage: float
    current: float
    power: float


class MonitorTab(ttk.Frame):
    COLORS = {1: "#0b4f9c", 2: "#d87300", 3: "#198754"}

    def __init__(
        self,
        parent: tk.Misc,
        client_getter: Callable[[], GPP3323Client | None],
        config: dict[str, Any],
    ) -> None:
        super().__init__(parent, padding=10)
        self.client_getter = client_getter
        self.channel_vars = {channel: tk.BooleanVar(value=channel == 1) for channel in (1, 2, 3)}
        self.interval_var = tk.StringVar(value=str(config.get("sample_interval", 1.0)))
        self.max_points_var = tk.StringVar(value=str(config.get("max_points", 3600)))
        self.latest_var = tk.StringVar(value="尚無量測資料")
        self.samples: list[Sample] = []
        self._queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False

        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(0, 8))
        ttk.Label(controls, text="Channels:").pack(side="left")
        self.control_widgets: list[tk.Widget] = []
        for channel in (1, 2, 3):
            widget = ttk.Checkbutton(
                controls, text=f"CH{channel}", variable=self.channel_vars[channel]
            )
            widget.pack(side="left", padx=4)
            self.control_widgets.append(widget)
        ttk.Label(controls, text="取樣週期 (s)").pack(side="left", padx=(18, 4))
        interval = ttk.Entry(controls, textvariable=self.interval_var, width=7)
        interval.pack(side="left")
        ttk.Label(controls, text="保留點數").pack(side="left", padx=(18, 4))
        max_points = ttk.Entry(controls, textvariable=self.max_points_var, width=8)
        max_points.pack(side="left")
        self.start_button = ttk.Button(controls, text="開始量測", command=self.start)
        self.start_button.pack(side="left", padx=(18, 4))
        self.stop_button = ttk.Button(controls, text="停止", command=self.stop)
        self.stop_button.pack(side="left", padx=4)
        self.clear_button = ttk.Button(controls, text="清除", command=self.clear)
        self.clear_button.pack(side="left", padx=4)
        self.export_button = ttk.Button(controls, text="匯出 CSV", command=self.export_csv)
        self.export_button.pack(side="right")
        self.control_widgets.extend(
            [interval, max_points, self.start_button, self.stop_button, self.clear_button, self.export_button]
        )

        ttk.Label(self, textvariable=self.latest_var, anchor="w").pack(fill="x", pady=(0, 6))

        self.figure = Figure(figsize=(9, 6), dpi=100, constrained_layout=True)
        self.voltage_axis = self.figure.add_subplot(211)
        self.current_axis = self.figure.add_subplot(212, sharex=self.voltage_axis)
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self._redraw()
        self.after(100, self._poll_queue)
        self.set_connected(False)

    def set_connected(self, connected: bool) -> None:
        if not connected:
            self.stop()
        for widget in self.control_widgets:
            if widget in (self.clear_button, self.export_button):
                continue
            try:
                widget.configure(state="normal" if connected else "disabled")
            except tk.TclError:
                pass
        self.clear_button.configure(state="normal")
        self.export_button.configure(state="normal" if self.samples else "disabled")
        self._update_button_states()

    def current_config(self) -> dict[str, float | int]:
        try:
            interval = float(self.interval_var.get())
        except ValueError:
            interval = 1.0
        try:
            max_points = int(self.max_points_var.get())
        except ValueError:
            max_points = 3600
        return {"sample_interval": interval, "max_points": max_points}

    def start(self) -> None:
        if self._running:
            return
        client = self.client_getter()
        if not client:
            messagebox.showerror("連續量測", "設備尚未連線", parent=self)
            return
        channels = [ch for ch, var in self.channel_vars.items() if var.get()]
        if not channels:
            messagebox.showerror("連續量測", "請至少選擇一個 Channel", parent=self)
            return
        try:
            interval = float(self.interval_var.get())
            max_points = int(self.max_points_var.get())
            if not 0.2 <= interval <= 3600:
                raise ValueError("取樣週期必須介於 0.2 至 3600 秒")
            if not 10 <= max_points <= 100000:
                raise ValueError("保留點數必須介於 10 至 100000")
        except ValueError as exc:
            messagebox.showerror("連續量測", str(exc), parent=self)
            return

        self._running = True
        self._stop_event.clear()
        start_time = time.monotonic()

        def worker() -> None:
            next_time = time.monotonic()
            while not self._stop_event.is_set():
                for channel in channels:
                    if self._stop_event.is_set():
                        break
                    try:
                        reading: Measurement = client.measure(channel)
                    except Exception as exc:
                        self._queue.put(("error", exc))
                        self._stop_event.set()
                        break
                    sample = Sample(
                        datetime.now(),
                        time.monotonic() - start_time,
                        channel,
                        reading.voltage,
                        reading.current,
                        reading.power,
                    )
                    self._queue.put(("sample", (sample, max_points)))
                next_time += interval
                self._stop_event.wait(max(0.0, next_time - time.monotonic()))
            self._queue.put(("stopped", None))

        self._thread = threading.Thread(target=worker, daemon=True)
        self._thread.start()
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
                        del self.samples[: len(self.samples) - max_points]
                    self.latest_var.set(
                        f"{sample.timestamp:%Y-%m-%d %H:%M:%S}  CH{sample.channel}  "
                        f"{sample.voltage:.5f} V   {sample.current:.5f} A   {sample.power:.5f} W"
                    )
                    changed = True
                elif event == "error":
                    messagebox.showerror("連續量測中斷", str(payload), parent=self)
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
        connected = self.client_getter() is not None
        self.start_button.configure(state="disabled" if self._running or not connected else "normal")
        self.stop_button.configure(state="normal" if self._running else "disabled")

    def _redraw(self) -> None:
        self.voltage_axis.clear()
        self.current_axis.clear()
        for channel in (1, 2, 3):
            data = [sample for sample in self.samples if sample.channel == channel]
            if not data:
                continue
            x = [sample.elapsed for sample in data]
            self.voltage_axis.plot(
                x, [sample.voltage for sample in data], label=f"CH{channel}", color=self.COLORS[channel]
            )
            self.current_axis.plot(
                x, [sample.current for sample in data], label=f"CH{channel}", color=self.COLORS[channel]
            )
        self.voltage_axis.set_ylabel("Voltage (V)")
        self.current_axis.set_ylabel("Current (A)")
        self.current_axis.set_xlabel("Elapsed time (s)")
        self.voltage_axis.grid(True, alpha=0.25)
        self.current_axis.grid(True, alpha=0.25)
        if self.voltage_axis.lines:
            self.voltage_axis.legend(loc="upper right")
            self.current_axis.legend(loc="upper right")
        self.canvas.draw_idle()

    def export_csv(self) -> None:
        if not self.samples:
            messagebox.showinfo("匯出 CSV", "目前沒有資料", parent=self)
            return
        default = f"gpp3323_{datetime.now():%Y%m%d_%H%M%S}.csv"
        path = filedialog.asksaveasfilename(
            parent=self,
            title="匯出量測資料",
            initialdir=str(Path(__file__).resolve().parents[1] / "data"),
            initialfile=default,
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv")],
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(["timestamp", "elapsed_s", "channel", "voltage_V", "current_A", "power_W"])
                for sample in self.samples:
                    writer.writerow([
                        sample.timestamp.isoformat(timespec="milliseconds"),
                        f"{sample.elapsed:.3f}",
                        sample.channel,
                        f"{sample.voltage:.6f}",
                        f"{sample.current:.6f}",
                        f"{sample.power:.6f}",
                    ])
        except OSError as exc:
            messagebox.showerror("匯出 CSV", str(exc), parent=self)
        else:
            messagebox.showinfo("匯出 CSV", f"已儲存：\n{path}", parent=self)
