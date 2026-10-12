# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interactive pyqtgraph views. Each captured segment is drawn as its own curve, so gaps between segments are
never bridged visually; tooltips always report the nearest *actual* sweep point."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont

from ..measurement.model import Measurement
from ..rf.rfmath import impedance, s11_db, vswr
from ..rf.smith import grid_lines, vswr_circle
from . import theme


@dataclass
class TraceSet:
    measurement: Measurement
    color: str
    primary: bool = False


def fmt_hz(f: float) -> str:
    if f >= 1e9:
        return f"{f / 1e9:.6g} GHz"
    if f >= 1e6:
        return f"{f / 1e6:.6g} MHz"
    return f"{f / 1e3:.6g} kHz"


def fmt_z(z: complex) -> str:
    if not np.isfinite(z.real):
        return "∞"
    return f"{z.real:.1f} {'+' if z.imag >= 0 else '−'} j{abs(z.imag):.1f} Ω"


def point_tooltip(param: str, f: float, g: complex) -> str:
    arr = np.array([g])
    lines = [fmt_hz(f), f"{param} {s11_db(arr)[0]:.2f} dB"]
    if param in ("S11", "S22"):
        v = vswr(arr)[0]
        lines += [f"VSWR {v:.2f}" if np.isfinite(v) else "VSWR ∞", f"Z {fmt_z(complex(impedance(arr)[0]))}"]
    return "\n".join(lines)


@dataclass
class Dip:
    freq_hz: float
    s11_db: float
    color: str
    target_hz: float | None = None  # set when the dip lies outside its band: arrow points there
    label: str | None = None


_RIGHT_LABEL = {"color": theme.TEXT_FAINT, "position": 0.985, "anchors": [(1, 1), (1, 1)]}


def _small_font() -> QFont:
    f = QFont()
    f.setPixelSize(11)
    f.setBold(True)
    return f


def style_axes(pi: pg.PlotItem) -> None:
    tick_font = QFont()
    tick_font.setPixelSize(11)
    for name in ("left", "bottom"):
        ax = pi.getAxis(name)
        ax.setPen(pg.mkPen(theme.PLOT_AXIS))
        ax.setTextPen(pg.mkPen(theme.TEXT_DIM))
        ax.setStyle(tickFont=tick_font, tickLength=-5, tickTextOffset=6)
        ax.label.setFont(tick_font)
    pi.getViewBox().setBorder(pg.mkPen(theme.BORDER))


class _BasePlot(pg.PlotWidget):
    marker_requested = Signal(float)
    _crosshair_enabled = True

    def __init__(self, parent=None):
        super().__init__(parent)
        pi = self.getPlotItem()
        assert pi is not None
        self.pi: pg.PlotItem = pi
        self.vb: pg.ViewBox = pi.getViewBox()
        self.setBackground(theme.PLOT_BG)
        style_axes(self.pi)
        self.showGrid(x=True, y=True, alpha=theme.PLOT_GRID_ALPHA)
        self.vb.setMouseMode(pg.ViewBox.RectMode)
        self._crosshair = pg.InfiniteLine(angle=90, movable=False,
                                          pen=pg.mkPen(QColor(43, 196, 227, 70), width=1, style=Qt.PenStyle.DashLine))
        self._crosshair.setZValue(40)
        self._crosshair.setVisible(False)
        self._hover_dot = pg.ScatterPlotItem(size=8, pen=pg.mkPen(theme.PLOT_BG, width=1.5),
                                             brush=pg.mkBrush(theme.ORANGE))
        self._hover_dot.setZValue(50)
        self._hover_text = pg.TextItem("", color=theme.TEXT, fill=pg.mkBrush(17, 24, 33, 235),
                                       border=pg.mkPen(theme.BORDER_STRONG), anchor=(0, 1))
        self._hover_text.setZValue(51)
        for it in (self._crosshair, self._hover_dot, self._hover_text):
            self.pi.addItem(it, ignoreBounds=True)
        self._hover_text.setFont(theme.mono_font(11))
        self._primary: tuple[np.ndarray, np.ndarray, str] | None = None
        scene = cast(pg.GraphicsScene, self.scene())
        self._proxy = pg.SignalProxy(scene.sigMouseMoved, rateLimit=40, slot=self._on_move)
        scene.sigMouseClicked.connect(self._on_click)
        self._items: list = []

    def _add(self, item):
        if item.scene() is None:
            self.addItem(item)
        self._items.append(item)
        return item

    def clear_data(self):
        for it in self._items:
            self.removeItem(it)
        self._items = []
        self._primary = None
        self._hover_dot.setData([], [])
        self._hover_text.setText("")
        self._crosshair.setVisible(False)

    def _nearest_index(self, vp) -> int | None:
        raise NotImplementedError

    def _point_xy(self, i: int) -> tuple[float, float]:
        raise NotImplementedError

    def _on_move(self, evt):
        if self._primary is None:
            return
        pos = evt[0]
        if not self.sceneBoundingRect().contains(pos):
            return
        vp = self.vb.mapSceneToView(pos)
        i = self._nearest_index(vp)
        if i is None:
            self._hover_dot.setData([], [])
            self._hover_text.setText("")
            self._crosshair.setVisible(False)
            return
        f, g, param = self._primary
        x, y = self._point_xy(i)
        self._hover_dot.setData([x], [y])
        self._hover_text.setText(point_tooltip(param, float(f[i]), complex(g[i])))
        # keep the readout inside the view: flip to the left/below on the far half
        (x0, x1), (y0, y1) = self.vb.viewRange()
        self._hover_text.setAnchor((1.05 if x > (x0 + x1) / 2 else -0.05, -0.1 if y > (y0 + y1) / 2 else 1.1))
        self._hover_text.setPos(x, y)
        if self._crosshair_enabled:
            self._crosshair.setPos(x)
            self._crosshair.setVisible(True)

    def _on_click(self, evt):
        if evt.double() and self._primary is not None:
            vp = self.vb.mapSceneToView(evt.scenePos())
            i = self._nearest_index(vp)
            if i is not None:
                self.marker_requested.emit(float(self._primary[0][i]))


