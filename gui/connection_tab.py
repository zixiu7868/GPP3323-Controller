from __future__ import annotations

from gpp3323.i18n import tr

import socket
import threading
import time
import tkinter as tk
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, Callable

from gpp3323 import GPP3323Client


class ConnectionTab(ttk.Frame):
    def __init__(
        self,
        parent: tk.Misc,
        config: dict[str, Any],
        on_connected: Callable[[GPP3323Client], None],
        on_disconnected: Callable[[], None],
        save_config: Callable[[dict[str, Any]], None],
    ) -> None:
        super().__init__(parent, padding=16)
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.save_config = save_config
        self.client: GPP3323Client | None = None

        self.host_var = tk.StringVar(value=str(config.get("host", "10.0.0.123")))
        self.port_var = tk.StringVar(value=str(config.get("port", 1026)))
        self.timeout_var = tk.StringVar(value=str(config.get("timeout", 3.0)))
        self.state_var = tk.StringVar(value=tr('● 未連線'))
        self.idn_var = tk.StringVar(value="—")

        settings = ttk.LabelFrame(self, text=tr('LAN / SCPI 連線'), padding=14)
        settings.pack(fill="x")
        for column in range(8):
            settings.columnconfigure(column, weight=1 if column in (1, 3, 5) else 0)

        ttk.Label(settings, text=tr('IP 位址')).grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.host_var, width=18).grid(
            row=0, column=1, padx=(6, 18), sticky="ew"
        )
        ttk.Label(settings, text="Port").grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, textvariable=self.port_var, width=8).grid(
            row=0, column=3, padx=(6, 18), sticky="ew"
        )
        ttk.Label(settings, text="Timeout (s)").grid(row=0, column=4, sticky="w")
        ttk.Entry(settings, textvariable=self.timeout_var, width=8).grid(
            row=0, column=5, padx=(6, 18), sticky="ew"
        )
        self.test_button = ttk.Button(settings, text=tr('測試 TCP'), command=self.test_tcp)
        self.test_button.grid(row=0, column=6, padx=4)
        self.connect_button = ttk.Button(settings, text=tr('連線'), command=self.connect)
        self.connect_button.grid(row=0, column=7, padx=4)

        status = ttk.LabelFrame(self, text=tr('設備狀態'), padding=14)
        status.pack(fill="x", pady=(14, 0))
        ttk.Label(status, textvariable=self.state_var, font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(status, text=tr('設備識別：')).grid(row=1, column=0, pady=(12, 0), sticky="nw")
        ttk.Label(status, textvariable=self.idn_var).grid(
            row=1, column=1, pady=(12, 0), sticky="w"
        )
        status.columnconfigure(1, weight=1)

        log_frame = ttk.LabelFrame(self, text=tr('SCPI 通訊紀錄'), padding=8)
        log_frame.pack(fill="both", expand=True, pady=(14, 0))
        self.log = scrolledtext.ScrolledText(
            log_frame, height=18, state="disabled", font=("Consolas", 9)
        )
        self.log.pack(fill="both", expand=True)
        ttk.Button(log_frame, text=tr('清除紀錄'), command=self.clear_log).pack(
            anchor="e", pady=(8, 0)
        )

    def _values(self) -> tuple[str, int, float]:
        host = self.host_var.get().strip()
        if not host:
            raise ValueError(tr('請輸入 IP 位址'))
        port = int(self.port_var.get())
        timeout = float(self.timeout_var.get())
        if not 1 <= port <= 65535:
            raise ValueError(tr('Port 必須介於 1 至 65535'))
        if not 0.2 <= timeout <= 60:
            raise ValueError(tr('Timeout 必須介於 0.2 至 60 秒'))
        return host, port, timeout

    def append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")

        def append() -> None:
            self.log.configure(state="normal")
            self.log.insert("end", f"[{stamp}] {message}\n")
            self.log.see("end")
            self.log.configure(state="disabled")

        self.after(0, append)

    def clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def test_tcp(self) -> None:
        try:
            host, port, timeout = self._values()
        except Exception as exc:
            messagebox.showerror(tr('連線參數'), str(exc), parent=self)
            return
        self.test_button.configure(state="disabled")
        self.append_log(tr('測試 TCP {0}:{1}', f'{host}', f'{port}'))

        def worker() -> None:
            try:
                with socket.create_connection((host, port), timeout=timeout):
                    pass
            except Exception as exc:
                self.after(
                    0,
                    lambda exc=exc: messagebox.showerror(
                        tr('TCP 測試'), str(exc), parent=self
                    ),
                )
            else:
                self.after(0, lambda: messagebox.showinfo(tr('TCP 測試'), tr('連線成功'), parent=self))
            finally:
                self.after(0, lambda: self.test_button.configure(state="normal"))

        threading.Thread(target=worker, daemon=True).start()

    def connect(self) -> None:
        if self.client and self.client.connected:
            self.disconnect()
            return
        try:
            host, port, timeout = self._values()
        except Exception as exc:
            messagebox.showerror(tr('連線參數'), str(exc), parent=self)
            return

        self.connect_button.configure(state="disabled")
        self.state_var.set(tr('● 連線中…'))
        self.save_config({"host": host, "port": port, "timeout": timeout})

        def worker() -> None:
            client = GPP3323Client(host, port, timeout, logger=self.append_log)
            try:
                identity = client.connect(validate_model=True)
                error = client.get_error()
            except Exception as exc:
                client.disconnect()
                self.after(0, lambda exc=exc: self._connect_failed(exc))
            else:
                self.after(0, lambda: self._connect_ok(client, identity, error))

        threading.Thread(target=worker, daemon=True).start()

    def _connect_ok(self, client: GPP3323Client, identity: str, error: str) -> None:
        self.client = client
        self.idn_var.set(identity)
        self.state_var.set(tr('● 已連線'))
        self.connect_button.configure(text=tr('斷線'), state="normal")
        self.append_log(tr('設備狀態：{0}', f'{error}'))
        self.on_connected(client)

    def _connect_failed(self, exc: Exception) -> None:
        self.client = None
        self.state_var.set(tr('● 連線失敗'))
        self.connect_button.configure(text=tr('連線'), state="normal")
        messagebox.showerror(tr('GPP-3323 連線失敗'), str(exc), parent=self)

    def disconnect(self) -> None:
        self.on_disconnected()
        if self.client:
            self.client.disconnect()
        self.client = None
        self.state_var.set(tr('● 未連線'))
        self.idn_var.set("—")
        self.connect_button.configure(text=tr('連線'), state="normal")
        self.append_log(tr('已中斷連線'))
