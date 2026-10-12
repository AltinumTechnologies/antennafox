# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Application state shared by all views."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QStandardPaths, Signal

from ..catalog.catalog import Catalog, CatalogError, load_catalog
from ..gain.study import GainStudy
from ..instrument.adapters import SIMULATION_ID, AdapterSpec, adapter_by_id
from ..instrument.base import VNABackend
from ..measurement.model import AntennaInfo, CalibrationMeta, InstrumentInfo, Measurement
from ..measurement.presets import (
    PRESETS,
    Preset,
    PresetError,
    band_preset,
    load_user_presets,
    save_user_presets,
)
from ..rf.assessment import Assessment, assess
from ..rf.bands import DensityPolicy, Thresholds

log = logging.getLogger("antennafox.ui")

MAX_TILES = 8
DEFAULT_TILES = ["wifi24", "dualband", "wifi5", "lora868", "subghz_eu", "ism433", "gnss_l1", "full"]


def data_dir() -> Path:
    p = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation) or Path.home() / ".antennafox")
    p.mkdir(parents=True, exist_ok=True)
    return p


class AppState(QObject):
    connection_changed = Signal()
    measurement_changed = Signal()
    assessment_changed = Signal()
    comparison_changed = Signal()
    busy_changed = Signal(bool)
    catalog_changed = Signal()
    presets_changed = Signal()
    settings_changed = Signal()

    def __init__(self, settings: QSettings | None = None):
        super().__init__()
        self.settings = settings or QSettings("AltinumTechnologies", "AntennaFox")
        self.backend: VNABackend | None = None
        self.instrument: InstrumentInfo | None = None
        self.instrument_warnings: list[str] = []
        self.gui_cal_type: str | None = None
        self.measurement: Measurement | None = None
        self.assessment: Assessment | None = None
        self.session_path: Path | None = None
        self.comparisons: list[Measurement] = []
        self.gain_study = GainStudy()
        self.target_ids: list[str] = []
        self.survey_ids: list[str] = []
        self.thresholds = Thresholds()
        self.density = DensityPolicy()
        self.catalog_error = ""
        self.catalog = self._load_catalog()
        self.presets: dict[str, Preset] = {}
        self._load_presets()
        self._busy = False
        self.restore()

    # -- catalog ----------------------------------------------------------------------------
    @property
    def user_catalog_path(self) -> Path:
        return data_dir() / "user_catalog.json"

    def _load_catalog(self) -> Catalog:
        try:
            return load_catalog(self.user_catalog_path)
        except (CatalogError, ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            log.warning("user catalog ignored: %s", e)
            self.catalog_error = f"User catalog ignored: {e}"
            return load_catalog(None)

    @property
    def operator(self) -> str:
        return str(self.settings.value("report/operator", ""))

    @operator.setter
    def operator(self, name: str) -> None:
        self.settings.setValue("report/operator", name.strip())

    # -- presets ----------------------------------------------------------------------------
    @property
    def user_presets_path(self) -> Path:
        return data_dir() / "user_presets.json"

    def _load_presets(self) -> None:
        self.presets = dict(PRESETS)
        for b in self.catalog.profiles:
            bp = band_preset(b)
            self.presets[bp.id] = bp
        try:
            user = load_user_presets(self.user_presets_path)
        except (PresetError, ValueError, TypeError, KeyError, AttributeError, OSError) as e:
            log.warning("user presets ignored: %s", e)
            user = []
        for p in user:
            if p.id in self.presets:
                log.warning("user preset %r collides with a built-in preset and is ignored", p.id)
                continue
            self.presets[p.id] = p

    def user_presets(self) -> list[Preset]:
        return [p for p in self.presets.values() if p.user_defined and p.band_id is None]

    def add_user_preset(self, preset: Preset) -> None:
        others = [p for p in self.user_presets() if p.id != preset.id]
        save_user_presets(self.user_presets_path, others + [preset])
        self._load_presets()
        if preset.tile:
            self.set_tile_ids(self._with_tile(self.tile_ids(), preset.id))
        else:
            self.presets_changed.emit()

    def remove_user_preset(self, preset_id: str) -> None:
        save_user_presets(self.user_presets_path, [p for p in self.user_presets() if p.id != preset_id])
        self._load_presets()
        self.set_tile_ids([i for i in self.tile_ids() if i != preset_id])

    # -- Test-page tiles --------------------------------------------------------------------
    def tile_ids(self) -> list[str]:
        raw = self.settings.value("test/tiles")
        if isinstance(raw, str):  # QSettings returns a one-element list as a plain string
            ids = [raw]
        elif isinstance(raw, list):
            ids = [str(x) for x in raw]
        else:
            ids = list(DEFAULT_TILES)
            for p in self.user_presets():
                if p.tile:
                    ids = self._with_tile(ids, p.id)
        return [i for i in ids if i in self.presets][:MAX_TILES]

    def set_tile_ids(self, ids: list[str]) -> None:
        self.settings.setValue("test/tiles", [i for i in ids if i in self.presets][:MAX_TILES])
        self.presets_changed.emit()

    @staticmethod
    def _with_tile(ids: list[str], pid: str) -> list[str]:
        """Insert before the full scan; when all slots are taken, the last built-in tile makes room."""
        if pid in ids:
            return ids
        ids = list(ids)
        if len(ids) >= MAX_TILES:
            victim = next((i for i in reversed(ids) if i != "full" and not i.startswith("user_")), ids[-1])
            ids.remove(victim)
        ids.insert(ids.index("full") if "full" in ids else len(ids), pid)
        return ids

    def reload_catalog(self) -> None:
        self.catalog = self._load_catalog()
        self._load_presets()
        self.reassess()
        self.catalog_changed.emit()
        self.presets_changed.emit()

    # -- busy -------------------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._busy

    def set_busy(self, b: bool) -> None:
        self._busy = b
        self.busy_changed.emit(b)

    # -- connection -------------------------------------------------------------------------
    @property
    def connected(self) -> bool:
        return self.backend is not None and self.backend.is_connected()

    @property
    def simulated_backend(self) -> bool:
        return bool(self.backend and (self.backend.simulated or (self.instrument and self.instrument.simulated)))

    def set_backend(self, backend: VNABackend | None, info: InstrumentInfo | None, warnings: list[str] | None = None):
        self.backend = backend
        self.instrument = info
        self.instrument_warnings = warnings or []
        self.connection_changed.emit()

    def live_adapter(self) -> AdapterSpec:
        """The hardware adapter selected in Setup (persisted as conn/mode); the simulator does not count."""
        mode = self.settings.value("conn/mode")
        return adapter_by_id(None if mode in (None, SIMULATION_ID) else str(mode))

    # -- measurement ------------------------------------------------------------------------
    def set_measurement(self, m: Measurement | None, session_path: Path | None = None,
                        gain_study: GainStudy | None = None) -> None:
        self.measurement = m
        self.session_path = session_path
        if gain_study is not None:
            self.gain_study = gain_study
        self.reassess()
        self.measurement_changed.emit()

    def set_targets(self, target_ids: list[str], survey_ids: list[str]) -> None:
        self.target_ids = list(target_ids)
        self.survey_ids = list(survey_ids)
        self.reassess()

    def reassess(self) -> None:
        if self.measurement is None:
            self.assessment = None
        else:
            self.assessment = assess(self.measurement, self.catalog, self.target_ids, self.survey_ids,
                                     self.thresholds, self.density)
        self.assessment_changed.emit()

    def assess_other(self, m: Measurement) -> Assessment:
        return assess(m, self.catalog, self.target_ids, self.survey_ids, self.thresholds, self.density)

    def add_comparison(self, m: Measurement) -> None:
        if all(c.id != m.id for c in self.comparisons):
            self.comparisons.append(m)
            self.comparison_changed.emit()

    def remove_comparison(self, idx: int) -> None:
        if 0 <= idx < len(self.comparisons):
            del self.comparisons[idx]
            self.comparison_changed.emit()

    # -- persistence of UI state ------------------------------------------------------------
    def save_json(self, key: str, obj) -> None:
        self.settings.setValue(key, json.dumps(obj))

    def load_json(self, key: str, default=None):
        raw = self.settings.value(key)
        if not raw:
            return default
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return default

    def restore(self) -> None:
        t = self.load_json("thresholds")
        if t:
            try:
                self.thresholds = Thresholds(**t)
            except TypeError:
                pass
        d = self.load_json("density")
        if d:
            try:
                self.density = DensityPolicy(**d)
            except TypeError:
                pass

    def persist(self) -> None:
        self.save_json("thresholds", asdict(self.thresholds))
        self.save_json("density", asdict(self.density))

    def set_thresholds(self, t: Thresholds, d: DensityPolicy) -> None:
        self.thresholds, self.density = t, d
        self.persist()
        self.reassess()
        self.settings_changed.emit()

    def saved_calibration(self) -> CalibrationMeta:
        d = self.load_json("calibration", {}) or {}
        try:
            return CalibrationMeta(**d)
        except TypeError:
            return CalibrationMeta()

    def saved_antenna(self) -> AntennaInfo:
        d = self.load_json("antenna", {}) or {}
        try:
            return AntennaInfo(**d)
        except TypeError:
            return AntennaInfo()
