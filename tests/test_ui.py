# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""UI smoke tests (offscreen): connect, scan, cancel, save/load, report, state restoration."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from pypdf import PdfReader
from PySide6.QtCore import QLocale, QSettings, Qt
from PySide6.QtWidgets import QMessageBox

from antennafox import diagnostics
from antennafox.gain.comparative import S21Capture
from antennafox.instrument.librevna.emulator import LibreVNAGUIEmulator
from antennafox.ui import theme
from antennafox.ui.main_window import MainWindow
from antennafox.ui.state import AppState

QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


@pytest.fixture
def win(qtbot, qapp, settings, monkeypatch):
    theme.apply(qapp)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    w = MainWindow(AppState(settings))
    qtbot.addWidget(w)
    w.show()
    yield w
    for job in (w.setup_page.job, w.gain.job):
        if job and job.running():
            job.cancel()
            job.wait(15000)


def connect_sim(qtbot, w, model="dualband_24_5", realtime=None):
    d = w.setup_page
    d.instrument.set_simulation(True)
    d.instrument.sim_model.setCurrentIndex(d.instrument.sim_model.findData(model))
    d.connect_instrument()
    qtbot.waitUntil(lambda: w.state.connected and not w.state.busy, timeout=5000)
    if realtime is not None:
        w.state.backend.realtime = realtime


def analyze(qtbot, w, preset="wifi24"):
    d = w.setup_page
    d.sweep.preset.setCurrentIndex(d.sweep.preset.findData(preset))
    d.sweep.passive_ok.setChecked(True)
    d.antenna.name.setText("Whip A")
    d.analyze()
    qtbot.waitUntil(lambda: w.state.measurement is not None and not w.state.busy, timeout=15000)


def test_connect_scan_results(qtbot, win):
    connect_sim(qtbot, win, realtime=0.0)
    assert "SIMULATION" in win.setup_page.instrument.badge.text()
    analyze(qtbot, win)
    r = win.setup_page.results
    assert r.kv["gain"].text() == "Not measured"
    assert r.kv["pattern"].text() == "Not measured"
    assert "Well matched" in r.verdict.text()
    assert r.table.rowCount() == 4
    assert win.quick.sim_badge.isVisible()
    assert win.quick.title.text() == "Good match for Wi-Fi / Bluetooth"
    win.nav.setCurrentRow(1)
    win.graphs.add_marker(2.44e9)
    assert win.graphs.mtable.rowCount() >= 1


def test_analyze_requires_passive_confirmation(qtbot, win):
    connect_sim(qtbot, win, realtime=0.0)
    win.setup_page.sweep.passive_ok.setChecked(False)
    win.setup_page.analyze()
    assert not win.state.busy and win.state.measurement is None


def test_cancel(qtbot, win):
    connect_sim(qtbot, win, realtime=20.0)
    d = win.setup_page
    d.sweep.passive_ok.setChecked(True)
    d.sweep.preset.setCurrentIndex(d.sweep.preset.findData("wifi24"))
    d.analyze()
    qtbot.waitUntil(lambda: win.state.busy, timeout=2000)
    d.cancel()
    qtbot.waitUntil(lambda: not win.state.busy, timeout=8000)
    assert win.state.measurement is None
    assert "cancelled" in d.sweep.progress_text.text().lower()


def test_save_load_report_exports(qtbot, win, tmp_path):
    diagnostics.setup(tmp_path / "logs")
    connect_sim(qtbot, win, realtime=0.0)
    analyze(qtbot, win)
    m = win.state.measurement
    p = win.save_session(path=str(tmp_path / "a.afox"))
    assert p.exists()
    pdf = win.export_pdf(path=str(tmp_path / "r.pdf"))
    text = "\n".join(pg.extract_text() for pg in PdfReader(pdf).pages)
    assert "Gain: Not measured" in text and "a.afox" in text
    assert win.export("touchstone", path=str(tmp_path / "t.s1p")).exists()
    assert win.export("csv", path=str(tmp_path / "t.csv")).exists()
    assert win.export("json", path=str(tmp_path / "t.json")).exists()
    assert win.graphs.export_current(str(tmp_path / "c.png"))
    diag = win.save_diagnostics(str(tmp_path / "d.txt")).read_text()
    assert "session saved: a.afox" in diag and "Simulator" in diag
    win.state.set_measurement(None)
    win.open_session(str(p))
    assert win.state.measurement.id == m.id
    assert win.setup_page.antenna.name.text() == "Whip A"
    win.import_touchstone(str(tmp_path / "t.s1p"))
    assert win.state.measurement.traces[0].source == "touchstone-import"


