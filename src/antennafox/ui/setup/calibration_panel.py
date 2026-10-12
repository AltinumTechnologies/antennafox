# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Calibration metadata panel. The calibration itself is performed in LibreVNA-GUI; this records and checks it."""

from __future__ import annotations

from PySide6.QtCore import QDateTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from ...measurement.model import REFERENCE_PLANES, CalibrationMeta, CalibrationStatus
from .. import theme
from ..state import AppState
from ..widgets import Card, muted, set_badge, tr, wrap_check

CAL_STATUS_TEXT = {
    CalibrationStatus.NOT_VERIFIED: ("Not verified", theme.BAD),
    CalibrationStatus.USER_CONFIRMED: ("User confirmed", theme.WARN),
    CalibrationStatus.VALID_METADATA: ("Valid metadata", theme.GOOD),
}

CHECKLIST_HTML = """
<h3>Calibration checklist (performed in LibreVNA-GUI)</h3>
<ol>
<li>Let the LibreVNA warm up for at least 10–15 minutes.</li>
<li>Attach every cable, adapter and pigtail that will be used during the antenna measurement.</li>
<li>In LibreVNA-GUI open <i>Calibration</i> and select the calibration kit that matches your standards.</li>
<li>Set the sweep span to cover all frequencies you intend to measure (calibration is only valid inside it).</li>
<li>Measure OPEN, SHORT and LOAD at the <b>reference plane</b> – ideally the antenna connector, including adapters.</li>
<li>Activate the calibration (e.g. SOLT port 1) and save it.</li>
<li>Verify: the LOAD should read below about −30 dB; OPEN and SHORT should sit on the outer rim of the Smith chart at
the right and left, respectively. AntennaFox offers a 50 Ω load check for this step.</li>
<li>Do not move or bend the test cable between calibration and measurement.</li>
</ol>
<p>AntennaFox never performs or simulates a calibration itself. It records your confirmation, the reference plane and
the span as metadata and labels uncorrected data accordingly.</p>
"""


class CalibrationPanel(Card):
    load_check_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(tr("Calibration"), parent)
        self.gui_cal_type: str | None = None
        self.badge = QLabel()
        self.header.addWidget(self.badge)
        self.gui_label = muted(tr("Instrument calibration: unknown (not connected)"))
        self.lay.addWidget(self.gui_label)
        self.confirm, row = wrap_check(tr("Instrument calibrated (OSL/SOLT) at the reference plane"))
        self.lay.addWidget(row)
        form = QFormLayout()
        self.ref_plane = QComboBox()
        for key, text in REFERENCE_PLANES.items():
            self.ref_plane.addItem(text, key)
        form.addRow(tr("Reference plane"), self.ref_plane)
        dr = QHBoxLayout()
        self.date_known = QCheckBox(tr("known"))
        self.date = QDateTimeEdit(QDateTime.currentDateTime())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("yyyy-MM-dd HH:mm")
        dr.addWidget(self.date, 1)
        dr.addWidget(self.date_known)
        form.addRow(tr("Calibrated at"), dr)
        sr = QHBoxLayout()
        self.span_start, self.span_stop = QDoubleSpinBox(), QDoubleSpinBox()
        for sb in (self.span_start, self.span_stop):
            sb.setRange(0, 100000)
            sb.setDecimals(3)
            sb.setSuffix(" MHz")
            sb.setSpecialValueText(tr("unknown"))
        sr.addWidget(self.span_start)
        sr.addWidget(QLabel("–"))
        sr.addWidget(self.span_stop)
        form.addRow(tr("Calibrated span"), sr)
        self.kit = QLineEdit()
        self.kit.setPlaceholderText(tr("e.g. LibreCAL, generic SMA kit"))
        form.addRow(tr("Calibration kit"), self.kit)
        self.lay.addLayout(form)
        self.btn_load_check = QPushButton(tr("50 Ω load check"))
        self.btn_load_check.setToolTip(tr("Attach a 50 Ω load at the reference plane and run a quick sweep. "
                                          "Reports the worst S11; it does not mark the instrument as calibrated."))
        self.btn_load_check.clicked.connect(self.load_check_requested)
        self.btn_checklist = QPushButton(tr("Checklist…"))
        self.btn_checklist.clicked.connect(self.show_checklist)
        brow = QHBoxLayout()
        brow.addWidget(self.btn_load_check)
        brow.addWidget(self.btn_checklist)
        brow.addStretch(1)
        self.lay.addLayout(brow)
        for w in (self.confirm, self.date_known):
            w.toggled.connect(self.refresh_badge)
        for w in (self.span_start, self.span_stop):
            w.valueChanged.connect(self.refresh_badge)
        self.ref_plane.currentIndexChanged.connect(self.refresh_badge)
        self.refresh_badge()

    verification: str = ""

    def meta(self) -> CalibrationMeta:
        date = None
        if self.date_known.isChecked():
            date = self.date.dateTime().toUTC().toString("yyyy-MM-ddTHH:mm:ss") + "+00:00"
        return CalibrationMeta(
            gui_reported_type=self.gui_cal_type, user_confirmed=self.confirm.isChecked(), cal_date=date,
            cal_start_hz=self.span_start.value() * 1e6 or None, cal_stop_hz=self.span_stop.value() * 1e6 or None,
            reference_plane=self.ref_plane.currentData(), cal_kit=self.kit.text().strip(),
            verification=self.verification,
        )

    def load(self, cal: CalibrationMeta) -> None:
        self.confirm.setChecked(cal.user_confirmed)
        self.ref_plane.setCurrentIndex(max(self.ref_plane.findData(cal.reference_plane), 0))
        if cal.cal_date:
            self.date_known.setChecked(True)
            self.date.setDateTime(QDateTime.fromString(cal.cal_date[:16], "yyyy-MM-ddTHH:mm"))
        self.span_start.setValue((cal.cal_start_hz or 0) / 1e6)
        self.span_stop.setValue((cal.cal_stop_hz or 0) / 1e6)
        self.kit.setText(cal.cal_kit)
        self.verification = cal.verification

    def refresh_badge(self, *_) -> None:
        meta = self.meta()
        text, color = CAL_STATUS_TEXT[meta.status()]
        set_badge(self.badge, text, color)
        self.badge.setToolTip("\n".join(meta.issues(None, None)))

    def refresh(self, state: AppState) -> None:
        self.gui_cal_type = state.gui_cal_type
        if state.connected and state.instrument:
            active = state.gui_cal_type and state.gui_cal_type != "None"
            owner = state.instrument.host_software or tr("Instrument")
            self.gui_label.setText(f"{owner} " + tr("active calibration") + f": {state.gui_cal_type or tr('unknown')}"
                                   + ("" if active else " – " + tr("data will be labeled as uncorrected")))
        else:
            self.gui_label.setText(tr("Instrument calibration: unknown (not connected)"))
        self.btn_load_check.setEnabled(state.connected and not state.busy)
        self.refresh_badge()

    def show_checklist(self) -> None:
        d = QDialog(self)
        d.setWindowTitle(tr("Calibration checklist"))
        d.resize(560, 480)
        lay = QVBoxLayout(d)
        tb = QTextBrowser()
        tb.setHtml(CHECKLIST_HTML)
        lay.addWidget(tb)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        d.exec()
