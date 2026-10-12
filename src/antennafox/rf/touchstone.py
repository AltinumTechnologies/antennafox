# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Touchstone 1.x / basic 2.0 reader and writer for 1- and 2-port S-parameter data."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

UNIT_SCALE = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}


class TouchstoneError(ValueError):
    pass


@dataclass
class TouchstoneData:
    nports: int
    freq_hz: np.ndarray
    s: dict[str, np.ndarray]  # "S11", "S21", "S12", "S22"
    z0: float = 50.0
    comments: list[str] = field(default_factory=list)
    source_format: str = "RI"


def _pair_to_complex(a: np.ndarray, b: np.ndarray, fmt: str) -> np.ndarray:
    if fmt == "RI":
        return a + 1j * b
    ang = np.deg2rad(b)
    if fmt == "MA":
        return a * np.exp(1j * ang)
    if fmt == "DB":
        return 10 ** (a / 20.0) * np.exp(1j * ang)
    raise TouchstoneError(f"unsupported data format {fmt}")


def parse_touchstone(text: str, nports: int | None = None) -> TouchstoneData:
    unit, param, fmt, z0 = "GHZ", "S", "MA", 50.0
    option_seen = False
    comments: list[str] = []
    tokens: list[float] = []
    v2_ports: int | None = None
    two_port_order = "21_12"
    in_data = True
    for raw in text.splitlines():
        line = raw.strip()
        if "!" in line:
            line, _, c = line.partition("!")
            if c.strip():
                comments.append(c.strip())
            line = line.strip()
        if not line:
            continue
        if line.startswith("["):
            kw, _, rest = line.partition("]")
            kw = kw[1:].strip().upper()
            rest = rest.strip()
            if kw == "VERSION":
                if not rest.startswith("2"):
                    raise TouchstoneError(f"unsupported Touchstone version {rest}")
            elif kw == "NUMBER OF PORTS":
                v2_ports = int(rest)
            elif kw == "TWO-PORT DATA ORDER":
                two_port_order = rest.replace(" ", "")
            elif kw == "REFERENCE":
                vals = rest.split()
                if vals and len({float(v) for v in vals}) != 1:
                    raise TouchstoneError("per-port reference impedances are not supported")
                if vals:
                    z0 = float(vals[0])
            elif kw == "MATRIX FORMAT":
                if rest.upper() != "FULL":
                    raise TouchstoneError("only full matrix format is supported")
            elif kw == "END":
                in_data = False
            # NUMBER OF FREQUENCIES, NETWORK DATA etc. need no handling
            continue
        if line.startswith("#"):
            if option_seen:
                continue  # spec: subsequent option lines are ignored
            option_seen = True
            parts = line[1:].upper().split()
            i = 0
            while i < len(parts):
                p = parts[i]
                if p in UNIT_SCALE:
                    unit = p
                elif p in ("S", "Y", "Z", "H", "G"):
                    param = p
                elif p in ("RI", "MA", "DB"):
                    fmt = p
                elif p == "R":
                    if i + 1 >= len(parts):
                        raise TouchstoneError("option line: R without value")
                    z0 = float(parts[i + 1])
                    i += 1
                else:
                    raise TouchstoneError(f"unknown option {p!r}")
                i += 1
            continue
        if not in_data:
            continue
        try:
            tokens.extend(float(x) for x in re.split(r"[\s,]+", line) if x)
        except ValueError as e:
            raise TouchstoneError(f"non-numeric data line: {raw!r}") from e

    if param != "S":
        raise TouchstoneError(f"only S-parameters are supported (file contains {param}-parameters)")
    if not (z0 > 0 and math.isfinite(z0)):
        raise TouchstoneError(f"invalid reference impedance {z0}")
    n = v2_ports or nports
    if n is None:
        raise TouchstoneError("number of ports unknown (use .s1p/.s2p extension)")
    if n not in (1, 2):
        raise TouchstoneError(f"{n}-port files are not supported (1 or 2 ports only)")
    per = 1 + 2 * n * n
    if not tokens or len(tokens) % per:
        raise TouchstoneError(f"data length {len(tokens)} is not a multiple of {per} values per frequency")
    arr = np.array(tokens).reshape(-1, per)
    f = arr[:, 0] * UNIT_SCALE[unit]
    if not np.all(np.isfinite(arr)):
        raise TouchstoneError("non-finite values in data")
    if len(f) > 1 and np.any(np.diff(f) <= 0):
        raise TouchstoneError("frequencies are not strictly increasing")
    if np.any(f < 0):
        raise TouchstoneError("negative frequency")
    pairs = [_pair_to_complex(arr[:, 1 + 2 * k], arr[:, 2 + 2 * k], fmt) for k in range(n * n)]
    if n == 1:
        s = {"S11": pairs[0]}
    elif two_port_order == "12_21":
        s = {"S11": pairs[0], "S12": pairs[1], "S21": pairs[2], "S22": pairs[3]}
    else:  # v1 convention N11 N21 N12 N22
        s = {"S11": pairs[0], "S21": pairs[1], "S12": pairs[2], "S22": pairs[3]}
    return TouchstoneData(nports=n, freq_hz=f, s=s, z0=z0, comments=comments, source_format=fmt)


def read_touchstone(path: str | Path) -> TouchstoneData:
    path = Path(path)
    m = re.search(r"\.s(\d+)p$", path.name, re.IGNORECASE)
    nports = int(m.group(1)) if m else None
    return parse_touchstone(path.read_text(encoding="utf-8", errors="replace"), nports=nports)


def touchstone_text(freq_hz, s: dict[str, np.ndarray], z0: float = 50.0, comments: list[str] | None = None) -> str:
    """Write Touchstone 1.1 RI. Refuses unless the data are mathematically sufficient for the port count."""
    f = np.asarray(freq_hz, dtype=float)
    keys = set(s)
    if keys == {"S11"}:
        order = ["S11"]
    elif {"S11", "S21", "S12", "S22"} <= keys:
        order = ["S11", "S21", "S12", "S22"]
    else:
        raise TouchstoneError(
            "Touchstone export requires S11 (.s1p) or the complete S11/S21/S12/S22 set (.s2p); "
            f"available: {sorted(keys)}"
        )
    for k in order:
        if np.asarray(s[k]).shape != f.shape:
            raise TouchstoneError(f"{k} is not on the frequency grid")
    lines = [f"! {c}" for c in (comments or [])]
    lines.append(f"# HZ S RI R {z0:g}")
    for i, fi in enumerate(f):
        vals = []
        for k in order:
            v = complex(s[k][i])
            vals += [f"{v.real:.12e}", f"{v.imag:.12e}"]
        lines.append(f"{fi:.0f} " + " ".join(vals))
    return "\n".join(lines) + "\n"
