# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Small reusable widgets and helpers."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..catalog.catalog import Catalog
from ..rf.bands import BandResult, Conclusion, Coverage
from . import theme


def tr(text: str) -> str:
    return QCoreApplication.translate("AntennaFox", text)


CONCLUSION_TEXT = {
    Conclusion.WELL_MATCHED: "Well matched",
    Conclusion.MARGINAL: "Marginal",
    Conclusion.POOR: "Poor match",
    Conclusion.INSUFFICIENT: "Insufficient data",
}
COVERAGE_TEXT = {
    Coverage.MEASURED: "Measured",
    Coverage.PARTIAL: "Partially measured",
    Coverage.NOT_MEASURED: "Not measured",
}


class Card(QFrame):
    """Panel with a small uppercase title row; extra header widgets go into `header`."""

    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 10, 14, 12)
        self.lay.setSpacing(8)
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        if title:
            t = QLabel(title.upper())
            t.setObjectName("PanelTitle")
            self.header.addWidget(t)
        self.header.addStretch(1)
        self.lay.addLayout(self.header)


def muted(text: str = "", wrap: bool = True) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Muted")
    lab.setWordWrap(wrap)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def set_badge(lab: QLabel, text: str, color: str, fg: str | None = None) -> None:
    """Status pill: tinted background with colored text keeps status colors readable without shouting."""
    c = QColor(color)
    lab.setText(f"● {text}")
    lab.setStyleSheet(
        f"background: rgba({c.red()},{c.green()},{c.blue()},38); color:{fg or color}; border-radius:4px;"
        f"padding:2px 8px; font-weight:600; font-size:{theme.FONT_SMALL}px;")


class _ClickLabel(QLabel):
    def __init__(self, text: str, box: QCheckBox):
        super().__init__(text)
        self._box = box
        self.setWordWrap(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e):
        if self._box.isEnabled():
            self._box.toggle()
        super().mousePressEvent(e)


def wrap_check(text: str) -> tuple[QCheckBox, QWidget]:
    """Checkbox with a word-wrapping label (QCheckBox text never wraps and would force a wide layout)."""
    box = QCheckBox()
    box.setAccessibleName(text)
    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    label = _ClickLabel(text, box)
    label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    lay.addWidget(box, 0, Qt.AlignmentFlag.AlignTop)
    lay.addWidget(label, 1)
    return box, row


def faint(text: str = "") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("Faint")
    lab.setWordWrap(True)
    return lab


def notice(text: str, kind: str = "Notice") -> QLabel:
    lab = QLabel(text)
    lab.setObjectName(kind)
    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lab


def conclusion_color(c: Conclusion) -> str:
    return theme.CONCLUSION_COLORS[c.value]


def item(text: str, align_right: bool = False, tooltip: str = "") -> QTableWidgetItem:
    it = QTableWidgetItem(text)
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    if align_right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if tooltip:
        it.setToolTip(tooltip)
    return it


def result_item(r: BandResult) -> QTableWidgetItem:
    it = item(CONCLUSION_TEXT[r.conclusion], tooltip="\n".join(r.notes))
    it.setForeground(QColor(conclusion_color(r.conclusion)))
    f = it.font()
    f.setBold(True)
    it.setFont(f)
    return it


def fmt(v, spec=".1f", unit="") -> str:
    if v is None:
        return "–"
    if v == float("inf"):
        return "∞"
    return f"{v:{spec}}{unit}"


class PickerDialog(QDialog):
    """Checkable, grouped tree used for every 'pick some of these' choice in the app."""

    def __init__(self, parent, title: str, intro: str, headers: list[str],
                 groups: list[tuple[str, list[tuple[str, list[str], str]]]], selected: list[str],
                 max_selected: int | None = None, column_widths: tuple[int, ...] = (330,)):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(600, 620)
        self.max_selected = max_selected
        lay = QVBoxLayout(self)
        lay.addWidget(muted(intro))
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(headers)
        for i, w in enumerate(column_widths):
            self.tree.setColumnWidth(i, w)
        for g, rows in groups:
            gi = QTreeWidgetItem([g])
            gi.setFlags(gi.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.tree.addTopLevelItem(gi)
            for pid, cols, tip in rows:
                ci = QTreeWidgetItem(cols)
                ci.setData(0, Qt.ItemDataRole.UserRole, pid)
                ci.setToolTip(0, tip)
                ci.setCheckState(0, Qt.CheckState.Checked if pid in selected else Qt.CheckState.Unchecked)
                gi.addChild(ci)
            gi.setExpanded(True)
        lay.addWidget(self.tree)
        self.count = muted()
        lay.addWidget(self.count)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.tree.itemChanged.connect(self._on_item_changed)
        self._update_count()

    def _on_item_changed(self, it: QTreeWidgetItem, col: int) -> None:
        over = self.max_selected is not None and len(self.selected_ids()) > self.max_selected
        if col == 0 and over and it.checkState(0) == Qt.CheckState.Checked:
            self.tree.blockSignals(True)
            it.setCheckState(0, Qt.CheckState.Unchecked)
            self.tree.blockSignals(False)
        self._update_count()

    def _update_count(self) -> None:
        n = len(self.selected_ids())
        self.count.setText(tr("%d of %d selected") % (n, self.max_selected) if self.max_selected is not None
                           else tr("%d selected") % n)

    def accept(self) -> None:
        n = len(self.selected_ids())
        if self.max_selected is not None and n > self.max_selected:
            QMessageBox.warning(self, self.windowTitle(), tr("Please tick at most %d entries.") % self.max_selected)
            return
        super().accept()

    def selected_ids(self) -> list[str]:
        out = []
        for i in range(self.tree.topLevelItemCount()):
            g = self.tree.topLevelItem(i)
            if g is None:
                continue
            for j in range(g.childCount()):
                c = g.child(j)
                if c is not None and c.checkState(0) == Qt.CheckState.Checked:
                    out.append(c.data(0, Qt.ItemDataRole.UserRole))
        return out


class BandPickerDialog(PickerDialog):
    """Catalog bands grouped as in the catalog."""

    def __init__(self, catalog: Catalog, selected: list[str], parent=None, title: str = "Select target bands"):
        groups = []
        for g in catalog.groups():
            rows = []
            for p in catalog.profiles:
                if p.group != g:
                    continue
                a, b = p.span
                rows.append((p.id, [p.label(), p.region, f"{a / 1e6:.3f}–{b / 1e6:.3f} MHz"], p.note()))
            groups.append((g, rows))
        super().__init__(parent, tr(title),
                         tr("Target bands drive the overall match verdict. Bands are evaluated over their full "
                            "required intervals; regional bands are not automatically applicable elsewhere."),
                         [tr("Band"), tr("Region"), tr("Range")], groups, selected)
