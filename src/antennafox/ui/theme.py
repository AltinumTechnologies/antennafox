# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""AntennaFox design tokens and the single application stylesheet.

Palette derived from the logo: deep navy/charcoal surfaces, fox orange for brand emphasis and primary actions, cyan
for technical highlights and the primary trace. Status colors (good/warn/bad) stay conventional and are never
replaced by brand colors.
"""

from __future__ import annotations

from pathlib import Path

import pyqtgraph as pg
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

# --- color tokens -------------------------------------------------------------------------
BG = "#0a0e14"
SURFACE = "#111821"
SURFACE_2 = "#17202b"
SURFACE_3 = "#1e2936"
BORDER = "#243041"
BORDER_STRONG = "#34445a"
TEXT = "#e6e9ee"
TEXT_DIM = "#8b9bb0"
TEXT_FAINT = "#5f6f82"
ORANGE = "#f57c1f"
ORANGE_HOVER = "#ff9142"
AMBER = "#ffa04a"
CYAN = "#2bc4e3"
TEAL = "#1fa6a6"
GOOD = "#3fb37f"
WARN = "#d9a521"
BAD = "#e0594f"
SIM = "#9b5de5"  # simulation is its own state, distinct from brand and error colors

PLOT_BG = "#0d131b"
PLOT_AXIS = "#3a4a5e"
PLOT_GRID_ALPHA = 0.11
TRACE_COLORS = [CYAN, AMBER, "#a78bfa", "#7dd87d", "#f472b6", "#60a5fa"]
BAND_FILL = (245, 124, 31, 22)
BAND_EDGE = (245, 124, 31, 60)

CONCLUSION_COLORS = {"well_matched": GOOD, "marginal": WARN, "poor": BAD, "insufficient": TEXT_FAINT}

# --- shape and type tokens ----------------------------------------------------------------
RADIUS = 6
RADIUS_LG = 8
FONT_BASE = 13
FONT_SMALL = 12
FONT_H1 = 18

_UI = (Path(__file__).resolve().parent.parent / "assets" / "ui").as_posix()


def _qss() -> str:
    return f"""
* {{ font-size: {FONT_BASE}px; outline: none; }}
QWidget {{ background: {BG}; color: {TEXT}; }}
QMainWindow::separator {{ background: {BORDER}; width: 1px; height: 1px; }}
QLabel, QCheckBox, QRadioButton {{ background: transparent; }}

/* panels */
QFrame#Card {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; }}
QFrame#Card QWidget {{ background: transparent; }}
QFrame#Card QLineEdit, QFrame#Card QSpinBox, QFrame#Card QDoubleSpinBox, QFrame#Card QComboBox,
QFrame#Card QDateTimeEdit, QFrame#Card QPlainTextEdit {{ background: {SURFACE_2}; }}
QFrame#Card QTableWidget {{ background: {SURFACE}; }}
QFrame#Card QPushButton {{ background: {SURFACE_2}; }}
QFrame#Card QPushButton:hover {{ background: {SURFACE_3}; }}
QFrame#Card QPushButton#Primary {{ background: {ORANGE}; }}
QFrame#Card QPushButton#Primary:hover {{ background: {ORANGE_HOVER}; }}
QFrame#Card QPushButton#Primary:disabled {{ background: #4a3423; }}
QLabel#PanelTitle {{ color: {TEXT_DIM}; font-size: {FONT_SMALL}px; font-weight: 600; letter-spacing: 0.6px; }}
QLabel#H1 {{ font-size: {FONT_H1}px; font-weight: 600; }}
QLabel#Muted {{ color: {TEXT_DIM}; }}
QLabel#Faint {{ color: {TEXT_FAINT}; font-size: {FONT_SMALL}px; }}
QLabel#Verdict {{ font-size: 16px; font-weight: 600; }}
QLabel#Notice, QFrame#Card QLabel#Notice {{ color: {TEXT}; background: {SURFACE_2}; border: 1px solid {BORDER}; border-left: 3px solid {CYAN};
    border-radius: 4px; padding: 8px 10px; }}
