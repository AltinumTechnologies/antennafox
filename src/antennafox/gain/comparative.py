# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Comparative (gain-substitution) measurement: DUT vs a characterized reference antenna via S21.

    G_DUT,realized(f, direction) ≈ G_ref,realized(f, direction) + S21_DUT(dB) − S21_ref(dB) + (L_DUT,path − L_ref,path)

Sign convention: L_*,path are *extra* losses (dB, positive) in the respective receive path that are not common
to both measurements (e.g. an adapter only used with the DUT). They reduce the measured S21, so they are
added back. Result is a *directional realized* gain estimate for the measured direction/polarization only –
never peak gain, never efficiency, never a 3-D pattern.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from ..rf.rfmath import db20, wavelength_m

S21_NOISE_FLOOR_DB = -90.0


class GainConvention(StrEnum):
    REALIZED = "realized"  # includes the reference antenna's own mismatch loss
    IEEE_GAIN = "ieee_gain"  # intrinsic gain; needs reference |Γ| to convert to realized


@dataclass
class ReferenceAntenna:
    name: str = ""
    source: str = ""  # certificate / calibration report reference
    convention: GainConvention = GainConvention.REALIZED
    gain_table: list[tuple[float, float]] = field(default_factory=list)  # (Hz, dBi) in the characterized direction
    tolerance_db: float = 1.0  # standard uncertainty (k=1) from the certificate
    polarization: str = "linear vertical"
    characterized_direction: str = "boresight"
    gamma_table: list[tuple[float, complex]] = field(default_factory=list)  # needed only for IEEE gain
    trusted: bool = False  # operator confirms the data come from a real characterization

    def gain_at(self, f_hz: float) -> float | None:
        """Linear interpolation inside the characterized range only; no extrapolation."""
        if not self.gain_table:
            return None
        t = sorted(self.gain_table)
        fs = np.array([x for x, _ in t])
        gs = np.array([g for _, g in t])
        if f_hz < fs[0] or f_hz > fs[-1]:
            return None
        if len(fs) == 1:
            return float(gs[0]) if f_hz == fs[0] else None
        return float(np.interp(f_hz, fs, gs))

    def mismatch_db_at(self, f_hz: float) -> float | None:
        if not self.gamma_table:
            return None
        t = sorted(self.gamma_table, key=lambda x: x[0])
        fs = np.array([x for x, _ in t])
        if f_hz < fs[0] or f_hz > fs[-1]:
            return None
        mag = float(np.interp(f_hz, fs, np.abs(np.array([g for _, g in t]))))
        if mag >= 1:
            return None
        return 10 * math.log10(1 - mag * mag)  # ≤ 0 dB


@dataclass
class GainSetup:
    frequency_hz: float
    distance_m: float = 0.0
    largest_dimension_m: float = 0.0  # largest dimension of any antenna involved
    polarization_matched: bool = False
    same_position_confirmed: bool = False
    loss_ref_path_db: float = 0.0
    loss_dut_path_db: float = 0.0
    loss_uncertainty_db: float = 0.2
    positioning_uncertainty_db: float = 0.5
    environment: str = "indoor"  # indoor | outdoor | anechoic
    environment_uncertainty_db: float = 1.0
    analysis_bandwidth_hz: float = 0.0  # 0 → ±0.5 % of f


@dataclass
class S21Capture:
    role: str  # "ref" | "dut"
    freq_hz: np.ndarray
    data: np.ndarray
    acquired_at: str = ""
    angle_deg: float | None = None
    label: str = ""
    simulated: bool = False


class GainStatus(StrEnum):
    ABSOLUTE = "absolute"  # estimated directional realized gain in dBi
    RELATIVE_ONLY = "relative_only"
    UNAVAILABLE = "unavailable"


@dataclass
class UncertaintyBudget:
    reference_db: float | None
    repeatability_db: float
    repeatability_estimated: bool
    cable_db: float
    positioning_db: float
    environment_db: float

    def combined(self, include_reference: bool) -> float:
        terms = [self.repeatability_db, self.cable_db, self.positioning_db, self.environment_db]
        if include_reference and self.reference_db is not None:
            terms.append(self.reference_db)
        return math.sqrt(sum(t * t for t in terms))


