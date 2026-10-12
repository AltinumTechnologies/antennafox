# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""S11 quick-match assessment. Deliberately limited to impedance-match claims: never gain/efficiency."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

import numpy as np

from ..catalog.catalog import BandProfile, Catalog
from ..measurement.model import CalibrationStatus, Measurement
from .bands import (
    BandResult,
    Conclusion,
    Coverage,
    DensityPolicy,
    Thresholds,
    evaluate_band,
    matched_windows,
    resonances,
)
from .quality import QualityConfig, QualityReport, assess_trace_quality
from .rfmath import impedance, s11_db, vswr

GAIN_NOT_MEASURED = "Not measured"
FREQUENCY_DISCLAIMER = (
    "Frequency match does not prove performance, gain, protocol operation, or regulatory suitability."
)
PROTOCOL_DISCLAIMER = (
    "Band compatibility ≠ protocol/device compatibility: an S11 measurement cannot validate, for example, "
    "antenna certification for a radio module, antenna diversity, transmit power limits, or link range."
)


class MatchRating(StrEnum):
    GOOD = "match_good"
    MARGINAL = "match_marginal"
    POOR = "match_poor"
    SUSPECT = "match_suspect"  # well matched but loss suspected → no positive statement possible
    INSUFFICIENT = "insufficient"
    SURVEY = "survey"  # no target bands selected: band matrix only, no overall match verdict


class EvidenceGrade(StrEnum):
    A = "A"  # calibrated (valid metadata), dense, consistent repeats
    B = "B"  # user-confirmed calibration, dense
    C = "C"  # uncorrected / unverified calibration
    D = "D"  # data problems
    SIM = "SIM"  # simulation – never real evidence


EVIDENCE_TEXT = {
    EvidenceGrade.A: "A – calibration with valid metadata, sufficient sample density, consistent repeats",
    EvidenceGrade.B: "B – calibration confirmed by the user, sufficient sample density",
    EvidenceGrade.C: "C – calibration not verified: values of limited reliability",
    EvidenceGrade.D: "D – data problems detected: result not reliable",
    EvidenceGrade.SIM: "SIMULATION – not a real measurement",
}

RATING_TEXT = {
    MatchRating.GOOD: "Well matched in all measured target bands",
    MatchRating.MARGINAL: "Marginal match in at least one target band",
    MatchRating.POOR: "Poor match in at least one target band",
    MatchRating.SUSPECT: "Good match but loss suspected – no positive performance statement possible",
    MatchRating.INSUFFICIENT: "Insufficient data for the selected target bands",
    MatchRating.SURVEY: "Survey scan – no target bands selected",
}


@dataclass
class TraceSummary:
    start_hz: float
    stop_hz: float
    n_points: int
    min_s11_db: float
    min_s11_freq_hz: float
    windows: list[tuple[float, float]]
    resonances: list[tuple[float, float]]


@dataclass
class Assessment:
    rating: MatchRating
    evidence: EvidenceGrade
    band_results: dict[str, BandResult]
    target_ids: list[str]
    survey_ids: list[str]
    worst_target: BandResult | None
    quality: QualityReport
    summary: TraceSummary | None
    calibration_status: CalibrationStatus
    calibration_issues: list[str]
    thresholds: Thresholds
    gain_status: str = GAIN_NOT_MEASURED
    pattern_status: str = GAIN_NOT_MEASURED
    warnings: list[str] = field(default_factory=list)
    disclaimers: list[str] = field(default_factory=lambda: [FREQUENCY_DISCLAIMER, PROTOCOL_DISCLAIMER])

    @property
    def headline(self) -> str:
        if self.rating == MatchRating.SURVEY:
            good = [pid for pid in self.survey_ids
                    if self.band_results[pid].conclusion == Conclusion.WELL_MATCHED]
            return f"{RATING_TEXT[self.rating]}; well matched in {len(good)} of {len(self.survey_ids)} catalog bands"
        if self.rating in (MatchRating.MARGINAL, MatchRating.POOR) and self.target_ids:
            results = [self.band_results[p] for p in self.target_ids if p in self.band_results]
            wanted = Conclusion.POOR if self.rating == MatchRating.POOR else Conclusion.MARGINAL
            k, n = sum(r.conclusion == wanted for r in results), len(results)
            word = "Poor match" if self.rating == MatchRating.POOR else "Marginal match"
            if n == 1:
                return f"{word} in the target band"
            return f"{word} in all {n} target bands" if k == n else f"{word} in {k} of {n} target bands"
        return RATING_TEXT[self.rating]

    @property
    def shown_ids(self) -> list[str]:
        return self.target_ids + [i for i in self.survey_ids if i not in self.target_ids]


_CONCLUSION_RANK = {Conclusion.POOR: 0, Conclusion.MARGINAL: 1, Conclusion.WELL_MATCHED: 2}


def summarize_trace(f: np.ndarray, g: np.ndarray, thr_db: float) -> TraceSummary | None:
    if len(f) == 0:
        return None
    db = s11_db(g)
    i = int(np.nanargmin(np.where(np.isfinite(db), db, np.inf)))
    return TraceSummary(
        start_hz=float(f[0]), stop_hz=float(f[-1]), n_points=len(f),
        min_s11_db=float(db[i]), min_s11_freq_hz=float(f[i]),
        windows=matched_windows(f, g, thr_db), resonances=resonances(f, g),
    )


