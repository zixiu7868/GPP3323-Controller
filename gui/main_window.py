from __future__ import annotations

import json
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Callable

from gpp3323 import GPP3323Client
from gui.channel_tab import ChannelTab
from gui.connection_tab import ConnectionTab
from gui.monitor_tab import MonitorTab


class MainWindow(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("GW Instek GPP-3323 控制與監測")
        self.geometry("1180x780")
        self.minsize(980, 680)
        self.client: GPP3323Client | None = None
        self.config_path = Path(__file__).resolve().parents[1] / "config.json"
        self.config_data = self._load_config()

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
        notebook.add(self.connection_tab, text="  設備連線  ")
        notebook.add(self.channel_tab, text="  Channel 設定  ")
        notebook.add(self.monitor_tab, text="  連續量測  ")

        self.status_var = tk.StringVar(value="未連線")
        ttk.Separator(self).pack(fill="x")
        ttk.Label(
            self, textvariable=self.status_var, style="Status.TLabel", anchor="w"
        ).pack(fill="x")
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_config(self) -> dict[str, Any]:
        defaults = {
            "host": "10.0.0.123",
            "port": 1026,
            "timeout": 3.0,
            "sample_interval": 1.0,
            "max_points": 3600,
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
                json.dumps(self.config_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            self.status_var.set(f"設定檔儲存失敗：{exc}")

    def get_client(self) -> GPP3323Client | None:
        return self.client if self.client and self.client.connected else None

    def _connected(self, client: GPP3323Client) -> None:
        if self.client and self.client is not client:
            self.client.disconnect()
        self.client = client
        self.channel_tab.set_connected(True)
        self.monitor_tab.set_connected(True)
        self.status_var.set(f"已連線：{client.identity}")
        self.channel_tab.refresh_all()

    def _disconnected(self) -> None:
        self.monitor_tab.stop()
        if self.client:
            self.client.disconnect()
        self.client = None
        self.channel_tab.set_connected(False)
        self.monitor_tab.set_connected(False)
        self.status_var.set("未連線")

    def run_io(
        self,
        operation: Callable[[], Any],
        success: Callable[[Any], None] | None = None,
        failure: Callable[[Exception], None] | None = None,
        message: str = "執行中…",
    ) -> None:
        self.status_var.set(message)

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
        self.status_var.set("操作完成")
        if callback:
            callback(result)

    def _io_failed(
        self, exc: Exception, callback: Callable[[Exception], None] | None
    ) -> None:
        self.status_var.set(f"操作失敗：{exc}")
        if callback:
            callback(exc)
        else:
            messagebox.showerror("GPP-3323", str(exc), parent=self)

    def _on_close(self) -> None:
        self.monitor_tab.stop()
        output_on = self.channel_tab.any_output_on()
        if self.get_client() and output_on:
            choice = messagebox.askyesnocancel(
                "輸出仍開啟",
                "偵測到至少一個 Channel 輸出仍為 ON。\n\n"
                "選擇「是」：關閉全部輸出後離開\n"
                "選擇「否」：保持輸出並離開\n"
                "選擇「取消」：返回程式",
                parent=self,
            )
            if choice is None:
                return
            if choice:
                try:
                    self.client.all_outputs_off()  # type: ignore[union-attr]
                except Exception as exc:
                    if not messagebox.askyesno(
                        "關閉輸出失敗",
                        f"無法確認輸出已關閉：{exc}\n仍要離開嗎？",
                        parent=self,
                    ):
                        return
        self.save_config(self.monitor_tab.current_config())
        if self.client:
            self.client.disconnect()
        self.destroy()
