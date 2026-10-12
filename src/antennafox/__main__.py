# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""AntennaFox entry point: `python -m antennafox` or the `antennafox` console script."""

from __future__ import annotations

import argparse
import logging
import sys


def _warn_about_known_bad_platform() -> None:
    """PySide6's aarch64 Linux wheels for Python 3.11 leak a reference per signal emission and abort after a
    few hundred emissions (seen with 6.9–6.12); the same wheels are fine on Python 3.12+ and on other platforms."""
    import platform

    if sys.platform.startswith("linux") and platform.machine() in ("aarch64", "arm64") and sys.version_info < (3, 12):
        print("AntennaFox: Python 3.11 on 64-bit ARM Linux is known to crash with PySide6 after a short while; "
              "please use Python 3.12 or newer (see docs/GETTING_STARTED.md).", file=sys.stderr)


def main() -> int:
    from PySide6.QtCore import QLocale
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME, diagnostics
    from .ui import theme
    from .ui.main_window import MainWindow, app_icon
    from .ui.state import data_dir

    ap = argparse.ArgumentParser(prog="antennafox", description="Antenna Analysis GUI for Vector Network Analyzers")
    ap.add_argument("--verbose", action="store_true", help="also print the log to stderr")
    ap.add_argument("--debug", action="store_true", help="write SCPI traffic to the log file")
    args, qt_args = ap.parse_known_args()

    _warn_about_known_bad_platform()
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName("AltinumTechnologies")
    QLocale.setDefault(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))
    diagnostics.setup(data_dir(), level=logging.DEBUG if args.debug else logging.INFO, console=args.verbose)
    app = QApplication([sys.argv[0], *qt_args])
    theme.apply(app)
    app.setWindowIcon(app_icon())
    w = MainWindow()
    w.show()
    rc = app.exec()
    w.deleteLater()  # tear the window down while the QApplication still exists
    app.processEvents()
    return rc


if __name__ == "__main__":
    sys.exit(main())
