# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Data validation, noise/ripple detection, repeated-sweep statistics and loss-suspicion heuristics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from scipy.signal import find_peaks, savgol_filter

from .rfmath import s11_db


@dataclass(frozen=True)
class QualityConfig:
    passivity_tolerance: float = 0.02  # |Γ| above 1+tol is physically impossible for a passive antenna
    noise_warn_linear: float = 0.01
    ripple_warn_db: float = 1.5
    repeat_warn_db: float = 0.5
    max_capture_spread_s: float = 600.0


@dataclass
class Finding:
    code: str
    severity: str  # "info" | "warn" | "error"
    message: str


@dataclass
class QualityReport:
    findings: list[Finding] = field(default_factory=list)
    noise_sigma: float | None = None
    ripple_pp_db: float | None = None
    repeat_max_std_db: float | None = None
    repeat_drift_db: float | None = None

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    def add(self, code: str, severity: str, message: str) -> None:
        self.findings.append(Finding(code, severity, message))


def validate_trace(freq_hz, data, expected_points: int | None = None, cfg: QualityConfig = QualityConfig(),
                   passive: bool = True) -> list[Finding]:
    out: list[Finding] = []
    f = np.asarray(freq_hz, dtype=float)
    d = np.asarray(data, dtype=complex)
    if f.shape != d.shape:
        out.append(Finding("shape", "error", "Frequency and data lengths differ."))
        return out
    if len(f) == 0:
        out.append(Finding("empty", "error", "No data points."))
        return out
    if not np.all(np.isfinite(f)) or not np.all(np.isfinite(d)):
        out.append(Finding("nonfinite", "error", "Non-finite values (NaN/Inf) in the data."))
    if np.any(f <= 0):
        out.append(Finding("freq_nonpositive", "error", "Non-positive frequencies."))
    if len(f) > 1 and np.any(np.diff(f) <= 0):
        out.append(Finding("freq_order", "error", "Frequencies are not strictly increasing."))
    if expected_points is not None and len(f) != expected_points:
        out.append(Finding("incomplete", "error",
                           f"Incomplete sweep: {len(f)} of {expected_points} points."))
    if passive:
        mag = np.abs(d[np.isfinite(d)])
        if len(mag) and np.max(mag) > 1 + cfg.passivity_tolerance:
            out.append(Finding(
                "non_passive", "warn",
                f"|S11| up to {np.max(mag):.3f} > 1 is physically impossible for a passive antenna – "
                "check calibration and reference plane."))
    return out


def noise_sigma(data) -> float | None:
    """Robust white-noise estimate from the 2nd difference (var(Δ²n) = 6σ² for white noise)."""
    d = np.asarray(data, dtype=complex)
    if len(d) < 8:
        return None
    d2 = np.diff(d, 2)
    mad = np.median(np.abs(d2 - np.median(np.real(d2)) - 1j * np.median(np.imag(d2))))
    # Rayleigh median = σ_c·sqrt(ln 4), with per-component σ_c = sqrt(6)·σ for the 2nd difference
    return float(mad / np.sqrt(np.log(4)) / np.sqrt(6.0))


def ripple_pp_db(data, window_fraction: float = 0.05) -> float | None:
    """Peak-to-peak deviation of |S11| dB from a smoothed trend, evaluated only off-resonance (S11 > -3 dB)
    and away from resonance dips, where standing waves from cable/adapter mismatch become visible."""
    db = s11_db(data)
    n = len(db)
    if n < 15 or not np.all(np.isfinite(db)):
        return None
    win = max(7, int(n * window_fraction) | 1)
    if win >= n:
        return None
    trend = savgol_filter(db, win, 2)
    resid = db - trend
    mask = db > -3
    dips, _ = find_peaks(-db, prominence=3.0)
    for i in dips:
        mask[max(0, i - 2 * win): i + 2 * win + 1] = False
    if mask.sum() < 5:
        return None
    return float(np.ptp(resid[mask]))


def repeat_statistics(sweeps: list[np.ndarray], floor_db: float = -20.0) -> tuple[float, float] | None:
    """(max per-frequency std of S11 dB, mean drift first→last in dB) for sweeps on an identical grid.
    Only frequencies with mean S11 above floor_db are used: deep nulls make dB statistics meaningless."""
    if len(sweeps) < 2:
        return None
    arr = np.vstack([s11_db(s) for s in sweeps])
    if not np.all(np.isfinite(arr)):
        return None
    mask = arr.mean(axis=0) > floor_db
    if mask.sum() < 2:
        return None
    arr = arr[:, mask]
    std = np.std(arr, axis=0, ddof=1)
    drift = float(np.mean(arr[-1] - arr[0]))
    return float(np.max(std)), drift