QLabel#Caution {{ color: {AMBER}; }}
QToolBar QWidget {{ background: transparent; }}
QLabel#SimBadge, QFrame#Card QLabel#SimBadge {{ background: {SIM}; color: white; font-weight: 700; font-size: {FONT_SMALL}px; border-radius: 4px;
    padding: 2px 8px; }}

/* quick test */
QLabel#StepTitle {{ font-size: {FONT_BASE + 2}px; font-weight: 600; }}
QLabel#ResultTitle {{ font-size: 19px; font-weight: 700; }}
QToolButton#UseCase {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px;
    padding: 8px 10px; color: {TEXT}; font-weight: 600; text-align: left; }}
QToolButton#UseCase:hover {{ background: {SURFACE_2}; border-color: {BORDER_STRONG}; }}
QToolButton#Link {{ border: none; background: transparent; color: {CYAN}; padding: 2px 4px; }}
QToolButton#Link:hover {{ text-decoration: underline; }}
QToolButton#UseCase:checked {{ background: rgba(245,124,31,0.12); border: 1px solid {ORANGE}; color: {TEXT}; }}
QToolButton#UseCase:disabled {{ color: {TEXT_FAINT}; }}
QProgressBar#MatchBar {{ max-height: 5px; background: {SURFACE_3}; }}

/* navigation */
QFrame#Sidebar {{ background: {SURFACE}; border-right: 1px solid {BORDER}; }}
QFrame#Sidebar QWidget {{ background: transparent; }}
QListWidget#Nav {{ background: transparent; border: none; padding: 4px 8px; }}
QListWidget#Nav::item {{ padding: 8px 10px; margin: 1px 0; border-radius: {RADIUS}px; color: {TEXT_DIM}; }}
QListWidget#Nav::item:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QListWidget#Nav::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}
QListWidget#Steps {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {RADIUS_LG}px; padding: 6px; }}
QListWidget#Steps::item {{ padding: 7px 10px; border-radius: {RADIUS}px; color: {TEXT_DIM}; }}
QListWidget#Steps::item:hover {{ background: {SURFACE_2}; }}
QListWidget#Steps::item:selected {{ background: {SURFACE_3}; color: {TEXT}; }}

