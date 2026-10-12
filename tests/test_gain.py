# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from antennafox.gain.comparative import (
    GainConvention,
    GainSetup,
    GainStatus,
    PatternCut,
    PatternPoint,
    ReferenceAntenna,
    S21Capture,
    estimate_gain,
    far_field_distance_m,
)

F = 2.44e9


def cap(role, level_db, jitter=0.0, seed=0):
    f = np.linspace(2.4e9, 2.5e9, 201)
    rng = np.random.default_rng(seed)
    lvl = level_db + jitter * rng.standard_normal()
    return S21Capture(role, f, np.full(201, 10 ** (lvl / 20) + 0j))


def good_reference(**kw):
    d = dict(name="Ref horn", source="Cal cert 1234", gain_table=[(2.3e9, 6.0), (2.6e9, 6.6)], tolerance_db=0.5,
             trusted=True)
    d.update(kw)
    return ReferenceAntenna(**d)


def good_setup(**kw):
    d = dict(frequency_hz=F, distance_m=3.0, largest_dimension_m=0.15, polarization_matched=True,
             same_position_confirmed=True)
    d.update(kw)
    return GainSetup(**d)


def test_relative_only_without_reference():
    r = estimate_gain(None, good_setup(), [cap("ref", -40)], [cap("dut", -37)])
    assert r.status == GainStatus.RELATIVE_ONLY
    assert r.relative_db == pytest.approx(3.0)
    assert r.absolute_dbi is None
    assert r.absolute_text() == "Not measurable with this setup"


def test_absolute_with_valid_reference():
    r = estimate_gain(good_reference(), good_setup(), [cap("ref", -40)], [cap("dut", -37)])
    assert r.status == GainStatus.ABSOLUTE
    g_ref = 6.0 + 0.6 * (F - 2.3e9) / 0.3e9
    assert r.absolute_dbi == pytest.approx(round(g_ref + 3.0, 1))
    assert r.absolute_expanded_db > r.relative_expanded_db  # reference tolerance included


def test_cable_correction():
    r = estimate_gain(None, good_setup(loss_dut_path_db=0.7, loss_ref_path_db=0.2), [cap("ref", -40)],
                      [cap("dut", -37)])
    assert r.relative_db == pytest.approx(3.5)


def test_untrusted_reference_unavailable():
    r = estimate_gain(good_reference(trusted=False), good_setup(), [cap("ref", -40)], [cap("dut", -37)])
    assert r.status == GainStatus.RELATIVE_ONLY
    assert r.absolute_text() == "Not measurable with this setup"
    assert any("trustworthy" in x for x in r.reasons)


def test_reference_out_of_characterized_range():
    ref = good_reference(gain_table=[(5e9, 8.0), (6e9, 8.0)])
    r = estimate_gain(ref, good_setup(), [cap("ref", -40)], [cap("dut", -37)])
    assert r.absolute_dbi is None
    assert any("extrapolation" in x for x in r.reasons)


def test_reference_uncertainty_contributes():
    lo = estimate_gain(good_reference(tolerance_db=0.2), good_setup(), [cap("ref", -40)], [cap("dut", -37)])
    hi = estimate_gain(good_reference(tolerance_db=2.0), good_setup(), [cap("ref", -40)], [cap("dut", -37)])
    assert hi.absolute_expanded_db > lo.absolute_expanded_db
    assert hi.relative_expanded_db == lo.relative_expanded_db


def test_near_field_refused():
    r = estimate_gain(good_reference(), good_setup(distance_m=0.2, largest_dimension_m=0.3),
                      [cap("ref", -40)], [cap("dut", -37)])
    assert r.status == GainStatus.RELATIVE_ONLY
    assert any("far-field" in x for x in r.reasons)


def test_polarization_and_position_gates():
    r = estimate_gain(good_reference(), good_setup(polarization_matched=False, same_position_confirmed=False),
                      [cap("ref", -40)], [cap("dut", -37)])
    assert r.absolute_dbi is None and len(r.reasons) == 2


def test_ieee_gain_requires_gamma():
    ref = good_reference(convention=GainConvention.IEEE_GAIN)
    r = estimate_gain(ref, good_setup(), [cap("ref", -40)], [cap("dut", -37)])
    assert r.absolute_dbi is None
    ref.gamma_table = [(2.3e9, 1 / 3 + 0j), (2.6e9, 1 / 3 + 0j)]
    r = estimate_gain(ref, good_setup(), [cap("ref", -40)], [cap("dut", -37)])
    mm = 10 * np.log10(1 - 1 / 9)
    g_ref = 6.0 + 0.6 * (F - 2.3e9) / 0.3e9
    assert r.absolute_dbi == pytest.approx(round(g_ref + mm + 3.0, 1))


def test_repeatability_from_repeats():
    refs = [cap("ref", -40, 0.1, s) for s in range(4)]
    duts = [cap("dut", -37, 0.1, s + 10) for s in range(4)]
    r = estimate_gain(good_reference(), good_setup(), refs, duts)
    assert not r.budget.repeatability_estimated
    assert r.repeatability == "good"
    r1 = estimate_gain(good_reference(), good_setup(), refs[:1], duts[:1])
    assert r1.budget.repeatability_estimated


def test_signal_in_noise_refused():
    r = estimate_gain(good_reference(), good_setup(), [cap("ref", -110)], [cap("dut", -107)])
    assert r.absolute_dbi is None


def test_no_data():
    r = estimate_gain(good_reference(), good_setup(frequency_hz=5e9), [cap("ref", -40)], [cap("dut", -37)])
    assert r.status == GainStatus.UNAVAILABLE


def test_far_field_formula():
    lam = 299_792_458.0 / 2.44e9
    assert far_field_distance_m(2.44e9, 0.5) == pytest.approx(2 * 0.25 / lam)
    assert far_field_distance_m(2.44e9, 0.01) == pytest.approx(3 * lam)


def test_pattern_cut_normalized():
    c = PatternCut("azimuth", F, "V", "TX horn",
                   [PatternPoint(a, -40 - 5 * np.sin(np.deg2rad(a)) ** 2) for a in range(0, 360, 15)])
    a, v = c.normalized()
    assert v.max() == 0
    s = c.summary()
    assert s["full_circle"] and s["step_deg"] == 15
