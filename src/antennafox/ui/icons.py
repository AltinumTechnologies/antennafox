# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Line icons drawn with QPainter on a 24×24 grid, so the app needs no icon font or image set."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

from . import theme

_cache: dict[tuple, QIcon] = {}


def _p(*pts) -> QPainterPath:
    path = QPainterPath(QPointF(*pts[0]))
    for pt in pts[1:]:
        path.lineTo(QPointF(*pt))
    return path


def _analyze(p: QPainter):
    p.drawPath(_p((3, 12), (7, 12), (9, 6), (12, 18), (15, 9), (17, 12), (21, 12)))


def _graphs(p: QPainter):
    p.drawRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
    p.drawPath(_p((6, 8), (9, 15), (12, 11), (15, 16), (18, 9)))


def _bands(p: QPainter):
    for y, w in ((7, 14), (12, 9), (17, 16)):
        p.drawLine(QPointF(4, y), QPointF(4 + w, y))
    p.drawLine(QPointF(4, 4), QPointF(4, 20))


def _gain(p: QPainter):
    p.drawLine(QPointF(7, 20), QPointF(7, 9))
    p.drawLine(QPointF(4, 9), QPointF(10, 9))
    for r in (4, 7):
        p.drawArc(QRectF(7 - r + 4, 9 - r, 2 * r, 2 * r), -50 * 16, 100 * 16)


def _compare(p: QPainter):
    p.drawRoundedRect(QRectF(3, 5, 8, 14), 1.5, 1.5)
    p.drawRoundedRect(QRectF(13, 5, 8, 14), 1.5, 1.5)
    p.drawLine(QPointF(5, 13), QPointF(9, 13))
    p.drawLine(QPointF(15, 10), QPointF(19, 10))


def _open(p: QPainter):
    p.drawPath(_p((3, 19), (3, 6), (9, 6), (11, 8), (20, 8), (20, 19), (3, 19)))


def _save(p: QPainter):
    p.drawPath(_p((4, 4), (17, 4), (20, 7), (20, 20), (4, 20), (4, 4)))
    p.drawRect(QRectF(8, 4, 7, 5))
    p.drawRect(QRectF(7, 13, 10, 7))


def _import(p: QPainter):
    p.drawLine(QPointF(12, 3), QPointF(12, 14))
    p.drawPath(_p((8, 10), (12, 14), (16, 10)))
    p.drawPath(_p((4, 15), (4, 20), (20, 20), (20, 15)))


def _export(p: QPainter):
    p.drawLine(QPointF(12, 14), QPointF(12, 3))
    p.drawPath(_p((8, 7), (12, 3), (16, 7)))
    p.drawPath(_p((4, 15), (4, 20), (20, 20), (20, 15)))


def _report(p: QPainter):
    p.drawPath(_p((6, 3), (15, 3), (19, 7), (19, 21), (6, 21), (6, 3)))
    for y in (11, 14, 17):
        p.drawLine(QPointF(9, y), QPointF(16, y))


def _settings(p: QPainter):
    for y, x in ((7, 15), (12, 8), (17, 13)):
        p.drawLine(QPointF(4, y), QPointF(20, y))
        p.setBrush(QColor(theme.SURFACE))
        p.drawEllipse(QPointF(x, y), 2.2, 2.2)
        p.setBrush(Qt.BrushStyle.NoBrush)


def _connect(p: QPainter):
    p.drawRoundedRect(QRectF(8, 9, 10, 8), 2, 2)
    p.drawLine(QPointF(11, 9), QPointF(11, 5))
    p.drawLine(QPointF(15, 9), QPointF(15, 5))
    p.drawPath(_p((13, 17), (13, 20), (6, 20)))


def _demo(p: QPainter):
    p.drawEllipse(QRectF(3.5, 3.5, 17, 17))
    p.drawPath(_p((10, 8), (16, 12), (10, 16), (10, 8)))


def _chart(p: QPainter):
    p.drawPath(_p((4, 4), (4, 20), (20, 20)))
    p.drawPath(_p((7, 16), (11, 10), (14, 13), (19, 6)))


_DRAW: dict[str, Callable[[QPainter], None]] = {
    "analyze": _analyze, "graphs": _graphs, "bands": _bands, "gain": _gain, "compare": _compare,
    "open": _open, "save": _save, "import": _import, "export": _export, "report": _report,
    "settings": _settings, "chart": _chart, "connect": _connect, "demo": _demo,
}


def icon(name: str, color: str = theme.TEXT_DIM, active: str | None = None) -> QIcon:
    key = (name, color, active)
    if key in _cache:
        return _cache[key]
    ic = QIcon()
    for mode, col in ((QIcon.Mode.Normal, color), (QIcon.Mode.Active, active or theme.TEXT), (QIcon.Mode.Selected, active or theme.TEXT),
                      (QIcon.Mode.Disabled, theme.TEXT_FAINT)):
        for scale in (1, 2):
            pm = QPixmap(24 * scale, 24 * scale)
            pm.setDevicePixelRatio(scale)
            pm.fill(Qt.GlobalColor.transparent)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(col), 1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            _DRAW[name](p)
            p.end()
            ic.addPixmap(pm, mode)
    _cache[key] = ic
    return ic
