# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Quality heuristics and plain-language explanations: thresholds, estimator accuracy and wording."""

import numpy as np
import pytest

from antennafox.catalog.catalog import load_catalog
from antennafox.instrument.simulated.models import AntennaModel, SeriesRLC
from antennafox.measurement.model import (
    AntennaInfo,
    CalibrationMeta,
    InstrumentInfo,
    Measurement,
    TraceSegment,
)
from antennafox.rf.assessment import EvidenceGrade, MatchRating, assess
from antennafox.rf.bands import Conclusion
from antennafox.rf.explain import band_resonance, band_sentence, explain, headline
from antennafox.rf.quality import (
    assess_trace_quality,
    capture_spread_seconds,
    loss_suspected,
    no_visible_resonance,
    noise_sigma,
    repeat_statistics,
    ripple_pp_db,
    validate_trace,
)


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def db_trace(f0, f1, db_fn, n=401):
    f = np.linspace(f0, f1, n)
    return f, 10 ** (db_fn(f) / 20) + 0j


# --- validation ------------------------------------------------------------------------------------------
def test_validate_trace_frequency_and_passivity_boundaries():
    def codes(f, d, **kw):
        return {x.code for x in validate_trace(np.asarray(f, dtype=float), np.asarray(d, dtype=complex), **kw)}

    assert codes([0.0, 1e9], [0.1, 0.1]) == {"freq_nonpositive"}
    assert codes([1e9, 2e9], [1.015, 0.5]) == set()  # inside the 2 % passivity tolerance
    assert codes([1e9, 2e9], [1.03, 0.5]) == {"non_passive"}
    assert codes([1e9, 2e9], [1.5, 0.5], passive=False) == set()  # S21 of an amplifier may exceed 0 dB
    assert codes([1e9], [0.1], expected_points=1) == set()
    assert codes([1e9, 1e9 + 1], [0.1, 0.1]) == set()  # strictly increasing by 1 Hz is still valid


# --- estimators ------------------------------------------------------------------------------------------
def test_noise_sigma_accuracy_and_short_input():
    assert noise_sigma(np.zeros(7, dtype=complex)) is None
    rng = np.random.default_rng(1)
    f = np.linspace(1e9, 2e9, 2001)
    smooth = 0.3 * np.exp(1j * f / 1e9)
    assert noise_sigma(smooth + 0.01 * (rng.standard_normal(2001) + 1j * rng.standard_normal(2001))) == \
        pytest.approx(0.01, rel=0.03)
    assert noise_sigma(smooth) == pytest.approx(0.0, abs=1e-6)


def test_ripple_needs_usable_off_resonance_data():
    assert ripple_pp_db(np.full(10, 0.5 + 0j)) is None
    assert ripple_pp_db(np.full(401, 0.1 + 0j)) is None  # everything below -3 dB: nothing to judge
    g = np.full(401, 0.9 + 0j)
    g[5] = np.nan
    assert ripple_pp_db(g) is None
    assert ripple_pp_db(np.full(401, 0.9 + 0j)) == pytest.approx(0.0, abs=1e-9)
    f = np.linspace(1e9, 3e9, 801)
    ripple = ripple_pp_db(0.9 * (1 + 0.15 * np.cos(f / 5e6)) + 0j)
    assert ripple is not None and 2.0 < ripple < 3.5  # ±15 % amplitude ripple ≈ 2.6 dB peak-to-peak


def test_repeat_statistics_known_values():
    a = 10 ** (np.array([-15.0, -15.0, -15.0, -40.0]) / 20) + 0j
    b = 10 ** (np.array([-14.0, -16.0, -15.0, -10.0]) / 20) + 0j
    assert repeat_statistics([a]) is None
    max_std, drift = repeat_statistics([a, b])  # last column averages -25 dB: below the floor, ignored
    assert max_std == pytest.approx(1 / np.sqrt(2))  # sample standard deviation of ±0.5 dB
    assert drift == pytest.approx(0.0)
    max_std, drift = repeat_statistics([a, b], floor_db=-50)
    assert max_std == pytest.approx(30 / np.sqrt(2)) and drift == pytest.approx(7.5)
    assert repeat_statistics([a, np.zeros(4, dtype=complex)]) is None  # a perfect null has no dB value
    assert repeat_statistics([np.full(4, 0.001 + 0j)] * 2) is None  # everything below the floor


