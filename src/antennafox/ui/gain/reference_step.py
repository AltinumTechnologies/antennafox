# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reference antenna step: characterization data, gain table, optional |Γ| for IEEE-gain conversion."""

from __future__ import annotations

import csv

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
)

from ...gain.comparative import GainConvention
from ...gain.study import GainStudy
from ...rf.touchstone import TouchstoneError, read_touchstone
from ..widgets import Card, muted, tr, wrap_check


class ReferenceStep(Card):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(tr("Characterized reference antenna"), parent)
        f = QFormLayout()
        self.name = QLineEdit()
        self.source = QLineEdit()
        self.source.setPlaceholderText(tr("certificate / calibration report ID and lab"))
        self.convention = QComboBox()
        self.convention.addItem(tr("Realized gain (includes mismatch)"), GainConvention.REALIZED.value)
        self.convention.addItem(tr("IEEE gain (excludes mismatch) – needs reference |Γ|"), GainConvention.IEEE_GAIN.value)
        self.tolerance = QDoubleSpinBox()
        self.tolerance.setRange(0, 10)
        self.tolerance.setDecimals(2)
        self.tolerance.setValue(1.0)
        self.tolerance.setSuffix(tr(" dB (k=1)"))
        self.polarization = QLineEdit("linear vertical")
        self.direction = QLineEdit("boresight")
        self.trusted, trusted_row = wrap_check(tr(
            "The gain values come from a real characterization of this exact antenna in the measured direction "
            "(not a datasheet typical value)"))
        f.addRow(tr("Name"), self.name)
        f.addRow(tr("Source"), self.source)
        f.addRow(tr("Convention"), self.convention)
        f.addRow(tr("Uncertainty"), self.tolerance)
        f.addRow(tr("Polarization"), self.polarization)
        f.addRow(tr("Characterized direction"), self.direction)
        self.lay.addLayout(f)
        self.lay.addWidget(trusted_row)
        self.lay.addWidget(QLabel(tr("Gain table (dBi vs frequency) – no extrapolation outside")))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels([tr("Frequency [MHz]"), tr("Gain [dBi]")])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(160)
        self.lay.addWidget(self.table)
        row = QHBoxLayout()
        for text, fn in ((tr("Add row"), self.add_row), (tr("Remove row"), self._remove_row),
                         (tr("Import CSV…"), self._import_csv), (tr("Reference |Γ| from .s1p/.s2p…"), self._import_gamma)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        self.lay.addLayout(row)
        self.gamma_label = muted()
        self.lay.addWidget(self.gamma_label)
        self._gamma_table: list[tuple[float, complex]] = []
        for w in (self.name, self.source, self.polarization, self.direction):
            w.textChanged.connect(self.changed)
        self.convention.currentIndexChanged.connect(self.changed)
        self.tolerance.valueChanged.connect(self.changed)
        self.trusted.toggled.connect(self.changed)
        self.table.itemChanged.connect(self.changed)

    def load(self, study: GainStudy) -> None:
        r = study.reference
        self.name.setText(r.name)
        self.source.setText(r.source)
        self.convention.setCurrentIndex(max(0, self.convention.findData(r.convention.value)))
        self.tolerance.setValue(r.tolerance_db)
        self.polarization.setText(r.polarization)
        self.direction.setText(r.characterized_direction)
        self.trusted.setChecked(r.trusted)
        self._gamma_table = list(r.gamma_table)
        self.table.setRowCount(0)
        for f_hz, g in r.gain_table:
            self.add_row(f_hz / 1e6, g)

    def apply(self, study: GainStudy) -> None:
        r = study.reference
        r.name = self.name.text()
        r.source = self.source.text()
        r.convention = GainConvention(self.convention.currentData())
        r.tolerance_db = self.tolerance.value()
        r.polarization = self.polarization.text()
        r.characterized_direction = self.direction.text()
        r.trusted = self.trusted.isChecked()
        r.gain_table = self._read_table()
        r.gamma_table = list(self._gamma_table)
        self.gamma_label.setText(tr("Reference |Γ| table") + f": {len(r.gamma_table)} " + tr("points")
                                 if r.gamma_table else tr("No reference |Γ| loaded (needed only for IEEE gain)."))

    def _read_table(self) -> list[tuple[float, float]]:
        out = []
        for i in range(self.table.rowCount()):
            a, b = self.table.item(i, 0), self.table.item(i, 1)
            if a is None or b is None:
                continue
            try:
                out.append((float(a.text()) * 1e6, float(b.text())))
            except ValueError:
                continue
        return sorted(out)

    def add_row(self, f_mhz: float | None = None, g: float | None = None) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem("" if f_mhz is None or f_mhz is False else f"{f_mhz:g}"))
        self.table.setItem(r, 1, QTableWidgetItem("" if g is None else f"{g:g}"))

    def _remove_row(self) -> None:
        r = self.table.currentRow()
        if r >= 0:
            self.table.removeRow(r)
            self.changed.emit()

    def _import_csv(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, tr("Reference gain CSV (MHz, dBi)"), "", "CSV (*.csv *.txt)")
        if not p:
            return
        rows = []
        with open(p, newline="", encoding="utf-8") as fh:
            for rec in csv.reader(fh):
                try:
                    rows.append((float(rec[0]), float(rec[1])))
                except (ValueError, IndexError):
                    continue
        if not rows:
            QMessageBox.warning(self, tr("Import"), tr("No numeric 'MHz, dBi' rows found."))
            return
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for f, g in rows:
            self.add_row(f, g)
        self.table.blockSignals(False)
        self.changed.emit()

    def _import_gamma(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, tr("Reference antenna S11"), "", "Touchstone (*.s1p *.s2p)")
        if not p:
            return
        try:
            ts = read_touchstone(p)
        except (TouchstoneError, OSError) as e:
            QMessageBox.warning(self, tr("Import"), str(e))
            return
        self._gamma_table = list(zip(ts.freq_hz.tolist(), ts.s["S11"].tolist(), strict=True))
        self.changed.emit()