def test_state_restoration(qtbot, qapp, settings, monkeypatch):
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    w = MainWindow(AppState(settings))
    qtbot.addWidget(w)
    d = w.setup_page
    d.instrument.host.setText("127.0.0.2")
    d.instrument.port.setValue(19999)
    d.sweep.preset.setCurrentIndex(d.sweep.preset.findData("gnss_l1"))
    d.calibration.confirm.setChecked(True)
    d.antenna.name.setText("Patch")
    w.nav.setCurrentRow(2)
    assert not any(b.isChecked() for b in w.quick.case_buttons.values()) or w.quick.case_buttons["gnss_l1"].isChecked()
    w.close()
    w2 = MainWindow(AppState(settings))
    qtbot.addWidget(w2)
    d2 = w2.setup_page
    assert d2.instrument.host.text() == "127.0.0.2" and d2.instrument.port.value() == 19999
    assert d2.sweep.preset.currentData() == "gnss_l1"
    assert d2.calibration.confirm.isChecked() and d2.antenna.name.text() == "Patch"
    assert w2.nav.currentRow() == 0  # always starts on the Test page


def test_scpi_path_through_ui(qtbot, win):
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    try:
        d = win.setup_page
        d.instrument.set_simulation(False)
        d.instrument.host.setText("127.0.0.1")
        d.instrument.port.setValue(srv.port)
        d.connect_instrument()
        qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
        assert win.state.gui_cal_type == "SOLT_1"
        analyze(qtbot, win)
        assert win.state.measurement.simulated  # emulator data must never look real
        srv.drop_client()
        d.sweep.passive_ok.setChecked(True)
        d.analyze()
        qtbot.waitUntil(lambda: not win.state.busy, timeout=8000)
        assert not win.state.connected
    finally:
        srv.stop()


def test_gain_page_relative_and_cut(qtbot, win):
    g = win.gain
    f = np.linspace(2.4e9, 2.5e9, 51)
    st = win.state.gain_study
    st.ref_captures.append(S21Capture("ref", f, np.full(51, 10 ** (-40 / 20) + 0j)))
    st.dut_captures.append(S21Capture("dut", f, np.full(51, 10 ** (-37 / 20) + 0j)))
    g.geometry_step.freq.setValue(2445)
    assert "not measurable" in g.result.main.text().lower()
    assert "+3.0 dB" in g.result.rel.text()
    for a, v in ((0, -40), (90, -43), (180, -50), (270, -43)):
        g.add_cut_point(a, v)
    assert g.cut.table.rowCount() == 4
    assert "full 360°" in g.cut.summary.text()
    assert "max @ 0°" in g.cut.summary.text()


def test_quick_test_flow(qtbot, win):
    q = win.quick
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    win.state.backend.realtime = 0.0
    q.name.setText("Helix 868 #2")
    q.name.textEdited.emit("Helix 868 #2")
    assert win.setup_page.antenna.name.text() == "Helix 868 #2"
    q.case_buttons["lora868"].click()
    assert win.setup_page.sweep.preset.currentData() == "lora868"
    q.start()
    assert win.state.measurement is None  # safety confirmation missing
    q.passive.setChecked(True)
    assert win.setup_page.sweep.passive_ok.isChecked()
    q.start()
    qtbot.waitUntil(lambda: win.state.measurement is not None and not win.state.busy, timeout=15000)
    assert q.progress.value() == 1000
    assert q.title.text().startswith(("Good match", "Borderline", "Poor match"))
    assert q.bands_box.count() == 1  # SRD 868, Meshtastic and MeshCore merge into one region
    assert q.btn_report.isEnabled()
    assert win.state.measurement.name == "Helix 868 #2"
    # with a result on screen, switching the use case must keep the new tile selected
    q.case_buttons["wifi24"].click()
    assert q.case_buttons["wifi24"].isChecked() and not q.case_buttons["lora868"].isChecked()
    win.setup_page.sweep.preset.setCurrentIndex(win.setup_page.sweep.preset.findData("custom"))
    assert not any(b.isChecked() for b in q.case_buttons.values())


