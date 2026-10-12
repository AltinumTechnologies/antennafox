# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
import warnings

import numpy as np
import pytest

from antennafox.catalog.catalog import load_builtin, make_user_profile
from antennafox.rf.bands import (
    Conclusion,
    Coverage,
    DensityPolicy,
    Thresholds,
    evaluate_band,
    matched_windows,
    resonances,
)
from antennafox.rf.rfmath import s11_db


def flat(f, db):
    return np.full(len(f), 10 ** (db / 20) + 0j)


@pytest.fixture(scope="module")
def cat():
    return load_builtin()


def test_complete_coverage_well_matched(cat):
    p = cat.by_id("ism24")
    f = np.linspace(2.3e9, 2.6e9, 601)
    r = evaluate_band(p, f, flat(f, -15))
    assert r.coverage == Coverage.MEASURED
    assert r.conclusion == Conclusion.WELL_MATCHED
    assert r.matched_fraction == pytest.approx(1.0)
    assert r.worst_s11_db == pytest.approx(-15, abs=1e-6)


def test_absent_coverage(cat):
    p = cat.by_id("ism24")
    f = np.linspace(800e6, 900e6, 101)
    r = evaluate_band(p, f, flat(f, -20))
    assert r.coverage == Coverage.NOT_MEASURED
    assert r.conclusion == Conclusion.INSUFFICIENT
    assert r.worst_s11_db is None and r.max_vswr is None and r.matched_fraction is None
    assert r.n_points == 0 and r.verdict_code == "N/A"
    assert r.notes == ["Band lies outside the measured range."]


def test_partial_coverage_no_extrapolation(cat):
    p = cat.by_id("wifi6_eu_lpi")  # 5945–6425 MHz, instrument stops at 6 GHz
    f = np.linspace(5.5e9, 6.0e9, 501)
    r = evaluate_band(p, f, flat(f, -20))
    assert r.coverage == Coverage.PARTIAL
    assert r.conclusion == Conclusion.INSUFFICIENT
    assert r.covered_fraction == pytest.approx((6000 - 5945) / (6425 - 5945), rel=1e-3)


def test_sparse_sampling_is_insufficient(cat):
    p = cat.by_id("meshcore_eu_narrow")  # 62.5 kHz channel
    f = np.linspace(100e3, 6e9, 4501)  # ~1.33 MHz step
    r = evaluate_band(p, f, flat(f, -20))
    assert r.conclusion == Conclusion.INSUFFICIENT
    assert not r.density_ok


def test_dense_sampling_narrow_channel(cat):
    p = cat.by_id("meshcore_eu_narrow")
    f = np.linspace(860e6, 875e6, 1501)  # 10 kHz step
    r = evaluate_band(p, f, flat(f, -12))
    assert r.coverage == Coverage.MEASURED
    assert r.density_ok
    assert r.conclusion == Conclusion.WELL_MATCHED


def test_gap_inside_band_is_not_bridged(cat):
    p = cat.by_id("ism24")
    f = np.concatenate([np.linspace(2.39e9, 2.42e9, 61), np.linspace(2.47e9, 2.5e9, 61)])
    r = evaluate_band(p, f, flat(f, -20))
    assert r.coverage == Coverage.PARTIAL
    assert r.conclusion == Conclusion.INSUFFICIENT


def test_worst_case_not_single_minimum(cat):
    p = cat.by_id("ism24")
    f = np.linspace(2.3e9, 2.6e9, 601)
    db = np.where(f < 2.45e9, -25.0, -7.0)  # deep minimum but upper half poor
    r = evaluate_band(p, f, 10 ** (db / 20) + 0j)
    assert r.best_s11_db == pytest.approx(-25)
    assert r.worst_s11_db == pytest.approx(-7)
    assert r.conclusion == Conclusion.MARGINAL
    assert 0.5 < r.matched_fraction < 0.7


def test_poor(cat):
    p = cat.by_id("ism24")
    f = np.linspace(2.3e9, 2.6e9, 601)
    r = evaluate_band(p, f, flat(f, -2))
    assert r.conclusion == Conclusion.POOR
    assert r.verdict_code == "FAIL"


