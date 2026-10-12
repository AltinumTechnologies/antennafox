# Packaging a macOS app

AntennaFox runs directly from source with `uv run antennafox`. To build a standalone `AntennaFox.app`:

```bash
./packaging/macos/build.sh
open dist/AntennaFox.app
```

The script installs a uv-managed CPython (3.12 by default; override with `ANTENNAFOX_BUILD_PYTHON`), syncs the
locked dependencies with the `package` extra into it, generates `build/antennafox.icns` from the icon source
(`scripts/make_icons.py` + `iconutil`), runs PyInstaller with `packaging/macos/antennafox.spec`, and finally runs
`scripts/bundle_inventory.py`, which writes `Contents/Resources/BUNDLE_INVENTORY.md` into the bundle
and **fails the build** if it finds a component without a known license, a GPL-only Qt module or GNU Readline.
The script sets `UV_PYTHON_PREFERENCE=only-managed`, so uv never picks a conda or Homebrew Python: those drag their
own native libraries (readline, ncurses, …) into the bundle and change the inventory. Run the inventory script from
the same uv environment the bundle was built with (as the script does): it attributes packages through the
running interpreter's package metadata.

The result is built for the architecture of the build machine (arm64 on Apple Silicon, x86_64 on Intel). The bundle
carries only an ad-hoc signature (no Developer ID) and is not notarized. PyInstaller signs the bundle, the script
then adds `BUNDLE_INVENTORY.md` and re-signs with `codesign --force --deep --sign -` so the seal stays valid –
`codesign --verify --deep --strict` must pass, otherwise macOS reports a downloaded copy as damaged. For distribution, sign it with a
Developer ID (`codesign --deep --options runtime`) and notarize it with `xcrun notarytool`. Ad-hoc-signed builds must
be opened via right-click → *Open* the first time.

## Licensing of the bundle

AntennaFox is GPL-3.0-or-later. The bundle must therefore be accompanied by its complete corresponding source
(GPLv3 §6): publish the bundle only together with the source archive or tag it was built from, and keep that
release available. The bundle contains `LICENSE`, `NOTICE`, `THIRD_PARTY_NOTICES.md`, `TRADEMARKS.md`, `QT_NOTICE.md`, the `LICENSES/`
directory (GPLv3, LGPLv3, DejaVu) and `BUNDLE_INVENTORY.md`; do not remove them.

**Qt is used under the LGPL version 3.** The obligations and how the bundle meets them:

| LGPLv3 | How the bundle complies |
|---|---|
| §4(a)/(b) notices and license texts | About dialog names Qt and the LGPL; `LGPL-3.0-only.txt` (from `packaging/licenses/`) and `GPL-3.0-or-later.txt` are in `Contents/Resources/LICENSES/`. |
| §4(d)(1) replaceable library | One-directory layout (`COLLECT` + `BUNDLE` in the spec). Qt lives as separate frameworks in `Contents/Frameworks/PySide6/Qt/lib/*.framework`, the Python bindings in `Contents/Frameworks/PySide6/Qt*.abi3.so`, `libpyside6*` and `libshiboken6*`. Nothing is statically linked. **Never switch to `--onefile`.** |
| §4(e) installation information | To replace Qt: build an ABI-compatible Qt 6.11 and PySide6/Shiboken6 6.11 (sources in `QT_NOTICE.md`), copy the frameworks and bindings over the ones in the bundle, then re-apply an ad-hoc signature to the whole bundle: `codesign --force --deep --sign - AntennaFox.app` (an arm64 binary must carry at least an ad-hoc signature to launch; do not merely remove the signature). Gatekeeper will treat the modified bundle as unsigned. |
| Source availability | Exact versions, download locations and a written offer are in `QT_NOTICE.md`. |

Distribution channels whose terms forbid users from replacing libraries (for example the Mac App Store) are not
compatible with the LGPL option and must not be used for this bundle.

The spec excludes `tkinter`, `readline` and `setuptools`, prunes Matplotlib's fonts to `DejaVuSans.ttf` and
`DejaVuSans-Bold.ttf` (the only ones the PDF report embeds – hence `Bitstream-Vera.txt` in `LICENSES/`) and drops
pyqtgraph's CC-BY colour-map data, which AntennaFox never uses. `Info.plist` carries
`NSHumanReadableCopyright` "© 2026 Altinum Technologies e.U. – GNU GPL v3 or later" and the version from
`antennafox.__version__`. The spec keeps `PySide6.QtQuick`, `PySide6.QtQml` and `PySide6.QtWebEngineCore` excluded; that exclusion is what
keeps the GPL-only Qt modules shipped inside the PySide6 wheel (Qt Lottie, Qt Quick Timeline, Qt Qml Compiler) out
of the bundle. The inventory script lists every `Qt*.framework` in the bundle and fails on a GPL-only one.

## Continuous integration

`.github/workflows/package.yml` runs on every `v*` tag and on manual dispatch, on `macos-latest` (Apple Silicon, so
the published artifact is arm64 only). It runs `build.sh`, re-runs `scripts/bundle_inventory.py`, asserts that
`LICENSES/LGPL-3.0-only.txt`, `LICENSES/GPL-3.0-or-later.txt` and `QT_NOTICE.md` are inside the bundle, starts the
app for a few seconds, and uploads the bundle as the artifact `AntennaFox-macOS`.

## Checking a build

```bash
uv run python scripts/bundle_inventory.py dist/AntennaFox.app          # prints the inventory, exit 1 on problems
ls dist/AntennaFox.app/Contents/Frameworks/PySide6/Qt/lib               # Qt frameworks actually shipped
ls dist/AntennaFox.app/Contents/Resources/LICENSES                      # license texts present
open dist/AntennaFox.app && sleep 5 && pgrep -f AntennaFox.app          # it starts
```
