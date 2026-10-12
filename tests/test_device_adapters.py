# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Device-independent adapter contract: capabilities, conversion into the shared model, registry, UI adaptation."""

import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from antennafox.instrument import librevna, simulated
from antennafox.instrument.adapters import LIVE_ADAPTERS, SIMULATION_ID, adapter_by_id
from antennafox.instrument.base import (
    AcquisitionParams,
    CalibrationOwner,
    CancelToken,
    DeviceCapabilities,
    OutOfRange,
    VNABackend,
    check_in_limits,
)
from antennafox.instrument.librevna import LibreVNABackend
from antennafox.instrument.librevna.emulator import LibreVNAGUIEmulator
from antennafox.instrument.simulated import SimulatedBackend
from antennafox.measurement.model import (
    AntennaInfo,
    CalibrationMeta,
    InstrumentInfo,
    InstrumentLimits,
    Measurement,
    TraceSegment,
)
from antennafox.measurement.orchestrator import new_measurement, run_plan
from antennafox.measurement.presets import custom_plan
from antennafox.measurement.session import load_session, measurement_from_touchstone, save_session
from antennafox.rf.rfmath import s11_db, vswr

PROTOCOL_METHODS = ("connect", "disconnect", "is_connected", "info", "refresh_device", "ping", "capabilities",
                    "calibration_type", "device_status", "acquire")
S11_ONLY = DeviceCapabilities(ports=1, parameters=("S11",), calibration=CalibrationOwner.SIMULATED)


class S11OnlyBackend:
    """Minimal stand-in for a one-port instrument; exercises the contract without any transport."""

    simulated = True
    warnings: list[str] = []

    def __init__(self):
        self.limits = InstrumentLimits(1e6, 1e9, 1001, source="profile:test")
        self._open = False

    def connect(self) -> InstrumentInfo:
        self._open = True
        return self.info()

    def disconnect(self) -> None:
        self._open = False

    def is_connected(self) -> bool:
        return self._open

    def info(self) -> InstrumentInfo:
        return InstrumentInfo(backend="test-s11", idn="Test,OnePort,1,1", model="OnePort", limits=self.limits,
                              simulated=True)

    def refresh_device(self) -> InstrumentInfo:
        return self.info()

    def ping(self) -> float:
        return 0.0

    def capabilities(self) -> DeviceCapabilities:
        return S11_ONLY

    def calibration_type(self) -> str | None:
        return None

    def device_status(self) -> dict[str, str]:
        return {}

    def acquire(self, params, cancel, progress=None) -> list[TraceSegment]:
        check_in_limits(params, self.limits, S11_ONLY)
        f = np.linspace(params.start_hz, params.stop_hz, params.points)
        return [TraceSegment("S11", f, np.full(params.points, 0.1 + 0j), "2026-01-01T00:00:00+00:00", "test-s11")]


def _run(backend: VNABackend, params: AcquisitionParams) -> list[TraceSegment]:
    backend.connect()
    try:
        return backend.acquire(params, CancelToken())
    finally:
        backend.disconnect()


# --- capabilities ------------------------------------------------------------------------------------
def test_declared_capabilities():
    assert librevna.CAPABILITIES.ports == 2
    assert set(librevna.CAPABILITIES.parameters) == {"S11", "S12", "S21", "S22"}
    assert librevna.CAPABILITIES.calibration is CalibrationOwner.HOST_SOFTWARE
    assert simulated.CAPABILITIES.calibration is CalibrationOwner.SIMULATED
    assert "S21" in simulated.CAPABILITIES.parameters
    assert LibreVNABackend(port=1).capabilities() is librevna.CAPABILITIES
    assert SimulatedBackend().capabilities() is simulated.CAPABILITIES


def test_unsupported_parameter_rejected_before_anything_is_sent():
    lim = InstrumentLimits(1e6, 1e9, 1001)
    check_in_limits(AcquisitionParams(1e8, 2e8, 11), lim, S11_ONLY)
    with pytest.raises(OutOfRange, match="S21"):
        check_in_limits(AcquisitionParams(1e8, 2e8, 11, parameters=("S11", "S21")), lim, S11_ONLY)
    with pytest.raises(OutOfRange, match="S22"):
        _run(SimulatedBackend(), AcquisitionParams(1e9, 2e9, 11, parameters=("S22",)))
    with pytest.raises(OutOfRange):
        _run(S11OnlyBackend(), AcquisitionParams(1e8, 2e8, 11, parameters=("S21",)))


