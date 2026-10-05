from __future__ import annotations

from gpp3323.i18n import tr

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable

from gpp3323 import GPP3323Client


class ChannelTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        client_getter: Callable[[], GPP3323Client | None],
        run_io: Callable[..., None],
    ) -> None:
        super().__init__(parent, padding=16)
        self.client_getter = client_getter
        self.run_io = run_io
        self.voltage_vars: dict[int, tk.StringVar] = {}
        self.current_vars: dict[int, tk.StringVar] = {}
        self.output_states = {1: False, 2: False, 3: False}
        self.output_labels: dict[int, tk.StringVar] = {}
        self.controls: list[tk.Widget] = []

        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(
            header,
            text=tr('輸出設定不會自動開啟 Channel；套用設定與 Output ON 是分開操作。'),
        ).pack(side="left")
        all_off = ttk.Button(header, text=tr('全部輸出 OFF'), command=self.all_off)
        all_off.pack(side="right")
        self.controls.append(all_off)

        channels = ttk.Frame(self)
        channels.pack(fill="both", expand=True)
        for channel in (1, 2, 3):
            self._build_channel(channels, channel).pack(
                side="left", fill="both", expand=True, padx=6
            )
        self.set_connected(False)

    def _build_channel(self, parent: tk.Misc, channel: int) -> ttk.LabelFrame:
        frame = ttk.LabelFrame(parent, text=f"CH{channel}", padding=14)
        self.voltage_vars[channel] = tk.StringVar(value="3.300" if channel == 3 else "0.000")
        self.current_vars[channel] = tk.StringVar(value="—" if channel == 3 else "0.1000")
        self.output_labels[channel] = tk.StringVar(value="OUTPUT OFF")

        ttk.Label(frame, text=tr('設定電壓 (V)')).grid(row=0, column=0, sticky="w", pady=5)
        if channel == 3:
            voltage: tk.Widget = ttk.Combobox(
                frame,
                textvariable=self.voltage_vars[channel],
                values=("1.800", "2.500", "3.300", "5.000"),
                state="readonly",
                width=12,
            )
        else:
            voltage = ttk.Entry(frame, textvariable=self.voltage_vars[channel], width=14)
        voltage.grid(row=0, column=1, sticky="ew", pady=5)

        ttk.Label(frame, text=tr('電流上限 (A)')).grid(row=1, column=0, sticky="w", pady=5)
        current = ttk.Entry(frame, textvariable=self.current_vars[channel], width=14)
        current.grid(row=1, column=1, sticky="ew", pady=5)
        if channel == 3:
            current.configure(state="disabled")
            ttk.Label(frame, text=tr('CH3 電流不可由 SCPI 設定'), foreground="#666").grid(
                row=2, column=0, columnspan=2, sticky="w"
            )

        ttk.Separator(frame).grid(row=3, column=0, columnspan=2, sticky="ew", pady=12)
        ttk.Label(
            frame,
            textvariable=self.output_labels[channel],
            font=("Segoe UI", 13, "bold"),
        ).grid(row=4, column=0, columnspan=2, pady=8)

        apply_button = ttk.Button(frame, text=tr('套用 V / I'), command=lambda: self.apply(channel))
        read_button = ttk.Button(frame, text=tr('讀回設定'), command=lambda: self.read(channel))
        output_button = ttk.Button(
            frame, text=tr('切換輸出'), command=lambda: self.toggle_output(channel)
        )
        apply_button.grid(row=5, column=0, columnspan=2, sticky="ew", pady=4)
        read_button.grid(row=6, column=0, columnspan=2, sticky="ew", pady=4)
        output_button.grid(row=7, column=0, columnspan=2, sticky="ew", pady=4)
        frame.columnconfigure(1, weight=1)
        self.controls.extend([voltage, apply_button, read_button, output_button])
        if channel != 3:
            self.controls.append(current)
        return frame

    def set_connected(self, connected: bool) -> None:
        state = "normal" if connected else "disabled"
        for widget in self.controls:
            try:
                if isinstance(widget, ttk.Combobox) and connected:
                    widget.configure(state="readonly")
                else:
                    widget.configure(state=state)
            except tk.TclError:
                pass
        if not connected:
            self.output_states = {1: False, 2: False, 3: False}
            for channel in (1, 2, 3):
                self.output_labels[channel].set("OUTPUT —")

    def refresh_all(self) -> None:
        def operation() -> dict[int, tuple[float, float | None, bool]]:
            client = self._client()
            return {
                channel: (
                    client.get_voltage_setting(channel),
                    client.get_current_setting(channel),
                    client.get_output(channel),
                )
                for channel in (1, 2, 3)
            }

        def success(results: dict[int, tuple[float, float | None, bool]]) -> None:
            for channel, (voltage, current, enabled) in results.items():
                self.voltage_vars[channel].set(f"{voltage:.3f}")
                if current is not None:
                    self.current_vars[channel].set(f"{current:.4f}")
                self._set_output_display(channel, enabled)

        self.run_io(operation, success, message=tr('正在讀取全部 Channel 狀態…'))

    def _client(self) -> GPP3323Client:
        client = self.client_getter()
        if not client:
            raise RuntimeError(tr('設備尚未連線'))
        return client

    def apply(self, channel: int) -> None:
        try:
            voltage = float(self.voltage_vars[channel].get())
            current = None if channel == 3 else float(self.current_vars[channel].get())
        except ValueError:
            messagebox.showerror(tr('輸入錯誤'), tr('電壓或電流格式不正確'), parent=self)
            return

        def operation() -> str:
            client = self._client()
            client.set_voltage(channel, voltage)
            if current is not None:
                client.set_current(channel, current)
            return client.get_error()

        self.run_io(
            operation,
            lambda error: messagebox.showinfo(
                f"CH{channel}", tr('設定已套用\n設備狀態：{0}', f'{error}'), parent=self
            ),
            message=tr('正在設定 CH{0}…', f'{channel}'),
        )

    def read(self, channel: int) -> None:
        def operation() -> tuple[float, float | None, bool]:
            client = self._client()
            return (
                client.get_voltage_setting(channel),
                client.get_current_setting(channel),
                client.get_output(channel),
            )

        def success(result: tuple[float, float | None, bool]) -> None:
            voltage, current, enabled = result
            self.voltage_vars[channel].set(f"{voltage:.3f}")
            if current is not None:
                self.current_vars[channel].set(f"{current:.4f}")
            self._set_output_display(channel, enabled)

        self.run_io(operation, success, message=tr('正在讀取 CH{0} 設定…', f'{channel}'))

    def toggle_output(self, channel: int) -> None:
        target = not self.output_states[channel]
        if target and not messagebox.askyesno(
            tr('確認開啟輸出'),
            tr('即將開啟 CH{0} 輸出。\n設定電壓：{1} V\n電流上限：{2} A\n\n請確認接線與負載正確。', f'{channel}', f'{self.voltage_vars[channel].get()}', f'{self.current_vars[channel].get()}'),
            parent=self,
        ):
            return

        def operation() -> bool:
            client = self._client()
            client.set_output(channel, target)
            return client.get_output(channel)

        self.run_io(
            operation,
            lambda enabled: self._set_output_display(channel, enabled),
            message=tr('正在切換 CH{0} 輸出…', f'{channel}'),
        )

    def _set_output_display(self, channel: int, enabled: bool) -> None:
        self.output_states[channel] = enabled
        self.output_labels[channel].set("OUTPUT ON" if enabled else "OUTPUT OFF")

    def all_off(self) -> None:
        def operation() -> None:
            self._client().all_outputs_off()

        def success(_: Any) -> None:
            for channel in (1, 2, 3):
                self._set_output_display(channel, False)

        self.run_io(operation, success, message=tr('正在關閉全部輸出…'))

    def any_output_on(self) -> bool:
        return any(self.output_states.values())
