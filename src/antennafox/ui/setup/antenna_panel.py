# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Antenna-under-test details (metadata stored with every measurement)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
)

from ...measurement.model import ENVIRONMENTS, AntennaInfo
from ..widgets import Card, tr

ANTENNA_TYPES = ["", "Whip / monopole", "Dipole", "PCB / chip antenna", "Patch", "Helical", "Yagi", "Panel",
                 "Collinear", "Active GNSS", "Other"]
CONNECTORS = ["", "SMA", "RP-SMA", "U.FL / IPEX MHF1", "MHF4", "N", "BNC", "Other"]


class AntennaPanel(Card):
    def __init__(self, parent=None):
        super().__init__(tr("Antenna under test"), parent)
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("e.g. Dual-band whip #3, bare"))
        form.addRow(tr("Measurement name"), self.name)
        self.antenna_type = QComboBox()
        self.antenna_type.setEditable(True)
        self.antenna_type.addItems(ANTENNA_TYPES)
        form.addRow(tr("Antenna type"), self.antenna_type)
        self.connector = QComboBox()
        self.connector.setEditable(True)
        self.connector.addItems(CONNECTORS)
        form.addRow(tr("Connector"), self.connector)
        self.cable = QLineEdit()
        self.cable.setPlaceholderText(tr("pigtail / cable / adapters"))
        form.addRow(tr("Cable / pigtail"), self.cable)
        self.mounting = QLineEdit()
        self.mounting.setPlaceholderText(tr("free space, on enclosure, ground plane …"))
        form.addRow(tr("Mounting"), self.mounting)
        self.variant = QLineEdit()
        self.variant.setPlaceholderText(tr("bare / in enclosure / other cable"))
        form.addRow(tr("Variant"), self.variant)
        self.environment = QComboBox()
        for key, label in ENVIRONMENTS.items():
            self.environment.addItem(tr(label), key)
        form.addRow(tr("Environment"), self.environment)
        self.dut_reference = QLineEdit()
        self.dut_reference.setPlaceholderText(tr("part number, lot, sample id"))
        form.addRow(tr("DUT reference"), self.dut_reference)
        self.project = QLineEdit()
        self.project.setPlaceholderText(tr("project or customer (for the report)"))
        form.addRow(tr("Project"), self.project)
        ph = QHBoxLayout()
        self.photo = QLineEdit()
        pick = QPushButton("…")
        pick.setFixedWidth(32)
        pick.clicked.connect(self._pick_photo)
        ph.addWidget(self.photo, 1)
        ph.addWidget(pick)
        form.addRow(tr("Photo"), ph)
        self.notes = QLineEdit()
        form.addRow(tr("Notes"), self.notes)
        self.lay.addLayout(form)
        self.active_gnss = QCheckBox(tr("Active GNSS antenna (contains an LNA)"))
        self.active_gnss.toggled.connect(self._on_active_gnss)
        self.lay.addWidget(self.active_gnss)

    def info(self) -> AntennaInfo:
        return AntennaInfo(
            name=self.name.text().strip(), antenna_type=self.antenna_type.currentText().strip(),
            connector=self.connector.currentText().strip(), cable=self.cable.text().strip(),
            mounting=self.mounting.text().strip(), variant=self.variant.text().strip(),
            photo_path=self.photo.text().strip(), active_gnss=self.active_gnss.isChecked(),
            notes=self.notes.text().strip(), dut_reference=self.dut_reference.text().strip(),
            environment=self.environment.currentData() or "",
        )

    def load(self, a: AntennaInfo) -> None:
        self.name.setText(a.name)
        self.antenna_type.setCurrentText(a.antenna_type)
        self.connector.setCurrentText(a.connector)
        self.cable.setText(a.cable)
        self.mounting.setText(a.mounting)
        self.variant.setText(a.variant)
        self.photo.setText(a.photo_path)
        self.notes.setText(a.notes)
        self.active_gnss.setChecked(a.active_gnss)
        self.dut_reference.setText(a.dut_reference)
        self.environment.setCurrentIndex(max(self.environment.findData(a.environment), 0))

    def _pick_photo(self) -> None:
        p, _ = QFileDialog.getOpenFileName(self, tr("Antenna photo"), "", "Images (*.png *.jpg *.jpeg *.heic)")
        if p:
            self.photo.setText(p)

    def _on_active_gnss(self, on: bool) -> None:
        if on:
            QMessageBox.information(self, tr("Active GNSS antenna"), tr(
                "AntennaFox never supplies bias-tee power. Measuring an unpowered active antenna shows the LNA "
                "input/output behavior, not the radiator match, and says nothing about LNA gain or noise figure. "
                "Measure the passive element where accessible."))
