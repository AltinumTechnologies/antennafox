# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Line-based SCPI transport for LibreVNA-GUI with explicit resynchronisation.

LibreVNA-GUI (verified against 1.6.5 sources and binary) emits *no* response line for a failed query; it only
sets the CME bit (32) in *ESR?. A naive "send query, read one line" client therefore desynchronises after any
error. Every query here is followed by a "*IDN?" sentinel whose reply ("LibreVNA,LibreVNA-GUI,...") cannot be
confused with any data response: all lines before the sentinel belong to the query, none means it was rejected.
"""

from __future__ import annotations

import logging
import random
import re
import socket
import time

from ..base import CommandRejected, ConnectionFailed, InstrumentTimeout, ProtocolError

SENTINEL_QUERY = "*IDN?"
SENTINEL_PREFIX = "LibreVNA,LibreVNA-GUI"

ESR_CME = 0x20
ESR_EXE = 0x10
ESR_DDE = 0x08
ESR_QYE = 0x04

log = logging.getLogger("antennafox.scpi")
LOG_LINE_LIMIT = 160


def _short(line: str) -> str:
    return line if len(line) <= LOG_LINE_LIMIT else f"{line[:LOG_LINE_LIMIT]}… ({len(line)} chars)"


class SCPITransport:
    def __init__(self, host: str = "127.0.0.1", port: int = 19542, timeout: float = 3.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._buf = bytearray()

    # -- connection -------------------------------------------------------------------------
    def open(self) -> None:
        self.close()
        try:
            s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        except (ConnectionRefusedError, TimeoutError) as e:
            # Windows drops connections to closed local ports silently, so "nothing listening" shows up as a timeout
            why = "timed out" if isinstance(e, TimeoutError) else "refused"
            raise ConnectionFailed(
                f"Cannot connect to {self.host}:{self.port} ({why}). Is LibreVNA-GUI running with the SCPI server "
                "enabled (Window → Preferences → General → SCPI Server)?"
            ) from e
        except OSError as e:
            raise ConnectionFailed(f"Connection to {self.host}:{self.port} failed: {e}") from e
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = s
        self._buf.clear()
        log.info("connected to %s:%s", self.host, self.port)

    def close(self) -> None:
        if self._sock is not None:
            log.info("connection closed")
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self._buf.clear()

    @property
    def is_open(self) -> bool:
        return self._sock is not None

    # -- low level --------------------------------------------------------------------------
    def _send(self, line: str) -> None:
        if self._sock is None:
            raise ConnectionFailed("Not connected to LibreVNA-GUI.")
        if "\n" in line:
            raise ValueError("SCPI command must not contain newlines")
        log.debug(">> %s", line)
        try:
            self._sock.sendall((line + "\n").encode("ascii"))
        except OSError as e:
            log.warning("send failed: %s", e)
            self.close()
            raise ConnectionFailed(f"Connection to LibreVNA-GUI lost: {e}") from e

    def _readline(self, deadline: float) -> str:
        assert self._sock is not None
        while True:
            idx = self._buf.find(b"\n")
            if idx >= 0:
                line = bytes(self._buf[:idx]).decode("utf-8", errors="replace").rstrip("\r")
                del self._buf[: idx + 1]
                log.debug("<< %s", _short(line))
                return line
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                log.warning("timeout waiting for a response")
                raise InstrumentTimeout("Timed out waiting for LibreVNA-GUI.")
            self._sock.settimeout(min(remaining, 0.5))
            try:
                chunk = self._sock.recv(1 << 20)
            except TimeoutError:
                continue
            except OSError as e:
                log.warning("receive failed: %s", e)
                self.close()
                raise ConnectionFailed(f"Connection to LibreVNA-GUI lost: {e}") from e
            if not chunk:
                log.warning("connection closed by LibreVNA-GUI")
                self.close()
                raise ConnectionFailed(
                    "LibreVNA-GUI closed the connection (another SCPI client may have connected)."
                )
            self._buf.extend(chunk)

    def resync(self, max_s: float = 6.0) -> None:
        """Discard late responses until a unique two-value *ESE handshake comes back in order.

        A query that timed out may still be answered later; a fixed sentinel cannot tell that late reply apart
        from the current one. Two random *ESE values read back consecutively can only originate from this
        handshake. If the GUI stays unresponsive the connection is closed instead of risking misaligned data.
        """
        if self._sock is None:
            return
        log.info("resynchronizing after an incomplete exchange")
        a, b = random.sample(range(1, 255), 2)
        for cmd in (f"*ESE {a}", "*ESE?", f"*ESE {b}", "*ESE?", "*ESE 255", "*CLS"):
            self._send(cmd)
        deadline = time.monotonic() + max_s
        prev = None
        try:
            while True:
                line = self._readline(deadline)
                if prev == str(a) and line == str(b):
                    return
                prev = line
        except InstrumentTimeout:
            log.error("resynchronization failed; closing the connection")
            self.close()
            raise ConnectionFailed(
                "LibreVNA-GUI is not responding; the connection was closed to avoid misaligned data. Reconnect."
            ) from None

    # -- public API -------------------------------------------------------------------------
    def query_lines(self, query: str, timeout: float | None = None) -> list[str]:
        """Raw query. Returns all response lines (empty list if the GUI rejected the query)."""
        deadline = time.monotonic() + (timeout or self.timeout)
        self._send(query)
        self._send(SENTINEL_QUERY)
        lines: list[str] = []
        try:
            while True:
                line = self._readline(deadline)
                if line.startswith(SENTINEL_PREFIX):
                    return lines
                lines.append(line)
                if len(lines) > 10000:
                    raise ProtocolError("Unexpectedly long response – resynchronizing the connection.")
        except (InstrumentTimeout, ProtocolError):
            if self._sock is not None:
                self.resync()
            raise

    def identify(self, timeout: float | None = None) -> str:
        """Plain *IDN? (cannot use the sentinel scheme for the sentinel itself)."""
        deadline = time.monotonic() + (timeout or self.timeout)
        self._send(SENTINEL_QUERY)
        try:
            line = self._readline(deadline)
        except InstrumentTimeout:
            self.resync()
            raise
        if not line.startswith(SENTINEL_PREFIX):
            raise ProtocolError(f"Unexpected identification {line!r} – is this really LibreVNA-GUI?")
        return line

    def query(self, query: str, timeout: float | None = None) -> str:
        lines = self.query_lines(query, timeout)
        if not lines:
            raise CommandRejected(f"LibreVNA-GUI rejected or does not support the query '{query}'.")
        if len(lines) > 1:
            raise ProtocolError(f"Unexpected multi-line response to '{query}'.")
        return lines[0]

    def try_query(self, query: str, timeout: float | None = None) -> str | None:
        try:
            return self.query(query, timeout)
        except CommandRejected:
            return None

    def esr(self, timeout: float | None = None) -> int:
        lines = self.query_lines("*ESR?", timeout)
        if len(lines) != 1 or not re.fullmatch(r"\d+", lines[0]):
            raise ProtocolError(f"Invalid response to *ESR?: {lines!r}")
        v = int(lines[0])
        if not 0 <= v <= 255:
            raise ProtocolError(f"*ESR? out of range 0–255: {v}")
        return v

    def command(self, cmd: str, timeout: float | None = None) -> None:
        """Send a setting/event and verify via *ESR? that the GUI accepted it."""
        self._send("*CLS")  # a rejected optional query earlier leaves CME set; only this command may be judged
        self._send(cmd)
        status = self.esr(timeout)
        if status & ESR_CME:
            log.warning("command rejected (CME): %s", cmd)
            raise CommandRejected(f"LibreVNA-GUI rejected the command '{cmd}' (command error).")
        if status & ESR_EXE:
            raise CommandRejected(f"Execution error for '{cmd}'.")
        if status & (ESR_DDE | ESR_QYE):
            raise CommandRejected(f"Device error for '{cmd}' (ESR={status}).")
