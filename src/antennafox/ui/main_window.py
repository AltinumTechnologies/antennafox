# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Main window: navigation, file/export actions, settings, status bar."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QByteArray, QSize, Qt, Signal
from PySide6.QtGui import QAction, QIcon, QKeySequence, QPixmap
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QSizePolicy,
    QSpinBox,
    QStackedWidget,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, COMPANY_LEGAL, COMPANY_URL, TAGLINE, __version__, diagnostics
from ..gain.study import GainStudy
from ..instrument.base import InstrumentError
from ..measurement.session import SessionError, load_session, measurement_from_touchstone, save_session
from ..reports.export import export_csv, export_json, export_touchstone, touchstone_possible
from ..reports.pdf import build_report
from ..rf.bands import DensityPolicy, Thresholds
from ..rf.touchstone import TouchstoneError
from . import icons, theme
from .bands_page import BandsPage
from .compare_page import ComparePage
from .gain import GainPage
from .graphs_page import GraphsPage
from .quick_page import QuickTestPage
from .report_dialog import ReportDetailsDialog
from .setup import SetupPage
from .state import AppState
from .widgets import muted, tr

ASSETS = Path(__file__).resolve().parent.parent / "assets"


def _licenses_dir() -> Path:
    """LICENSES/ next to the package in a bundle, or the repository directory when running from source."""
    for cand in (ASSETS.parent.parent / "LICENSES", ASSETS.parent.parent.parent / "LICENSES"):
        if cand.is_dir():
            return cand
    return ASSETS.parent
log = logging.getLogger("antennafox.ui")


def app_icon() -> QIcon:
    for name in ("antennafox-icon.png", "antennafox-logo.png"):
        p = ASSETS / name
        if p.exists():
            return QIcon(str(p))
    return QIcon()


class _StatusLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)


