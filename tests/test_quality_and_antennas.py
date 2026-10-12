# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from antennafox.catalog.catalog import load_catalog
from antennafox.instrument.base import CancelToken
from antennafox.instrument.simulated import SimulatedBackend
from antennafox.instrument.simulated.models import MODELS, AntennaModel, SeriesRLC
from antennafox.measurement.model import (
    AntennaInfo,
    CalibrationMeta,
    CalibrationStatus,
    InstrumentInfo,
    Measurement,
    TraceSegment,
)
from antennafox.measurement.orchestrator import new_measurement, run_plan
from antennafox.measurement.presets import PRESETS, plan_for_preset
from antennafox.rf.assessment import GAIN_NOT_MEASURED, EvidenceGrade, MatchRating, assess
from antennafox.rf.bands import Conclusion, Coverage
from antennafox.rf.explain import (
    accepted_floor_percent,
    accepted_percent,
    band_resonance,
    explain,
    mismatch_ceil_db,
    mismatch_loss_db,
    reflected_percent,
)
from antennafox.rf.quality import loss_suspected, noise_sigma, validate_trace


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def measure(model, preset, repeats=1, cal=None):
    b = SimulatedBackend(model)
    b.connect()
    plan = plan_for_preset(preset, b.limits)
    tr, rep = run_plan(b, plan, CancelToken(), repeats=repeats)
    return new_measurement(model, AntennaInfo(name=model), b, cal or CalibrationMeta(), plan, tr, rep)


def run(model, preset, cat, **kw):
    m = measure(model, preset, **kw)
    return m, assess(m, cat, list(PRESETS[preset].targets), list(PRESETS[preset].survey))


def test_good_24(cat):
    _, a = run("wifi24_good", "wifi24", cat)
    assert a.rating == MatchRating.GOOD
    assert a.band_results["ism24"].conclusion == Conclusion.WELL_MATCHED
    assert a.gain_status == GAIN_NOT_MEASURED
    assert a.evidence == EvidenceGrade.SIM


def test_dualband(cat):
    _, a = run("dualband_24_5", "dualband", cat)
    assert a.band_results["ism24"].conclusion == Conclusion.WELL_MATCHED
    assert a.band_results["wifi5_eu_a"].conclusion == Conclusion.WELL_MATCHED


def test_868_narrowband(cat):
    _, a = run("lora868_narrow", "subghz_eu", cat)
    r = a.band_results
    assert r["meshtastic_eu868_longfast"].conclusion == Conclusion.WELL_MATCHED
    assert r["meshcore_eu_narrow"].conclusion == Conclusion.WELL_MATCHED
    assert r["srd433_eu"].conclusion == Conclusion.POOR
    assert a.rating == MatchRating.POOR  # 433 is a selected target of this preset


def test_lossy_but_matched_never_positive(cat):
    m, a = run("lossy_matched", "full", cat)
    assert a.rating == MatchRating.SUSPECT
    assert any(f.code == "loss_suspected" for f in a.quality.findings)
    assert a.gain_status == GAIN_NOT_MEASURED
    assert "loss suspected" in a.headline


def test_lossy_narrow_span_warns(cat):
    _, a = run("lossy_matched", "wifi24", cat)
    assert any(f.code == "no_resonance_in_span" for f in a.quality.findings)


def test_no_resonance(cat):
    _, a = run("no_resonance", "wifi24", cat)
    assert a.rating == MatchRating.POOR
    assert a.summary.windows == []


def test_6ghz_out_of_range(cat):
    m, a = run("wifi6e_out_of_range", "wifi5", cat)
    assert any("limited" in o for o in m.sweep_plan.omissions)
    r = a.band_results["wifi6_eu_lpi"]
    assert r.coverage == Coverage.PARTIAL
    assert r.conclusion == Conclusion.INSUFFICIENT
    assert m.span("S11")[1] <= 6e9


def test_survey_has_no_overall_verdict(cat):
    _, a = run("wifi24_good", "full", cat)
    assert a.rating == MatchRating.SURVEY
    assert a.target_ids == []
    assert a.band_results["ism24"].conclusion == Conclusion.WELL_MATCHED


def test_repeats_statistics(cat):
    _, a = run("wifi24_good", "wifi24", cat, repeats=3)
    assert a.quality.repeat_max_std_db is not None
    assert a.quality.repeat_max_std_db < 0.5


def test_evidence_grades_follow_calibration(cat):
    m = measure("wifi24_good", "wifi24")
    m.instrument.simulated = False
    m.traces = [type(t)(t.parameter, t.freq_hz, t.data, t.acquired_at, "librevna-scpi", t.label, t.provenance)
                for t in m.traces]
    a = assess(m, cat, ["ism24"])
    assert a.calibration_status == CalibrationStatus.NOT_VERIFIED
    assert a.evidence == EvidenceGrade.C
    m.calibration.user_confirmed = True
    assert assess(m, cat, ["ism24"]).evidence == EvidenceGrade.B


