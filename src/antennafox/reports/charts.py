# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Static chart rendering (matplotlib, headless) for reports and PNG/SVG export."""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from ..catalog.catalog import Catalog  # noqa: E402
from ..measurement.model import Measurement  # noqa: E402
from ..rf.rfmath import s11_db, vswr  # noqa: E402
from ..rf.smith import grid_lines, vswr_circle  # noqa: E402

PALETTE = ["#0e8fb3", "#e0594f", "#3fa06f", "#8b5cf6", "#f57c1f", "#64748b"]
BAND_COLOR = "#f57c1f"
INK = "#0f172a"
MUTED = "#64748b"
GRID = "#e2e8f0"


def _paper(ax):
    ax.set_facecolor("white")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=7, length=3)
    ax.xaxis.label.set_color(MUTED)
    ax.yaxis.label.set_color(MUTED)
    ax.grid(True, color=GRID, lw=0.6)
    ax.set_axisbelow(True)


def _segments_with_gaps(m: Measurement, param: str = "S11"):
    for s in m.segments(param):
        yield s.freq_hz, s.data, s.label


def regions(catalog: Catalog | None, target_ids: list[str]) -> list[tuple[float, float, str]]:
    """Contiguous frequency regions covered by the target bands, each named after its widest band."""
    spans = []
    for pid in target_ids:
        p = catalog.get(pid) if catalog else None
        if p:
            spans += [(a, b, p.label().split(" (")[0]) for a, b in p.intervals_hz]
    out: list[tuple[float, float, str, float]] = []
    for a, b, name in sorted(spans):
        if out and a <= out[-1][1]:
            lo, hi, wname, w = out[-1]
            out[-1] = (lo, max(hi, b), name if b - a > w else wname, max(w, b - a))
        else:
            out.append((a, b, name, b - a))
    return [(lo, hi, name) for lo, hi, name, _ in out]


def _band_spans(ax, catalog: Catalog | None, target_ids: list[str], fmin: float, fmax: float, labels: bool = False):
    if not catalog:
        return
    for pid in target_ids:
        p = catalog.get(pid)
        if not p:
            continue
        for a, b in p.intervals_hz:
            if b < fmin or a > fmax:
                continue
            ax.axvspan(a / 1e6, b / 1e6, color=BAND_COLOR, alpha=0.10, lw=0)
    if not labels:
        return
    for lo, hi, name in regions(catalog, target_ids):
        if hi < fmin or lo > fmax:
            continue
        ax.text((lo + hi) / 2e6, 0.98, name, transform=ax.get_xaxis_transform(), ha="center", va="top",
                fontsize=6, color="#c2410c", clip_on=True)


def band_panels(measurements: list[Measurement], catalog: Catalog | None, target_ids: list[str],
                thr_db: float = -10.0, max_panels: int = 4):
    """One zoomed S11 panel per target region, so narrow bands are readable on a wide sweep."""
    regs = regions(catalog, target_ids)[:max_panels]
    if not regs or not measurements:
        return None
    fig, axes = plt.subplots(1, len(regs), figsize=(7.2, 2.4), dpi=150, squeeze=False)
    for ax, (lo, hi, name) in zip(axes[0], regs, strict=True):
        _paper(ax)
        pad = max(0.25 * (hi - lo), 0.03 * (lo + hi) / 2)
        fmin, fmax = lo - pad, hi + pad
        shown = False
        for i, m in enumerate(measurements):
            for f, d, _ in _segments_with_gaps(m):
                sel = (f >= fmin) & (f <= fmax)
                if sel.sum() < 2:
                    continue
                shown = True
                ax.plot(f[sel] / 1e6, s11_db(d[sel]), color=PALETTE[i % len(PALETTE)], lw=1.2 if i == 0 else 0.9)
        ax.axvspan(lo / 1e6, hi / 1e6, color=BAND_COLOR, alpha=0.10, lw=0)
        ax.axhline(thr_db, color=MUTED, ls="--", lw=0.8)
        ax.set_xlim(fmin / 1e6, fmax / 1e6)
        ax.set_ylim(top=2)
        ax.set_title(name, fontsize=7.5, color=INK, loc="left")
        ax.set_xlabel("MHz")
        if ax is axes[0][0]:
            ax.set_ylabel("S11 [dB]")
        if shown:
            _annotate_dips(ax, measurements[0], catalog, target_ids, thr_db, PALETTE[0])
        else:
            ax.text(0.5, 0.5, "not measured", transform=ax.transAxes, ha="center", va="center", fontsize=7, color=MUTED)
    fig.tight_layout()
    return fig