@dataclass
class GainResult:
    status: GainStatus
    frequency_hz: float
    relative_db: float | None = None
    relative_expanded_db: float | None = None  # k=2
    absolute_dbi: float | None = None
    absolute_expanded_db: float | None = None  # k=2
    reference_realized_dbi: float | None = None
    s21_ref_db: list[float] = field(default_factory=list)
    s21_dut_db: list[float] = field(default_factory=list)
    budget: UncertaintyBudget | None = None
    repeatability: str = "unknown"
    far_field_min_m: float | None = None
    reasons: list[str] = field(default_factory=list)  # why absolute dBi is not available
    warnings: list[str] = field(default_factory=list)
    convention_note: str = ""

    def absolute_text(self) -> str:
        if self.status != GainStatus.ABSOLUTE or self.absolute_dbi is None:
            return "Not measurable with this setup"
        return f"≈ {self.absolute_dbi:+.1f} dBi ± {self.absolute_expanded_db:.1f} dB (k=2), directional, realized"

    def relative_text(self) -> str:
        if self.relative_db is None:
            return "Not available"
        return f"{self.relative_db:+.1f} dB ± {self.relative_expanded_db:.1f} dB (k=2) relative to reference"


def far_field_distance_m(f_hz: float, largest_dim_m: float) -> float:
    """Practical far-field start: max(2D²/λ, 5D, 3λ). Not an anechoic guarantee – reflections still matter."""
    lam = wavelength_m(f_hz)
    return max(2 * largest_dim_m**2 / lam, 5 * largest_dim_m, 3 * lam)


def s21_db_at(cap: S21Capture, f_hz: float, bw_hz: float) -> float | None:
    f = np.asarray(cap.freq_hz, dtype=float)
    d = np.asarray(cap.data, dtype=complex)
    if len(f) == 0 or f_hz < f[0] or f_hz > f[-1]:
        return None
    sel = (f >= f_hz - bw_hz / 2) & (f <= f_hz + bw_hz / 2)
    if sel.any():
        # power average over the analysis window (dB of mean |S21|²)
        return float(10 * np.log10(np.mean(np.abs(d[sel]) ** 2)))
    return float(np.interp(f_hz, f, db20(d)))


def repeatability_label(std_db: float | None) -> str:
    if std_db is None:
        return "unknown (no repeats)"
    if std_db < 0.3:
        return "good"
    if std_db < 1.0:
        return "fair"
    return "poor"


