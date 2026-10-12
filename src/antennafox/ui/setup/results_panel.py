# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Full assessment view: verdict, key values, band table, warnings."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHeaderView, QLabel, QTableWidget

from ...gain.comparative import GainStatus
from ...rf.assessment import EVIDENCE_TEXT, GAIN_NOT_MEASURED, MatchRating, point_values
from .. import theme
from ..plots import fmt_hz, fmt_z
from ..state import AppState
from ..widgets import COVERAGE_TEXT, Card, faint, fmt, item, muted, notice, result_item, tr
from .calibration_panel import CAL_STATUS_TEXT

RATING_COLORS = {
    MatchRating.GOOD: theme.GOOD, MatchRating.MARGINAL: theme.WARN, MatchRating.POOR: theme.BAD,
    MatchRating.SUSPECT: theme.WARN, MatchRating.INSUFFICIENT: theme.TEXT_FAINT, MatchRating.SURVEY: theme.CYAN,
}


class ResultsPanel(Card):
    def __init__(self, parent=None):
        super().__init__(tr("Result"), parent)
        self.sim_badge = QLabel("SIMULATION")
        self.sim_badge.setObjectName("SimBadge")
        self.header.addWidget(self.sim_badge)
        self.verdict = QLabel(tr("No measurement yet"))
        self.verdict.setObjectName("Verdict")
        self.verdict.setWordWrap(True)
        self.lay.addWidget(self.verdict)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self.kv: dict[str, QLabel] = {}
        keys = [("gain", tr("Gain")), ("pattern", tr("Radiation pattern")), ("evidence", tr("Evidence")),
                ("cal", tr("Calibration")), ("min", tr("Minimum S11")), ("z", tr("Z at minimum")),
                ("windows", tr("S11 ≤ threshold")), ("span", tr("Measured span"))]
        for i, (key, label) in enumerate(keys):
            lab = faint(label)
            lab.setWordWrap(False)
            val = QLabel("–")
            val.setWordWrap(True)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(lab, i // 2, (i % 2) * 2)
            grid.addWidget(val, i // 2, (i % 2) * 2 + 1)
            self.kv[key] = val
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(3, 1)
        self.lay.addLayout(grid)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels([tr("Band"), tr("Coverage"), tr("Worst S11"), tr("Max VSWR"),
                                              tr("Matched"), tr("Result")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(220)
        self.lay.addWidget(self.table, 1)
        self.warnings = muted()
        self.warnings.setObjectName("Caution")
        self.lay.addWidget(self.warnings)
        self.lay.addWidget(notice(tr(
            "<b>Band compatibility ≠ protocol/device compatibility.</b> Frequency match does not prove performance, "
            "gain, protocol operation, or regulatory suitability. An S11 measurement cannot validate antenna "
            "certification for a radio module, antenna diversity, transmit power limits, or link range.")))

    def refresh(self, state: AppState) -> None:
        a, m = state.assessment, state.measurement
        self.sim_badge.setVisible(bool(m and m.simulated))
        self.table.setRowCount(0)
        if not a or not m:
            self.verdict.setText(tr("No measurement yet"))
            self.verdict.setStyleSheet(f"color:{theme.TEXT_DIM};")
            for key, val in self.kv.items():
                val.setText(GAIN_NOT_MEASURED if key in ("gain", "pattern") else "–")
            self.warnings.setText("")
            return
        self.verdict.setText(a.headline)
        col = RATING_COLORS[a.rating]
        self.verdict.setStyleSheet(f"color:{col}; border-left:3px solid {col}; padding-left:10px;")
        self.kv["gain"].setText(self._gain_text(state))
        self.kv["pattern"].setText(tr("Measured directional cut(s) – not a 3D pattern")
                                   if any(c.points for c in state.gain_study.pattern_cuts) else GAIN_NOT_MEASURED)
        self.kv["evidence"].setText(EVIDENCE_TEXT[a.evidence])
        self.kv["cal"].setText(CAL_STATUS_TEXT[a.calibration_status][0] + (
            "" if not a.calibration_issues else " – " + a.calibration_issues[0]))
        f, g = m.merged("S11")
        if a.summary:
            sm = a.summary
            self.kv["min"].setText(f"{sm.min_s11_db:.1f} dB @ {fmt_hz(sm.min_s11_freq_hz)}")
            pv = point_values(f, g, sm.min_s11_freq_hz)
            self.kv["z"].setText(f"{fmt_z(pv['z'])}, VSWR {pv['vswr']:.2f}" if pv else "–")
            self.kv["windows"].setText(", ".join(f"{fmt_hz(x)}–{fmt_hz(y)}" for x, y in sm.windows[:4]) or tr("none"))
            self.kv["span"].setText(f"{fmt_hz(sm.start_hz)} – {fmt_hz(sm.stop_hz)}, {sm.n_points} pts, "
                                    f"{len(m.segments('S11'))} segment(s)")
        cat = state.catalog
        for pid in a.shown_ids:
            r = a.band_results[pid]
            row = self.table.rowCount()
            self.table.insertRow(row)
            p = cat.by_id(pid)
            label = p.label() + ("" if pid in a.target_ids or not a.target_ids else "  " + tr("(survey)"))
            self.table.setItem(row, 0, item(label, tooltip=p.note()))
            self.table.setItem(row, 1, item(f"{COVERAGE_TEXT[r.coverage]} ({r.covered_fraction:.0%})"))
            self.table.setItem(row, 2, item(fmt(r.worst_s11_db, ".1f", " dB"), True))
            self.table.setItem(row, 3, item(fmt(r.max_vswr, ".2f"), True))
            self.table.setItem(row, 4, item("–" if r.matched_fraction is None else f"{r.matched_fraction:.0%}", True))
            self.table.setItem(row, 5, result_item(r))
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.warnings.setText("\n".join("⚠ " + w for w in a.warnings + a.calibration_issues[1:]))

    @staticmethod
    def _gain_text(state: AppState) -> str:
        r = state.gain_study.estimate()
        if r is None:
            return GAIN_NOT_MEASURED
        if r.status == GainStatus.ABSOLUTE:
            return r.absolute_text() + f" @ {fmt_hz(r.frequency_hz)}"
        if r.status == GainStatus.RELATIVE_ONLY:
            return GAIN_NOT_MEASURED + " (dBi) – " + tr("relative") + f": {r.relative_text()}"
        return GAIN_NOT_MEASURED
