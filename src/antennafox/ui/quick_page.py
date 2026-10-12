# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Default "Test" page: connect, choose what the antenna is for, start – with a plain-language result next to
the S11 plot. All acquisition and analysis is delegated to the Setup page, so behavior is identical;
advanced settings live there."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..instrument.base import InstrumentError
from ..rf.assessment import MatchRating
from ..rf.bands import Conclusion
from ..rf.explain import (
    SHORT_CONCLUSION,
    accepted_floor_percent,
    band_resonance,
    explain,
    mismatch_ceil_db,
    short_label,
)
from . import theme
from .plots import Dip, TracePlot, TraceSet
from .setup import SetupPage
from .state import MAX_TILES, AppState
from .widgets import PickerDialog, faint, muted, notice, set_badge, tr, wrap_check

USE_CASES = [
    ("wifi24", "Wi-Fi / Bluetooth", "2.4 GHz"),
    ("dualband", "Dual-band Wi-Fi", "2.4 + 5 GHz"),
    ("wifi5", "5 GHz Wi-Fi", "5150–5850 MHz"),
    ("lora868", "LoRa 868 MHz", "Meshtastic · MeshCore"),
    ("subghz_eu", "Sub-GHz EU", "433 + 868 MHz"),
    ("ism433", "433 MHz", "ISM / SRD"),
    ("gnss_l1", "GPS / GNSS", "L1 · 1575 MHz"),
    ("full", "Unknown antenna", "scan everything"),
]

RATING_COLOR = {
    MatchRating.GOOD: theme.GOOD, MatchRating.MARGINAL: theme.WARN, MatchRating.POOR: theme.BAD,
    MatchRating.SUSPECT: theme.WARN, MatchRating.INSUFFICIENT: theme.TEXT_DIM, MatchRating.SURVEY: theme.CYAN,
}
TILES_PER_ROW = 4
TILE_TEXT = {pid: (title, sub) for pid, title, sub in USE_CASES}

CONCLUSION_ICON = {
    Conclusion.WELL_MATCHED: ("✓", theme.GOOD), Conclusion.MARGINAL: ("!", theme.WARN),
    Conclusion.POOR: ("✕", theme.BAD), Conclusion.INSUFFICIENT: ("–", theme.TEXT_FAINT),
}


_SEVERITY = {Conclusion.POOR: 3, Conclusion.MARGINAL: 2, Conclusion.INSUFFICIENT: 1, Conclusion.WELL_MATCHED: 0}


def region_label(start: float, stop: float) -> str:
    fc = 0.5 * (start + stop)
    return f"{fc / 1e9:.1f} GHz" if fc >= 1e9 else f"{fc / 1e6:.0f} MHz"


def merge_regions(bands: list) -> list[dict]:
    """Group overlapping target bands (e.g. ISM 2.4 GHz, Wi-Fi channels, BLE) into one region per frequency
    range; each region shows its worst member, so one shaded area and one row summarize them."""
    items = sorted(((p.span[0], p.span[1], p, r, text) for p, r, text in bands), key=lambda x: x[0])
    regions: list[dict] = []
    for a, b, p, r, text in items:
        cur = regions[-1] if regions else None
        if cur and a <= cur["stop"] + 0.02 * a:
            cur["stop"] = max(cur["stop"], b)
            cur["members"].append((p, r, text))
            if _SEVERITY[r.conclusion] > _SEVERITY[cur["conclusion"]] or (
                    r.conclusion == cur["conclusion"] and (r.worst_s11_db or 0) > (cur["worst"].worst_s11_db or 0)):
                cur["conclusion"], cur["worst"], cur["profile"] = r.conclusion, r, p
        else:
            regions.append({"start": a, "stop": b, "members": [(p, r, text)], "conclusion": r.conclusion,
                            "worst": r, "profile": p})
    for reg in regions:
        reg["label"] = region_label(reg["start"], reg["stop"])
    return regions