def test_capture_spread_seconds():
    assert capture_spread_seconds([]) is None
    assert capture_spread_seconds(["2026-01-01T00:00:00+00:00"]) == 0.0
    assert capture_spread_seconds(["2026-01-01T00:20:00+00:00", "2026-01-01T00:00:00+00:00"]) == 1200.0
    assert capture_spread_seconds(["2026-01-01T00:00:00+00:00", "yesterday"]) is None


# --- loss heuristics -------------------------------------------------------------------------------------
def test_loss_heuristic_thresholds():
    def flat(f):
        return np.full(f.shape, -15.0)

    assert loss_suspected(*db_trace(100e6, 1e9, flat))[0]  # 10:1 broadband match
    ok, msg = loss_suspected(*db_trace(1e9, 2.5e9, flat))
    assert ok and "2.5:1" in msg and "does not prove radiation" in msg
    assert not loss_suspected(*db_trace(1e9, 1.5e9, flat))[0]  # 1.5:1 is an ordinary antenna bandwidth

    def dip(f, fc):
        return -15 - 25 * np.exp(-(((f - fc) / 50e6) ** 2))

    assert not loss_suspected(*db_trace(1e9, 2.5e9, lambda f: dip(f, 1.7e9)))[0]  # distinct resonance
    assert loss_suspected(*db_trace(100e6, 1e9, lambda f: dip(f, 500e6)))[0]  # ≥ 4:1 is flagged regardless
    assert not loss_suspected(*db_trace(1e9, 2.5e9, lambda f: np.where(f < 1.7e9, -15.0, -3.0)))[0]
    assert loss_suspected(*db_trace(1e9, 2.5e9, flat, n=9)) == (False, "")


def test_no_visible_resonance():
    f = np.linspace(1e9, 2e9, 201)
    assert no_visible_resonance(f, np.full(201, 0.1 + 0j))
    dipped = -20 - 8 * np.exp(-(((f - 1.5e9) / 30e6) ** 2))
    assert not no_visible_resonance(f, 10 ** (dipped / 20) + 0j)  # an 8 dB dip is a resonance
    assert not no_visible_resonance(f, np.full(201, 0.5 + 0j))  # not matched at all
    assert not no_visible_resonance(f[:5], np.full(5, 0.1 + 0j))


def test_trace_quality_findings():
    f = np.linspace(1e9, 2e9, 401)
    rng = np.random.default_rng(7)
    smooth = 0.3 * np.exp(1j * f / 1e9)
    rep = assess_trace_quality(f, smooth + 0.03 * (rng.standard_normal(401) + 1j * rng.standard_normal(401)))
    assert rep.noise_sigma is not None and rep.noise_sigma > 0.01
    assert {x.code: x.severity for x in rep.findings}["noisy"] == "warn" and not rep.has_errors
    rep = assess_trace_quality(f, smooth, repeats=[smooth, smooth * 10 ** (-1 / 20)],
                               timestamps=["2026-01-01T00:00:00+00:00", "2026-01-01T00:30:00+00:00"],
                               provenance_keys=[("A", "SOLT"), ("B", "SOLT")])
    sev = {x.code: x.severity for x in rep.findings}
    assert sev["repeatability"] == "warn" and sev["stale_mix"] == "warn" and sev["mixed_provenance"] == "warn"
    assert rep.repeat_drift_db == pytest.approx(-1.0, abs=1e-6)
    rep = assess_trace_quality(f, smooth, repeats=[smooth, smooth], timestamps=["2026-01-01T00:00:00+00:00"] * 2,
                               provenance_keys=[("A", "SOLT")] * 2)
    sev = {x.code: x.severity for x in rep.findings}
    assert sev["repeatability"] == "info" and rep.repeat_max_std_db == 0.0
    assert "stale_mix" not in sev and "mixed_provenance" not in sev
    rep = assess_trace_quality(np.array([1e9, 1e9]), np.array([0.1, np.nan]))
    assert rep.has_errors and rep.noise_sigma is None and rep.ripple_pp_db is None
    f3 = np.linspace(1e9, 3e9, 801)
    rep = assess_trace_quality(f3, 0.9 * np.exp(-1j * f3 / 5e7) * (1 + 0.15 * np.cos(f3 / 5e6)))
    assert {x.code: x.severity for x in rep.findings}["ripple"] == "warn" and rep.ripple_pp_db > 1.5


