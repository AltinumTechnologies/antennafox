# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Band / protocol suitability matrix and user catalog editor."""

from __future__ import annotations

import re

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..catalog.catalog import Catalog, make_user_profile, save_user_profiles
from ..rf.assessment import FREQUENCY_DISCLAIMER, PROTOCOL_DISCLAIMER
from .plots import fmt_hz
from .widgets import COVERAGE_TEXT, BandPickerDialog, fmt, item, muted, notice, result_item, tr


class AddBandDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Add user band"))
        form = QFormLayout(self)
        self.name = QLineEdit()
        self.start, self.stop = QDoubleSpinBox(), QDoubleSpinBox()
        for sb in (self.start, self.stop):
            sb.setRange(0.001, 100000)
            sb.setDecimals(4)
            sb.setSuffix(" MHz")
        self.region = QLineEdit("user")
        self.comment = QLineEdit()
        form.addRow(tr("Name"), self.name)
        form.addRow(tr("Start"), self.start)
        form.addRow(tr("Stop"), self.stop)
        form.addRow(tr("Region"), self.region)
        form.addRow(tr("Comment"), self.comment)
        form.addRow(muted(tr("User bands make no regulatory or protocol claims; they are plain frequency intervals.")))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        form.addRow(bb)


class BandsPage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        bar = QHBoxLayout()
        t = QLabel(tr("Band suitability"))
        t.setObjectName("H1")
        bar.addWidget(t)
        bar.addStretch(1)
        self.btn_all = QPushButton(tr("Evaluate bands…"))
        self.btn_all.setToolTip(tr("Choose which catalog bands are shown in addition to the target bands."))
        self.btn_all.clicked.connect(self._pick_survey)
        self.btn_add = QPushButton(tr("Add user band…"))
        self.btn_add.clicked.connect(self._add_user_band)
        self.btn_del = QPushButton(tr("Remove user band"))
        self.btn_del.clicked.connect(self._remove_user_band)
        for b in (self.btn_all, self.btn_add, self.btn_del):
            bar.addWidget(b)
        lay.addLayout(bar)
        lay.addWidget(notice(f"<b>{FREQUENCY_DISCLAIMER}</b><br>{PROTOCOL_DISCLAIMER}"))
        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels([
            tr("Band"), tr("Group"), tr("Region"), tr("Required range"), tr("Coverage"), tr("Worst S11"),
            tr("Median S11"), tr("Max VSWR"), tr("Matched"), tr("Points"), tr("Max gap / allowed"), tr("Result")])
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(False)
        lay.addWidget(self.table, 1)
        self.info = muted()
        lay.addWidget(self.info)
        state.assessment_changed.connect(self.refresh)
        state.catalog_changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_):
        a = self.state.assessment
        cat = self.state.catalog
        self.table.setRowCount(0)
        d = self.state.density
        self.info.setText(
            tr("Catalog version") + f" {cat.version}. " + tr("Coverage policy: neighbouring points cover a band only if "
                                                              "their spacing ≤ min(max(width/(n−1), 0.1 % f), 0.5 % f); "
                                                              "≥ ") + f"{d.min_points} " + tr("points per interval required. ")
            + tr("Thresholds") + f": ≤ {self.state.thresholds.pass_s11_db:.0f} dB / ≤ {self.state.thresholds.warn_s11_db:.0f} dB."
            + (f"\n⚠ {self.state.catalog_error}" if self.state.catalog_error else ""))
        if not a:
            return
        for pid in a.shown_ids:
            r = a.band_results[pid]
            p = cat.get(pid)
            if not p:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            rng = "; ".join(f"{x / 1e6:.4g}–{y / 1e6:.4g}" for x, y in p.intervals_hz) + " MHz"
            gaps = max((iv.max_gap_hz for iv in r.intervals), default=float("inf"))
            allowed = min((iv.allowed_gap_hz for iv in r.intervals), default=0)
            vals = [
                item(p.label() + ("" if pid in a.target_ids or not a.target_ids else "  " + tr("(survey)")),
                     tooltip=p.note()),
                item(p.group), item(p.region), item(rng),
                item(f"{COVERAGE_TEXT[r.coverage]} ({r.covered_fraction:.0%})"),
                item(fmt(r.worst_s11_db, ".1f", " dB"), True), item(fmt(r.median_s11_db, ".1f", " dB"), True),
                item(fmt(r.max_vswr, ".2f"), True),
                item("–" if r.matched_fraction is None else f"{r.matched_fraction:.0%}", True),
                item(str(r.n_points), True),
                item(("–" if gaps == float("inf") else fmt_hz(gaps)) + f" / {fmt_hz(allowed)}", True),
                result_item(r),
            ]
            for c, it in enumerate(vals):
                self.table.setItem(row, c, it)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

    def _pick_survey(self):
        d = BandPickerDialog(self.state.catalog, self.state.survey_ids, self, tr("Bands to evaluate (survey)"))
        if d.exec():
            self.state.set_targets(self.state.target_ids, d.selected_ids())

    def _add_user_band(self):
        dlg = AddBandDialog(self)
        if not dlg.exec():
            return
        self.add_user_band(dlg.name.text(), dlg.start.value() * 1e6, dlg.stop.value() * 1e6, dlg.region.text(),
                           dlg.comment.text())

    def add_user_band(self, name: str, start_hz: float, stop_hz: float, region: str = "user",
                      comment: str = "") -> str | None:
        """Validate and store a user band; returns its id or None after showing the reason."""
        name = name.strip()
        if not name or stop_hz <= start_hz:
            QMessageBox.warning(self, tr("Invalid band"), tr("A name and stop > start are required."))
            return None
        pid = "user_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        cat = self.state.catalog
        if cat.get(pid):
            QMessageBox.warning(self, tr("Invalid band"), tr("A band with this name already exists."))
            return None
        prof = make_user_profile(pid, name, start_hz, stop_hz, region.strip() or "user", comment.strip())
        save_user_profiles(self.state.user_catalog_path, cat.user_profiles() + [prof])
        self.state.reload_catalog()
        self.state.set_targets(self.state.target_ids, self.state.survey_ids + [pid])
        return pid

    def _remove_user_band(self):
        users = self.state.catalog.user_profiles()
        if not users:
            QMessageBox.information(self, tr("User bands"), tr("There are no user-defined bands."))
            return
        d = BandPickerDialog(Catalog(self.state.catalog.version, users), [], self, tr("Select user bands to remove"))
        if not d.exec():
            return
        remove = set(d.selected_ids())
        keep = [p for p in users if p.id not in remove]
        save_user_profiles(self.state.user_catalog_path, keep)
        self.state.target_ids = [i for i in self.state.target_ids if i not in remove]
        self.state.survey_ids = [i for i in self.state.survey_ids if i not in remove]
        self.state.reload_catalog()
