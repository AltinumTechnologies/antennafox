# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure reflection-coefficient math. All inputs are complex Γ = S11 (linear), never dB traces."""

from __future__ import annotations

import numpy as np

Z0_DEFAULT = 50.0
# |Γ| at or above this is treated as total reflection; avoids division blow-ups near the unit circle.
GAMMA_UNITY_EPS = 1e-12


def as_complex_array(gamma) -> np.ndarray:
    g = np.asarray(gamma)
    if not np.iscomplexobj(g):
        g = g.astype(complex)
    return g


def gamma_magnitude(gamma) -> np.ndarray:
    return np.abs(as_complex_array(gamma))


def s11_db(gamma) -> np.ndarray:
    """S11 in dB, 20·log10|Γ| (≤ 0 dB for passive loads). |Γ| = 0 → -inf."""
    mag = gamma_magnitude(gamma)
    with np.errstate(divide="ignore"):
        return 20.0 * np.log10(mag)


def return_loss_db(gamma) -> np.ndarray:
    """Positive return-loss convention: RL = -20·log10|Γ|."""
    return -s11_db(gamma)


def vswr(gamma) -> np.ndarray:
    """VSWR = (1+|Γ|)/(1-|Γ|); returns +inf for |Γ| ≥ 1 (non-passive or total reflection)."""
    mag = gamma_magnitude(gamma)
    out = np.full(mag.shape, np.inf, dtype=float)
    ok = mag < 1.0 - GAMMA_UNITY_EPS
    out[ok] = (1.0 + mag[ok]) / (1.0 - mag[ok])
    return out


def impedance(gamma, z0: float = Z0_DEFAULT) -> np.ndarray:
    """Z = Z0(1+Γ)/(1-Γ). Γ → 1 (open) yields complex inf instead of a division warning."""
    g = as_complex_array(gamma)
    denom = 1.0 - g
    out = np.full(g.shape, complex(np.inf, np.inf), dtype=complex)
    ok = np.abs(denom) > GAMMA_UNITY_EPS
    out[ok] = z0 * (1.0 + g[ok]) / denom[ok]
    return out


def gamma_from_impedance(z, z0: float = Z0_DEFAULT) -> np.ndarray:
    z = as_complex_array(z)
    return (z - z0) / (z + z0)


def reflected_power_fraction(gamma) -> np.ndarray:
    return gamma_magnitude(gamma) ** 2


def mismatch_loss_db(gamma) -> np.ndarray:
    """-10·log10(1-|Γ|²); +inf for |Γ| ≥ 1 where no power is delivered (or data is non-physical)."""
    p = reflected_power_fraction(gamma)
    out = np.full(p.shape, np.inf, dtype=float)
    ok = p < 1.0 - GAMMA_UNITY_EPS
    out[ok] = -10.0 * np.log10(1.0 - p[ok])
    return out


def vswr_to_gamma(v: float) -> float:
    if v < 1.0:
        raise ValueError("VSWR must be ≥ 1")
    if np.isinf(v):
        return 1.0
    return (v - 1.0) / (v + 1.0)


def s11_db_to_vswr(db: float) -> float:
    return float(vswr(np.array([10 ** (db / 20.0)]))[0])


def db20(x) -> np.ndarray:
    with np.errstate(divide="ignore"):
        return 20.0 * np.log10(np.abs(x))


def wavelength_m(freq_hz: float) -> float:
    return 299_792_458.0 / freq_hz