def test_evidence_grades_and_gnss_warning(cat):
    f = np.linspace(2.3e9, 2.6e9, 601)
    stamp = "2026-01-01T00:00:00+00:00"
    t = TraceSegment("S11", f, np.full(601, 0.15 + 0j), stamp, "librevna-scpi")  # -16.5 dB: above the repeat floor
    cal = CalibrationMeta(gui_reported_type="SOLT_1", user_confirmed=True, cal_date="2026-01-01T00:00:00+00:00",
                          cal_start_hz=1e9, cal_stop_hz=3e9, reference_plane="cable_end")
    m = Measurement("t", AntennaInfo(), InstrumentInfo(), cal, None, [t], repeats=[[t]])
    a = assess(m, cat, ["ism24"])
    assert a.rating == MatchRating.GOOD and a.evidence == EvidenceGrade.A and a.calibration_issues == []
    warns = {fd.code for fd in a.quality.findings if fd.severity == "warn"}
    assert warns == {"no_resonance_in_span"}  # a flat -16.5 dB span is matched but shows no resonance
    m.repeats = []
    assert assess(m, cat, ["ism24"]).evidence == EvidenceGrade.B  # no repeat statistics: never grade A
    bad = TraceSegment("S11", f, np.where(f < 2.4e9, np.nan, 0.1) + 0j, stamp, "librevna-scpi")
    a = assess(Measurement("t", AntennaInfo(active_gnss=True), InstrumentInfo(), cal, None, [bad]), cat, ["ism24"])
    assert a.evidence == EvidenceGrade.D and a.quality.has_errors
    assert any("Active GNSS antenna" in w for w in a.warnings) and any("Non-finite" in w for w in a.warnings)
    empty = Measurement("t", AntennaInfo(), InstrumentInfo(), cal, None, [])
    a = assess(empty, cat, ["ism24"])
    assert a.rating == MatchRating.INSUFFICIENT and a.evidence == EvidenceGrade.D
    assert a.summary is None and a.band_results == {} and any(fd.code == "empty" for fd in a.quality.findings)
    assert band_resonance(empty, cat.by_id("ism24")) is None


# --- plain-language layer --------------------------------------------------------------------------------
def run_flat(cat, db, targets, survey=None, f=None):
    f = np.linspace(2.3e9, 2.6e9, 601) if f is None else f
    t = TraceSegment("S11", f, np.full(len(f), 10 ** (db / 20) + 0j), "2026-01-01T00:00:00+00:00", "simulation")
    m = Measurement("t", AntennaInfo(), InstrumentInfo(simulated=True), CalibrationMeta(), None, [t])
    a = assess(m, cat, targets, survey or [])
    return m, a


