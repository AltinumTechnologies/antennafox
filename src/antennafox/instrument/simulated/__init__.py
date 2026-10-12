# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""In-process simulator: a demo backend driven by synthetic antenna models, never a real device."""

from .backend import CAPABILITIES, SimulatedBackend
from .models import MODELS, AntennaModel, SeriesRLC

__all__ = ["CAPABILITIES", "SimulatedBackend", "MODELS", "AntennaModel", "SeriesRLC"]
