# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""GUI smoke tests (offscreen): loading data into the plots, invalid files, failed connections and the
connection states a user sees."""

import os
import socket
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QLocale, QSettings
from PySide6.QtWidgets import QMessageBox

from antennafox.instrument.librevna.emulator import LibreVNAGUIEmulator
from antennafox.ui import theme
from antennafox.ui.main_window import MainWindow
from antennafox.ui.state import AppState

QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))
REF = Path(__file__).parent / "data" / "librevna_v1_cable_into_port2_uncal.s1p"


@pytest.fixture
def win(qtbot, qapp, tmp_path, monkeypatch):
    theme.apply(qapp)
    monkeypatch.setattr("antennafox.ui.state.data_dir", lambda: tmp_path)
    boxes = {"warning": [], "information": [], "critical": []}
    for kind in boxes:
        monkeypatch.setattr(QMessageBox, kind, lambda *a, _k=kind, **k: (boxes[_k].append(a[2]), QMessageBox.Ok)[1])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    w = MainWindow(AppState(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)))
    w.boxes = boxes
    qtbot.addWidget(w)
    w.show()
    yield w
    for job in (w.setup_page.job, w.gain.job):
        if job and job.running():
            job.cancel()
            job.wait(15000)


def curve_lengths(plot):
    return [len(it.xData) for it in plot._items if getattr(it, "xData", None) is not None]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_imported_capture_is_drawn_in_every_plot(qtbot, win):
    g = win.graphs
    assert curve_lengths(g.p_s11) == [] and win.state.measurement is None
    win.import_touchstone(str(REF))
    m = win.state.measurement
    assert m is not None and m.instrument.backend == "import" and not m.simulated
    assert 451 in curve_lengths(g.p_s11) and 451 in curve_lengths(g.p_vswr) and 451 in curve_lengths(g.p_rx)
    assert 451 in curve_lengths(g.p_smith)
    assert not g.tabs.isTabEnabled(4)  # no S21 in a one-port file
    assert g.title.text() == REF.stem
    assert win.state.assessment is not None and win.state.assessment.evidence.value == "C"
    assert win.setup_page.antenna.name.text() == REF.stem
    assert not win.quick.sim_badge.isVisible()
    win.nav.setCurrentRow(1)
    g.add_marker(2.44e9)
    assert g.mtable.rowCount() >= 1
    for i in range(5):
        g.tabs.setCurrentIndex(i)
    assert win.export("csv", path=str(Path(win.state.settings.fileName()).parent / "ref.csv")).exists()


def test_invalid_files_are_reported_and_leave_the_state_untouched(qtbot, win, tmp_path):
    bad_ts = tmp_path / "bad.s1p"
    bad_ts.write_text("# HZ S RI R 50\n1 a b\n")
    win.import_touchstone(str(bad_ts))
    assert win.state.measurement is None and win.boxes["critical"] and "non-numeric" in win.boxes["critical"][-1]
    win.import_touchstone(str(tmp_path / "missing.s1p"))
    assert win.state.measurement is None and len(win.boxes["critical"]) == 2
    bad_session = tmp_path / "bad.afox"
    bad_session.write_bytes(b"garbage")
    win.open_session(str(bad_session))
    assert win.state.measurement is None and "Not a valid AntennaFox session" in win.boxes["critical"][-1]
    win.import_touchstone(str(REF))
    win.open_session(str(bad_session))
    assert win.state.measurement is not None  # a failed open keeps the current measurement


def test_failed_connection_is_shown_inline_and_leaves_the_app_disconnected(qtbot, win):
    d = win.setup_page
    d.instrument.set_simulation(False)
    d.instrument.host.setText("127.0.0.1")
    d.instrument.port.setValue(free_port())
    d.connect_instrument()
    qtbot.waitUntil(lambda: not win.state.busy, timeout=10000)
    assert not win.state.connected and win.state.backend is None
    assert "Cannot connect" in d.sweep.progress_text.text() and "SCPI server" in d.sweep.progress_text.text()
    assert not win.boxes["critical"]
    assert "Not connected" in d.instrument.badge.text() and "Not connected" in win.quick.conn_pill.text()
    assert not win.a_disconnect.isEnabled() and win.a_connect.isEnabled()


def test_gui_without_device_cannot_start_a_measurement(qtbot, win):
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    srv.faults.device_connected = False
    try:
        d = win.setup_page
        d.instrument.set_simulation(False)
        d.instrument.host.setText("127.0.0.1")
        d.instrument.port.setValue(srv.port)
        d.connect_instrument()
        qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
        assert win.state.instrument is not None and win.state.instrument.limits is None
        assert "no device" in d.instrument.badge.text() and "no device" in win.quick.conn_pill.text()
        d.sweep.passive_ok.setChecked(True)
        d.analyze()
        assert not win.state.busy and win.state.measurement is None
        assert "limits unknown" in d.sweep.progress_text.text()
        win.quick.passive.setChecked(True)
        win.quick.start()
        assert win.state.measurement is None
        win.a_disconnect.trigger()
        assert not win.state.connected and "Not connected" in win.quick.conn_pill.text()
    finally:
        srv.stop()


def test_connection_states_are_named_on_the_quick_page(qtbot, win):
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    assert "Demo mode" in win.quick.conn_pill.text() and "SIMULATION" in win.setup_page.instrument.badge.text()
    win.a_disconnect.trigger()
    assert "Not connected" in win.quick.conn_pill.text() and "try the demo" in win.quick.conn_hint.text()
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    try:
        srv.serial = "12345678"
        d = win.setup_page
        d.instrument.set_simulation(False)
        d.instrument.port.setValue(srv.port)
        d.connect_instrument()
        qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
        assert "LibreVNA connected · 12345678" in win.quick.conn_pill.text()
        assert "LibreVNA-GUI 1.6.5" in win.quick.conn_hint.text()
        assert "Calibration active" in win.quick.cal_hint.text() and "SOLT_1" in win.quick.cal_hint.text()
        assert "Connected" in d.instrument.badge.text() and "SIMULATION" not in d.instrument.badge.text()
        srv.cal_type = "None"
        d.instrument.btn_test.click()
        qtbot.waitUntil(lambda: not win.state.busy, timeout=10000)
        assert "Not calibrated" in win.quick.cal_hint.text()
    finally:
        srv.stop()