/* inputs */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QDateTimeEdit, QPlainTextEdit, QTextEdit, QTextBrowser {{
    background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: {RADIUS}px; padding: 4px 8px;
    min-height: 20px; selection-background-color: {TEAL}; selection-color: white;
}}
QLineEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover, QDateTimeEdit:hover {{ border-color: {BORDER_STRONG}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus, QDateTimeEdit:focus, QPlainTextEdit:focus {{
    border-color: {CYAN};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ color: {TEXT_FAINT}; }}
QComboBox::drop-down, QDateTimeEdit::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow, QDateTimeEdit::down-arrow {{ image: url({_UI}/chevron-down.svg); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS}px;
    padding: 4px; selection-background-color: {SURFACE_3}; selection-color: {TEXT}; }}
QSpinBox::up-button, QDoubleSpinBox::up-button, QDateTimeEdit::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button, QDateTimeEdit::down-button {{
    border: none; width: 18px; background: transparent;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow, QDateTimeEdit::up-arrow {{ image: url({_UI}/chevron-up.svg); width: 9px; height: 9px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow, QDateTimeEdit::down-arrow {{ image: url({_UI}/chevron-down.svg); width: 9px; height: 9px; }}

/* buttons */
QPushButton {{ background: {SURFACE_2}; border: 1px solid {BORDER}; border-radius: {RADIUS}px; padding: 5px 14px;
    min-height: 20px; }}
QPushButton:hover {{ background: {SURFACE_3}; border-color: {BORDER_STRONG}; }}
QPushButton:pressed {{ background: {BORDER}; }}
QPushButton:focus {{ border-color: {CYAN}; }}
QPushButton:disabled {{ color: {TEXT_FAINT}; border-color: {SURFACE_2}; }}
QPushButton#Primary {{ background: {ORANGE}; color: #1a0c02; border: none; font-weight: 700; padding: 7px 20px; }}
QPushButton#Primary:hover {{ background: {ORANGE_HOVER}; }}
QPushButton#Primary:disabled {{ background: #4a3423; color: #9c8471; }}
QPushButton#Danger {{ color: {BAD}; }}
QPushButton#Danger:disabled {{ color: {TEXT_FAINT}; }}

/* checkboxes */
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {BORDER_STRONG}; border-radius: 4px;
    background: {SURFACE_2}; }}
QCheckBox::indicator:hover {{ border-color: {CYAN}; }}
QCheckBox::indicator:checked {{ background: {CYAN}; border-color: {CYAN}; image: url({_UI}/check.svg); }}
QCheckBox:disabled {{ color: {TEXT_FAINT}; }}

/* progress */
QProgressBar {{ background: {SURFACE_2}; border: none; border-radius: 3px; max-height: 6px; text-align: center;
    color: transparent; }}
QProgressBar::chunk {{ background: {CYAN}; border-radius: 3px; }}

/* tables */
QTableWidget, QTableView, QTreeWidget {{ background: {SURFACE}; alternate-background-color: #141c26;
    gridline-color: transparent; border: 1px solid {BORDER}; border-radius: {RADIUS}px;
    selection-background-color: {SURFACE_3}; selection-color: {TEXT}; }}
QTableWidget::item, QTreeWidget::item {{ padding: 3px 6px; border-bottom: 1px solid #18212c; }}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{ background: {SURFACE_2}; color: {TEXT_DIM}; border: none; border-bottom: 1px solid {BORDER};
    padding: 6px; font-weight: 600; font-size: {FONT_SMALL}px; }}
QTableCornerButton::section {{ background: {SURFACE_2}; border: none; }}

/* tabs */
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: {RADIUS}px; top: -1px; background: {PLOT_BG}; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {TEXT_DIM}; padding: 7px 14px; margin-right: 2px;
    border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {CYAN}; }}
QTabBar::tab:disabled {{ color: {TEXT_FAINT}; }}

/* chrome */
QToolBar {{ background: {SURFACE}; border: none; border-bottom: 1px solid {BORDER}; spacing: 2px; padding: 4px 8px; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 6px 6px; }}
QToolButton {{ background: transparent; border: 1px solid transparent; border-radius: {RADIUS}px; padding: 4px 8px;
    color: {TEXT_DIM}; }}
QToolButton:hover {{ background: {SURFACE_2}; color: {TEXT}; }}
QToolButton:pressed {{ background: {SURFACE_3}; }}
QToolButton:disabled {{ color: {TEXT_FAINT}; }}
QStatusBar {{ background: {SURFACE}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QStatusBar::item {{ border: none; }}
QStatusBar QLabel {{ color: {TEXT_DIM}; font-size: {FONT_SMALL}px; padding: 0 6px; }}
QMenuBar {{ background: {SURFACE}; }}
QMenuBar::item:selected {{ background: {SURFACE_2}; }}
QMenu {{ background: {SURFACE_2}; border: 1px solid {BORDER_STRONG}; border-radius: {RADIUS}px; padding: 4px; }}
QMenu::item {{ padding: 5px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background: {SURFACE_3}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:hover {{ background: {BORDER}; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle {{ background: {BORDER}; border-radius: 3px; min-height: 30px; min-width: 30px; }}
QScrollBar::handle:hover {{ background: {BORDER_STRONG}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QToolTip {{ background: {SURFACE_2}; color: {TEXT}; border: 1px solid {BORDER_STRONG}; border-radius: 4px; padding: 5px 7px; }}
QDialog, QMessageBox {{ background: {SURFACE}; }}
QDialog QLabel, QMessageBox QLabel {{ background: transparent; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, BG), (QPalette.ColorRole.WindowText, TEXT), (QPalette.ColorRole.Base, SURFACE_2),
        (QPalette.ColorRole.AlternateBase, SURFACE), (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.Button, SURFACE_2),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, TEAL), (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.ToolTipBase, SURFACE_2), (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.PlaceholderText, TEXT_FAINT),
        (QPalette.ColorRole.Link, CYAN),
    ]:
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(_qss())
    pg.setConfigOptions(antialias=True, background=PLOT_BG, foreground=TEXT_DIM)


def mono_font(size: int = FONT_BASE) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    f.setPixelSize(size)
    return f
