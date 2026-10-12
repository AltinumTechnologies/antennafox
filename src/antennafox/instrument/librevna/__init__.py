# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""LibreVNA adapter: talks to LibreVNA-GUI's SCPI server; the first implementation of the VNABackend contract."""

from .backend import (
    ADAPTER_ID,
    CAPABILITIES,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEVICE_NAME,
    EMULATOR_SERIAL_PREFIX,
    HOST_SOFTWARE,
    TESTED_GUI_VERSIONS,
    LibreVNABackend,
    parse_idn,
    parse_trace_data,
)

__all__ = ["ADAPTER_ID", "CAPABILITIES", "DEFAULT_HOST", "DEFAULT_PORT", "DEVICE_NAME", "EMULATOR_SERIAL_PREFIX", "HOST_SOFTWARE", "LibreVNABackend", "TESTED_GUI_VERSIONS", "parse_idn", "parse_trace_data"]
