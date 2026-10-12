# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Touchstone reader/writer: formats, units, v2 keywords, malformed files and metadata preservation.
The reference fixture was written by LibreVNA-GUI, not by AntennaFox."""

from pathlib import Path

import numpy as np
import pytest

from antennafox.measurement.model import CalibrationStatus
from antennafox.measurement.session import SessionError, measurement_from_touchstone
from antennafox.reports.export import export_touchstone
from antennafox.rf.touchstone import TouchstoneError, parse_touchstone, read_touchstone, touchstone_text

REF = Path(__file__).parent / "data" / "librevna_v1_cable_into_port2_uncal.s1p"


# --- representations and units ---------------------------------------------------------------------------
def test_db_format_magnitude_and_phase():
    t = parse_touchstone("# MHZ S DB R 50\n100 -20 90\n200 0 -90\n300 -6.0206 45\n", nports=1)
    assert t.s["S11"][0] == pytest.approx(0.1j, abs=1e-12)
    assert t.s["S11"][1] == pytest.approx(-1j, abs=1e-12)
    assert t.s["S11"][2] == pytest.approx(0.5 * np.exp(1j * np.pi / 4), abs=1e-5)
    assert t.source_format == "DB" and t.z0 == 50.0 and t.nports == 1


def test_ma_format_phase_sign():
    t = parse_touchstone("# GHZ S MA R 50\n1 0.5 -90\n2 0.5 180\n", nports=1)
    assert t.s["S11"][0] == pytest.approx(-0.5j, abs=1e-12)
    assert t.s["S11"][1] == pytest.approx(-0.5 + 0j, abs=1e-12)
    assert t.source_format == "MA"


@pytest.mark.parametrize("unit,scale", [("HZ", 1), ("KHZ", 1e3), ("MHZ", 1e6), ("GHZ", 1e9), ("khz", 1e3), ("Hz", 1)])
def test_frequency_units(unit, scale):
    t = parse_touchstone(f"# {unit} S RI R 50\n2.5 0 0\n", nports=1)
    assert t.freq_hz[0] == pytest.approx(2.5 * scale)


def test_option_line_is_case_and_order_insensitive():
    t = parse_touchstone("# ri s r 75 mhz\n1 0.1 0.2\n", nports=1)
    assert t.freq_hz[0] == 1e6 and t.z0 == 75.0 and t.s["S11"][0] == 0.1 + 0.2j
    t = parse_touchstone("#HZ S RI R 0.5\n1 0.1 0.2\n", nports=1)  # no space after '#', unusual but valid Z0
    assert t.freq_hz[0] == 1 and t.z0 == 0.5


@pytest.mark.parametrize("param", ["Y", "Z", "H", "G"])
def test_non_s_parameter_files_are_named_in_the_error(param):
    with pytest.raises(TouchstoneError, match=f"contains {param}-parameters"):
        parse_touchstone(f"# HZ {param} RI R 50\n1 0 0\n", nports=1)


def test_only_the_first_option_line_counts():
    t = parse_touchstone("# HZ S RI R 50\n# GHZ S MA R 75\n1000 0.5 0\n", nports=1)
    assert t.freq_hz[0] == 1000 and t.z0 == 50 and t.s["S11"][0] == 0.5


def test_comments_kept_and_inline_comments_stripped():
    text = "! header one\n# HZ S RI R 50 ! trailing\n1000 0.5 0 ! point ! comment\n!\n2000, 0, 0.5\n"
    t = parse_touchstone(text, nports=1)
    assert t.comments == ["header one", "trailing", "point ! comment"]
    assert t.freq_hz.tolist() == [1000, 2000] and t.s["S11"][1] == 0.5j  # comma separators are accepted


def test_two_port_v1_order_across_wrapped_lines():
    text = "# MHZ S MA R 50\n1 0.1 0 0.9 0\n0.8 0 0.2 0\n2 0.1 0 0.9 0 0.8 0 0.2 0\n"
    t = parse_touchstone(text, nports=2)
    assert len(t.freq_hz) == 2
    assert (t.s["S11"][0], t.s["S21"][0], t.s["S12"][0], t.s["S22"][0]) == (0.1, 0.9, 0.8, 0.2)


# --- Touchstone 2.0 keywords -----------------------------------------------------------------------------
def test_v2_reference_matrix_format_and_end():
    text = ("[Version] 2.0\n# HZ S RI\n[Number of Ports] 1\n[Reference] 75\n[Matrix Format] Full\n"
            "[Number of Frequencies] 2\n[Network Data]\n1 0.5 0\n2 0 0.5\n[End]\n3 1 1\n")
    t = parse_touchstone(text)
    assert t.nports == 1 and t.z0 == 75.0
    assert t.freq_hz.tolist() == [1, 2]  # data after [End] is ignored
    same = text.replace("[Reference] 75", "[Reference] 75 75").replace("[Number of Ports] 1", "[Number of Ports] 2")
    same = same.replace("1 0.5 0\n2 0 0.5\n", "1 0.5 0 0 0 0 0 0 0\n")
    assert parse_touchstone(same).z0 == 75.0  # identical per-port references are fine


def test_v2_two_port_data_orders():
    data = "1 0.1 0 0.8 0 0.9 0 0.2 0\n"
    head = "[Version] 2.0\n# HZ S RI R 50\n[Number of Ports] 2\n"
    t = parse_touchstone(head + "[Network Data]\n" + data)  # default order 21_12
    assert (t.s["S11"][0], t.s["S21"][0], t.s["S12"][0], t.s["S22"][0]) == (0.1, 0.8, 0.9, 0.2)
    t = parse_touchstone(head + "[Two-Port Data Order] 12_21\n[Network Data]\n" + data)
    assert (t.s["S11"][0], t.s["S12"][0], t.s["S21"][0], t.s["S22"][0]) == (0.1, 0.8, 0.9, 0.2)
    assert t.nports == 2 and set(t.s) == {"S11", "S12", "S21", "S22"}


# --- malformed and unsupported input ---------------------------------------------------------------------
@pytest.mark.parametrize("text,msg", [
    ("[Version] 1.0\n# HZ S RI R 50\n1 0 0\n", "version"),
    ("[Version] 2.0\n# HZ S RI\n[Number of Ports] 2\n[Reference] 50 75\n[Network Data]\n1 0 0 0 0 0 0 0 0\n", "per-port"),
    ("[Version] 2.0\n# HZ S RI\n[Number of Ports] 2\n[Matrix Format] Lower\n[Network Data]\n1 0 0 0 0 0 0 0 0\n",
     "matrix"),
    ("# HZ S RI R\n1 0 0\n", "R without value"),
    ("# HZ S RI R 50\n1 0 0\n1 0 0\n", "increasing"),
    ("# HZ S RI R 50\n-1 0 0\n", "negative"),
    ("# HZ S RI R 50\n1 nan 0\n", "non-finite"),
    ("# HZ S RI R 50\n1 inf 0\n", "non-finite"),
    ("# HZ S RI R -50\n1 0 0\n", "impedance"),
    ("# HZ S RI R 50\n", "multiple"),
    ("# HZ S RI R 50\n1 0 0 0\n", "multiple"),
    ("# HZ Y RI R 50\n1 0 0\n", "S-parameters"),
    ("[Version] 3.0\n# HZ S RI R 50\n1 0 0\n", "version"),
])
def test_rejected_inputs(text, msg):
    with pytest.raises(TouchstoneError, match=msg):
        parse_touchstone(text, nports=1)


def test_port_count_must_be_known():
    with pytest.raises(TouchstoneError, match="ports unknown"):
        parse_touchstone("1 0 0\n")
    with pytest.raises(TouchstoneError, match="3-port"):
        parse_touchstone("[Version] 2.0\n# HZ S RI\n[Number of Ports] 3\n[Network Data]\n" + "1 " + "0 " * 18 + "\n")


def test_read_touchstone_takes_port_count_from_extension(tmp_path):
    p = tmp_path / "two.S2P"
    p.write_text("# HZ S RI R 50\n1 0.1 0 0.9 0 0.8 0 0.2 0\n")
    t = read_touchstone(p)
    assert t.nports == 2 and t.s["S21"][0] == 0.9
    q = tmp_path / "data.txt"
    q.write_text("# HZ S RI R 50\n1 0.1 0\n")
    with pytest.raises(TouchstoneError, match="ports unknown"):
        read_touchstone(q)
    bad = tmp_path / "bad.s1p"
    bad.write_bytes(b"# HZ S RI R 50\n1 0.1 \xff\n")  # undecodable byte: reported as a data error, not a crash
    with pytest.raises(TouchstoneError, match="non-numeric"):
        read_touchstone(bad)


# --- writer ----------------------------------------------------------------------------------------------
def test_two_port_writer_roundtrip_and_header():
    f = np.array([1e9, 2e9, 3e9])
    rng = np.random.default_rng(3)
    s = {k: 0.3 * (rng.standard_normal(3) + 1j * rng.standard_normal(3)) for k in ("S11", "S21", "S12", "S22")}
    text = touchstone_text(f, s, z0=75.0, comments=["a", "b"])
    assert text.splitlines()[:3] == ["! a", "! b", "# HZ S RI R 75"]
    t = parse_touchstone(text, nports=2)
    for k in s:
        np.testing.assert_allclose(t.s[k], s[k], rtol=1e-11)
    assert t.z0 == 75.0 and t.comments == ["a", "b"] and t.source_format == "RI"
    assert t.freq_hz.tolist() == [1e9, 2e9, 3e9]


def test_writer_refuses_inconsistent_data():
    f = np.array([1e9, 2e9, 3e9])
    with pytest.raises(TouchstoneError, match="frequency grid"):
        touchstone_text(f, {"S11": np.zeros(2, dtype=complex)})
    with pytest.raises(TouchstoneError, match="requires S11"):
        touchstone_text(f, {"S21": np.zeros(3, dtype=complex)})
    with pytest.raises(TouchstoneError, match="requires S11"):
        touchstone_text(f, {"S11": np.zeros(3, dtype=complex), "S22": np.zeros(3, dtype=complex)})


# --- independent reference fixture (LibreVNA-GUI export) -------------------------------------------------
def test_reference_capture_parses_with_expected_grid_and_values():
    t = read_touchstone(REF)
    assert t.nports == 1 and t.z0 == 50.0 and t.source_format == "RI"
    assert len(t.freq_hz) == 451 and t.freq_hz[0] == 100e3 and t.freq_hz[-1] == 6e9
    assert np.all(np.diff(t.freq_hz) > 0)
    assert t.s["S11"][0] == pytest.approx(-0.0829007 + 0.337255j, abs=1e-9)
    assert t.s["S11"][-1] == pytest.approx(0.0254231 - 0.246329j, abs=1e-9)
    assert any("no calibration active" in c for c in t.comments)
    assert np.all(np.abs(t.s["S11"]) < 1.0)


def test_import_preserves_metadata_and_marks_calibration_unknown():
    m = measurement_from_touchstone(REF)
    (t,) = m.traces
    assert t.parameter == "S11" and t.label == REF.name and t.source == "touchstone-import" and t.z0 == 50.0
    assert t.provenance["format"] == "RI" and t.provenance["z0"] == 50.0
    assert t.provenance["comments"][0].startswith("LibreVNA V1")
    assert m.calibration.gui_reported_type is None and not m.calibration.user_confirmed
    assert m.calibration.status() == CalibrationStatus.NOT_VERIFIED
    assert m.instrument.backend == "import" and m.name == REF.stem and m.antenna.name == REF.stem
    assert measurement_from_touchstone(REF, name="DUT 7").name == "DUT 7"


def test_import_refuses_non_50_ohm_reference(tmp_path):
    p = tmp_path / "x.s1p"
    p.write_text("# HZ S RI R 75\n1 0 0\n")
    with pytest.raises(SessionError, match="75"):
        measurement_from_touchstone(p)


def test_export_of_imported_capture_is_lossless_and_states_calibration(tmp_path):
    m = measurement_from_touchstone(REF)
    p = export_touchstone(m, tmp_path / "out")
    assert p.suffix == ".s1p"
    text = p.read_text()
    assert "Calibration (instrument-reported): None" in text and "Simulated: False" in text
    t = read_touchstone(p)
    np.testing.assert_allclose(t.s["S11"], m.traces[0].data, rtol=1e-11, atol=1e-15)
    np.testing.assert_array_equal(t.freq_hz, m.traces[0].freq_hz)
