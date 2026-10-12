# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measurement data model. Captured traces are immutable; everything derived is recomputed from them."""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

import numpy as np


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _frozen(a: np.ndarray) -> np.ndarray:
    a = np.array(a, copy=True)
    a.setflags(write=False)
    return a


@dataclass(frozen=True)
class TraceSegment:
    """One contiguous captured sweep of one S-parameter. Never mutated after capture."""

    parameter: str
    freq_hz: np.ndarray
    data: np.ndarray
    acquired_at: str
    source: str  # adapter id for live data (e.g. "librevna-scpi"), "simulation" or "touchstone-import"
    label: str = ""
    provenance: dict = field(default_factory=dict)
    z0: float = 50.0  # reference impedance in ohms; the analysis pipeline currently handles 50 Ω only

    def __post_init__(self):
        f = np.asarray(self.freq_hz, dtype=float)
        d = np.asarray(self.data, dtype=complex)
        if f.ndim != 1 or d.shape != f.shape:
            raise ValueError("frequency and data arrays must be 1-D and of equal length")
        object.__setattr__(self, "freq_hz", _frozen(f))
        object.__setattr__(self, "data", _frozen(d))

    @property
    def start_hz(self) -> float:
        return float(self.freq_hz[0]) if len(self.freq_hz) else float("nan")

    @property
    def stop_hz(self) -> float:
        return float(self.freq_hz[-1]) if len(self.freq_hz) else float("nan")


class CalibrationStatus(StrEnum):
    NOT_VERIFIED = "not_verified"
    USER_CONFIRMED = "user_confirmed"
    VALID_METADATA = "valid_metadata"


REFERENCE_PLANES = {
    "unknown": "Unknown",
    "vna_port": "VNA port (no cable)",
    "cable_end": "End of test cable",
    "antenna_connector": "Antenna connector (incl. adapter/pigtail)",
}


@dataclass
class CalibrationMeta:
    """App-side calibration metadata. The correction itself is applied by the instrument or its host software."""

    gui_reported_type: str | None = None  # VNABackend.calibration_type(); None = unknown (name kept for sessions)
    user_confirmed: bool = False
    cal_date: str | None = None
    cal_start_hz: float | None = None
    cal_stop_hz: float | None = None
    reference_plane: str = "unknown"
    cal_kit: str = ""
    notes: str = ""
    verification: str = ""  # e.g. result of the 50 Ω load check, with timestamp

    def status(self, sweep_start_hz: float | None = None, sweep_stop_hz: float | None = None) -> CalibrationStatus:
        if not self.user_confirmed:
            return CalibrationStatus.NOT_VERIFIED
        cs, ce = self.cal_start_hz, self.cal_stop_hz
        span_known = cs is not None and ce is not None
        covers = True
        if cs is not None and ce is not None and sweep_start_hz is not None and sweep_stop_hz is not None:
            covers = cs <= sweep_start_hz and ce >= sweep_stop_hz
        gui_active = bool(self.gui_reported_type) and self.gui_reported_type.lower() != "none"
        if span_known and covers and self.cal_date and self.reference_plane != "unknown" and gui_active:
            return CalibrationStatus.VALID_METADATA
        return CalibrationStatus.USER_CONFIRMED

    def age_days(self, now: datetime | None = None) -> float | None:
        if not self.cal_date:
            return None
        try:
            d = datetime.fromisoformat(self.cal_date)
        except ValueError:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=UTC)
        now = now or datetime.now(UTC)
        return (now - d).total_seconds() / 86400.0

    def issues(self, sweep_start_hz: float | None, sweep_stop_hz: float | None) -> list[str]:
        out = []
        if not self.gui_reported_type or self.gui_reported_type.lower() == "none":
            out.append("The instrument reports no active calibration – values are uncorrected.")
        if not self.user_confirmed:
            out.append("Calibration not confirmed by the user.")
        if self.reference_plane == "unknown":
            out.append("Reference plane unknown.")
        if (
            self.cal_start_hz is not None
            and self.cal_stop_hz is not None
            and sweep_start_hz is not None
            and sweep_stop_hz is not None
            and (self.cal_start_hz > sweep_start_hz or self.cal_stop_hz < sweep_stop_hz)
        ):
            out.append("Calibration span does not fully cover the measured span.")
        return out


