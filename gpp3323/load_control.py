"""Verified load operations for one or both channels."""
from dataclasses import dataclass, field
import math
import re

from gpp3323.instrument import GPP3323Client, LoadVoltagePresentError, Measurement
from gpp3323.i18n import tr


@dataclass(frozen=True)
class LoadState:
    mode: str
    value: float | None
    enabled: bool
    measurement: Measurement | None = None


@dataclass
class LoadResult:
    states: dict[int, LoadState] = field(default_factory=dict)
    errors: dict[int, str] = field(default_factory=dict)
    completed: set[int] = field(default_factory=set)
    rollback: dict[int, str] = field(default_factory=dict)


def validate_setting(mode: str, value: float) -> float:
    limits = {'CC': (0, 3.2), 'CV': (1.5, 33), 'CR': (1, 1000)}
    if mode not in limits:
        raise ValueError(tr('Load Mode 必須是 CV、CC 或 CR'))
    low, high = limits[mode]
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(tr('設定超出範圍：{0}，允許 {1} 至 {2}', mode, low, high))
    return value


def read_state(client: GPP3323Client, channel: int, measure=False) -> LoadState:
    with client._lock:
        mode = client.get_channel_mode(channel)
        value = client.get_load_setting(channel, mode.strip().upper()[:2]) if client.is_load_mode(mode) else None
        return LoadState(mode, value, client.get_output(channel), client.measure(channel) if measure else None)


def _capture(client, channels, result, measure=False):
    for channel in channels:
        try:
            result.states[channel] = read_state(client, channel, measure)
        except Exception as exc:
            result.errors.setdefault(channel, str(exc))


def _check_error(error: str):
    if not re.match(r'^\s*[+]?0\s*(?:,|$)', error):
        raise RuntimeError(error)


def apply_settings(client: GPP3323Client, settings: dict[int, tuple[str, float]]) -> LoadResult:
    """Preflight every target before writing; report partial application explicitly."""
    result = LoadResult()
    with client._lock:
        for channel, (mode, value) in settings.items():
            try:
                client._validate_load_channel(channel)
                validate_setting(mode, value)
                actual = client.get_channel_mode(channel)
                same = client.is_load_mode(actual) and actual.strip().upper().startswith(mode)
                if not same:
                    voltage = client.measure(channel).voltage
                    if abs(voltage) >= client.LOAD_SWITCH_VOLTAGE_THRESHOLD:
                        raise LoadVoltagePresentError(channel, voltage)
            except Exception as exc:
                result.errors[channel] = str(exc)
        if not result.errors:
            for channel, (mode, value) in settings.items():
                try:
                    _check_error(client.configure_load(channel, mode, value))
                    state = read_state(client, channel)
                    # Match the command's representable precision, not arbitrary input digits.
                    places = {'CC': 4, 'CV': 3, 'CR': 3}[mode]
                    expected = float(f'{value:.{places}f}')
                    if (not client.is_load_mode(state.mode) or not state.mode.strip().upper().startswith(mode)
                            or state.value is None or not math.isclose(state.value, expected, abs_tol=10 ** -places / 2)):
                        raise RuntimeError(tr('讀回設定與要求不符'))
                    result.states[channel] = state
                    result.completed.add(channel)
                except Exception as exc:
                    result.errors[channel] = str(exc)
                    break
        _capture(client, settings, result)
    return result


def set_inputs(client: GPP3323Client, channels: tuple[int, ...], enabled: bool) -> LoadResult:
    """Verified ON with rollback of newly enabled inputs; OFF attempts every target."""
    result = LoadResult()
    with client._lock:
        if enabled:
            before = {}
            for channel in channels:
                try:
                    state = read_state(client, channel)
                    if not client.is_load_mode(state.mode):
                        raise RuntimeError(tr('CH{0} 目前不是 Load Mode，請先套用 CV、CC 或 CR 設定', channel))
                    before[channel] = state.enabled
                except Exception as exc:
                    result.errors[channel] = str(exc)
            attempted = []
            if not result.errors:
                for channel in channels:
                    if before[channel]:
                        result.completed.add(channel)
                        continue
                    # Include a failed ON attempt: the command may have reached the device.
                    attempted.append(channel)
                    try:
                        _, actual = client.enable_load_input(channel)
                        _check_error(client.get_error())
                        if not actual:
                            raise RuntimeError(tr('無法確認 Load Input 已啟用'))
                        result.completed.add(channel)
                    except Exception as exc:
                        result.errors[channel] = str(exc)
                        break
                if result.errors:
                    for channel in reversed(attempted):
                        try:
                            client.set_output(channel, False)
                            if client.get_output(channel):
                                raise RuntimeError(tr('無法確認負載已關閉'))
                            result.rollback[channel] = tr('已回復 OFF')
                            result.completed.discard(channel)
                        except Exception as exc:
                            result.rollback[channel] = tr('回復 OFF 失敗：{0}', str(exc))
                            result.errors.setdefault(channel, result.rollback[channel])
        else:
            for channel in channels:
                try:
                    mode = client.get_channel_mode(channel)
                    if client.is_load_mode(mode):
                        client.set_output(channel, False)
                        _check_error(client.get_error())
                        if client.get_output(channel):
                            raise RuntimeError(tr('無法確認負載已關閉'))
                    elif client.get_output(channel):
                        raise RuntimeError(tr('此通道為電源模式，請在 Channel 設定關閉輸出'))
                    result.completed.add(channel)
                except Exception as exc:
                    result.errors[channel] = str(exc)
        _capture(client, channels, result)
    return result
