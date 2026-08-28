from __future__ import annotations

import re
import socketserver
import threading


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        while True:
            raw = self.rfile.readline()
            if not raw:
                return
            command = raw.decode("ascii", errors="replace").strip()
            response = self.server.instrument.handle(command)  # type: ignore[attr-defined]
            if response is not None:
                self.wfile.write((response + "\n").encode("ascii"))
                self.wfile.flush()


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class FakeGPP3323:
    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.voltage = {1: 0.0, 2: 0.0, 3: 3.3}
        self.current = {1: 0.0, 2: 0.0}
        self.output = {1: False, 2: False, 3: False}
        self.external_voltage = {1: 0.0, 2: 0.0, 3: 0.0}
        self.mode = {1: "INDEPENDENT", 2: "INDEPENDENT"}
        self.resistance = {1: 100.0, 2: 100.0}
        self._server = _Server((host, port), _Handler)
        self._server.instrument = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def address(self) -> tuple[str, int]:
        return self._server.server_address

    def start(self) -> "FakeGPP3323":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)

    def handle(self, command: str) -> str | None:
        upper = command.upper()
        if upper == "*IDN?":
            return "GW INSTEK,GPP-3323,SN:FAKE001,V1.26"
        if upper == ":SYSTEM:ERROR?":
            return '0,"No error"'
        if upper == "ALLOUTOFF":
            self.output = {1: False, 2: False, 3: False}
            return None

        match = re.match(r":SOURCE([123]):VOLTAGE(?:\s+(.+)|\?)$", upper)
        if match:
            channel = int(match.group(1))
            if upper.endswith("?"):
                return f"{self.voltage[channel]:.4f}"
            self.voltage[channel] = float(match.group(2))
            return None

        match = re.match(r":SOURCE([12]):CURRENT(?:\s+(.+)|\?)$", upper)
        if match:
            channel = int(match.group(1))
            if upper.endswith("?"):
                return f"{self.current[channel]:.5f}"
            self.current[channel] = float(match.group(2))
            return None

        match = re.match(r":LOAD([12]):(CV|CC|CR)\s+(ON|OFF)$", upper)
        if match:
            channel = int(match.group(1))
            self.output[channel] = False
            self.mode[channel] = (
                f"{match.group(2)} LOAD" if match.group(3) == "ON" else "INDEPENDENT"
            )
            return None

        match = re.match(r":MODE([12])\?", upper)
        if match:
            return self.mode[int(match.group(1))]

        match = re.match(r":LOAD([12]):RESISTOR(?:\s+(.+)|\?)$", upper)
        if match:
            channel = int(match.group(1))
            if upper.endswith("?"):
                return f"{self.resistance[channel]:.3f}"
            self.resistance[channel] = float(match.group(2))
            return None

        match = re.match(r":OUTPUT([123])(?:\s+(ON|OFF)|\?)$", upper)
        if match:
            channel = int(match.group(1))
            if upper.endswith("?"):
                return "1" if self.output[channel] else "0"
            self.output[channel] = match.group(2) == "ON"
            return None

        match = re.match(r":MEASURE([123]):ALL\?", upper)
        if match:
            channel = int(match.group(1))
            voltage = (
                self.voltage[channel]
                if self.output[channel] and "LOAD" not in self.mode.get(channel, "")
                else self.external_voltage[channel]
            )
            current = 0.0 if channel == 3 or not self.output[channel] else min(
                self.current[channel], voltage / 10.0
            )
            return f"{voltage:.5f},{current:.5f},{voltage * current:.5f}"
        return '-100,"Unknown command"' if upper.endswith("?") else None
