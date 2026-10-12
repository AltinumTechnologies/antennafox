# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression tests for previously fixed defects."""

from pathlib import Path

import numpy as np

from antennafox.catalog.catalog import load_catalog
from antennafox.instrument.simulated.models import MODELS
from antennafox.measurement.model import (
    AntennaInfo,
    CalibrationMeta,
    InstrumentInfo,
    Measurement,
    TraceSegment,
)
from antennafox.measurement.presets import PRESETS
from antennafox.measurement.session import load_session, measurement_from_touchstone, save_session
from antennafox.rf.assessment import MatchRating, assess
from antennafox.rf.quality import repeat_statistics, ripple_pp_db


def test_session_csv_accepts_numpy_scalars(tmp_path):
    # numpy scalars must be written as plain numbers, not as "np.float64(...)"
    f = np.linspace(1e9, 2e9, 5)
    t = TraceSegment("S11", f, np.full(5, 0.1 + 0.2j), "2026-01-01T00:00:00+00:00", "simulation")
    m = Measurement("x", AntennaInfo(), InstrumentInfo(simulated=True), CalibrationMeta(), None, [t])
    m2, _, _ = load_session(save_session(tmp_path / "x", m))
    np.testing.assert_array_equal(m2.traces[0].freq_hz, f)


def test_ripple_detector_ignores_genuine_resonances():
    f = np.linspace(2.3e9, 2.6e9, 601)
    assert (ripple_pp_db(MODELS["wifi24_good"].gamma(f)) or 0.0) < 1.5


def test_ripple_detector_flags_standing_wave():
    f = np.linspace(1e9, 3e9, 801)
    # ~31 MHz period: standing wave of a ~3 m mismatched coax
    g = 0.9 * np.exp(-1j * f / 5e7) * (1 + 0.15 * np.cos(f / 5e6))
    assert ripple_pp_db(g) > 1.5


def test_repeat_statistics_ignore_deep_nulls():
    f = np.linspace(2.3e9, 2.6e9, 301)
    rng = np.random.default_rng(0)
    g = MODELS["wifi24_good"].gamma(f)
    sweeps = [g + 0.002 * (rng.standard_normal(301) + 1j * rng.standard_normal(301)) for _ in range(3)]
    max_std, _ = repeat_statistics(sweeps)
    assert max_std < 0.5


def test_preset_notes_are_not_survey_ids():
    # preset notes must never end up in the survey band list
    for p in PRESETS.values():
        assert all(" " not in pid for pid in p.survey)


def test_real_matched_load_is_loss_suspected():
    # LibreVNA V1 capture of a cable into the 50 Ω port 2: a non-radiating load must not pass as a good antenna
    m = measurement_from_touchstone(Path(__file__).parent / "data" / "librevna_v1_cable_into_port2_uncal.s1p")
    a = assess(m, load_catalog(), [], list(PRESETS["full"].survey))
    assert a.rating == MatchRating.SUSPECT
    assert any(f.code == "loss_suspected" for f in a.quality.findings)
