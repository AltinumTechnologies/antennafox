# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Sweep presets (built-in and user-defined) and plan construction with honest clamping to the instrument range."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..catalog.catalog import BandProfile
from .model import InstrumentLimits, SweepPlan, SweepSegmentSpec

PRESETS_SCHEMA = "antennafox.presets/1"
MIN_POINTS, MAX_POINTS = 11, 100_001


class PresetError(ValueError):
    pass


@dataclass(frozen=True)
class SegmentTemplate:
    label: str
    start_hz: float | None  # None → instrument minimum
    stop_hz: float | None  # None → instrument maximum
    points: int | None  # None → instrument maximum


@dataclass(frozen=True)
class Preset:
    id: str
    title: str
    segments: tuple[SegmentTemplate, ...]
    targets: tuple[str, ...]
    description: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)
    survey: tuple[str, ...] = ()
    subtitle: str = ""  # second line on a Test-page tile
    tile: bool = False  # user presets: show as a tile on the Test page
    user_defined: bool = False
    band_id: str | None = None  # set for presets generated from a single catalog band


# Detail spans are wide enough to show where a detuned antenna actually resonates, not just the band itself.
_DETAIL_24 = SegmentTemplate("2.4 GHz detail", 2.00e9, 3.00e9, 1001)
_DETAIL_5 = SegmentTemplate("5 GHz detail", 4.60e9, 6.50e9, 1901)
_DETAIL_433 = SegmentTemplate("433 MHz detail", 425e6, 445e6, 401)
_DETAIL_868 = SegmentTemplate("868 MHz detail", 860e6, 875e6, 1501)
_DETAIL_GNSS_L1 = SegmentTemplate("GNSS L1 detail", 1.54e9, 1.62e9, 801)
_CONTEXT_433 = SegmentTemplate("433 MHz context", 360e6, 510e6, 301)
_CONTEXT_868 = SegmentTemplate("868 MHz context", 700e6, 1050e6, 701)
_CONTEXT_GNSS = SegmentTemplate("GNSS context", 1.30e9, 1.85e9, 551)
_DETAIL_GNSS_L5 = SegmentTemplate("GNSS L5/L2 detail", 1.15e9, 1.25e9, 501)

PRESETS: dict[str, Preset] = {
    p.id: p
    for p in [
        Preset(
            "full", "Full supported scan",
            (SegmentTemplate("Overview", None, None, None), _DETAIL_433, _DETAIL_868, _DETAIL_GNSS_L5,
             _DETAIL_GNSS_L1, _DETAIL_24, _DETAIL_5),
            (),
            "Overview of the full supported range plus dense detail sweeps of the common bands.",
            survey=("srd433_eu", "srd868_eu", "meshtastic_eu868_longfast", "meshcore_eu_narrow", "gnss_l5", "gnss_l1",
                    "ism24", "ble", "wifi5_eu_a", "wifi5_eu_a2", "wifi5_eu_b", "wifi6_eu_lpi"),
        ),
        Preset(
            "dualband", "Dual-band Wi-Fi (2.4 + 5 GHz)", (_DETAIL_24, _DETAIL_5),
            ("ism24", "wifi24_eu", "ble", "wifi5_full", "wifi5_eu_a", "wifi5_eu_a2", "wifi5_eu_b", "wifi5_us_unii3"),
            "2.4 GHz and 5 GHz Wi-Fi/BLE ranges for dual-band antennas and modules.",
            ("Frequency suitability does not replace certification of the antenna with the radio module.",),
        ),
        Preset(
            "wifi24", "2.4 GHz Wi-Fi / BLE", (_DETAIL_24,),
            ("ism24", "wifi24_eu", "ble", "bt_classic"),
        ),
        Preset(
            "wifi5", "5 GHz Wi-Fi", (_DETAIL_5,),
            ("wifi5_eu_a", "wifi5_eu_a2", "wifi5_eu_b", "wifi5_us_unii3", "wifi5_full", "wifi6_eu_lpi"),
            notes=("6 GHz Wi-Fi is, depending on the device, only partially measurable or not at all.",),
        ),
        Preset(
            "subghz_eu", "Sub-GHz EU 433/868", (_DETAIL_433, _DETAIL_868),
            ("srd433_eu", "srd868_eu", "srd868_g", "srd868_g3", "meshtastic_eu868_longfast", "meshcore_eu_narrow",
             "lorawan_eu868_default"),
        ),
        Preset(
            "lora868", "LoRa 868 MHz (Meshtastic, MeshCore)", (_CONTEXT_868, _DETAIL_868),
            ("srd868_eu", "meshtastic_eu868_longfast", "meshcore_eu_narrow"),
        ),
        Preset(
            "ism433", "433 MHz", (_CONTEXT_433, _DETAIL_433),
            ("srd433_eu",),
        ),
        Preset(
            "gnss_l1", "GNSS L1/E1", (_CONTEXT_GNSS, _DETAIL_GNSS_L1),
            ("gnss_l1", "gnss_b1i", "gnss_glonass_l1"),
            notes=("Active GNSS antennas: AntennaFox never supplies bias-tee power; the LNA is not assessed.",),
        ),
    ]
}


