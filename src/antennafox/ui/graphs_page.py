# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Measurement graphs with markers and before/after overlays."""

from __future__ import annotations

import numpy as np
import pyqtgraph as pg
import pyqtgraph.exporters as pgexp
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..rf.assessment import point_values
from ..rf.explain import band_resonance
from . import theme
from .plots import SmithPlot, TracePlot, TraceSet, fmt_hz, fmt_z
from .state import AppState
from .widgets import item, muted, tr


class GraphsPage(QWidget):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.state = state
        self.markers: list[float] = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        bar = QHBoxLayout()
        self.title = QLabel(tr("Graphs"))
        self.title.setObjectName("H1")
        bar.addWidget(self.title)
        bar.addStretch(1)
        self.show_bands = QCheckBox(tr("Band overlays"))
        self.show_bands.setChecked(True)
        self.show_overlays = QCheckBox(tr("Comparison overlays"))
        self.show_overlays.setChecked(True)
        for cb in (self.show_bands, self.show_overlays):
            cb.toggled.connect(self.refresh)
            bar.addWidget(cb)
        self.btn_min = QPushButton(tr("Marker at minimum"))
        self.btn_min.clicked.connect(self._marker_min)
        self.btn_clear = QPushButton(tr("Clear markers"))
        self.btn_clear.clicked.connect(self._clear_markers)
        self.btn_export = QPushButton(tr("Export chart…"))
        self.btn_export.clicked.connect(self.export_current)
        for b in (self.btn_min, self.btn_clear, self.btn_export):
            bar.addWidget(b)
        lay.addLayout(bar)
        lay.addWidget(muted(tr("Drag a rectangle to zoom, right-click to reset, double-click to place a marker on the "
                               "nearest actual sweep point. Separate sweep segments are never connected, and nothing "
                               "is extrapolated beyond the measured span.")))
        split = QSplitter(Qt.Orientation.Vertical)
        self.tabs = QTabWidget()
        self.p_s11 = TracePlot("s11")
        self.p_vswr = TracePlot("vswr")
        self.p_smith = SmithPlot()
        self.p_rx = TracePlot("rx")
        self.p_s21 = TracePlot("s21")
        self.tabs.addTab(self.p_s11, "S11 / Return loss")
        self.tabs.addTab(self.p_vswr, "VSWR")
        self.tabs.addTab(self.p_smith, tr("Smith chart"))
        self.tabs.addTab(self.p_rx, "R / X")
        self.tabs.addTab(self.p_s21, "S21")
        for p in (self.p_s11, self.p_vswr, self.p_smith, self.p_rx, self.p_s21):
            p.marker_requested.connect(self.add_marker)
        split.addWidget(self.tabs)
        self.mtable = QTableWidget(0, 7)
        self.mtable.setHorizontalHeaderLabels([tr("Marker"), tr("Frequency"), "S11", tr("Return loss"), "VSWR",
                                               "Z", tr("Mismatch loss")])
        self.mtable.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.mtable.verticalHeader().setVisible(False)
        self.mtable_hint = muted(tr("No markers – double-click the plot or use 'Marker at minimum'."))
        table_box = QWidget()
        tl = QVBoxLayout(table_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.addWidget(self.mtable_hint)
        tl.addWidget(self.mtable, 1)
        split.addWidget(table_box)
        split.setSizes([620, 160])
        lay.addWidget(split, 1)
        state.measurement_changed.connect(self._on_new_measurement)
        state.assessment_changed.connect(self.refresh)
        state.comparison_changed.connect(self.refresh)
        state.settings_changed.connect(self.refresh)

    def _on_new_measurement(self):
        self.markers = self._auto_markers()
        self.refresh()

    def _auto_markers(self) -> list[float]:
        """Resonances inside or near the target bands, so the table is not empty after a test."""
        m = self.state.measurement
        if not m:
            return []
        out: list[float] = []
        for pid in self.state.target_ids or self.state.survey_ids:
            p = self.state.catalog.get(pid)
            res = band_resonance(m, p) if p else None
            if res and res.s11_db <= -6 and not any(abs(res.freq_hz - x) < 1e6 for x in out):
                out.append(res.freq_hz)
        return sorted(out)[:4]

    def _sets(self) -> list[TraceSet]:
        out = []
        if self.state.measurement:
            out.append(TraceSet(self.state.measurement, theme.TRACE_COLORS[0], primary=True))
        if self.show_overlays.isChecked():
            for i, m in enumerate(self.state.comparisons):
                if self.state.measurement and m.id == self.state.measurement.id:
                    continue
                out.append(TraceSet(m, theme.TRACE_COLORS[1 + i % (len(theme.TRACE_COLORS) - 1)]))
        return out

    def _bands(self):
        if not self.show_bands.isChecked() or not self.state.assessment:
            return []
        a = self.state.assessment
        out = []
        for pid in a.target_ids or a.survey_ids:
            p = self.state.catalog.get(pid)
            if p:
                out += [(x, y, p.label()) for x, y in p.intervals_hz]
        return out

    def refresh(self, *_):
        sets = self._sets()
        thr = self.state.thresholds.pass_s11_db
        bands = self._bands()
        self.p_s11.set_data(sets, bands, thr, self.markers)
        self.p_vswr.set_data(sets, bands, thr, self.markers)
        self.p_rx.set_data(sets, bands, thr, self.markers)
        has_s21 = any("S21" in s.measurement.parameters() for s in sets)
        self.tabs.setTabEnabled(4, has_s21)
        if has_s21:
            self.p_s21.set_data(sets, bands, thr, self.markers)
        self.p_smith.set_data(sets, self.markers)
        m = self.state.measurement
        self.title.setText(m.name + ("  [SIMULATION]" if m.simulated else "") if m else tr("Graphs"))
        self._fill_markers()

    def add_marker(self, f: float):
        if f not in self.markers:
            self.markers.append(f)
            self.markers.sort()
            self.refresh()

    def _marker_min(self):
        a = self.state.assessment
        if a and a.summary:
            self.add_marker(a.summary.min_s11_freq_hz)

    def _clear_markers(self):
        self.markers = []
        self.refresh()

    def _fill_markers(self):
        self.mtable.setRowCount(0)
        m = self.state.measurement
        self.mtable_hint.setVisible(bool(m) and not self.markers)
        if not m:
            return
        f, g = m.merged("S11")
        for i, mf in enumerate(self.markers):
            pv = point_values(f, g, mf)
            if not pv:
                continue
            r = self.mtable.rowCount()
            self.mtable.insertRow(r)
            mag = abs(pv["gamma"])
            ml = -10 * np.log10(1 - mag * mag) if mag < 1 else float("inf")
            vals = [f"M{i + 1}", fmt_hz(pv["freq_hz"]), f"{pv['s11_db']:.2f} dB", f"{-pv['s11_db']:.2f} dB",
                    f"{pv['vswr']:.3f}" if np.isfinite(pv["vswr"]) else "∞", fmt_z(pv["z"]),
                    f"{ml:.2f} dB" if np.isfinite(ml) else "∞"]
            for c, v in enumerate(vals):
                self.mtable.setItem(r, c, item(v, c > 0))

    def current_plot(self) -> pg.PlotWidget:
        w = self.tabs.currentWidget()
        assert isinstance(w, pg.PlotWidget)
        return w

    def export_current(self, path: str | None = None):
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, tr("Export chart"), "chart.png", "PNG (*.png);;SVG (*.svg)")
            if not path:
                return None
        plot_item = self.current_plot().getPlotItem()
        if path.lower().endswith(".svg"):
            ex = pgexp.SVGExporter(plot_item)
        else:
            ex = pgexp.ImageExporter(plot_item)
            ex.parameters()["width"] = 1800
        ex.export(path)
        return path
