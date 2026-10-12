# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Guided comparative gain / directional-cut wizard (two-antenna S21 substitution method)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...gain.comparative import PatternCut, PatternPoint, S21Capture, s21_db_at
from ...instrument.base import AcquisitionParams, InstrumentError
from ..state import AppState
from ..widgets import muted, tr
from ..worker import Job
from .capture_steps import CaptureStep, CutStep
from .reference_step import ReferenceStep
from .steps import GeometryStep, LossesStep, ResultStep, SafetyStep

STEP_TITLES = ["1 · Safety & setup", "2 · Geometry", "3 · Reference antenna", "4 · Losses & uncertainty",
               "5 · Measure reference", "6 · Measure DUT", "7 · Result", "8 · Directional cut (optional)"]
CUT_STEP = 7


class GainPage(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.job: Job | None = None
        self.safety = SafetyStep()
        self.geometry_step = GeometryStep()
        self.reference = ReferenceStep()
        self.losses = LossesStep()
        self.ref_capture = CaptureStep("ref")
        self.dut_capture = CaptureStep("dut")
        self.result = ResultStep()
        self.cut = CutStep()

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        title = QLabel(tr("Comparative gain & directional cut"))
        title.setObjectName("H1")
        root.addWidget(title)
        root.addWidget(muted(tr(
            "Estimates the directional realized gain of the DUT relative to a characterized reference antenna "
            "(substitution method): G_DUT ≈ G_ref + S21_DUT − S21_ref + Δloss. Absolute dBi is only reported when "
            "reference data, geometry and setup checks are valid; otherwise the result is a relative difference in "
            "dB. Never peak gain, efficiency or a full 3D pattern.")))
        body = QHBoxLayout()
        self.steps = QListWidget()
        self.steps.setObjectName("Steps")
        self.steps.addItems([tr(s) for s in STEP_TITLES])
        self.steps.setFixedWidth(230)
        self.stack = QStackedWidget()
        self.steps.currentRowChanged.connect(self._goto)
        body.addWidget(self.steps)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        nav = QHBoxLayout()
        self.btn_back = QPushButton(tr("Back"))
        self.btn_next = QPushButton(tr("Next"))
        self.btn_back.clicked.connect(lambda: self.steps.setCurrentRow(max(0, self.steps.currentRow() - 1)))
        self.btn_next.clicked.connect(lambda: self.steps.setCurrentRow(min(len(STEP_TITLES) - 1,
                                                                           self.steps.currentRow() + 1)))
        self.status = muted()
        nav.addWidget(self.status, 1)
        nav.addWidget(self.btn_back)
        nav.addWidget(self.btn_next)
        root.addLayout(nav)
        for step in (self.safety, self.geometry_step, self.reference, self.losses, self.ref_capture, self.dut_capture,
                     self.result, self.cut):
            sa = QScrollArea()
            sa.setWidgetResizable(True)
            sa.setWidget(step)
            self.stack.addWidget(sa)
        for form in self.stack.findChildren(QFormLayout):
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        for edit in self.stack.findChildren(QLineEdit):
            edit.setMinimumWidth(260)

        self._form_steps = (self.safety, self.geometry_step, self.reference, self.losses)
        for step in self._form_steps:
            step.changed.connect(self._sync)
        for cap in (self.ref_capture, self.dut_capture):
            cap.capture_requested.connect(self.capture)
            cap.changed.connect(self._sync)
        self.cut.capture_requested.connect(self.capture_angle)
        self.cut.undo_requested.connect(self._cut_undo)
        self.cut.new_requested.connect(self._cut_new)
        self.steps.setCurrentRow(0)
        state.measurement_changed.connect(self.load_from_state)
        state.busy_changed.connect(lambda _: self._refresh_buttons())
        state.connection_changed.connect(self._refresh_buttons)
        self.load_from_state()

    # -- model <-> widgets ---------------------------------------------------------------------
    def load_from_state(self) -> None:
        study = self.state.gain_study
        for step in self._form_steps:
            step.blockSignals(True)
            step.load(study)
            step.blockSignals(False)
        self.ref_capture.bind(study.ref_captures)
        self.dut_capture.bind(study.dut_captures)
        self._sync()

    def _sync(self, *_) -> None:
        study = self.state.gain_study
        self.geometry_step.apply(study)  # frequency first: the other steps display values derived from it
        for step in (self.safety, self.reference, self.losses):
            step.apply(study)
        done, total = self.safety.confirmed_count()
        self.status.setText(tr("Setup checklist") + f": {done}/{total} " + tr("confirmed"))
        for cap in (self.ref_capture, self.dut_capture):
            cap.refresh(study.setup)
        self.result.show_result(study.estimate())

    def _refresh_buttons(self) -> None:
        ok = self.state.connected and not self.state.busy
        for b in (self.ref_capture.btn_capture, self.dut_capture.btn_capture, self.cut.btn_capture):
            b.setEnabled(ok)

    def _goto(self, i: int) -> None:
        self.stack.setCurrentIndex(i)
        self.btn_back.setEnabled(i > 0)
        self.btn_next.setEnabled(i < len(STEP_TITLES) - 1)
        if i == CUT_STEP:
            self.cut.show_cut(self._current_cut())

    # -- acquisition ---------------------------------------------------------------------------
    def _acquire_s21(self, on_done) -> None:
        backend = self.state.backend
        if backend is None or not self.state.connected or self.state.busy:
            return
        if "S21" not in backend.capabilities().parameters:
            self.status.setText(tr("This instrument cannot measure S21; comparative gain needs two ports."))
            return
        setup = self.state.gain_study.setup
        f = setup.frequency_hz
        lim = self.state.instrument.limits if self.state.instrument else None
        span = max(0.05 * f, 4 * (setup.analysis_bandwidth_hz or 0))
        start, stop = f - span / 2, f + span / 2
        if lim:
            start, stop = max(start, lim.min_freq_hz), min(stop, lim.max_freq_hz)
        params = AcquisitionParams(start, stop, 201, ifbw_hz=1000, averages=4,
                                   power_dbm=-10 if lim is None else lim.max_power_dbm,
                                   parameters=("S21",), label="S21")
        simulated = self.state.simulated_backend
        label = (self.state.instrument.model if self.state.instrument else "") or backend.info().backend
        self.state.set_busy(True)
        self.status.setText(tr("Capturing S21 …"))

        def work(token, progress):
            return backend.acquire(params, token, progress)

        job = Job(work, self)
        job.finished.connect(lambda segs: on_done(segs[0], simulated, label))
        job.failed.connect(lambda e, tb: self.status.setText(str(e) if isinstance(e, InstrumentError) else tb[-400:]))
        job.done.connect(lambda: self.state.set_busy(False))
        self.job = job
        job.start()

    def capture(self, role: str) -> None:
        captures = self.state.gain_study.ref_captures if role == "ref" else self.state.gain_study.dut_captures

        def done(seg, simulated, label):
            captures.append(S21Capture(role, seg.freq_hz, seg.data, seg.acquired_at, label=label, simulated=simulated))
            self.status.setText(tr("Capture stored."))
            self._sync()
            self.state.assessment_changed.emit()

        self._acquire_s21(done)

    # -- directional cut -----------------------------------------------------------------------
    def _current_cut(self) -> PatternCut:
        study = self.state.gain_study
        f, plane, pol = study.setup.frequency_hz, self.cut.plane.currentData(), self.cut.polarization.text()
        for c in study.pattern_cuts:
            if c.plane == plane and c.frequency_hz == f and c.polarization == pol:
                return c
        c = PatternCut(plane, f, pol, self.cut.fixed_antenna.text())
        study.pattern_cuts.append(c)
        return c

    def capture_angle(self, angle: float) -> None:
        def done(seg, simulated, label):
            s = self.state.gain_study.setup
            cap = S21Capture("dut", seg.freq_hz, seg.data, seg.acquired_at, label=label, angle_deg=angle,
                             simulated=simulated)
            v = s21_db_at(cap, s.frequency_hz, s.analysis_bandwidth_hz or 0.01 * s.frequency_hz)
            if v is None:
                self.status.setText(tr("Test frequency outside captured span."))
                return
            self.add_cut_point(angle, v)
            self.cut.advance_angle()
            self.state.assessment_changed.emit()

        self._acquire_s21(done)

    def add_cut_point(self, angle: float, s21_db: float) -> None:
        cut = self._current_cut()
        cut.points = [p for p in cut.points if p.angle_deg != angle] + [PatternPoint(angle, s21_db)]
        self.cut.show_cut(cut)

    def _cut_undo(self) -> None:
        cut = self._current_cut()
        if cut.points:
            cut.points.pop()
            self.cut.show_cut(cut)

    def _cut_new(self) -> None:
        self.state.gain_study.pattern_cuts.remove(self._current_cut())
        self.cut.angle.setValue(0)
        self.cut.show_cut(self._current_cut())