def test_split_band_requires_all_intervals(cat):
    p = cat.by_id("lorawan_eu868_default")  # three separate 125 kHz channels
    assert len(p.intervals_hz) == 3
    f = np.linspace(868.0e6, 868.4e6, 401)  # covers 868.1 and 868.3, not 868.5
    r = evaluate_band(p, f, flat(f, -15))
    assert r.coverage == Coverage.PARTIAL
    assert r.conclusion == Conclusion.INSUFFICIENT
    f2 = np.linspace(867.9e6, 868.7e6, 801)
    r2 = evaluate_band(p, f2, flat(f2, -15))
    assert r2.conclusion == Conclusion.WELL_MATCHED
    assert len(r2.intervals) == 3 and r2.n_points == sum(iv.n_points for iv in r2.intervals) > 0
    assert r2.covered_fraction == pytest.approx(1.0) and r2.matched_fraction == pytest.approx(1.0)


def test_region_specific_profiles_exist(cat):
    assert cat.by_id("ism915_us").region == "US"
    assert cat.by_id("srd868_eu").region == "EU"
    assert cat.by_id("gnss_l1").gnss


def test_custom_thresholds(cat):
    p = cat.by_id("ism24")
    f = np.linspace(2.3e9, 2.6e9, 601)
    r = evaluate_band(p, f, flat(f, -12), Thresholds(pass_s11_db=-14, warn_s11_db=-10))
    assert r.conclusion == Conclusion.MARGINAL


def test_density_policy_max_gap():
    d = DensityPolicy()
    assert d.max_gap(869.586e6, 869.649e6) == pytest.approx(0.001 * 869.6175e6, rel=1e-3)
    assert d.max_gap(5.15e9, 5.85e9) == pytest.approx(0.005 * 5.5e9)


def test_user_profile(cat):
    p = make_user_profile("u1", "My band", 1.0e9, 1.1e9)
    f = np.linspace(0.9e9, 1.2e9, 301)
    assert evaluate_band(p, f, flat(f, -11)).conclusion == Conclusion.WELL_MATCHED


def test_matched_windows():
    f = np.linspace(1e9, 2e9, 101)
    db = np.where((f > 1.3e9) & (f < 1.5e9), -20.0, -3.0)
    w = matched_windows(f, 10 ** (db / 20) + 0j, -10)
    assert len(w) == 1
    assert 1.29e9 < w[0][0] < 1.31e9
    assert 1.49e9 < w[0][1] < 1.51e9


def test_unsorted_frequencies_rejected(cat):
    with pytest.raises(ValueError):
        evaluate_band(cat.by_id("ism24"), np.array([2.5e9, 2.4e9]), np.array([0j, 0j]))
    with pytest.raises(ValueError):
        evaluate_band(cat.by_id("ism24"), np.array([2.4e9, 2.4e9]), np.array([0j, 0j]))  # duplicates too
    r = evaluate_band(cat.by_id("ism24"), np.array([]), np.array([], dtype=complex))
    assert r.coverage == Coverage.NOT_MEASURED and r.n_points == 0


# --- exact values and boundaries -----------------------------------------------------------------------
def test_matched_fraction_is_interpolated_exactly():
    # S11 ramps linearly in dB from -20 dB at 1.0 GHz to 0 dB at 1.1 GHz: -10 dB is crossed at the band centre
    p = make_user_profile("u", "ramp", 1.0e9, 1.1e9)
    f = np.linspace(0.95e9, 1.15e9, 2001)
    db = -20 + 200 * (f - 1.0e9) / 1e9
    r = evaluate_band(p, f, 10 ** (db / 20) + 0j)
    assert r.coverage == Coverage.MEASURED and r.density_ok
    assert r.matched_fraction == pytest.approx(0.5, abs=1e-3)
    assert r.worst_s11_db == pytest.approx(0.0, abs=1e-6) and r.worst_freq_hz == pytest.approx(1.1e9)
    assert r.best_s11_db == pytest.approx(-20.0, abs=1e-6)
    assert r.median_s11_db == pytest.approx(-10.0, abs=0.05)
    assert r.max_vswr == np.inf  # |Γ| = 1 at the upper band edge
    assert r.conclusion == Conclusion.POOR


