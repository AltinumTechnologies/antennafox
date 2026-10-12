# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
import numpy as np
import pytest

from antennafox.rf import rfmath as r


def test_matched_load():
    g = np.array([0j])
    assert r.vswr(g)[0] == pytest.approx(1.0)
    assert r.impedance(g)[0] == pytest.approx(50 + 0j)
    assert r.s11_db(g)[0] == -np.inf
    assert r.return_loss_db(g)[0] == np.inf
    assert r.mismatch_loss_db(g)[0] == pytest.approx(0.0)


@pytest.mark.parametrize(
    "z,gamma",
    [(100 + 0j, 1 / 3), (25 + 0j, -1 / 3), (50 + 50j, (50j) / (100 + 50j)), (0j, -1.0)],
)
def test_impedance_gamma_roundtrip(z, gamma):
    assert r.gamma_from_impedance(np.array([z]))[0] == pytest.approx(gamma)
    if abs(gamma) < 1:
        assert r.impedance(np.array([gamma]))[0] == pytest.approx(z)


def test_vswr_known_values():
    assert r.vswr(np.array([1 / 3]))[0] == pytest.approx(2.0)
    # -10 dB S11 corresponds to VSWR ≈ 1.925
    assert r.s11_db_to_vswr(-10.0) == pytest.approx(1.925, abs=1e-3)
    assert r.vswr_to_gamma(2.0) == pytest.approx(1 / 3)


def test_open_and_short_bounds():
    g = np.array([1 + 0j, -1 + 0j, 1.2 + 0j])
    v = r.vswr(g)
    assert np.all(np.isinf(v))
    z = r.impedance(g)
    assert np.isinf(z[0].real)  # open: singularity guarded, no warning
    assert z[1] == pytest.approx(0j)
    assert np.isinf(r.mismatch_loss_db(g)).all()


def test_near_unity():
    g = np.array([0.999999])
    assert r.vswr(g)[0] > 1e6
    assert np.isfinite(r.vswr(g)[0])


def test_return_loss_sign_convention():
    g = np.array([0.1])
    assert r.s11_db(g)[0] == pytest.approx(-20.0)
    assert r.return_loss_db(g)[0] == pytest.approx(20.0)
    assert r.reflected_power_fraction(g)[0] == pytest.approx(0.01)
    assert r.mismatch_loss_db(g)[0] == pytest.approx(-10 * np.log10(0.99))


def test_real_input_is_promoted_to_complex():
    assert r.impedance([0.0])[0] == pytest.approx(50 + 0j)


def test_wavelength():
    assert r.wavelength_m(299_792_458.0) == pytest.approx(1.0)
