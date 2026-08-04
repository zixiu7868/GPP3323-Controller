from __future__ import annotations

import re
import socket
import threading
from dataclasses import dataclass
from typing import Callable


class GPPError(RuntimeError):
    """Raised when communication or validation fails."""


@dataclass(frozen=True)
class Measurement:
    voltage: float
    current: float
    power: float


class GPP3323Client:
    """Thread-safe SCPI-over-TCP client for the GW Instek GPP-3323."""

    CH12_MAX_VOLTAGE = 32.0
    CH12_MAX_CURRENT = 3.0
    CH3_VOLTAGES = (1.8, 2.5, 3.3, 5.0)

    def __init__(
        self,
        host: str = "10.0.0.123",
        port: int = 1026,
        timeout: float = 3.0,
        logger: Callable[[str], None] | None = None,
    ) -> None:
        self.host = host
        self.port = int(port)
        self.timeout = float(timeout)
        self.logger = logger
        self._socket: socket.socket | None = None
        self._lock = threading.RLock()
        self._rx_buffer = bytearray()
        self.identity = ""

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger(message)

    def connect(self, validate_model: bool = True) -> str:
        with self._lock:
            self.disconnect()
            try:
                sock = socket.create_connection(
                    (self.host, self.port), timeout=self.timeout
                )
                sock.settimeout(self.timeout)
                self._socket = sock
                self._rx_buffer.clear()
                identity = self.query("*IDN?")
            except Exception as exc:
                self.disconnect()
                raise GPPError(f"無法連線至 {self.host}:{self.port}: {exc}") from exc

            if validate_model and "GPP-3323" not in identity.upper():
                self.disconnect()
                raise GPPError(f"設備回覆不是 GPP-3323：{identity!r}")
            self.identity = identity
            return identity

    def disconnect(self) -> None:
        with self._lock:
            sock, self._socket = self._socket, None
            self._rx_buffer.clear()
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()

    def write(self, command: str) -> None:
        command = command.strip()
        if not command:
            raise ValueError("SCPI command cannot be empty")
        with self._lock:
            self._send(command)

    def query(self, command: str) -> str:
        command = command.strip()
        if not command:
            raise ValueError("SCPI query cannot be empty")
        with self._lock:
            self._send(command)
            response = self._readline()
            self._log(f"RX  {response}")
            return response

    def _send(self, command: str) -> None:
        if self._socket is None:
            raise GPPError("設備尚未連線")
        self._log(f"TX  {command}")
        try:
            self._socket.sendall((command + "\n").encode("ascii"))
        except (OSError, UnicodeEncodeError) as exc:
            raise GPPError(f"SCPI 傳送失敗：{exc}") from exc

    def _readline(self) -> str:
        if self._socket is None:
            raise GPPError("設備尚未連線")
        try:
            while b"\n" not in self._rx_buffer:
                block = self._socket.recv(4096)
                if not block:
                    raise GPPError("設備已關閉連線")
                self._rx_buffer.extend(block)
            raw, _, remainder = self._rx_buffer.partition(b"\n")
            self._rx_buffer = bytearray(remainder)
            return raw.rstrip(b"\r").decode("ascii", errors="replace").strip()
        except socket.timeout as exc:
            raise GPPError("等待設備回覆逾時") from exc
        except OSError as exc:
            raise GPPError(f"SCPI 接收失敗：{exc}") from exc

    @staticmethod
    def _validate_channel(channel: int) -> int:
        channel = int(channel)
        if channel not in (1, 2, 3):
            raise ValueError("GPP-3323 channel must be 1, 2, or 3")
        return channel

    def set_voltage(self, channel: int, voltage: float) -> None:
        channel = self._validate_channel(channel)
        voltage = float(voltage)
        if channel in (1, 2):
            if not 0.0 <= voltage <= self.CH12_MAX_VOLTAGE:
                raise ValueError("CH1/CH2 電壓必須介於 0 至 32 V")
        elif not any(abs(voltage - allowed) < 1e-6 for allowed in self.CH3_VOLTAGES):
            raise ValueError("CH3 電壓只能是 1.8、2.5、3.3 或 5.0 V")
        self.write(f":SOURce{channel}:VOLTage {voltage:.3f}")

    def get_voltage_setting(self, channel: int) -> float:
        channel = self._validate_channel(channel)
        return float(self.query(f":SOURce{channel}:VOLTage?"))

    def set_current(self, channel: int, current: float) -> None:
        channel = self._validate_channel(channel)
        if channel == 3:
            raise ValueError("GPP-3323 CH3 不支援 SCPI 電流設定")
        current = float(current)
        if not 0.0 <= current <= self.CH12_MAX_CURRENT:
            raise ValueError("CH1/CH2 電流必須介於 0 至 3 A")
        self.write(f":SOURce{channel}:CURRent {current:.4f}")

    def get_current_setting(self, channel: int) -> float | None:
        channel = self._validate_channel(channel)
        if channel == 3:
            return None
        return float(self.query(f":SOURce{channel}:CURRent?"))

    def set_output(self, channel: int, enabled: bool) -> None:
        channel = self._validate_channel(channel)
        self.write(f":OUTPut{channel} {'ON' if enabled else 'OFF'}")

    def get_output(self, channel: int) -> bool:
        channel = self._validate_channel(channel)
        value = self.query(f":OUTPut{channel}?" ).upper()
        return value in {"1", "ON"}

    def all_outputs_off(self) -> None:
        self.write("ALLOUTOFF")

    def measure(self, channel: int) -> Measurement:
        channel = self._validate_channel(channel)
        response = self.query(f":MEASure{channel}:ALL?")
        values = [float(x) for x in re.findall(
            r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", response
        )]
        if len(values) >= 3:
            return Measurement(values[0], values[1], values[2])

        voltage = float(self.query(f":MEASure{channel}:VOLTage?"))
        current = float(self.query(f":MEASure{channel}:CURRent?"))
        power = float(self.query(f":MEASure{channel}:POWer?"))
        return Measurement(voltage, current, power)

    def get_error(self) -> str:
        return self.query(":SYSTem:ERRor?")
