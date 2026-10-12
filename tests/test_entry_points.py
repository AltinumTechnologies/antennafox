# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Drive the application through its user entry points – buttons, menu actions, dialogs and the CLI.

The slots behind these were already unit-tested; these tests exist because a bug in the wiring (a Qt signal
argument, a modal that never returns, a dialog field that is not read) only shows up when the UI is used the way a
person uses it.
"""

import os
import subprocess
import sys
import textwrap
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest
from PySide6.QtCore import QLocale, QSettings
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from antennafox.instrument.base import Cancelled, CancelToken, InstrumentError
from antennafox.ui import theme
from antennafox.ui.main_window import MainWindow, SettingsDialog
from antennafox.ui.state import AppState
from antennafox.ui.widgets import PickerDialog
from antennafox.ui.worker import Job

QLocale.setDefault(QLocale(QLocale.English, QLocale.UnitedStates))


@pytest.fixture
def win(qtbot, qapp, tmp_path, monkeypatch):
    theme.apply(qapp)
    monkeypatch.setattr("antennafox.ui.state.data_dir", lambda: tmp_path)
    boxes = {"warning": [], "information": [], "critical": []}
    for kind in boxes:
        monkeypatch.setattr(QMessageBox, kind, lambda *a, _k=kind, **k: (boxes[_k].append(a[2]), QMessageBox.Ok)[1])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    w = MainWindow(AppState(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)))
    w.boxes = boxes
    qtbot.addWidget(w)
    w.show()
    yield w
    for job in (w.setup_page.job, w.gain.job):
        if job and job.running():
            job.cancel()
            job.wait(15000)


def measured(qtbot, win, case="wifi24"):
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    win.state.backend.realtime = 0.0
    win.quick._select_case(case)
    win.quick.passive.setChecked(True)
    win.quick.start()
    qtbot.waitUntil(lambda: win.state.measurement is not None and not win.state.busy, timeout=20000)
    return win.state.measurement


# --- Bands page -------------------------------------------------------------------------------
def test_bands_page_user_band_lifecycle(qtbot, win, monkeypatch):
    measured(qtbot, win)
    page = win.bands
    assert page.add_user_band("", 1e9, 2e9) is None and "name and stop" in win.boxes["warning"][-1]
    assert page.add_user_band("LTE 20", 2e9, 1e9) is None
    pid = page.add_user_band("LTE 20", 791e6, 862e6, "EU", "band 20 downlink/uplink")
    assert pid == "user_lte_20" and win.state.catalog.get(pid) is not None
    assert pid in win.state.survey_ids and (win.state.user_catalog_path).exists()
    assert page.add_user_band("LTE 20", 791e6, 862e6) is None and "already exists" in win.boxes["warning"][-1]
    labels = [page.table.item(r, 0).text() for r in range(page.table.rowCount())]
    assert any("LTE 20" in t for t in labels)

    # the add dialog itself: fields are read when it is accepted
    from antennafox.ui.bands_page import AddBandDialog

    def fake_exec(self):
        self.name.setText("FPV")
        self.start.setValue(1200)
        self.stop.setValue(1400)
        return 1

    monkeypatch.setattr(AddBandDialog, "exec", fake_exec)
    page.btn_add.click()
    assert win.state.catalog.get("user_fpv") is not None

    # remove: picker lists only user bands; selecting one deletes it from catalog and selections
    def pick_exec(self):
        self.tree.topLevelItem(0).child(0).setCheckState(0, __import__("PySide6").QtCore.Qt.CheckState.Checked)
        return 1

    monkeypatch.setattr(PickerDialog, "exec", pick_exec)
    page.btn_del.click()
    assert win.state.catalog.get("user_lte_20") is None and "user_lte_20" not in win.state.survey_ids
    assert win.state.catalog.get("user_fpv") is not None

    # survey picker feeds set_targets
    page.btn_all.click()
    assert win.state.survey_ids  # at least the first catalog band got ticked by pick_exec

    # nothing left to remove → information box, no crash
    page.btn_del.click()
    page.btn_del.click()
    assert win.state.catalog.get("user_fpv") is None
    page.btn_del.click()
    assert "no user-defined bands" in win.boxes["information"][-1]


# --- Setup page: preset dialogs, load check, connection test, error paths ----------------------
def test_sweep_panel_preset_dialogs(qtbot, win, monkeypatch):
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    sweep = win.setup_page.sweep
    monkeypatch.setattr(QDialog, "exec", lambda self: 1)  # accept the save-as dialog with an empty title
    assert sweep.btn_save_preset.isEnabled()
    sweep.btn_save_preset.click()
    assert "title" in win.boxes["warning"][-1].lower()
    sweep.preset.setCurrentIndex(sweep.preset.findData("wifi24"))
    assert not sweep.btn_delete_preset.isEnabled()  # built-ins cannot be deleted
    sweep._delete_preset()
    assert "Only user-defined presets" in win.boxes["information"][-1]


def test_load_check_records_verification(qtbot, win):
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    win.state.backend.realtime = 0.0
    page = win.setup_page
    page.calibration.btn_load_check.click()
    qtbot.waitUntil(lambda: not win.state.busy, timeout=20000)
    assert win.boxes["information"] and "Worst S11 with the load attached" in win.boxes["information"][-1]
    assert page.calibration.verification.startswith("50 Ω load check") and "worst S11" in page.calibration.verification
    assert page.calibration.meta().verification == page.calibration.verification


def test_test_connection_before_and_after_connecting(qtbot, win):
    page = win.setup_page
    page.instrument.set_simulation(True)
    page.instrument.btn_test.click()  # temporary connection
    qtbot.waitUntil(lambda: not win.state.busy, timeout=10000)
    assert "Connection OK" in page.sweep.progress_text.text() and not win.state.connected
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    page.instrument.btn_test.click()
    qtbot.waitUntil(lambda: not win.state.busy, timeout=10000)
    assert "Connection OK" in page.sweep.progress_text.text() and win.state.connected
    win.a_disconnect.trigger()
    assert not win.state.connected


def test_unexpected_job_error_is_reported_not_swallowed(qtbot, win):
    page = win.setup_page

    def boom(token, progress):
        raise ZeroDivisionError("division by zero")

    page._run(boom, lambda r: None, "boom")
    qtbot.waitUntil(lambda: not win.state.busy, timeout=5000)
    assert win.boxes["critical"] and "ZeroDivisionError" in win.boxes["critical"][-1]
    assert "ZeroDivisionError" in page.sweep.progress_text.text()


def test_instrument_error_is_shown_inline_and_drops_dead_backend(qtbot, win):
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    page = win.setup_page

    def fail(token, progress):
        win.state.backend.disconnect()  # the backend died underneath us
        raise InstrumentError("LibreVNA-GUI closed the connection")

    page._run(fail, lambda r: None, "fail")
    qtbot.waitUntil(lambda: not win.state.busy, timeout=5000)
    assert not win.boxes["critical"]
    assert "closed the connection" in page.sweep.progress_text.text() and not win.state.connected


def test_job_cancellation_reaches_failed_signal(qtbot, qapp):
    seen = []

    def work(token, progress):
        raise Cancelled("stop")

    job = Job(work)
    job.failed.connect(lambda e, tb: seen.append(type(e).__name__))
    job.start()
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)
    job.wait(5000)
    assert seen == ["Cancelled"]
    assert isinstance(job.token, CancelToken)


# --- Gain wizard ------------------------------------------------------------------------------
def test_gain_wizard_captures_and_cut_through_buttons(qtbot, win):
    win.connect_demo()
    qtbot.waitUntil(lambda: win.state.connected and not win.state.busy, timeout=5000)
    win.state.backend.realtime = 0.0
    g = win.gain
    g.geometry_step.freq.setValue(2440)
    study = win.state.gain_study
    g.ref_capture.btn_capture.click()
    qtbot.waitUntil(lambda: len(study.ref_captures) == 1 and not win.state.busy, timeout=20000)
    g.dut_capture.btn_capture.click()
    qtbot.waitUntil(lambda: len(study.dut_captures) == 1 and not win.state.busy, timeout=20000)
    assert g.ref_capture.table.rowCount() == 1 and "dB" in g.ref_capture.table.item(0, 2).text()
    g.cut.angle.setValue(0)
    g.cut.btn_capture.click()
    qtbot.waitUntil(lambda: not win.state.busy and g.cut.table.rowCount() == 1, timeout=20000)
    assert g.cut.angle.value() == 15  # advanced by the step
    g._cut_undo()
    assert g.cut.table.rowCount() == 0
    g.add_cut_point(0, -40)
    g.add_cut_point(90, -46)
    g._cut_new()
    assert g.cut.table.rowCount() == 0 and g.cut.angle.value() == 0
    g.ref_capture.table.selectRow(0)
    g.ref_capture._remove_selected()
    assert study.ref_captures == []


def test_reference_step_imports(qtbot, win, tmp_path, monkeypatch):
    ref = win.gain.reference
    csv_path = tmp_path / "ref.csv"
    csv_path.write_text("frequency_mhz,gain_dbi\n2400,2.1\n2450,2.2\n2500,2.0\nnot,numbers\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(csv_path), ""))
    ref._import_csv()
    assert ref.table.rowCount() == 3 and ref._read_table()[0] == (2400e6, 2.1)
    empty = tmp_path / "empty.csv"
    empty.write_text("a,b\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(empty), ""))
    ref._import_csv()
    assert "No numeric" in win.boxes["warning"][-1]
    s1p = tmp_path / "ref.s1p"
    s1p.write_text("# Hz S RI R 50\n2400000000 -0.1 0.05\n2450000000 -0.12 0.04\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(s1p), ""))
    ref._import_gamma()
    assert len(ref._gamma_table) == 2
    bad = tmp_path / "bad.s1p"
    bad.write_text("garbage\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(bad), ""))
    ref._import_gamma()
    assert win.boxes["warning"]


# --- Settings ----------------------------------------------------------------------------------
def test_settings_dialog_applies_operator_and_thresholds(qtbot, win, monkeypatch):
    def fake_exec(self):
        self.operator.setText("A. Tester")
        self.pass_db.setValue(-12)
        self.warn_db.setValue(-7)
        return 1

    monkeypatch.setattr(SettingsDialog, "exec", fake_exec)
    win.a_settings.trigger()
    assert win.state.operator == "A. Tester"
    assert win.state.thresholds.pass_s11_db == -12 and win.state.thresholds.warn_s11_db == -7


# --- CLI and process start -----------------------------------------------------------------------
def test_cli_help():
    out = subprocess.run([sys.executable, "-m", "antennafox", "--help"], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and "--debug" in out.stdout and "--verbose" in out.stdout


DRIVER = textwrap.dedent('''
    import sys
    from pathlib import Path
    import antennafox.ui.state as st
    data_dir = Path(sys.argv[1])
    st.data_dir = lambda: data_dir
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from antennafox.ui import main_window
    shown = main_window.MainWindow.show
    def show(self):
        shown(self)
        QTimer.singleShot(400, self.close)
        QTimer.singleShot(800, lambda: QApplication.instance().quit())
    main_window.MainWindow.show = show
    from antennafox.__main__ import main
    sys.argv = ["antennafox", "--verbose"]
    sys.exit(main())
''')


def _start_app(data_dir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    return subprocess.run([sys.executable, "-c", DRIVER, str(data_dir)], capture_output=True, text=True, timeout=120,
                          env=env)


def test_app_starts_with_fresh_and_corrupt_data_dir(tmp_path):
    fresh = tmp_path / "fresh"
    out = _start_app(fresh)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "starting on" in out.stderr and (fresh / "antennafox.log").exists()
    corrupt = tmp_path / "corrupt"
    corrupt.mkdir()
    (corrupt / "user_presets.json").write_text("{not json")
    (corrupt / "user_catalog.json").write_text("[]")
    out = _start_app(corrupt)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "user presets ignored" in out.stderr


@pytest.mark.skipif(sys.platform == "win32" or getattr(os, "geteuid", lambda: 1)() == 0, reason="needs a directory the user cannot write")
def test_app_starts_when_data_dir_is_read_only(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        out = _start_app(ro)
    finally:
        ro.chmod(0o700)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "file logging disabled" in out.stderr


def test_simulated_sweep_values_are_finite(qtbot, win):
    m = measured(qtbot, win, "dualband")
    f, g = m.merged("S11")
    assert np.all(np.isfinite(g)) and np.all(np.diff(f) > 0)
