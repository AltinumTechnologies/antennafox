# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Wizard steps with plain form widgets: safety checklist, geometry, path losses, result."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout, QLabel, QWidget

from ...gain.comparative import GainResult, GainStatus, far_field_distance_m
from ...gain.study import GainStudy
from ...rf.rfmath import wavelength_m
from .. import theme
from ..plots import fmt_hz
from ..widgets import Card, muted, tr

CHECKLIST = [
    "DUT and reference antenna are disconnected from all radios, transmitters and DC/bias-tee supplies.",
    "Port power is low and both VNA ports see only passive antennas and cables.",
    "Transmit antenna on port 1 is fixed (mast/tripod) and will not move between measurements.",
    "Receive position on port 2 is marked (phase center, height, orientation) so reference and DUT sit identically.",
    "Cables are fixed with tape/clamps; the port 2 cable is not moved when swapping antennas.",
    "Polarization of TX, reference and DUT is aligned (e.g. all vertical).",
    "Room: no people or metal objects near the line of sight; reflections are understood to bias results.",
    "Calibration/reference plane for S21 is known (response or full 2-port calibration in LibreVNA-GUI).",
]


class SetupDiagram(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)
        self.distance_text = "R"

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        p.fillRect(self.rect(), QColor(theme.PLOT_BG))
        ax, bx, base = w * 0.18, w * 0.82, h * 0.78

        def antenna(x, color, label, sub):
            p.setPen(QPen(QColor(color), 3))
            p.drawLine(QPointF(x, base), QPointF(x, base - 70))
            p.drawLine(QPointF(x - 14, base - 70), QPointF(x + 14, base - 70))
            p.setPen(QPen(QColor(theme.TEXT_DIM), 1))
            p.drawLine(QPointF(x - 30, base), QPointF(x + 30, base))
            p.setPen(QColor(theme.TEXT))
            f = QFont()
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(x - 90, base + 4, 180, 18), Qt.AlignmentFlag.AlignCenter, label)
            p.setFont(QFont())
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(QRectF(x - 160, base + 20, 320, 18), Qt.AlignmentFlag.AlignCenter, sub)

        antenna(ax, theme.CYAN, "TX antenna (fixed)", "VNA port 1")
        antenna(bx, theme.ORANGE, "Reference ⇄ DUT", "VNA port 2, same spot & polarization")
        p.setPen(QPen(QColor(theme.TEXT_DIM), 1, Qt.PenStyle.DashLine))
        y = base - 40
        p.drawLine(QPointF(ax + 22, y), QPointF(bx - 22, y))
        for i in range(1, 4):
            r = 18 * i
            p.setPen(QPen(QColor(theme.CYAN), 1.2))
            p.drawArc(QRectF(ax + 10 - r, base - 70 - r, 2 * r, 2 * r), -40 * 16, 80 * 16)
        p.setPen(QColor(theme.TEXT))
        p.drawText(QRectF(ax, y - 22, bx - ax, 18), Qt.AlignmentFlag.AlignCenter, f"distance {self.distance_text}")
        p.end()


class SafetyStep(Card):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(tr("Physical setup"), parent)
        self.diagram = SetupDiagram()
        self.lay.addWidget(self.diagram)
        self.checks: dict[str, QCheckBox] = {}
        for text in CHECKLIST:
            cb = QCheckBox(tr(text))
            cb.toggled.connect(self.changed)
            self.checks[text] = cb
            self.lay.addWidget(cb)
        warn = muted(tr("Indoor, non-anechoic rooms produce multipath reflections that can shift results by several "
                        "dB. Antenna coupling, cable movement, phase-center misalignment, near-field operation, "
                        "polarization mismatch and reference quality all add error. Repeat measurements."))
        warn.setObjectName("Caution")
        self.lay.addWidget(warn)
        self.lay.addStretch(1)

    def load(self, study: GainStudy) -> None:
        for text, cb in self.checks.items():
            cb.setChecked(study.checklist.get(text, False))

    def apply(self, study: GainStudy) -> None:
        study.checklist = {k: cb.isChecked() for k, cb in self.checks.items()}
        s = study.setup
        self.diagram.distance_text = f"R = {s.distance_m:.2f} m" if s.distance_m else "R"
        self.diagram.update()

    def confirmed_count(self) -> tuple[int, int]:
        return sum(1 for cb in self.checks.values() if cb.isChecked()), len(self.checks)


