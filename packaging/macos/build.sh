#!/usr/bin/env bash
# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
# Build dist/AntennaFox.app with PyInstaller on a uv-managed CPython (see docs/PACKAGING.md).
set -euo pipefail
cd "$(dirname "$0")/../.."
PY="${ANTENNAFOX_BUILD_PYTHON:-3.12}"
export UV_PYTHON_PREFERENCE=only-managed  # never a conda/Homebrew interpreter: their native libraries would end up in the bundle
uv python install "$PY"
uv sync --extra package --python "$PY"
uv run --python "$PY" python -c "import sys; print('build interpreter:', sys.version)"
rm -rf build dist
mkdir -p build
uv run --python "$PY" python scripts/make_icons.py --iconset build/antennafox.iconset
iconutil -c icns build/antennafox.iconset -o build/antennafox.icns
uv run --python "$PY" pyinstaller --noconfirm packaging/macos/antennafox.spec
uv run --python "$PY" python scripts/bundle_inventory.py dist/AntennaFox.app --write dist/AntennaFox.app/Contents/Resources/BUNDLE_INVENTORY.md
# the inventory file is added after PyInstaller signed the bundle; re-seal or Gatekeeper reports the app as damaged
codesign --force --deep --sign - dist/AntennaFox.app
codesign --verify --deep --strict dist/AntennaFox.app
echo "Built dist/AntennaFox.app"
