# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Side-by-side comparison of several measurements per target band (no single overall "best")."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..gain.comparative import GainStatus
from ..measurement.model import ENVIRONMENTS
from ..measurement.session import SessionError, load_session, measurement_from_touchstone
from ..rf.bands import Conclusion
from ..rf.touchstone import TouchstoneError
from .widgets import fmt, item, muted, result_item, tr


def usable_bandwidth_hz(windows, a: float, b: float) -> float:
    return sum(max(0.0, min(y, b) - max(x, a)) for x, y in windows)


class ComparePage(QWidget):
    def __init__(self, state, parent=None):
        super().__init__(parent)
        self.state = state
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        bar = QHBoxLayout()
        t = QLabel(tr("Compare antennas"))
        t.setObjectName("H1")
        bar.addWidget(t)
        bar.addStretch(1)
        self.btn_add_cur = QPushButton(tr("Add current measurement"))
        self.btn_add_cur.clicked.connect(self.add_current)
        self.btn_add_file = QPushButton(tr("Add from file…"))
        self.btn_add_file.clicked.connect(self.add_file)
        self.btn_remove = QPushButton(tr("Remove selected"))
        self.btn_remove.clicked.connect(self.remove_selected)
        for b in (self.btn_add_cur, self.btn_add_file, self.btn_remove):
            bar.addWidget(b)
        lay.addLayout(bar)
        lay.addWidget(muted(tr("Compare per band: worst-case S11 over the full band, best S11, usable bandwidth "
                               "(S11 ≤ threshold within the band) and gain only where legitimately measured. There "
                               "is deliberately no single overall ranking – the lowest VSWR is not the best antenna. "
                               "Overlays appear on the Graphs page.")))
        self.table = QTableWidget(0, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table, 1)
        state.comparison_changed.connect(self.refresh)
        state.assessment_changed.connect(self.refresh)
        self.refresh()

    def _members(self):
        out = list(self.state.comparisons)
        m = self.state.measurement
        if m and all(c.id != m.id for c in out):
            out.insert(0, m)
        return out

    def refresh(self, *_):
        members = self._members()
        bands = self.state.target_ids or self.state.survey_ids
        cat = self.state.catalog
        cols = [tr("Measurement"), tr("Variant"), tr("Source")]
        for pid in bands:
            p = cat.get(pid)
            if p:
                cols += [f"{p.label()}\n" + tr("result"), tr("worst / best S11"), tr("usable BW")]
        cols.append(tr("Gain"))
        self.table.clear()
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(0)
        for m in members:
            a = self.state.assess_other(m)
            r = self.table.rowCount()
            self.table.insertRow(r)
            cur = self.state.measurement is not None and m.id == self.state.measurement.id
            self.table.setItem(r, 0, item(m.name + ("  (" + tr("current") + ")" if cur else "")))
            self.table.setItem(r, 1, item(m.antenna.variant or ENVIRONMENTS.get(m.antenna.environment, "–")))
            self.table.setItem(r, 2, item("SIMULATION" if m.simulated else m.instrument.backend))
            c = 3
            for pid in bands:
                p = cat.get(pid)
                if not p:
                    continue
                br = a.band_results.get(pid)
                if br is None:
                    c += 3
                    continue
                self.table.setItem(r, c, result_item(br))
                self.table.setItem(r, c + 1, item(f"{fmt(br.worst_s11_db)} / {fmt(br.best_s11_db)} dB", True))
                bw = 0.0
                if a.summary and br.conclusion != Conclusion.INSUFFICIENT:
                    bw = sum(usable_bandwidth_hz(a.summary.windows, x, y) for x, y in p.intervals_hz)
                self.table.setItem(r, c + 2, item(f"{bw / 1e6:.1f} MHz" if br.conclusion != Conclusion.INSUFFICIENT
                                                  else "–", True))
                c += 3
            self.table.setItem(r, c, item(tr("Not measured") if not cur else self._gain_text()))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

    def _gain_text(self):
        res = self.state.gain_study.estimate()
        if res is None:
            return tr("Not measured")
        return res.absolute_text() if res.status == GainStatus.ABSOLUTE else tr("relative only") + f": {res.relative_text()}"

    def add_current(self):
        if self.state.measurement:
            self.state.add_comparison(self.state.measurement)

    def add_file(self):
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Add measurements"), "",
                                                "AntennaFox / Touchstone (*.afox *.s1p *.s2p)")
        for p in paths:
            try:
                m = load_session(p)[0] if p.endswith(".afox") else measurement_from_touchstone(p)
            except (SessionError, TouchstoneError, OSError, KeyError, ValueError) as e:
                QMessageBox.warning(self, tr("Cannot load"), f"{p}\n{e}")
                continue
            self.state.add_comparison(m)

    def remove_selected(self):
        r = self.table.currentRow()
        if r < 0:
            return
        members = self._members()
        m = members[r]
        for i, c in enumerate(self.state.comparisons):
            if c.id == m.id:
                self.state.remove_comparison(i)
                return