class GeometryStep(Card):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(tr("Frequency and geometry"), parent)
        f = QFormLayout()
        self.freq = QDoubleSpinBox()
        self.freq.setRange(0.1, 100000)
        self.freq.setDecimals(3)
        self.freq.setSuffix(" MHz")
        self.freq.setValue(2440)
        self.bw = QDoubleSpinBox()
        self.bw.setRange(0, 1000)
        self.bw.setDecimals(3)
        self.bw.setSuffix(" MHz")
        self.bw.setSpecialValueText(tr("auto (±0.5 %)"))
        self.dist = QDoubleSpinBox()
        self.dist.setRange(0, 100)
        self.dist.setDecimals(2)
        self.dist.setSuffix(" m")
        self.dmax = QDoubleSpinBox()
        self.dmax.setRange(0, 10)
        self.dmax.setDecimals(3)
        self.dmax.setSuffix(" m")
        self.env = QComboBox()
        self.env.addItem(tr("Indoor (reflective)"), "indoor")
        self.env.addItem(tr("Outdoor / open field"), "outdoor")
        self.env.addItem(tr("Anechoic chamber"), "anechoic")
        f.addRow(tr("Test frequency"), self.freq)
        f.addRow(tr("Analysis bandwidth"), self.bw)
        f.addRow(tr("Distance TX ↔ RX"), self.dist)
        f.addRow(tr("Largest antenna dimension D"), self.dmax)
        f.addRow(tr("Environment"), self.env)
        self.lay.addLayout(f)
        self.ff_label = QLabel()
        self.ff_label.setWordWrap(True)
        self.lay.addWidget(self.ff_label)
        for w in (self.freq, self.bw, self.dist, self.dmax):
            w.valueChanged.connect(self.changed)
        self.env.currentIndexChanged.connect(self.changed)
        self.lay.addStretch(1)

    def load(self, study: GainStudy) -> None:
        s = study.setup
        self.freq.setValue(s.frequency_hz / 1e6)
        self.bw.setValue(s.analysis_bandwidth_hz / 1e6)
        self.dist.setValue(s.distance_m)
        self.dmax.setValue(s.largest_dimension_m)
        self.env.setCurrentIndex(max(0, self.env.findData(s.environment)))

    def apply(self, study: GainStudy) -> None:
        s = study.setup
        s.frequency_hz = self.freq.value() * 1e6
        s.analysis_bandwidth_hz = self.bw.value() * 1e6
        s.distance_m = self.dist.value()
        s.largest_dimension_m = self.dmax.value()
        s.environment = self.env.currentData()
        lam = wavelength_m(s.frequency_hz)
        if s.largest_dimension_m > 0:
            rmin = far_field_distance_m(s.frequency_hz, s.largest_dimension_m)
            ok = s.distance_m >= rmin
            self.ff_label.setText(
                f"λ = {lam * 100:.1f} cm · 2D²/λ = {2 * s.largest_dimension_m ** 2 / lam:.2f} m · "
                + tr("required") + f" R ≥ max(2D²/λ, 5D, 3λ) = {rmin:.2f} m → "
                + (tr("OK") if ok else tr("too close – absolute dBi will be refused")))
            self.ff_label.setStyleSheet(f"color:{theme.GOOD if ok else theme.BAD};")
        else:
            self.ff_label.setText(f"λ = {lam * 100:.1f} cm · " + tr("enter D to check the far-field condition"))
            self.ff_label.setStyleSheet(f"color:{theme.TEXT_DIM};")


