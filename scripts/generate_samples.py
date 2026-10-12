# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regenerate the simulated sample datasets in samples/ (deterministic)."""

from pathlib import Path

from antennafox.catalog.catalog import load_catalog
from antennafox.instrument.base import CancelToken
from antennafox.instrument.simulated import SimulatedBackend
from antennafox.measurement.model import AntennaInfo, CalibrationMeta
from antennafox.measurement.orchestrator import new_measurement, run_plan
from antennafox.measurement.presets import PRESETS, plan_for_preset
from antennafox.measurement.session import save_session
from antennafox.reports.export import export_touchstone
from antennafox.reports.pdf import build_report
from antennafox.rf.assessment import assess

OUT = Path(__file__).resolve().parent.parent / "samples"
CASES = [
    ("sim_dualband_whip", "dualband_24_5", "dualband", "Dual-band 2.4/5 GHz whip (simulated)"),
    ("sim_lora868_helix", "lora868_narrow", "subghz_eu", "868 MHz helix (simulated)"),
    ("sim_gnss_patch", "gnss_l1_patch", "gnss_l1", "GNSS L1 patch (simulated)"),
    ("sim_lossy_matched", "lossy_matched", "full", "Lossy but matched load (simulated)"),
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    cat = load_catalog()
    for stem, model, preset, name in CASES:
        b = SimulatedBackend(model, seed=42)
        b.connect()
        plan = plan_for_preset(preset, b.limits)
        traces, repeats = run_plan(b, plan, CancelToken())
        m = new_measurement(name, AntennaInfo(name=name, connector="SMA"), b, CalibrationMeta(), plan, traces, repeats)
        m.id = f"sample-{stem}"
        m.created_at = "2026-10-11T12:00:00+00:00"
        a = assess(m, cat, list(PRESETS[preset].targets), list(PRESETS[preset].survey))
        session = save_session(OUT / stem, m)
        export_touchstone(m, OUT / stem, segment_index=0)
        if stem == "sim_dualband_whip":
            build_report(OUT / f"{stem}.pdf", m, a, cat, session_ref=session.name)
        print("wrote", session.name)


if __name__ == "__main__":
    main()
