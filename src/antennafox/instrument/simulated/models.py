# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reproducible synthetic antenna impedance models for simulation mode and tests (not real antennas)."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ...rf.rfmath import gamma_from_impedance


@dataclass(frozen=True)
class SeriesRLC:
    f0_hz: float
    r_ohm: float
    q: float

    def z(self, f: np.ndarray) -> np.ndarray:
        # Series RLC with loaded-Q definition Q = ω0L/R
        w0 = 2 * np.pi * self.f0_hz
        L = self.q * self.r_ohm / w0
        C = 1.0 / (w0 * w0 * L)
        w = 2 * np.pi * f
        return self.r_ohm + 1j * (w * L - 1.0 / (w * C))


@dataclass(frozen=True)
class AntennaModel:
    key: str
    title: str
    branches: tuple[SeriesRLC, ...] = ()
    fixed_z: complex | None = None  # frequency-independent load (e.g. resistor)
    shunt_c_pf: float = 0.0
    line_length_m: float = 0.0  # coax between reference plane and antenna (velocity factor 0.7)
    line_loss_db_per_m_at_1ghz: float = 0.0
    noise: float = 0.002
    description: str = ""

    def impedance(self, f: np.ndarray) -> np.ndarray:
        f = np.asarray(f, dtype=float)
        if self.fixed_z is not None:
            y = np.full(f.shape, 1.0 / self.fixed_z, dtype=complex)
        else:
            y = np.zeros(f.shape, dtype=complex)
            for b in self.branches:
                y += 1.0 / b.z(f)
        if self.shunt_c_pf:
            y += 1j * 2 * np.pi * f * self.shunt_c_pf * 1e-12
        return 1.0 / y

    def gamma(self, f: np.ndarray, rng: np.random.Generator | None = None) -> np.ndarray:
        f = np.asarray(f, dtype=float)
        g = gamma_from_impedance(self.impedance(f))
        if self.line_length_m:
            beta = 2 * np.pi * f / (0.7 * 299_792_458.0)
            alpha_db = self.line_loss_db_per_m_at_1ghz * np.sqrt(f / 1e9) * self.line_length_m
            g = g * np.exp(-2j * beta * self.line_length_m) * 10 ** (-2 * alpha_db / 20)
        if rng is not None and self.noise:
            g = g + self.noise * (rng.standard_normal(f.shape) + 1j * rng.standard_normal(f.shape))
        return g


MODELS: dict[str, AntennaModel] = {
    m.key: m
    for m in [
        AntennaModel(
            "wifi24_good", "2.4 GHz whip (well matched)",
            branches=(SeriesRLC(2.44e9, 50.0, 6.0),),
            description="Single-band monopole, resonance at 2.44 GHz.",
        ),
        AntennaModel(
            "wifi24_detuned", "2.4 GHz whip, detuned (too short)",
            branches=(SeriesRLC(2.64e9, 50.0, 9.0),),
            description="Single-band monopole resonating above the band, e.g. a radiator cut too short.",
        ),
        AntennaModel(
            "dualband_24_5", "Dual-band 2.4/5 GHz",
            branches=(SeriesRLC(2.44e9, 48.0, 5.0), SeriesRLC(5.45e9, 52.0, 2.2)),
            description="Dual-band antenna with two resonances.",
        ),
        AntennaModel(
            "lora868_narrow", "868 MHz LoRa antenna (narrowband)",
            branches=(SeriesRLC(867.5e6, 50.0, 25.0),),
            description="Narrowband helix for 868 MHz.",
        ),
        AntennaModel(
            "lossy_matched", "Lossy but well matched",
            fixed_z=complex(47.0, 3.0), line_length_m=1.5, line_loss_db_per_m_at_1ghz=1.2,
            description="Near-50 Ω termination behind a lossy cable – radiates practically nothing.",
        ),
        AntennaModel(
            "no_resonance", "No resonance (too short)",
            branches=(SeriesRLC(9.5e9, 3.0, 40.0),),
            description="Electrically short radiator, strongly capacitive.",
        ),
        AntennaModel(
            "wifi6e_out_of_range", "6 GHz antenna (outside measurable range)",
            branches=(SeriesRLC(6.5e9, 50.0, 4.0),),
            description="Resonance at 6.5 GHz, above the LibreVNA V1 range.",
        ),
        AntennaModel(
            "gnss_l1_patch", "GNSS L1 patch (passive)",
            branches=(SeriesRLC(1575.42e6, 50.0, 30.0),),
            description="Passive patch antenna for L1/E1.",
        ),
        AntennaModel(
            "load_50", "50 Ω load (calibration check)",
            fixed_z=complex(50.0, 0.0), noise=0.001,
            description="Ideal termination for plausibility checks.",
        ),
    ]
}
