# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Plain-language interpretation of an S11 assessment for non-specialist users."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ..catalog.catalog import BandProfile, Catalog
from ..measurement.model import Measurement
from .assessment import Assessment, MatchRating
from .bands import BandResult, Conclusion, matched_windows
from .rfmath import s11_db

SHORT_CONCLUSION = {
    Conclusion.WELL_MATCHED: "Good match",
    Conclusion.MARGINAL: "Borderline",
    Conclusion.POOR: "Poor match",
    Conclusion.INSUFFICIENT: "Not enough data",
}


def reflected_percent(s11_db_value: float) -> float:
    return 100.0 * 10 ** (s11_db_value / 10.0)


def accepted_percent(s11_db_value: float) -> float:
    """Share of the incident power the antenna accepts at this S11 (the rest is reflected)."""
    return 100.0 - reflected_percent(s11_db_value)


def mismatch_loss_db(s11_db_value: float) -> float:
    """Power lost to reflection, in dB; +inf at total reflection."""
    accepted = 1.0 - 10 ** (s11_db_value / 10.0)
    return float("inf") if accepted <= 0 else -10.0 * math.log10(accepted)


def accepted_floor_percent(s11_db_value: float) -> int:
    """Accepted power as a lower bound: rounded down, so '≥ 81 %' is never overstated."""
    return int(math.floor(accepted_percent(s11_db_value)))


def mismatch_ceil_db(s11_db_value: float) -> float:
    """Mismatch loss as an upper bound: rounded up to 0.1 dB, so '≤ 1.2 dB' is never understated."""
    loss = mismatch_loss_db(s11_db_value)
    return loss if not math.isfinite(loss) else math.ceil(loss * 10 - 1e-9) / 10


def power_sentence(worst_s11_db: float) -> str:
    """How much of the transmitter power gets into the antenna at the worst point of the band."""
    pct, loss = accepted_floor_percent(worst_s11_db), mismatch_ceil_db(worst_s11_db)
    loss_text = f"{loss:.1f} dB" if math.isfinite(loss) else "∞"
    return f"accepts at least {pct} % of the power across the band (mismatch loss ≤ {loss_text})"


def short_label(p: BandProfile) -> str:
    return p.label().split(" (")[0]


@dataclass
class Resonance:
    freq_hz: float
    s11_db: float
    band_center_hz: float
    offset_hz: float  # positive: resonance above the band center
    inside_band: bool
    edge: str | None = None  # "upper"/"lower": S11 still falling at the end of the measured range


def band_resonance(m: Measurement, profile: BandProfile, search_fraction: float = 0.35) -> Resonance | None:
    """Deepest S11 point within ±35 % of the band center (only actually measured points)."""
    f, g = m.merged("S11")
    if len(f) == 0:
        return None
    a, b = profile.span
    fc = 0.5 * (a + b)
    sel = (f >= fc * (1 - search_fraction)) & (f <= fc * (1 + search_fraction))
    if sel.sum() < 3:
        return None
    db = s11_db(g[sel])
    i = int(np.argmin(db))
    fr = float(f[sel][i])
    edge = None
    if i == len(db) - 1 and f[sel][-1] >= f[-1]:
        edge = "upper"
    elif i == 0 and f[sel][0] <= f[0]:
        edge = "lower"
    return Resonance(fr, float(db[i]), fc, fr - fc, a <= fr <= b, edge)


def tuning_hint(res: Resonance, result: BandResult) -> str | None:
    """Direction hint only when the band is not well matched and the dip sits clearly outside the band."""
    if result.conclusion == Conclusion.WELL_MATCHED or res.inside_band:
        return None
    if res.edge:
        side = "above" if res.edge == "upper" else "below"
        change = "lower" if res.edge == "upper" else "higher"
        length = "longer" if res.edge == "upper" else "shorter"
        return (f"S11 is still falling at the end of the measured range – the resonance lies {side} it. A {change} "
                f"resonance is needed – for wire/whip antennas this usually means a slightly {length} radiator.")
    if res.s11_db > -6:
        return None
    off_mhz = abs(res.offset_hz) / 1e6
    if res.offset_hz > 0:
        return (f"The resonance (deepest dip) is about {off_mhz:.0f} MHz above the band. A lower resonance is needed – "
                "for wire/whip antennas this usually means a slightly longer radiator.")
    return (f"The resonance (deepest dip) is about {off_mhz:.0f} MHz below the band. A higher resonance is needed – "
            "for wire/whip antennas this usually means a slightly shorter radiator.")