def test_crossing_interpolation_on_a_coarse_grid():
    # 4 MHz steps make the interpolated crossing segment matter: -10 dB is reached at 1.525 GHz, mid-segment
    p = make_user_profile("u", "ramp", 1.0e9, 2.0e9)
    f = np.linspace(1.0e9, 2.0e9, 251)
    rising = -20.5 + 20 * (f - 1.0e9) / 1e9
    assert evaluate_band(p, f, 10 ** (rising / 20) + 0j).matched_fraction == pytest.approx(0.525, abs=1e-6)
    falling = -0.5 - 20 * (f - 1.0e9) / 1e9
    assert evaluate_band(p, f, 10 ** (falling / 20) + 0j).matched_fraction == pytest.approx(0.525, abs=1e-6)


def test_marginal_fraction_rule(cat):
    p = cat.by_id("ism24")
    f = np.linspace(2.3e9, 2.6e9, 3001)
    a, b = p.span
    for frac, expected in ((0.9, Conclusion.MARGINAL), (0.5, Conclusion.POOR)):
        db = np.where(f < a + frac * (b - a), -20.0, -3.0)
        r = evaluate_band(p, f, 10 ** (db / 20) + 0j)
        assert r.worst_s11_db == pytest.approx(-3.0)
        assert r.matched_fraction == pytest.approx(frac, abs=0.01)
        assert r.conclusion == expected
    r = evaluate_band(p, f, 10 ** (np.where(f < a + 0.9 * (b - a), -20.0, -3.0) / 20) + 0j,
                      Thresholds(marginal_fraction=0.95))
    assert r.conclusion == Conclusion.POOR


def test_conclusion_thresholds_are_inclusive(cat):
    p = cat.by_id("ism24")
    f = np.linspace(2.3e9, 2.6e9, 601)
    g = np.full(len(f), 0.3 + 0j)
    exact = float(s11_db(g)[0])  # ≈ -10.46 dB, bit-identical to what evaluate_band computes
    assert evaluate_band(p, f, g, Thresholds(pass_s11_db=exact)).conclusion == Conclusion.WELL_MATCHED
    stricter = exact - 1e-9
    assert evaluate_band(p, f, g, Thresholds(pass_s11_db=stricter, warn_s11_db=exact)).conclusion == \
        Conclusion.MARGINAL
    assert evaluate_band(p, f, g, Thresholds(pass_s11_db=stricter, warn_s11_db=stricter)).conclusion == \
        Conclusion.POOR


def test_interval_statistics_and_point_counts():
    p = make_user_profile("u", "u", 1.0e9, 1.1e9)
    f = 1e9 + 1e6 * np.arange(-50, 151)  # 1 MHz grid with samples exactly on both band edges
    r = evaluate_band(p, f, np.full(len(f), 0.2 + 0j))
    (iv,) = r.intervals
    assert (iv.start_hz, iv.stop_hz) == (1.0e9, 1.1e9)
    assert iv.n_points == 101 and r.n_points == 101
    assert iv.covered_hz == pytest.approx(100e6)
    assert iv.max_gap_hz == pytest.approx(1e6)
    assert iv.allowed_gap_hz == pytest.approx(DensityPolicy().max_gap(1.0e9, 1.1e9))
    assert r.median_s11_db == pytest.approx(-13.979, abs=1e-3) == r.worst_s11_db == r.best_s11_db
    assert r.max_vswr == pytest.approx(1.5)
    assert r.verdict_code == "PASS" and r.notes == []


def test_sparse_in_band_samples_still_report_statistics():
    # three isolated points inside the band, spaced far wider than the density policy allows
    p = make_user_profile("u", "u", 1.0e9, 1.1e9)
    f = np.array([0.9e9, 1.02e9, 1.05e9, 1.08e9, 1.2e9])
    db = np.array([-5.0, -12.0, -20.0, -15.0, -5.0])
    r = evaluate_band(p, f, 10 ** (db / 20) + 0j)
    assert r.coverage == Coverage.PARTIAL and r.conclusion == Conclusion.INSUFFICIENT
    assert r.covered_fraction == 0.0 and r.n_points == 3 and r.matched_fraction is None
    assert (r.worst_s11_db, r.worst_freq_hz) == (pytest.approx(-12.0), 1.02e9)
    assert r.best_s11_db == pytest.approx(-20.0) and r.median_s11_db == pytest.approx(-15.0)
    assert r.max_vswr == pytest.approx(1.6709, abs=1e-3)
    assert r.notes == ["Only 0% of the band measured with sufficient sample density."]


