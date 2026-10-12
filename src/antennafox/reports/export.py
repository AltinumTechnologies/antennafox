# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""CSV / JSON / Touchstone exports. Touchstone only when the S-parameter set is mathematically sufficient."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np

from ..measurement.model import Measurement
from ..rf.rfmath import impedance, s11_db, vswr
from ..rf.touchstone import TouchstoneError, touchstone_text


def export_csv(m: Measurement, path: str | Path) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as fh:
        fh.write(f"# AntennaFox export; measurement={m.name}; id={m.id}; simulated={m.simulated}\n")
        fh.write("# S11_dB = 20*log10|Gamma| (negative = matched); Z in Ohm at Z0 = 50 Ohm\n")
        w = csv.writer(fh)
        w.writerow(["segment", "parameter", "freq_hz", "re", "im", "mag_db", "phase_deg", "vswr", "r_ohm", "x_ohm"])
        for t in sorted(m.traces, key=lambda t: (t.parameter, t.start_hz)):
            db = s11_db(t.data)
            ph = np.degrees(np.angle(t.data))
            is_refl = t.parameter in ("S11", "S22")
            v = vswr(t.data) if is_refl else np.full(len(t.data), np.nan)
            z = impedance(t.data) if is_refl else np.full(len(t.data), np.nan + 0j)
            for i in range(len(t.freq_hz)):
                w.writerow([t.label, t.parameter, f"{t.freq_hz[i]:.0f}", f"{t.data[i].real:.9e}",
                            f"{t.data[i].imag:.9e}", f"{db[i]:.4f}", f"{ph[i]:.3f}",
                            "" if not np.isfinite(v[i]) else f"{v[i]:.4f}",
                            "" if not np.isfinite(z[i].real) else f"{z[i].real:.3f}",
                            "" if not np.isfinite(z[i].imag) else f"{z[i].imag:.3f}"])
    return path


def export_json(m: Measurement, path: str | Path, derived: dict | None = None) -> Path:
    path = Path(path)
    doc = {
        "format": "antennafox.export/1",
        "measurement": m.to_meta_dict(),
        "simulated": m.simulated,
        "traces": [
            {"parameter": t.parameter, "label": t.label, "source": t.source, "acquired_at": t.acquired_at,
             "provenance": t.provenance, "freq_hz": t.freq_hz.tolist(), "re": np.real(t.data).tolist(),
             "im": np.imag(t.data).tolist()}
            for t in m.traces
        ],
        "derived": derived or {},
    }
    path.write_text(json.dumps(doc, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def touchstone_possible(m: Measurement) -> tuple[bool, str]:
    """Touchstone needs one frequency grid: exactly one segment per parameter and S11 (or all four S)."""
    params = m.parameters()
    for p in params:
        if len(m.segments(p)) != 1:
            return False, (f"{p} consists of {len(m.segments(p))} segments with different grids – "
                           "Touchstone requires a single grid (export segments individually).")
    if "S11" not in params:
        return False, "No S11 data."
    if set(params) >= {"S11", "S21", "S12", "S22"}:
        grids = [m.segments(p)[0].freq_hz for p in ("S11", "S21", "S12", "S22")]
        if all(np.array_equal(grids[0], g) for g in grids[1:]):
            return True, ".s2p"
        return False, "S-parameters are not on an identical frequency grid."
    return True, ".s1p"


def export_touchstone(m: Measurement, path: str | Path, segment_index: int | None = None) -> Path:
    path = Path(path)
    comments = [f"AntennaFox export: {m.name} ({m.id})", f"Instrument: {m.instrument.idn or m.instrument.backend}",
                f"Calibration (instrument-reported): {m.calibration.gui_reported_type}", f"Simulated: {m.simulated}"]
    if segment_index is not None:
        seg = m.segments("S11")[segment_index]
        text = touchstone_text(seg.freq_hz, {"S11": seg.data}, comments=comments + [f"Segment: {seg.label}"])
        path = path.with_suffix(".s1p")
    else:
        ok, info = touchstone_possible(m)
        if not ok:
            raise TouchstoneError(info)
        if info == ".s2p":
            f = m.segments("S11")[0].freq_hz
            s = {p: m.segments(p)[0].data for p in ("S11", "S21", "S12", "S22")}
        else:
            seg = m.segments("S11")[0]
            f, s = seg.freq_hz, {"S11": seg.data}
        text = touchstone_text(f, s, comments=comments)
        path = path.with_suffix(info)
    # Touchstone is an ASCII format: map typographic punctuation instead of leaving "?" in the comments
    text = text.translate(str.maketrans({"–": "-", "—": "-", "…": "...", "·": "-", "Ω": "Ohm", "°": "deg"}))
    path.write_text(text, encoding="ascii", errors="replace")
    return path
