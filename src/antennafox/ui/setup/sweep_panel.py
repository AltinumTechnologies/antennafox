# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sweep configuration: preset or custom range, acquisition parameters, target bands, start/cancel, progress."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
)

from ...instrument.base import InstrumentError
from ...measurement.model import InstrumentLimits, SweepPlan
from ...measurement.presets import Preset, PresetError, custom_plan, make_user_preset, plan_for_preset
from ..state import AppState
from ..widgets import BandPickerDialog, Card, muted, tr, wrap_check

IFBW_CHOICES = [100, 300, 1000, 3000, 10000, 50000]


class SweepPanel(Card):
    analyze_requested = Signal()
    cancel_requested = Signal()

    def __init__(self, state: AppState, parent=None):
        super().__init__(tr("Analyze antenna"), parent)
        self.state = state
        self._selected_targets: list[str] | None = None
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.preset = QComboBox()
        self._fill_preset_combo()
        self.preset.currentIndexChanged.connect(self._on_preset_changed)
        grid.addWidget(QLabel(tr("Preset")), 0, 0)
        grid.addWidget(self.preset, 0, 1, 1, 3)
        self.c_start, self.c_stop = QDoubleSpinBox(), QDoubleSpinBox()
        for sb, v in ((self.c_start, 2300.0), (self.c_stop, 2600.0)):
            sb.setRange(0.1, 100000)
            sb.setDecimals(3)
            sb.setSuffix(" MHz")
            sb.setValue(v)
        self.c_points = QSpinBox()
        self.c_points.setRange(11, 100001)
        self.c_points.setValue(601)
        grid.addWidget(QLabel(tr("Start / stop")), 1, 0)
        grid.addWidget(self.c_start, 1, 1)
        grid.addWidget(self.c_stop, 1, 2)
        grid.addWidget(self.c_points, 1, 3)
        self.ifbw = QComboBox()
        for v in IFBW_CHOICES:
            self.ifbw.addItem(f"{v:g} Hz", v)
        self.ifbw.setCurrentIndex(IFBW_CHOICES.index(1000))
        self.avg = QSpinBox()
        self.avg.setRange(1, 99)
        self.avg.setPrefix(tr("avg "))
        self.power = QDoubleSpinBox()
        self.power.setRange(-40, 0)
        self.power.setValue(-20)
        self.power.setSuffix(" dBm")
        self.repeats = QSpinBox()
        self.repeats.setRange(1, 10)
        self.repeats.setPrefix(tr("repeats "))
        self.repeats.setToolTip(tr("Repeated sweeps (single-segment presets) for repeatability statistics."))
        grid.addWidget(QLabel(tr("IFBW / avg")), 2, 0)
        grid.addWidget(self.ifbw, 2, 1)
        grid.addWidget(self.avg, 2, 2)
        grid.addWidget(self.repeats, 2, 3)
        grid.addWidget(QLabel(tr("Port power")), 3, 0)
        grid.addWidget(self.power, 3, 1)
        self.btn_bands = QPushButton(tr("Target bands…"))
        self.btn_bands.clicked.connect(self._pick_bands)
        grid.addWidget(self.btn_bands, 3, 2, 1, 2)
        self.btn_save_preset = QPushButton(tr("Save as preset…"))
        self.btn_save_preset.setToolTip(tr("Store the current range and target bands as a reusable preset."))
        self.btn_save_preset.clicked.connect(self._save_preset_dialog)
        self.btn_delete_preset = QPushButton(tr("Delete preset"))
        self.btn_delete_preset.clicked.connect(self._delete_preset)
        grid.addWidget(self.btn_save_preset, 4, 1)
        grid.addWidget(self.btn_delete_preset, 4, 2)
        self.lay.addLayout(grid)
        state.presets_changed.connect(self._populate_presets)
        self.targets_label = muted()
        self.lay.addWidget(self.targets_label)
        self.passive_ok, row = wrap_check(tr("The DUT is a passive antenna, disconnected from any radio, transmitter "
                                             "or DC/bias-tee supply"))
        self.lay.addWidget(row)
        warn = muted(tr("Never connect transmitters or powered devices to the VNA ports. AntennaFox does not drive DC "
                        "into antennas. Use low port power for measurements on sensitive devices."))
        warn.setObjectName("Caution")
        self.lay.addWidget(warn)
        buttons = QHBoxLayout()
        self.btn_analyze = QPushButton(tr("Analyze antenna"))
        self.btn_analyze.setObjectName("Primary")
        self.btn_analyze.clicked.connect(self.analyze_requested)
        self.btn_cancel = QPushButton(tr("Cancel"))
        self.btn_cancel.setObjectName("Danger")
        self.btn_cancel.clicked.connect(self.cancel_requested)
        buttons.addWidget(self.btn_analyze)
        buttons.addWidget(self.btn_cancel)
        buttons.addStretch(1)
        self.lay.addLayout(buttons)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress_text = muted()
        self.lay.addWidget(self.progress)
        self.lay.addWidget(self.progress_text)
        self.omissions = muted()
        self.omissions.setObjectName("Caution")
        self.lay.addWidget(self.omissions)

    # -- settings -------------------------------------------------------------------------
    def load(self, s: QSettings) -> None:
        self.preset.setCurrentIndex(max(self.preset.findData(s.value("test/preset", "dualband")), 0))
        self._selected_targets = self.state.load_json("test/targets")
        self._on_preset_changed()

    def save(self, s: QSettings) -> None:
        s.setValue("test/preset", self.preset.currentData())
        self.state.save_json("test/targets", self._selected_targets)

    # -- presets --------------------------------------------------------------------------
    def _fill_preset_combo(self) -> None:
        current = self.preset.currentData()
        self.preset.blockSignals(True)
        self.preset.clear()
        presets = list(self.state.presets.values())
        for group in ([p for p in presets if not p.user_defined and p.band_id is None],
                      [p for p in presets if p.band_id is not None],
                      [p for p in presets if p.user_defined and p.band_id is None]):
            for p in group:
                label = tr(p.title) if not p.user_defined and p.band_id is None else p.title
                self.preset.addItem((tr("Band") + ": " + label) if p.band_id else label, p.id)
            if group:
                self.preset.insertSeparator(self.preset.count())
        self.preset.addItem(tr("User-defined sweep"), "custom")
        idx = self.preset.findData(current)
        self.preset.setCurrentIndex(idx if idx >= 0 else 0)
        self.preset.blockSignals(False)

    def _populate_presets(self) -> None:
        self._fill_preset_combo()
        self._on_preset_changed()

    def current_preset(self) -> Preset | None:
        return self.state.presets.get(self.preset.currentData())

    def create_preset(self, title: str, start_hz: float, stop_hz: float, points: int, targets: list[str],
                      subtitle: str = "", tile: bool = True) -> Preset:
        """Validate, store and select a user preset; raises PresetError on bad input."""
        preset = make_user_preset(title, start_hz, stop_hz, points, targets, subtitle, tile)
        if any(not p.user_defined and p.title.lower() == preset.title.lower() for p in self.state.presets.values()):
            raise PresetError("This title clashes with a built-in preset.")
        for pid in targets:
            band = self.state.catalog.get(pid)
            if band is None:
                raise PresetError(f"Unknown band '{pid}'.")
            lo, hi = band.span
            if lo < start_hz or hi > stop_hz:
                raise PresetError(f"Target band '{band.label()}' ({lo / 1e6:.3f}–{hi / 1e6:.3f} MHz) lies outside "
                                  f"the sweep range – widen the range or pick another band.")
        self.state.add_user_preset(preset)
        self.preset.setCurrentIndex(self.preset.findData(preset.id))
        return preset

    def _save_preset_dialog(self) -> None:
        targets, _ = self.current_targets()
        custom = self.preset.currentData() == "custom"
        if custom:
            start, stop, points = self.c_start.value() * 1e6, self.c_stop.value() * 1e6, self.c_points.value()
        else:
            preset = self.current_preset()
            segs = preset.segments if preset is not None else ()
            lo = min((s.start_hz for s in segs if s.start_hz), default=self.c_start.value() * 1e6)
            hi = max((s.stop_hz for s in segs if s.stop_hz), default=self.c_stop.value() * 1e6)
            start, stop, points = lo, hi, max((s.points or 0 for s in segs), default=self.c_points.value())
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Save as preset"))
        form = QFormLayout(dlg)
        title = QLineEdit()
        subtitle = QLineEdit()
        subtitle.setPlaceholderText(tr("second line on the Test-page tile (optional)"))
        f_start, f_stop = QDoubleSpinBox(), QDoubleSpinBox()
        for sb, v in ((f_start, start / 1e6), (f_stop, stop / 1e6)):
            sb.setRange(0.001, 100000)
            sb.setDecimals(3)
            sb.setSuffix(" MHz")
            sb.setValue(v)
        f_points = QSpinBox()
        f_points.setRange(11, 100001)
        f_points.setValue(points)
        tile = QCheckBox(tr("Show as a tile on the Test page"))
        tile.setChecked(True)
        form.addRow(tr("Title"), title)
        form.addRow(tr("Subtitle"), subtitle)
        form.addRow(tr("Start"), f_start)
        form.addRow(tr("Stop"), f_stop)
        form.addRow(tr("Points"), f_points)
        form.addRow(tr("Target bands"), QLabel(", ".join(targets) if targets else tr("none – choose target bands first")))
        form.addRow(tile)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        form.addRow(bb)
        if not dlg.exec():
            return
        try:
            self.create_preset(title.text(), f_start.value() * 1e6, f_stop.value() * 1e6, f_points.value(), targets,
                               subtitle.text(), tile.isChecked())
        except PresetError as e:
            QMessageBox.warning(self, tr("Cannot save preset"), str(e))

    def _delete_preset(self) -> None:
        p = self.current_preset()
        if p is None or not p.user_defined:
            QMessageBox.information(self, tr("Delete preset"), tr("Only user-defined presets can be deleted."))
            return
        if QMessageBox.question(self, tr("Delete preset"), tr("Delete preset") + f" '{p.title}'?") == \
                QMessageBox.StandardButton.Yes:
            self.state.remove_user_preset(p.id)

    # -- targets --------------------------------------------------------------------------
    def _on_preset_changed(self) -> None:
        custom = self.preset.currentData() == "custom"
        for w in (self.c_start, self.c_stop, self.c_points):
            w.setEnabled(custom)
        p = self.current_preset()
        self.btn_delete_preset.setEnabled(p is not None and p.user_defined)
        self._selected_targets = None if not custom else (self._selected_targets or [])
        self.update_targets()

    def current_targets(self) -> tuple[list[str], list[str]]:
        if self._selected_targets is not None:
            return list(self._selected_targets), []
        p = self.current_preset()
        if p is None:
            return [], []
        return list(p.targets), list(p.survey)

    def update_targets(self) -> None:
        targets, survey = self.current_targets()
        cat = self.state.catalog
        if targets:
            names = ", ".join(cat.by_id(i).label() for i in targets if cat.get(i))
            self.targets_label.setText(tr("Target bands") + f": {names}")
        elif survey:
            self.targets_label.setText(tr("Survey mode: all common bands are shown, no target bands selected "
                                          "(no overall verdict)."))
        else:
            self.targets_label.setText(tr("No target bands selected."))
        self.state.set_targets(targets, survey)

    def _pick_bands(self) -> None:
        targets, _ = self.current_targets()
        d = BandPickerDialog(self.state.catalog, targets, self)
        if d.exec():
            self._selected_targets = d.selected_ids()
            self.update_targets()

    # -- plan -----------------------------------------------------------------------------
    def build_plan(self, limits: InstrumentLimits) -> SweepPlan:
        kw = dict(ifbw_hz=float(self.ifbw.currentData()), averages=self.avg.value(), power_dbm=self.power.value())
        p = self.current_preset()
        if p is None:
            if self.c_stop.value() <= self.c_start.value():
                raise InstrumentError(tr("Stop frequency must be greater than start frequency."))
            return custom_plan(self.c_start.value() * 1e6, self.c_stop.value() * 1e6, self.c_points.value(),
                               limits, **kw)
        return plan_for_preset(p, limits, **kw)

    def refresh(self, state: AppState) -> None:
        busy, conn = state.busy, state.connected
        self.btn_analyze.setEnabled(not busy and conn)
        self.btn_cancel.setEnabled(busy)
        if state.instrument and state.instrument.limits:
            lim = state.instrument.limits
            self.power.setRange(lim.min_power_dbm, lim.max_power_dbm)

    def show_progress(self, fraction: float, text: str) -> None:
        self.progress.setValue(int(fraction * 1000))
        self.progress_text.setText(text)
