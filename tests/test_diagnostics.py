# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Logging of SCPI traffic and the diagnostics bundle."""

import logging

import pytest

from antennafox import diagnostics
from antennafox.instrument.base import AcquisitionParams, CancelToken, CommandRejected, InstrumentTimeout
from antennafox.instrument.librevna import LibreVNABackend
from antennafox.instrument.librevna.emulator import LibreVNAGUIEmulator


@pytest.fixture
def srv():
    s = LibreVNAGUIEmulator(sweep_seconds=0.05).start()
    yield s
    s.stop()


def test_scpi_traffic_is_logged(srv, caplog):
    caplog.set_level(logging.DEBUG, logger="antennafox")
    b = LibreVNABackend(port=srv.port, timeout=1.0, poll_interval=0.02)
    b.connect()
    b.acquire(AcquisitionParams(2.3e9, 2.6e9, 1001), CancelToken())
    b.disconnect()
    msgs = [r.getMessage() for r in caplog.records]
    assert any(m == ">> :VNA:ACQ:SINGLE TRUE" for m in msgs)
    assert any(m.startswith("<< LibreVNA,LibreVNA-GUI,EMULATOR-0001") for m in msgs)
    assert any(m.startswith("acquire S11: 2300000000") for m in msgs)
    assert any(m.startswith("sweep complete in") for m in msgs)
    # a 1001-point trace is logged truncated, not as a 30 kB line
    trace_lines = [m for m in msgs if m.startswith("<< [")]
    assert trace_lines and all(len(m) < 220 and "chars)" in m for m in trace_lines)
    assert any(m == "connection closed" for m in msgs)


def test_protocol_problems_are_logged_as_warnings(srv, caplog):
    caplog.set_level(logging.DEBUG, logger="antennafox")
    b = LibreVNABackend(port=srv.port, timeout=0.3, poll_interval=0.02)
    b.connect()
    srv.faults.reject = {"VNA:ACQ:IFBW"}
    with pytest.raises(CommandRejected):
        b.acquire(AcquisitionParams(2.3e9, 2.6e9, 101), CancelToken())
    srv.faults.reject = set()
    srv.faults.hang_on = {"VNA:ACQ:FIN?"}
    with pytest.raises(InstrumentTimeout):
        b.transport.query(":VNA:ACQ:FIN?")
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("command rejected (CME): :VNA:ACQ:IFBW" in m for m in warnings)
    assert any("timeout waiting for a response" in m for m in warnings)
    assert any("resynchronizing" in r.getMessage() for r in caplog.records)


def test_bundle_contains_environment_and_recent_log(tmp_path):
    diagnostics.setup(tmp_path / "logs", level=logging.DEBUG)
    logging.getLogger("antennafox.scpi").debug(">> *IDN?")
    out = diagnostics.write_bundle(tmp_path / "diag.txt", {"Instrument": "emulator"})
    text = out.read_text()
    assert "AntennaFox diagnostics" in text and "Platform:" in text and "Qt:" in text
    assert "Instrument: emulator" in text
    assert ">> *IDN?" in text
    assert (tmp_path / "logs" / diagnostics.LOG_FILE).exists()