class TracePlot(_BasePlot):
    """kind: 's11' | 'vswr' | 'rx' | 's21'"""

    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        lab = {"color": theme.TEXT_DIM, "font-size": "11px"}
        self.setLabel("bottom", "Frequency", units="Hz", **lab)
        self.pi.getAxis("bottom").enableAutoSIPrefix(True)
        labels = {"s11": "S11 [dB]  (20·log10|Γ|)", "vswr": "VSWR", "rx": "Impedance [Ω]", "s21": "S21 [dB]"}
        self.setLabel("left", labels[kind], **lab)
        legend = self.addLegend(offset=(-12, 10), labelTextColor=theme.TEXT_DIM,
                                brush=pg.mkBrush(17, 24, 33, 220), pen=pg.mkPen(theme.BORDER))
        legend.setZValue(30)

    def _y(self, d: np.ndarray):
        if self.kind in ("s11", "s21"):
            return s11_db(d)
        if self.kind == "vswr":
            return np.clip(vswr(d), 1.0, 50.0)
        return impedance(d)

    def set_data(self, sets: list[TraceSet], bands: list[tuple] | None = None,
                 thr_db: float = -10.0, markers: list[float] | None = None, band_labels: bool = False,
                 good_zone: bool = False, dips: list[Dip] | None = None, show_legend: bool = True,
                 x_range: tuple[float, float] | None = None):
        """bands: (start, stop, label[, color]) – color tints the band by its verdict."""
        self.clear_data()
        if self.pi.legend is not None:
            self.pi.legend.setVisible(show_legend)
        param = "S21" if self.kind == "s21" else "S11"
        lo, hi = np.inf, -np.inf
        if good_zone and self.kind == "s11":
            zone = pg.LinearRegionItem((-200, thr_db), orientation="horizontal", movable=False,
                                       brush=pg.mkBrush(63, 179, 127, 16), pen=pg.mkPen(None))
            zone.setZValue(-20)
            self.pi.addItem(zone, ignoreBounds=True)  # must not drive autoscaling
            self._items.append(zone)
        for band in bands or []:
            b0, b1, label = band[:3]
            c = QColor(band[3]) if len(band) > 3 and band[3] else QColor(*theme.BAND_FILL[:3])
            fill = QColor(c)
            fill.setAlpha(34 if len(band) > 3 else theme.BAND_FILL[3])
            edge = QColor(c)
            edge.setAlpha(110 if len(band) > 3 else theme.BAND_EDGE[3])
            reg = pg.LinearRegionItem((b0, b1), movable=False, brush=pg.mkBrush(fill), pen=pg.mkPen(edge))
            reg.setZValue(-10)
            reg.setToolTip(label)
            self._add(reg)
            if band_labels and label:
                txt = pg.TextItem(label, color=edge.lighter(160) if len(band) > 3 else theme.AMBER, anchor=(0.5, 0))
                txt.setFont(_small_font())
                txt.setPos((b0 + b1) / 2, 0.6)
                self._add(txt)
        for ts in sets:
            segs = ts.measurement.segments(param)
            style = Qt.PenStyle.SolidLine if ts.primary else Qt.PenStyle.DashLine
            width = 1.8 if ts.primary else 1.3
            name = ts.measurement.name + (" [SIM]" if ts.measurement.simulated else "")
            for k, s in enumerate(segs):
                y = self._y(s.data)
                lo, hi = min(lo, s.start_hz), max(hi, s.stop_hz)
                if self.kind == "rx":
                    pen_r = pg.mkPen(ts.color, width=width, style=style)
                    pen_x = pg.mkPen(theme.AMBER if ts.primary else ts.color, width=width, style=Qt.PenStyle.DotLine)
                    self._add(self.plot(s.freq_hz, np.clip(np.real(y), -2000, 2000), pen=pen_r,
                                        name=f"R {name}" if k == 0 else None))
                    self._add(self.plot(s.freq_hz, np.clip(np.imag(y), -2000, 2000), pen=pen_x,
                                        name=f"X {name}" if k == 0 else None))
                else:
                    self._add(self.plot(s.freq_hz, y, pen=pg.mkPen(ts.color, width=width, style=style),
                                        name=name if k == 0 else None))
            if ts.primary:
                f, g = ts.measurement.merged(param)
                self._primary = (f, g, param) if len(f) else None
        if self.kind == "s11":
            self._add(pg.InfiniteLine(thr_db, angle=0, pen=pg.mkPen(theme.TEXT_FAINT, style=Qt.PenStyle.DashLine),
                                      label=f"{thr_db:.0f} dB", labelOpts=_RIGHT_LABEL))
        elif self.kind == "vswr":
            v = float(vswr(np.array([10 ** (thr_db / 20)]))[0])
            self._add(pg.InfiniteLine(v, angle=0, pen=pg.mkPen(theme.TEXT_FAINT, style=Qt.PenStyle.DashLine),
                                      label=f"VSWR {v:.2f}", labelOpts=_RIGHT_LABEL))
            self.vb.setYRange(1, 10)
        for mf in markers or []:
            self._add(pg.InfiniteLine(mf, angle=90, pen=pg.mkPen(theme.ORANGE, width=1.2),
                                      label=fmt_hz(mf), labelOpts={"color": theme.ORANGE, "position": 0.06, "anchors": [(0, 1), (0, 1)]}))
        for d in dips or []:
            self._add(pg.ScatterPlotItem([d.freq_hz], [d.s11_db], size=11, pen=pg.mkPen(theme.PLOT_BG, width=2),
                                         brush=pg.mkBrush(d.color)))
            # beside the dot: deep notches sit at the bottom edge of the plot
            lab = pg.TextItem(d.label or f"{fmt_hz(d.freq_hz)}\n{d.s11_db:.1f} dB", color=d.color,
                              anchor=(-0.08, 0.5) if d.label is None else (1.05, 0.5))
            lab.setFont(_small_font())
            lab.setPos(d.freq_hz, d.s11_db)
            self._add(lab)
            if d.target_hz is not None:
                # arrow from the dip toward the band it should move into
                arrow = pg.ArrowItem(angle=0 if d.target_hz < d.freq_hz else 180, tipAngle=35, headLen=12,
                                     tailLen=0, brush=pg.mkBrush(d.color), pen=pg.mkPen(None))
                y = d.s11_db / 2
                arrow.setPos(d.target_hz, y)
                self._add(arrow)
                self._add(self.plot([d.freq_hz, d.target_hz], [y, y],
                                    pen=pg.mkPen(d.color, width=1.5, style=Qt.PenStyle.DashLine)))
        if x_range is not None:
            lo, hi = x_range
        if np.isfinite(lo):
            self.vb.setXRange(lo, hi, padding=0.01)
        if not sets and self.kind == "s11":
            self.vb.setYRange(-40, 1, padding=0)
        if self.kind == "s11" and self._primary is not None:
            finite = s11_db(self._primary[1])
            finite = finite[np.isfinite(finite)]
            if len(finite):
                self.vb.setYRange(max(float(np.min(finite)), -80) - 3, 1, padding=0)

    def _nearest_index(self, vp) -> int | None:
        assert self._primary is not None
        f = self._primary[0]
        x = vp.x()
        if x < f[0] or x > f[-1]:
            return None
        i = int(np.clip(np.searchsorted(f, x), 1, len(f) - 1))
        return i if abs(f[i] - x) < abs(f[i - 1] - x) else i - 1

    def _point_xy(self, i: int):
        assert self._primary is not None
        f, g, _ = self._primary
        y = self._y(g[i : i + 1])
        if self.kind == "rx":
            return float(f[i]), float(y[0].real)
        return float(f[i]), float(y[0])


