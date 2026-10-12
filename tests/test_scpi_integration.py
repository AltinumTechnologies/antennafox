# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""LibreVNA SCPI adapter against the emulated LibreVNA-GUI server (no hardware)."""

import threading
import time

import numpy as np
import pytest

from antennafox.instrument.base import (
    AcquisitionParams,
    Cancelled,
    CancelToken,
    CommandRejected,
    ConnectionFailed,
    DeviceNotConnected,
    InstrumentTimeout,
    OutOfRange,
    ProtocolError,
)
from antennafox.instrument.librevna import LibreVNABackend, parse_trace_data
from antennafox.instrument.librevna.emulator import LibreVNAGUIEmulator

P = AcquisitionParams(2.3e9, 2.6e9, 201)


@pytest.fixture
def srv():
    s = LibreVNAGUIEmulator(sweep_seconds=0.1).start()
    yield s
    s.stop()


@pytest.fixture
def vna(srv):
    b = LibreVNABackend(port=srv.port, timeout=1.0, poll_interval=0.02)
    b.connect()
    yield b
    b.disconnect()


def test_connect_identity(vna):
    info = vna.info()
    assert info.gui_version == "1.6.5"
    assert info.limits.source == "device"
    assert info.limits.max_freq_hz == 6e9
    # real hardware reports 0 Hz; the documented 100 kHz minimum is used instead
    assert info.limits.min_freq_hz == 100e3
    assert (info.limits.min_power_dbm, info.limits.max_power_dbm) == (-40, 0)
    assert vna.warnings == []
    assert info.simulated  # emulator serial is flagged as simulation
    assert vna.calibration_type() == "SOLT_1"


def test_acquire_ok(vna):
    segs = vna.acquire(P, CancelToken())
    s = segs[0]
    assert len(s.freq_hz) == 201
    assert s.freq_hz[0] == 2.3e9 and s.freq_hz[-1] == 2.6e9
    assert s.source == "simulation"
    assert s.provenance["cal_type"] == "SOLT_1"


def test_acquire_two_params(vna):
    segs = vna.acquire(AcquisitionParams(2.3e9, 2.6e9, 101, parameters=("S11", "S21")), CancelToken())
    assert [s.parameter for s in segs] == ["S11", "S21"]


def test_rejected_command_then_resync(srv, vna):
    srv.faults.reject = {"VNA:ACQ:IFBW"}
    with pytest.raises(CommandRejected):
        vna.acquire(P, CancelToken())
    srv.faults.reject = set()
    assert len(vna.acquire(P, CancelToken())[0].data) == 201


def test_rejected_query_does_not_desync(vna):
    assert vna.transport.try_query(":NOT:A:COMMAND?") is None
    assert vna.transport.query(":DEV:CONN?") == "EMULATOR-0001"


def test_timeout_and_recovery_with_late_reply(srv, vna):
    srv.faults.hang_on = {"VNA:ACQ:FIN?"}
    vna.transport.timeout = 0.3
    with pytest.raises(InstrumentTimeout):
        vna.transport.query(":VNA:ACQ:FIN?")
    srv.faults.hang_on = set()
    # the late FIN?/sentinel replies were discarded by the resync handshake
    assert vna.transport.query(":DEV:CONN?") == "EMULATOR-0001"
    assert vna.transport.query(":VNA:FREQ:START?").isdigit()


def test_unresponsive_gui_closes_connection(srv, vna):
    srv.faults.hang_on = {"VNA:ACQ:FIN?"}
    srv.faults.hang_seconds = 10.0
    vna.transport.timeout = 0.2
    with pytest.raises(ConnectionFailed, match="not responding"):
        vna.transport.query(":VNA:ACQ:FIN?")
    assert not vna.is_connected()


def test_sweep_never_finishes(srv, vna, monkeypatch):
    srv.faults.never_finish = True
    monkeypatch.setattr("antennafox.instrument.librevna.backend.estimate_sweep_seconds", lambda p: -3.6)
    with pytest.raises(InstrumentTimeout):
        vna.acquire(P, CancelToken())


def test_disconnect_mid_sweep(srv, vna):
    srv.faults.drop_after_trigger = True
    with pytest.raises(ConnectionFailed):
        vna.acquire(P, CancelToken())
    assert not vna.is_connected()


