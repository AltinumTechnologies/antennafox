# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Adapter boundary under invalid or incomplete device responses, plus the device-independent guards."""

import pytest

from antennafox.instrument.base import (
    AcquisitionParams,
    Cancelled,
    CancelToken,
    CommandRejected,
    ConnectionFailed,
    DeviceNotConnected,
    InstrumentLimits,
    InstrumentTimeout,
    OutOfRange,
    ProtocolError,
    check_in_limits,
    estimate_sweep_seconds,
)
from antennafox.instrument.librevna import LibreVNABackend, parse_idn
from antennafox.instrument.librevna.emulator import LibreVNAGUIEmulator
from antennafox.instrument.librevna.scpi_transport import SCPITransport
from antennafox.instrument.simulated import SimulatedBackend

P = AcquisitionParams(1e9, 2e9, 2)


class ScriptedGUI(LibreVNAGUIEmulator):
    """Emulator that answers selected queries with a fixed string, to feed the adapter bad device responses."""

    def __init__(self, overrides: dict[str, str], **kw):
        super().__init__(**kw)
        self.overrides = overrides

    def _one(self, cmd: str):
        head = cmd.lstrip(":").split(" ")[0].upper()
        return self.overrides.get(head, None) if head in self.overrides else super()._one(cmd)


@pytest.fixture
def scripted():
    servers = []

    def start(overrides: dict[str, str], **kw):
        kw.setdefault("sweep_seconds", 0.05)
        s = ScriptedGUI(overrides, **kw).start()
        servers.append(s)
        return s

    yield start
    for s in servers:
        s.stop()


def connect(srv):
    b = LibreVNABackend(port=srv.port, timeout=1.0, poll_interval=0.02)
    b.connect()
    return b


# --- invalid device responses ------------------------------------------------------------------------------
@pytest.mark.parametrize("overrides", [
    {"DEV:INF:LIM:MAXF?": "abc"},
    {"DEV:INF:LIM:MINF?": "7e9"},  # minimum above maximum
    {"DEV:INF:LIM:MAXP?": "nan"},
])
def test_implausible_limits_fall_back_to_the_profile_with_a_warning(scripted, overrides):
    b = connect(scripted(overrides))
    try:
        assert b.info().limits is not None
        assert b.info().limits.source.startswith("profile:")
        assert b.info().limits.max_freq_hz == 6e9
        assert any("could not be read plausibly" in w for w in b.warnings)
    finally:
        b.disconnect()


@pytest.mark.parametrize("overrides,match", [
    ({"VNA:ACQ:IFBW?": "abc"}, "Invalid readback"),
    ({"VNA:TRAC:PARAM?": "S21"}, "measures S21"),
    ({"VNA:TRAC:PAUSED?": "TRUE"}, "paused"),
    ({"VNA:ACQ:FIN?": "MAYBE"}, "Unexpected response"),
    ({"VNA:TRAC:DATA?": "[1000000000,nan,0],[2000000000,0.1,0]"}, "non-finite"),
    ({"VNA:TRAC:DATA?": "[1000000000,0.1,0]"}, "instead of 2 points"),
    ({"VNA:TRAC:DATA?": "[1000000000,0.1,0],[2500000000,0.1,0]"}, "grid"),
])
def test_bad_responses_abort_the_sweep_with_a_protocol_error(scripted, overrides, match):
    b = connect(scripted(overrides))
    try:
        with pytest.raises(ProtocolError, match=match):
            b.acquire(P, CancelToken())
        assert b.is_connected()  # a protocol error is not a lost connection
    finally:
        b.disconnect()


@pytest.mark.parametrize("esr,exc,match", [
    ("abc", ProtocolError, "ESR"),
    ("999", ProtocolError, "out of range"),
    ("16", CommandRejected, "Execution error"),
    ("8", CommandRejected, "Device error"),
])
def test_status_register_answers_are_interpreted(scripted, esr, exc, match):
    b = connect(scripted({"*ESR?": esr}))
    try:
        with pytest.raises(exc, match=match):
            b.acquire(P, CancelToken())
    finally:
        b.disconnect()


def test_sweep_frequency_readback_is_optional(scripted):
    b = connect(scripted({"VNA:ACQ:FREQ?": "abc"}, sweep_seconds=0.1))
    try:
        seen = []
        b.acquire(AcquisitionParams(1e9, 2e9, 51), CancelToken(), progress=lambda fr, msg: seen.append(msg))
        assert "Sweeping …" in seen and not any("MHz" in m for m in seen)
    finally:
        b.disconnect()


