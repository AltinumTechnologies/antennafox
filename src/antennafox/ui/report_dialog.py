# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Report details asked for once per PDF export: who measured, for which project, which DUT, in what environment."""

from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLineEdit

from ..measurement.model import ENVIRONMENTS, Measurement
from .state import AppState
from .widgets import muted, tr


class ReportDetailsDialog(QDialog):
    def __init__(self, state: AppState, m: Measurement, parent=None):
        super().__init__(parent)
        self.state, self.m = state, m
        self.setWindowTitle(tr("Report details"))
        f = QFormLayout(self)
        f.addRow(muted(tr("Printed in the report header and stored with the session. All fields are optional.")))
        self.operator = QLineEdit(m.operator or state.operator)
        self.project = QLineEdit(m.project or str(state.settings.value("report/project", "")))
        self.dut = QLineEdit(m.antenna.dut_reference)
        self.variant = QLineEdit(m.antenna.variant)
        self.variant.setPlaceholderText(tr("bare / in enclosure / other cable – distinguishes compared measurements"))
        self.environment = QComboBox()
        for key, label in ENVIRONMENTS.items():
            self.environment.addItem(tr(label), key)
        self.environment.setCurrentIndex(max(self.environment.findData(m.antenna.environment), 0))
        self.notes = QLineEdit(m.notes)
        for w in (self.operator, self.project, self.dut, self.variant, self.notes):
            w.setMinimumWidth(320)
        f.addRow(tr("Operator"), self.operator)
        f.addRow(tr("Project"), self.project)
        f.addRow(tr("DUT reference"), self.dut)
        f.addRow(tr("Variant"), self.variant)
        f.addRow(tr("Environment"), self.environment)
        f.addRow(tr("Notes"), self.notes)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Write report"))
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def apply(self) -> None:
        """Store the entries in the measurement; operator and project also become the defaults."""
        m = self.m
        m.operator, m.project, m.notes = self.operator.text().strip(), self.project.text().strip(), self.notes.text().strip()
        m.antenna.dut_reference = self.dut.text().strip()
        m.antenna.variant = self.variant.text().strip()
        m.antenna.environment = self.environment.currentData() or ""
        self.state.operator = m.operator
        self.state.settings.setValue("report/project", m.project)