def test_adapters_implement_the_protocol():
    for cls in (LibreVNABackend, SimulatedBackend, S11OnlyBackend):
        for name in PROTOCOL_METHODS:
            assert callable(getattr(cls, name)), (cls.__name__, name)
        assert isinstance(cls.simulated, bool)


# --- conversion of device data into the shared model ---------------------------------------------------
@pytest.fixture
def srv():
    s = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    yield s
    s.stop()


def test_librevna_data_arrives_as_shared_model(srv):
    b = LibreVNABackend(port=srv.port, timeout=1.0, poll_interval=0.02)
    info = b.connect()
    try:
        assert (info.backend, info.model, info.host_software) == (librevna.ADAPTER_ID, "LibreVNA", "LibreVNA-GUI")
        assert info.gui_version == "1.6.5" and info.limits is not None
        segs = b.acquire(AcquisitionParams(1e9, 2e9, 51, parameters=librevna.CAPABILITIES.parameters), CancelToken())
    finally:
        b.disconnect()
    assert [s.parameter for s in segs] == list(librevna.CAPABILITIES.parameters)
    for s in segs:
        assert s.freq_hz.dtype == np.float64 and s.data.dtype == np.complex128
        assert s.z0 == 50.0
        assert s.source == "simulation"  # emulator serial is flagged; a real device yields the adapter id
        assert {"cal_type", "serial", "gui_version", "firmware", "ifbw_hz", "points"} <= set(s.provenance)
        assert s.acquired_at.endswith("+00:00")


def test_real_serial_yields_adapter_id_as_source(srv):
    srv.serial = "12345678"
    segs = _run(LibreVNABackend(port=srv.port, timeout=1.0, poll_interval=0.02), AcquisitionParams(1e9, 2e9, 11))
    assert segs[0].source == librevna.ADAPTER_ID
    assert not segs[0].provenance.get("deembedding_active")


def test_measurement_from_any_adapter_feeds_the_same_pipeline(tmp_path):
    for backend in (SimulatedBackend(), S11OnlyBackend()):
        backend.connect()
        plan = custom_plan(2.0e8, 4.0e8, 21, backend.info().limits)
        traces, extra = run_plan(backend, plan, CancelToken())
        m = new_measurement("x", AntennaInfo(name="x"), backend, CalibrationMeta(), plan, traces, extra)
        backend.disconnect()
        f, g = m.merged("S11")
        assert len(f) == 21 and np.all(np.isfinite(s11_db(g))) and np.all(vswr(g) >= 1)
        assert m.instrument.backend == backend.info().backend
        p = save_session(tmp_path / f"{m.instrument.backend}.afox", m)
        m2, _, _ = load_session(p)
        assert m2.instrument.model == m.instrument.model
        assert m2.traces[0].z0 == 50.0 and np.array_equal(m2.traces[0].data, m.traces[0].data)


# --- shared model --------------------------------------------------------------------------------------
def test_trace_segment_defaults_and_immutability():
    t = TraceSegment("S11", [1e9, 2e9], [0.1j, 0.2], "2026-01-01T00:00:00+00:00", "test")
    assert t.z0 == 50.0 and t.freq_hz.dtype == np.float64 and t.data.dtype == np.complex128
    with pytest.raises(ValueError):
        t.data[0] = 0
    with pytest.raises(ValueError):
        TraceSegment("S11", [1e9, 2e9], [0.1j], "2026-01-01T00:00:00+00:00", "test")


def test_calibration_status_unknown_is_explicit():
    unknown = CalibrationMeta()
    assert unknown.gui_reported_type is None
    assert any("no active calibration" in i for i in unknown.issues(None, None))
    assert "LibreVNA" not in " ".join(unknown.issues(None, None))
    confirmed = CalibrationMeta(gui_reported_type="SOLT_1", user_confirmed=True, cal_date="2026-01-01T00:00:00+00:00",
                                cal_start_hz=1e9, cal_stop_hz=3e9, reference_plane="cable_end")
    assert confirmed.status(1.5e9, 2.5e9).value == "valid_metadata"
    assert confirmed.status(0.5e9, 2.5e9).value == "user_confirmed"


def test_instrument_info_defaults_are_device_neutral():
    i = InstrumentInfo()
    assert (i.backend, i.model, i.host_software, i.gui_version, i.limits) == ("none", "", "", "", None)
    m = Measurement("m", AntennaInfo(), i, CalibrationMeta(), None)
    assert not m.simulated and m.parameters() == [] and m.span() is None


