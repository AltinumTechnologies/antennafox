# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""S21 capture steps (reference and DUT) and the manual directional-cut step."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
)

from ...gain.comparative import GainSetup, PatternCut, S21Capture, s21_db_at
from ...measurement.model import utc_now_iso
from ...rf.touchstone import TouchstoneError, read_touchstone
from ..plots import PolarPlot
from ..widgets import Card, item, muted, tr


class CaptureStep(Card):
    capture_requested = Signal(str)
    changed = Signal()

    def __init__(self, role: str, parent=None):
        super().__init__(tr("Measure reference S21") if role == "ref" else tr("Measure DUT S21"), parent)
        self.role = role
        self.lay.addWidget(muted(tr("Each capture runs one S21 sweep around the test frequency. Take several captures, "
                                    "re-seating the antenna between them, to quantify repeatability.")))
        row = QHBoxLayout()
        self.btn_capture = QPushButton(tr("Capture S21"))
        self.btn_capture.setObjectName("Primary")
        self.btn_capture.clicked.connect(lambda: self.capture_requested.emit(self.role))
        btn_import = QPushButton(tr("Import S21 from .s2p…"))
        btn_import.clicked.connect(self._import)
        btn_remove = QPushButton(tr("Remove selected"))
        btn_remove.clicked.connect(self._remove_selected)
        for w in (self.btn_capture, btn_import, btn_remove):
            row.addWidget(w)
        row.addStretch(1)
        self.lay.addLayout(row)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("Captured"), tr("Source"), tr("S21 at test frequency")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.lay.addWidget(self.table)
        self.captures: list[S21Capture] = []

    def bind(self, captures: list[S21Capture]) -> None:
        """Edit the study's capture list in place so the page and the session see the same objects."""
        self.captures = captures

    def refresh(self, setup: GainSetup) -> None:
        bw = setup.analysis_bandwidth_hz or 0.01 * setup.frequency_hz
        self.table.setRowCount(0)
        for c in self.captures:
            r = self.table.rowCount()
            self.table.insertRow(r)
            v = s21_db_at(c, setup.frequency_hz, bw)
            self.table.setItem(r, 0, item(c.acquired_at))
            self.table.setItem(r, 1, item(("SIMULATION " if c.simulated else "") + (c.label or "")))
            self.table.setItem(r, 2, item("outside captured span" if v is None else f"{v:.2f} dB", True))

    def _remove_selected(self) -> None:
        r = self.table.currentRow()
        if 0 <= r < len(self.captures):
            del self.captures[r]
            self.changed.emit()

    def _import(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, tr("Import S21"), "", "Touchstone (*.s2p)")
        if not p:
            return
        try:
            ts = read_touchstone(p)
            if "S21" not in ts.s:
                raise TouchstoneError("file contains no S21")
        except (TouchstoneError, OSError) as e:
            QMessageBox.warning(self, tr("Import"), str(e))
            return
        self.captures.append(S21Capture(self.role, ts.freq_hz, ts.s["S21"], utc_now_iso(), label=Path(p).name))
        self.changed.emit()


class CutStep(Card):
    capture_requested = Signal(float)
    undo_requested = Signal()
    new_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(tr("Directional cut (manual rotation)"), parent)
        self.lay.addWidget(muted(tr("Keep the TX antenna fixed, rotate the DUT in steps and capture S21 at each angle. "
                                    "The plot is normalized to the strongest direction. A cut is one plane only – two "
                                    "planes still do not constitute a full 3D gain pattern.")))
        f = QFormLayout()
        self.plane = QComboBox()
        self.plane.addItem(tr("Azimuth (H-plane)"), "azimuth")
        self.plane.addItem(tr("Elevation (E-plane)"), "elevation")
        self.polarization = QLineEdit("vertical")
        self.fixed_antenna = QLineEdit(tr("TX antenna on port 1"))
        self.step = QSpinBox()
        self.step.setRange(1, 90)
        self.step.setValue(15)
        self.step.setSuffix("°")
        self.angle = QDoubleSpinBox()
        self.angle.setRange(0, 359.9)
        self.angle.setSuffix("°")
        f.addRow(tr("Plane"), self.plane)
        f.addRow(tr("Polarization"), self.polarization)
        f.addRow(tr("Fixed antenna"), self.fixed_antenna)
        f.addRow(tr("Step"), self.step)
        f.addRow(tr("Next angle"), self.angle)
        self.lay.addLayout(f)
        row = QHBoxLayout()
        self.btn_capture = QPushButton(tr("Capture at angle"))
        self.btn_capture.setObjectName("Primary")
        self.btn_capture.clicked.connect(lambda: self.capture_requested.emit(self.angle.value()))
        undo = QPushButton(tr("Remove last"))
        undo.clicked.connect(self.undo_requested)
        new = QPushButton(tr("New cut"))
        new.clicked.connect(self.new_requested)
        for w in (self.btn_capture, undo, new):
            row.addWidget(w)
        row.addStretch(1)
        self.lay.addLayout(row)
        h = QHBoxLayout()
        self.polar = PolarPlot()
        self.polar.setMinimumSize(320, 320)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels([tr("Angle"), "S21", tr("Normalized")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        h.addWidget(self.polar, 1)
        h.addWidget(self.table, 1)
        self.lay.addLayout(h)
        self.summary = muted()
        self.lay.addWidget(self.summary)

    def advance_angle(self) -> None:
        self.angle.setValue((self.angle.value() + self.step.value()) % 360)

    def show_cut(self, cut: PatternCut) -> None:
        angles, rel = cut.normalized()
        self.polar.set_cut(angles, rel)
        self.table.setRowCount(0)
        raw = {p.angle_deg % 360: p.s21_db for p in cut.points}
        for a, v in zip(angles, rel, strict=True):
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, item(f"{a:.1f}°"))
            self.table.setItem(r, 1, item(f"{raw[a]:.2f} dB", True))
            self.table.setItem(r, 2, item(f"{v:.2f} dB", True))
        s = cut.summary()
        if s:
            self.summary.setText(
                f"{s['n']} " + tr("angles") + f", max @ {s['max_angle_deg']:.0f}°, min {s['min_rel_db']:.1f} dB"
                + (", " + tr("full 360° circle") if s.get("full_circle") else ", " + tr("partial cut")))
        else:
            self.summary.setText("")
