# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
# PyInstaller spec for the macOS app bundle. Build via packaging/macos/build.sh.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

from antennafox import __version__

here = Path(SPECPATH)
root = here.parents[1]
datas = collect_data_files("antennafox", includes=["assets/*.png", "catalog/data/*.json"])
datas += collect_data_files("matplotlib", includes=["mpl-data/fonts/ttf/DejaVuSans*.ttf", "mpl-data/matplotlibrc"])
datas += [(str(root / "LICENSE"), "."), (str(root / "NOTICE"), "."), (str(root / "THIRD_PARTY_NOTICES.md"), "."), (str(root / "TRADEMARKS.md"), "."),
          (str(root / "QT_NOTICE.md"), "."), (str(root / "LICENSES" / "GPL-3.0-or-later.txt"), "LICENSES"),
          (str(root / "packaging" / "licenses"), "LICENSES")]

a = Analysis(
    [str(here / "launcher.py")],
    pathex=[str(root / "src")],
    datas=datas,
    # QtQuick/QtQml keep the GPL-only Qt modules (Lottie, QuickTimeline, QmlCompiler) out of the bundle;
    # readline is a GPL library swept in from some interpreters and never used by AntennaFox.
    excludes=["tkinter", "readline", "setuptools", "PySide6.QtWebEngineCore", "PySide6.QtQuick", "PySide6.QtQml"],
)


def _keep(entry):
    """Drop data the app never uses: non-DejaVu Matplotlib fonts and the CC-BY colour-map CSVs of pyqtgraph."""
    dest = entry[0].replace("\\", "/")
    if "mpl-data/fonts/" in dest and not dest.endswith(("DejaVuSans.ttf", "DejaVuSans-Bold.ttf")):
        return False
    if "pyqtgraph/colors/maps/" in dest:
        return False
    return True


a.datas = [d for d in a.datas if _keep(d)]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="AntennaFox", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="AntennaFox")  # one-dir: Qt stays replaceable (LGPLv3 §4(d)(1))
app = BUNDLE(
    coll,
    name="AntennaFox.app",
    icon=str(root / "build" / "antennafox.icns"),
    bundle_identifier="com.altinumtechnologies.antennafox",
    info_plist={
        "CFBundleShortVersionString": __version__,
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": "© 2026 Altinum Technologies e.U. – GNU GPL v3 or later",
    },
)
