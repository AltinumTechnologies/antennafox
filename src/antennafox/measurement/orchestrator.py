# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Runs a SweepPlan segment by segment on a backend (blocking; call from a worker thread)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from .. import __version__
from ..instrument.base import AcquisitionParams, CancelToken, VNABackend
from .model import AntennaInfo, CalibrationMeta, Measurement, SweepPlan, TraceSegment

ProgressFn = Callable[[float, str], None]


def run_plan(backend: VNABackend, plan: SweepPlan, cancel: CancelToken, progress: ProgressFn | None = None,
             repeats: int = 1) -> tuple[list[TraceSegment], list[list[TraceSegment]]]:
    """Returns (primary traces, additional repeat traces). Repeats are only taken for single-segment plans,
    because repeat statistics require an identical frequency grid."""
    if not plan.segments:
        raise ValueError("The sweep plan contains no measurable segments.")
    n_rep = repeats if len(plan.segments) == 1 else 1
    total = len(plan.segments) * n_rep
    done = 0
    primary: list[TraceSegment] = []
    extra: list[list[TraceSegment]] = []
    for r in range(n_rep):
        for seg in plan.segments:
            cancel.check()
            params = AcquisitionParams(
                start_hz=seg.start_hz, stop_hz=seg.stop_hz, points=seg.points, ifbw_hz=plan.ifbw_hz,
                averages=plan.averages, power_dbm=plan.power_dbm, parameters=tuple(plan.parameters), label=seg.label,
            )
            base = done

            def sub(frac: float, msg: str, _base=base, _seg=seg, _r=r) -> None:
                if progress:
                    rep = f" (repeat {_r + 1}/{n_rep})" if n_rep > 1 else ""
                    progress((_base + frac) / total, f"{_seg.label}{rep}: {msg}")

            traces = backend.acquire(params, cancel, sub)
            if r == 0:
                primary.extend(traces)
            else:
                extra.append(traces)
            done += 1
    return primary, extra


def new_measurement(name: str, antenna: AntennaInfo, backend: VNABackend, calibration: CalibrationMeta,
                    plan: SweepPlan, traces: list[TraceSegment], repeats: list[list[TraceSegment]],
                    notes: str = "") -> Measurement:
    cal = replace(calibration, gui_reported_type=backend.calibration_type())
    return Measurement(
        name=name, antenna=antenna, instrument=backend.info(), calibration=cal, sweep_plan=plan, traces=traces,
        repeats=repeats, notes=notes, app_version=__version__,
    )
