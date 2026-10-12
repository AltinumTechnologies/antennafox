# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""In-process simulated VNA. Every trace it produces is tagged source="simulation"."""

from __future__ import annotations

import time

import numpy as np

from ...measurement.model import InstrumentInfo, InstrumentLimits, TraceSegment, utc_now_iso
from ..base import (
    DEVICE_PROFILES,
    AcquisitionParams,
    CalibrationOwner,
    CancelToken,
    DeviceCapabilities,
    DeviceNotConnected,
    ProgressCallback,
    check_in_limits,
)
from .models import MODELS, AntennaModel

CAPABILITIES = DeviceCapabilities(ports=2, parameters=("S11", "S21"), calibration=CalibrationOwner.SIMULATED)


class SimulatedBackend:
    simulated = True

    def __init__(self, model_key: str = "dualband_24_5", seed: int = 1234, realtime: float = 0.0,
                 limits: InstrumentLimits | None = None):
        self.model_key = model_key
        self.seed = seed
        self.realtime = realtime  # seconds per sweep to emulate (0 = instant)
        base = limits or DEVICE_PROFILES["librevna_v1"]
        self.limits = InstrumentLimits(**{**base.__dict__, "source": "simulation"})
        self._connected = False
        self._sweep_count = 0
        self.cal_type = "SOLT_1 (simulated)"
        self.warnings: list[str] = []

    @property
    def model(self) -> AntennaModel:
        return MODELS[self.model_key]

    def connect(self) -> InstrumentInfo:
        self._connected = True
        return self.info()

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def info(self) -> InstrumentInfo:
        return InstrumentInfo(
            backend="simulation", idn="AntennaFox,Simulator,SIM-0001,1.0", model="Simulator",
            serial="SIMULATION", firmware="sim", hardware_revision="sim", limits=self.limits, simulated=True,
        )

    def refresh_device(self) -> InstrumentInfo:
        return self.info()

    def ping(self) -> float:
        return 0.0

    def capabilities(self) -> DeviceCapabilities:
        return CAPABILITIES

    def calibration_type(self) -> str | None:
        return self.cal_type

    def device_status(self) -> dict[str, str]:
        return {"Temperatures": "not available (simulation)", "Status flags": "all clear (simulation)",
                "Active calibration": self.cal_type or "none"}

    def acquire(self, params: AcquisitionParams, cancel: CancelToken,
                progress: ProgressCallback | None = None) -> list[TraceSegment]:
        if not self._connected:
            raise DeviceNotConnected("Simulator not connected.")
        check_in_limits(params, self.limits, CAPABILITIES)
        steps = 10
        for i in range(steps):
            cancel.check()
            if self.realtime:
                # sleep in short slices so a cancel is honoured within ~50 ms, not after a whole step
                deadline = time.monotonic() + self.realtime / steps
                while time.monotonic() < deadline:
                    cancel.check()
                    time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))
            if progress:
                progress((i + 1) / steps, f"Simulated sweep … {i + 1}/{steps}")
        f = np.linspace(params.start_hz, params.stop_hz, params.points)
        rng = np.random.default_rng(self.seed + self._sweep_count)
        self._sweep_count += 1
        noise_scale = 1.0 / np.sqrt(params.averages) * np.sqrt(params.ifbw_hz / 1000.0)
        out = []
        for p in params.parameters:
            if p == "S11":
                g = self.model.gamma(f)
                g = g + self.model.noise * noise_scale * (rng.standard_normal(f.shape) + 1j * rng.standard_normal(f.shape))
            else:
                # crude S21: free-space coupling between two antennas ~ -40 dB, with the model's mismatch
                gm = self.model.gamma(f)
                g = 10 ** (-40 / 20) * np.sqrt(1 - np.abs(gm) ** 2) * np.exp(-1j * 2 * np.pi * f * 1e-8)
                g = g + 1e-4 * noise_scale * (rng.standard_normal(f.shape) + 1j * rng.standard_normal(f.shape))
            out.append(TraceSegment(
                parameter=p, freq_hz=f, data=g, acquired_at=utc_now_iso(), source="simulation", label=params.label,
                provenance={"model": self.model_key, "seed": self.seed, "ifbw_hz": params.ifbw_hz,
                            "averages": params.averages, "power_dbm": params.power_dbm, "points": params.points,
                            "cal_type": self.cal_type, "serial": "SIMULATION"},
            ))
        return out
