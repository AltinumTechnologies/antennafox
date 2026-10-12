# Session file format (`.afox`)

An `.afox` file is a ZIP archive:

```
measurement.json          metadata, trace index, gain study, derived snapshot
traces/main_000_S11.csv   one file per captured sweep segment
traces/rep0_001_S11.csv   repeated sweeps (optional)
gain/ref_002.csv          S21 captures of the gain study (optional)
```

## Trace CSV

```
# parameter=S11; source=librevna-scpi; acquired_at=2026-10-11T14:40:28+00:00
freq_hz,re,im
2300000000.0,0.1234567890123,-0.0456789012345
```

Values are written with Python `repr` (round-trip exact for IEEE-754 doubles). S21 captures under `gain/` use the
comment line `# S21 role=ref; acquired_at=…` with the same header and value format. On load, lines starting with `#`
are ignored and the header must be exactly `freq_hz,re,im`. Each file's SHA-256 is stored in `measurement.json`;
loading fails if a checksum does not match. The archive uses DEFLATE, is written to `<name>.tmp` and renamed, and the
`.afox` suffix is enforced; JSON is UTF-8 (`ensure_ascii=False`).

## `measurement.json`

| Key | Content |
|---|---|
| `format` | `antennafox.session/1` |
| `saved_at`, `saved_by` | UTC ISO 8601 time of the save and the writing application, e.g. `AntennaFox 1.0.0` |
| `measurement` | `id`, `name`, `created_at`, `app_version`, `notes`, `operator`, `project`; `antenna` (`name`, `dut_reference`, `environment`, `antenna_type`, `connector`, `cable`, `mounting`, `variant`, `photo_path`, `active_gnss`, `notes`); `instrument` (`backend` = adapter id or `import`, `idn`, `model`, `host_software` and its version in `gui_version`, `serial`, `firmware`, `hardware_revision`, `host`, `port`, `limits` {`min_freq_hz`, `max_freq_hz`, `max_points`, `min/max_ifbw_hz`, `min/max_power_dbm`, `source` = `device` / `profile:<name>` / `simulation`}, `simulated`); `calibration` (`gui_reported_type` = instrument-reported calibration type, `user_confirmed`, `cal_date`, `cal_start_hz`, `cal_stop_hz`, `reference_plane`, `cal_kit`, `notes`, `verification`); `sweep_plan` (`preset_id`, `segments`, `ifbw_hz`, `averages`, `power_dbm`, `parameters`, `omissions` = disclosed limitations; `null` for Touchstone imports). The names `gui_version` and `gui_reported_type` are kept for compatibility. |
| `simulated` | `true` if any data came from simulation or the emulated GUI |
| `traces[]` | `file`, `parameter`, `label`, `source` (adapter id such as `librevna-scpi`, `simulation`, `touchstone-import`), `acquired_at`, `provenance` (free-form, adapter-specific; LibreVNA adapter: `trace`, `ifbw_hz`, `averages`, `power_dbm`, `points`, `cal_type`, `serial`, `gui_version`, `firmware`, `sweep_seconds` and `deembedding_active` when the GUI supports the query; simulator: additionally `model`, `seed`; Touchstone import: `file`, `format`, `z0`, `comments`), `points`, `z0` (reference impedance in Ω; absent in older files = 50), `sha256` |
| `repeats[][]` | Same structure for repeated sweeps |
| `gain` | `null` without a gain study; otherwise `study` (`reference` with `convention` as a string and `gamma_table` as `[f_hz, re, im]` rows, `setup`, `pattern_cuts`, `checklist`, `notes`) and `captures[]` (`file`, `role` = `ref`/`dut`, `acquired_at`, `label`, `angle_deg`, `simulated`, `sha256`) |
| `derived` | Snapshot of the assessment at save time, for reference only; the application recomputes everything after load and never reads it |

Captured traces are immutable. All derived values (band results, verdicts, gain estimates) are recomputed from the raw
complex data.

## Compatibility rules

Required keys: `format`, `measurement.{id, name, created_at, antenna, instrument, calibration}` and
`traces[].{file, parameter, source, acquired_at, sha256}`. Unknown keys inside `antenna`, `instrument`, `calibration`
and `sweep_plan` are ignored; missing optional keys take their defaults (e.g. `z0` 50, empty `verification`), and
`repeats`, `gain`, `label` and `provenance` may be absent. An unknown `format` is rejected.

## Touchstone import

Touchstone 1.x and basic 2.0 files with one or two ports, S-parameters in RI, MA or DB with any frequency unit, and a
reference impedance of 50 Ω are accepted (other impedances are refused rather than converted). The resulting
measurement has `instrument.backend = "import"`, no limits, `sweep_plan: null`, `source = "touchstone-import"` and an
unknown calibration state.

## Exports

- **CSV**: two comment lines (measurement name/id/simulated flag; conventions), then
  `segment, parameter, freq_hz, re, im, mag_db, phase_deg, vswr, r_ohm, x_ohm` for every point of every segment
  (VSWR and impedance only for S11/S22, Z0 = 50 Ω).
- **JSON**: `format: antennafox.export/1` with the metadata of `measurement.json` and inline `freq_hz`/`re`/`im`
  arrays.
- **Touchstone**: version 1.1, `# HZ S RI R 50`, comment lines naming the export, instrument, instrument-reported
  calibration and the simulation flag; typographic punctuation is transliterated to ASCII. Export is refused unless
  each parameter has exactly one segment and either S11 alone (`.s1p`) or the full S11/S21/S12/S22 set on one grid
  (`.s2p`) is available.
