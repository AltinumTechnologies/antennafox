# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""User-defined sweep presets: persistence, validation, plan building and UI integration."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from antennafox.instrument.base import DEVICE_PROFILES
from antennafox.measurement.presets import (
    PRESETS,
    PresetError,
    load_user_presets,
    make_user_preset,
    plan_for_preset,
    save_user_presets,
)
from antennafox.rf.bands import Conclusion
from antennafox.ui import theme
from antennafox.ui.main_window import MainWindow
from antennafox.ui.state import DEFAULT_TILES, MAX_TILES, AppState


def test_make_and_roundtrip(tmp_path):
    p = make_user_preset("LTE band 20", 780e6, 880e6, 501, ["srd868_eu"], subtitle="800 MHz")
    assert p.id == "user_lte_band_20" and p.user_defined and p.tile
    save_user_presets(tmp_path / "p.json", [p])
    loaded = load_user_presets(tmp_path / "p.json")
    assert loaded == [p]
    plan = plan_for_preset(loaded[0], DEVICE_PROFILES["librevna_v1"])
    assert plan.preset_id == "user_lte_band_20"
    assert (plan.segments[0].start_hz, plan.segments[0].stop_hz, plan.segments[0].points) == (780e6, 880e6, 501)


@pytest.mark.parametrize("kwargs,msg", [
    (dict(title=" ", start_hz=1e9, stop_hz=2e9, points=101, targets=["ism24"]), "title"),
    (dict(title="x", start_hz=2e9, stop_hz=1e9, points=101, targets=["ism24"]), "Start must be below stop"),
    (dict(title="x", start_hz=1e9, stop_hz=2e9, points=5, targets=["ism24"]), "Points"),
    (dict(title="x", start_hz=1e9, stop_hz=2e9, points=101, targets=[]), "target band"),
    (dict(title="!!!", start_hz=1e9, stop_hz=2e9, points=101, targets=["ism24"]), "letter or digit"),
])
def test_validation(kwargs, msg):
    with pytest.raises(PresetError, match=msg):
        make_user_preset(**kwargs)


def test_broken_file_is_rejected(tmp_path):
    (tmp_path / "p.json").write_text('{"schema": "antennafox.presets/1", "presets": [{"id": "x", "title": "x", '
                                     '"segments": [{"start_hz": 2e9, "stop_hz": 1e9, "points": 101}], "targets": ["a"]}]}')
    with pytest.raises(PresetError):
        load_user_presets(tmp_path / "p.json")


def test_ui_creates_lists_and_deletes_preset(qtbot, qapp, tmp_path, monkeypatch):
    theme.apply(qapp)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr("antennafox.ui.state.data_dir", lambda: tmp_path)
    state = AppState(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    w = MainWindow(state)
    qtbot.addWidget(w)
    sweep, quick = w.setup_page.sweep, w.quick
    n_builtin_tiles = len(quick.case_buttons)

    preset = sweep.create_preset("FPV 1.3 GHz", 1.1e9, 1.4e9, 401, ["gnss_l5"], subtitle="video link")
    assert sweep.preset.currentData() == preset.id
    assert state.target_ids == ["gnss_l5"]
    assert preset.id in quick.case_buttons and quick.case_buttons[preset.id].isChecked()
    # all eight slots were taken: the new tile goes in front of the full scan and the last built-in makes room
    assert len(quick.case_buttons) == n_builtin_tiles == MAX_TILES
    assert state.tile_ids()[-2:] == [preset.id, "full"] and "gnss_l1" not in quick.case_buttons
    assert (tmp_path / "user_presets.json").exists()
    assert quick.use_case_name() == "FPV 1.3 GHz"

    with pytest.raises(PresetError, match="built-in"):
        sweep.create_preset(PRESETS["wifi24"].title, 1e9, 2e9, 101, ["ism24"])
    with pytest.raises(PresetError, match="outside the sweep range"):
        sweep.create_preset("Too narrow", 1.2e9, 1.4e9, 101, ["gnss_l5"])  # L5 is 1166–1187 MHz

    state2 = AppState(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    assert preset.id in state2.presets  # persisted across restarts

    sweep._delete_preset()
    assert preset.id not in state.presets and preset.id not in quick.case_buttons
    assert len(quick.case_buttons) == n_builtin_tiles - 1


def test_tile_selection_is_persisted(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("antennafox.ui.state.data_dir", lambda: tmp_path)
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    state = AppState(settings)
    w = MainWindow(state)
    qtbot.addWidget(w)
    assert state.tile_ids() == DEFAULT_TILES
    state.set_tile_ids(["gnss_l1", "wifi24", "bogus"])
    assert [b for b in w.quick.case_buttons] == ["gnss_l1", "wifi24"]
    state.set_tile_ids(["ism433"])  # a one-element list survives the QSettings round trip
    w2 = MainWindow(AppState(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)))
    qtbot.addWidget(w2)
    assert list(w2.quick.case_buttons) == ["ism433"]


def test_any_band_can_be_a_tile(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("antennafox.ui.state.data_dir", lambda: tmp_path)
    state = AppState(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    w = MainWindow(state)
    qtbot.addWidget(w)
    assert "band_srd433_eu" in state.presets and state.presets["band_srd433_eu"].targets == ("srd433_eu",)
    state.set_tile_ids(["band_srd433_eu", "full"])
    tile = w.quick.case_buttons["band_srd433_eu"]
    assert tile.text().startswith("SRD 433 MHz EU") and "433.05" in tile.text()
    w.connect_demo()
    qtbot.waitUntil(lambda: state.connected and not state.busy, timeout=5000)
    state.backend.realtime = 0.0
    w.quick._select_case("band_srd433_eu")
    assert w.setup_page.sweep.preset.currentData() == "band_srd433_eu" and state.target_ids == ["srd433_eu"]
    w.quick.passive.setChecked(True)
    w.quick.start()
    qtbot.waitUntil(lambda: state.measurement is not None and not state.busy, timeout=20000)
    plan = state.measurement.sweep_plan
    assert plan.preset_id == "band_srd433_eu" and len(plan.segments) == 2
    assert state.assessment.band_results["srd433_eu"].conclusion != Conclusion.INSUFFICIENT

