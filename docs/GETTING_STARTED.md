# Getting started

## Requirements

- **Python 3.11 or newer** (3.12 recommended; CI tests 3.11 and 3.12, the development environment and the macOS
  bundle use 3.12). On Linux, Qt needs `libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1`. On 64-bit ARM Linux
  (Raspberry Pi and similar) use Python 3.12 or newer: the PySide6 wheels for Python 3.11 on that platform leak a
  reference on every signal emission and abort after a few hundred emissions. PySide6 6.12.0 is excluded on all
  platforms for a related upstream bug (PYSIDE-3477).
- **Operating system:** macOS (tested on Apple Silicon). AntennaFox is a pure Python/Qt application. Linux and
  Windows are expected to work and are part of the CI test matrix, but they have not been verified with hardware.
- **LibreVNA** with the official **LibreVNA-GUI** (verified with GUI and firmware 1.6.5 on V1 hardware). AntennaFox
  controls the GUI; it does not replace it.

## Install

With [uv](https://docs.astral.sh/uv/) (uses the locked dependency versions in `uv.lock`):

```bash
git clone https://github.com/AltinumTechnologies/antennafox.git
cd antennafox
uv sync
uv run antennafox
```

With pip:

```bash
git clone https://github.com/AltinumTechnologies/antennafox.git
cd antennafox
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install .
antennafox
```

A standalone macOS app bundle can be built from source, see [Packaging](PACKAGING.md).

## Prepare LibreVNA-GUI

1. Connect the LibreVNA via USB and start LibreVNA-GUI.
2. Enable the SCPI server: *Window → Preferences → General → SCPI Server* (default port **19542**).
3. For accurate values, calibrate port 1 in LibreVNA-GUI, see [Calibration](CALIBRATION.md).

LibreVNA-GUI accepts one SCPI client at a time. Close other scripts that use the SCPI port before connecting
AntennaFox.

## First measurement

AntennaFox opens on the **Test** page.

1. **Connect.** Click *Connect LibreVNA*. AntennaFox connects to LibreVNA-GUI on `127.0.0.1:19542`. If the GUI is
   running but has not connected to the device yet, AntennaFox asks it to connect to the first device it finds.
   The status pill shows the serial number when this succeeds.
2. **Choose the use case.** Select what the antenna is for: *Wi-Fi / Bluetooth* (2.4 GHz), *Dual-band Wi-Fi*, *5 GHz
   Wi-Fi*, *LoRa 868 MHz*, *Sub-GHz EU* (433 + 868 MHz),
   433 MHz, GPS/GNSS, or *Unknown antenna* to scan the whole range. Before the test, the plot already shades the
   band where the notch should appear.
3. **Start.** Attach only the passive antenna to port 1, tick *Only the antenna is connected – no radio, no power supply* and click
   *Start test*. Optionally enter an antenna name first.
4. **Read the result.**
   - The S11 plot shades each target band in its verdict color (green, amber, red).
   - The deepest notch near each band is marked with its frequency and depth. An orange arrow points toward the
     band when the notch lies outside it.
   - The result panel summarizes the match in plain words, shows the share of each band that is well matched, and
     gives a tuning direction where one applies.
5. **Keep the result.** *Save* writes an `.afox` session (raw complex data with provenance). *PDF report* creates
   a report. *Details* opens the Graphs page with S11, VSWR, Smith chart and R/X views and markers.

To try the workflow without hardware, click *Try demo*. The simulated antenna can be changed on the **Setup** page;
simulated data is labeled as such everywhere.

## Pages

| Page | Purpose |
|---|---|
| Test | Guided quick test with visual verdict |
| Graphs | S11/return loss, VSWR, Smith chart, R/X and S21 with markers and comparison overlays |
| Bands | Full band table: coverage, worst/median S11, maximum VSWR, matched fraction, sample density |
| Compare | Several measurements side by side, per band |
| Gain & pattern | Guided comparative S21 measurement against a characterized reference antenna |
| Setup | Connection details, calibration metadata, antenna details, sweep parameters, custom ranges |

## Your own bands and presets

- **Bands:** *Bands → Add user band…* adds a frequency range (for example a cellular band or a proprietary ISM
  channel) to the catalog. User bands appear in every band picker and are evaluated like built-in ones.
- **Presets:** on the *Setup* page, configure a range (or pick *User-defined sweep*) and target bands, then click
  *Save as preset…*. The preset appears in the preset list and, if you tick *Show as a tile on the Test page*, on the Test page next
  to the built-in use cases. *Delete preset* removes it again.
- **Tiles:** *Choose tiles…* on the Test page picks what appears as tiles (up to eight): the built-in sweep
  presets, any single band from the catalog (AntennaFox builds a context + detail sweep around it), your own bands
  and your saved presets. The page always shows these tiles on start.

Both are stored as JSON in the application data directory (`user_catalog.json`, `user_presets.json`) and survive
updates. Built-in entries cannot be changed. A user preset whose id clashes with a built-in one is skipped; a user
band whose id clashes with a built-in band makes AntennaFox ignore the whole `user_catalog.json` (a warning is
logged).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| "Cannot connect to 127.0.0.1:19542" | LibreVNA-GUI is not running, or its SCPI server is disabled or uses another port. Check *Preferences → General*; change the port in *Setup*. |
| "LibreVNA-GUI is running but no LibreVNA device is attached" | The GUI has no device. Check the USB cable, then click *Connect LibreVNA* again. |
| "LibreVNA-GUI closed the connection" | Another SCPI client connected to the GUI. Close it and reconnect. |
| "LibreVNA-GUI set … instead of …" | The GUI changed a requested setting (instrument limit). The measurement is aborted rather than stored with wrong settings. |
| Values look implausible (S11 above 0 dB) | Missing or invalid calibration, or the reference plane differs from where the antenna is attached. See [Calibration](CALIBRATION.md). |
| "Not enough data" for a band | The band was not fully covered by the sweep, or sample density was too low. Use the matching use case or a custom range in *Setup*. |

### Reports

*PDF report* first asks for the report details – operator, project, DUT reference, variant, environment and notes.
They are stored in the session and printed in the report (operator, project and DUT reference in the header);
operator and project are remembered as defaults (the operator name also lives in *Analysis settings…*, the ⚙
toolbar button, Ctrl+,). The acceptance criterion printed in the report comes from the same dialog's thresholds. The report carries a report ID (`AFX-<date>-<id>`), the scope and acceptance criterion, values at each
band centre, zoomed panels per band, the markers you placed on the Graphs page, a comparison table when overlays
are loaded, and the calibration verification from the 50 Ω load check if you ran one.

### Diagnostics

AntennaFox writes a log file to its data directory (shown in *Help → About AntennaFox*; typically
`~/Library/Application Support/AltinumTechnologies/AntennaFox` on macOS, `~/.local/share/AltinumTechnologies/AntennaFox`
on Linux, `%APPDATA%\AltinumTechnologies\AntennaFox` on Windows). *Help → Save diagnostics…* writes a
text file with the environment, the instrument state and the recent log, including every SCPI line exchanged with
LibreVNA-GUI (long responses such as trace data are truncated to 160 characters); attach it to bug reports. Start with `antennafox --debug` to keep SCPI traffic in the log file
permanently, or `--verbose` to also print the log to the terminal. The log contains the serial number of your
LibreVNA and the host/port you connected to; trace data appears only as truncated SCPI responses.
