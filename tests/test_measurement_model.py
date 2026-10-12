# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared measurement model and session bundle: merging, calibration metadata rules, integrity checks."""

import hashlib
import json
import zipfile
from dataclasses import replace
from datetime import UTC, datetime
from enum import Enum

import numpy as np
import pytest

from antennafox.gain.comparative import S21Capture
from antennafox.gain.study import GainStudy
from antennafox.instrument.base import CancelToken
from antennafox.instrument.simulated import SimulatedBackend
from antennafox.measurement.model import (
    AntennaInfo,
    CalibrationMeta,
    CalibrationStatus,
    InstrumentInfo,
    Measurement,
    TraceSegment,
)
from antennafox.measurement.orchestrator import new_measurement, run_plan
from antennafox.measurement.presets import custom_plan
from antennafox.measurement.session import SessionError, load_session, save_session
from antennafox.rf.bands import Coverage

STAMP = "2026-01-01T00:00:00+00:00"


class Kind(Enum):
    A = 7


def seg(f, g, param="S11", source="librevna-scpi", **kw):
    return TraceSegment(param, np.asarray(f, dtype=float), np.asarray(g, dtype=complex), STAMP, source, **kw)


def simulated_measurement(points=11, repeats=1):
    b = SimulatedBackend()
    b.connect()
    plan = custom_plan(1e9, 2e9, points, b.limits, averages=2)
    traces, extra = run_plan(b, plan, CancelToken(), repeats=repeats)
    return new_measurement("sim", AntennaInfo(name="sim"), b, CalibrationMeta(), plan, traces, extra)


# --- model -----------------------------------------------------------------------------------------------
def test_merged_sorts_segments_and_keeps_the_worse_duplicate():
    a = seg([1e9, 2e9, 3e9], [0.1, 0.5, 0.1])
    b = seg([3e9, 4e9], [0.3, 0.2])  # overlaps at 3 GHz with a worse value
    m = Measurement("m", AntennaInfo(), InstrumentInfo(), CalibrationMeta(), None, [b, a])
    f, g = m.merged()
    assert f.tolist() == [1e9, 2e9, 3e9, 4e9]
    assert g[2] == 0.3 and g.dtype == complex
    assert m.span() == (1e9, 4e9) and m.parameters() == ["S11"]
    assert [s.start_hz for s in m.segments()] == [1e9, 3e9]
    f2, g2 = m.merged("S21")
    assert len(f2) == 0 and g2.dtype == complex and m.span("S21") is None


def test_report_id_and_simulation_flag():
    m = Measurement("m", AntennaInfo(), InstrumentInfo(), CalibrationMeta(), None,
                    id="abcdef1234", created_at="2026-03-04T05:06:07+00:00")
    assert m.report_id == "AFX-20260304-ABCDEF"
    assert not m.simulated
    m.traces.append(seg([1e9], [0], source="simulation"))
    assert m.simulated  # one simulated trace taints the whole measurement
    assert Measurement("m", AntennaInfo(), InstrumentInfo(simulated=True), CalibrationMeta(), None).simulated


def test_trace_segment_shape_rules():
    with pytest.raises(ValueError):
        TraceSegment("S11", np.zeros((2, 2)), np.zeros((2, 2)), STAMP, "x")
    t = TraceSegment("S11", [], [], STAMP, "x")
    assert np.isnan(t.start_hz) and np.isnan(t.stop_hz)
    t = seg([1e9, 2e9], [0.1, 0.2])
    assert not t.freq_hz.flags.writeable and not t.data.flags.writeable


