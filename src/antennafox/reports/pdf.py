# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""PDF antenna report (reportlab). Always states the gain status explicitly."""

from __future__ import annotations

import io
from collections.abc import Callable
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any, cast

import matplotlib
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    HRFlowable,
    Image,
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .. import REPO_URL, __version__
from ..catalog.catalog import Catalog
from ..gain.comparative import GainResult, GainStatus
from ..gain.study import GainStudy
from ..measurement.model import ENVIRONMENTS, REFERENCE_PLANES, CalibrationStatus, Measurement
from ..rf.assessment import EVIDENCE_TEXT, GAIN_NOT_MEASURED, Assessment, MatchRating, assess, point_values
from ..rf.bands import Conclusion, Coverage
from ..rf.explain import (
    accepted_floor_percent,
    band_resonance,
    headline,
    mismatch_ceil_db,
    mismatch_loss_db,
    short_label,
)
from . import charts

_FONT_READY = False
LOGO = Path(__file__).resolve().parent.parent / "assets" / "antennafox-logo.png"

NAVY = colors.HexColor("#0a0e14")
ORANGE = colors.HexColor("#f57c1f")
INK = colors.HexColor("#0f172a")
MUTED = colors.HexColor("#64748b")
RULE = colors.HexColor("#e2e8f0")
ZEBRA = colors.HexColor("#f8fafc")
RATING_INK = {
    MatchRating.GOOD: colors.HexColor("#15803d"), MatchRating.MARGINAL: colors.HexColor("#b45309"),
    MatchRating.POOR: colors.HexColor("#b91c1c"), MatchRating.SUSPECT: colors.HexColor("#b45309"),
}
RATING_TINT = {
    MatchRating.GOOD: colors.HexColor("#ecfdf3"), MatchRating.MARGINAL: colors.HexColor("#fffbeb"),
    MatchRating.POOR: colors.HexColor("#fef2f2"), MatchRating.SUSPECT: colors.HexColor("#fffbeb"),
}

COVERAGE_TEXT = {Coverage.MEASURED: "Measured", Coverage.PARTIAL: "Partially measured", Coverage.NOT_MEASURED: "Not measured"}
CONCLUSION_TEXT = {
    Conclusion.WELL_MATCHED: "Well matched",
    Conclusion.MARGINAL: "Marginal",
    Conclusion.POOR: "Poor match",
    Conclusion.INSUFFICIENT: "Insufficient data",
}
CONCLUSION_COLOR = {
    Conclusion.WELL_MATCHED: colors.HexColor("#dcfce7"),
    Conclusion.MARGINAL: colors.HexColor("#fef9c3"),
    Conclusion.POOR: colors.HexColor("#fee2e2"),
    Conclusion.INSUFFICIENT: colors.HexColor("#f1f5f9"),
}
CONCLUSION_INK = {
    Conclusion.WELL_MATCHED: colors.HexColor("#15803d"),
    Conclusion.MARGINAL: colors.HexColor("#b45309"),
    Conclusion.POOR: colors.HexColor("#b91c1c"),
    Conclusion.INSUFFICIENT: colors.HexColor("#64748b"),
}
CAL_TEXT = {
    CalibrationStatus.NOT_VERIFIED: "Not verified",
    CalibrationStatus.USER_CONFIRMED: "User confirmed",
    CalibrationStatus.VALID_METADATA: "Valid metadata",
}


def _fonts() -> tuple[str, str]:
    global _FONT_READY
    if not _FONT_READY:
        base = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
        pdfmetrics.registerFont(TTFont("DejaVu", str(base / "DejaVuSans.ttf")))
        pdfmetrics.registerFont(TTFont("DejaVu-Bold", str(base / "DejaVuSans-Bold.ttf")))
        _FONT_READY = True
    return "DejaVu", "DejaVu-Bold"