def _annotate_dips(ax, m: Measurement, catalog: Catalog | None, target_ids: list[str], thr_db: float, color: str):
    """Mark the deepest point inside each target region, like the Test page does."""
    f, g = m.merged("S11")
    if len(f) == 0:
        return
    db = s11_db(g)
    done: list[float] = []
    for pid in target_ids:
        p = catalog.get(pid) if catalog else None
        if not p:
            continue
        a, b = p.span
        fc = 0.5 * (a + b)
        sel = (f >= fc * 0.65) & (f <= fc * 1.35)
        if sel.sum() < 3:
            continue
        i = int(np.argmin(db[sel]))
        fr, v = float(f[sel][i]), float(db[sel][i])
        xmin, xmax = ax.get_xlim()
        if v > thr_db or any(abs(fr - x) < 0.01 * fr for x in done) or not (xmin <= fr / 1e6 <= xmax):
            continue
        done.append(fr)
        ax.plot([fr / 1e6], [v], "o", ms=3.5, color=color, mec="white", mew=0.6, zorder=5)
        ax.annotate(f"{fr / 1e6:.0f} MHz · {v:.1f} dB", (fr / 1e6, v), xytext=(7, 6), textcoords="offset points",
                    fontsize=6.5, color=INK, va="bottom", ha="left")


def s11_figure(measurements: list[Measurement], catalog: Catalog | None = None, target_ids: list[str] | None = None,
               thr_db: float = -10.0, fmin: float | None = None, fmax: float | None = None, kind: str = "s11",
               title: str = "", sim_label: bool = True):
    fig, ax = plt.subplots(figsize=(7.2, 3.2), dpi=150)
    _paper(ax)
    lo, hi = np.inf, -np.inf
    for i, m in enumerate(measurements):
        color = PALETTE[i % len(PALETTE)]
        first = True
        for f, d, _ in _segments_with_gaps(m):
            y = s11_db(d) if kind == "s11" else np.clip(vswr(d), 1, 20)
            ax.plot(f / 1e6, y, color=color, lw=1.2 if i == 0 else 0.9,
                    label=m.name + (" (SIM)" if m.simulated else "") if first else None)
            first = False
            lo, hi = min(lo, f[0]), max(hi, f[-1])
    if not np.isfinite(lo):
        lo, hi = 1e8, 6e9
    fmin = fmin or lo
    fmax = fmax or hi
    _band_spans(ax, catalog, target_ids or [], fmin, fmax, labels=kind == "s11")
    if kind == "s11":
        ax.axhline(thr_db, color=MUTED, ls="--", lw=0.8)
        ax.text(fmax / 1e6, thr_db, f" {thr_db:.0f} dB", fontsize=6, color=MUTED, va="bottom", ha="right")
        ax.set_ylabel("S11 [dB]")
        ax.set_ylim(top=2)
        if measurements:
            _annotate_dips(ax, measurements[0], catalog, target_ids or [], thr_db, PALETTE[0])
    else:
        ax.axhline(1.92, color=MUTED, ls="--", lw=0.8)
        ax.set_ylabel("VSWR")
        ax.set_ylim(1, 10)
    ax.set_xlim(fmin / 1e6, fmax / 1e6)
    ax.set_xlabel("Frequency [MHz]")
    if title:
        ax.set_title(title, fontsize=8, color=INK, loc="left")
    if len(measurements) > 1:
        ax.legend(fontsize=6.5, loc="lower right", frameon=False)
    if sim_label and any(m.simulated for m in measurements):
        ax.text(0.5, 0.5, "SIMULATION", transform=ax.transAxes, fontsize=34, color="#dc2626", alpha=0.15,
                ha="center", va="center", rotation=15)
    fig.tight_layout()
    return fig


def smith_figure(measurements: list[Measurement], sim_label: bool = True):
    fig, ax = plt.subplots(figsize=(3.6, 3.6), dpi=150)
    for x, y, kind in grid_lines():
        ax.plot(x, y, color="#cbd5e1" if kind != "unit" else "#475569", lw=0.5 if kind != "unit" else 1.0)
    cx, cy = vswr_circle(1.92)
    ax.plot(cx, cy, color=BAND_COLOR, ls="--", lw=0.8, alpha=0.8)
    for i, m in enumerate(measurements):
        for _, d, _ in _segments_with_gaps(m):
            ax.plot(np.real(d), np.imag(d), color=PALETTE[i % len(PALETTE)], lw=1.1)
    ax.plot([1], [0], "o", ms=2.5, color="#475569")
    ax.set_aspect("equal")
    ax.set_xlim(-1.05, 1.05)
    ax.set_ylim(-1.05, 1.05)
    ax.axis("off")
    ax.set_title("Smith chart (Z0 = 50 Ω) · dashed: VSWR 1.92", fontsize=7, color=MUTED, loc="left")
    if sim_label and any(m.simulated for m in measurements):
        ax.text(0, 0, "SIM", fontsize=30, color="#dc2626", alpha=0.15, ha="center", va="center")
    fig.tight_layout()
    return fig


def pattern_figure(angles_deg: np.ndarray, rel_db: np.ndarray, title: str):
    fig = plt.figure(figsize=(3.6, 3.6), dpi=150)
    ax = fig.add_subplot(111, projection="polar")
    a = np.deg2rad(np.append(angles_deg, angles_deg[:1]))
    v = np.append(rel_db, rel_db[:1])
    ax.plot(a, np.maximum(v, -40), color=PALETTE[0], marker="o", ms=2)
    ax.set_ylim(-40, 0)
    ax.set_title(title, fontsize=8)
    fig.tight_layout()
    return fig


def fig_bytes(fig, fmt: str = "png") -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt)
    plt.close(fig)
    return buf.getvalue()
