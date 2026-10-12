# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Session bundle (.afox, a ZIP archive): measurement.json plus lossless per-trace CSV with SHA-256 checksums.
The format is documented in docs/FILE_FORMAT.md."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from dataclasses import fields
from pathlib import Path

import numpy as np

from .. import __version__
from ..gain.comparative import S21Capture
from ..gain.study import GainStudy
from ..rf.touchstone import read_touchstone
from .model import (
    AntennaInfo,
    CalibrationMeta,
    InstrumentInfo,
    InstrumentLimits,
    Measurement,
    SweepPlan,
    SweepSegmentSpec,
    TraceSegment,
    utc_now_iso,
)

FORMAT = "antennafox.session/1"
SUFFIX = ".afox"


class SessionError(ValueError):
    pass


def _trace_csv(freq, data, header: str) -> bytes:
    buf = io.StringIO()
    buf.write(f"# {header}\n")
    buf.write("freq_hz,re,im\n")
    for f, v in zip(freq, data, strict=True):
        buf.write(f"{float(f)!r},{float(v.real)!r},{float(v.imag)!r}\n")
    return buf.getvalue().encode("utf-8")


def _parse_trace_csv(raw: bytes) -> tuple[np.ndarray, np.ndarray]:
    lines = [ln for ln in raw.decode("utf-8").splitlines() if ln and not ln.startswith("#")]
    if not lines or lines[0].strip() != "freq_hz,re,im":
        raise SessionError("Trace CSV without a valid header line")
    arr = np.array([[float(x) for x in ln.split(",")] for ln in lines[1:]], dtype=float).reshape(-1, 3)
    return arr[:, 0], arr[:, 1] + 1j * arr[:, 2]


def _only_known(cls, d: dict) -> dict:
    names = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in names}


