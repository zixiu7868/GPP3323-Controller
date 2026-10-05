from __future__ import annotations

from gpp3323.i18n import tr

import re
import socket
import threading
from dataclasses import dataclass
from typing import Callable


class GPPError(RuntimeError):
    """Raised when communication or validation fails."""


class ResponseTimeoutError(GPPError):
    """Raised when a complete instrument response does not arrive in time."""


class LoadVoltagePresentError(GPPError):
    """Raised when external voltage prevents a safe switch to Load Mode."""

    def __init__(self, channel: int, voltage: float) -> None:
        self.channel = channel
        self.voltage = voltage
        super().__init__(
            tr('CH{0} 端子目前偵測到 {1} V。請先斷開外部電源，確認端子無電壓後再切換 Load Mode。', f'{channel}', f'{voltage:.4g}')
        )


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
    LOAD_MODES = ("CV", "CC", "CR")
    LOAD_MIN_VOLTAGE = 1.5
    LOAD_MAX_VOLTAGE = 33.0
    LOAD_MAX_CURRENT = 3.2
    LOAD_MIN_RESISTANCE = 1.0
    LOAD_MAX_RESISTANCE = 1000.0
    LOAD_SWITCH_VOLTAGE_THRESHOLD = 0.1

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
                raise GPPError(tr('無法連線至 {0}:{1}: {2}', f'{self.host}', f'{self.port}', f'{exc}')) from exc

            if validate_model and "GPP-3323" not in identity.upper():
                self.disconnect()
                raise GPPError(tr('設備回覆不是 GPP-3323：{0}', f'{identity!r}'))
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

    def query(
        self,
        command: str,
        *,
        timeout_retries: int = 0,
        on_timeout: Callable[[int, int], None] | None = None,
    ) -> str:
        command = command.strip()
        if not command:
            raise ValueError("SCPI query cannot be empty")
        timeout_retries = int(timeout_retries)
        if timeout_retries < 0:
            raise ValueError("timeout_retries cannot be negative")
        with self._lock:
            self._send(command)
            for attempt in range(timeout_retries + 1):
                try:
                    response = self._readline()
                except ResponseTimeoutError:
                    if attempt >= timeout_retries:
                        raise
                    retry_number = attempt + 1
                    self._log(
                        f"RX  timeout; retrying wait "
                        f"({retry_number}/{timeout_retries})"
                    )
                    if on_timeout:
                        on_timeout(retry_number, timeout_retries)
                else:
                    self._log(f"RX  {response}")
                    return response
        raise AssertionError("unreachable")

    def _send(self, command: str) -> None:
        if self._socket is None:
            raise GPPError(tr('設備尚未連線'))
        self._log(f"TX  {command}")
        try:
            self._socket.sendall((command + "\n").encode("ascii"))
        except (OSError, UnicodeEncodeError) as exc:
            raise GPPError(tr('SCPI 傳送失敗：{0}', f'{exc}')) from exc

    def _readline(self) -> str:
        if self._socket is None:
            raise GPPError(tr('設備尚未連線'))
        try:
            while b"\n" not in self._rx_buffer:
                block = self._socket.recv(4096)
                if not block:
                    raise GPPError(tr('設備已關閉連線'))
                self._rx_buffer.extend(block)
            raw, _, remainder = self._rx_buffer.partition(b"\n")
            self._rx_buffer = bytearray(remainder)
            return raw.rstrip(b"\r").decode("ascii", errors="replace").strip()
        except socket.timeout as exc:
            raise ResponseTimeoutError(tr('等待設備回覆逾時')) from exc
        except OSError as exc:
            raise GPPError(tr('SCPI 接收失敗：{0}', f'{exc}')) from exc

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
                raise ValueError(tr('CH1/CH2 電壓必須介於 0 至 32 V'))
        elif not any(abs(voltage - allowed) < 1e-6 for allowed in self.CH3_VOLTAGES):
            raise ValueError(tr('CH3 電壓只能是 1.8、2.5、3.3 或 5.0 V'))
        self.write(f":SOURce{channel}:VOLTage {voltage:.3f}")

    def get_voltage_setting(self, channel: int) -> float:
        channel = self._validate_channel(channel)
        return float(self.query(f":SOURce{channel}:VOLTage?"))

    def set_current(self, channel: int, current: float) -> None:
        channel = self._validate_channel(channel)
        if channel == 3:
            raise ValueError(tr('GPP-3323 CH3 不支援 SCPI 電流設定'))
        current = float(current)
        if not 0.0 <= current <= self.CH12_MAX_CURRENT:
            raise ValueError(tr('CH1/CH2 電流必須介於 0 至 3 A'))
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

    @staticmethod
    def _validate_load_channel(channel: int) -> int:
        channel = int(channel)
        if channel not in (1, 2):
            raise ValueError(tr('GPP-3323 Load Mode 僅支援 CH1 或 CH2'))
        return channel

    @classmethod
    def _validate_load_mode(cls, mode: str) -> str:
        mode = str(mode).strip().upper()
        if mode not in cls.LOAD_MODES:
            raise ValueError(tr('Load Mode 必須是 CV、CC 或 CR'))
        return mode

    def set_load_mode(self, channel: int, mode: str, enabled: bool = True) -> None:
        channel = self._validate_load_channel(channel)
        mode = self._validate_load_mode(mode)
        self.write(f":LOAD{channel}:{mode} {'ON' if enabled else 'OFF'}")

    def get_channel_mode(self, channel: int) -> str:
        channel = self._validate_load_channel(channel)
        return self.query(f":MODE{channel}?")

    @classmethod
    def is_load_mode(cls, mode: str) -> bool:
        """Accept both short (CC) and descriptive (CC LOAD) mode replies."""
        normalized = re.sub(r"[\s_-]+", " ", str(mode).strip().upper())
        return re.fullmatch(r"(?:CV|CC|CR)(?: ?LOAD)?", normalized) is not None

    def ensure_load_inputs_off(self) -> dict[int, tuple[str, bool]]:
        """Turn off CH1/CH2 inputs only when those channels are in Load Mode."""
        result: dict[int, tuple[str, bool]] = {}
        with self._lock:
            for channel in (1, 2):
                mode = self.get_channel_mode(channel)
                if self.is_load_mode(mode) and self.get_output(channel):
                    self.set_output(channel, False)
                result[channel] = (mode, self.get_output(channel))
        return result

    def enable_load_input(self, channel: int) -> tuple[str, bool]:
        """Enable a channel only after verifying that it is in Load Mode."""
        channel = self._validate_load_channel(channel)
        with self._lock:
            mode = self.get_channel_mode(channel)
            if not self.is_load_mode(mode):
                raise GPPError(
                    tr('CH{0} 目前不是 Load Mode，請先套用 CV、CC 或 CR 設定', f'{channel}')
                )
            self.set_output(channel, True)
            return mode, self.get_output(channel)

    def set_load_voltage(self, channel: int, voltage: float) -> None:
        channel = self._validate_load_channel(channel)
        voltage = float(voltage)
        if not self.LOAD_MIN_VOLTAGE <= voltage <= self.LOAD_MAX_VOLTAGE:
            raise ValueError(tr('Load CV 電壓必須介於 1.5 至 33 V'))
        self.write(f":SOURce{channel}:VOLTage {voltage:.3f}")

    def set_load_current(self, channel: int, current: float) -> None:
        channel = self._validate_load_channel(channel)
        current = float(current)
        if not 0.0 <= current <= self.LOAD_MAX_CURRENT:
            raise ValueError(tr('Load CC 電流必須介於 0 至 3.2 A'))
        self.write(f":SOURce{channel}:CURRent {current:.4f}")

    def set_load_resistance(self, channel: int, resistance: float) -> None:
        channel = self._validate_load_channel(channel)
        resistance = float(resistance)
        if not self.LOAD_MIN_RESISTANCE <= resistance <= self.LOAD_MAX_RESISTANCE:
            raise ValueError(tr('Load CR 電阻必須介於 1 至 1000 Ω'))
        self.write(f":LOAD{channel}:RESistor {resistance:.3f}")

    def get_load_setting(self, channel: int, mode: str) -> float:
        channel = self._validate_load_channel(channel)
        mode = self._validate_load_mode(mode)
        if mode == "CV":
            return float(self.query(f":SOURce{channel}:VOLTage?"))
        if mode == "CC":
            return float(self.query(f":SOURce{channel}:CURRent?"))
        return float(self.query(f":LOAD{channel}:RESistor?"))

    def configure_load(self, channel: int, mode: str, value: float) -> str:
        """Update an active load mode in place; guard actual mode switches."""
        channel = self._validate_load_channel(channel)
        mode = self._validate_load_mode(mode)
        value = float(value)
        if mode == "CV" and not self.LOAD_MIN_VOLTAGE <= value <= self.LOAD_MAX_VOLTAGE:
            raise ValueError(tr('Load CV 電壓必須介於 1.5 至 33 V'))
        if mode == "CC" and not 0.0 <= value <= self.LOAD_MAX_CURRENT:
            raise ValueError(tr('Load CC 電流必須介於 0 至 3.2 A'))
        if mode == "CR" and not self.LOAD_MIN_RESISTANCE <= value <= self.LOAD_MAX_RESISTANCE:
            raise ValueError(tr('Load CR 電阻必須介於 1 至 1000 Ω'))
        with self._lock:
            actual_mode = self.get_channel_mode(channel)
            same_mode = self.is_load_mode(actual_mode) and actual_mode.strip().upper().startswith(mode)
            if not same_mode:
                terminal_voltage = self.measure(channel).voltage
                if abs(terminal_voltage) >= self.LOAD_SWITCH_VOLTAGE_THRESHOLD:
                    raise LoadVoltagePresentError(channel, terminal_voltage)
                self.set_load_mode(channel, mode, True)
            if mode == "CV":
                self.set_load_voltage(channel, value)
            elif mode == "CC":
                self.set_load_current(channel, value)
            else:
                self.set_load_resistance(channel, value)
            return self.get_error()

    def all_outputs_off(self) -> None:
        self.write("ALLOUTOFF")

    def measure(
        self,
        channel: int,
        *,
        timeout_retries: int = 0,
        on_timeout: Callable[[int, int], None] | None = None,
    ) -> Measurement:
        channel = self._validate_channel(channel)
        response = self.query(
            f":MEASure{channel}:ALL?",
            timeout_retries=timeout_retries,
            on_timeout=on_timeout,
        )
        values = [float(x) for x in re.findall(
            r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][-+]?\d+)?", response
        )]
        if len(values) >= 3:
            return Measurement(values[0], values[1], values[2])

        voltage = float(self.query(
            f":MEASure{channel}:VOLTage?",
            timeout_retries=timeout_retries,
            on_timeout=on_timeout,
        ))
        current = float(self.query(
            f":MEASure{channel}:CURRent?",
            timeout_retries=timeout_retries,
            on_timeout=on_timeout,
        ))
        power = float(self.query(
            f":MEASure{channel}:POWer?",
            timeout_retries=timeout_retries,
            on_timeout=on_timeout,
        ))
        return Measurement(voltage, current, power)

    def get_error(self) -> str:
        return self.query(":SYSTem:ERRor?")