def test_identification_timeout_recovers_by_resync():
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    try:
        b = connect(srv)
        srv.faults.hang_on = {"*IDN?"}
        srv.faults.hang_seconds = 0.6
        b.transport.timeout = 0.2
        with pytest.raises(InstrumentTimeout):
            b.ping()
        srv.faults.hang_on = set()
        assert b.is_connected() and b.transport.query(":DEV:CONN?") == "EMULATOR-0001"  # late reply discarded
        b.disconnect()
    finally:
        srv.stop()


def test_connect_maps_socket_errors(monkeypatch):
    def refuse(*a, **k):
        raise OSError("no route to host")

    monkeypatch.setattr("antennafox.instrument.librevna.scpi_transport.socket.create_connection", refuse)
    with pytest.raises(ConnectionFailed, match="no route to host"):
        SCPITransport(port=1).open()


def test_multi_line_answer_is_rejected_without_losing_sync():
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    try:
        b = connect(srv)
        srv.faults.garbage_line_once = True
        with pytest.raises(ProtocolError, match="multi-line"):
            b.transport.query(":DEV:CONN?")
        assert b.transport.query(":DEV:CONN?") == "EMULATOR-0001"
        b.disconnect()
    finally:
        srv.stop()


def test_missing_required_commands_refuse_the_connection(monkeypatch):
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    try:
        def partial(self):
            self.supported_commands = {"*IDN?", "VNA:ACQUISITION:SINGLE"}

        monkeypatch.setattr(LibreVNABackend, "_load_command_list", partial)
        b = LibreVNABackend(port=srv.port, timeout=1.0)
        with pytest.raises(CommandRejected, match="required commands"):
            b.connect()
        assert not b.is_connected()
    finally:
        srv.stop()


def test_rejected_optional_query_does_not_fail_the_next_command():
    # the adapter probes optional queries with try_query(); a rejection sets CME, which must not be blamed on
    # the first real command of the following sweep
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    try:
        b = connect(srv)
        assert b.transport.try_query(":DEV:INF:NOPE?") is None
        assert len(b.acquire(P, CancelToken())[0].freq_hz) == 2
        srv.faults.device_connected = False
        b.device_status()  # several unsupported queries in a row
        srv.faults.device_connected = True
        assert len(b.acquire(P, CancelToken())[0].freq_hz) == 2
        srv.faults.reject = {"VNA:SWEEP"}
        with pytest.raises(CommandRejected, match="VNA:SWEEP"):  # genuine rejections are still attributed
            b.acquire(P, CancelToken())
        b.disconnect()
    finally:
        srv.stop()


def test_rejected_command_list_falls_back_to_optimistic_support():
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    try:
        srv.faults.reject = {"*LST?"}
        b = connect(srv)
        assert b.supported_commands == set() and b.supports("ANY:THING")
        assert len(b.acquire(P, CancelToken())[0].freq_hz) == 2
        b.disconnect()
    finally:
        srv.stop()


def test_progress_ping_and_status_without_device():
    srv = LibreVNAGUIEmulator(sweep_seconds=0.1).start()
    try:
        b = connect(srv)
        seen = []
        b.acquire(AcquisitionParams(1e9, 2e9, 51), CancelToken(), progress=lambda fr, msg: seen.append((fr, msg)))
        assert seen[0] == (0.0, "Configuring sweep …") and seen[-1] == (1.0, "Sweep complete")
        assert any(m.startswith("Sweeping") for _, m in seen)
        assert all(a <= c for (a, _), (c, _) in zip(seen, seen[1:], strict=False))
        assert 0 <= b.ping() < 1.0
        srv.faults.device_connected = False
        st = b.device_status()
        assert "Temperatures" not in st and "Status flags" not in st
        info = b.refresh_device()
        assert info.serial == "" and info.limits is None
        with pytest.raises(DeviceNotConnected, match="no LibreVNA device"):
            b.acquire(P, CancelToken())
        b.disconnect()
        with pytest.raises(DeviceNotConnected):
            b.acquire(P, CancelToken())
    finally:
        srv.stop()


