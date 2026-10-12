# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""TCP server emulating the LibreVNA-GUI 1.6.5 SCPI behaviour for integration tests and offline demos.

Emulated semantics (from upstream scpi.cpp / vna.cpp / tracewidget.cpp):
- failed commands/queries produce no output and set ESR CME (32); *ESR? reads and clears
- :VNA:TRACE:DATA? returns "[f,re,im],..." or "EMPTY"
- single sweep: :VNA:ACQ:SINGLE TRUE starts a sweep and resets live traces; FIN? TRUE once averaging settled
- only one client at a time; a new connection drops the previous one
Fault injection flags allow testing timeouts, disconnects, truncated traces, rejected commands etc.

Run standalone:  python -m antennafox.instrument.librevna.emulator --port 19542 --model dualband_24_5
"""

from __future__ import annotations

import argparse
import socket
import threading
import time
from dataclasses import dataclass, field

import numpy as np

from ..simulated.models import MODELS


@dataclass
class Faults:
    reject: set[str] = field(default_factory=set)  # uppercase command prefixes to reject (CME)
    drop_after_trigger: bool = False  # close socket right after the sweep is triggered
    hang_on: set[str] = field(default_factory=set)  # never answer these queries
    truncate_trace: bool = False
    wrong_idn: bool = False
    stale_grid_once: bool = False  # first DATA? returns the previous sweep's grid (out-of-order update)
    clamp_stop_hz: float | None = None  # emulate silent GUI clamping
    never_finish: bool = False
    device_connected: bool = True
    device_available: bool = False  # attached via USB but not yet connected in the GUI
    delay_s: float = 0.0
    garbage_line_once: bool = False
    hang_seconds: float = 1.0


class LibreVNAGUIEmulator:
    def __init__(self, host: str = "127.0.0.1", port: int = 0, model: str = "dualband_24_5",
                 sweep_seconds: float = 0.2, version: str = "1.6.5", seed: int = 7):
        self.model = MODELS[model]
        self.sweep_seconds = sweep_seconds
        self.version = version
        self.faults = Faults()
        self.rng = np.random.default_rng(seed)
        # serial prefix makes the client mark all data as simulated
        self.serial = "EMULATOR-0001"
        self.st = {
            "start": 1e9, "stop": 6e9, "points": 501, "ifbw": 1000.0, "avg": 1, "lvl": -10.0,
            "single": False, "running": False, "mode": "VNA", "sweep": "FREQUENCY",
        }
        self._sweep_end = 0.0
        self._data: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._prev: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self.esr = 0
        self.ese = 255
        self.cal_type = "SOLT_1"
        self.commands_seen: list[str] = []
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((host, port))
        self._srv.listen(2)
        self.port = self._srv.getsockname()[1]
        self._client: socket.socket | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)

    # -- lifecycle --------------------------------------------------------------------------
    def start(self) -> LibreVNAGUIEmulator:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        if self._client:
            try:
                self._client.close()
            except OSError:
                pass

    def drop_client(self) -> None:
        if self._client:
            try:
                self._client.shutdown(socket.SHUT_RDWR)
                self._client.close()
            except OSError:
                pass

    def _accept_loop(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            self.drop_client()  # only one connection at a time, like the real GUI
            self._client = conn
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                chunk = conn.recv(65536)
            except OSError:
                return
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                with self._lock:
                    outs = self._process(line.decode().strip())
                for o in outs:
                    if o is None:
                        try:
                            conn.shutdown(socket.SHUT_RDWR)
                            conn.close()
                        except OSError:
                            pass
                        return
                    if self.faults.delay_s:
                        time.sleep(self.faults.delay_s)
                    try:
                        conn.sendall((o + "\n").encode())
                    except OSError:
                        return

    # -- SCPI emulation ---------------------------------------------------------------------
    def _update_sweep(self) -> None:
        if self.st["running"] and time.monotonic() >= self._sweep_end and not self.faults.never_finish:
            f = np.linspace(self.st["start"], self.st["stop"], int(self.st["points"]))
            noise = self.model.noise / np.sqrt(self.st["avg"])
            g = self.model.gamma(f) + noise * (self.rng.standard_normal(f.shape) + 1j * self.rng.standard_normal(f.shape))
            s21 = 10 ** (-40 / 20) * np.sqrt(np.clip(1 - np.abs(self.model.gamma(f)) ** 2, 0, 1)) + 0j
            self._data = {"S11": (f, g), "S21": (f, s21), "S12": (f, s21), "S22": (f, g)}
            if self.st["single"]:
                self.st["running"] = False

    def _trigger(self) -> None:
        self.st["running"] = True
        if self._data:
            self._prev = self._data
        self._data = {}
        self._sweep_end = time.monotonic() + self.sweep_seconds * self.st["avg"]

    def _process(self, line: str) -> list[str | None]:
        outs: list[str | None] = []
        for cmd in line.split(";"):
            cmd = cmd.strip()
            if not cmd:
                continue
            self.commands_seen.append(cmd)
            r = self._one(cmd)
            if r is None:
                outs.append(None)
                return outs
            if r == "__ERR__":
                self.esr |= 32
            elif r != "":
                outs.append(r)
        return outs

    def _one(self, cmd: str) -> str | None:
        self._update_sweep()
        c = cmd.lstrip(":")
        head, _, arg = c.partition(" ")
        h = head.upper()
        arg = arg.strip()
        for rej in self.faults.reject:
            if h.startswith(rej):
                return "__ERR__"
        if h in self.faults.hang_on:
            time.sleep(self.faults.hang_seconds)  # the real GUI processes commands serially, so a stall blocks all
        if self.faults.garbage_line_once and h.endswith("?") and h != "*IDN?" and h != "*ESR?":
            self.faults.garbage_line_once = False
            return "GARBAGE\nMORE"
        is_q = h.endswith("?")
        key = h.rstrip("?")
        alias = {
            "*IDN": "IDN", "*ESR": "ESR", "*CLS": "CLS", "*LST": "LST", "*ESE": "ESE",
            "DEV:CONN": "CONN", "DEVICE:CONNECT": "CONN", "DEV:LIST": "LIST", "DEV:MODE": "MODE",
            "DEV:INF:FWREV": "FW", "DEV:INF:HWREV": "HW", "DEV:INF:TEMP": "TEMP", "DEV:REF:IN": "REFIN",
            "DEV:STA:UNLO": "UNLO", "DEV:STA:ADCO": "ADCO", "DEV:STA:UNLE": "UNLE",
            "DEV:INF:LIM:MINF": "LMINF", "DEV:INF:LIM:MAXF": "LMAXF", "DEV:INF:LIM:MAXP": "LMAXP",
            "DEV:INF:LIM:MINIFBW": "LMINIFBW", "DEV:INF:LIM:MAXIFBW": "LMAXIFBW",
            "DEV:INF:LIM:MINPOW": "LMINPOW", "DEV:INF:LIM:MAXPOW": "LMAXPOW",
            "VNA:SWEEP": "SWEEP", "VNA:SWEEPTYPE": "SWEEPTYPE",
            "VNA:FREQ:START": "start", "VNA:FREQUENCY:START": "start",
            "VNA:FREQ:STOP": "stop", "VNA:FREQUENCY:STOP": "stop",
            "VNA:ACQ:POINTS": "points", "VNA:ACQ:IFBW": "ifbw", "VNA:ACQ:AVG": "avg", "VNA:STIM:LVL": "lvl",
            "VNA:ACQ:SINGLE": "SINGLE", "VNA:ACQ:RUN": "RUN", "VNA:ACQ:STOP": "ASTOP", "VNA:ACQ:FIN": "FIN",
            "VNA:ACQ:FREQ": "AFREQ", "VNA:ACQ:AVGLEV": "AVGLEV",
            "VNA:TRAC:DATA": "DATA", "VNA:TRACE:DATA": "DATA", "VNA:TRAC:LIST": "TLIST",
            "VNA:TRAC:PARAM": "TPARAM", "VNA:TRAC:PAUSED": "TPAUSED", "VNA:TRAC:DEEMB:ACT": "TDEEMB",
            "VNA:CAL:ACTIVE": "CALACT",
        }.get(key)
        dev = self.faults.device_connected
        if alias is None:
            return "__ERR__"
        if is_q and alias in ("TEMP", "REFIN", "UNLO", "ADCO", "UNLE"):
            if not dev:
                return "__ERR__"
            return {"TEMP": "30/33/42", "REFIN": "Internal", "UNLO": "FALSE", "ADCO": "FALSE", "UNLE": "FALSE"}[alias]
        if alias == "IDN" and is_q:
            if self.faults.wrong_idn:
                return "Keysight,E5071C,1234,1.0"
            return f"LibreVNA,LibreVNA-GUI,{self.serial if dev else 'Not connected'},{self.version}"
        if alias == "ESR" and is_q:
            v, self.esr = self.esr, 0
            return str(v)
        if alias == "ESE":
            if is_q:
                return str(self.ese)
            try:
                v = int(arg)
            except ValueError:
                return "__ERR__"
            if not 0 <= v <= 255:
                return "__ERR__"
            self.ese = v
            return ""
        if alias == "CLS":
            self.esr = 0
            return ""
        if alias == "LST" and is_q:
            return "\n".join([
                "*IDN?", "*ESR?", "*CLS", "*LST?", "DEVice:CONNect?", "DEVice:CONNect", "DEVice:LIST?",
                "DEVice:MODE",
                "DEVice:INFo:FWREVision?", "DEVice:INFo:HWREVision?",
                "DEVice:INFo:LIMits:MINFrequency?", "DEVice:INFo:LIMits:MAXFrequency?",
                "DEVice:INFo:LIMits:MAXPoints?", "DEVice:INFo:LIMits:MINIFBW?", "DEVice:INFo:LIMits:MAXIFBW?",
                "DEVice:INFo:LIMits:MINPOWer?", "DEVice:INFo:LIMits:MAXPOWer?",
                "VNA:SWEEP", "VNA:SWEEPTYPE", "VNA:FREQuency:START", "VNA:FREQuency:START?", "VNA:FREQuency:STOP",
                "VNA:FREQuency:STOP?", "VNA:ACQuisition:RUN", "VNA:ACQuisition:RUN?", "VNA:ACQuisition:STOP",
                "VNA:ACQuisition:IFBW", "VNA:ACQuisition:IFBW?", "VNA:ACQuisition:POINTS",
                "VNA:ACQuisition:POINTS?", "VNA:ACQuisition:AVG", "VNA:ACQuisition:AVG?",
                "VNA:ACQuisition:FINished?", "VNA:ACQuisition:SINGLE", "VNA:ACQuisition:FREQuency?",
                "VNA:STIMulus:LVL", "VNA:STIMulus:LVL?", "VNA:TRACe:LIST?", "VNA:TRACe:DATA?",
                "VNA:TRACe:PARAMeter?", "VNA:TRACe:PAUSED?", "VNA:TRACe:DEEMBedding:ACTive?",
                "VNA:CALibration:ACTIVE?",
            ])
        if alias == "CONN":
            if is_q:
                return self.serial if dev else "Not connected"
            if self.faults.device_available:
                self.faults.device_connected = True
                return ""
            return "__ERR__"
        if alias == "LIST" and is_q:
            return self.serial if self.faults.device_available else ""
        if alias == "MODE":
            if is_q:
                return self.st["mode"]
            self.st["mode"] = arg.upper()
            return ""
        if alias in ("FW", "HW") or alias.startswith("L"):
            if not dev:
                return "__ERR__" if alias in ("FW", "HW") else "1e+11"
            # values as reported by a LibreVNA V1 Rev.B with firmware 1.6.5
            return {"FW": "1.6.5", "HW": "1 Rev.B", "LMINF": "0", "LMAXF": "6e+09", "LMAXP": "4501",
                    "LMINIFBW": "6", "LMAXIFBW": "50000", "LMINPOW": "-40", "LMAXPOW": "0"}[alias]
        if alias in ("SWEEP", "SWEEPTYPE"):
            return "FREQUENCY" if is_q else ""
        if alias in ("start", "stop", "points", "ifbw", "avg", "lvl"):
            if is_q:
                v = self.st[alias]
                return f"{v:.0f}" if alias in ("start", "stop") else f"{v:g}"
            try:
                v = float(arg)
            except ValueError:
                return "__ERR__"
            if alias == "stop" and self.faults.clamp_stop_hz is not None:
                v = min(v, self.faults.clamp_stop_hz)
            if alias in ("start", "stop"):
                v = min(max(v, 100e3), 6e9)
            self.st[alias] = v
            if alias == "start" and self.st["stop"] < v:
                self.st["stop"] = v
            if alias == "stop" and self.st["start"] > v:
                self.st["start"] = v
            if self.st["running"]:
                self._trigger()
            return ""
        if alias == "SINGLE":
            if is_q:
                return "TRUE" if self.st["single"] else "FALSE"
            self.st["single"] = arg.upper() in ("TRUE", "1", "ON")
            if self.st["single"]:
                self._trigger()
                if self.faults.drop_after_trigger:
                    return None
            return ""
        if alias == "RUN":
            if is_q:
                return "TRUE" if self.st["running"] else "FALSE"
            self._trigger()
            return ""
        if alias == "ASTOP":
            self.st["running"] = False
            return ""
        if alias == "FIN" and is_q:
            return "TRUE" if (self._data and not self.st["running"]) or (self._data and not self.st["single"]) else "FALSE"
        if alias == "AFREQ" and is_q:
            return f"{self.st['start']:.0f}"
        if alias == "AVGLEV" and is_q:
            return str(int(self.st["avg"]) if self._data else 0)
        if alias == "TLIST" and is_q:
            return "S11,S12,S21,S22"
        if alias == "TPARAM" and is_q:
            return arg.upper()
        if alias == "TPAUSED" and is_q:
            return "FALSE"
        if alias == "TDEEMB" and is_q:
            return "FALSE"
        if alias == "CALACT" and is_q:
            return self.cal_type
        if alias == "DATA" and is_q:
            name = arg.upper()
            src = self._data
            if self.faults.stale_grid_once and self._prev:
                self.faults.stale_grid_once = False
                src = self._prev
            if name not in ("S11", "S12", "S21", "S22"):
                return "__ERR__"
            if name not in src:
                return "EMPTY"
            f, d = src[name]
            s = ",".join(f"[{fi:.0f},{v.real:g},{v.imag:g}]" for fi, v in zip(f, d, strict=True))
            if self.faults.truncate_trace:
                s = s[: len(s) // 2]
            return s
        return "__ERR__"


def main() -> None:
    ap = argparse.ArgumentParser(description="Emulated LibreVNA-GUI SCPI server (simulation – not a real device)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=19542)
    ap.add_argument("--model", default="dualband_24_5", choices=sorted(MODELS))
    a = ap.parse_args()
    srv = LibreVNAGUIEmulator(a.host, a.port, a.model).start()
    print(f"Emulated LibreVNA-GUI SCPI server on {a.host}:{srv.port} (model {a.model}). Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.stop()


if __name__ == "__main__":
    main()
