# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventory the contents of a built AntennaFox bundle and the licenses that apply to them.

Walks the bundle, attributes every Python package directory to its installed distribution (license metadata from the
dist-info), lists the Qt frameworks, native libraries, fonts and license files it finds, and writes a Markdown report.
Exits non-zero when a component has no known license or when a GPL-only Qt module or an excluded library is present,
so the packaging workflow fails instead of shipping an unreviewed bundle.
"""

from __future__ import annotations

import argparse
import importlib.metadata as md
import sys
from collections import defaultdict
from pathlib import Path

GPL_ONLY_QT = {"QtLottie", "QtQuickTimeline", "QtQmlCompiler", "QtCharts", "QtDataVisualization", "QtVirtualKeyboard",
               "QtWaylandCompositor", "QtNetworkAuth", "QtMqtt", "QtCoap", "QtHttpServer", "QtGraphs", "QtQuick3D"}
MUST_NOT_SHIP = {"libreadline", "readline.cpython"}
# libraries that come with the interpreter or wheels and have no dist-info of their own
KNOWN_NATIVE = {
    "libpython": ("CPython", "PSF-2.0"),
    "libssl": ("OpenSSL 3", "Apache-2.0"),
    "libcrypto": ("OpenSSL 3", "Apache-2.0"),
    "libgfortran": ("GCC runtime (SciPy)", "GPL-3.0-or-later WITH GCC-exception-3.1"),
    "libgcc_s": ("GCC runtime (SciPy)", "GPL-3.0-or-later WITH GCC-exception-3.1"),
    "libquadmath": ("libquadmath (SciPy)", "LGPL-2.1-or-later"),
    "libopenblas": ("OpenBLAS (SciPy)", "BSD-3-Clause"),
    "libscipy_openblas": ("OpenBLAS (SciPy)", "BSD-3-Clause"),
    "libpyside6": ("PySide6", "LGPL-3.0-only"),
    "libshiboken6": ("Shiboken6", "LGPL-3.0-only"),
    "libz": ("zlib", "Zlib"), "libbz2": ("bzip2", "bzip2-1.0.6"), "liblzma": ("xz/liblzma", "0BSD"),
    "libffi": ("libffi", "MIT"), "libexpat": ("Expat", "MIT"), "libncurses": ("ncurses", "MIT-X11"),
    "libtinfo": ("ncurses", "MIT-X11"), "libsqlite3": ("SQLite", "blessing"), "libmpdec": ("mpdecimal", "BSD-2-Clause"),
    "libintl": ("gettext runtime", "LGPL-2.1-or-later"), "libuuid": ("util-linux uuid", "BSD-3-Clause"),
    # libraries vendored by the Pillow wheel (notices in Pillow's LICENSE file)
    "libavif": ("libavif (Pillow)", "BSD-2-Clause"), "libbrotli": ("Brotli (Pillow)", "MIT"),
    "libfreetype": ("FreeType (Pillow)", "FTL OR GPL-2.0-or-later"), "libharfbuzz": ("HarfBuzz (Pillow)", "MIT-old-style"),
    "libjpeg": ("libjpeg-turbo (Pillow)", "IJG AND BSD-3-Clause AND Zlib"), "liblcms2": ("Little-CMS (Pillow)", "MIT"),
    "libopenjp2": ("OpenJPEG (Pillow)", "BSD-2-Clause"), "libpng16": ("libpng (Pillow)", "libpng-2.0"),
    "libsharpyuv": ("libwebp (Pillow)", "BSD-3-Clause"), "libwebp": ("libwebp (Pillow)", "BSD-3-Clause"),
    "libtiff": ("libtiff (Pillow)", "libtiff"), "libXau": ("libXau (Pillow)", "MIT"), "libxcb": ("libxcb (Pillow)", "MIT"),
}
# top-level bundle entries that are not Python distributions
NOT_PACKAGES = {"AntennaFox", "base_library", "LICENSE", "LICENSES", "NOTICE", "QT_NOTICE", "THIRD_PARTY_NOTICES",
                "TRADEMARKS", "BUNDLE_INVENTORY", "Info", "PkgInfo", "lib-dynload", "antennafox"}


def dist_license(dist: md.Distribution) -> str:
    meta = dist.metadata
    expr = meta.get("License-Expression")
    if expr:
        return expr
    classifiers = [c.split("::")[-1].strip() for c in meta.get_all("Classifier", []) if c.startswith("License ::")]
    if classifiers:
        return " / ".join(classifiers)
    lic = (meta.get("License") or "").strip()
    return lic.splitlines()[0][:80] if lic else "UNKNOWN"


def top_level_map() -> dict[str, md.Distribution]:
    """Import name → distribution for every installed distribution."""
    out: dict[str, md.Distribution] = {}
    for dist in md.distributions():
        names = set()
        for f in dist.files or []:
            parts = f.parts
            if parts and not parts[0].endswith((".dist-info", ".data")):
                names.add(parts[0].split(".")[0])
        for n in names:
            out.setdefault(n, dist)
    return out


def inventory(app: Path) -> tuple[list[str], list[str]]:
    lines: list[str] = []
    problems: list[str] = []
    frameworks = app / "Contents" / "Frameworks"
    resources = app / "Contents" / "Resources"
    search = [d for d in (frameworks, resources) if d.is_dir()] or [app]
    tl = top_level_map()

    packages: dict[str, set[str]] = defaultdict(set)
    qt_frameworks: set[str] = set()
    natives: dict[str, Path] = {}
    fonts: list[Path] = []
    license_files: list[Path] = []
    for base in search:
        for p in base.rglob("*"):
            rel = p.relative_to(app)
            name = p.name
            if p.is_dir() and name.endswith(".framework"):
                qt_frameworks.add(name[:-len(".framework")])
                continue
            if not p.is_file():
                continue
            if ".framework/" in str(rel):
                continue
            top = rel.parts[2] if len(rel.parts) > 2 else (rel.parts[-1] if rel.parts else "")
            if top.endswith(".dist-info"):
                continue  # metadata of a distribution listed via its import name
            if len(rel.parts) == 3 and p.is_file() and not name.endswith((".dylib", ".so")):
                if rel.parts[1] == "Resources" and (name in ("LICENSE", "NOTICE") or name.endswith(".md")):
                    license_files.append(rel)
                continue  # a notice or license text at the bundle root, not a package
            if "LICENSES" in rel.parts and p.is_file():
                if rel.parts[1] == "Resources":
                    license_files.append(rel)
                continue
            if "lib-dynload" in rel.parts or top.startswith("python3"):
                packages["CPython standard library"].add(name)
                continue
            if name.endswith((".dylib", ".so")):
                natives[name] = rel
            elif name.lower().endswith((".ttf", ".otf", ".ttc")):
                fonts.append(rel)
            elif name.upper().startswith(("LICENSE", "COPYING", "NOTICE")) or ".dist-info" in str(rel) and "licenses" in str(rel):
                license_files.append(rel)
            packages[top.split(".")[0]].add(name)

    lines.append("# Bundle inventory\n")
    lines.append(f"Bundle: `{app.name}` · generated by `scripts/bundle_inventory.py`\n")
    lines.append("## Python distributions\n")
    lines.append("| Import name | Distribution | Version | License |\n|---|---|---|---|")
    for top in sorted(packages):
        if top in NOT_PACKAGES or top.endswith((".plist", ".icns", ".dist-info")) or top.startswith("Qt"):
            continue  # Qt* entries are framework symlinks, covered in the Qt section
        if top == "CPython standard library":
            lines.append("| stdlib | CPython | 3.x | PSF-2.0 |")
            continue
        dist = tl.get(top.split("-")[0])
        if dist is None:
            if top.startswith("lib"):
                continue
            problems.append(f"no distribution found for bundled package '{top}'")
            lines.append(f"| `{top}` | – | – | UNKNOWN |")
            continue
        lic = dist_license(dist)
        if lic == "UNKNOWN":
            problems.append(f"no license metadata for {dist.metadata['Name']}")
        lines.append(f"| `{top}` | {dist.metadata['Name']} | {dist.version} | {lic} |")

    lines.append("\n## Qt frameworks\n")
    for fw in sorted(qt_frameworks):
        flag = " **GPL-only – must not be shipped under the LGPL option**" if fw in GPL_ONLY_QT else ""
        lines.append(f"- {fw} (LGPL-3.0-only){flag}")
        if fw in GPL_ONLY_QT:
            problems.append(f"GPL-only Qt framework in bundle: {fw}")

    lines.append("\n## Native libraries outside Qt\n")
    lines.append("| Library | Attributed to | License |\n|---|---|---|")
    for name in sorted(natives):
        rel = natives[name]
        if any(bad in name for bad in MUST_NOT_SHIP):
            problems.append(f"excluded library present: {rel}")
        owner = next((v for k, v in KNOWN_NATIVE.items() if name.startswith(k)), None)
        if owner is None:
            pkg = rel.parts[2] if len(rel.parts) > 2 else ""
            dist = tl.get(pkg.split(".")[0])
            if dist is not None:
                owner = (dist.metadata["Name"], dist_license(dist))
        if owner is None and "/PySide6/" in str(rel):
            owner = ("PySide6", "LGPL-3.0-only")
        if owner is None:
            owner = ("?", "UNKNOWN")
            problems.append(f"native library with unknown origin: {rel}")
        lines.append(f"| `{rel}` | {owner[0]} | {owner[1]} |")

    lines.append("\n## Fonts\n")
    for f in sorted(fonts):
        lic = "Bitstream Vera / DejaVu (LICENSES/Bitstream-Vera.txt in the bundle)" if "DejaVu" in f.name else "UNKNOWN"
        if lic == "UNKNOWN":
            problems.append(f"font without a known license: {f}")
        lines.append(f"- `{f}` – {lic}")
    lines.append("\n## License files present\n")
    for f in sorted(license_files):
        lines.append(f"- `{f}`")
    return lines, problems


def main() -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("bundle", type=Path, help="path to AntennaFox.app (or an unpacked one-dir bundle)")
    ap.add_argument("--write", type=Path, help="write the Markdown report to this file")
    ap.add_argument("--allow-problems", action="store_true", help="report problems but exit 0")
    args = ap.parse_args()
    if not args.bundle.exists():
        print(f"not found: {args.bundle}", file=sys.stderr)
        return 2
    lines, problems = inventory(args.bundle)
    if problems:
        lines.append("\n## Problems\n")
        lines += [f"- {p}" for p in problems]
    text = "\n".join(lines) + "\n"
    if args.write:
        args.write.parent.mkdir(parents=True, exist_ok=True)
        args.write.write_text(text, encoding="utf-8")
        print(f"wrote {args.write}")
    else:
        print(text)
    for p in problems:
        print("PROBLEM:", p, file=sys.stderr)
    return 1 if problems and not args.allow_problems else 0


if __name__ == "__main__":
    sys.exit(main())