@dataclass
class InstrumentLimits:
    min_freq_hz: float
    max_freq_hz: float
    max_points: int
    min_ifbw_hz: float = 10.0
    max_ifbw_hz: float = 50_000.0
    min_power_dbm: float = -40.0
    max_power_dbm: float = 0.0
    source: str = "profile"  # "device" | "profile:<name>" | "simulation"


@dataclass
class InstrumentInfo:
    backend: str = "none"  # adapter id (e.g. "librevna-scpi"), "simulation" or "import"
    idn: str = ""
    model: str = ""  # device family as identified by the adapter, e.g. "LibreVNA"
    host_software: str = ""  # intermediary the adapter talks to (e.g. "LibreVNA-GUI"); empty for direct links
    gui_version: str = ""  # version of host_software (field name kept for session compatibility)
    serial: str = ""
    firmware: str = ""
    hardware_revision: str = ""
    host: str = ""
    port: int = 0
    limits: InstrumentLimits | None = None
    simulated: bool = False


ENVIRONMENTS = {
    "": "not stated",
    "free_space": "free space (tripod, > 1 m from objects)",
    "ground_plane": "on a ground plane",
    "enclosure": "in its enclosure",
    "final_device": "mounted in the final device",
    "bench": "on the bench, objects nearby",
}


@dataclass
class AntennaInfo:
    name: str = ""
    dut_reference: str = ""  # part number, lot, sample id
    environment: str = ""  # key of ENVIRONMENTS
    antenna_type: str = ""
    connector: str = ""
    cable: str = ""
    mounting: str = ""
    variant: str = ""  # e.g. "bare", "in enclosure" – used for before/after overlays
    photo_path: str = ""
    active_gnss: bool = False
    notes: str = ""


@dataclass
class SweepSegmentSpec:
    start_hz: float
    stop_hz: float
    points: int
    label: str = ""


@dataclass
class SweepPlan:
    preset_id: str
    segments: list[SweepSegmentSpec]
    ifbw_hz: float = 1000.0
    averages: int = 1
    power_dbm: float = -20.0
    parameters: tuple[str, ...] = ("S11",)
    omissions: list[str] = field(default_factory=list)


@dataclass
class Measurement:
    name: str
    antenna: AntennaInfo
    instrument: InstrumentInfo
    calibration: CalibrationMeta
    sweep_plan: SweepPlan | None
    traces: list[TraceSegment] = field(default_factory=list)
    repeats: list[list[TraceSegment]] = field(default_factory=list)
    notes: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=utc_now_iso)
    app_version: str = ""
    operator: str = ""
    project: str = ""

    @property
    def report_id(self) -> str:
        return f"AFX-{self.created_at[:10].replace('-', '')}-{self.id[:6].upper()}"

    @property
    def simulated(self) -> bool:
        return self.instrument.simulated or any(t.source == "simulation" for t in self.traces)

    def parameters(self) -> list[str]:
        return sorted({t.parameter for t in self.traces})

    def segments(self, parameter: str = "S11") -> list[TraceSegment]:
        return sorted((t for t in self.traces if t.parameter == parameter), key=lambda t: t.start_hz)

    def merged(self, parameter: str = "S11") -> tuple[np.ndarray, np.ndarray]:
        """Sorted union of all segments. Duplicate frequencies keep the worse (larger |S|) value."""
        segs = self.segments(parameter)
        if not segs:
            return np.array([]), np.array([], dtype=complex)
        f = np.concatenate([s.freq_hz for s in segs])
        d = np.concatenate([s.data for s in segs])
        order = np.lexsort((-np.abs(d), f))
        f, d = f[order], d[order]
        keep = np.ones(len(f), dtype=bool)
        keep[1:] = f[1:] != f[:-1]
        return f[keep], d[keep]

    def span(self, parameter: str = "S11") -> tuple[float, float] | None:
        segs = self.segments(parameter)
        if not segs:
            return None
        return min(s.start_hz for s in segs), max(s.stop_hz for s in segs)

    def to_meta_dict(self) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at,
            "app_version": self.app_version,
            "notes": self.notes,
            "operator": self.operator,
            "project": self.project,
            "antenna": asdict(self.antenna),
            "instrument": asdict(self.instrument),
            "calibration": asdict(self.calibration),
            "sweep_plan": asdict(self.sweep_plan) if self.sweep_plan else None,
        }
        return d