def save_session(path: str | Path, m: Measurement, gain_study: GainStudy | None = None,
                 derived: dict | None = None) -> Path:
    path = Path(path)
    if path.suffix != SUFFIX:
        path = path.with_suffix(SUFFIX)
    files: dict[str, bytes] = {}
    entries = []

    def add_trace(t: TraceSegment, prefix: str) -> dict:
        name = f"traces/{prefix}_{len(files):03d}_{t.parameter}.csv"
        blob = _trace_csv(t.freq_hz, t.data, f"parameter={t.parameter}; source={t.source}; acquired_at={t.acquired_at}")
        files[name] = blob
        return {"file": name, "parameter": t.parameter, "label": t.label, "source": t.source,
                "acquired_at": t.acquired_at, "provenance": t.provenance, "points": len(t.freq_hz), "z0": t.z0,
                "sha256": hashlib.sha256(blob).hexdigest()}

    for t in m.traces:
        entries.append(add_trace(t, "main"))
    repeats = [[add_trace(t, f"rep{i}") for t in rep] for i, rep in enumerate(m.repeats)]
    gain = None
    if gain_study is not None:
        caps = []
        for c in gain_study.ref_captures + gain_study.dut_captures:
            name = f"gain/{c.role}_{len(files):03d}.csv"
            blob = _trace_csv(c.freq_hz, c.data, f"S21 role={c.role}; acquired_at={c.acquired_at}")
            files[name] = blob
            caps.append({"file": name, "role": c.role, "acquired_at": c.acquired_at, "label": c.label,
                         "angle_deg": c.angle_deg, "simulated": c.simulated, "sha256": hashlib.sha256(blob).hexdigest()})
        gain = {"study": gain_study.to_dict(), "captures": caps}

    doc = {
        "format": FORMAT,
        "saved_at": utc_now_iso(),
        "saved_by": f"AntennaFox {__version__}",
        "measurement": m.to_meta_dict(),
        "simulated": m.simulated,
        "traces": entries,
        "repeats": repeats,
        "gain": gain,
        "derived": {"note": "Derived values – recomputed on load, stored for reference only.",
                    **(derived or {})},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr("measurement.json", json.dumps(doc, indent=2, ensure_ascii=False, default=_json_default))
        for name, blob in files.items():
            z.writestr(name, blob)
    tmp.replace(path)
    return path


def _json_default(o):
    if isinstance(o, complex):
        return [o.real, o.imag]
    if isinstance(o, np.generic):
        return o.item()
    if hasattr(o, "value"):
        return o.value
    raise TypeError(f"not serializable: {type(o)}")


def load_session(path: str | Path) -> tuple[Measurement, GainStudy | None, dict]:
    path = Path(path)
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, FileNotFoundError) as e:
        raise SessionError(f"Not a valid AntennaFox session: {e}") from e
    with z:
        doc = json.loads(z.read("measurement.json"))
        if doc.get("format") != FORMAT:
            raise SessionError(f"Unknown session format {doc.get('format')!r}")

        def read_trace(e: dict) -> TraceSegment:
            blob = z.read(e["file"])
            if hashlib.sha256(blob).hexdigest() != e["sha256"]:
                raise SessionError(f"Checksum mismatch for {e['file']} – file modified or corrupted.")
            f, d = _parse_trace_csv(blob)
            return TraceSegment(parameter=e["parameter"], freq_hz=f, data=d, acquired_at=e["acquired_at"],
                                source=e["source"], label=e.get("label", ""), provenance=e.get("provenance", {}),
                                z0=float(e.get("z0", 50.0)))

        md = doc["measurement"]
        inst = dict(md["instrument"])
        lim = inst.pop("limits", None)
        instrument = InstrumentInfo(**_only_known(InstrumentInfo, inst),
                                    limits=InstrumentLimits(**lim) if lim else None)
        sp = md.get("sweep_plan")
        plan = None
        if sp:
            segs = [SweepSegmentSpec(**s) for s in sp.pop("segments", [])]
            sp["parameters"] = tuple(sp.get("parameters", ("S11",)))
            plan = SweepPlan(**_only_known(SweepPlan, sp), segments=segs)
        m = Measurement(
            name=md["name"], antenna=AntennaInfo(**_only_known(AntennaInfo, md["antenna"])), instrument=instrument,
            calibration=CalibrationMeta(**_only_known(CalibrationMeta, md["calibration"])), sweep_plan=plan,
            traces=[read_trace(e) for e in doc["traces"]],
            repeats=[[read_trace(e) for e in rep] for rep in doc.get("repeats", [])],
            notes=md.get("notes", ""), id=md["id"], created_at=md["created_at"], app_version=md.get("app_version", ""),
            operator=md.get("operator", ""), project=md.get("project", ""),
        )
        study = None
        if doc.get("gain"):
            caps = {"ref": [], "dut": []}
            for c in doc["gain"]["captures"]:
                blob = z.read(c["file"])
                if hashlib.sha256(blob).hexdigest() != c["sha256"]:
                    raise SessionError(f"Checksum mismatch for {c['file']}.")
                f, d = _parse_trace_csv(blob)
                caps[c["role"]].append(S21Capture(role=c["role"], freq_hz=f, data=d, acquired_at=c["acquired_at"],
                                                  label=c.get("label", ""), angle_deg=c.get("angle_deg"),
                                                  simulated=c.get("simulated", False)))
            study = GainStudy.from_dict(doc["gain"]["study"], caps["ref"], caps["dut"])
        return m, study, doc


def measurement_from_touchstone(path: str | Path, name: str | None = None) -> Measurement:
    """Imported files enter the same Measurement pipeline as live sweeps but are never a connected instrument:
    backend "import", no limits, calibration state unknown."""
    ts = read_touchstone(path)
    path = Path(path)
    if abs(ts.z0 - 50.0) > 1e-9:
        raise SessionError(f"Reference impedance {ts.z0:g} Ω is not supported (50 Ω only).")
    stamp = utc_now_iso()
    traces = [
        TraceSegment(parameter=k, freq_hz=ts.freq_hz, data=v, acquired_at=stamp, source="touchstone-import",
                     label=path.name, provenance={"file": path.name, "format": ts.source_format, "z0": ts.z0,
                                                  "comments": ts.comments[:20]}, z0=ts.z0)
        for k, v in ts.s.items()
    ]
    return Measurement(
        name=name or path.stem, antenna=AntennaInfo(name=path.stem),
        instrument=InstrumentInfo(backend="import", idn=f"Touchstone import {path.name}"),
        calibration=CalibrationMeta(notes="Import: calibration state of the source file is unknown."),
        sweep_plan=None, traces=traces, notes=f"Imported from {path.name}", app_version=__version__,
    )
