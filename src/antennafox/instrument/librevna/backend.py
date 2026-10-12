# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""LibreVNA adapter: talks to LibreVNA-GUI's SCPI/TCP server (never to the USB device directly) and translates
its command set and trace format into the shared InstrumentInfo / TraceSegment model.

Acquisition sequence (chosen from the 1.6.5 VNA::SetSingleSweep/ConfigureDevice/SettingsChanged logic, where
settings changed while running are applied asynchronously via a timer):
  1. :VNA:ACQ:STOP           – settings are then applied synchronously, no stale reconfiguration in flight
  2. set sweep, read every value back, refuse if the GUI clamped anything
  3. :VNA:ACQ:SINGLE TRUE    – triggers exactly one (averaged) sweep and resets the live traces
  4. poll :VNA:ACQ:FIN? until TRUE and :VNA:ACQ:RUN? is FALSE
  5. read traces, verify point count and frequency grid against the requested sweep
"""

from __future__ import annotations

import logging
import math
import time

import numpy as np

from ...measurement.model import InstrumentInfo, InstrumentLimits, TraceSegment, utc_now_iso
from ..base import (
    DEVICE_PROFILES,
    AcquisitionParams,
    CalibrationOwner,
    CancelToken,
    CommandRejected,
    DeviceCapabilities,
    DeviceNotConnected,
    InstrumentTimeout,
    ProgressCallback,
    ProtocolError,
    check_in_limits,
    estimate_sweep_seconds,
)
from .scpi_transport import SCPITransport

log = logging.getLogger("antennafox.instrument")

ADAPTER_ID = "librevna-scpi"
DEVICE_NAME = "LibreVNA"
HOST_SOFTWARE = "LibreVNA-GUI"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 19542
TESTED_GUI_VERSIONS = ("1.6.5",)
# Full two-port VNA; the GUI exposes all four parameters as default traces and the calibration lives in the GUI.
CAPABILITIES = DeviceCapabilities(ports=2, parameters=("S11", "S12", "S21", "S22"),
                                  calibration=CalibrationOwner.HOST_SOFTWARE)
EMULATOR_SERIAL_PREFIX = "EMULATOR"
REQUIRED_COMMANDS = (
    "VNA:ACQuisition:SINGLE", "VNA:ACQuisition:FINished?", "VNA:TRACe:DATA?", "VNA:FREQuency:START",
    "VNA:FREQuency:STOP", "VNA:ACQuisition:POINTS", "VNA:ACQuisition:STOP",
)


def parse_trace_data(text: str) -> tuple[np.ndarray, np.ndarray]:
    """Parse '[f,re,im],[f,re,im],...' as returned by :VNA:TRACE:DATA? (see the LibreVNA programming guide)."""
    text = text.strip()
    if text == "EMPTY":
        return np.array([]), np.array([], dtype=complex)
    if not text.startswith("[") or not text.endswith("]"):
        raise ProtocolError("Trace data incomplete (missing brackets) – possibly truncated.")
    values = text.replace("[", "").replace("]", "").split(",")
    if len(values) % 3:
        raise ProtocolError("Trace data: value count is not a multiple of three (truncated?).")
    try:
        arr = np.array([float(v) for v in values], dtype=float).reshape(-1, 3)
    except ValueError as e:
        raise ProtocolError(f"Trace data not numeric: {e}") from e
    return arr[:, 0], arr[:, 1] + 1j * arr[:, 2]


def parse_idn(idn: str) -> dict:
    parts = [p.strip() for p in idn.split(",")]
    if len(parts) < 4 or parts[0] != "LibreVNA" or parts[1] != "LibreVNA-GUI":
        raise ProtocolError(f"Unexpected identification {idn!r} – is this really LibreVNA-GUI?")
    return {"serial": parts[2], "gui_version": parts[3]}


class LibreVNABackend:
    simulated = False

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, timeout: float = 3.0,
                 poll_interval: float = 0.1, fallback_profile: str = "librevna_v1"):
        self.transport = SCPITransport(host, port, timeout)
        self.poll_interval = poll_interval
        self.fallback_profile = fallback_profile
        self._info = InstrumentInfo(backend=ADAPTER_ID, model=DEVICE_NAME, host_software=HOST_SOFTWARE, host=host,
                                    port=port)
        self.supported_commands: set[str] = set()
        self.warnings: list[str] = []

    # -- connection -------------------------------------------------------------------------
    def connect(self) -> InstrumentInfo:
        t = self.transport
        t.open()
        try:
            idn = t.identify()
            t.resync()
            ids = parse_idn(idn)
            self.warnings = []
            log.info("LibreVNA-GUI %s identified: %s", ids["gui_version"], idn)
            if ids["gui_version"] not in TESTED_GUI_VERSIONS:
                log.warning("untested LibreVNA-GUI version %s", ids["gui_version"])
                self.warnings.append(
                    f"LibreVNA-GUI {ids['gui_version']} has not been tested (tested: "
                    f"{', '.join(TESTED_GUI_VERSIONS)}). Commands are verified individually."
                )
            self._load_command_list()
            missing = [c for c in REQUIRED_COMMANDS if self.supported_commands and c.upper() not in self.supported_commands]
            if missing:
                raise CommandRejected("LibreVNA-GUI does not support required commands: " + ", ".join(missing))
            self._info = InstrumentInfo(
                backend=ADAPTER_ID, idn=idn, model=DEVICE_NAME, host_software=HOST_SOFTWARE,
                gui_version=ids["gui_version"], serial=ids["serial"], host=t.host, port=t.port,
            )
            self.attach_device()
            self.refresh_device()
        except Exception:
            t.close()
            raise
        return self._info

    def _load_command_list(self) -> None:
        try:
            lines = self.transport.query_lines("*LST?", timeout=5.0)
        except (InstrumentTimeout, ProtocolError):
            lines = []
        self.supported_commands = {ln.strip().upper() for ln in lines if ln.strip()}

    def supports(self, cmd: str) -> bool:
        return not self.supported_commands or cmd.upper() in self.supported_commands

    def attach_device(self, wait_s: float = 8.0) -> bool:
        """If the GUI runs without a device but one is available, ask the GUI to connect it (as the official
        examples do with :DEV:CONN). Returns True if a device is connected afterwards."""
        t = self.transport
        if t.query(":DEV:CONN?").strip() != "Not connected":
            return True
        devices = [d.strip() for d in t.query_lines(":DEV:LIST?") if d.strip()] if self.supports("DEVice:LIST?") else []
        if not devices:
            log.info("no LibreVNA device listed by the GUI")
            return False
        log.info("asking LibreVNA-GUI to connect device %s", devices[0])
        t.command(f":DEV:CONN {devices[0]}", timeout=15.0)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if t.query(":DEV:CONN?").strip() != "Not connected":
                return True
            time.sleep(0.25)
        return False

    def refresh_device(self) -> InstrumentInfo:
        """Re-read device identity and limits. Device-reported limits are only trusted with a device attached."""
        t = self.transport
        dev = t.query(":DEV:CONN?")
        info = self._info
        if dev.strip() == "Not connected":
            info.serial = ""
            info.firmware = ""
            info.hardware_revision = ""
            info.limits = None
            return info
        info.serial = dev.strip()
        info.simulated = info.serial.startswith(EMULATOR_SERIAL_PREFIX)
        info.firmware = t.try_query(":DEV:INF:FWREV?") or ""
        info.hardware_revision = t.try_query(":DEV:INF:HWREV?") or ""
        info.limits = self._read_limits()
        log.info("device %s, firmware %s, hardware %s, limits %s", info.serial, info.firmware,
                 info.hardware_revision, info.limits)
        return info

    def _read_limits(self) -> InstrumentLimits:
        t = self.transport
        fb = DEVICE_PROFILES[self.fallback_profile]

        def num(q: str) -> float | None:
            v = t.try_query(q)
            try:
                x = float(v) if v is not None else None
            except ValueError:
                return None
            return x if x is not None and math.isfinite(x) else None

        raw = [num(q) for q in (":DEV:INF:LIM:MINF?", ":DEV:INF:LIM:MAXF?", ":DEV:INF:LIM:MAXP?",
                                ":DEV:INF:LIM:MINIFBW?", ":DEV:INF:LIM:MAXIFBW?", ":DEV:INF:LIM:MINPOW?",
                                ":DEV:INF:LIM:MAXPOW?")]
        vals = [v for v in raw if v is not None]
        if len(vals) != len(raw) or not (0 <= vals[0] < vals[1] <= 20e9):
            log.warning("implausible device limits %s; using profile %s", raw, fb.source)
            self.warnings.append("Instrument limits could not be read plausibly – using " + fb.source)
            return fb
        min_f, max_f, max_points, min_ifbw, max_ifbw, min_pow, max_pow = vals
        if min_f < fb.min_freq_hz:
            # LibreVNA V1 firmware 1.6.5 reports 0 Hz although the documented range starts at 100 kHz
            min_f = fb.min_freq_hz
        return InstrumentLimits(
            min_freq_hz=min_f, max_freq_hz=max_f, max_points=int(max_points), min_ifbw_hz=min_ifbw,
            max_ifbw_hz=max_ifbw, min_power_dbm=min_pow, max_power_dbm=max_pow, source="device",
        )

    def disconnect(self) -> None:
        self.transport.close()

    def is_connected(self) -> bool:
        return self.transport.is_open

    def info(self) -> InstrumentInfo:
        return self._info

    def capabilities(self) -> DeviceCapabilities:
        return CAPABILITIES

    def calibration_type(self) -> str | None:
        return self.transport.try_query(":VNA:CAL:ACTIVE?")

    def ping(self) -> float:
        t0 = time.monotonic()
        self.transport.identify()
        return time.monotonic() - t0

    def device_status(self) -> dict[str, str]:
        t = self.transport
        out: dict[str, str] = {}
        temps = t.try_query(":DEV:INF:TEMP?")
        if temps:
            parts = temps.split("/")
            names = ("Source", "LO", "MCU")
            out["Temperatures"] = " · ".join(f"{n} {v} °C" for n, v in zip(names, parts, strict=False))
        flags = {"PLL unlocked": ":DEV:STA:UNLO?", "ADC overload": ":DEV:STA:ADCO?", "Source unlevelled": ":DEV:STA:UNLE?"}
        states = {k: t.try_query(q) for k, q in flags.items()}
        if any(v is not None for v in states.values()):
            problems = [k for k, v in states.items() if v and v.strip().upper() in ("TRUE", "1")]
            out["Status flags"] = ", ".join(problems) if problems else "all clear"
        ref = t.try_query(":DEV:REF:IN?")
        if ref:
            out["Reference clock"] = ref
        cal = t.try_query(":VNA:CAL:ACTIVE?")
        if cal:
            out["Active calibration (GUI)"] = cal
        for label, q, unit in (("IF bandwidth range", (":DEV:INF:LIM:MINIFBW?", ":DEV:INF:LIM:MAXIFBW?"), "Hz"),
                               ("Maximum points", (":DEV:INF:LIM:MAXP?",), "")):
            vals = [v for v in (t.try_query(x) for x in q) if v]
            if len(vals) == len(q):
                out[label] = ("–".join(vals) + f" {unit}").strip()
        return out

    # -- acquisition ------------------------------------------------------------------------
    def _set_and_verify(self, cmd: str, query: str, expected: float, tol: float, what: str) -> None:
        self.transport.command(f"{cmd} {expected:.0f}" if float(expected).is_integer() else f"{cmd} {expected:g}")
        got_s = self.transport.query(query)
        try:
            got = float(got_s)
        except ValueError as e:
            raise ProtocolError(f"Invalid readback for {what}: {got_s!r}") from e
        if abs(got - expected) > tol:
            log.warning("%s read back as %g after setting %g", what, got, expected)
            raise CommandRejected(
                f"LibreVNA-GUI set {what} to {got:g} instead of {expected:g} (instrument limit?). Measurement aborted "
                "to avoid storing data at wrong settings."
            )

    def _check_trace_ready(self, name: str) -> dict:
        t = self.transport
        prov: dict = {"trace": name}
        if self.supports("VNA:TRACe:PARAMeter?"):
            param = t.try_query(f":VNA:TRAC:PARAM? {name}")
            if param is not None and param.strip().upper() != name.upper():
                raise ProtocolError(
                    f"Trace '{name}' in LibreVNA-GUI measures {param} instead of {name}. Check the trace settings."
                )
        if self.supports("VNA:TRACe:PAUSED?") and (t.try_query(f":VNA:TRAC:PAUSED? {name}") or "").upper() == "TRUE":
            raise ProtocolError(f"Trace '{name}' is paused (frozen) in LibreVNA-GUI – data would be stale.")
        if self.supports("VNA:TRACe:DEEMBedding:ACTive?"):
            prov["deembedding_active"] = (t.try_query(f":VNA:TRAC:DEEMB:ACT? {name}") or "").upper() == "TRUE"
        return prov

    def acquire(self, params: AcquisitionParams, cancel: CancelToken,
                progress: ProgressCallback | None = None) -> list[TraceSegment]:
        t = self.transport
        if not t.is_open:
            raise DeviceNotConnected("Not connected to LibreVNA-GUI.")
        info = self.refresh_device()
        if not info.serial:
            raise DeviceNotConnected(
                "LibreVNA-GUI is running but no LibreVNA device is attached (:DEV:CONN? = 'Not connected')."
            )
        assert info.limits is not None
        check_in_limits(params, info.limits, CAPABILITIES)
        cancel.check()
        log.info("acquire %s: %.0f–%.0f Hz, %d points, IFBW %g Hz, avg %d, %g dBm", "/".join(params.parameters),
                 params.start_hz, params.stop_hz, params.points, params.ifbw_hz, params.averages, params.power_dbm)
        if progress:
            progress(0.0, "Configuring sweep …")

        t.command(":DEV:MODE VNA")
        t.command(":VNA:ACQ:STOP")
        t.command(":VNA:SWEEP FREQUENCY")
        if self.supports("VNA:SWEEPTYPE"):
            t.command(":VNA:SWEEPTYPE LIN")
        self._set_and_verify(":VNA:STIM:LVL", ":VNA:STIM:LVL?", params.power_dbm, 0.05, "output power")
        self._set_and_verify(":VNA:ACQ:IFBW", ":VNA:ACQ:IFBW?", params.ifbw_hz, 0.5, "IF bandwidth")
        self._set_and_verify(":VNA:ACQ:AVG", ":VNA:ACQ:AVG?", params.averages, 0, "averages")
        self._set_and_verify(":VNA:ACQ:POINTS", ":VNA:ACQ:POINTS?", params.points, 0, "point count")
        # order avoids the GUI auto-adjusting the other edge when the new span does not overlap the old one
        cur_start = float(t.query(":VNA:FREQ:START?"))
        if params.start_hz > cur_start:
            self._set_and_verify(":VNA:FREQ:STOP", ":VNA:FREQ:STOP?", params.stop_hz, 1, "stop frequency")
            self._set_and_verify(":VNA:FREQ:START", ":VNA:FREQ:START?", params.start_hz, 1, "start frequency")
        else:
            self._set_and_verify(":VNA:FREQ:START", ":VNA:FREQ:START?", params.start_hz, 1, "start frequency")
            self._set_and_verify(":VNA:FREQ:STOP", ":VNA:FREQ:STOP?", params.stop_hz, 1, "stop frequency")
        # re-check start after stop was set (GUI keeps start<stop by moving the other edge)
        if abs(float(t.query(":VNA:FREQ:START?")) - params.start_hz) > 1:
            raise CommandRejected("LibreVNA-GUI changed the start frequency.")

        trace_prov = {name: self._check_trace_ready(name) for name in params.parameters}
        cal_type = self.calibration_type()

        cancel.check()
        t.command(":VNA:ACQ:SINGLE TRUE")
        expected = estimate_sweep_seconds(params)
        deadline = time.monotonic() + 15.0 + 4.0 * expected
        t_start = time.monotonic()
        saw_running = False
        try:
            while True:
                cancel.wait(self.poll_interval)
                fin = t.query(":VNA:ACQ:FIN?").strip().upper()
                running = t.query(":VNA:ACQ:RUN?").strip().upper() == "TRUE"
                if fin == "FALSE" or running:
                    saw_running = True
                if fin == "TRUE" and not running and (saw_running or time.monotonic() - t_start > 1.0):
                    break
                if fin not in ("TRUE", "FALSE"):
                    raise ProtocolError(f"Unexpected response to :VNA:ACQ:FIN?: {fin!r}")
                if progress:
                    frac = min(0.95, (time.monotonic() - t_start) / max(expected, 0.2))
                    cur = t.try_query(":VNA:ACQ:FREQ?")
                    msg = "Sweeping …"
                    if cur:
                        try:
                            msg = f"Sweeping … {float(cur) / 1e6:.1f} MHz"
                        except ValueError:
                            pass
                    progress(frac, msg)
                if time.monotonic() > deadline:
                    raise InstrumentTimeout(
                        f"Sweep did not finish within {deadline - t_start:.0f} s. Is the device attached "
                        "and acquiring in LibreVNA-GUI?"
                    )
        except BaseException as e:
            log.info("sweep aborted (%s); stopping acquisition", type(e).__name__)
            try:
                t.command(":VNA:ACQ:STOP")
            except Exception:
                pass
            raise

        acquired_at = utc_now_iso()
        expected_f = np.linspace(params.start_hz, params.stop_hz, params.points)
        out: list[TraceSegment] = []
        for name in params.parameters:
            raw = t.query(f":VNA:TRAC:DATA? {name}", timeout=max(10.0, params.points * 0.002))
            f, d = parse_trace_data(raw)
            if len(f) != params.points:
                raise ProtocolError(
                    f"Trace {name}: {len(f)} instead of {params.points} points – sweep incomplete or changed while "
                    "reading. Please measure again."
                )
            if np.max(np.abs(f - expected_f)) > max(2.0, 1e-9 * params.stop_hz):
                raise ProtocolError(f"Trace {name}: frequency grid does not match the requested sweep (stale data?).")
            if not np.all(np.isfinite(d)):
                raise ProtocolError(f"Trace {name}: non-finite values.")
            prov = {
                **trace_prov[name], "ifbw_hz": params.ifbw_hz, "averages": params.averages,
                "power_dbm": params.power_dbm, "points": params.points, "cal_type": cal_type,
                "serial": info.serial, "gui_version": info.gui_version, "firmware": info.firmware,
                "sweep_seconds": round(time.monotonic() - t_start, 2),
            }
            out.append(TraceSegment(parameter=name, freq_hz=f, data=d, acquired_at=acquired_at,
                                    source="simulation" if info.simulated else ADAPTER_ID,
                                    label=params.label, provenance=prov))
        log.info("sweep complete in %.2f s", time.monotonic() - t_start)
        if progress:
            progress(1.0, "Sweep complete")
        return out

