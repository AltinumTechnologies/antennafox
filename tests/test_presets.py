# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
from antennafox.instrument.base import DEVICE_PROFILES
from antennafox.measurement.model import InstrumentLimits
from antennafox.measurement.presets import PRESETS, SegmentTemplate, build_plan, custom_plan, plan_for_preset

V1 = DEVICE_PROFILES["librevna_v1"]


def test_full_uses_instrument_range():
    plan = plan_for_preset("full", V1)
    ov = plan.segments[0]
    assert ov.start_hz == V1.min_freq_hz and ov.stop_hz == V1.max_freq_hz and ov.points == V1.max_points


def test_clamping_is_disclosed():
    plan = plan_for_preset("wifi5", V1)
    seg = plan.segments[0]
    assert seg.stop_hz == 6e9
    assert any("limited" in o for o in plan.omissions)


def test_segment_outside_range_omitted():
    small = InstrumentLimits(min_freq_hz=1e6, max_freq_hz=3e9, max_points=1001)
    plan = plan_for_preset("dualband", small)
    assert len(plan.segments) == 1
    assert any("outside" in o for o in plan.omissions)


def test_points_capped():
    small = InstrumentLimits(min_freq_hz=1e6, max_freq_hz=3e9, max_points=101)
    plan = build_plan([SegmentTemplate("x", 1e9, 2e9, 1000)], small)
    assert plan.segments[0].points == 101
    assert plan.omissions


def test_custom_and_power_clamp():
    plan = custom_plan(1e9, 2e9, 201, V1, power_dbm=5.0)
    assert plan.power_dbm == V1.max_power_dbm
    assert any("power" in o for o in plan.omissions)


def test_all_preset_targets_exist():
    from antennafox.catalog.catalog import load_catalog
    cat = load_catalog()
    for p in PRESETS.values():
        for pid in p.targets + p.survey:
            assert cat.get(pid) is not None, pid