def test_calibration_status_rules():
    now = datetime(2026, 1, 11, tzinfo=UTC)
    c = CalibrationMeta()
    assert c.status() == CalibrationStatus.NOT_VERIFIED and c.age_days() is None
    assert CalibrationMeta(user_confirmed=True).status() == CalibrationStatus.USER_CONFIRMED
    c = CalibrationMeta(gui_reported_type="SOLT_1", user_confirmed=True, cal_date="2026-01-01T00:00:00+00:00",
                        cal_start_hz=1e9, cal_stop_hz=3e9, reference_plane="cable_end")
    assert c.status(1e9, 3e9) == CalibrationStatus.VALID_METADATA
    assert c.status(None, None) == CalibrationStatus.VALID_METADATA
    assert c.status(1e9, 3.1e9) == CalibrationStatus.USER_CONFIRMED  # sweep exceeds the calibrated span
    assert c.status(0.9e9, 3e9) == CalibrationStatus.USER_CONFIRMED
    assert c.age_days(now) == pytest.approx(10.0)
    assert replace(c, cal_date="2026-01-01").age_days(now) == pytest.approx(10.0)  # naive date is taken as UTC
    assert replace(c, cal_date="not a date").age_days(now) is None
    for weak in (replace(c, gui_reported_type="None"), replace(c, gui_reported_type=None),
                 replace(c, reference_plane="unknown"), replace(c, cal_date=None), replace(c, cal_start_hz=None)):
        assert weak.status(1e9, 3e9) == CalibrationStatus.USER_CONFIRMED
    assert c.issues(1e9, 3e9) == []
    issues = replace(c, gui_reported_type="none", user_confirmed=False, reference_plane="unknown").issues(0.5e9, 3.5e9)
    assert len(issues) == 4 and any("does not fully cover" in i for i in issues)
    assert c.issues(None, None) == []  # unknown sweep span cannot contradict the calibration span


# --- session bundle --------------------------------------------------------------------------------------
def test_session_rejects_non_sessions(tmp_path):
    bad = tmp_path / "x.afox"
    bad.write_bytes(b"not a zip")
    with pytest.raises(SessionError, match="Not a valid"):
        load_session(bad)
    with pytest.raises(SessionError, match="Not a valid"):
        load_session(tmp_path / "missing.afox")
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("measurement.json", json.dumps({"format": "other/9"}))
    with pytest.raises(SessionError, match="Unknown session format"):
        load_session(bad)


def _rewrite(path, edit):
    with zipfile.ZipFile(path) as z:
        items = {n: z.read(n) for n in z.namelist()}
    edit(items)
    with zipfile.ZipFile(path, "w") as z:
        for n, blob in items.items():
            z.writestr(n, blob)


def test_session_csv_header_is_validated_even_with_matching_checksum(tmp_path):
    p = save_session(tmp_path / "s", simulated_measurement())

    def edit(items):
        doc = json.loads(items["measurement.json"])
        name = doc["traces"][0]["file"]
        items[name] = items[name].replace(b"freq_hz,re,im", b"f,re,im")
        doc["traces"][0]["sha256"] = hashlib.sha256(items[name]).hexdigest()
        items["measurement.json"] = json.dumps(doc).encode()

    _rewrite(p, edit)
    with pytest.raises(SessionError, match="header"):
        load_session(p)


def test_session_keeps_plan_limits_and_averaging(tmp_path):
    m = simulated_measurement(points=21, repeats=2)
    assert m.sweep_plan is not None and m.sweep_plan.averages == 2
    p = save_session(tmp_path / "s", m, derived={"z": 1 + 2j, "n": np.int64(3), "c": Coverage.MEASURED, "k": Kind.A})
    m2, study, doc = load_session(p)
    assert study is None
    assert doc["derived"]["z"] == [1.0, 2.0] and doc["derived"]["n"] == 3
    assert doc["derived"]["c"] == "measured" and doc["derived"]["k"] == 7
    assert m2.sweep_plan is not None
    assert m2.sweep_plan.averages == 2 and m2.sweep_plan.parameters == ("S11",)
    assert [(s.start_hz, s.stop_hz, s.points) for s in m2.sweep_plan.segments] == [(1e9, 2e9, 21)]
    assert m2.instrument.limits is not None and m2.instrument.limits.source == "simulation"
    assert m2.calibration.gui_reported_type == "SOLT_1 (simulated)"
    assert len(m2.repeats) == 1 and np.array_equal(m2.repeats[0][0].data, m.repeats[0][0].data)
    assert m2.traces[0].provenance["averages"] == 2 and m2.app_version == m.app_version
    with pytest.raises(TypeError):
        save_session(tmp_path / "t", m, derived={"o": object()})


def test_gain_capture_checksum_is_verified(tmp_path):
    f = np.linspace(2.4e9, 2.5e9, 11)
    study = GainStudy(ref_captures=[S21Capture("ref", f, np.full(11, 0.01 + 0j))])
    p = save_session(tmp_path / "g", simulated_measurement(), gain_study=study)
    _, loaded, _ = load_session(p)
    assert loaded is not None and len(loaded.ref_captures) == 1

    def edit(items):
        name = next(n for n in items if n.startswith("gain/"))
        items[name] = items[name].replace(b"0.01", b"0.02", 1)

    _rewrite(p, edit)
    with pytest.raises(SessionError, match="Checksum"):
        load_session(p)