def _step(n: int, text: str) -> QLabel:
    lab = QLabel(f"<span style='color:{theme.ORANGE}; font-weight:700'>{n}</span>&nbsp;&nbsp;{text}")
    lab.setObjectName("StepTitle")
    return lab


class QuickTestPage(QWidget):
    open_details = Signal()
    open_setup = Signal()
    open_gain = Signal()
    demo_requested = Signal()
    save_requested = Signal()
    report_requested = Signal()

    def __init__(self, state: AppState, setup_page: SetupPage, parent=None):
        super().__init__(parent)
        self.state = state
        self.setup = setup_page
        self.use_case: str | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 14)
        root.setSpacing(12)

        # --- step 1: connection --------------------------------------------------------
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(_step(1, tr("Connect")))
        self.conn_pill = QLabel()
        top.addWidget(self.conn_pill)
        top.addStretch(1)
        root.addLayout(top)
        self.conn_hint = faint()
        self.conn_hint.setTextFormat(Qt.TextFormat.RichText)
        self.conn_hint.linkActivated.connect(lambda _: self.demo_requested.emit())
        root.addWidget(self.conn_hint)

        # --- step 2: use case --------------------------------------------------------------
        hdr = QHBoxLayout()
        hdr.addWidget(_step(2, tr("What is the antenna for?")))
        hdr.addStretch(1)
        self.btn_tiles = QToolButton()
        self.btn_tiles.setObjectName("Link")
        self.btn_tiles.setText(tr("Choose tiles…"))
        self.btn_tiles.setToolTip(tr("Pick which presets – built-in or your own – appear here (up to %d).") % MAX_TILES)
        self.btn_tiles.clicked.connect(self._choose_tiles)
        hdr.addWidget(self.btn_tiles)
        root.addLayout(hdr)
        self.tile_grid = QGridLayout()
        self.tile_grid.setSpacing(8)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.case_buttons: dict[str, QToolButton] = {}
        self._build_tiles()
        root.addLayout(self.tile_grid)

        # --- step 3: start -----------------------------------------------------------------
        run = QHBoxLayout()
        run.setSpacing(12)
        run.addWidget(_step(3, tr("Test")))
        self.btn_start = QPushButton(tr("Start test"))
        self.btn_start.setObjectName("Primary")
        self.btn_start.setMinimumWidth(150)
        self.btn_start.clicked.connect(self.start)
        self.btn_cancel = QPushButton(tr("Cancel"))
        self.btn_cancel.setObjectName("Danger")
        self.btn_cancel.clicked.connect(self.setup.cancel)
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("Antenna name"))
        self.name.setToolTip(tr("Used for the result, saved session and report. Same field as in Setup."))
        self.name.setMinimumWidth(190)
        self.name.setMaximumWidth(260)
        self.name.setText(self.setup.antenna.name.text())
        self.name.textEdited.connect(self.setup.antenna.name.setText)
        self.setup.antenna.name.textChanged.connect(lambda t: t != self.name.text() and self.name.setText(t))
        run.addWidget(self.name)
        run.addWidget(self.btn_start)
        run.addWidget(self.btn_cancel)
        self.passive, passive_row = wrap_check(tr("Only the antenna is connected – no radio, no power supply"))
        self.passive.toggled.connect(self.setup.sweep.passive_ok.setChecked)
        self.setup.sweep.passive_ok.toggled.connect(self.passive.setChecked)
        run.addWidget(passive_row, 1, Qt.AlignmentFlag.AlignVCenter)
        root.addLayout(run)
        prog = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.status = muted()
        self.status.setWordWrap(False)
        prog.addWidget(self.progress, 1)
        prog.addWidget(self.status, 2)
        root.addLayout(prog)
        self.cal_hint = faint()
        self.cal_hint.setTextFormat(Qt.TextFormat.RichText)
        self.cal_hint.linkActivated.connect(lambda _: self.open_setup.emit())
        root.addWidget(self.cal_hint)

        # --- results -----------------------------------------------------------------------
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(10)
        plot_box = QFrame()
        plot_box.setObjectName("Card")
        pl = QVBoxLayout(plot_box)
        pl.setContentsMargins(10, 8, 10, 10)
        head = QHBoxLayout()
        t = QLabel(tr("REFLECTION (S11)"))
        t.setObjectName("PanelTitle")
        head.addWidget(t)
        head.addStretch(1)
        legend = faint(tr("shaded = target band · the dip should sit inside it, below the dashed line"))
        legend.setWordWrap(False)
        head.addWidget(legend)
        pl.addLayout(head)
        self.plot = TracePlot("s11")
        self.plot.setMinimumHeight(260)
        pl.addWidget(self.plot, 1)
        split.addWidget(plot_box)

        self.result = QFrame()
        self.result.setObjectName("Card")
        rl = QVBoxLayout(self.result)
        rl.setContentsMargins(16, 12, 16, 14)
        rl.setSpacing(8)
        rh = QHBoxLayout()
        rt = QLabel(tr("RESULT"))
        rt.setObjectName("PanelTitle")
        rh.addWidget(rt)
        rh.addStretch(1)
        self.sim_badge = QLabel("SIMULATION")
        self.sim_badge.setObjectName("SimBadge")
        rh.addWidget(self.sim_badge)
        rl.addLayout(rh)
        self.title = QLabel()
        self.title.setObjectName("ResultTitle")
        self.title.setWordWrap(True)
        self.sentence = muted()
        rl.addWidget(self.title)
        rl.addWidget(self.sentence)
        self.bands_box = QVBoxLayout()
        self.bands_box.setSpacing(6)
        rl.addLayout(self.bands_box)
        self.hints = notice("")
        rl.addWidget(self.hints)
        self.notes = faint()
        rl.addWidget(self.notes)
        rl.addStretch(1)
        self.scope = faint(tr("This test shows how much of the transmitter power the antenna accepts in each band. "
                              "How much of that is radiated (gain in dBi) needs a reference antenna: ")
                           + f"<a style='color:{theme.CYAN}' href='gain'>" + tr("run the gain test") + "</a>.")
        self.scope.setTextFormat(Qt.TextFormat.RichText)
        self.scope.linkActivated.connect(lambda _: self.open_gain.emit())
        rl.addWidget(self.scope)
        btns = QHBoxLayout()
        self.btn_save = QPushButton(tr("Save"))
        self.btn_report = QPushButton(tr("PDF report"))
        self.btn_details = QPushButton(tr("Details"))
        self.btn_save.clicked.connect(self.save_requested)
        self.btn_report.clicked.connect(self.report_requested)
        self.btn_details.clicked.connect(self.open_details)
        for b in (self.btn_save, self.btn_report, self.btn_details):
            btns.addWidget(b)
        btns.addStretch(1)
        rl.addLayout(btns)
        split.addWidget(self.result)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([860, 400])
        root.addWidget(split, 1)

        state.connection_changed.connect(self._refresh_connection)
        state.busy_changed.connect(self._refresh_buttons)
        state.assessment_changed.connect(self._refresh_result)
        state.measurement_changed.connect(self._refresh_result)
        setup_page.job_started.connect(self._watch_job)
        setup_page.sweep.preset.currentIndexChanged.connect(self._sync_from_setup)
        state.presets_changed.connect(self._rebuild_tiles)
        self._sync_from_setup()
        self._refresh_connection()
        self._refresh_result()

    # ------------------------------------------------------------------ tiles
    def _tile_specs(self) -> list[tuple[str, str, str]]:
        out = []
        for pid in self.state.tile_ids():
            p = self.state.presets[pid]
            title, sub = TILE_TEXT.get(pid, (p.title, p.subtitle))
            out.append((pid, title, sub))
        return out

    def _choose_tiles(self) -> None:
        cat = self.state.catalog
        current = self.state.tile_ids()

        def row(p):
            segs = [sg for sg in p.segments if sg.start_hz and sg.stop_hz]
            rng = (f"{min(sg.start_hz for sg in segs) / 1e6:.0f}–{max(sg.stop_hz for sg in segs) / 1e6:.0f} MHz"
                   if segs else tr("full supported range"))
            bands = ", ".join(short_label(b) for pid in p.targets if (b := cat.get(pid))) or tr("survey")
            return (p.id, [TILE_TEXT.get(p.id, (p.title, ""))[0], rng, bands], p.description)

        def ordered(items):
            picked = sorted((p for p in items if p.id in current), key=lambda p: current.index(p.id))
            return picked + [p for p in items if p.id not in current]

        allp = list(self.state.presets.values())
        groups = [
            (tr("Sweep presets"), [row(p) for p in ordered([p for p in allp if not p.user_defined and not p.band_id])]),
            (tr("Single bands"), [row(p) for p in ordered([p for p in allp if p.band_id and not p.user_defined])]),
        ]
        own = ordered([p for p in allp if p.user_defined])
        if own:
            groups.append((tr("Your presets and bands"), [row(p) for p in own]))
        d = PickerDialog(self, tr("Choose tiles"),
                         tr("Tick up to %d entries – sweep presets, single bands or your own. Existing tiles keep "
                            "their order; new ones are added after them.") % MAX_TILES,
                         [tr("Preset / band"), tr("Sweep range"), tr("Target bands")], groups, current, MAX_TILES,
                         column_widths=(230, 130))
        if d.exec() and d.selected_ids():
            picked = d.selected_ids()
            self.state.set_tile_ids([i for i in current if i in picked] + [i for i in picked if i not in current])

    def _build_tiles(self) -> None:
        for i, (pid, title, sub) in enumerate(self._tile_specs()):
            b = QToolButton()
            b.setObjectName("UseCase")
            b.setCheckable(True)
            b.setText(f"{title}\n{sub}" if sub else title)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            b.setMinimumHeight(54)
            self.group.addButton(b)
            self.case_buttons[pid] = b
            b.clicked.connect(lambda _=False, p=pid: self._select_case(p))
            self.tile_grid.addWidget(b, i // TILES_PER_ROW, i % TILES_PER_ROW)

    def _rebuild_tiles(self) -> None:
        for b in self.case_buttons.values():
            self.group.removeButton(b)
            self.tile_grid.removeWidget(b)
            b.setParent(None)
            b.deleteLater()
        self.case_buttons.clear()
        self._build_tiles()
        self._sync_from_setup()

    # ------------------------------------------------------------------ actions
    def _select_case(self, pid: str):
        self.case_buttons[pid].setChecked(True)
        self.use_case = pid
        i = self.setup.sweep.preset.findData(pid)
        if i >= 0 and self.setup.sweep.preset.currentIndex() != i:
            self.setup.sweep.preset.setCurrentIndex(i)

    def _sync_from_setup(self, *_):
        """The Setup page owns the preset; a preset without a tile (custom, Sub-GHz combo) clears the selection."""
        pid = self.setup.sweep.preset.currentData()
        self.use_case = pid
        if pid in self.case_buttons:
            self.case_buttons[pid].setChecked(True)
        else:
            self.group.setExclusive(False)
            for b in self.case_buttons.values():
                b.setChecked(False)
            self.group.setExclusive(True)
        if self.state.measurement is None:
            self._preview_bands()

    def use_case_name(self) -> str:
        for pid, title, _ in self._tile_specs():
            if pid == self.use_case:
                return title
        p = self.state.presets.get(self.use_case or "")
        return p.title if p else tr("the selected bands")

    def start(self):
        if not self.passive.isChecked():
            self.status.setText(tr("Please confirm that only the antenna is connected."))
            self.status.setStyleSheet(f"color:{theme.AMBER};")
            return
        self.status.setStyleSheet("")
        self.setup.analyze()

    def _watch_job(self, job):
        self.progress.setValue(0)
        job.progress.connect(lambda f, m: (self.progress.setValue(int(f * 1000)), self.status.setText(m)))
        job.finished.connect(lambda _: (self.progress.setValue(1000), self.status.setText(tr("Done"))))
        job.failed.connect(lambda e, tb: (self.progress.setValue(0),
                                          self.status.setText(str(e) if isinstance(e, InstrumentError) else
                                                              f"{type(e).__name__}: {e}")))

    # ------------------------------------------------------------------ refresh
    def _refresh_buttons(self, *_):
        busy, conn = self.state.busy, self.state.connected
        self.btn_start.setEnabled(conn and not busy)
        self.btn_cancel.setVisible(busy)
        for b in self.case_buttons.values():
            b.setEnabled(not busy)
        has = self.state.measurement is not None and not busy
        for b in (self.btn_save, self.btn_report, self.btn_details):
            b.setEnabled(has)

    def _refresh_connection(self):
        st, info = self.state, self.state.instrument
        if st.connected and info:
            if st.simulated_backend:
                set_badge(self.conn_pill, tr("Demo mode (simulated antenna)"), theme.SIM)
                self.conn_hint.setText(tr("Change the simulated antenna or connection details in Setup."))
            elif info.serial:
                set_badge(self.conn_pill, f"{info.model or tr('Instrument')} " + tr("connected") + f" · {info.serial}",
                          theme.GOOD)
                host = f"{info.host_software} {info.gui_version} · " if info.host_software else ""
                self.conn_hint.setText(host + tr("firmware") + f" {info.firmware or '–'} · "
                                       + tr("hardware") + f" {info.hardware_revision or '–'}")
            else:
                set_badge(self.conn_pill, f"{info.host_software or info.model} " + tr("running – no device"), theme.WARN)
                self.conn_hint.setText(tr("Attach the device, then press Reconnect in the toolbar."))
        else:
            set_badge(self.conn_pill, tr("Not connected"), theme.TEXT_DIM)
            self.conn_hint.setText(tr(st.live_adapter().connect_hint) + " " + tr("Or")
                                   + f" <a style='color:{theme.CYAN}' href='demo'>" + tr("try the demo") + "</a>.")
        cal = st.gui_cal_type
        if st.connected and not st.simulated_backend:
            if cal and cal.lower() != "none":
                where = f" {tr('in')} {info.host_software}" if info and info.host_software else ""
                self.cal_hint.setText(tr("Calibration active") + where + f" ({cal}).")
            else:
                self.cal_hint.setText(
                    tr("Not calibrated – values are approximate.") +
                    f" <a style='color:{theme.CYAN}' href='setup'>" + tr("How to calibrate") + "</a>")
        else:
            self.cal_hint.setText("")
        self._refresh_buttons()

    def _region_row(self, reg: dict) -> QWidget:
        c = reg["conclusion"]
        icon, col = CONCLUSION_ICON[c]
        r = reg["worst"]
        w = QWidget()
        w.setToolTip("\n".join(text for _, _, text in reg["members"]))
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 2, 0, 4)
        lay.setSpacing(4)
        head = QHBoxLayout()
        name = QLabel(f"<span style='color:{col}; font-weight:700'>{icon}</span>&nbsp; <b>{reg['label']}</b>"
                      f"&nbsp;&nbsp;<span style='color:{col}'>{SHORT_CONCLUSION[c]}</span>")
        head.addWidget(name, 1)
        if r.worst_s11_db is not None and c != Conclusion.INSUFFICIENT:
            head.addWidget(faint(tr("worst") + f" {r.worst_s11_db:.1f} dB"))
        lay.addLayout(head)
        if r.worst_s11_db is not None and c != Conclusion.INSUFFICIENT:
            loss = mismatch_ceil_db(r.worst_s11_db)
            loss_text = f"{loss:.1f} dB" if loss != float("inf") else "∞"
            power = QLabel(f"<span style='color:{col}; font-weight:600'>{accepted_floor_percent(r.worst_s11_db)} %</span>"
                           f"<span style='color:{theme.TEXT_DIM}'> " + tr("of the power accepted at the worst point")
                           + " · " + tr("mismatch loss") + f" ≤ {loss_text}</span>")
            lay.addWidget(power)
        bar = QProgressBar()
        bar.setObjectName("MatchBar")
        bar.setRange(0, 100)
        bar.setValue(int(round(100 * (r.matched_fraction or 0))))
        bar.setTextVisible(False)
        bar.setStyleSheet(f"QProgressBar#MatchBar::chunk {{ background: {col}; border-radius: 2px; }}")
        lay.addWidget(bar)
        names = ", ".join(short_label(p) for p, _, _ in reg["members"])
        sub = faint(names)
        sub.setWordWrap(True)
        lay.addWidget(sub)
        return w

    def _preview_bands(self):
        """Before a test: show where the notch should land for the selected use case."""
        preset = self.state.presets.get(self.setup.sweep.preset.currentData())
        cat = self.state.catalog
        bands, spans = [], []
        if preset:
            ids = preset.targets or preset.survey
            for pid in ids:
                p = cat.get(pid)
                if p:
                    bands.append((p.span[0], p.span[1], region_label(*p.span)))
            spans = [(t.start_hz, t.stop_hz) for t in preset.segments if t.start_hz and t.stop_hz]
        merged: list[list] = []
        for a, b, _ in sorted(bands):
            if merged and a <= merged[-1][1] + 0.02 * a:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        shown = [(a, b, region_label(a, b), theme.ORANGE) for a, b in merged]
        x = (min(s for s, _ in spans), max(e for _, e in spans)) if spans else None
        self.plot.set_data([], shown, self.state.thresholds.pass_s11_db, band_labels=True, good_zone=True,
                           show_legend=False, x_range=x)

    def _clear_bands(self):
        while self.bands_box.count():
            it = self.bands_box.takeAt(0)
            w = it.widget() if it is not None else None
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _refresh_result(self, *_):
        m, a = self.state.measurement, self.state.assessment
        self._clear_bands()
        self.sim_badge.setVisible(bool(m and m.simulated))
        if not m or not a:
            self._preview_bands()
            self.title.setText(tr("No test yet"))
            self.title.setStyleSheet(f"color:{theme.TEXT_DIM};")
            self.sentence.setText(tr("Connect, choose what the antenna is for and press Start test. The result "
                                     "appears here in plain words."))
            self.hints.setVisible(False)
            self.notes.setVisible(False)
            self._refresh_buttons()
            return
        cat = self.state.catalog
        e = explain(m, a, cat, self.use_case_name())
        self.title.setText(e["title"])
        self.title.setStyleSheet(f"color:{RATING_COLOR[a.rating]};")
        self.sentence.setText(e["sentence"])
        regions = merge_regions(e["bands"])
        for reg in regions[:4]:
            self.bands_box.addWidget(self._region_row(reg))
        if len(regions) > 4:
            self.bands_box.addWidget(faint(tr("More bands on the Bands page.")))
        self.hints.setText("<br>".join(e["hints"]))
        self.hints.setVisible(bool(e["hints"]))
        self.notes.setText("<br>".join(e["notes"]))
        self.notes.setVisible(bool(e["notes"]))
        bands, dips = [], []
        for reg in regions:
            bands.append((reg["start"], reg["stop"], reg["label"], CONCLUSION_ICON[reg["conclusion"]][1]))
            res = band_resonance(m, reg["profile"])
            if not res or any(abs(res.freq_hz - d.freq_hz) < 1e6 for d in dips):
                continue
            outside = not res.inside_band and reg["conclusion"] != Conclusion.WELL_MATCHED
            if res.edge:
                side = "above" if res.edge == "upper" else "below"
                dips.append(Dip(res.freq_hz, res.s11_db, theme.ORANGE, res.band_center_hz,
                                tr("resonance") + f" {side}\n" + tr("measured range")))
            elif res.s11_db <= -6:
                dips.append(Dip(res.freq_hz, res.s11_db, theme.ORANGE if outside else theme.TEXT,
                                res.band_center_hz if outside else None))
        self.plot.set_data([TraceSet(m, theme.TRACE_COLORS[0], primary=True)], bands,
                           self.state.thresholds.pass_s11_db, band_labels=True, good_zone=True, dips=dips[:4],
                           show_legend=False)
        self._refresh_buttons()