def test_headlines_follow_the_rating(cat):
    _, a = run_flat(cat, -2.0, ["ism24"])
    assert a.rating == MatchRating.POOR and headline(a, "Wi-Fi")[0] == "Poor match for Wi-Fi"
    _, a = run_flat(cat, -7.0, ["ism24"])
    assert a.rating == MatchRating.MARGINAL and headline(a, "Wi-Fi")[0] == "Borderline match for Wi-Fi"
    _, a = run_flat(cat, -15.0, ["wifi6_eu_lpi"])
    assert a.rating == MatchRating.INSUFFICIENT and headline(a, "Wi-Fi 6E") == (
        "Not enough data", "The target band was not fully measured.")
    _, a = run_flat(cat, -15.0, [], survey=["ism24", "wifi6_eu_lpi"])
    assert a.rating == MatchRating.SURVEY
    assert headline(a, "x")[0] == "Scan complete – well matched in 1 of 2 common bands"
    assert "well matched in 1 of 2 catalog bands" in a.headline
    assert a.shown_ids == ["ism24", "wifi6_eu_lpi"]
    _, a = run_flat(cat, -15.0, ["ism24"])
    assert a.rating == MatchRating.GOOD and "gain test" in headline(a, "Wi-Fi")[1]


def test_band_sentences_and_survey_explanation(cat):
    m, a = run_flat(cat, -15.0, [], survey=["ism24", "wifi6_eu_lpi"])
    assert band_sentence(cat.by_id("wifi6_eu_lpi"), a.band_results["wifi6_eu_lpi"]).endswith(
        "not fully measured – no statement possible.")
    s = band_sentence(cat.by_id("ism24"), a.band_results["ism24"])
    assert s.startswith("ISM 2.4 GHz: accepts at least 96 %")  # 96.84 % rounded down: a lower bound and "worst S11 -15.0 dB, VSWR 1.43." in s
    e = explain(m, a, cat, "survey")
    assert [p.id for p, _, _ in e["bands"]] == ["ism24"]  # insufficient bands are left out of a survey summary
    assert e["title"].startswith("Scan complete") and e["hints"] == []


def test_tuning_hints_at_the_edge_of_the_sweep(cat):
    f = np.linspace(2.2e9, 2.8e9, 601)
    for f0, side, advice in ((2.9e9, "above", "longer"), (2.1e9, "below", "shorter")):
        g = AntennaModel("t", "t", branches=(SeriesRLC(f0, 50.0, 6.0),)).gamma(f)
        t = TraceSegment("S11", f, g, "2026-01-01T00:00:00+00:00", "simulation")
        m = Measurement("t", AntennaInfo(), InstrumentInfo(simulated=True), CalibrationMeta(), None, [t])
        e = explain(m, assess(m, cat, ["ism24"]), cat, "Wi-Fi")
        assert e["title"].startswith("Poor match")
        assert len(e["hints"]) == 1
        assert "still falling at the end of the measured range" in e["hints"][0]
        assert f"lies {side} it" in e["hints"][0] and advice in e["hints"][0]


def test_centering_note_for_tight_lower_margin(cat):
    f = np.linspace(2.2e9, 2.8e9, 601)
    g = AntennaModel("t", "t", branches=(SeriesRLC(2.505e9, 50.0, 6.0),),).gamma(f)
    t = TraceSegment("S11", f, g, "2026-01-01T00:00:00+00:00", "simulation")
    m = Measurement("t", AntennaInfo(), InstrumentInfo(simulated=True), CalibrationMeta(), None, [t])
    a = assess(m, cat, ["ism24"])
    assert a.band_results["ism24"].conclusion == Conclusion.WELL_MATCHED
    e = explain(m, a, cat, "Wi-Fi")
    assert e["hints"] == [] and len(e["notes"]) == 1
    assert "MHz above the band centre" in e["notes"][0] and "widens the tight lower margin" in e["notes"][0]
    # centred but narrow: the dip is fine, both margins are small
    g = AntennaModel("t", "t", branches=(SeriesRLC(2.4418e9, 50.0, 13.0),)).gamma(f)
    t = TraceSegment("S11", f, g, "2026-01-01T00:00:00+00:00", "simulation")
    m = Measurement("t", AntennaInfo(), InstrumentInfo(simulated=True), CalibrationMeta(), None, [t])
    e = explain(m, assess(m, cat, ["ism24"]), cat, "Wi-Fi")
    assert e["title"].startswith("Good match") and len(e["notes"]) == 1
    assert e["notes"][0].startswith("ISM 2.4 GHz: good, but with little margin.")