class SettingsDialog(QDialog):
    def __init__(self, state: AppState, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Analysis settings"))
        f = QFormLayout(self)
        self.operator = QLineEdit(state.operator)
        self.operator.setPlaceholderText(tr("name printed on reports"))
        f.addRow(tr("Operator"), self.operator)
        t, d = state.thresholds, state.density
        self.pass_db, self.warn_db, self.frac = QDoubleSpinBox(), QDoubleSpinBox(), QDoubleSpinBox()
        for sb, v in ((self.pass_db, t.pass_s11_db), (self.warn_db, t.warn_s11_db)):
            sb.setRange(-40, -1)
            sb.setDecimals(1)
            sb.setSuffix(" dB")
            sb.setValue(v)
        self.frac.setRange(0, 1)
        self.frac.setSingleStep(0.05)
        self.frac.setValue(t.marginal_fraction)
        self.minpts = QSpinBox()
        self.minpts.setRange(2, 50)
        self.minpts.setValue(d.min_points)
        f.addRow(tr("Well matched if worst S11 ≤"), self.pass_db)
        f.addRow(tr("Marginal if worst S11 ≤"), self.warn_db)
        f.addRow(tr("…or matched fraction ≥"), self.frac)
        f.addRow(tr("Minimum points per band interval"), self.minpts)
        f.addRow(muted(tr("−10 dB corresponds to VSWR ≈ 1.92, −6 dB to VSWR ≈ 3.0. A good match alone never yields "
                          "a performance verdict.")))
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def values(self) -> tuple[Thresholds, DensityPolicy]:
        return (Thresholds(self.pass_db.value(), max(self.warn_db.value(), self.pass_db.value()), self.frac.value()),
                DensityPolicy(min_points=self.minpts.value()))


class MainWindow(QMainWindow):
    def __init__(self, state: AppState | None = None):
        super().__init__()
        self.state = state or AppState()
        self.setWindowTitle(f"{APP_NAME} – {TAGLINE}")
        self.setWindowIcon(app_icon())
        self.resize(1440, 900)
        self.setMinimumSize(1180, 720)

        central = QWidget()
        h = QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(200)
        sb = QVBoxLayout(sidebar)
        sb.setContentsMargins(0, 14, 0, 10)
        sb.setSpacing(6)
        brand = QHBoxLayout()
        brand.setContentsMargins(16, 0, 12, 8)
        logo = QLabel()
        icon = app_icon()
        if not icon.isNull():
            logo.setPixmap(icon.pixmap(34, 34))
        brand.addWidget(logo)
        name = QLabel(f"<span style='font-size:15px; font-weight:700; color:{theme.TEXT}'>Antenna</span>"
                      f"<span style='font-size:15px; font-weight:700; color:{theme.ORANGE}'>Fox</span><br>"
                      f"<span style='font-size:11px; color:{theme.TEXT_FAINT}'>Antenna analysis</span>")
        brand.addWidget(name, 1)
        sb.addLayout(brand)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setIconSize(QSize(18, 18))
        self.nav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        sb.addWidget(self.nav, 1)
        self.side_conn = QLabel()
        self.side_conn.setWordWrap(True)
        self.side_conn.setContentsMargins(16, 0, 12, 0)
        sb.addWidget(self.side_conn)
        self.stack = QStackedWidget()
        self.setup_page = SetupPage(self.state)
        self.quick = QuickTestPage(self.state, self.setup_page)
        self.graphs = GraphsPage(self.state)
        self.bands = BandsPage(self.state)
        self.gain = GainPage(self.state)
        self.compare = ComparePage(self.state)
        pages = ((tr("Test"), "analyze", self.quick), (tr("Graphs"), "graphs", self.graphs),
                 (tr("Bands"), "bands", self.bands), (tr("Compare"), "compare", self.compare),
                 (tr("Gain & pattern"), "gain", self.gain), (tr("Setup"), "settings", self.setup_page))
        for i, (title, ic, page) in enumerate(pages):
            it = QListWidgetItem(icons.icon(ic, theme.TEXT_DIM, theme.CYAN), title)
            it.setToolTip(f"{title} (Ctrl+{i + 1})")
            self.nav.addItem(it)
            self.stack.addWidget(page)
            sc = QAction(self)
            sc.setShortcut(QKeySequence(f"Ctrl+{i + 1}"))
            sc.triggered.connect(lambda _=False, r=i: self.nav.setCurrentRow(r))
            self.addAction(sc)
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        h.addWidget(sidebar)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.setup_page.measurement_done.connect(lambda: self.nav.setCurrentRow(0))
        self.quick.open_details.connect(lambda: self.nav.setCurrentRow(1))
        self.quick.open_setup.connect(lambda: self.nav.setCurrentRow(5))
        self.quick.open_gain.connect(lambda: self.nav.setCurrentRow(4))
        self.quick.demo_requested.connect(self.connect_demo)
        self.quick.save_requested.connect(lambda: self.save_session())
        self.quick.report_requested.connect(lambda: self.export_pdf())

        # Combos otherwise size to their longest entry and force horizontal overflow in narrow panels.
        for cb in self.findChildren(QComboBox):
            cb.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            cb.setMinimumContentsLength(8)
        # Spin boxes size their minimum to the widest value of their range; an explicit minimum overrides that.
        for sb in self.findChildren(QAbstractSpinBox):
            sb.setMinimumWidth(78)
        for cb in self.gain.findChildren(QComboBox):
            cb.setMinimumContentsLength(24)
        self._build_actions()
        self._build_statusbar()
        self.state.connection_changed.connect(self._refresh_status)
        self.state.measurement_changed.connect(self._refresh_status)
        self.state.measurement_changed.connect(self._refresh_actions)
        self.state.busy_changed.connect(self._refresh_actions)
        self._refresh_status()
        self._refresh_actions()
        geo = self.state.settings.value("window/geometry")
        if isinstance(geo, QByteArray):
            self.restoreGeometry(geo)

    # ------------------------------------------------------------------ actions
    def _act(self, text, slot, shortcut=None, tip="", icon_name=None):
        a = QAction(text, self)
        if icon_name:
            a.setIcon(icons.icon(icon_name, theme.TEXT_DIM, theme.TEXT))
        a.triggered.connect(lambda *_: slot())  # drop Qt's 'checked' argument: slots take optional paths
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setToolTip(tip)
        return a

    def _build_actions(self):
        self.a_open = self._act(tr("Open…"), self.open_session, QKeySequence.StandardKey.Open, icon_name="open")
        self.a_save = self._act(tr("Save"), self.save_session, QKeySequence.StandardKey.Save, icon_name="save")
        self.a_save_as = self._act(tr("Save as…"), lambda: self.save_session(ask=True), QKeySequence.StandardKey.SaveAs)
        self.a_import = self._act(tr("Import Touchstone…"), self.import_touchstone, icon_name="import")
        self.a_report = self._act(tr("PDF report…"), self.export_pdf, "Ctrl+P", icon_name="report")
        self.a_csv = self._act(tr("Export CSV…"), lambda: self.export("csv"))
        self.a_json = self._act(tr("Export JSON…"), lambda: self.export("json"))
        self.a_ts = self._act(tr("Export Touchstone…"), lambda: self.export("touchstone"), icon_name="export")
        self.a_chart = self._act(tr("Export chart…"), lambda: self.graphs.export_current(), icon_name="chart")
        self.a_settings = self._act(tr("Analysis settings…"), self.open_settings, "Ctrl+,", icon_name="settings")
        adapter = self.state.live_adapter()
        self._connect_label = tr("Connect") + f" {adapter.device_name}"
        self.a_connect = self._act(self._connect_label, self.connect_live, "Ctrl+K", icon_name="connect",
                                   tip=tr(adapter.connect_hint))
        self.a_demo = self._act(tr("Try demo"), self.connect_demo, icon_name="demo",
                                tip=tr("Simulated antenna – no hardware needed. Results are clearly marked as simulation."))
        self.a_disconnect = self._act(tr("Disconnect"), self.disconnect_instrument)
        self.a_diag = self._act(tr("Save diagnostics…"), self.save_diagnostics)
        self.a_about = self._act(tr("About AntennaFox"), self.about)
        mb = self.menuBar()
        fm = mb.addMenu(tr("&File"))
        for a in (self.a_open, self.a_save, self.a_save_as, None, self.a_import, None, self.a_report, self.a_csv,
                  self.a_json, self.a_ts, self.a_chart, None, self.a_settings):
            fm.addSeparator() if a is None else fm.addAction(a)
        hm = mb.addMenu(tr("&Help"))
        hm.addAction(self.a_diag)
        hm.addAction(self.a_about)
        tb = QToolBar(tr("Main"))
        tb.setMovable(False)
        tb.setIconSize(QSize(18, 18))
        tb.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for a in (self.a_open, self.a_save, None, self.a_import, None, self.a_report, self.a_ts, self.a_chart):
            tb.addSeparator() if a is None else tb.addAction(a)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        tb.addWidget(spacer)
        self.tb_conn = _StatusLabel()
        self.tb_conn.setContentsMargins(0, 0, 10, 0)
        self.tb_conn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.tb_conn.setToolTip(tr("Instrument details"))
        self.tb_conn.clicked.connect(self.instrument_details)
        tb.addWidget(self.tb_conn)
        for a in (self.a_connect, self.a_disconnect, self.a_demo):
            tb.addAction(a)
        tb.addSeparator()
        tb.addAction(self.a_settings)
        for a, tip in ((self.a_open, tr("Open session")), (self.a_save, tr("Save session")),
                       (self.a_settings, tr("Analysis settings"))):
            w = tb.widgetForAction(a)
            if isinstance(w, QToolButton):
                w.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
                w.setToolTip(f"{tip} ({a.shortcut().toString(QKeySequence.SequenceFormat.NativeText)})")
        self.addToolBar(tb)

    def _build_statusbar(self):
        sb = self.statusBar()
        self.st_conn = QLabel()
        self.st_meas = QLabel()
        self.st_sim = QLabel("SIMULATION")
        self.st_sim.setObjectName("SimBadge")
        sb.addWidget(self.st_conn)
        sb.addWidget(self.st_meas, 1)
        sb.addPermanentWidget(self.st_sim)
        sb.addPermanentWidget(QLabel(f"AntennaFox {__version__}"))

    def _refresh_status(self):
        st = self.state
        if st.connected and st.instrument:
            i = st.instrument
            col = theme.SIM if st.simulated_backend else (theme.GOOD if i.serial else theme.WARN)
            self.st_conn.setText(f"<span style='color:{col}'>●</span> {i.idn or i.backend}"
                                 + (f" · {i.serial}" if i.serial else ""))
            dev = tr("Simulation") if st.simulated_backend else (i.serial or tr("no device"))
            self.side_conn.setText(f"<span style='color:{col}'>●</span> <span style='color:{theme.TEXT_DIM}'>"
                                   f"{tr('Connected')}</span><br><span style='color:{theme.TEXT_FAINT}; "
                                   f"font-size:11px'>{dev}</span>")
            self.tb_conn.setText(f"<span style='color:{col}'>●</span> " + (tr("Demo") if st.simulated_backend else
                                 tr("Connected")) + f" <span style='color:{theme.TEXT_DIM}'>· {dev}</span>")
        else:
            self.st_conn.setText(f"<span style='color:{theme.TEXT_FAINT}'>○</span> " + tr("not connected"))
            self.side_conn.setText(f"<span style='color:{theme.TEXT_FAINT}'>○ {tr('Not connected')}</span>")
            self.tb_conn.setText(f"<span style='color:{theme.TEXT_FAINT}'>○ {tr('Not connected')}</span>")
        self.a_connect.setText(tr("Reconnect") if st.connected and not st.simulated_backend else self._connect_label)
        self._refresh_actions()
        m = st.measurement
        self.st_meas.setText((f"{m.name} · {m.created_at}" + (f" · {st.session_path.name}" if st.session_path else
                                                              " · " + tr("unsaved"))) if m else "")
        self.st_sim.setVisible(bool(st.simulated_backend or (m and m.simulated)))

    def _refresh_actions(self, *_):
        has = self.state.measurement is not None and not self.state.busy
        for a in (self.a_save, self.a_save_as, self.a_report, self.a_csv, self.a_json, self.a_ts, self.a_chart):
            a.setEnabled(has)
        self.a_connect.setEnabled(not self.state.busy)
        self.a_demo.setEnabled(not self.state.busy)
        self.a_disconnect.setEnabled(self.state.connected and not self.state.busy)

    # ------------------------------------------------------------------ connection
    def instrument_details(self):
        st = self.state
        info = st.instrument
        if not st.connected or info is None or st.backend is None:
            QMessageBox.information(self, tr("Instrument"), tr("Not connected. Use Connect in the toolbar."))
            return
        rows: list[tuple[str, str]] = [(tr("Identification"), info.idn or info.backend)]
        if info.host_software:
            rows.append((info.host_software, info.gui_version or "–"))
        rows += [
            (tr("Serial number"), info.serial or "–"),
            (tr("Firmware"), info.firmware or "–"),
            (tr("Hardware revision"), info.hardware_revision or "–"),
        ]
        if info.limits:
            lim = info.limits
            rows.append((tr("Frequency range"), f"{lim.min_freq_hz / 1e6:.3f} – {lim.max_freq_hz / 1e9:.3f} GHz"
                                                f" ({lim.source})"))
            rows.append((tr("Port power"), f"{lim.min_power_dbm:g} … {lim.max_power_dbm:g} dBm"))
            rows.append((tr("Points per sweep"), f"up to {lim.max_points}"))
        if st.busy:
            rows.append((tr("Live values"), tr("unavailable while a measurement is running")))
        else:
            try:
                rows += list(st.backend.device_status().items())
            except InstrumentError as e:
                rows.append((tr("Live values"), str(e)))
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Instrument details"))
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(20, 16, 20, 14)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        for r, (k, v) in enumerate(rows):
            key = QLabel(k)
            key.setStyleSheet(f"color:{theme.TEXT_DIM};")
            val = QLabel(v)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(key, r, 0)
            grid.addWidget(val, r, 1)
        lay.addLayout(grid)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    def connect_live(self):
        self.setup_page.instrument.set_simulation(False)
        self.setup_page.connect_instrument()

    def connect_demo(self):
        self.setup_page.instrument.set_simulation(True)
        self.setup_page.connect_instrument()

    def disconnect_instrument(self):
        self.setup_page.disconnect_instrument()

    # ------------------------------------------------------------------ files
    def _default_dir(self) -> str:
        return str(self.state.settings.value("files/dir", str(Path.home())))

    def _remember_dir(self, p: str | Path):
        self.state.settings.setValue("files/dir", str(Path(p).parent))

    def _derived(self) -> dict:
        a = self.state.assessment
        if not a:
            return {}
        return {"rating": a.rating.value, "evidence": a.evidence.value, "targets": a.target_ids,
                "bands": {k: {"conclusion": v.conclusion.value, "coverage": v.coverage.value,
                              "worst_s11_db": v.worst_s11_db} for k, v in a.band_results.items()}}

    def save_session(self, ask: bool = False, path: str | None = None):
        m = self.state.measurement
        if not m:
            return None
        if path is None:
            path = str(self.state.session_path) if self.state.session_path and not ask else None
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, tr("Save session"), str(Path(self._default_dir()) /
                                                  f"{m.name or 'measurement'}.afox"), "AntennaFox (*.afox)")
            if not path:
                return None
        p = save_session(path, m, gain_study=self.state.gain_study, derived=self._derived())
        log.info("session saved: %s", p.name)
        self.state.session_path = p
        self._remember_dir(p)
        self.state.settings.setValue("files/last_session", str(p))
        self._refresh_status()
        self.statusBar().showMessage(tr("Saved") + f" {p}", 5000)
        return p

    def open_session(self, path: str | None = None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, tr("Open session"), self._default_dir(), "AntennaFox (*.afox)")
            if not path:
                return
        try:
            m, study, _ = load_session(path)
        except (SessionError, OSError, KeyError, ValueError, TypeError) as e:
            log.warning("cannot open session %s: %s", Path(path).name, e)
            QMessageBox.critical(self, tr("Cannot open session"), str(e))
            return
        log.info("session opened: %s", Path(path).name)
        self._remember_dir(path)
        self.state.set_measurement(m, Path(path), study or GainStudy())
        self.setup_page.apply_measurement_fields()
        self.gain.load_from_state()
        self.nav.setCurrentRow(0)

    def import_touchstone(self, path: str | None = None):
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, tr("Import Touchstone"), self._default_dir(),
                                                  "Touchstone (*.s1p *.s2p)")
            if not path:
                return
        try:
            m = measurement_from_touchstone(path)
        except (TouchstoneError, SessionError, OSError) as e:
            QMessageBox.critical(self, tr("Import failed"), str(e))
            return
        self._remember_dir(path)
        self.state.set_measurement(m)
        self.setup_page.apply_measurement_fields()
        self.nav.setCurrentRow(0)

    def export(self, kind: str, path: str | None = None):
        m = self.state.measurement
        if not m:
            return None
        base = str(Path(self._default_dir()) / (m.name or "measurement"))
        if kind == "touchstone":
            ok, info = touchstone_possible(m)
            seg_idx = None
            if not ok:
                if len(m.segments("S11")) > 1:
                    QMessageBox.information(self, tr("Touchstone"), info + "\n\n" +
                                            tr("Exporting the first S11 segment only; use 'Save' for the complete data."))
                    seg_idx = 0
                else:
                    QMessageBox.warning(self, tr("Touchstone"), info)
                    return None
            if path is None:
                path, _ = QFileDialog.getSaveFileName(self, tr("Export Touchstone"), base, "Touchstone (*.s1p *.s2p)")
                if not path:
                    return None
            out = export_touchstone(m, path, segment_index=seg_idx)
        elif kind == "csv":
            if path is None:
                path, _ = QFileDialog.getSaveFileName(self, tr("Export CSV"), base + ".csv", "CSV (*.csv)")
                if not path:
                    return None
            out = export_csv(m, path)
        else:
            if path is None:
                path, _ = QFileDialog.getSaveFileName(self, tr("Export JSON"), base + ".json", "JSON (*.json)")
                if not path:
                    return None
            out = export_json(m, path, derived=self._derived())
        self._remember_dir(out)
        self.statusBar().showMessage(tr("Exported") + f" {out}", 5000)
        return out

    def export_pdf(self, path: str | None = None):
        m, a = self.state.measurement, self.state.assessment
        if not m or not a:
            return None
        if path is None:
            details = ReportDetailsDialog(self.state, m, self)
            if not details.exec():
                return None
            details.apply()
            self.setup_page.apply_measurement_fields()
            path, _ = QFileDialog.getSaveFileName(self, tr("PDF report"), str(Path(self._default_dir()) /
                                                  f"{m.name or 'report'}.pdf"), "PDF (*.pdf)")
            if not path:
                return None
        if self.state.session_path is None:
            ans = QMessageBox.question(self, tr("Save dataset?"), tr(
                "The report should reference a saved dataset. Save the session now?"))
            if ans == QMessageBox.StandardButton.Yes:
                self.save_session()
        st = self.state.gain_study
        study = st if (st.ref_captures or any(c.points for c in st.pattern_cuts)) else None
        comps = [c for c in self.state.comparisons if c.id != m.id]
        out = build_report(path, m, a, self.state.catalog, gain_study=study,
                           session_ref=str(self.state.session_path.name) if self.state.session_path else "",
                           compare=comps, markers=list(self.graphs.markers))
        self._remember_dir(out)
        self.statusBar().showMessage(tr("Report written") + f" {out}", 6000)
        return out

    def save_diagnostics(self, path: str | None = None):
        """Write a text file with environment, instrument state and the recent log for bug reports."""
        if path is None:
            path, _ = QFileDialog.getSaveFileName(self, tr("Save diagnostics"), str(Path(self._default_dir()) /
                                                  "antennafox-diagnostics.txt"), "Text (*.txt)")
            if not path:
                return None
        st = self.state
        info = st.instrument
        context = {
            "Instrument": (f"{info.idn or info.backend}; serial {info.serial or '-'}; firmware {info.firmware or '-'}; "
                           f"hardware {info.hardware_revision or '-'}; limits {info.limits}") if info else "not connected",
            "Backend simulated": str(st.simulated_backend),
            "Instrument calibration": str(st.gui_cal_type),
            "Measurement": (f"{st.measurement.name} ({st.measurement.id}), {len(st.measurement.traces)} traces, "
                            f"simulated={st.measurement.simulated}") if st.measurement else "none",
            "Targets": ", ".join(st.target_ids) or "-",
            "Log file": str(diagnostics.log_path() or "disabled"),
        }
        out = diagnostics.write_bundle(Path(path), context)
        self._remember_dir(out)
        self.statusBar().showMessage(tr("Diagnostics written") + f" {out}", 6000)
        return out

    def open_settings(self):
        d = SettingsDialog(self.state, self)
        if d.exec():
            self.state.operator = d.operator.text()
            self.state.set_thresholds(*d.values())

    def about(self):
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("About AntennaFox"))
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(QPixmap(str(ASSETS / "antennafox-logo.png")).scaledToWidth(
            180, Qt.TransformationMode.SmoothTransformation))
        logo.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        lay.addWidget(logo)
        text = QLabel(
            f"<h3 style='margin:0'>AntennaFox {__version__}</h3><p style='margin-top:2px'>{TAGLINE}</p>"
            f"<p>© 2026 {COMPANY_LEGAL} · <a style='color:{theme.CYAN}' href='{COMPANY_URL}'>{COMPANY_URL.split('//')[1]}</a></p>"
            "<p>Free software under the GNU General Public License, version 3 or (at your option) any later version. "
            "This program comes with ABSOLUTELY NO WARRANTY. Third-party components keep their own licenses; "
            f"see <a style='color:{theme.CYAN}' href='{_licenses_dir().as_uri()}'>the bundled license texts</a>.</p>"
            "<p>Built with Qt 6 and Qt for Python (PySide6), © The Qt Company Ltd. and other contributors, used under "
            "the GNU Lesser General Public License version 3. The Qt libraries can be replaced with your own build; "
            "see QT_NOTICE.md for source availability.</p>"
            "<p>AntennaFox is an independent project and is not affiliated with or endorsed by the LibreVNA project. "
            "It controls the official LibreVNA-GUI through its documented SCPI interface.</p>"
            "<p>S11 results describe impedance match only. Gain is reported solely from a valid comparative "
            "measurement against a characterized reference antenna.</p>"
            f"<p style='color:{theme.TEXT_DIM}'>Log file: {diagnostics.log_path() or tr('disabled')}</p>")
        text.setWordWrap(True)
        text.setOpenExternalLinks(True)
        text.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        text.setMaximumWidth(460)
        lay.addWidget(text)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()

    def closeEvent(self, e):
        if self.state.busy:
            if QMessageBox.question(self, tr("Measurement running"), tr("Cancel the running measurement and quit?")) \
                    != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            for job in (self.setup_page.job, self.gain.job):
                if job and job.running():
                    job.cancel()
                    job.wait(15000)
        self.setup_page.save_settings()
        self.state.settings.setValue("window/geometry", self.saveGeometry())
        if self.state.backend:
            self.state.backend.disconnect()
        super().closeEvent(e)
