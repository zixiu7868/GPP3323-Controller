from __future__ import annotations

from gpp3323.i18n import tr

import csv
import math
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure


def build_steps(start: float, end: float, count: int, seconds: float,
                interval: float) -> list[float]:
    if not all(math.isfinite(v) and 0 <= v <= 3.2 for v in (start, end)):
        raise ValueError(tr('起始與結束電流必須介於 0 至 3.2 A'))
    if not 2 <= count <= 1000:
        raise ValueError(tr('階梯數必須介於 2 至 1000（包含起始與結束電流）'))
    if not math.isfinite(seconds) or not 0.2 <= seconds <= 86400:
        raise ValueError(tr('每階時間必須介於 0.2 至 86400 秒'))
    if not math.isfinite(interval) or not 0.2 <= interval <= seconds:
        raise ValueError(tr('取樣週期至少 0.2 秒，且不可大於每階時間'))
    return [start + (end - start) * i / (count - 1) for i in range(count)]


def run_steps(client, channel, levels, seconds, interval, stop, emit,
              clock=time.monotonic):
    """Run each level for its full dwell, with interruptible waits and cleanup."""
    touched = False
    try:
        mode = client.get_channel_mode(channel)
        if not client.is_load_mode(mode) or not mode.strip().upper().startswith("CC"):
            raise ValueError(tr('請先在外部電源斷開時按「準備 CC 模式」，再接上待測電源'))
        if stop.is_set():
            return
        touched = True
        client.set_output(channel, False)
        origin = None
        for index, level in enumerate(levels):
            if stop.is_set():
                break
            client.set_load_current(channel, level)
            if index == 0:
                if stop.is_set():
                    break
                with client._lock:
                    if stop.is_set():
                        break
                    _, enabled = client.enable_load_input(channel)
                if not enabled:
                    raise RuntimeError(tr('無法確認 Load Input 已啟用'))
            started = clock()
            if origin is None:
                origin = started
            emit("step", (started - origin, index + 1, level))
            deadline = started + seconds
            while not stop.is_set():
                reading = client.measure(channel)
                emit("sample", (clock() - origin, index + 1, level,
                                reading.voltage, reading.current))
                remaining = deadline - clock()
                if remaining <= 0:
                    break
                if stop.wait(min(interval, remaining)) or clock() >= deadline:
                    break
        emit("status", tr('已停止') if stop.is_set() else tr('測試完成'))
    except Exception as exc:
        emit("error", str(exc))
    finally:
        if touched:
            try:
                client.set_output(channel, False)
            except Exception as exc:
                emit("error", tr('無法確認負載已關閉：{0}', f'{exc}'))
        emit("done", None)


