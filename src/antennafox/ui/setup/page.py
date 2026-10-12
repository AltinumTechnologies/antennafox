# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Setup page: composes the panels and owns the instrument workflow (connect, test, analyze, load check)."""

from __future__ import annotations

import time
from datetime import datetime

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QScrollArea, QSplitter, QVBoxLayout, QWidget

from ...instrument.base import InstrumentError
from ...measurement.model import utc_now_iso
from ...measurement.orchestrator import new_measurement, run_plan
from ...measurement.presets import custom_plan
from ...rf.rfmath import s11_db
from ..state import AppState
from ..widgets import tr
from ..worker import Job
from .antenna_panel import AntennaPanel
from .calibration_panel import CalibrationPanel
from .instrument_panel import InstrumentPanel
from .results_panel import ResultsPanel
from .sweep_panel import SweepPanel


class SetupPage(QWidget):
    measurement_done = Signal()
    job_started = Signal(object)

    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.job: Job | None = None
        self.instrument = InstrumentPanel()
        self.calibration = CalibrationPanel()
        self.antenna = AntennaPanel()
        self.sweep = SweepPanel(state)
        self.results = ResultsPanel()

        outer = QHBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setHandleWidth(10)
        outer.addWidget(self.splitter)
        for panels, min_w in (((self.instrument, self.calibration, self.antenna), 420), ((self.sweep, self.results), 470)):
            column = QWidget()
            lay = QVBoxLayout(column)
            lay.setContentsMargins(0, 0, 6, 0)
            lay.setSpacing(12)
            for panel in panels:
                lay.addWidget(panel)
            lay.addStretch(1)
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setWidget(column)
            sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            sa.setMinimumWidth(min_w)
            self.splitter.addWidget(sa)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([480, 1000])

        self.instrument.connect_requested.connect(self.connect_instrument)
        self.instrument.disconnect_requested.connect(self.disconnect_instrument)
        self.instrument.test_requested.connect(self.test_connection)
        self.calibration.load_check_requested.connect(self.load_check)
        self.sweep.analyze_requested.connect(self.analyze)
        self.sweep.cancel_requested.connect(self.cancel)
        state.connection_changed.connect(self._refresh_connection)
        state.busy_changed.connect(self._refresh_connection)
        state.assessment_changed.connect(lambda: self.results.refresh(self.state))
        self._load_saved()
        self._refresh_connection()
        self.results.refresh(state)

    # -- settings ---------------------------------------------------------------------------
    def _load_saved(self) -> None:
        self.instrument.load(self.state.settings)
        self.sweep.load(self.state.settings)
        self.calibration.load(self.state.saved_calibration())
        self.antenna.load(self.state.saved_antenna())
        self.antenna.project.setText(str(self.state.settings.value("report/project", "")))

    def save_settings(self) -> None:
        s = self.state.settings
        self.instrument.save(s)
        self.sweep.save(s)
        self.state.save_json("calibration", self.calibration.meta().__dict__)
        self.state.save_json("antenna", self.antenna.info().__dict__)
        s.setValue("report/project", self.antenna.project.text().strip())

    def apply_measurement_fields(self) -> None:
        m = self.state.measurement
        if m:
            self.antenna.load(m.antenna)
            self.antenna.name.setText(m.name)
            self.antenna.project.setText(m.project)

    def _refresh_connection(self, *_) -> None:
        self.instrument.refresh(self.state)
        self.calibration.refresh(self.state)
        self.sweep.refresh(self.state)

    # -- jobs -------------------------------------------------------------------------------
    def _run(self, fn, on_done, label: str) -> Job | None:
        if self.state.busy:
            return None
        self.state.set_busy(True)
        self.sweep.show_progress(0.0, label)
        job = Job(fn, self)
        job.progress.connect(self.sweep.show_progress)
        job.finished.connect(on_done)
        job.failed.connect(self._on_failed)
        job.done.connect(lambda: self.state.set_busy(False))
        self.job = job
        job.start()
        self.job_started.emit(job)
        return job

    def _on_failed(self, exc, tb) -> None:
        msg = str(exc) if isinstance(exc, InstrumentError) else f"{type(exc).__name__}: {exc}"
        self.sweep.show_progress(0.0, msg)
        if self.state.backend and not self.state.backend.is_connected():
            self.state.set_backend(None, None)
        if not isinstance(exc, InstrumentError):
            QMessageBox.critical(self, tr("Unexpected error"), msg + "\n\n" + tb[-1500:])

    # -- connection -------------------------------------------------------------------------
    def connect_instrument(self) -> None:
        if self.state.backend:
            self.state.backend.disconnect()
            self.state.set_backend(None, None)
        backend = self.instrument.make_backend()

        def work(token, progress):
            progress(0.2, tr("Connecting …"))
            info = backend.connect()
            return info, backend.calibration_type()

        def done(res):
            info, cal = res
            self.state.gui_cal_type = cal
            self.state.set_backend(backend, info, backend.warnings)
            self.sweep.show_progress(1.0, tr("Connected"))
            self.save_settings()

        self._run(work, done, tr("Connecting …"))

    def disconnect_instrument(self) -> None:
        if self.state.backend:
            self.state.backend.disconnect()
        self.state.gui_cal_type = None
        self.state.set_backend(None, None)

    def test_connection(self) -> None:
        backend = self.state.backend or self.instrument.make_backend()
        temporary = self.state.backend is None

        def work(token, progress):
            t0 = time.monotonic()
            info = backend.connect() if temporary else backend.refresh_device()
            backend.ping()
            dt = time.monotonic() - t0
            cal = backend.calibration_type()
            if temporary:
                backend.disconnect()
            return info, cal, dt

        def done(res):
            info, cal, dt = res
            dev = info.serial or tr("no device attached")
            self.sweep.show_progress(1.0, tr("Connection OK") + f" – {info.idn or info.backend}, {dev}, "
                                     f"{dt * 1000:.0f} ms, " + tr("GUI calibration") + f": {cal}")
            if not temporary:
                self.state.gui_cal_type = cal
                self.state.connection_changed.emit()

        self._run(work, done, tr("Testing connection …"))

    # -- measurements -----------------------------------------------------------------------
    def _limits(self):
        return self.state.instrument.limits if self.state.instrument else None

    def analyze(self) -> None:
        if not self.sweep.passive_ok.isChecked():
            QMessageBox.warning(self, tr("Safety confirmation required"), tr(
                "Confirm that the DUT is a passive antenna and not connected to any radio, transmitter or DC supply."))
            return
        backend, limits = self.state.backend, self._limits()
        if backend is None or limits is None:
            self.sweep.show_progress(0.0, tr("Instrument limits unknown – is the device attached?"))
            return
        try:
            plan = self.sweep.build_plan(limits)
        except InstrumentError as e:
            self.sweep.show_progress(0.0, str(e))
            return
        if not plan.segments:
            self.sweep.omissions.setText("\n".join(plan.omissions))
            self.sweep.show_progress(0.0, tr("Nothing to measure within the instrument range."))
            return
        self.sweep.omissions.setText("\n".join("⚠ " + o for o in plan.omissions))
        antenna = self.antenna.info()
        cal = self.calibration.meta()
        name = antenna.name or f"Measurement {datetime.now().strftime('%Y-%m-%d %H:%M')}"
        repeats = self.sweep.repeats.value()
        self.save_settings()

        def work(token, progress):
            traces, extra = run_plan(backend, plan, token, progress, repeats=repeats)
            return new_measurement(name, antenna, backend, cal, plan, traces, extra)

        def done(m):
            m.operator, m.project = self.state.operator, self.antenna.project.text().strip()
            self.sweep.show_progress(1.0, tr("Measurement complete") + f" – {m.created_at}")
            self.state.gui_cal_type = m.calibration.gui_reported_type
            self.sweep.update_targets()
            self.state.set_measurement(m)
            self.measurement_done.emit()

        self._run(work, done, tr("Starting measurement …"))

    def cancel(self) -> None:
        if self.job and self.job.running():
            self.job.cancel()
            self.sweep.progress_text.setText(tr("Cancelling …"))

    def load_check(self) -> None:
        backend, limits = self.state.backend, self._limits()
        if backend is None or limits is None:
            self.sweep.show_progress(0.0, tr("Instrument limits unknown."))
            return
        try:
            plan = self.sweep.build_plan(limits)
        except InstrumentError:
            plan = custom_plan(max(limits.min_freq_hz, 1e6), limits.max_freq_hz, 401, limits)

        def work(token, progress):
            traces, _ = run_plan(backend, plan, token, progress)
            return traces

        def done(traces):
            worst = max(float(np.max(s11_db(t.data))) for t in traces if t.parameter == "S11")
            ok = worst <= -30
            self.calibration.verification = (f"50 Ω load check {utc_now_iso()[:16].replace('T', ' ')} UTC: worst S11 "
                                             f"{worst:.1f} dB ({'plausible' if ok else 'higher than expected'})")
            QMessageBox.information(self, tr("50 Ω load check"), (
                tr("Worst S11 with the load attached") + f": {worst:.1f} dB\n\n" + (
                    tr("Plausible for a calibrated port with a good load.") if ok else
                    tr("Higher than expected (≤ −30 dB typical). Check calibration, reference plane, load quality "
                       "and connectors.")) + "\n\n" + tr("This check does not certify the calibration.")))
            self.sweep.show_progress(1.0, tr("Load check") + f": {worst:.1f} dB")

        self._run(work, done, tr("Running 50 Ω load check …"))
