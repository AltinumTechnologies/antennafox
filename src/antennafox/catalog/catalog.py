# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Versioned, data-driven frequency/protocol catalog (built-in JSON + optional user JSON)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

SCHEMA = "antennafox.catalog/1"


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Channel:
    label: str
    center_hz: float
    width_hz: float


@dataclass(frozen=True)
class BandProfile:
    id: str
    group: str
    name: dict
    region: str
    kind: str
    intervals_hz: tuple[tuple[float, float], ...]
    comment: dict = field(default_factory=dict)
    channels: tuple[Channel, ...] = ()
    gnss: bool = False
    user_defined: bool = False

    def label(self, lang: str = "en") -> str:
        return self.name.get(lang) or self.name.get("en") or self.id

    def note(self, lang: str = "en") -> str:
        return self.comment.get(lang) or self.comment.get("en") or ""

    @property
    def span(self) -> tuple[float, float]:
        return min(a for a, _ in self.intervals_hz), max(b for _, b in self.intervals_hz)

    @property
    def total_width_hz(self) -> float:
        return sum(b - a for a, b in self.intervals_hz)

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "group": self.group,
            "name": self.name,
            "region": self.region,
            "kind": self.kind,
            "intervals_hz": [list(i) for i in self.intervals_hz],
            "comment": self.comment,
        }
        if self.channels:
            d["channels"] = [
                {"label": c.label, "center_hz": c.center_hz, "width_hz": c.width_hz} for c in self.channels
            ]
        if self.gnss:
            d["gnss"] = True
        return d


def _profile_from_dict(d: dict, user_defined: bool) -> BandProfile:
    try:
        intervals = tuple((float(a), float(b)) for a, b in d["intervals_hz"])
        pid = str(d["id"])
        channels = tuple(
            Channel(str(c["label"]), float(c["center_hz"]), float(c["width_hz"])) for c in d.get("channels", [])
        )
    except (KeyError, TypeError, ValueError) as e:
        raise CatalogError(f"invalid profile entry: {d!r}") from e
    if not intervals:
        raise CatalogError(f"profile {pid}: no intervals")
    for a, b in intervals:
        if not (a > 0 and b > a):
            raise CatalogError(f"profile {pid}: invalid interval {a}–{b} Hz")
    name = d.get("name") or {"en": pid}
    if isinstance(name, str):
        name = {"en": name, "de": name}
    comment = d.get("comment") or {}
    if isinstance(comment, str):
        comment = {"en": comment, "de": comment}
    return BandProfile(
        id=pid,
        group=str(d.get("group", "User")),
        name=name,
        region=str(d.get("region", "user")),
        kind=str(d.get("kind", "user")),
        intervals_hz=tuple(sorted(intervals)),
        comment=comment,
        channels=channels,
        gnss=bool(d.get("gnss", False)),
        user_defined=user_defined,
    )


@dataclass
class Catalog:
    version: str
    profiles: list[BandProfile]
    disclaimer: dict = field(default_factory=dict)

    def by_id(self, pid: str) -> BandProfile:
        for p in self.profiles:
            if p.id == pid:
                return p
        raise KeyError(pid)

    def get(self, pid: str) -> BandProfile | None:
        try:
            return self.by_id(pid)
        except KeyError:
            return None

    def groups(self) -> list[str]:
        seen: list[str] = []
        for p in self.profiles:
            if p.group not in seen:
                seen.append(p.group)
        return seen

    def user_profiles(self) -> list[BandProfile]:
        return [p for p in self.profiles if p.user_defined]


def parse_catalog(data: dict, user_defined: bool = False) -> Catalog:
    if not isinstance(data, dict):
        raise CatalogError("catalog file must contain a JSON object")
    if data.get("schema") != SCHEMA:
        raise CatalogError(f"unsupported catalog schema {data.get('schema')!r}")
    profiles = [_profile_from_dict(p, user_defined) for p in data.get("profiles", [])]
    ids = [p.id for p in profiles]
    if len(ids) != len(set(ids)):
        raise CatalogError("duplicate profile ids")
    return Catalog(version=str(data.get("version", "?")), profiles=profiles, disclaimer=data.get("disclaimer", {}))


def load_builtin() -> Catalog:
    text = resources.files("antennafox.catalog").joinpath("data/bands.json").read_text(encoding="utf-8")
    return parse_catalog(json.loads(text))


def load_catalog(user_path: Path | None = None) -> Catalog:
    cat = load_builtin()
    if user_path and user_path.exists():
        user = parse_catalog(json.loads(user_path.read_text(encoding="utf-8")), user_defined=True)
        builtin_ids = {p.id for p in cat.profiles}
        for p in user.profiles:
            if p.id in builtin_ids:
                raise CatalogError(f"user profile id {p.id!r} collides with built-in profile")
        cat.profiles.extend(user.profiles)
        cat.version = f"{cat.version}+user:{user.version}"
    return cat


def save_user_profiles(path: Path, profiles: list[BandProfile], version: str = "1") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"schema": SCHEMA, "version": version, "profiles": [p.to_dict() for p in profiles]}
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def make_user_profile(pid: str, name: str, start_hz: float, stop_hz: float, region: str = "user",
                      comment: str = "") -> BandProfile:
    return _profile_from_dict(
        {"id": pid, "name": name, "intervals_hz": [[start_hz, stop_hz]], "region": region, "comment": comment,
         "group": "User"},
        user_defined=True,
    )