def build_plan(segments: list[SegmentTemplate] | tuple[SegmentTemplate, ...], limits: InstrumentLimits,
               preset_id: str = "custom", ifbw_hz: float = 1000.0, averages: int = 1, power_dbm: float = -20.0,
               parameters: tuple[str, ...] = ("S11",), extra_notes: tuple[str, ...] = ()) -> SweepPlan:
    out: list[SweepSegmentSpec] = []
    omissions: list[str] = list(extra_notes)
    for t in segments:
        start = limits.min_freq_hz if t.start_hz is None else t.start_hz
        stop = limits.max_freq_hz if t.stop_hz is None else t.stop_hz
        points = limits.max_points if t.points is None else t.points
        lo, hi = max(start, limits.min_freq_hz), min(stop, limits.max_freq_hz)
        if hi <= lo:
            omissions.append(
                f"Segment '{t.label}' ({start / 1e6:.1f}–{stop / 1e6:.1f} MHz) is outside the instrument range "
                f"({limits.min_freq_hz / 1e6:.1f}–{limits.max_freq_hz / 1e6:.1f} MHz) and is not measured."
            )
            continue
        if lo != start or hi != stop:
            omissions.append(
                f"Segment '{t.label}' limited to {lo / 1e6:.1f}–{hi / 1e6:.1f} MHz (requested "
                f"{start / 1e6:.1f}–{stop / 1e6:.1f} MHz; instrument limit). No statement is made beyond it."
            )
            # keep the original point density on the clipped span
            points = max(11, round(points * (hi - lo) / (stop - start)))
        if points > limits.max_points:
            omissions.append(f"Segment '{t.label}': points reduced to {limits.max_points}.")
            points = limits.max_points
        out.append(SweepSegmentSpec(lo, hi, int(points), t.label))
    ifbw = min(max(ifbw_hz, limits.min_ifbw_hz), limits.max_ifbw_hz)
    if ifbw != ifbw_hz:
        omissions.append(f"IF bandwidth limited to {ifbw:g} Hz.")
    pwr = min(max(power_dbm, limits.min_power_dbm), limits.max_power_dbm)
    if pwr != power_dbm:
        omissions.append(f"Output power limited to {pwr:g} dBm.")
    return SweepPlan(preset_id=preset_id, segments=out, ifbw_hz=ifbw, averages=averages, power_dbm=pwr,
                     parameters=parameters, omissions=omissions)


def plan_for_preset(preset: Preset | str, limits: InstrumentLimits, **kw) -> SweepPlan:
    p = PRESETS[preset] if isinstance(preset, str) else preset
    return build_plan(p.segments, limits, preset_id=p.id, extra_notes=p.notes, **kw)


# --- band presets -----------------------------------------------------------------------------
BAND_PRESET_PREFIX = "band_"


def _mhz(hz: float) -> str:
    return f"{hz / 1e6:.2f}".rstrip("0").rstrip(".")