def test_picker_dialog_enforces_maximum(qtbot, qapp, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    from antennafox.ui.widgets import PickerDialog

    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    rows = [(f"p{i}", [f"Preset {i}", "", ""], "") for i in range(4)]
    d = PickerDialog(None, "Choose", "intro", ["A", "B", "C"], [("Built-in", rows)], ["p0", "p1", "p2"], 2)
    qtbot.addWidget(d)
    assert d.selected_ids() == ["p0", "p1", "p2"]
    d.accept()
    assert warned and d.result() == 0  # pre-selected over the limit: refused on OK
    d.tree.topLevelItem(0).child(2).setCheckState(0, Qt.CheckState.Unchecked)
    assert d.count.text() == "2 of 2 selected"
    d.tree.topLevelItem(0).child(3).setCheckState(0, Qt.CheckState.Checked)  # ninth tick is refused immediately
    assert d.selected_ids() == ["p0", "p1"]
    d.accept()
    assert d.result() == 1


def test_graphs_table_has_markers_after_a_test(qtbot, win):
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    win.state.backend.realtime = 0.0
    q = win.quick
    q._select_case("dualband")
    q.passive.setChecked(True)
    q.start()
    qtbot.waitUntil(lambda: win.state.measurement is not None and not win.state.busy, timeout=20000)
    g = win.graphs
    assert g.mtable.rowCount() == 2 and not g.mtable_hint.isVisible()
    assert g.mtable.item(0, 1).text().endswith("GHz")
    g._clear_markers()
    assert g.mtable.rowCount() == 0 and g.mtable_hint.isVisibleTo(g)


def test_every_file_action_opens_its_dialog(qtbot, win, tmp_path, monkeypatch):
    """Qt passes the action's checked flag to triggered slots; menu clicks must still reach the file dialogs."""
    from PySide6.QtWidgets import QFileDialog

    from antennafox.ui.report_dialog import ReportDetailsDialog

    asked = []
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (asked.append(a[1]), ("", ""))[1])
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (asked.append(a[1]), ("", ""))[1])
    monkeypatch.setattr(ReportDetailsDialog, "exec", lambda self: 1)  # the report asks for details first
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    win.state.backend.realtime = 0.0
    win.quick._select_case("wifi24")
    win.quick.passive.setChecked(True)
    win.quick.start()
    qtbot.waitUntil(lambda: win.state.measurement is not None and not win.state.busy, timeout=20000)
    actions = [win.a_open, win.a_save, win.a_save_as, win.a_import, win.a_report, win.a_csv, win.a_json, win.a_ts,
               win.a_chart, win.a_diag]
    for action in actions:
        assert action.isEnabled(), action.text()
        action.trigger()
    assert len(asked) == len(actions), asked


def test_about_dialog_names_the_publisher(qtbot, win, monkeypatch):
    from PySide6.QtWidgets import QDialog, QLabel

    seen = {}

    def fake_exec(self):
        seen["text"] = " ".join(w.text() for w in self.findChildren(QLabel))
        return 0

    monkeypatch.setattr(QDialog, "exec", fake_exec)
    win.about()
    assert "Altinum Technologies" in seen["text"] and "altinum.tech" in seen["text"]


def test_instrument_details_dialog(qtbot, win, monkeypatch):
    from PySide6.QtWidgets import QDialog, QLabel, QMessageBox

    told = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: told.append(a[2]))
    win.instrument_details()
    assert told and "Not connected" in told[0]
    seen = {}
    monkeypatch.setattr(QDialog, "exec", lambda self: seen.setdefault("t", " ".join(
        w.text() for w in self.findChildren(QLabel))) and 0)
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    win.instrument_details()
    assert "SIM-0001" in seen["t"] and "Temperatures" in seen["t"] and "Frequency range" in seen["t"]


def test_report_details_dialog_stores_fields_and_defaults(qtbot, win):
    from antennafox.ui.report_dialog import ReportDetailsDialog

    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    win.state.backend.realtime = 0.0
    win.quick._select_case("wifi24")
    win.quick.passive.setChecked(True)
    win.quick.start()
    qtbot.waitUntil(lambda: win.state.measurement is not None and not win.state.busy, timeout=20000)
    m = win.state.measurement
    d = ReportDetailsDialog(win.state, m)
    qtbot.addWidget(d)
    assert d.operator.text() == ""  # nothing configured yet
    d.operator.setText("A. Tester")
    d.project.setText("Node v2")
    d.dut.setText("ANT-0042")
    d.environment.setCurrentIndex(d.environment.findData("enclosure"))
    d.apply()
    assert (m.operator, m.project, m.antenna.dut_reference, m.antenna.environment) == (
        "A. Tester", "Node v2", "ANT-0042", "enclosure")
    assert win.state.operator == "A. Tester"
    d2 = ReportDetailsDialog(win.state, m)
    qtbot.addWidget(d2)
    assert d2.operator.text() == "A. Tester" and d2.project.text() == "Node v2"