class CCStepTab(ttk.Frame):
    def __init__(self, parent, client_getter, run_io, config, on_running, load_busy=lambda: False):
        super().__init__(parent, padding=10)
        self.client_getter, self.run_io, self.on_running = client_getter, run_io, on_running
        self.load_busy = load_busy
        self.connected = self.running = False
        self.worker = None
        self.stop_event = threading.Event()
        self.events = queue.Queue()
        self.samples, self.transitions = [], []
        self.channel = tk.StringVar(value="1")
        self.fields = {}
        self.controls = []
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="Channel").pack(side="left")
        channel = ttk.Combobox(row, textvariable=self.channel, values=("1", "2"), width=3, state="readonly")
        channel.pack(side="left", padx=5)
        self.controls.append(channel)
        for key, label, default in (("start", tr('起始 CC (A)'), "0.1"),
                                    ("end", tr('結束 CC (A)'), "1.0"),
                                    ("count", tr('階梯數'), "10"),
                                    ("seconds", tr('每階 (s)'), "10"),
                                    ("interval", tr('取樣 (s)'), "0.5")):
            ttk.Label(row, text=label).pack(side="left", padx=(8, 2))
            var = tk.StringVar(value=str(config.get("cc_step_" + key, default)))
            self.fields[key] = var
            entry = ttk.Entry(row, textvariable=var, width=7)
            entry.pack(side="left")
            self.controls.append(entry)
        ttk.Label(self, text=tr('等距階梯包含起始與結束電流，可遞增或遞減。準備模式時請斷開外部電源；每通道上限 50 W。')
                  ).pack(anchor="w", pady=8)
        buttons = ttk.Frame(self)
        buttons.pack(fill="x")
        for label, command in ((tr('準備 CC 模式'), self.prepare), (tr('開始測試'), self.start)):
            button = ttk.Button(buttons, text=label, command=command)
            button.pack(side="left", padx=3)
            self.controls.append(button)
        self.stop_button = ttk.Button(buttons, text=tr('停止並關閉負載'), command=self.stop)
        self.stop_button.pack(side="left", padx=3)
        ttk.Button(buttons, text=tr('匯出 CSV'), command=self.export).pack(side="right")
        self.status = tk.StringVar(value=tr('尚未開始'))
        self.cursor = tk.StringVar(value=tr('游標移到曲線可查看時間、電壓與電流；工具列可縮放轉折區域。'))
        ttk.Label(self, textvariable=self.status).pack(anchor="w", pady=5)
        ttk.Label(self, textvariable=self.cursor).pack(anchor="w")
        self.figure = Figure(figsize=(9, 5), dpi=100, constrained_layout=True)
        self.voltage_axis = self.figure.add_subplot(111)
        self.current_axis = self.voltage_axis.twinx()
        self.canvas = FigureCanvasTkAgg(self.figure, master=self)
        NavigationToolbar2Tk(self.canvas, self)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.canvas.mpl_connect("motion_notify_event", self.hover)
        self.redraw()
        self.set_connected(False)
        self.after(100, self.poll)

    def values(self):
        start, end = float(self.fields["start"].get()), float(self.fields["end"].get())
        count = int(self.fields["count"].get())
        seconds, interval = float(self.fields["seconds"].get()), float(self.fields["interval"].get())
        return build_steps(start, end, count, seconds, interval), seconds, interval

    def prepare(self):
        if self.load_busy():
            messagebox.showerror(tr('CC 階梯'), tr('請先停止 Load Mode 測試'), parent=self)
            return
        try:
            levels, _, _ = self.values()
            client, channel = self.client_getter(), int(self.channel.get())
            if client is None:
                raise ValueError(tr('設備尚未連線'))
        except ValueError as exc:
            messagebox.showerror(tr('CC 階梯'), str(exc), parent=self)
            return
        self.run_io(lambda: client.configure_load(channel, "CC", levels[0]),
                    lambda result: self.status.set(tr('CC 準備完成：{0}；接上待測電源後開始測試', f'{result}')))

    def start(self):
        if self.running:
            return
        if self.load_busy():
            messagebox.showerror(tr('CC 階梯'), tr('請先停止 Load Mode 測試'), parent=self)
            return
        try:
            levels, seconds, interval = self.values()
            client, channel = self.client_getter(), int(self.channel.get())
            if client is None:
                raise ValueError(tr('設備尚未連線'))
        except ValueError as exc:
            messagebox.showerror(tr('CC 階梯'), str(exc), parent=self)
            return
        self.samples.clear()
        self.transitions.clear()
        self.run_channel = channel
        self.redraw()
        self.stop_event.clear()
        self.running = True
        self.on_running(True)
        self.set_connected(self.connected)
        self.status.set(tr('執行中：{0} 階，預計 {1} 秒', f'{len(levels)}', f'{len(levels) * seconds:g}'))
        self.worker = threading.Thread(target=run_steps, args=(client, channel, levels, seconds,
            interval, self.stop_event, lambda kind, value: self.events.put((kind, value))), daemon=True)
        self.worker.start()

    def stop(self):
        self.stop_event.set()

    def shutdown(self):
        self.stop()
        if self.worker:
            self.worker.join()

    def set_connected(self, connected):
        self.connected = connected
        for widget in self.controls:
            widget.configure(state=("readonly" if isinstance(widget, ttk.Combobox) else "normal")
                             if connected and not self.running else "disabled")
        self.stop_button.configure(state="normal" if self.running else "disabled")

    def poll(self):
        changed = False
        while not self.events.empty():
            kind, value = self.events.get_nowait()
            if kind == "sample":
                self.samples.append(value)
                changed = True
            elif kind == "step":
                self.transitions.append(value)
                self.status.set(tr('第 {0} 階：{1} A，切換時間 {2} s', f'{value[1]}', f'{value[2]:.4f}', f'{value[0]:.3f}'))
            elif kind == "status":
                self.status.set(value)
            elif kind == "error":
                self.status.set(value)
                messagebox.showerror(tr('CC 階梯'), value, parent=self)
            elif kind == "done":
                self.running = False
                self.on_running(False)
                self.set_connected(self.connected)
        if changed:
            self.redraw()
        self.after(100, self.poll)

    def redraw(self):
        left, right = self.voltage_axis, self.current_axis
        left.clear()
        right.clear()
        left.set_xlabel("Elapsed time (s)")
        left.set_ylabel("Voltage (V)", color="tab:blue")
        right.set_ylabel("Current (A)", color="tab:red")
        left.tick_params(axis="y", colors="tab:blue")
        right.tick_params(axis="y", colors="tab:red")
        left.grid(alpha=0.25)
        if self.samples:
            t, _, _, v, i = zip(*self.samples)
            left.plot(t, v, color="tab:blue", label="Voltage")
            right.plot(t, i, color="tab:red", label="Measured current")
            times = [s[0] for s in self.transitions] + [t[-1]]
            levels = [s[2] for s in self.transitions]
            right.step(times, levels + [levels[-1]], where="post", color="tab:orange",
                       linestyle="--", alpha=0.7, label="CC setpoint")
            for when, _, _ in self.transitions[1:]:
                left.axvline(when, color="gray", alpha=0.2, linewidth=0.7)
            left.legend(loc="upper left")
            right.legend(loc="upper right")
        self.canvas.draw_idle()

    def hover(self, event):
        if event.xdata is None or not self.samples:
            return
        s = min(self.samples, key=lambda s: abs(s[0] - event.xdata))
        self.cursor.set(tr('時間 {0} s ｜ 第 {1} 階 ｜ 設定 {2} A ｜ 電壓 {3} V ｜ 電流 {4} A', f'{s[0]:.3f}', f'{s[1]}', f'{s[2]:.4f}', f'{s[3]:.5g}', f'{s[4]:.5g}'))

    def export(self):
        if not self.samples:
            return
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if path:
            try:
                with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["channel", "elapsed_s", "step", "set_current_A", "voltage_V", "current_A"])
                    writer.writerows((self.run_channel, *row) for row in self.samples)
            except OSError as exc:
                messagebox.showerror(tr('匯出失敗'), str(exc), parent=self)

    def current_config(self):
        return {"cc_step_" + key: var.get() for key, var in self.fields.items()}
