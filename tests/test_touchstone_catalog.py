# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
import json

import numpy as np
import pytest

from antennafox.catalog.catalog import (
    CatalogError,
    load_catalog,
    make_user_profile,
    parse_catalog,
    save_user_profiles,
)
from antennafox.rf.touchstone import TouchstoneError, parse_touchstone, touchstone_text


def test_parse_ri_hz():
    t = parse_touchstone("! c\n# HZ S RI R 50\n1000 0.5 0\n2000 0 0.5\n", nports=1)
    assert t.freq_hz.tolist() == [1000, 2000]
    assert t.s["S11"][1] == pytest.approx(0.5j)


def test_parse_ma_ghz_default():
    t = parse_touchstone("# GHZ S MA R 50\n2.4 0.1 90\n", nports=1)
    assert t.freq_hz[0] == pytest.approx(2.4e9)
    assert t.s["S11"][0] == pytest.approx(0.1j)


def test_parse_db_mhz():
    t = parse_touchstone("# MHz S DB R 50\n100 -20 180\n", nports=1)
    assert t.s["S11"][0] == pytest.approx(-0.1 + 0j, abs=1e-12)


def test_defaults_without_option_line():
    t = parse_touchstone("1 0.5 0\n", nports=1)  # GHz, MA, 50 Ω
    assert t.freq_hz[0] == 1e9


def test_two_port_v1_order_and_wrapping():
    text = "# HZ S RI R 50\n1e9 0.1 0 0.9 0\n 0.8 0 0.2 0\n"
    t = parse_touchstone(text, nports=2)
    assert t.s["S11"][0] == 0.1
    assert t.s["S21"][0] == 0.9
    assert t.s["S12"][0] == 0.8
    assert t.s["S22"][0] == 0.2


@pytest.mark.parametrize("text,msg", [
    ("# HZ Z RI R 50\n1 0 0\n", "S-parameters"),
    ("# HZ S RI R 50\n2 0 0\n1 0 0\n", "increasing"),
    ("# HZ S RI R 50\n1 0\n", "multiple"),
    ("# HZ S RI R 0\n1 0 0\n", "impedance"),
    ("# HZ S XX R 50\n1 0 0\n", "unknown option"),
    ("# HZ S RI R 50\n1 a b\n", "non-numeric"),
])
def test_invalid(text, msg):
    with pytest.raises(TouchstoneError, match=msg):
        parse_touchstone(text, nports=1)


def test_unsupported_port_count():
    with pytest.raises(TouchstoneError):
        parse_touchstone("# HZ S RI R 50\n" + "1 " + "0 " * 18 + "\n", nports=3)


def test_roundtrip_and_sufficiency():
    f = np.linspace(1e9, 2e9, 11)
    s11 = 0.3 * np.exp(1j * np.linspace(0, 3, 11))
    t = parse_touchstone(touchstone_text(f, {"S11": s11}), nports=1)
    np.testing.assert_allclose(t.s["S11"], s11, rtol=1e-11)
    with pytest.raises(TouchstoneError):
        touchstone_text(f, {"S11": s11, "S21": s11})  # incomplete 2-port set


def test_v2_keywords():
    text = "[Version] 2.0\n# HZ S RI R 50\n[Number of Ports] 2\n[Two-Port Data Order] 12_21\n[Number of Frequencies] 1\n" \
           "[Network Data]\n1 0.1 0 0.8 0 0.9 0 0.2 0\n[End]\n"
    t = parse_touchstone(text)
    assert t.s["S12"][0] == 0.8 and t.s["S21"][0] == 0.9


def test_catalog_builtin_valid():
    cat = load_catalog()
    assert cat.version
    for p in cat.profiles:
        for a, b in p.intervals_hz:
            assert 0 < a < b
        assert p.label("en") and p.label("de")


def test_catalog_rejects_bad_schema():
    with pytest.raises(CatalogError):
        parse_catalog({"schema": "x", "profiles": []})
    with pytest.raises(CatalogError):
        parse_catalog({"schema": "antennafox.catalog/1", "profiles": [{"id": "a", "intervals_hz": [[2, 1]]}]})


def test_user_catalog_merge_and_collision(tmp_path):
    path = tmp_path / "user.json"
    save_user_profiles(path, [make_user_profile("lte20", "LTE band 20", 791e6, 862e6)])
    cat = load_catalog(path)
    assert cat.by_id("lte20").user_defined
    data = json.loads(path.read_text())
    data["profiles"][0]["id"] = "ism24"
    path.write_text(json.dumps(data))
    with pytest.raises(CatalogError):
        load_catalog(path)
