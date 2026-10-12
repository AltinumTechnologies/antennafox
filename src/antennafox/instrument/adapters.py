# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Registry of live hardware adapters offered in the UI. A plain tuple: adding a device means adding one entry
here plus its backend module; the simulator is deliberately not listed because it is a demo mode, not a device."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from . import librevna
from .base import VNABackend


@dataclass(frozen=True)
class AdapterSpec:
    id: str  # equals InstrumentInfo.backend and TraceSegment.source of the data it produces
    title: str  # source selector entry
    device_name: str  # short name for status texts ("Connect <device_name>")
    default_host: str
    default_port: int
    setup_help: str  # shown next to the connection settings
    connect_hint: str  # one sentence shown while not connected
    factory: Callable[[str, int], VNABackend]


LIVE_ADAPTERS: tuple[AdapterSpec, ...] = (
    AdapterSpec(
        id=librevna.ADAPTER_ID,
        title="LibreVNA-GUI (SCPI/TCP)",
        device_name=librevna.DEVICE_NAME,
        default_host=librevna.DEFAULT_HOST,
        default_port=librevna.DEFAULT_PORT,
        setup_help=("Start LibreVNA-GUI and enable the SCPI server (Window → Preferences → General → SCPI Server, "
                    "default port 19542). AntennaFox talks to the GUI only and never opens the USB device directly. "
                    "Keep the default localhost address unless you trust the network."),
        connect_hint="Start LibreVNA-GUI with the SCPI server enabled (default port 19542), then connect.",
        factory=lambda host, port: librevna.LibreVNABackend(host, port),
    ),
)

SIMULATION_ID = "sim"  # source-selector value for the in-process simulator (not an adapter)
# Settings written before adapter ids existed stored this value for the LibreVNA adapter.
LEGACY_MODE_IDS = {"scpi": librevna.ADAPTER_ID}


def adapter_by_id(adapter_id: str | None) -> AdapterSpec:
    """Resolve a stored selection; unknown or legacy ids fall back to the first (and currently only) adapter."""
    wanted = LEGACY_MODE_IDS.get(adapter_id or "", adapter_id)
    return next((a for a in LIVE_ADAPTERS if a.id == wanted), LIVE_ADAPTERS[0])