class SmithPlot(_BasePlot):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAspectLocked(True)
        self.hideAxis("left")
        self.hideAxis("bottom")
        self.showGrid(x=False, y=False)
        self._crosshair_enabled = False
        for x, y, kind in grid_lines():
            pen = pg.mkPen(theme.TEXT_DIM if kind == "unit" else theme.BORDER, width=1.2 if kind == "unit" else 0.8)
            self.plot(x, y, pen=pen)
        self.setXRange(-1.05, 1.05)
        self.setYRange(-1.05, 1.05)
        cx, cy = vswr_circle(1.92)
        self.plot(cx, cy, pen=pg.mkPen(theme.TEXT_DIM, style=Qt.PenStyle.DashLine))
        for r, xpos in ((0.2, 2 / 3), (0.5, 1 / 3), (1.0, 0.0), (2.0, -1 / 3)):
            t = pg.TextItem(f"{r:g}", color=theme.TEXT_FAINT, anchor=(0.5, 1))
            t.setPos(-xpos, 0)
            self.addItem(t)

    def set_data(self, sets: list[TraceSet], markers: list[float] | None = None):
        self.clear_data()
        for ts in sets:
            for s in ts.measurement.segments("S11"):
                self._add(self.plot(np.real(s.data), np.imag(s.data),
                                    pen=pg.mkPen(ts.color, width=1.8 if ts.primary else 1.2,
                                                 style=Qt.PenStyle.SolidLine if ts.primary else Qt.PenStyle.DashLine)))
            if ts.primary:
                f, g = ts.measurement.merged("S11")
                self._primary = (f, g, "S11") if len(f) else None
        if self._primary and markers:
            f, g, _ = self._primary
            idx = [int(np.argmin(np.abs(f - mf))) for mf in markers]
            self._add(pg.ScatterPlotItem([float(np.real(g[i])) for i in idx], [float(np.imag(g[i])) for i in idx], size=8,
                                         brush=pg.mkBrush(theme.ORANGE), pen=pg.mkPen(None)))

    def _nearest_index(self, vp) -> int | None:
        assert self._primary is not None
        g = self._primary[1]
        d = np.abs(g - complex(vp.x(), vp.y()))
        i = int(np.argmin(d))
        return i if d[i] < 0.08 else None

    def _point_xy(self, i: int):
        assert self._primary is not None
        g = self._primary[1]
        return float(np.real(g[i])), float(np.imag(g[i]))