def estimate_gain(reference: ReferenceAntenna | None, setup: GainSetup, ref_caps: list[S21Capture],
                  dut_caps: list[S21Capture]) -> GainResult:
    f = setup.frequency_hz
    bw = setup.analysis_bandwidth_hz or 0.01 * f
    res = GainResult(status=GainStatus.UNAVAILABLE, frequency_hz=f)
    ref_vals = [v for v in (s21_db_at(c, f, bw) for c in ref_caps) if v is not None]
    dut_vals = [v for v in (s21_db_at(c, f, bw) for c in dut_caps) if v is not None]
    res.s21_ref_db, res.s21_dut_db = ref_vals, dut_vals
    if not ref_vals or not dut_vals:
        res.reasons.append("No valid S21 measurements of reference and DUT at this frequency.")
        return res

    for name, vals in (("reference", ref_vals), ("DUT", dut_vals)):
        if max(vals) < S21_NOISE_FLOOR_DB:
            res.warnings.append(f"S21 {name} below {S21_NOISE_FLOOR_DB:.0f} dB – probably in the noise.")

    s_ref = float(np.std(ref_vals, ddof=1)) if len(ref_vals) >= 2 else None
    s_dut = float(np.std(dut_vals, ddof=1)) if len(dut_vals) >= 2 else None
    if s_ref is not None and s_dut is not None:
        u_rep = math.sqrt(s_ref**2 / len(ref_vals) + s_dut**2 / len(dut_vals))
        # spread of single measurements characterizes the setup better than the standard error alone
        u_rep = max(u_rep, 0.5 * max(s_ref, s_dut))
        rep_est = False
        res.repeatability = repeatability_label(max(s_ref, s_dut))
    else:
        u_rep, rep_est = 0.5, True
        res.repeatability = repeatability_label(None)
        res.warnings.append("No repeated measurements: repeatability assumed as 0.5 dB.")

    delta = float(np.mean(dut_vals) - np.mean(ref_vals)) + (setup.loss_dut_path_db - setup.loss_ref_path_db)
    budget = UncertaintyBudget(
        reference_db=reference.tolerance_db if reference else None, repeatability_db=u_rep,
        repeatability_estimated=rep_est, cable_db=setup.loss_uncertainty_db,
        positioning_db=setup.positioning_uncertainty_db, environment_db=setup.environment_uncertainty_db,
    )
    res.budget = budget
    res.relative_db = round(delta, 1)
    res.relative_expanded_db = _round_up(2 * budget.combined(include_reference=False))
    res.status = GainStatus.RELATIVE_ONLY

    # --- gates for absolute dBi ---
    reasons = res.reasons
    ref_realized = None
    if reference is None or not reference.gain_table:
        reasons.append("No characterized reference antenna (gain table) provided.")
    elif not reference.trusted:
        reasons.append("Reference data not confirmed as trustworthy (certificate/calibration source missing).")
    elif not reference.source.strip():
        reasons.append("Source/certificate of the reference gain data is missing.")
    else:
        g = reference.gain_at(f)
        if g is None:
            reasons.append("Reference gain not characterized at this frequency (no extrapolation).")
        elif reference.convention == GainConvention.IEEE_GAIN:
            mm = reference.mismatch_db_at(f)
            if mm is None:
                reasons.append(
                    "Reference gain is IEEE gain (excluding mismatch); converting to realized gain requires the "
                    "reference |Γ| at this frequency."
                )
            else:
                ref_realized = g + mm
                res.convention_note = (
                    f"Reference IEEE gain {g:+.2f} dBi + mismatch {mm:+.2f} dB = realized {ref_realized:+.2f} dBi."
                )
        else:
            ref_realized = g
            res.convention_note = "Reference gain given as realized gain (mismatch included)."
    if reference and reference.tolerance_db <= 0:
        reasons.append("Reference uncertainty missing (tolerance must be > 0 dB).")
    if setup.largest_dimension_m <= 0 or setup.distance_m <= 0:
        reasons.append("Distance and largest antenna dimension are required (far-field check).")
    else:
        rmin = far_field_distance_m(f, setup.largest_dimension_m)
        res.far_field_min_m = rmin
        if setup.distance_m < rmin:
            reasons.append(f"Distance {setup.distance_m:.2f} m < far-field minimum {rmin:.2f} m.")
    if not setup.polarization_matched:
        reasons.append("Matching polarization/orientation of reference and DUT not confirmed.")
    if not setup.same_position_confirmed:
        reasons.append("Identical position/phase center of reference and DUT not confirmed.")
    if any("noise" in w for w in res.warnings):
        reasons.append("Signal too weak for a reliable statement.")
    if any(c.simulated for c in ref_caps + dut_caps):
        res.warnings.append("SIMULATED S21 data – illustrative only, not a measurement.")
    if setup.environment == "indoor":
        res.warnings.append(
            "Indoor (non-anechoic): multipath can bias results by several dB."
        )

    if not reasons and ref_realized is not None:
        res.reference_realized_dbi = ref_realized
        res.absolute_dbi = round(ref_realized + delta, 1)
        res.absolute_expanded_db = _round_up(2 * budget.combined(include_reference=True))
        res.status = GainStatus.ABSOLUTE
    return res


def _round_up(x: float, step: float = 0.1) -> float:
    return round(math.ceil(x / step - 1e-9) * step, 1)


@dataclass
class PatternPoint:
    angle_deg: float
    s21_db: float


@dataclass
class PatternCut:
    plane: str  # "azimuth" | "elevation"
    frequency_hz: float
    polarization: str
    fixed_antenna: str
    points: list[PatternPoint] = field(default_factory=list)

    def normalized(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.points:
            return np.array([]), np.array([])
        pts = sorted(self.points, key=lambda p: p.angle_deg % 360)
        a = np.array([p.angle_deg % 360 for p in pts])
        v = np.array([p.s21_db for p in pts])
        return a, v - np.max(v)

    def summary(self) -> dict:
        a, v = self.normalized()
        if len(a) == 0:
            return {}
        out = {"n": len(a), "max_angle_deg": float(a[np.argmax(v)]), "min_rel_db": float(np.min(v))}
        if len(a) >= 3:
            step = np.median(np.diff(np.sort(a)))
            out["step_deg"] = float(step)
            out["full_circle"] = bool((np.max(a) - np.min(a)) + step >= 359.0)
        return out
