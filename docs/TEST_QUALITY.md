# Test Quality Review

Scope: a focused review of the automated test suite before the first public release, concentrating on the
scientific correctness of the RF pipeline, the Touchstone reader/writer, the shared measurement model and the
LibreVNA adapter boundary. All figures below were produced with
`QT_QPA_PLATFORM=offscreen uv run pytest -q --cov --cov-report=term` (statement coverage; CI fails below 85 % via
`--cov-fail-under=85`, a local run does not) and
`--cov-branch` for the combined statement+branch figure.

## 1. Initial measurements

173 tests, all passing. Statement coverage 91 %, combined statement+branch coverage 88 %.

| Module group | Statement coverage before | Observations |
|---|---|---|
| `rf/rfmath.py` | 90 % | `vswr_to_gamma`, `db20` and the open/short/non-passive guards only partly exercised |
| `rf/bands.py` | 94 % | `matched_windows` edge interpolation, interval statistics, notes and the `IntervalResult` fields untested |
| `rf/touchstone.py` | 84 % | `[Version]`, `[Reference]`, `[Matrix Format]`, `[End]`, duplicate/negative frequencies, `R` without value, DB phase sign untested; `read_touchstone` only reached through the application's own exports |
| `rf/quality.py` | 87 % | estimator constants loosely checked (±15 %), ripple/repeat/spread/provenance paths unverified |
| `rf/explain.py` | 88 % | ratings other than GOOD/SUSPECT, edge-of-sweep hints, "little margin" wording unverified |
| `rf/smith.py` | 100 % executed | executed only by widget construction, no geometric assertion at all |
| `measurement/model.py` | 92 % | `age_days`, calibration span checks, duplicate handling in `merged()` untested |
| `measurement/session.py` | 85 % | invalid archives, wrong format, CSV header tamper, gain checksum, non-50 Ω import untested |
| `instrument/librevna/backend.py` | 83 % | invalid read-backs, trace parameter/paused checks, `FIN?` garbage, non-finite traces, limit fallback untested |
| `instrument/librevna/scpi_transport.py` | 83 % | multi-line answers, ESR execution/device bits, identify timeout, socket error mapping untested |
| `instrument/base.py` | 86 % | only two of seven `check_in_limits` branches reached |

Mutation testing baseline (mutmut 3.8, six RF modules, four RF test files): 2028 mutants, 1078 killed (53 %).
`smith.py` had a 0 % kill rate (no direct tests); `quality.py` 47 %, `explain.py` 49 %, `bands.py` 61 %,
`touchstone.py` 62 %, `rfmath.py` 72 %.

Distinguishing executed from tested code: `smith.py` was the clearest case of coverage without verification;
`touchstone.py`'s DB branch counted as covered although a 180° test angle cannot detect a phase sign error;
`matched_windows` and `_fraction_below` were covered by dense grids on which an interpolation error of a full
sample step changes the result by less than the test tolerance.

## 2. Weaknesses found

Two production defects, both fixed minimally with regression tests:

1. **Exactly matched samples broke band evaluation** (`rf/bands.py`). A sample with |Γ| = 0 has S11 = −∞ dB.
   The dB-linear interpolation of threshold crossings in `matched_windows()` and `_fraction_below()` then
   divided ∞/∞, producing NaN: a matched window containing such a sample disappeared from the summary, the
   band's `matched_fraction` became NaN and a `RuntimeWarning` was emitted. Fixed by flooring S11 at −200 dB for
   interpolation only (statistics such as `best_s11_db` still report −∞). Regression test:
   `test_bands.py::test_perfectly_matched_sample_does_not_break_windows_or_fractions`.
2. **Stale status bits were blamed on the next command** (`instrument/librevna/scpi_transport.py`). LibreVNA-GUI sets the
   CME bit for a rejected query and the adapter deliberately probes optional queries with `try_query()`
   (firmware, temperatures, limits). The bit stayed set until the next `*ESR?`, so the first setting of the
   following sweep was reported as "rejected by LibreVNA-GUI" and the sweep aborted; a retry succeeded. Fixed by
   clearing the event status (`*CLS`) immediately before every verified command. Regression test:
   `test_scpi_faults.py::test_rejected_optional_query_does_not_fail_the_next_command`.

Other findings (no production change):

- `touchstone_text()` writes frequencies with 1 Hz resolution; grids with sub-hertz spacing would not survive
  an export/import round trip. Not relevant for instrument data, documented here.
