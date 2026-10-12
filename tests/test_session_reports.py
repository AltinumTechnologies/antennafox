# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
import zipfile

import numpy as np
import pytest
from pypdf import PdfReader

from antennafox.catalog.catalog import load_catalog
from antennafox.gain.comparative import GainSetup, ReferenceAntenna, S21Capture
from antennafox.gain.study import GainStudy
from antennafox.instrument.base import CancelToken
from antennafox.instrument.simulated import SimulatedBackend
from antennafox.measurement.model import AntennaInfo, CalibrationMeta
from antennafox.measurement.orchestrator import new_measurement, run_plan
from antennafox.measurement.presets import PRESETS, plan_for_preset
from antennafox.measurement.session import (
    SessionError,
    load_session,
    measurement_from_touchstone,
    save_session,
)
from antennafox.reports.export import export_csv, export_json, export_touchstone, touchstone_possible
from antennafox.reports.pdf import build_report
from antennafox.rf.assessment import assess
from antennafox.rf.touchstone import TouchstoneError


@pytest.fixture(scope="module")
def cat():
    return load_catalog()


def meas(preset="wifi24", repeats=1):
    b = SimulatedBackend("dualband_24_5")
    b.connect()
    plan = plan_for_preset(preset, b.limits)
    tr, rep = run_plan(b, plan, CancelToken(), repeats=repeats)
    return new_measurement("DUT 1", AntennaInfo(name="Whip", connector="SMA"), b, CalibrationMeta(), plan, tr, rep)


def test_session_roundtrip_exact(tmp_path):
    m = meas(repeats=2)
    p = save_session(tmp_path / "s1", m, derived={"rating": "x"})
    assert p.suffix == ".afox"
    m2, study, doc = load_session(p)
    assert study is None
    assert m2.id == m.id and m2.antenna.connector == "SMA"
    for a, b in zip(m.traces, m2.traces, strict=True):
        np.testing.assert_array_equal(a.freq_hz, b.freq_hz)
        np.testing.assert_array_equal(a.data, b.data)  # lossless
        assert a.provenance == b.provenance
    assert len(m2.repeats) == 1
    assert doc["simulated"] is True
    assert not m2.traces[0].data.flags.writeable  # immutable capture


def test_session_tamper_detected(tmp_path):
    p = save_session(tmp_path / "s2", meas())
    with zipfile.ZipFile(p) as z:
        items = {n: z.read(n) for n in z.namelist()}
    name = next(n for n in items if n.startswith("traces/"))
    items[name] = items[name].replace(b"e", b"E", 1)[:-30] + b"\n"
    with zipfile.ZipFile(p, "w") as z:
        for n, b in items.items():
            z.writestr(n, b)
    with pytest.raises(SessionError, match="Checksum"):
        load_session(p)


def test_gain_study_roundtrip(tmp_path):
    f = np.linspace(2.4e9, 2.5e9, 11)
    st = GainStudy(reference=ReferenceAntenna(name="R", gain_table=[(2.4e9, 5.0)], gamma_table=[(2.4e9, 0.1 + 0.2j)]),
                   setup=GainSetup(frequency_hz=2.45e9, distance_m=2.0),
                   ref_captures=[S21Capture("ref", f, np.full(11, 0.01 + 0j))],
                   dut_captures=[S21Capture("dut", f, np.full(11, 0.02 + 0j), angle_deg=30.0)])
    p = save_session(tmp_path / "g", meas(), gain_study=st)
    _, st2, _ = load_session(p)
    assert st2.reference.gamma_table[0][1] == 0.1 + 0.2j
    assert st2.dut_captures[0].angle_deg == 30.0
    assert st2.setup.distance_m == 2.0


def test_touchstone_export_reimport(tmp_path):
    m = meas()
    ok, kind = touchstone_possible(m)
    assert ok and kind == ".s1p"
    p = export_touchstone(m, tmp_path / "x")
    m2 = measurement_from_touchstone(p)
    np.testing.assert_allclose(m2.traces[0].data, m.traces[0].data, rtol=1e-11)
    np.testing.assert_array_equal(m2.traces[0].freq_hz, m.traces[0].freq_hz)
    assert m2.traces[0].source == "touchstone-import"


