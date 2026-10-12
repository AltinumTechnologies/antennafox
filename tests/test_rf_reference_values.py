# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""RF conversions against independently derived reference values, singular inputs, and cases where a
mathematically correct number would still be physically misleading."""

from pathlib import Path

import numpy as np
import pytest

from antennafox.catalog.catalog import load_catalog
from antennafox.measurement.model import (
    AntennaInfo,
    CalibrationMeta,
    CalibrationStatus,
    InstrumentInfo,
    Measurement,
    TraceSegment,
)
from antennafox.measurement.session import measurement_from_touchstone
from antennafox.rf import rfmath as r
from antennafox.rf.assessment import EvidenceGrade, MatchRating, assess, point_values, summarize_trace
from antennafox.rf.explain import band_sentence, mismatch_loss_db, power_sentence
from antennafox.rf.smith import R_VALUES, X_VALUES, grid_lines, vswr_circle
from antennafox.rf.touchstone import read_touchstone

REF = Path(__file__).parent / "data" / "librevna_v1_cable_into_port2_uncal.s1p"


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


# --- textbook conversion tables -------------------------------------------------------------------------
@pytest.mark.parametrize("mag,return_loss_db,vswr", [
    (0.1, 20.0, 1.2222), (0.2, 13.9794, 1.5), (1 / 3, 9.5424, 2.0), (0.5, 6.0206, 3.0),
    (0.75, 2.4988, 7.0), (0.9, 0.9151, 19.0),
])
def test_reflection_magnitude_table(mag, return_loss_db, vswr):
    g = np.array([mag * np.exp(1j * 0.7)])  # phase must not matter for scalar quantities
    assert r.return_loss_db(g)[0] == pytest.approx(return_loss_db, abs=1e-4)
    assert r.s11_db(g)[0] == pytest.approx(-return_loss_db, abs=1e-4)
    assert r.vswr(g)[0] == pytest.approx(vswr, abs=1e-4)
    assert r.reflected_power_fraction(g)[0] == pytest.approx(mag**2)
    assert r.mismatch_loss_db(g)[0] == pytest.approx(-10 * np.log10(1 - mag**2))
    assert r.vswr_to_gamma(vswr) == pytest.approx(mag, abs=1e-4)
    assert r.s11_db_to_vswr(-return_loss_db) == pytest.approx(vswr, rel=1e-4)


@pytest.mark.parametrize("gamma,z", [
    (0.5, 150 + 0j), (-0.5, 50 / 3 + 0j), (1j, 50j), (-1j, -50j), (0.2 + 0.4j, 50 + 50j), (0j, 50 + 0j),
])
def test_impedance_table(gamma, z):
    assert r.impedance(np.array([gamma]))[0] == pytest.approx(z, abs=1e-9)
    assert r.gamma_from_impedance(np.array([z]))[0] == pytest.approx(gamma, abs=1e-12)


def test_reference_impedance_scales_linearly():
    assert r.impedance(np.array([0.5]), z0=75.0)[0] == pytest.approx(225 + 0j)
    assert r.gamma_from_impedance(np.array([225 + 0j]), z0=75.0)[0] == pytest.approx(0.5)


def test_scalar_helpers_and_boundaries():
    assert r.vswr_to_gamma(1.0) == 0.0
    assert r.vswr_to_gamma(1.5) == pytest.approx(0.2)
    assert r.vswr_to_gamma(float("inf")) == 1.0
    with pytest.raises(ValueError):
        r.vswr_to_gamma(0.999)
    assert r.s11_db_to_vswr(0.0) == np.inf
    np.testing.assert_allclose(r.db20(np.array([0.1, 1.0, 10.0])), [-20.0, 0.0, 20.0])
    assert r.db20(np.array([0.0]))[0] == -np.inf
    assert r.wavelength_m(1e9) == 0.299792458
    assert r.wavelength_m(2.45e9) == pytest.approx(0.12236, abs=1e-5)


def test_singular_inputs_stay_finite_or_explicitly_infinite():
    g = np.array([0j, 1 + 0j, -1 + 0j, 1.5 + 0j, 0.999999999999 + 0j])
    assert r.s11_db(g).tolist()[0] == -np.inf
    assert r.s11_db(g)[3] == pytest.approx(20 * np.log10(1.5))  # non-passive data is reported, not clipped
    v = r.vswr(g)
    assert v[0] == 1.0 and np.isinf(v[1]) and np.isinf(v[2]) and np.isinf(v[3]) and np.isinf(v[4])
    z = r.impedance(g)
    assert z[0] == 50 and np.isinf(z[1].real) and z[2] == 0 and z[3] == pytest.approx(-250 + 0j)
    assert np.isinf(r.impedance(np.array([1 + 1e-13j]))[0].real)  # within the unity guard band
    assert np.isfinite(r.impedance(np.array([1 + 1e-9j]))[0].real)  # just outside it: huge but finite
    ml = r.mismatch_loss_db(g)
    assert ml[0] == 0.0 and np.isinf(ml[1:4]).all()
    assert np.isfinite(ml[4]) and ml[4] > 100  # 2e-12 of the power still gets through
    assert np.isnan(r.s11_db(np.array([np.nan + 0j])))[0]  # NaN propagates rather than disappearing
    assert r.s11_db([]).shape == (0,) and r.vswr([]).shape == (0,)


# --- values of a real LibreVNA capture, derived by hand from the first data line ------------------------
def test_reference_capture_first_sample_values():
    t = read_touchstone(REF)
    g = t.s["S11"][:1]
    assert r.gamma_magnitude(g)[0] == pytest.approx(0.347294, abs=1e-6)
    assert r.s11_db(g)[0] == pytest.approx(-9.18604, abs=1e-5)
    assert r.vswr(g)[0] == pytest.approx(2.06417, abs=1e-5)
    assert r.impedance(g)[0] == pytest.approx(34.1797 + 26.2167j, abs=1e-4)
    pv = point_values(t.freq_hz, t.s["S11"], 100e3)
    assert pv is not None and pv["freq_hz"] == 100e3 and pv["vswr"] == pytest.approx(2.06417, abs=1e-5)
    assert pv["z"] == pytest.approx(34.1797 + 26.2167j, abs=1e-4)
    assert point_values(t.freq_hz, t.s["S11"], 7e9) is None  # outside the sweep: no extrapolation


def test_trace_summary_of_reference_capture():
    t = read_touchstone(REF)
    s = summarize_trace(t.freq_hz, t.s["S11"], -10.0)
    assert s is not None and s.n_points == 451 and (s.start_hz, s.stop_hz) == (100e3, 6e9)
    assert s.min_s11_db == pytest.approx(20 * np.log10(np.min(np.abs(t.s["S11"]))))
    assert all(lo < hi for lo, hi in s.windows) and all(np.isfinite(d) for _, d in s.resonances)
    assert summarize_trace(np.array([]), np.array([]), -10.0) is None


# --- correct numbers that would mislead without context ------------------------------------------------
def test_uncalibrated_import_is_graded_c_with_explicit_issues(cat):
    m = measurement_from_touchstone(REF)
    a = assess(m, cat, ["ism24"])
    assert a.calibration_status == CalibrationStatus.NOT_VERIFIED and a.evidence == EvidenceGrade.C
    joined = " ".join(a.calibration_issues)
    assert "no active calibration" in joined and "not confirmed" in joined and "Reference plane unknown" in joined
    m.calibration.user_confirmed = True  # the operator vouches for it, but the file still reports no correction
    a2 = assess(m, cat, ["ism24"])
    assert a2.evidence == EvidenceGrade.B and any("no active calibration" in i for i in a2.calibration_issues)


def test_non_passive_trace_is_flagged_and_never_passes(cat):
    f = np.linspace(2.3e9, 2.6e9, 601)
    t = TraceSegment("S11", f, np.full(601, 1.05 + 0j), "2026-01-01T00:00:00+00:00", "librevna-scpi")
    m = Measurement("t", AntennaInfo(), InstrumentInfo(), CalibrationMeta(), None, [t])
    a = assess(m, cat, ["ism24"])
    assert a.rating == MatchRating.POOR
    assert any(fd.code == "non_passive" and fd.severity == "warn" for fd in a.quality.findings)
    br = a.band_results["ism24"]
    assert br.max_vswr == np.inf and br.worst_s11_db == pytest.approx(20 * np.log10(1.05))
    assert "VSWR ∞" in band_sentence(cat.by_id("ism24"), br)


def test_power_wording_matches_the_numbers():
    assert power_sentence(-10.0) == "accepts at least 90 % of the power across the band (mismatch loss ≤ 0.5 dB)"
    assert power_sentence(-20.0).startswith("accepts at least 99 %")
    assert power_sentence(0.0).endswith("(mismatch loss ≤ ∞)")
    assert mismatch_loss_db(-20.0) == pytest.approx(0.04365, abs=1e-5)
    assert mismatch_loss_db(3.0) == float("inf")  # non-passive: no finite loss figure


# --- Smith chart geometry --------------------------------------------------------------------------------
def test_smith_grid_geometry():
    lines = grid_lines(n=60)
    kinds = [k for _, _, k in lines]
    assert kinds.count("unit") == 1
    assert kinds.count("r") == len(R_VALUES) - 1
    assert kinds.count("x") == 2 * len(X_VALUES) + 1
    ux, uy, _ = next(ln for ln in lines if ln[2] == "unit")
    np.testing.assert_allclose(np.hypot(ux, uy), 1.0, atol=1e-9)  # r = 0 maps onto the unit circle
    for (x, y, _), rv in zip([ln for ln in lines if ln[2] == "r"], R_VALUES[1:], strict=True):
        gamma = x + 1j * y
        z = (1 + gamma) / (1 - gamma)
        np.testing.assert_allclose(z.real, rv, atol=1e-6)  # every point of an r-circle has that resistance
        assert np.all(np.hypot(x, y) <= 1 + 1e-12)
    *xs, axis = [ln for ln in lines if ln[2] == "x"]
    for (x, y, _), xv in zip(xs, [v * s for v in X_VALUES for s in (1, -1)], strict=True):
        gamma = x + 1j * y
        z = (1 + gamma) / (1 - gamma)
        np.testing.assert_allclose(z.imag, xv, atol=1e-6)
        assert np.hypot(x[0], y[0]) == pytest.approx(1.0)  # every reactance arc starts on the unit circle
        assert np.sign(y[1]) == np.sign(xv)  # inductive above, capacitive below the real axis
    assert axis[0].tolist() == [-1.0, 1.0] and axis[1].tolist() == [0.0, 0.0]
    cx, cy = vswr_circle(3.0)
    np.testing.assert_allclose(np.hypot(cx, cy), 0.5)  # VSWR 3 ↔ |Γ| = 0.5
    assert (cx[0], cy[0]) == (0.5, 0.0) and cx[-1] == pytest.approx(0.5) and cy[-1] == pytest.approx(0.0)
    assert cy.max() == pytest.approx(0.5) and cy.min() == pytest.approx(-0.5)  # one full, closed circle
    assert vswr_circle(1.0)[0].max() == 0.0
