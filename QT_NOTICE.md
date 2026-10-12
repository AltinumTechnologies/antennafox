# Qt and Qt for Python (PySide6) – license notice

AntennaFox uses Qt 6 and Qt for Python (PySide6 and Shiboken6), version 6.11.2 as locked in `uv.lock`
(6.12.0 is excluded: its bindings leak a reference per signal emission on Python 3.11, upstream PYSIDE-3477).

Copyright (C) The Qt Company Ltd. and other contributors.
Contact: https://www.qt.io/licensing/

The Qt libraries shipped with AntennaFox are used under the terms of the **GNU Lesser General Public License,
version 3** (`packaging/licenses/LGPL-3.0-only.txt` in the repository, `LICENSES/LGPL-3.0-only.txt` in a binary bundle; the LGPL is a set of additional permissions on top of the GNU General
Public License version 3, `LICENSES/GPL-3.0-or-later.txt`). Qt is also available under commercial licenses and under the GPL;
AntennaFox exercises the LGPL option.

## Modules

Only Qt Essentials modules are used and shipped: QtCore, QtGui, QtWidgets, QtNetwork, QtDBus, QtOpenGL,
QtOpenGLWidgets, QtSvg and QtTest (the last six are pulled in by QtGui and pyqtgraph). All of them are licensed under
the LGPL-3.0. No GPL-only Qt module (Qt Quick Timeline, Qt Lottie, Qt Qml Compiler, …) is distributed with
AntennaFox; the packaging specification excludes the Qt Quick/QML stack explicitly and the per-build inventory
(`BUNDLE_INVENTORY.md`) lists every Qt framework actually contained in a binary bundle.

## Replacing the Qt libraries (LGPLv3 section 4(d))

Binary bundles of AntennaFox keep Qt as separate dynamic libraries. In the macOS application bundle they are the
frameworks in `AntennaFox.app/Contents/Frameworks/PySide6/Qt/lib/` and the Python bindings next to them. You may
replace them with your own, interface-compatible build of Qt 6.11 and of PySide6/Shiboken6 6.11; the application
is not statically linked against Qt. After replacing files, re-apply an ad-hoc code signature to the whole bundle
(`codesign --force --deep --sign - AntennaFox.app`; an arm64 binary must carry at least an ad-hoc signature to
launch). The procedure is
described in `docs/PACKAGING.md`.

## Source code of Qt and PySide6

The complete corresponding source code of the shipped versions is published by The Qt Company:

- Qt 6.11.2: https://download.qt.io/official_releases/qt/6.11/6.11.2/single/
- PySide6 / Shiboken6 6.11.2 (pyside-setup): https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/
- Git: https://code.qt.io/cgit/qt/qt5.git/ (tag v6.11.2) and https://code.qt.io/cgit/pyside/pyside-setup.git/

Written offer: Altinum Technologies e.U. will provide a copy of the complete corresponding source code of the Qt,
PySide6 and Shiboken6 libraries contained in any AntennaFox binary release, on request, for at least three years
after that release was made available, for no more than the cost of physically performing the distribution.
Requests: https://altinum.tech or the AntennaFox issue tracker at https://github.com/AltinumTechnologies/antennafox.

## Third-party code inside Qt

The Qt libraries contain code that is not the intellectual property of The Qt Company (FreeType, HarfBuzz, PCRE2,
libpng, libjpeg-turbo, zlib, Unicode data, the Public Suffix List and others). Their licenses and attributions are
listed at https://doc.qt.io/qt-6/licenses-used-in-qt.html and in the SPDX SBOM files that accompany the Qt source
releases (https://download.qt.io/official_releases/qt/6.11/6.11.2/). Those notices apply to the Qt libraries as shipped
and are reproduced here by reference.