def _styles():
    reg, bold = _fonts()
    ss = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=ss["Title"], fontName=bold, fontSize=16, leading=19, textColor=colors.white,
                                alignment=0, spaceAfter=0),
        "subtitle": ParagraphStyle("st", parent=ss["BodyText"], fontName=reg, fontSize=8.2, leading=11,
                                   textColor=colors.HexColor("#b6c2d2")),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontName=bold, fontSize=8, leading=10, textColor=ORANGE,
                             spaceBefore=10, spaceAfter=1),
        "body": ParagraphStyle("b", parent=ss["BodyText"], fontName=reg, fontSize=8.6, leading=11, textColor=INK),
        "small": ParagraphStyle("s", parent=ss["BodyText"], fontName=reg, fontSize=7.2, leading=9, textColor=MUTED),
        "cell": ParagraphStyle("c", parent=ss["BodyText"], fontName=reg, fontSize=7.2, leading=8.8, textColor=INK),
        "key": ParagraphStyle("k", parent=ss["BodyText"], fontName=reg, fontSize=7.2, leading=8.8, textColor=MUTED),
        "verdict": ParagraphStyle("v", parent=ss["BodyText"], fontName=bold, fontSize=13, leading=16),
        "sim": ParagraphStyle("sim", parent=ss["BodyText"], fontName=bold, fontSize=10, textColor=colors.white,
                              alignment=TA_CENTER, backColor=colors.HexColor("#dc2626"), borderPadding=3),
    }


def _section(story, text: str, S, *keep_with) -> None:
    """Section label; pass the flowables that must stay on the same page as the heading."""
    head = [Paragraph(text.upper(), S["h2"]), HRFlowable(width="100%", thickness=0.6, color=RULE, spaceBefore=0,
                                                         spaceAfter=4)]
    story.append(KeepTogether([*head, *keep_with]) if keep_with else head[0])
    if not keep_with:
        story.append(head[1])


def _kv(rows, S, widths=(50, 124)):
    """Key/value table without a grid: dim keys, hairline between rows."""
    reg, _ = _fonts()
    t = Table([[Paragraph(str(k), S["key"]), Paragraph(str(v), S["cell"])] for k, v in rows],
              colWidths=[w * mm for w in widths])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), reg), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
    ]))
    return t


def _fmt(v, spec=".1f", unit="") -> str:
    if v is None:
        return "–"
    try:
        if v == float("inf"):
            return "∞"
    except TypeError:
        pass
    return f"{v:{spec}}{unit}"


def _accepted(worst_s11_db: float | None) -> str:
    if worst_s11_db is None:
        return "–"
    loss = mismatch_ceil_db(worst_s11_db)
    return f"≥ {accepted_floor_percent(worst_s11_db)} % (≤ {loss:.1f} dB)" if loss != float("inf") else "0 %"


def _local(iso: str) -> str:
    """ISO timestamp (UTC, as stored in sessions) in the reader's local time; unparseable values pass through."""
    try:
        return datetime.fromisoformat(iso).astimezone().strftime("%Y-%m-%d %H:%M %Z")
    except ValueError:
        return iso or "–"


def _region_representatives(catalog: Catalog, ids: list[str]) -> list[str]:
    """One band per contiguous frequency region: the widest band of each group of overlapping target bands."""
    spans = sorted(((catalog.by_id(p).span, p) for p in ids if catalog.get(p)), key=lambda t: t[0][0])
    groups: list[list] = []
    for (lo, hi), pid in spans:
        if groups and lo <= groups[-1][1]:
            groups[-1][1] = max(groups[-1][1], hi)
            groups[-1][2].append(pid)
        else:
            groups.append([lo, hi, [pid]])
    return [max(g[2], key=lambda p: catalog.by_id(p).span[1] - catalog.by_id(p).span[0]) for g in groups]


def _instrument_line(m: Measurement) -> str:
    i = m.instrument
    if m.simulated:
        return "Simulation – no instrument"
    parts = [f"{i.model} {i.serial}".strip() if i.serial else (i.idn or i.backend)]
    if i.firmware:
        parts.append(f"firmware {i.firmware}")
    if i.hardware_revision:
        parts.append(f"hardware {i.hardware_revision}")
    if i.gui_version:
        parts.append(f"{i.host_software or 'host software'} {i.gui_version}")
    return " · ".join(parts)


