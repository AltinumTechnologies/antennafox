# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Container for a guided comparative gain / pattern study (serializable)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from .comparative import (
    GainConvention,
    GainResult,
    GainSetup,
    PatternCut,
    PatternPoint,
    ReferenceAntenna,
    S21Capture,
    estimate_gain,
)


@dataclass
class GainStudy:
    reference: ReferenceAntenna = field(default_factory=ReferenceAntenna)
    setup: GainSetup = field(default_factory=lambda: GainSetup(frequency_hz=2.44e9))
    ref_captures: list[S21Capture] = field(default_factory=list)
    dut_captures: list[S21Capture] = field(default_factory=list)
    pattern_cuts: list[PatternCut] = field(default_factory=list)
    checklist: dict[str, bool] = field(default_factory=dict)
    notes: str = ""

    def estimate(self) -> GainResult | None:
        """Current gain estimate, or None until both a reference and a DUT capture exist."""
        if not (self.ref_captures and self.dut_captures):
            return None
        return estimate_gain(self.reference, self.setup, self.ref_captures, self.dut_captures)

    def to_dict(self) -> dict:
        ref = asdict(self.reference)
        ref["convention"] = self.reference.convention.value
        ref["gamma_table"] = [[f, g.real, g.imag] for f, g in self.reference.gamma_table]
        return {
            "reference": ref,
            "setup": asdict(self.setup),
            "pattern_cuts": [asdict(c) for c in self.pattern_cuts],
            "checklist": self.checklist,
            "notes": self.notes,
        }

    @staticmethod
    def from_dict(d: dict, ref_caps: list[S21Capture], dut_caps: list[S21Capture]) -> GainStudy:
        r = dict(d.get("reference", {}))
        r["convention"] = GainConvention(r.get("convention", "realized"))
        r["gain_table"] = [tuple(x) for x in r.get("gain_table", [])]
        r["gamma_table"] = [(x[0], complex(x[1], x[2])) for x in r.get("gamma_table", [])]
        cuts = []
        for c in d.get("pattern_cuts", []):
            pts = [PatternPoint(**p) for p in c.pop("points", [])]
            cuts.append(PatternCut(**c, points=pts))
        return GainStudy(
            reference=ReferenceAntenna(**r), setup=GainSetup(**d.get("setup", {"frequency_hz": 2.44e9})),
            ref_captures=ref_caps, dut_captures=dut_caps, pattern_cuts=cuts,
            checklist=d.get("checklist", {}), notes=d.get("notes", ""),
        )

