# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Band coverage and match qualification from sampled complex S11.

Coverage policy (configurable via DensityPolicy):
  Two neighbouring samples "cover" the frequencies between them only if their spacing is ≤ max_gap(band).
  max_gap = min(max(width/(min_points-1), floor_fraction·fc), cap_fraction·fc).
  The floor reflects that an antenna S11 cannot change materially over ~0.1 % of fc unless Q ≫ 100;
  the cap prevents wide bands from being "covered" by a handful of points.
A band is "Measured" only if 100 % of every required interval is covered AND each interval contains
at least min_points real samples. Values are never extrapolated beyond the sampled span.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np
from scipy.signal import find_peaks

from ..catalog.catalog import BandProfile
from .rfmath import s11_db, vswr

# An exactly matched sample has S11 = -inf dB; interpolating threshold crossings needs a finite floor.
S11_DB_FLOOR = -200.0


def _db_for_interpolation(gamma_or_mag) -> np.ndarray:
    return np.maximum(s11_db(gamma_or_mag), S11_DB_FLOOR)


class Coverage(StrEnum):
    MEASURED = "measured"
    PARTIAL = "partial"
    NOT_MEASURED = "not_measured"


class Conclusion(StrEnum):
    WELL_MATCHED = "well_matched"
    MARGINAL = "marginal"
    POOR = "poor"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class Thresholds:
    pass_s11_db: float = -10.0  # ≈ VSWR 1.92
    warn_s11_db: float = -6.0  # ≈ VSWR 3.0
    marginal_fraction: float = 0.8


@dataclass(frozen=True)
class DensityPolicy:
    min_points: int = 3
    floor_fraction: float = 0.001
    cap_fraction: float = 0.005

    def max_gap(self, a: float, b: float) -> float:
        fc = 0.5 * (a + b)
        width = b - a
        return min(max(width / max(self.min_points - 1, 1), self.floor_fraction * fc), self.cap_fraction * fc)


@dataclass
class IntervalResult:
    start_hz: float
    stop_hz: float
    covered_hz: float
    n_points: int
    max_gap_hz: float  # largest spacing between consecutive samples touching the interval
    allowed_gap_hz: float