def point_values(f: np.ndarray, g: np.ndarray, freq_hz: float, z0: float = 50.0) -> dict | None:
    """Nearest actual sweep point (no interpolation) – used for markers/tooltips."""
    if len(f) == 0 or freq_hz < f[0] or freq_hz > f[-1]:
        return None
    i = int(np.argmin(np.abs(f - freq_hz)))
    gi = g[i : i + 1]
    z = complex(impedance(gi, z0)[0])
    return {"freq_hz": float(f[i]), "s11_db": float(s11_db(gi)[0]), "vswr": float(vswr(gi)[0]),
            "z": z, "gamma": complex(gi[0])}


def assess(measurement: Measurement, catalog: Catalog, target_ids: list[str],
           survey_ids: list[str] | None = None, thresholds: Thresholds = Thresholds(),
           density: DensityPolicy = DensityPolicy(), qcfg: QualityConfig = QualityConfig()) -> Assessment:
    f, g = measurement.merged("S11")
    segs = measurement.segments("S11")
    repeats = []
    for rep in measurement.repeats:
        s11 = [t for t in rep if t.parameter == "S11"]
        if len(s11) == 1 and len(segs) == 1 and np.array_equal(s11[0].freq_hz, segs[0].freq_hz):
            repeats.append(s11[0].data)
    if repeats and len(segs) == 1:
        repeats = [segs[0].data, *repeats]
    prov_keys = [(t.provenance.get("serial"), t.provenance.get("cal_type")) for t in segs]
    quality = assess_trace_quality(
        f, g, qcfg, repeats=repeats or None,
        timestamps=[t.acquired_at for t in segs], provenance_keys=prov_keys, segments=[t.data for t in segs],
    ) if len(f) else QualityReport()
    if not len(f):
        quality.add("empty", "error", "No S11 data available.")

    profiles: list[BandProfile] = [p for p in (catalog.get(i) for i in target_ids) if p is not None]
    survey: list[BandProfile] = [p for p in (catalog.get(i) for i in (survey_ids or [])) if p is not None]
    results = {p.id: evaluate_band(p, f, g, thresholds, density) for p in profiles + survey} if len(f) else {}

    span = measurement.span("S11")
    cal_status = measurement.calibration.status(*(span or (None, None)))
    cal_issues = measurement.calibration.issues(*(span or (None, None)))

    measured = [results[p.id] for p in profiles
                if p.id in results and results[p.id].conclusion != Conclusion.INSUFFICIENT]
    worst = min(measured, key=lambda r: (_CONCLUSION_RANK[r.conclusion], -(r.worst_s11_db or 0))) if measured else None
    loss = any(fd.code == "loss_suspected" for fd in quality.findings)

    if not profiles:
        rating = MatchRating.SURVEY
    elif worst is None:
        rating = MatchRating.INSUFFICIENT
    elif worst.conclusion == Conclusion.POOR:
        rating = MatchRating.POOR
    elif worst.conclusion == Conclusion.MARGINAL:
        rating = MatchRating.MARGINAL
    else:
        rating = MatchRating.GOOD
    survey_has_match = any(r.conclusion == Conclusion.WELL_MATCHED for r in results.values())
    if loss and (rating in (MatchRating.GOOD, MatchRating.MARGINAL)
                 or (rating == MatchRating.SURVEY and survey_has_match)):
        rating = MatchRating.SUSPECT

    if measurement.simulated:
        evidence = EvidenceGrade.SIM
    elif quality.has_errors:
        evidence = EvidenceGrade.D
    elif cal_status == CalibrationStatus.NOT_VERIFIED:
        evidence = EvidenceGrade.C
    elif cal_status == CalibrationStatus.VALID_METADATA and all(r.density_ok for r in measured) and \
            any(fd.code == "repeatability" and fd.severity == "info" for fd in quality.findings):
        evidence = EvidenceGrade.A
    else:
        evidence = EvidenceGrade.B

    warnings = [fd.message for fd in quality.findings if fd.severity in ("warn", "error")]
    missing = [p for p in profiles if results.get(p.id) and results[p.id].coverage != Coverage.MEASURED]
    if missing:
        warnings.append("Target bands not fully measured: " + ", ".join(p.label() for p in missing))
    if measurement.antenna.active_gnss:
        warnings.append(
            "Active GNSS antenna: a passive S11 measurement assesses neither LNA gain nor noise figure. "
            "AntennaFox never supplies bias-tee power."
        )

    return Assessment(
        rating=rating, evidence=evidence, band_results=results, target_ids=[p.id for p in profiles],
        survey_ids=[p.id for p in survey],
        worst_target=worst, quality=quality,
        summary=summarize_trace(f, g, thresholds.pass_s11_db) if len(f) else None,
        calibration_status=cal_status, calibration_issues=cal_issues, thresholds=thresholds, warnings=warnings,
    )