def test_gui_closing_the_socket_is_a_connection_failure():
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    try:
        b = connect(srv)
        srv.drop_client()
        with pytest.raises(ConnectionFailed):
            for _ in range(3):  # the first send may still succeed into the dead socket's buffer
                b.transport.query(":DEV:CONN?")
        assert not b.is_connected()
    finally:
        srv.stop()


def test_transport_guards():
    t = SCPITransport(port=1)
    with pytest.raises(ConnectionFailed, match="Not connected"):
        t.query(":DEV:CONN?")
    t.resync()  # nothing to do without a socket
    assert not t.is_open
    srv = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    try:
        b = connect(srv)
        with pytest.raises(ValueError):
            b.transport._send("a\nb")
        assert b.transport.esr() == 0
        b.disconnect()
        assert not b.is_connected()
        b.disconnect()  # idempotent
    finally:
        srv.stop()


def test_parse_idn():
    assert parse_idn("LibreVNA,LibreVNA-GUI,206D33B6,1.6.5") == {"serial": "206D33B6", "gui_version": "1.6.5"}
    for bad in ("LibreVNA,LibreVNA-GUI,1.6.5", "Keysight,E5071C,1234,1.0", ""):
        with pytest.raises(ProtocolError):
            parse_idn(bad)


# --- device-independent guards -----------------------------------------------------------------------------
LIM = InstrumentLimits(100e3, 6e9, 4501, min_ifbw_hz=10, max_ifbw_hz=50e3, min_power_dbm=-40, max_power_dbm=0)


@pytest.mark.parametrize("params,match", [
    (AcquisitionParams(50e3, 1e9, 11), "outside the instrument limits"),
    (AcquisitionParams(1e9, 7e9, 11), "outside the instrument limits"),
    (AcquisitionParams(2e9, 2e9, 11), "Stop frequency"),
    (AcquisitionParams(1e9, 2e9, 1), "Point count"),
    (AcquisitionParams(1e9, 2e9, 4502), "Point count"),
    (AcquisitionParams(1e9, 2e9, 11, ifbw_hz=5), "IF bandwidth"),
    (AcquisitionParams(1e9, 2e9, 11, ifbw_hz=60e3), "IF bandwidth"),
    (AcquisitionParams(1e9, 2e9, 11, power_dbm=1), "Output power"),
    (AcquisitionParams(1e9, 2e9, 11, power_dbm=-41), "Output power"),
    (AcquisitionParams(1e9, 2e9, 11, averages=0), "Averages"),
])
def test_check_in_limits_rejects_every_out_of_range_setting(params, match):
    with pytest.raises(OutOfRange, match=match):
        check_in_limits(params, LIM)


def test_check_in_limits_accepts_the_edges():
    check_in_limits(AcquisitionParams(100e3, 6e9, 4501, ifbw_hz=10, power_dbm=-40), LIM)
    check_in_limits(AcquisitionParams(100e3, 6e9, 2, ifbw_hz=50e3, power_dbm=0), LIM)
    assert estimate_sweep_seconds(AcquisitionParams(1e9, 2e9, 1000, ifbw_hz=1000, averages=2)) == pytest.approx(6.6)


def test_cancel_token():
    tok = CancelToken()
    tok.check()
    tok.wait(0.01)
    tok.cancel()
    with pytest.raises(Cancelled):
        tok.check()
    with pytest.raises(Cancelled):
        tok.wait(10.0)


def test_simulated_backend_guards_and_two_port_data():
    b = SimulatedBackend("load_50", realtime=0.01)
    with pytest.raises(DeviceNotConnected):
        b.acquire(P, CancelToken())
    b.connect()
    seen = []
    segs = b.acquire(AcquisitionParams(1e9, 2e9, 11, parameters=("S11", "S21")), CancelToken(),
                     progress=lambda fr, m: seen.append(fr))
    assert seen[-1] == 1.0 and [s.parameter for s in segs] == ["S11", "S21"]
    assert abs(segs[0].data).max() < 0.05  # a 50 Ω load reflects almost nothing
    assert abs(segs[1].data).mean() == pytest.approx(10 ** (-40 / 20), rel=0.1)
    assert b.device_status()["Active calibration"] == "SOLT_1 (simulated)"
    assert b.refresh_device().simulated and b.ping() == 0.0
    tok = CancelToken()
    tok.cancel()
    with pytest.raises(Cancelled):
        b.acquire(P, tok)
