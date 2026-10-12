# Changelog

All notable changes to AntennaFox are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [Semantic Versioning](https://semver.org/).

## [1.0.0] – 2026-10-12

First public release.

### Added
- Guided antenna test: pick what the antenna is for (Wi-Fi/Bluetooth 2.4 GHz, dual-band Wi-Fi, 5 GHz Wi-Fi, LoRa 868,
  433 MHz, Sub-GHz EU, GNSS L1, unknown antenna, any of the 25 catalog bands or your own presets) and get a
  plain-language verdict with the resonance marked in the sweep.
- Live S11 acquisition from a LibreVNA through LibreVNA-GUI's SCPI/TCP server, with cancellable sweeps,
  resynchronisation after protocol errors, structured SCPI logging and a diagnostics bundle.
- Return loss, VSWR, complex impedance, Smith chart, markers, per-band results with power accepted and mismatch
  loss, tuning hints and centring notes.
- Band catalog with regional Wi-Fi/ISM/SRD/GNSS definitions; user-defined bands, presets and Test-page tiles.
- Touchstone .s1p/.s2p import (RI/MA/DB, all frequency units) and export; `.afox` session files with checksums.
- Measurement comparison per band (bare vs. in enclosure, antenna A vs. B) with overlays and a report table.
- PDF report with verdict, scope and acceptance criterion, band table, annotated charts, per-band panels, markers,
  calibration record, instrument details, report ID and page numbering.
- Calibration awareness: reads the instrument-reported calibration, records the operator's confirmation, 50 Ω load
  check, evidence grading of every result.
- Comparative gain wizard (substitution method with a characterized reference antenna; relative comparison
  otherwise) and manual directional cuts. Gain is never derived from S11.
- Simulation mode with antenna models for demonstrations and tests; every simulated result is labelled as such.
- Device-adapter architecture (`docs/DEVICE_ADAPTERS.md`); LibreVNA is the first and currently only live adapter.
- macOS application bundle with a verified dependency and license inventory.

### Validated on hardware
- LibreVNA V1 Rev.B with LibreVNA-GUI 1.6.5: connection, sweeps, cancellation, calibrated load/open/short, a 2.4 GHz
  antenna bare and in an enclosure, sessions, Touchstone and PDF from real data (`docs/HARDWARE_VALIDATION.md`).

[1.0.0]: https://github.com/AltinumTechnologies/antennafox/releases/tag/v1.0.0
