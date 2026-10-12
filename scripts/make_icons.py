# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Derive the application icons from the square master in assets/branding.

Writes the rounded window/taskbar icon used by the app and a macOS-style icon canvas (icon on a transparent
margin) that packaging/macos/build.sh converts to .icns.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "branding" / "antennafox-icon-square.png"
APP_ICON = ROOT / "src" / "antennafox" / "assets" / "antennafox-icon.png"
MAC_ICON = ROOT / "packaging" / "macos" / "antennafox-icon-1024.png"
CORNER = 0.2237  # macOS icon corner radius relative to the icon edge


def rounded(img: Image.Image, size: int) -> Image.Image:
    img = img.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size * 4 - 1, size * 4 - 1), radius=int(CORNER * size * 4), fill=255)
    img.putalpha(mask.resize((size, size), Image.Resampling.LANCZOS))
    return img


def mac_canvas(src: Image.Image, size: int) -> Image.Image:
    """macOS icon: rounded square on a transparent margin (icon = 80.5 % of the canvas)."""
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    inner_size = round(size * 824 / 1024)
    inner = rounded(src, inner_size)
    canvas.paste(inner, ((size - inner_size) // 2, (size - inner_size) // 2), inner)
    return canvas


def write_iconset(src: Image.Image, folder: Path) -> None:
    """All sizes iconutil expects for a .icns file."""
    folder.mkdir(parents=True, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        mac_canvas(src, base).save(folder / f"icon_{base}x{base}.png")
        mac_canvas(src, base * 2).save(folder / f"icon_{base}x{base}@2x.png")


def main() -> None:
    ap = argparse.ArgumentParser(description="Derive the application icons from the square master.")
    ap.add_argument("--iconset", type=Path, help="also write a macOS .iconset folder for iconutil")
    args = ap.parse_args()
    src = Image.open(SOURCE)
    rounded(src, 512).save(APP_ICON)
    mac_canvas(src, 1024).save(MAC_ICON)
    print(f"wrote {APP_ICON.relative_to(ROOT)} and {MAC_ICON.relative_to(ROOT)}")
    if args.iconset:
        write_iconset(src, args.iconset)
        print(f"wrote {args.iconset}")


if __name__ == "__main__":
    main()