def test_touchstone_import_is_not_an_instrument():
    m = measurement_from_touchstone(Path(__file__).parent / "data" / "librevna_v1_cable_into_port2_uncal.s1p")
    assert m.instrument.backend == "import" and m.instrument.limits is None and not m.instrument.model
    assert m.sweep_plan is None and m.calibration.gui_reported_type is None
    assert all(t.source == "touchstone-import" and t.z0 == 50.0 for t in m.traces)


def test_session_without_z0_or_model_still_loads(tmp_path):
    b = SimulatedBackend()
    b.connect()
    plan = custom_plan(1e9, 2e9, 11, b.limits)
    traces, extra = run_plan(b, plan, CancelToken())
    m = new_measurement("old", AntennaInfo(), b, CalibrationMeta(), plan, traces, extra)
    p = save_session(tmp_path / "old.afox", m)
    with zipfile.ZipFile(p) as z:
        doc = json.loads(z.read("measurement.json"))
        files = {n: z.read(n) for n in z.namelist() if n != "measurement.json"}
    for e in doc["traces"]:
        e.pop("z0")
    for k in ("model", "host_software"):
        doc["measurement"]["instrument"].pop(k)
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("measurement.json", json.dumps(doc))
        for n, blob in files.items():
            z.writestr(n, blob)
    m2, _, _ = load_session(p)
    assert m2.traces[0].z0 == 50.0 and m2.instrument.model == ""


# --- registry and UI adaptation ------------------------------------------------------------------------
def test_registry_offers_exactly_the_librevna_adapter():
    assert [a.id for a in LIVE_ADAPTERS] == [librevna.ADAPTER_ID]
    spec = adapter_by_id(librevna.ADAPTER_ID)
    assert adapter_by_id("scpi") is spec and adapter_by_id(None) is spec and adapter_by_id("nonsense") is spec
    b = spec.factory("10.0.0.5", 4242)
    assert isinstance(b, LibreVNABackend) and (b.transport.host, b.transport.port) == ("10.0.0.5", 4242)
    assert spec.default_port == librevna.DEFAULT_PORT


def test_instrument_panel_lists_one_live_source_and_keeps_legacy_setting(qtbot, tmp_path):
    from antennafox.ui.setup.instrument_panel import InstrumentPanel

    s = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    s.setValue("conn/mode", "scpi")
    s.setValue("conn/port", 20000)
    panel = InstrumentPanel()
    qtbot.addWidget(panel)
    assert [panel.mode.itemData(i) for i in range(panel.mode.count())] == [librevna.ADAPTER_ID, SIMULATION_ID]
    panel.load(s)
    assert panel.mode.currentData() == librevna.ADAPTER_ID and panel.port.value() == 20000
    assert isinstance(panel.make_backend(), LibreVNABackend)
    assert "LibreVNA-GUI" in panel.help.text()
    panel.set_simulation(True)
    assert panel.simulation and isinstance(panel.make_backend(), SimulatedBackend)
    assert panel.adapter.id == librevna.ADAPTER_ID
    panel.save(s)
    assert s.value("conn/mode") == SIMULATION_ID
    panel.set_simulation(False)
    assert panel.mode.currentData() == librevna.ADAPTER_ID


def test_gain_page_refuses_one_port_instruments(qtbot, tmp_path):
    from antennafox.ui.gain.page import GainPage
    from antennafox.ui.state import AppState

    state = AppState(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    page = GainPage(state)
    qtbot.addWidget(page)
    b = S11OnlyBackend()
    state.set_backend(b, b.connect())
    page.capture("ref")
    assert "S21" in page.status.text() and not state.busy and not state.gain_study.ref_captures
    assert state.live_adapter().id == librevna.ADAPTER_ID


def test_setup_views_name_the_connected_instrument(qtbot, tmp_path):
    from antennafox.ui.setup.calibration_panel import CalibrationPanel
    from antennafox.ui.state import AppState

    state = AppState(QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat))
    panel = CalibrationPanel()
    qtbot.addWidget(panel)
    panel.refresh(state)
    assert "LibreVNA" not in panel.gui_label.text()
    b = S11OnlyBackend()
    state.set_backend(b, b.connect())
    state.gui_cal_type = None
    panel.refresh(state)
    assert panel.gui_label.text().startswith("Instrument")
