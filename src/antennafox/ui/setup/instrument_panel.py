# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Instrument connection panel: source selection (live adapters from the registry plus the simulator), host/port,
connection state."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSpinBox

from ...instrument.adapters import LIVE_ADAPTERS, SIMULATION_ID, AdapterSpec, adapter_by_id
from ...instrument.base import VNABackend
from ...instrument.simulated import SimulatedBackend
from ...instrument.simulated.models import MODELS
from .. import theme
from ..plots import fmt_hz
from ..state import AppState
from ..widgets import Card, faint, muted, set_badge, tr


class InstrumentPanel(Card):
    connect_requested = Signal()
    disconnect_requested = Signal()
    test_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(tr("Instrument"), parent)
        form = QFormLayout()
        self.mode = QComboBox()
        for spec in LIVE_ADAPTERS:
            self.mode.addItem(tr(spec.title), spec.id)
        self.mode.addItem(tr("Simulation (offline demo)"), SIMULATION_ID)
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow(tr("Source"), self.mode)
        self.sim_model = QComboBox()
        for key, model in MODELS.items():
            self.sim_model.addItem(model.title, key)
        form.addRow(tr("Simulated antenna"), self.sim_model)
        self._sim_model_label = form.labelForField(self.sim_model)
        hp = QHBoxLayout()
        self.host = QLineEdit(LIVE_ADAPTERS[0].default_host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(LIVE_ADAPTERS[0].default_port)
        hp.addWidget(self.host, 1)
        hp.addWidget(self.port)
        form.addRow(tr("Host / port"), hp)
        self.lay.addLayout(form)
        row = QHBoxLayout()
        self.btn_connect = QPushButton(tr("Connect"))
        self.btn_disconnect = QPushButton(tr("Disconnect"))
        self.btn_test = QPushButton(tr("Test connection"))
        self.btn_connect.clicked.connect(self.connect_requested)
        self.btn_disconnect.clicked.connect(self.disconnect_requested)
        self.btn_test.clicked.connect(self.test_requested)
        for b in (self.btn_connect, self.btn_disconnect, self.btn_test):
            row.addWidget(b)
        self.lay.addLayout(row)
        self.badge = QLabel()
        self.header.addWidget(self.badge)
        self.info = muted()
        self.lay.addWidget(self.info)
        self.help = faint()
        self.lay.addWidget(self.help)
        self._live_id = LIVE_ADAPTERS[0].id
        self._on_mode_changed()

    @property
    def simulation(self) -> bool:
        return self.mode.currentData() == SIMULATION_ID

    @property
    def adapter(self) -> AdapterSpec:
        """The live adapter in use (or last selected while the simulator is active)."""
        return adapter_by_id(self._live_id)

    def set_simulation(self, on: bool) -> None:
        self.mode.setCurrentIndex(self.mode.findData(SIMULATION_ID if on else self._live_id))

    def _on_mode_changed(self) -> None:
        sim = self.simulation
        if not sim:
            self._live_id = str(self.mode.currentData())
        self.help.setText(tr(self.adapter.setup_help))
        self.sim_model.setVisible(sim)
        if self._sim_model_label:
            self._sim_model_label.setVisible(sim)
        for w in (self.host, self.port, self.help):
            w.setVisible(not sim)

    def make_backend(self) -> VNABackend:
        if self.simulation:
            return SimulatedBackend(self.sim_model.currentData(), realtime=0.6)
        spec = self.adapter
        return spec.factory(self.host.text().strip() or spec.default_host, self.port.value())

    def load(self, s: QSettings) -> None:
        mode = str(s.value("conn/mode", LIVE_ADAPTERS[0].id))
        if mode != SIMULATION_ID:
            self._live_id = adapter_by_id(mode).id
            mode = self._live_id
        spec = self.adapter
        self.host.setText(str(s.value("conn/host", spec.default_host)))
        self.port.setValue(int(str(s.value("conn/port", spec.default_port))))
        self.mode.setCurrentIndex(max(self.mode.findData(mode), 0))
        self.sim_model.setCurrentIndex(max(self.sim_model.findData(s.value("conn/sim_model", "dualband_24_5")), 0))

    def save(self, s: QSettings) -> None:
        s.setValue("conn/host", self.host.text())
        s.setValue("conn/port", self.port.value())
        s.setValue("conn/mode", self.mode.currentData())
        s.setValue("conn/sim_model", self.sim_model.currentData())

    def refresh(self, state: AppState) -> None:
        info = state.instrument
        if state.connected and info:
            if state.simulated_backend:
                set_badge(self.badge, tr("Connected – SIMULATION"), theme.SIM)
            elif info.serial:
                set_badge(self.badge, tr("Connected"), theme.GOOD)
            else:
                set_badge(self.badge, tr("Connected – no device"), theme.WARN)
            lines = [info.idn or info.backend]
            if info.serial:
                lines.append(tr("Serial") + f": {info.serial} · FW {info.firmware or '–'} · "
                             f"HW {info.hardware_revision or '–'}")
            if info.limits:
                lim = info.limits
                lines.append(tr("Range") + f": {fmt_hz(lim.min_freq_hz)} – {fmt_hz(lim.max_freq_hz)}, "
                             f"{lim.max_points} pts ({lim.source})")
            lines += [f"⚠ {w}" for w in state.instrument_warnings]
            self.info.setText("\n".join(lines))
        else:
            set_badge(self.badge, tr("Not connected"), theme.TEXT_FAINT)
            self.info.setText("")
        busy, conn = state.busy, state.connected
        self.btn_connect.setEnabled(not busy)
        self.btn_disconnect.setEnabled(not busy and conn)
        self.btn_test.setEnabled(not busy)
        self.mode.setEnabled(not busy)