class PolarPlot(pg.PlotWidget):
    """Normalized directional cut (dB) on a polar grid, -40…0 dB."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground(theme.PLOT_BG)
        self.setAspectLocked(True)
        self.hideAxis("left")
        self.hideAxis("bottom")
        t = np.linspace(0, 2 * np.pi, 361)
        for db in (0, -10, -20, -30):
            r = (db + 40) / 40
            self.plot(r * np.cos(t), r * np.sin(t), pen=pg.mkPen(theme.BORDER))
            lab = pg.TextItem(f"{db} dB", color=theme.TEXT_FAINT, anchor=(0, 1))
            lab.setPos(0.02, r)
            self.addItem(lab)
        for a in range(0, 360, 30):
            ar = np.deg2rad(a)
            self.plot([0, np.sin(ar)], [0, np.cos(ar)], pen=pg.mkPen(theme.BORDER))
            lab = pg.TextItem(f"{a}°", color=theme.TEXT_FAINT, anchor=(0.5, 0.5))
            lab.setPos(1.1 * np.sin(ar), 1.1 * np.cos(ar))
            self.addItem(lab)
        self.curve = self.plot([], [], pen=pg.mkPen(theme.CYAN, width=2), symbol="o", symbolSize=5,
                               symbolBrush=theme.CYAN)
        pi = self.getPlotItem()
        assert pi is not None
        pi.getViewBox().setRange(xRange=(-1.2, 1.2), yRange=(-1.2, 1.2))

    def set_cut(self, angles_deg: np.ndarray, rel_db: np.ndarray):
        if len(angles_deg) == 0:
            self.curve.setData([], [])
            return
        r = (np.clip(rel_db, -40, 0) + 40) / 40
        a = np.deg2rad(angles_deg)
        x, y = r * np.sin(a), r * np.cos(a)  # 0° at top, clockwise
        if len(x) > 2:
            x, y = np.append(x, x[0]), np.append(y, y[0])
        self.curve.setData(x, y)