def capture_spread_seconds(timestamps: list[str]) -> float | None:
    ts = []
    for t in timestamps:
        try:
            ts.append(datetime.fromisoformat(t))
        except ValueError:
            return None
    if len(ts) < 2:
        return 0.0 if ts else None
    return (max(ts) - min(ts)).total_seconds()


def loss_suspected(freq_hz, gamma, thr_db: float = -10.0) -> tuple[bool, str]:
    """Heuristic: S11 ≤ thr over ≥ 1 octave without a distinct resonance suggests absorbed rather than
    radiated power (lossy antenna, attenuator, long lossy cable, or a 50 Ω termination)."""
    f = np.asarray(freq_hz, dtype=float)
    db = s11_db(gamma)
    if len(f) < 10:
        return False, ""
    good = db <= thr_db
    if good.mean() < 0.5:
        return False, ""
    # longest contiguous run of good match
    best_ratio, run_start = 1.0, None
    for i, ok in enumerate(good):
        if ok and run_start is None:
            run_start = i
        if (not ok or i == len(good) - 1) and run_start is not None:
            end = i if ok else i - 1
            if f[run_start] > 0:
                best_ratio = max(best_ratio, f[end] / f[run_start])
            run_start = None
    finite = db[np.isfinite(db)]
    depth = float(np.median(finite) - np.min(finite)) if len(finite) else 0.0
    # Noise on a matched load produces deep but meaningless dips, so very wide matches are flagged regardless.
    if best_ratio >= 4.0 or (best_ratio >= 2.0 and depth < 10.0):
        return True, (
            f"Broadband match (S11 ≤ {thr_db:.0f} dB over a {best_ratio:.3g}:1 frequency ratio) without a distinct "
            "resonance. Typical of lossy antennas, attenuators, long lossy cables, or 50 Ω terminations – or of a "
            "genuinely broadband antenna. S11 alone does not prove radiation."
        )
    return False, ""


def no_visible_resonance(freq_hz, gamma, thr_db: float = -10.0) -> bool:
    db = s11_db(gamma)
    if len(db) < 10 or not np.all(db[np.isfinite(db)] <= thr_db):
        return False
    peaks, _ = find_peaks(-np.where(np.isfinite(db), db, -200.0), prominence=6.0)
    return len(peaks) == 0


def assess_trace_quality(freq_hz, gamma, cfg: QualityConfig = QualityConfig(),
                         repeats: list[np.ndarray] | None = None, timestamps: list[str] | None = None,
                         provenance_keys: list[tuple] | None = None,
                         segments: list[np.ndarray] | None = None) -> QualityReport:
    rep = QualityReport()
    rep.findings.extend(validate_trace(freq_hz, gamma, cfg=cfg))
    rep.noise_sigma = noise_sigma(gamma)
    if rep.noise_sigma is not None and rep.noise_sigma > cfg.noise_warn_linear:
        rep.add("noisy", "warn",
                f"High measurement noise (σ≈{rep.noise_sigma:.3f} linear). Reduce IF bandwidth or average.")
    ripples = [r for r in (ripple_pp_db(seg) for seg in (segments or [gamma])) if r is not None]
    rep.ripple_pp_db = max(ripples) if ripples else None
    if rep.ripple_pp_db is not None and rep.ripple_pp_db > cfg.ripple_warn_db:
        rep.add("ripple", "warn",
                f"Ripple of {rep.ripple_pp_db:.1f} dB peak-to-peak – possible cable/adapter mismatch, "
                "multipath, or missing calibration.")
    if repeats:
        st = repeat_statistics(repeats)
        if st:
            rep.repeat_max_std_db, rep.repeat_drift_db = st
            if st[0] > cfg.repeat_warn_db or abs(st[1]) > cfg.repeat_warn_db:
                rep.add("repeatability", "warn",
                        f"Repeated sweeps scatter (max σ {st[0]:.2f} dB, drift {st[1]:+.2f} dB) – "
                        "check cable movement, hand effect, or surroundings.")
            else:
                rep.add("repeatability", "info",
                        f"Repeated sweeps consistent (max σ {st[0]:.2f} dB).")
    if timestamps:
        spread = capture_spread_seconds(timestamps)
        if spread is not None and spread > cfg.max_capture_spread_s:
            rep.add("stale_mix", "warn",
                    f"Segments were captured over {spread / 60:.0f} min – the setup may have changed.")
    if provenance_keys and len(set(provenance_keys)) > 1:
        rep.add("mixed_provenance", "warn",
                "Segments come from different instruments or calibration states.")
    suspected, msg = loss_suspected(freq_hz, gamma)
    if suspected:
        rep.add("loss_suspected", "warn", msg)
    elif no_visible_resonance(freq_hz, gamma):
        rep.add("no_resonance_in_span", "warn",
                "The whole measured span is matched without a visible resonance. Losses (attenuator, lossy cable, "
                "termination) cannot be excluded from this span alone – run a wide survey scan.")
    return rep