@dataclass
class BandResult:
    profile_id: str
    coverage: Coverage
    conclusion: Conclusion
    covered_fraction: float
    matched_fraction: float | None
    worst_s11_db: float | None
    worst_freq_hz: float | None
    median_s11_db: float | None
    best_s11_db: float | None
    max_vswr: float | None
    n_points: int
    density_ok: bool
    intervals: list[IntervalResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def verdict_code(self) -> str:
        return {
            Conclusion.WELL_MATCHED: "PASS",
            Conclusion.MARGINAL: "WARN",
            Conclusion.POOR: "FAIL",
            Conclusion.INSUFFICIENT: "N/A",
        }[self.conclusion]


def _covered_pieces(f: np.ndarray, a: float, b: float, max_gap: float) -> list[tuple[float, float]]:
    """Sub-intervals of [a,b] bracketed by consecutive samples spaced ≤ max_gap."""
    pieces: list[tuple[float, float]] = []
    if len(f) < 2:
        return pieces
    gaps = np.diff(f)
    lo, hi = f[:-1], f[1:]
    sel = (gaps <= max_gap) & (hi >= a) & (lo <= b)
    for x0, x1 in zip(lo[sel], hi[sel], strict=True):
        s, e = max(x0, a), min(x1, b)
        if e <= s:
            continue
        if pieces and s <= pieces[-1][1]:
            pieces[-1] = (pieces[-1][0], max(e, pieces[-1][1]))
        else:
            pieces.append((s, e))
    return pieces


def _fraction_below(x0: float, x1: float, y0: float, y1: float, thr: float) -> float:
    """Length of [x0,x1] where the linear segment y0→y1 is ≤ thr."""
    if y0 <= thr and y1 <= thr:
        return x1 - x0
    if y0 > thr and y1 > thr:
        return 0.0
    t = (thr - y0) / (y1 - y0)
    xc = x0 + t * (x1 - x0)
    return (xc - x0) if y0 <= thr else (x1 - xc)


def evaluate_band(
    profile: BandProfile,
    freq_hz: np.ndarray,
    gamma: np.ndarray,
    thresholds: Thresholds = Thresholds(),
    density: DensityPolicy = DensityPolicy(),
) -> BandResult:
    f = np.asarray(freq_hz, dtype=float)
    g = np.asarray(gamma, dtype=complex)
    if len(f) and np.any(np.diff(f) <= 0):
        raise ValueError("frequencies must be strictly increasing (use Measurement.merged())")
    mag = np.abs(g)

    eval_f: list[np.ndarray] = []
    eval_m: list[np.ndarray] = []
    interval_results: list[IntervalResult] = []
    total_w = profile.total_width_hz
    covered_total = 0.0
    matched_len = 0.0
    total_pts = 0
    density_ok = True
    notes: list[str] = []

    for a, b in profile.intervals_hz:
        allowed = density.max_gap(a, b)
        pieces = _covered_pieces(f, a, b, allowed)
        covered = sum(e - s for s, e in pieces)
        covered_total += covered
        inside = (f >= a) & (f <= b)
        n_in = int(inside.sum())
        total_pts += n_in
        # spacing of all sample pairs that touch this interval (incl. the brackets around its edges)
        if len(f) >= 2:
            lo_i = max(int(np.searchsorted(f, a, side="right")) - 1, 0)
            hi_i = min(int(np.searchsorted(f, b, side="left")), len(f) - 1)
            seg = f[lo_i : hi_i + 1]
            max_gap = float(np.max(np.diff(seg))) if len(seg) >= 2 else float("inf")
        else:
            max_gap = float("inf")
        interval_results.append(IntervalResult(a, b, covered, n_in, max_gap, allowed))
        if n_in < density.min_points or covered < (b - a) * (1 - 1e-9):
            density_ok = False

        for s, e in pieces:
            # evaluation nodes: real samples within the piece + linear-interpolated piece edges
            in_piece = (f >= s) & (f <= e)
            nodes_f = np.concatenate(([s], f[in_piece], [e]))
            nodes_m = np.interp(nodes_f, f, mag)
            nodes_f, uniq = np.unique(nodes_f, return_index=True)
            nodes_m = nodes_m[uniq]
            eval_f.append(nodes_f)
            eval_m.append(nodes_m)
            db_nodes = _db_for_interpolation(nodes_m)
            for i in range(len(nodes_f) - 1):
                matched_len += _fraction_below(
                    nodes_f[i], nodes_f[i + 1], db_nodes[i], db_nodes[i + 1], thresholds.pass_s11_db
                )

    covered_fraction = covered_total / total_w if total_w > 0 else 0.0
    if covered_fraction >= 1 - 1e-9:
        coverage = Coverage.MEASURED
    elif covered_fraction > 0:
        coverage = Coverage.PARTIAL
    else:
        coverage = Coverage.NOT_MEASURED
    # A few in-band samples without covering spacing still count as partial evidence.
    if coverage == Coverage.NOT_MEASURED and total_pts > 0:
        coverage = Coverage.PARTIAL

    if eval_f:
        all_m = np.concatenate(eval_m)
        all_f = np.concatenate(eval_f)
        db = s11_db(all_m)
        wi = int(np.argmax(all_m))
        worst, worst_f = float(db[wi]), float(all_f[wi])
        median = float(np.median(db))
        best = float(np.min(db))
        maxv = float(np.max(vswr(all_m)))
        matched_fraction = matched_len / covered_total if covered_total > 0 else None
    elif total_pts > 0:
        inside_all = np.zeros(len(f), dtype=bool)
        for a, b in profile.intervals_hz:
            inside_all |= (f >= a) & (f <= b)
        db = s11_db(mag[inside_all])
        wi = int(np.argmax(db))
        worst, worst_f = float(db[wi]), float(f[inside_all][wi])
        median, best = float(np.median(db)), float(np.min(db))
        maxv = float(np.max(vswr(mag[inside_all])))
        matched_fraction = None
    else:
        worst = worst_f = median = best = maxv = None
        matched_fraction = None

    if coverage != Coverage.MEASURED:
        conclusion = Conclusion.INSUFFICIENT
        if coverage == Coverage.PARTIAL:
            notes.append(f"Only {covered_fraction:.0%} of the band measured with sufficient sample density.")
        else:
            notes.append("Band lies outside the measured range.")
    elif not density_ok:
        conclusion = Conclusion.INSUFFICIENT
        notes.append(
            f"Sample density too low: at least {density.min_points} points per interval required "
            "(use a dedicated sweep)."
        )
    else:
        assert worst is not None
        if worst <= thresholds.pass_s11_db:
            conclusion = Conclusion.WELL_MATCHED
        elif worst <= thresholds.warn_s11_db or (matched_fraction or 0.0) >= thresholds.marginal_fraction:
            conclusion = Conclusion.MARGINAL
        else:
            conclusion = Conclusion.POOR

    return BandResult(
        profile_id=profile.id,
        coverage=coverage,
        conclusion=conclusion,
        covered_fraction=covered_fraction,
        matched_fraction=matched_fraction,
        worst_s11_db=worst,
        worst_freq_hz=worst_f,
        median_s11_db=median,
        best_s11_db=best,
        max_vswr=maxv,
        n_points=total_pts,
        density_ok=density_ok,
        intervals=interval_results,
        notes=notes,
    )


def matched_windows(freq_hz: np.ndarray, gamma: np.ndarray, thr_db: float = -10.0,
                    max_gap_hz: float | None = None) -> list[tuple[float, float]]:
    """Contiguous frequency windows with S11 ≤ thr (edges linearly interpolated, no bridging of gaps)."""
    f = np.asarray(freq_hz, dtype=float)
    db = _db_for_interpolation(gamma)
    if len(f) < 2:
        return []
    if max_gap_hz is None:
        max_gap_hz = 4 * float(np.median(np.diff(f)))
    out: list[tuple[float, float]] = []
    cur: list[float] | None = None
    for i in range(len(f)):
        good = db[i] <= thr_db
        broken = i > 0 and (f[i] - f[i - 1]) > max_gap_hz
        if broken and cur is not None:
            cur[1] = f[i - 1]
            out.append((cur[0], cur[1]))
            cur = None
        if good and cur is None:
            if i > 0 and not broken and db[i - 1] > thr_db:
                t = (thr_db - db[i - 1]) / (db[i] - db[i - 1])
                cur = [f[i - 1] + t * (f[i] - f[i - 1]), f[i]]
            else:
                cur = [f[i], f[i]]
        elif not good and cur is not None:
            t = (thr_db - db[i - 1]) / (db[i] - db[i - 1])
            cur[1] = f[i - 1] + t * (f[i] - f[i - 1])
            out.append((cur[0], cur[1]))
            cur = None
        elif good and cur is not None:
            cur[1] = f[i]
    if cur is not None:
        out.append((cur[0], cur[1]))
    return [w for w in out if w[1] > w[0]]


def resonances(freq_hz: np.ndarray, gamma: np.ndarray, min_depth_db: float = 3.0) -> list[tuple[float, float]]:
    """Local S11 minima at least min_depth_db below their surroundings: [(f, S11 dB)]."""
    db = s11_db(gamma)
    if len(db) < 3:
        return []
    finite = np.where(np.isfinite(db), db, -200.0)
    peaks, _ = find_peaks(-finite, prominence=min_depth_db)
    return [(float(freq_hz[i]), float(db[i])) for i in peaks]