class LossesStep(Card):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(tr("Path loss corrections and uncertainty"), parent)
        self.lay.addWidget(muted(tr("Enter only losses that differ between the reference and the DUT path (e.g. an "
                                    "adapter used only with the DUT). Common cables cancel in the substitution.")))
        f = QFormLayout()
        self.l_ref, self.l_dut, self.u_loss, self.u_pos, self.u_env = (QDoubleSpinBox() for _ in range(5))
        for sb, v in ((self.l_ref, 0), (self.l_dut, 0), (self.u_loss, 0.2), (self.u_pos, 0.5), (self.u_env, 1.0)):
            sb.setRange(0, 30)
            sb.setDecimals(2)
            sb.setSuffix(" dB")
            sb.setValue(v)
            sb.valueChanged.connect(self.changed)
        self.pol_ok = QCheckBox(tr("Same polarization/orientation for reference and DUT confirmed"))
        self.pos_ok = QCheckBox(tr("Same position/phase center for reference and DUT confirmed"))
        f.addRow(tr("Extra loss in reference path"), self.l_ref)
        f.addRow(tr("Extra loss in DUT path"), self.l_dut)
        f.addRow(tr("Loss uncertainty (k=1)"), self.u_loss)
        f.addRow(tr("Positioning uncertainty (k=1)"), self.u_pos)
        f.addRow(tr("Environment/multipath (k=1)"), self.u_env)
        self.lay.addLayout(f)
        for cb in (self.pol_ok, self.pos_ok):
            cb.toggled.connect(self.changed)
            self.lay.addWidget(cb)
        self.lay.addStretch(1)

    def load(self, study: GainStudy) -> None:
        s = study.setup
        self.l_ref.setValue(s.loss_ref_path_db)
        self.l_dut.setValue(s.loss_dut_path_db)
        self.u_loss.setValue(s.loss_uncertainty_db)
        self.u_pos.setValue(s.positioning_uncertainty_db)
        self.u_env.setValue(s.environment_uncertainty_db)
        self.pol_ok.setChecked(s.polarization_matched)
        self.pos_ok.setChecked(s.same_position_confirmed)

    def apply(self, study: GainStudy) -> None:
        s = study.setup
        s.loss_ref_path_db = self.l_ref.value()
        s.loss_dut_path_db = self.l_dut.value()
        s.loss_uncertainty_db = self.u_loss.value()
        s.positioning_uncertainty_db = self.u_pos.value()
        s.environment_uncertainty_db = self.u_env.value()
        s.polarization_matched = self.pol_ok.isChecked()
        s.same_position_confirmed = self.pos_ok.isChecked()


class ResultStep(Card):
    def __init__(self, parent=None):
        super().__init__(tr("Result"), parent)
        self.main = QLabel()
        self.main.setObjectName("Verdict")
        self.main.setWordWrap(True)
        self.rel = QLabel()
        self.rel.setWordWrap(True)
        self.detail = muted()
        self.lay.addWidget(self.main)
        self.lay.addWidget(self.rel)
        self.lay.addWidget(self.detail)
        self.lay.addStretch(1)

    def show_result(self, r: GainResult | None) -> None:
        if r is None:
            self.main.setText(tr("Gain: Not measured"))
            self.main.setStyleSheet(f"color:{theme.TEXT_DIM};")
            self.rel.setText(tr("Capture at least one reference and one DUT S21 measurement."))
            self.detail.setText("")
            return
        if r.status == GainStatus.ABSOLUTE:
            self.main.setText(tr("Estimated directional realized gain") + f": {r.absolute_text()}")
            self.main.setStyleSheet(f"color:{theme.CYAN};")
        else:
            self.main.setText(tr("Absolute gain: not measurable with this setup"))
            self.main.setStyleSheet(f"color:{theme.WARN};")
        self.rel.setText(tr("Relative to reference") + f": {r.relative_text()} @ {fmt_hz(r.frequency_hz)}")
        lines = []
        if r.convention_note:
            lines.append(r.convention_note)
        if r.budget:
            b = r.budget
            ref = f"{b.reference_db}" if b.reference_db is not None else "–"
            lines.append(tr("Uncertainty (k=1)") + f": reference {ref} dB, repeatability {b.repeatability_db:.2f} dB"
                         f"{' (assumed)' if b.repeatability_estimated else ''}, cable {b.cable_db:.2f} dB, "
                         f"positioning {b.positioning_db:.2f} dB, environment {b.environment_db:.2f} dB")
        lines.append(tr("Repeatability") + f": {r.repeatability}")
        lines += ["✗ " + x for x in r.reasons]
        lines += ["⚠ " + x for x in r.warnings]
        lines.append(tr("Valid only for the measured direction and polarization – not peak gain, not efficiency."))
        self.detail.setText("\n".join(lines))