def test_bracketed_narrow_band_without_in_band_points_is_insufficient():
    p = make_user_profile("u", "u", 1.0000e9, 1.0005e9)  # 500 kHz wide
    f = np.array([0.9990e9, 0.9999e9, 1.0006e9, 1.0013e9])  # 0.7 MHz spacing brackets the band, no sample inside
    r = evaluate_band(p, f, np.full(4, 0.1 + 0j))
    assert r.coverage == Coverage.MEASURED and r.covered_fraction == pytest.approx(1.0)
    assert r.n_points == 0 and not r.density_ok
    assert r.conclusion == Conclusion.INSUFFICIENT and "density too low" in r.notes[0]
    assert r.worst_s11_db == pytest.approx(-20.0)  # interpolated edge values are still reported


def test_perfectly_matched_sample_does_not_break_windows_or_fractions():
    # |Γ| = 0 gives S11 = -inf dB; the dB-linear edge interpolation must stay finite and warning-free
    f = np.linspace(1e9, 2e9, 1001)
    g = np.where((f > 1.3e9) & (f < 1.5e9), 0.1, 0.5) + 0j
    g[499] = 0  # perfect sample right next to the upper window edge
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        w = matched_windows(f, g, -10.0)
        r = evaluate_band(make_user_profile("u", "u", 1.2e9, 1.8e9), f, g)
    assert len(w) == 1 and 1.29e9 < w[0][0] < 1.31e9 and 1.49e9 < w[0][1] < 1.51e9
    assert r.matched_fraction is not None and 0.3 < r.matched_fraction < 0.35
    assert r.best_s11_db == -np.inf and np.isfinite(r.median_s11_db)
    lone = np.full(1001, 0.5 + 0j)
    lone[500] = 0
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        (w,) = matched_windows(f, lone, -10.0)
    assert w[0] < 1.5e9 < w[1] and w[1] - w[0] < 2e6  # a lone perfect point is a window narrower than one step


def test_matched_windows_interpolation_gaps_and_edges():
    f = np.array([1000, 1100, 1200, 1300, 1400, 1500, 1600, 1700]) * 1e6
    db = np.array([-20.0, -20.0, -3.0, -3.0, -20.0, -20.0, -3.0, -20.0])
    w = matched_windows(f, 10 ** (db / 20) + 0j, -10.0)
    t = (-10 + 20) / (-3 + 20)  # -10 dB lies 10/17 of the way from a -20 dB sample to a -3 dB one
    assert len(w) == 3
    assert w[0] == pytest.approx((1.0e9, 1.1e9 + t * 0.1e9))  # starts at the first sample
    assert w[1] == pytest.approx((1.4e9 - t * 0.1e9, 1.5e9 + t * 0.1e9))
    assert w[2] == pytest.approx((1.7e9 - t * 0.1e9, 1.7e9))  # runs into the end of the sweep
    f2 = np.array([1000, 1100, 1200, 1650, 1750, 1850]) * 1e6  # 450 MHz gap > 4 × the 100 MHz median step
    g2 = np.full(6, 0.1 + 0j)
    assert matched_windows(f2, g2, -10.0) == [(1.0e9, 1.2e9), (1.65e9, 1.85e9)]  # a gap is never bridged
    assert matched_windows(f2, g2, -10.0, max_gap_hz=1e9) == [(1.0e9, 1.85e9)]
    assert matched_windows(f2[:1], g2[:1], -10.0) == []
    assert matched_windows(f2, np.full(6, 0.5 + 0j), -10.0) == []


def test_resonances_detects_distinct_dips():
    f = np.linspace(1e9, 3e9, 2001)
    db = -3 - 17 * np.exp(-(((f - 1.5e9) / 30e6) ** 2)) - 5 * np.exp(-(((f - 2.5e9) / 30e6) ** 2))
    g = 10 ** (db / 20) + 0j
    res = resonances(f, g)
    assert [round(x / 1e6) for x, _ in res] == [1500, 2500]
    assert res[0][1] == pytest.approx(-20.0, abs=0.01) and res[1][1] == pytest.approx(-8.0, abs=0.01)
    assert [round(x / 1e6) for x, _ in resonances(f, g, min_depth_db=6.0)] == [1500]
    assert resonances(f[:2], g[:2]) == []
    g[750] = 0  # an exact null is still located
    assert [round(x / 1e6) for x, _ in resonances(f, g)] == [1500, 1750, 2500]