def band_preset(profile: BandProfile) -> Preset:
    """Sweep plan for a single band: a context sweep around it plus a dense detail sweep over it."""
    lo, hi = profile.span
    fc, width = 0.5 * (lo + hi), hi - lo
    ctx = max(0.125 * fc, 3 * width)
    pad = max(width, 0.01 * fc)
    name = profile.label().split(" (")[0]
    return Preset(
        BAND_PRESET_PREFIX + profile.id, name,
        (SegmentTemplate(f"{name} context", max(fc - ctx, 1e3), fc + ctx, 401),
         SegmentTemplate(f"{name} detail", max(lo - pad, 1e3), hi + pad, 801)),
        (profile.id,), f"Sweep around the {profile.label()} band.",
        subtitle=f"{_mhz(lo)}–{_mhz(hi)} MHz", band_id=profile.id, user_defined=profile.user_defined,
    )


# --- user presets -----------------------------------------------------------------------------
def make_user_preset(title: str, start_hz: float, stop_hz: float, points: int, targets: list[str],
                     subtitle: str = "", tile: bool = True) -> Preset:
    title = title.strip()
    if not title:
        raise PresetError("A preset needs a title.")
    if not (1e3 <= start_hz < stop_hz <= 100e9):
        raise PresetError("Start must be below stop, both between 1 kHz and 100 GHz.")
    if not (MIN_POINTS <= points <= MAX_POINTS):
        raise PresetError(f"Points must be between {MIN_POINTS} and {MAX_POINTS}.")
    if not targets:
        raise PresetError("Select at least one target band.")
    pid = "user_" + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    if pid == "user_":
        raise PresetError("The title needs at least one letter or digit.")
    return Preset(pid, title, (SegmentTemplate(title, float(start_hz), float(stop_hz), int(points)),),
                  tuple(targets), subtitle=subtitle.strip(), tile=tile, user_defined=True)


def preset_to_dict(p: Preset) -> dict:
    return {
        "id": p.id, "title": p.title, "subtitle": p.subtitle, "tile": p.tile,
        "segments": [{"label": s.label, "start_hz": s.start_hz, "stop_hz": s.stop_hz, "points": s.points}
                     for s in p.segments],
        "targets": list(p.targets),
    }


def preset_from_dict(d: dict) -> Preset:
    try:
        segments = tuple(SegmentTemplate(str(s.get("label", d["title"])), float(s["start_hz"]), float(s["stop_hz"]),
                                         int(s["points"])) for s in d["segments"])
        targets = tuple(str(t) for t in d["targets"])
        pid, title = str(d["id"]), str(d["title"])
    except (KeyError, TypeError, ValueError) as e:
        raise PresetError(f"invalid preset entry: {d!r}") from e
    if not segments or not targets:
        raise PresetError(f"preset {pid}: needs at least one segment and one target band")
    for s in segments:
        if not (s.start_hz is not None and s.stop_hz is not None and 1e3 <= s.start_hz < s.stop_hz <= 100e9):
            raise PresetError(f"preset {pid}: invalid segment range")
        if s.points is None or not (MIN_POINTS <= s.points <= MAX_POINTS):
            raise PresetError(f"preset {pid}: invalid point count")
    return Preset(pid, title, segments, targets, subtitle=str(d.get("subtitle", "")), tile=bool(d.get("tile", True)),
                  user_defined=True)


def load_user_presets(path: Path) -> list[Preset]:
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema") != PRESETS_SCHEMA:
        raise PresetError(f"unsupported presets schema {data.get('schema')!r}")
    entries = data.get("presets", [])
    if not isinstance(entries, list):
        raise PresetError("'presets' must be a list")
    presets = [preset_from_dict(d) for d in entries]
    ids = [p.id for p in presets]
    if len(ids) != len(set(ids)):
        raise PresetError("duplicate preset ids")
    return presets


def save_user_presets(path: Path, presets: list[Preset]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema": PRESETS_SCHEMA, "presets": [preset_to_dict(p) for p in presets]}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def custom_plan(start_hz: float, stop_hz: float, points: int, limits: InstrumentLimits, **kw) -> SweepPlan:
    return build_plan([SegmentTemplate("User defined", start_hz, stop_hz, points)], limits, preset_id="custom", **kw)