def centering_note(m: Measurement, profile: BandProfile, res: Resonance, result: BandResult,
                   thr_db: float) -> str | None:
    """For a passing band: say when the dip is off-centre or an edge of the matched window is close to the band."""
    if result.conclusion != Conclusion.WELL_MATCHED or res.edge:
        return None
    f, g = m.merged("S11")
    a, b = profile.span
    win = next(((lo, hi) for lo, hi in matched_windows(f, g, thr_db) if lo <= res.freq_hz <= hi), None)
    if win is None:
        return None
    # a window that runs into the end of the sweep only gives a lower bound for that margin
    lo_clipped, hi_clipped = win[0] <= f[0], win[1] >= f[-1]
    lo_margin, hi_margin = a - win[0], win[1] - b
    width = b - a
    tight_lo, tight_hi = (not lo_clipped and lo_margin < 0.5 * width), (not hi_clipped and hi_margin < 0.5 * width)
    off_centre = not res.inside_band or abs(res.offset_hz) > width / 3
    if not (tight_lo or tight_hi or off_centre) or min(lo_margin, hi_margin) >= width:
        return None  # plenty of margin on both sides: nothing worth saying
    name = short_label(profile)
    if off_centre:
        side = "above" if res.offset_hz > 0 else "below"
        lead = f"{name}: good, but not centred – the dip sits {abs(res.offset_hz) / 1e6:.0f} MHz {side} the band centre."
    else:
        lead = f"{name}: good, but with little margin."

    def margin(v: float, clipped: bool) -> str:
        return f"more than {v / 1e6:.0f} MHz" if clipped else f"{v / 1e6:.0f} MHz"

    text = (f"{lead} The {thr_db:.0f} dB window extends {margin(lo_margin, lo_clipped)} below the band and "
            f"{margin(hi_margin, hi_clipped)} above it.")
    # dielectric loading (a case, a hand, nearby objects) shifts the whole matched window to lower frequencies
    if tight_hi:
        text += (" A case or nearby objects usually pull the resonance down, which would eat into the tight upper "
                 "margin – re-test in the final mounting.")
    elif tight_lo:
        text += " A case or nearby objects usually pull the resonance down, which widens the tight lower margin."
    return text


def band_sentence(p: BandProfile, r: BandResult) -> str:
    name = short_label(p)
    if r.conclusion == Conclusion.INSUFFICIENT:
        return f"{name}: not fully measured – no statement possible."
    worst = r.worst_s11_db if r.worst_s11_db is not None else 0.0
    vswr = f"VSWR {r.max_vswr:.2f}" if r.max_vswr is not None and np.isfinite(r.max_vswr) else "VSWR ∞"
    return f"{name}: {power_sentence(worst)}; worst S11 {worst:.1f} dB, {vswr}."


def headline(a: Assessment, use_case: str) -> tuple[str, str]:
    """(title, one-sentence explanation)."""
    if a.rating == MatchRating.GOOD:
        return (f"Good match for {use_case}",
                "The antenna accepts the transmitter power across the whole target band. How much of it is "
                "radiated is a separate question – see the gain test.")
    if a.rating == MatchRating.MARGINAL:
        return (f"Borderline match for {use_case}",
                "Usable, but part of the band reflects noticeably more power than ideal.")
    if a.rating == MatchRating.POOR:
        return (f"Poor match for {use_case}",
                "A large share of the power is reflected in at least one target band.")
    if a.rating == MatchRating.SUSPECT:
        return ("Matched everywhere – possibly lossy",
                "A match this wide without a clear resonance is typical of lossy antennas, attenuators or "
                "terminations. S11 cannot tell whether the antenna actually radiates.")
    if a.rating == MatchRating.SURVEY:
        good = [i for i in a.survey_ids if a.band_results[i].conclusion == Conclusion.WELL_MATCHED]
        return (f"Scan complete – well matched in {len(good)} of {len(a.survey_ids)} common bands",
                "Pick an intended use to get a clear verdict for that band.")
    return ("Not enough data", "The target band was not fully measured.")


def explain(m: Measurement, a: Assessment, catalog: Catalog, use_case: str) -> dict:
    title, sentence = headline(a, use_case)
    ids = a.target_ids or [i for i in a.survey_ids if a.band_results[i].conclusion != Conclusion.INSUFFICIENT]
    bands, hints, notes, seen = [], [], [], []
    for pid in ids:
        p, r = catalog.get(pid), a.band_results.get(pid)
        if p is None or r is None:
            continue
        bands.append((p, r, band_sentence(p, r)))
        res = band_resonance(m, p)
        if res is None or any(abs(res.freq_hz - f) < 0.01 * res.freq_hz for f in seen):
            continue  # overlapping bands share one notch: one hint is enough
        hint = tuning_hint(res, r)
        note = centering_note(m, p, res, r, a.thresholds.pass_s11_db) if hint is None else None
        if hint or note:
            seen.append(res.freq_hz)
        if hint:
            hints.append(hint)
        if note:
            notes.append(note)
    return {"title": title, "sentence": sentence, "bands": bands, "hints": hints[:2], "notes": notes[:2]}
