# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression data from a real LibreVNA (V1 Rev.B, GUI 1.6.5, OSL-calibrated port 1, 2026-10-11).

The same 2.4 GHz whip was swept bare and inside a plastic enclosure. Values were cross-checked against
LibreVNA-GUI's own trace readout (:VNA:TRAC:AT?) to within 0.01 dB. See docs/HARDWARE_VALIDATION.md.
"""

from pathlib import Path

import pytest

from antennafox.catalog.catalog import load_builtin
from antennafox.measurement.session import measurement_from_touchstone
from antennafox.rf.assessment import assess
from antennafox.rf.bands import Conclusion, matched_windows, resonances
from antennafox.rf.explain import band_resonance, explain

DATA = Path(__file__).parent / "data"
BANDS = ["ism24", "wifi24_eu", "ble", "bt_classic"]


@pytest.fixture(scope="module")
def cat():
    return load_builtin()


def _load(name):
    m = measurement_from_touchstone(DATA / name)
    f, g = m.merged("S11")
    assert len(f) == 1001 and f[0] == 2.0e9 and f[-1] == 3.0e9
    return m, f, g


def test_bare_whip_passes_with_tight_upper_margin(cat):
    m, f, g = _load("librevna_v1_whip24_bare_cal.s1p")
    a = assess(m, cat, BANDS)
    r = a.band_results["ism24"]
    assert r.conclusion == Conclusion.WELL_MATCHED
    assert -12.5 < r.worst_s11_db < -11.5 and abs(r.worst_freq_hz - 2.4835e9) < 2e6  # worst at the upper edge
    res = band_resonance(m, cat.by_id("ism24"))
    assert 2.33e9 < res.freq_hz < 2.40e9 and res.s11_db < -30
    (lo, hi), = [w for w in matched_windows(f, g) if w[0] < 2.4e9 < w[1]]
    assert 2.25e9 < lo < 2.28e9 and 2.51e9 < hi < 2.54e9
    e = explain(m, a, cat, "Wi-Fi / Bluetooth")
    assert e["title"].startswith("Good match") and e["hints"] == []
    assert "not centred" in e["notes"][0] and "eat into the tight upper margin" in e["notes"][0]


def test_enclosure_pulls_the_resonance_down_and_breaks_the_upper_edge(cat):
    bare, fb, gb = _load("librevna_v1_whip24_bare_cal.s1p")
    enc, fe, ge = _load("librevna_v1_whip24_enclosure_cal.s1p")
    a = assess(enc, cat, BANDS)
    r = a.band_results["ism24"]
    assert r.conclusion == Conclusion.MARGINAL and -7.0 < r.worst_s11_db < -6.0
    assert a.headline == "Marginal match in all 4 target bands"
    f_bare = resonances(fb, gb, 6.0)[0][0]
    f_enc = resonances(fe, ge, 6.0)[0][0]
    assert -150e6 < f_enc - f_bare < -100e6  # dielectric loading lowers the resonance
    e = explain(enc, a, cat, "Wi-Fi / Bluetooth")
    assert e["title"].startswith("Borderline") and any("below the band" in h for h in e["hints"])


def test_captures_are_real_calibrated_measurements(cat):
    for name in ("librevna_v1_whip24_bare_cal.s1p", "librevna_v1_whip24_enclosure_cal.s1p"):
        text = (DATA / name).read_text()
        assert "209D39773350" in text and "OSL_1" in text
        m, _, _ = _load(name)
        assert not m.simulated