def _img(fig, width_mm: float) -> Image:
    data = charts.fig_bytes(fig, "png")
    img = Image(io.BytesIO(data))
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width_mm * mm
    img.drawHeight = width_mm * mm * ratio
    return img


def _table(rows, col_widths, header=True, style_extra=None):
    reg, bold = _fonts()
    t = Table(rows, colWidths=[w * mm for w in col_widths], repeatRows=1 if header else 0)
    st = [
        ("FONTNAME", (0, 0), (-1, -1), reg),
        ("FONTSIZE", (0, 0), (-1, -1), 7.2),
        ("TEXTCOLOR", (0, 0), (-1, -1), INK),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, RULE),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
               ("FONTNAME", (0, 0), (-1, 0), bold), ("LINEBELOW", (0, 0), (-1, 0), 1.2, ORANGE)]
        st += [("BACKGROUND", (0, r), (-1, r), ZEBRA) for r in range(2, len(rows), 2)]
    t.setStyle(TableStyle(st + (style_extra or [])))
    return t


class _NumberedCanvas(pdfcanvas.Canvas):
    """Two-pass canvas so the footer can say 'Page n of N'; footer(canvas, page, total) is drawn on save."""

    def __init__(self, *args, footer: Callable[[Any, int, int], None] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._footer = footer
        self._saved: list[dict] = []

    def showPage(self):
        self._saved.append(dict(self.__dict__))
        cast(Any, self)._startPage()  # reportlab keeps this helper private but documents the recipe

    def save(self):
        total = len(self._saved)
        for i, state in enumerate(self._saved, start=1):
            self.__dict__.update(state)
            if self._footer:
                self._footer(self, i, total)
            super().showPage()
        super().save()


def _vswr_of(s11_db_value: float) -> float:
    g = 10 ** (s11_db_value / 20)
    return (1 + g) / (1 - g)


def _z_text(z: complex) -> str:
    return f"{z.real:.1f} {'+' if z.imag >= 0 else '−'} j{abs(z.imag):.1f} Ω"


def build_report(path: str | Path, m: Measurement, a: Assessment, catalog: Catalog,
                 gain_study: GainStudy | None = None, session_ref: str = "",
                 compare: list[Measurement] | None = None, markers: list[float] | None = None) -> Path:
    path = Path(path)
    S = _styles()
    story = []
    P = Paragraph
    f, g = m.merged("S11")
    thr = a.thresholds.pass_s11_db

    logo = Image(str(LOGO), 14 * mm, 14 * mm)
    generated = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    who = " · ".join(x for x in (f"Operator {m.operator}" if m.operator else "", f"Project {m.project}" if m.project else "",
                                 f"DUT {m.antenna.dut_reference}" if m.antenna.dut_reference else "") if x)
    head = Table([[logo, [P("Antenna Test Report", S["title"]),
                          P(f"{m.name}", S["subtitle"]),
                          P(f"Measured {_local(m.created_at)} · Report generated {generated} · AntennaFox {__version__}",
                            S["subtitle"]),
                          P(f"Report {m.report_id}" + (f" · {who}" if who else ""), S["subtitle"])]]],
                 colWidths=[18 * mm, 162 * mm])
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, -1), (-1, -1), 1.5, ORANGE),
    ]))
    story.append(head)
    if m.simulated:
        story.append(P("SIMULATION – NOT A REAL MEASUREMENT", S["sim"]))
    story.append(Spacer(1, 8))

    # --- executive verdict
    gain_res: GainResult | None = gain_study.estimate() if gain_study is not None else None
    if gain_res and gain_res.status == GainStatus.ABSOLUTE:
        gain_line = (f"Estimated directional realized gain: {gain_res.absolute_text()} at "
                     f"{gain_res.frequency_hz / 1e6:.1f} MHz")
    elif gain_res and gain_res.status == GainStatus.RELATIVE_ONLY:
        gain_line = (f"Not measured (absolute value not determinable) – relative comparison only: "
                     f"{gain_res.relative_text()} at {gain_res.frequency_hz / 1e6:.1f} MHz")
    else:
        gain_line = GAIN_NOT_MEASURED
    pattern_line = ("Measured individual cuts (not a complete 3D pattern)"
                    if gain_study and any(c.points for c in gain_study.pattern_cuts)
                    else GAIN_NOT_MEASURED)
    span = m.span("S11")
    ink = RATING_INK.get(a.rating, MUTED)
    verdict = ParagraphStyle("vc", parent=S["verdict"], textColor=ink)
    _, sentence = headline(a, "the target bands")
    card = Table([[[P(a.headline, verdict), P(sentence, S["body"])]]], colWidths=[180 * mm])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), RATING_TINT.get(a.rating, ZEBRA)),
        ("LINEBEFORE", (0, 0), (0, -1), 3, ink),
        ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(card)
    story.append(Spacer(1, 4))
    plane = REFERENCE_PLANES.get(m.calibration.reference_plane, m.calibration.reference_plane)
    scope = (f"<b>Scope:</b> one-port S11 at the reference plane '{plane}'"
             + (f" via {m.antenna.cable}" if m.antenna.cable else "")
             + f"; antenna {ENVIRONMENTS.get(m.antenna.environment, 'environment not stated')}"
             + f"; acceptance S11 ≤ {thr:.0f} dB (VSWR ≤ {_vswr_of(thr):.2f}) at every point of each target band; "
             "no extrapolation beyond the measured span.")
    story.append(P(scope, S["body"]))
    story.append(Spacer(1, 5))
    facts = [
        ["Worst target band", f"{catalog.by_id(a.worst_target.profile_id).label()} – "
                              f"{CONCLUSION_TEXT[a.worst_target.conclusion]} (worst S11 "
                              f"{_fmt(a.worst_target.worst_s11_db)} dB)" if a.worst_target else "–"],
        ["Evidence quality", EVIDENCE_TEXT[a.evidence]],
        ["Calibration", CAL_TEXT[a.calibration_status]],
        ["Gain", gain_line],
        ["Radiation pattern", pattern_line],
        ["Measured span", f"{span[0] / 1e6:.3f}–{span[1] / 1e6:.3f} MHz" if span else "–"],
        ["Instrument", _instrument_line(m)],
    ]
    story.append(_kv(facts, S, (36, 144)))
    story.append(Spacer(1, 3))
    for d in a.disclaimers:
        story.append(P("⚠ " + d, S["small"]))

    # --- band matrix
    _section(story, "Frequency / protocol bands", S)
    hdr = ParagraphStyle("hdr", parent=S["cell"], textColor=colors.white, fontName=_fonts()[1])
    rows: list[list] = [[P(h, hdr) for h in ("Band", "Region", "Coverage", "Worst S11", "Power accepted",
                                             "Max VSWR", f"Span ≤ {thr:.0f} dB", "Pts", "Result")]]
    extra = []
    for i, pid in enumerate(a.shown_ids, start=1):
        r = a.band_results[pid]
        prof = catalog.by_id(pid)
        res_style = ParagraphStyle("res", parent=S["cell"], fontName=_fonts()[1], textColor=CONCLUSION_INK[r.conclusion])
        rows.append([
            P(prof.label() + ("" if pid in a.target_ids or not a.target_ids else " (survey)"), S["cell"]), prof.region,
            P(f"{COVERAGE_TEXT[r.coverage]} ({r.covered_fraction:.0%})", S["cell"]),
            _fmt(r.worst_s11_db, ".1f", " dB"), _accepted(r.worst_s11_db), _fmt(r.max_vswr, ".2f"),
            "–" if r.matched_fraction is None else f"{r.matched_fraction:.0%}", str(r.n_points),
            P(CONCLUSION_TEXT[r.conclusion], res_style),
        ])
        extra.append(("BACKGROUND", (8, i), (8, i), CONCLUSION_COLOR[r.conclusion]))
    story.append(_table(rows, [40, 14, 24, 14, 27, 12, 13, 9, 27], style_extra=extra))
    story.append(P(
        f"Thresholds: well matched ≤ {a.thresholds.pass_s11_db:.0f} dB (VSWR ≈ 1.92), marginal ≤ {a.thresholds.warn_s11_db:.0f} dB. "
        "Each band is rated by its worst value over the entire required interval; no extrapolation beyond the "
        "measured span. 'Power accepted' is the share of the incident power not reflected at the worst point "
        "(1 − |Γ|²), rounded down, with the mismatch loss rounded up; it says nothing about how much is radiated. "
        f"'Span ≤ {thr:.0f} dB' is the share of the band's frequency span that lies below the threshold. "
        "Convention: S11 [dB] = 20·log10|Γ| (negative = matched), return loss RL = −S11.", S["small"]))
    notes = [(catalog.by_id(pid).label(), n) for pid in a.shown_ids for n in a.band_results[pid].notes]
    for label, n in notes:
        story.append(P(f"• {label}: {n}", S["small"]))

    # --- values at band centre (matching work cares about the centre, not only the dip)
    centre_ids = [pid for pid in (a.target_ids or a.shown_ids) if pid in a.band_results]
    centre_rows: list[list] = [[P(h, hdr) for h in ("Band", "Centre", "S11", "VSWR", "Impedance", "Power accepted")]]
    for pid in centre_ids[:10]:
        prof = catalog.by_id(pid)
        lo, hi = prof.span
        pv = point_values(f, g, 0.5 * (lo + hi)) if len(f) else None
        if pv is None:
            continue
        centre_rows.append([P(prof.label(), S["cell"]), f"{pv['freq_hz'] / 1e6:.3f} MHz", f"{pv['s11_db']:.1f} dB",
                            f"{pv['vswr']:.2f}" if pv["vswr"] != float("inf") else "∞", _z_text(pv["z"]),
                            _accepted(pv["s11_db"])])
    if len(centre_rows) > 1:
        story.append(Spacer(1, 4))
        story.append(P("At band centre", ParagraphStyle("sub", parent=S["body"], fontName=_fonts()[1])))
        story.append(_table(centre_rows, [58, 24, 18, 16, 32, 32]))

    # --- charts
    ms = [m] + list(compare or [])
    ids = a.target_ids or a.survey_ids
    traces: list = []
    _section(traces, "Measured traces", S)
    traces.append(_img(charts.s11_figure(ms, catalog, ids, thr), 175))
    story.append(KeepTogether(traces))
    panels = charts.band_panels(ms, catalog, ids, thr)
    if panels is not None:
        story.append(_img(panels, 175))
    story.append(_img(charts.s11_figure(ms, catalog, ids, kind="vswr"), 175))
    smith = _img(charts.smith_figure(ms), 95)
    smith.hAlign = "CENTER"
    story.append(smith)

    # --- key measurements
    kv = []
    if a.summary:
        sm = a.summary
        pv = point_values(f, g, sm.min_s11_freq_hz)
        kv.append(["Minimum S11", f"{sm.min_s11_db:.1f} dB at {sm.min_s11_freq_hz / 1e6:.3f} MHz"])
        if pv:
            z = pv["z"]
            kv.append(["Impedance at minimum", f"{z.real:.1f} {'+' if z.imag >= 0 else '−'} j{abs(z.imag):.1f} Ω, "
                                              f"VSWR {pv['vswr']:.2f}"])
        win = ", ".join(f"{a_ / 1e6:.1f}–{b_ / 1e6:.1f} MHz ({(b_ - a_) / 1e6:.1f} MHz)" for a_, b_ in sm.windows[:8])
        kv.append([f"Windows S11 ≤ {a.thresholds.pass_s11_db:.0f} dB", win or "none"])
        res = ", ".join(f"{fr / 1e6:.1f} MHz ({db:.1f} dB)" for fr, db in sm.resonances[:8])
        kv.append(["Resonances (minima)", res or "none distinct"])
        kv.append(["Data points", f"{sm.n_points}"])
    q = a.quality
    kv.append(["Noise (σ, linear)", _fmt(q.noise_sigma, ".4f")])
    kv.append(["Ripple (peak-to-peak)", _fmt(q.ripple_pp_db, ".2f", " dB")])
    if q.repeat_max_std_db is not None:
        kv.append(["Repeatability", f"max σ {q.repeat_max_std_db:.2f} dB, drift {q.repeat_drift_db:+.2f} dB"])
    if a.calibration_status == CalibrationStatus.NOT_VERIFIED:
        kv.append(["S11 uncertainty", "Calibration not confirmed – the data may be uncorrected; no uncertainty can be "
                                      "stated. Treat margins as unknown."])
    else:
        kv.append(["S11 uncertainty", "Indicative only, not a traceable figure: roughly ±0.5 dB at −10 dB and ±1 dB at "
                                      "−20 dB for an SOL calibration with ideal standards. AntennaFox does not compute a "
                                      "measurement uncertainty; a pass should have more margin than this."])
    _section(story, "Key values", S, _kv(kv[:4], S))
    if len(kv) > 4:
        story.append(_kv(kv[4:], S))
    if a.warnings:
        story.append(Spacer(1, 3))
        for w in a.warnings:
            story.append(P("⚠ " + w, S["small"]))

    # --- markers placed on the Graphs page
    if markers and len(f):
        mrows: list[list] = [[P(h, hdr) for h in ("Marker", "Frequency", "S11", "Return loss", "VSWR", "Impedance",
                                                  "Mismatch loss")]]
        for i, mf in enumerate(sorted(markers), start=1):
            pv = point_values(f, g, mf)
            if pv is None:
                continue
            ml = mismatch_loss_db(pv["s11_db"])
            mrows.append([f"M{i}", f"{pv['freq_hz'] / 1e6:.3f} MHz", f"{pv['s11_db']:.2f} dB", f"{-pv['s11_db']:.2f} dB",
                          f"{pv['vswr']:.3f}" if pv["vswr"] != float("inf") else "∞", _z_text(pv["z"]),
                          f"{ml:.2f} dB" if ml != float("inf") else "∞"])
        if len(mrows) > 1:
            _section(story, "Markers", S)
            story.append(_table(mrows, [16, 28, 22, 24, 18, 40, 32]))

    # --- comparison with the overlaid measurements
    if compare and a.target_ids:
        crow: list[list] = [[P(h, hdr) for h in ("Band", "Measurement", "Worst S11", "Δ vs. primary", "Resonance",
                                                 "Shift")]]
        members = [m, *compare]
        # a stale variant text (same for every measurement) says nothing; the environment then tells them apart
        variants = {x.antenna.variant for x in members}
        use_env = len(variants) <= 1

        def tag(x: Measurement) -> str:
            t = ENVIRONMENTS.get(x.antenna.environment, "").split(" (")[0] if use_env else x.antenna.variant
            return f" ({t})" if t and t != "not stated" else ""

        assessed = {id(x): (a if x is m else assess(x, catalog, list(a.target_ids), thresholds=a.thresholds))
                    for x in members}
        for pid in _region_representatives(catalog, a.target_ids)[:6]:
            prof = catalog.by_id(pid)
            base = a.band_results.get(pid)
            base_res = band_resonance(m, prof)
            for other in members:
                r = assessed[id(other)].band_results.get(pid)
                res = band_resonance(other, prof)
                if r is None:
                    continue
                d_worst = ("–" if other is m or base is None or r.worst_s11_db is None or base.worst_s11_db is None
                           else f"{round(r.worst_s11_db, 1) - round(base.worst_s11_db, 1):+.1f} dB")
                shift = ("–" if other is m or res is None or base_res is None
                         else f"{(res.freq_hz - base_res.freq_hz) / 1e6:+.1f} MHz")
                crow.append([P(short_label(prof), S["cell"]), P(other.name + tag(other), S["cell"]),
                             _fmt(r.worst_s11_db, ".1f", " dB"), d_worst,
                             f"{res.freq_hz / 1e6:.1f} MHz ({res.s11_db:.1f} dB)" if res else "–", shift])
        if len(crow) > 1:
            _section(story, "Comparison", S)
            story.append(_table(crow, [40, 42, 18, 22, 34, 24]))
            story.append(P("One row per frequency region (bands that share a region, such as ISM 2.4 GHz, Wi-Fi "
                           "2.4 GHz channels and Bluetooth, are represented by the widest band). Same thresholds for "
                           "every measurement. Δ = worst S11 of the compared measurement minus that of the primary "
                           "measurement, computed from the rounded values shown; negative Δ means the compared "
                           "measurement is better matched. Shift = its resonance minus the primary's. No overall "
                           "ranking is implied.", S["small"]))

    # --- calibration & setup
    c = m.calibration
    cal_rows = [
        ["Status", CAL_TEXT[a.calibration_status]],
        ["Instrument-reported calibration", c.gui_reported_type or "unknown"],
        ["Reference plane", REFERENCE_PLANES.get(c.reference_plane, c.reference_plane)],
        ["Calibration span", f"{c.cal_start_hz / 1e6:.1f}–{c.cal_stop_hz / 1e6:.1f} MHz"
         if c.cal_start_hz and c.cal_stop_hz else "unknown"],
        ["Calibration date / age", (c.cal_date or "unknown") +
         (f" ({c.age_days():.1f} days)" if c.age_days() is not None else "")],
        ["Calibration kit", c.cal_kit or "–"],
        ["Verification", c.verification or "not performed (50 Ω load check available on the Setup page)"],
    ]
    for issue in a.calibration_issues:
        cal_rows.append(["Note", issue])
    ant = m.antenna
    cal_rows += [
        ["Operator / project", f"{m.operator or '–'} / {m.project or '–'}"],
        ["DUT reference", ant.dut_reference or "–"],
        ["Environment", ENVIRONMENTS.get(ant.environment, "not stated")],
        ["Antenna", f"{ant.name or '–'} · type {ant.antenna_type or '–'} · connector {ant.connector or '–'}"],
        ["Cable / pigtail", ant.cable or "–"],
        ["Mounting / variant", f"{ant.mounting or '–'} / {ant.variant or '–'}"],
        ["Active GNSS antenna", "yes – no bias-tee power supplied, LNA not assessed" if ant.active_gnss else "no"],
    ]
    if m.sweep_plan:
        sp = m.sweep_plan
        segs = "; ".join(f"{s.label}: {s.start_hz / 1e6:.3f}–{s.stop_hz / 1e6:.3f} MHz, {s.points} pts"
                         for s in sp.segments)
        cal_rows += [["Sweep plan", f"{sp.preset_id}: {segs}"],
                     ["IFBW / averages / power", f"{sp.ifbw_hz:g} Hz / {sp.averages} / {sp.power_dbm:g} dBm"]]
        for o in sp.omissions:
            cal_rows.append(["Limitation", o])
    _section(story, "Calibration and test setup", S, _kv(cal_rows[:4], S))
    if len(cal_rows) > 4:
        story.append(_kv(cal_rows[4:], S))

    # --- gain
    _section(story, "Gain and radiation", S)
    if gain_res is None or gain_study is None:
        story.append(P(
            f"<b>Gain: {GAIN_NOT_MEASURED}. Radiation pattern: {GAIN_NOT_MEASURED}.</b> "
            "A good match (S11) is necessary but not sufficient for good radiation: an antenna that "
            "dissipates energy in losses can show an excellent S11.", S["body"]))
    else:
        rows = [
            ["Status", {GainStatus.ABSOLUTE: "Estimated directional realized gain",
                        GainStatus.RELATIVE_ONLY: "Relative comparison only (dBi not determinable)",
                        GainStatus.UNAVAILABLE: "Not available"}[gain_res.status]],
            ["Frequency", f"{gain_res.frequency_hz / 1e6:.3f} MHz"],
            ["Relative to reference", gain_res.relative_text()],
            ["Absolute", gain_res.absolute_text()],
            ["Reference antenna", f"{gain_study.reference.name or '–'} ({gain_study.reference.source or 'no source'}),"
                                f" {gain_study.reference.convention.value}"],
            ["Convention", gain_res.convention_note or "–"],
            ["Repeatability", gain_res.repeatability],
            ["S21 reference / DUT", f"{', '.join(f'{v:.2f}' for v in gain_res.s21_ref_db)} / "
                                        f"{', '.join(f'{v:.2f}' for v in gain_res.s21_dut_db)} dB"],
        ]
        if gain_res.far_field_min_m:
            rows.append(["Far field", f"distance {gain_study.setup.distance_m:.2f} m, minimum "
                                     f"{gain_res.far_field_min_m:.2f} m"])
        if gain_res.budget:
            b = gain_res.budget
            rows.append(["Uncertainty budget (k=1)",
                         f"reference {_fmt(b.reference_db, '.2f')} dB, repeatability {b.repeatability_db:.2f} dB"
                         f"{' (assumed)' if b.repeatability_estimated else ''}, cable {b.cable_db:.2f} dB, "
                         f"positioning {b.positioning_db:.2f} dB, environment {b.environment_db:.2f} dB"])
        for r in gain_res.reasons:
            rows.append(["No dBi because", r])
        for w in gain_res.warnings:
            rows.append(["Warning", w])
        story.append(_kv(rows, S))
        story.append(P("Valid only for the measured direction and polarization. Not peak gain, not "
                       "efficiency, not a complete 3D pattern.", S["small"]))
        for cut in gain_study.pattern_cuts:
            if not cut.points:
                continue
            ang, rel = cut.normalized()
            story.append(_img(charts.pattern_figure(
                ang, rel, f"{cut.plane} cut, {cut.frequency_hz / 1e6:.1f} MHz, {cut.polarization} (normalized)"), 80))

    # --- limitations & provenance
    _section(story, "Limitations", S)
    for t in [
        "S11 describes only the impedance match at the reference plane. It does not demonstrate gain, efficiency, "
        "polarization purity, range, throughput, or regulatory compliance.",
        "Results depend on cable routing, enclosure, ground plane, and surroundings; re-measure the antenna in the final device.",
        "Not a substitute for certification or conformity testing (e.g. RED, FCC).",
        f"Hardware validation status of this software version: {REPO_URL}/blob/main/docs/HARDWARE_VALIDATION.md",
    ]:
        story.append(P("• " + t, S["small"]))
    inst = m.instrument
    prov = [
        ["Backend", inst.backend + (" (SIMULATION)" if m.simulated else "")],
        ["Identification", inst.idn or "–"],
        [f"{inst.host_software or 'Host software'} / firmware / HW",
         f"{inst.gui_version or '–'} / {inst.firmware or '–'} / {inst.hardware_revision or '–'}"],
        ["Serial number", inst.serial or "–"],
        ["Instrument limits", f"{inst.limits.min_freq_hz / 1e6:.3f}–{inst.limits.max_freq_hz / 1e6:.1f} MHz ({inst.limits.source})"
         if inst.limits else "–"],
        ["Measurement ID / created", f"{m.id} / {m.created_at} ({_local(m.created_at)})"],
        ["Report generated", generated],
        ["Dataset", session_ref or "not saved"],
        ["Catalog", catalog.version],
        ["Software", f"AntennaFox {__version__}"],
    ]
    _section(story, "Instrument, software and dataset", S, _kv(prov[:3], S))
    if len(prov) > 3:
        story.append(_kv(prov[3:], S))

    def footer(canvas, page: int, total: int):
        reg, _ = _fonts()
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(15 * mm, 13.5 * mm, 195 * mm, 13.5 * mm)
        canvas.setFont(reg, 7)
        canvas.setFillColor(MUTED)
        footer_text = f"AntennaFox {__version__} · {m.name}"
        if m.simulated:
            footer_text = "SIMULATION – " + footer_text
        canvas.drawString(15 * mm, 9.5 * mm, footer_text[:90])
        canvas.drawRightString(195 * mm, 9.5 * mm, "Gain: " + ("see gain section" if gain_res else GAIN_NOT_MEASURED)
                               + f" · {m.report_id} · Page {page} of {total}")
        canvas.restoreState()

    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                            bottomMargin=16 * mm, title=f"AntennaFox – {m.name}", author="AntennaFox",
                            subject="Antenna test report")
    doc.build(story, canvasmaker=partial(_NumberedCanvas, footer=footer))
    return path

