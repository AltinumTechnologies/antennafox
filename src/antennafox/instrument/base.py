# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Device-independent instrument interface: errors, cancellation, acquisition request, capabilities and the
adapter Protocol every VNA backend implements. Nothing in here knows a specific command set."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from ..measurement.model import InstrumentInfo, InstrumentLimits, TraceSegment


class InstrumentError(Exception):
    """Base for all instrument problems; messages are user-facing."""


class ConnectionFailed(InstrumentError):
    pass


class InstrumentTimeout(InstrumentError):
    pass


class CommandRejected(InstrumentError):
    pass


class ProtocolError(InstrumentError):
    pass


class DeviceNotConnected(InstrumentError):
    pass


class OutOfRange(InstrumentError):
    pass


class Cancelled(InstrumentError):
    pass


class CancelToken:
    def __init__(self):
        self._ev = threading.Event()

    def cancel(self) -> None:
        self._ev.set()

    def check(self) -> None:
        if self._ev.is_set():
            raise Cancelled("Measurement cancelled.")

    def wait(self, seconds: float) -> None:
        if self._ev.wait(seconds):
            raise Cancelled("Measurement cancelled.")


@dataclass
class AcquisitionParams:
    start_hz: float
    stop_hz: float
    points: int
    ifbw_hz: float = 1000.0
    averages: int = 1
    power_dbm: float = -20.0
    parameters: tuple[str, ...] = ("S11",)
    label: str = ""


ProgressCallback = Callable[[float, str], None]


class CalibrationOwner(StrEnum):
    """Where the correction applied to delivered data comes from. Only values an existing adapter uses."""

    HOST_SOFTWARE = "host_software"  # the device's host software calibrates; the adapter only reads the active type
    SIMULATED = "simulated"


@dataclass(frozen=True)
class DeviceCapabilities:
    """What an adapter can actually deliver. Declared statically per adapter and checked before every sweep."""

    ports: int
    parameters: tuple[str, ...]  # S-parameters the adapter can acquire, e.g. ("S11", "S21")
    calibration: CalibrationOwner


# Known hardware profiles used when the device cannot report its limits (or for planning offline).
DEVICE_PROFILES: dict[str, InstrumentLimits] = {
    "librevna_v1": InstrumentLimits(
        min_freq_hz=100e3, max_freq_hz=6e9, max_points=4501, min_ifbw_hz=10, max_ifbw_hz=50e3,
        min_power_dbm=-40, max_power_dbm=0, source="profile:LibreVNA V1 (100 kHz–6 GHz)",
    ),
}


class VNABackend(Protocol):
    """Adapter contract. Implementations translate their protocol into InstrumentInfo and TraceSegments and raise
    InstrumentError subclasses for every failure; blocking calls honour the CancelToken."""

    simulated: bool
    warnings: list[str]  # non-fatal findings from connect(), shown to the user

    def connect(self) -> InstrumentInfo: ...

    def disconnect(self) -> None: ...

    def is_connected(self) -> bool: ...

    def info(self) -> InstrumentInfo: ...

    def refresh_device(self) -> InstrumentInfo: ...

    def ping(self) -> float: ...

    def capabilities(self) -> DeviceCapabilities: ...

    def calibration_type(self) -> str | None:
        """Calibration the device (or its host software) reports as active; None when unknown or unsupported."""
        ...

    def device_status(self) -> dict[str, str]:
        """Live, human-readable instrument details (temperatures, lock state, reference …)."""
        ...

    def acquire(self, params: AcquisitionParams, cancel: CancelToken,
                progress: ProgressCallback | None = None) -> list[TraceSegment]: ...


def check_in_limits(params: AcquisitionParams, limits: InstrumentLimits,
                    capabilities: DeviceCapabilities | None = None) -> None:
    """Never send out-of-range settings silently: reject before anything goes to the instrument."""
    if capabilities is not None:
        unsupported = [p for p in params.parameters if p not in capabilities.parameters]
        if unsupported:
            raise OutOfRange(f"This instrument cannot measure {', '.join(unsupported)} "
                             f"(supported: {', '.join(capabilities.parameters)}).")
    if params.start_hz < limits.min_freq_hz or params.stop_hz > limits.max_freq_hz:
        raise OutOfRange(
            f"Frequency range {params.start_hz / 1e6:.3f}–{params.stop_hz / 1e6:.3f} MHz is outside the "
            f"instrument limits {limits.min_freq_hz / 1e6:.3f}–{limits.max_freq_hz / 1e6:.3f} MHz."
        )
    if params.stop_hz <= params.start_hz:
        raise OutOfRange("Stop frequency must be greater than start frequency.")
    if not (2 <= params.points <= limits.max_points):
        raise OutOfRange(f"Point count {params.points} outside 2–{limits.max_points}.")
    if not (limits.min_ifbw_hz <= params.ifbw_hz <= limits.max_ifbw_hz):
        raise OutOfRange(f"IF bandwidth {params.ifbw_hz:g} Hz outside {limits.min_ifbw_hz:g}–{limits.max_ifbw_hz:g} Hz.")
    if not (limits.min_power_dbm <= params.power_dbm <= limits.max_power_dbm):
        raise OutOfRange(
            f"Output power {params.power_dbm:g} dBm outside {limits.min_power_dbm:g}…{limits.max_power_dbm:g} dBm."
        )
    if params.averages < 1:
        raise OutOfRange("Averages must be ≥ 1.")


def estimate_sweep_seconds(params: AcquisitionParams) -> float:
    # Conservative per-point estimate: ~3 IF periods plus an assumed 0.3 ms USB/processing overhead.
    per_point = 3.0 / params.ifbw_hz + 3e-4
    return params.points * params.averages * per_point
