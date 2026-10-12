# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Smith chart grid geometry (normalized to Z0) shared by the Qt view and report charts."""

from __future__ import annotations

import numpy as np

R_VALUES = (0.0, 0.2, 0.5, 1.0, 2.0, 5.0)
X_VALUES = (0.2, 0.5, 1.0, 2.0, 5.0)


def _gamma(z: np.ndarray) -> np.ndarray:
    return (z - 1) / (z + 1)


def grid_lines(n: int = 400) -> list[tuple[np.ndarray, np.ndarray, str]]:
    """Polylines (re, im, kind) for constant-r circles and constant-x arcs."""
    lines: list[tuple[np.ndarray, np.ndarray, str]] = []
    x = np.concatenate([-np.logspace(3, -3, n), [0.0], np.logspace(-3, 3, n)])
    for r in R_VALUES:
        g = _gamma(r + 1j * x)
        lines.append((np.real(g), np.imag(g), "unit" if r == 0 else "r"))
    rr = np.concatenate([[0.0], np.logspace(-3, 3, n)])
    for xv in X_VALUES:
        for s in (1, -1):
            g = _gamma(rr + 1j * s * xv)
            lines.append((np.real(g), np.imag(g), "x"))
    lines.append((np.array([-1.0, 1.0]), np.array([0.0, 0.0]), "x"))
    return lines


def vswr_circle(v: float, n: int = 361) -> tuple[np.ndarray, np.ndarray]:
    rho = (v - 1) / (v + 1)
    t = np.linspace(0, 2 * np.pi, n)
    return rho * np.cos(t), rho * np.sin(t)