def test_wrong_identity(srv):
    srv.faults.wrong_idn = True
    b = LibreVNABackend(port=srv.port, timeout=0.5)
    with pytest.raises(ProtocolError):
        b.connect()
    assert not b.is_connected()


def test_no_server():
    b = LibreVNABackend(port=1, timeout=0.5)
    with pytest.raises(ConnectionFailed, match="SCPI server"):
        b.connect()


def test_no_device(srv):
    srv.faults.device_connected = False
    b = LibreVNABackend(port=srv.port, timeout=1.0)
    info = b.connect()
    assert info.limits is None  # 1e11 "limit" without device is not trusted
    with pytest.raises(DeviceNotConnected):
        b.acquire(P, CancelToken())


def test_truncated_trace(srv, vna):
    srv.faults.truncate_trace = True
    with pytest.raises(ProtocolError):
        vna.acquire(P, CancelToken())


def test_stale_out_of_order_data(srv, vna):
    vna.acquire(AcquisitionParams(1e9, 2e9, 201), CancelToken())
    srv.faults.stale_grid_once = True
    with pytest.raises(ProtocolError, match="grid"):
        vna.acquire(P, CancelToken())


def test_silent_clamp_detected(srv, vna):
    srv.faults.clamp_stop_hz = 2.5e9
    with pytest.raises(CommandRejected, match="stop frequency"):
        vna.acquire(P, CancelToken())


def test_out_of_range_never_sent(srv, vna):
    n = len(srv.commands_seen)
    with pytest.raises(OutOfRange):
        vna.acquire(AcquisitionParams(5e9, 7e9, 101), CancelToken())
    assert not any("FREQ" in c and "7000000000" in c for c in srv.commands_seen[n:])


def test_cancel(srv, vna):
    srv.sweep_seconds = 5.0
    tok = CancelToken()
    threading.Timer(0.3, tok.cancel).start()
    t0 = time.monotonic()
    with pytest.raises(Cancelled):
        vna.acquire(P, tok)
    assert time.monotonic() - t0 < 2.0
    assert srv.st["running"] is False  # acquisition stopped on cancel


def test_second_client_kicks_first(srv, vna):
    other = LibreVNABackend(port=srv.port, timeout=0.5)
    other.connect()
    with pytest.raises(ConnectionFailed):
        vna.transport.query(":DEV:CONN?")
    other.disconnect()


def test_parse_trace_data():
    f, d = parse_trace_data("[1e+6,0.4,0.03],[2000000,-0.09,-0.01]")
    assert f.tolist() == [1e6, 2e6]
    assert d[1] == pytest.approx(-0.09 - 0.01j)
    assert len(parse_trace_data("EMPTY")[0]) == 0
    for bad in ("[1,2],[3", "[1,2,x]", "[1,2]"):
        with pytest.raises(ProtocolError):
            parse_trace_data(bad)


def test_untested_version_warning():
    s = LibreVNAGUIEmulator(version="1.7.0").start()
    try:
        b = LibreVNABackend(port=s.port)
        b.connect()
        assert any("not been tested" in w for w in b.warnings)
        assert len(b.acquire(P, CancelToken())[0].freq_hz) == 201
        np.testing.assert_allclose(b.acquire(P, CancelToken())[0].freq_hz, np.linspace(2.3e9, 2.6e9, 201))
    finally:
        s.stop()


def test_device_attached_but_not_connected_is_connected_automatically(srv):
    # LibreVNA-GUI can run with a device listed in :DEV:LIST? but not connected
    srv.faults.device_connected = False
    srv.faults.device_available = True
    b = LibreVNABackend(port=srv.port, timeout=1.0)
    info = b.connect()
    assert info.serial == "EMULATOR-0001" and info.limits is not None
    assert any(c.startswith(":DEV:CONN EMULATOR") for c in srv.commands_seen)


def test_device_status_reports_temperatures_and_flags():
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    try:
        b = LibreVNABackend(port=srv.port, timeout=1.0, poll_interval=0.02)
        b.connect()
        st = b.device_status()
        assert st["Temperatures"] == "Source 30 °C · LO 33 °C · MCU 42 °C"
        assert st["Status flags"] == "all clear" and st["Reference clock"] == "Internal"
        b.disconnect()
    finally:
        srv.stop()