- The "connection closed by LibreVNA-GUI" path of the transport (`recv` returning no data) is reached
  depending on socket timing; the adapter behaves correctly either way (`ConnectionFailed`), but the specific
  branch is not deterministically covered.
- `ripple_pp_db` contains an unreachable guard (`win >= n`) given its own window formula.

## 3. Tests added

Suite grown from 173 to 307 tests at the time of the review (six new files, two extended); the release tree has 310
tests after `tests/test_hardware_captures.py` (real calibrated LibreVNA captures) was added:

- `test_rf_reference_values.py` – textbook |Γ| / return-loss / VSWR / impedance tables, Z0 scaling, scalar
  helper boundaries (`vswr_to_gamma(1) = 0`, `vswr_to_gamma(∞) = 1`, `db20`, exact wavelength), singular inputs
  (open, short, non-passive, near-unity guard band, NaN propagation, empty arrays), hand-derived values of the
  first sample of the LibreVNA capture (|Γ| = 0.3473, S11 = −9.186 dB, VSWR 2.064, Z = 34.18 + j26.22 Ω),
  trace summary of the capture, Smith-chart geometry (unit circle, constant-r and constant-x loci, arc start
  points, closed VSWR circle). Physically misleading cases: an uncalibrated import is graded C with explicit
  issues and can never exceed grade B without instrument-reported correction; a non-passive trace is flagged,
  rated POOR and shown as "VSWR ∞"; power wording matches the numbers.
- `test_touchstone_formats.py` – RI/MA/DB with non-trivial phases, all frequency units, option line case and
  order, first-option-line rule, inline and header comments, wrapped 2-port lines, Touchstone 2.0 keywords
  (`[Reference]`, `[Matrix Format]`, `[End]`, both `[Two-Port Data Order]` values), fourteen malformed inputs,
  port count from the file extension, undecodable bytes, 2-port writer round trip, writer refusals, the
  independent LibreVNA-GUI fixture (451 points, 100 kHz–6 GHz, first/last values, comments, passivity),
  metadata preservation on import, 75 Ω refusal, lossless export of the imported capture.
- `test_quality_metrics.py` – validation boundaries (zero frequency, 2 % passivity tolerance, `passive=False`),
  noise estimator within 3 % of the injected σ, ripple None paths and a standing-wave value, repeat statistics
  with analytically known standard deviation and drift including the −20 dB floor, capture spread, loss
  heuristic thresholds (10:1, 2.5:1, 1.5:1, resonance depth, half-matched span), `no_visible_resonance`,
  all finding codes of `assess_trace_quality`, evidence grades A/B/D, empty measurement, GNSS warning,
  headlines for every rating, survey explanation, edge-of-sweep tuning hints, "little margin" note.
- `test_measurement_model.py` – `merged()` ordering and worst-duplicate rule, `report_id`, simulation taint,
  immutable segments, every `CalibrationMeta.status()` rule and `age_days`, session rejection (not a ZIP,
  missing file, unknown format), CSV header validation with a recomputed checksum, sweep plan / limits /
  averaging round trip, JSON encoding of complex, numpy and enum values, gain-capture checksum.
- `test_scpi_faults.py` – a 10-line emulator subclass that answers chosen queries with fixed strings:
  implausible limits fall back to the profile with a warning; invalid read-back, trace parameter mismatch,
  paused trace, `FIN?` garbage, non-finite/short/stale trace data abort with `ProtocolError` and keep the
  connection; ESR execution/device bits and invalid ESR answers; optional `ACQ:FREQ?` read-back; identify
  timeout recovery through the resync handshake; socket error mapping; multi-line answer rejection without
  losing sync; missing required commands; rejected `*LST?`; progress callback contract; status without a
  device; `DeviceNotConnected` after disconnect; `check_in_limits` for all nine out-of-range settings and both
  edges; `CancelToken`; simulated backend guards and S21 data.
- `test_ui_smoke.py` – importing the real capture draws 451-point curves in S11/VSWR/R-X/Smith plots and
  disables the S21 tab; invalid Touchstone and session files are reported in a dialog and leave the state
  untouched; a connection to a closed port is shown inline and leaves the app disconnected; LibreVNA-GUI
  without a device is "connected – no device" and cannot start a measurement; demo / real / disconnected
  states and the calibration hint are named on the Quick page.
- `test_bands.py` – exact crossing interpolation on dense and coarse grids (rising and falling), inclusive
  threshold boundaries, the marginal-fraction rule, interval statistics and point counts, sparse in-band
  statistics, a narrow band bracketed without in-band samples, window interpolation/gaps/sweep edges,
  resonance detection with depth parameter, the exactly-matched-sample regression.