def test_touchstone_refused_for_multisegment(tmp_path):
    m = meas("dualband")
    ok, why = touchstone_possible(m)
    assert not ok and "segments" in why
    with pytest.raises(TouchstoneError):
        export_touchstone(m, tmp_path / "y")
    assert export_touchstone(m, tmp_path / "seg", segment_index=1).suffix == ".s1p"


def test_csv_json_export(tmp_path):
    m = meas()
    c = export_csv(m, tmp_path / "a.csv").read_text()
    assert "simulated=True" in c and "vswr" in c
    assert export_json(m, tmp_path / "a.json").exists()


def test_pdf_s11_only_says_gain_not_measured(tmp_path, cat):
    m = meas()
    a = assess(m, cat, list(PRESETS["wifi24"].targets))
    p = build_report(tmp_path / "r.pdf", m, a, cat, session_ref="s1.afox")
    text = "\n".join(page.extract_text() for page in PdfReader(p).pages)
    assert "Gain: Not measured" in text
    assert "Power accepted" in text and "says nothing about how much is radiated" in text
    assert "Report generated 20" in text and "Measured 20" in text
    assert f"Report {m.report_id}" in text and "Page 1 of" in text
    assert "Scope:" in text and "acceptance S11" in text
    assert "S11 uncertainty" in text and "At band centre" in text
    assert "Verification" in text and "not performed" in text
    assert "SIMULATION" in text
    assert "s1.afox" in text and m.id in text
    assert "dBi" not in text


def test_pdf_with_valid_gain(tmp_path, cat):
    m = meas()
    a = assess(m, cat, ["ism24"])
    f = np.linspace(2.4e9, 2.5e9, 51)
    st = GainStudy(
        reference=ReferenceAntenna(name="Ref", source="cert", gain_table=[(2.3e9, 6.0), (2.6e9, 6.0)],
                                   tolerance_db=0.5, trusted=True),
        setup=GainSetup(frequency_hz=2.45e9, distance_m=3, largest_dimension_m=0.1, polarization_matched=True,
                        same_position_confirmed=True),
        ref_captures=[S21Capture("ref", f, np.full(51, 0.01 + 0j))],
        dut_captures=[S21Capture("dut", f, np.full(51, 0.02 + 0j))])
    p = build_report(tmp_path / "g.pdf", m, a, cat, gain_study=st)
    text = "\n".join(page.extract_text() for page in PdfReader(p).pages)
    assert "dBi" in text and "directional" in text


def test_pdf_markers_comparison_and_report_details(tmp_path, cat):
    m = meas("wifi24")
    m.operator, m.project = "A. Tester", "Node v2"
    m.antenna.dut_reference, m.antenna.environment = "ANT-0042 lot 7", "enclosure"
    m.calibration.verification = "50 Ω load check 2026-10-11 16:00 UTC: worst S11 -42.6 dB (plausible)"
    other = meas("wifi24")
    other.name, other.antenna.variant = "same antenna", "in enclosure"
    a = assess(m, cat, ["ism24", "wifi24_eu"])
    f = m.merged("S11")[0]
    p = build_report(tmp_path / "c.pdf", m, a, cat, compare=[other], markers=[float(f[len(f) // 2])])
    text = "\n".join(page.extract_text() for page in PdfReader(p).pages)
    assert "Operator A. Tester" in text and "Project Node v2" in text and "DUT ANT-0042 lot 7" in text
    assert "in its enclosure" in text
    assert "MARKERS" in text and "M1" in text
    assert "COMPARISON" in text and "same antenna (in enclosure)" in text and "vs. primary" in text
    assert "worst S11 -42.6 dB (plausible)" in text


def test_session_keeps_report_fields(tmp_path, cat):
    m = meas("wifi24")
    m.operator, m.project = "A. Tester", "Node v2"
    m.antenna.dut_reference, m.antenna.environment = "ANT-0042", "ground_plane"
    m.calibration.verification = "50 Ω load check: ok"
    p = save_session(tmp_path / "r.afox", m)
    m2, _, _ = load_session(p)
    assert (m2.operator, m2.project) == ("A. Tester", "Node v2")
    assert (m2.antenna.dut_reference, m2.antenna.environment) == ("ANT-0042", "ground_plane")
    assert m2.calibration.verification == "50 Ω load check: ok" and m2.report_id == m.report_id