def test_validate_corrupted_records():
    f = np.array([1e9, 1e9, 2e9])
    codes = {x.code for x in validate_trace(f, np.array([0.1, np.nan, 1.5]), expected_points=4)}
    assert {"freq_order", "nonfinite", "incomplete"} <= codes
    codes = {x.code for x in validate_trace(np.array([1e9, 2e9]), np.array([0.1, 1.2 + 0j]))}
    assert "non_passive" in codes
    assert any(x.code == "shape" for x in validate_trace(np.array([1.0]), np.array([0j, 0j])))
    assert any(x.code == "empty" for x in validate_trace(np.array([]), np.array([])))


def test_noise_estimator():
    rng = np.random.default_rng(1)
    f = np.linspace(1e9, 2e9, 2001)
    g = 0.3 * np.exp(1j * f / 1e9) + 0.01 * (rng.standard_normal(2001) + 1j * rng.standard_normal(2001))
    assert noise_sigma(g) == pytest.approx(0.01, rel=0.15)


def test_loss_heuristic_not_triggered_by_resonant_antenna():
    f = np.linspace(100e3, 6e9, 4501)
    assert not loss_suspected(f, MODELS["dualband_24_5"].gamma(f))[0]
    assert loss_suspected(f, MODELS["lossy_matched"].gamma(f))[0]


def test_plain_language_explanation(cat):
    m, a = run("wifi24_good", "wifi24", cat)
    e = explain(m, a, cat, "Wi-Fi 2.4 GHz")
    assert e["title"] == "Good match for Wi-Fi 2.4 GHz"
    assert all("accepts at least" in s for _, _, s in e["bands"])
    assert e["hints"] == []
    assert reflected_percent(-10) == pytest.approx(10.0)
    assert accepted_percent(-10) == pytest.approx(90.0)
    assert mismatch_loss_db(-10) == pytest.approx(0.458, abs=1e-3)
    assert mismatch_loss_db(-3) == pytest.approx(3.02, abs=0.01)
    assert mismatch_loss_db(0) == float("inf")
    # bounds never overstate: '≥' rounds down, '≤' rounds up
    assert accepted_percent(-7.3) == pytest.approx(81.37, abs=0.01) and accepted_floor_percent(-7.3) == 81
    assert mismatch_loss_db(-7.3) == pytest.approx(0.895, abs=0.002) and mismatch_ceil_db(-7.3) == 0.9
    assert accepted_floor_percent(-6.4) == 77 and mismatch_ceil_db(-6.4) == 1.2  # 1.129 dB rounds up
    assert mismatch_ceil_db(-20) == 0.1 and accepted_floor_percent(-20) == 99
    assert band_resonance(m, cat.by_id("ism24")).inside_band


def test_centering_note_for_passing_band(cat):
    f = np.linspace(2.2e9, 2.8e9, 601)

    def run_model(f0, q):
        g = AntennaModel("t", "t", branches=(SeriesRLC(f0, 50.0, q),)).gamma(f)
        t = TraceSegment("S11", f, g, "2026-01-01T00:00:00+00:00", "simulation")
        m = Measurement("t", AntennaInfo(), InstrumentInfo(simulated=True), CalibrationMeta(), None, [t])
        return explain(m, assess(m, cat, ["ism24"]), cat, "Wi-Fi 2.4 GHz")

    e = run_model(2.44e9, 4.0)  # broad and centred: nothing to add
    assert e["title"].startswith("Good match") and e["hints"] == [] and e["notes"] == []
    e = run_model(2.38e9, 6.0)  # passes, but tuned low: dip outside the band, tight margin above it
    assert e["title"].startswith("Good match") and e["hints"] == []
    assert len(e["notes"]) == 1
    assert e["notes"][0].startswith("ISM 2.4 GHz") and "not centred" in e["notes"][0]
    assert "MHz below the band centre" in e["notes"][0]
    assert "eat into the tight upper margin" in e["notes"][0]  # a case lowers the resonance: the upper margin shrinks


def test_tuning_hint_direction(cat):
    f = np.linspace(2.2e9, 2.8e9, 601)
    for f0, word, advice in ((2.60e9, "above", "longer"), (2.28e9, "below", "shorter")):
        g = AntennaModel("t", "t", branches=(SeriesRLC(f0, 50.0, 25.0),)).gamma(f)
        t = TraceSegment("S11", f, g, "2026-01-01T00:00:00+00:00", "simulation")
        m = Measurement("t", AntennaInfo(), InstrumentInfo(simulated=True), CalibrationMeta(), None, [t])
        e = explain(m, assess(m, cat, ["ism24"]), cat, "Wi-Fi 2.4 GHz")
        assert len(e["hints"]) == 1 and word in e["hints"][0] and advice in e["hints"][0]