No test was added purely to execute a line; UI modules remain at 77–98 % by design.

## 4. Final measurements

310 tests, all passing (measured at version 1.0.0). Statement coverage 93 % (7027 statements, 488 missed), combined
statement+branch coverage 91 %.

| Module | Statement | Statement+branch |
|---|---|---|
| `rf/rfmath.py` | 100 % | 100 % |
| `rf/bands.py` | 100 % | 100 % |
| `rf/smith.py` | 100 % | 100 % |
| `rf/touchstone.py` | 99 % | 99 % (unreachable `raise` for an unknown format) |
| `rf/quality.py` | 99 % | 98 % |
| `rf/explain.py` | 99 % | 98 % |
| `rf/assessment.py` | 100 % | 99 % |
| `measurement/model.py` | 100 % | 100 % |
| `measurement/session.py` | 100 % | 100 % |
| `instrument/base.py` | 100 % | 99 % |
| `instrument/librevna/backend.py` | 97 % | 95 % |
| `instrument/librevna/scpi_transport.py` | 93–96 % (timing-dependent branch, see §2; OS-level fault paths, see §6) | 96 % |
| `instrument/simulated/backend.py` | 100 % | 99 % |

The review's targets (RF ≈ 90 %, Touchstone ≈ 90 %, model ≈ 90 %, LibreVNA ≈ 80 %, abstraction ≈ 85 %) are
all met.

## 5. Mutation testing

mutmut 3.8 was run ad hoc (`uv run --with mutmut`, temporary `[tool.mutmut]` section, not committed) on the six
RF modules with the RF test files only, so that a run stays around two minutes.

| Module | Mutants | Killed before | Killed after |
|---|---|---|---|
| `rfmath.py` | 119 | 72 % | 84 % |
| `bands.py` | 514 | 61 % | 83 % |
| `touchstone.py` | 395 | 62 % | 92 % |
| `smith.py` | 102 | 0 % | 82 % |
| `quality.py` | 521 | 47 % | 72 % |
| `explain.py` | 382 | 49 % | 69 % |
| total | 2033 (2028 in the baseline run; mutants are regenerated when tests change) | 53 % | 79 % |

Meaningful survivors that drove new tests: the DB-format phase sign (`* exp(jφ)` → `/ exp(jφ)`), the v2
`12_21` S11 slot, `[Version]`/`[Reference]`/`[Matrix Format]`/`[End]` handling, duplicate and negative
frequencies, `vswr_to_gamma` boundaries, the interpolation arithmetic in `_fraction_below` and
`matched_windows`, the 4× gap factor, the marginal-fraction comparison, `noise_sigma` constants, the
repeatability `ddof`, the loss-heuristic ratio and depth thresholds, and Smith arcs starting on the unit circle.

Remaining survivors were reviewed and are equivalent or cosmetic: `dtype=`/`errstate` arguments, `<` vs `<=`
at measure-zero boundaries (an S11 value exactly equal to a threshold), `-200` vs `-201` dB floors, message
wording (capitalisation, "XX" padding) and Smith grid extents. Three mutants of the option-line parser time out
(infinite loop) and count as detected.

## 6. Remaining untested or hardware-dependent areas

- Behaviour of a physical LibreVNA through the real LibreVNA-GUI (timing, firmware limit values, actual
  calibration types, USB disconnects) – covered only by the emulator built from the 1.6.5 sources; see
  `docs/HARDWARE_VALIDATION.md`.
- LibreVNA-GUI versions other than 1.6.5 (the version warning and per-command verification are tested, the
  command sets are not).
- `attach_device()` waiting for a slow device connection, and a `*LST?` that times out rather than being
  rejected.
- Pixel-level rendering of plots and PDF charts; UI modules `plots.py`, `compare_page.py`, `worker.py`,
  `gain/steps.py` and `state.py` sit between 77 % and 86 %.
- Error paths that require OS-level faults (socket `close()` failures, `recv` errors other than a closed peer).

## 7. Final automated results

- `uv run ruff check src tests scripts` – all checks passed
- `uv run pyright` – 0 errors, 0 warnings, 0 informations
- `QT_QPA_PLATFORM=offscreen uv run pytest -q --cov --cov-report=term` – 310 passed, total coverage 93 %
  (gate 85 %); with `--cov-branch` 91 %
