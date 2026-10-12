# Device adapters

AntennaFox separates *how data is obtained* from *what is done with it*. Everything downstream of
`src/antennafox/instrument/` – sweep planning, sessions, RF analysis, reports and the Qt views – works on a
device-independent measurement model. A VNA is integrated by writing one adapter module that fulfils the small
contract described here. LibreVNA (through LibreVNA-GUI's SCPI server) is the first and, in this release, the only
hardware adapter; the in-process simulator is the second implementation of the same contract.

This document describes the extension points that exist today. It is not a driver guide for any particular device.
The README's *Supported instruments* table states which devices are supported and which are planned.

## The adapter contract

`instrument/base.py` defines `VNABackend`, a `typing.Protocol`. An adapter is any class with these members; no
base class or registration decorator is involved.

| Member | Purpose |
|---|---|
| `simulated: bool` | `True` only for data that must never look real (simulator). |
| `warnings: list[str]` | Non-fatal findings from `connect()` or `refresh_device()` (untested versions, fallback limits); the adapter resets the list itself at connect; shown to the user. |
| `connect() -> InstrumentInfo` | Open the link, identify the device, read or derive its limits. Raise on failure and leave nothing half-open. |
| `disconnect()`, `is_connected()` | Link lifecycle. |
| `info()`, `refresh_device()` | Current identity and limits; `refresh_device()` may talk to the device, `info()` must not. |
| `ping() -> float` | Round-trip time in seconds for the connection test. |
| `capabilities() -> DeviceCapabilities` | Static declaration of what the adapter can deliver (see below). |
| `calibration_type() -> str \| None` | Calibration the device or its host software reports as active; `None` when unknown. |
| `device_status() -> dict[str, str]` | Free-form live details for the *Instrument details* dialog. May be empty. |
| `acquire(params, cancel, progress) -> list[TraceSegment]` | One sweep, blocking, cancellable; one segment per requested S-parameter. |

Rules every adapter follows:

- **Errors are `InstrumentError` subclasses** (`ConnectionFailed`, `InstrumentTimeout`, `CommandRejected`,
  `ProtocolError`, `DeviceNotConnected`, `OutOfRange`, `Cancelled`) with user-readable messages. The UI shows them
  verbatim and treats anything else as a bug.
- **Validate before sending.** Call `check_in_limits(params, limits, capabilities)` first; it rejects
  out-of-range settings and unsupported S-parameters so nothing is silently clamped by the device.
- **Verify what comes back.** Read settings back where the protocol allows it; check point count and the frequency
  grid of returned traces against the request; reject non-finite values.
- **Honour the `CancelToken`** in every wait loop (`cancel.check()` / `cancel.wait(seconds)`) and stop the device
  when a sweep is aborted.
- **Stay in the adapter.** Command strings, response parsing and protocol quirks never leave the adapter module.
  `instrument/librevna/scpi_transport.py` is the LibreVNA adapter's transport, not a shared layer.

`AcquisitionParams` is the request: `start_hz`, `stop_hz`, `points`, `ifbw_hz`, `averages`, `power_dbm`,
`parameters` (tuple of S-parameter names) and a `label`. Adapters whose hardware cannot set a value (for example
output power) should record the actual value in `TraceSegment.provenance` rather than pretend it was applied.

## Device capabilities

`DeviceCapabilities` (frozen dataclass in `instrument/base.py`) declares, per adapter:

| Field | Meaning |
|---|---|
| `ports` | Number of test ports. |
| `parameters` | S-parameters the adapter can acquire, e.g. `("S11", "S21")`. Requests for anything else fail in `check_in_limits`. |
| `calibration` | `CalibrationOwner.HOST_SOFTWARE` (correction applied by the device's host software, adapter reads the active type) or `CalibrationOwner.SIMULATED`. |

Only values that an existing adapter actually uses are defined. The UI consults capabilities instead of assuming a
device: the comparative-gain page refuses to capture S21 when it is not in `parameters`.

Numeric limits are *not* capabilities: `InstrumentLimits` (frequency range, maximum points, IF-bandwidth and power
ranges) is part of `InstrumentInfo` because it may be read from the device at connect time. `limits.source`
records where the values came from (`device`, `profile:<name>`, `simulation`). `DEVICE_PROFILES` holds known
fallbacks for devices that cannot report their limits.

## The shared measurement model (`measurement/model.py`)

| Type | Content |
|---|---|
| `TraceSegment` | One captured sweep of one S-parameter: `parameter`, `freq_hz` (float64, Hz), `data` (complex128), `acquired_at` (ISO 8601 UTC), `source`, `label`, `provenance` (free-form dict), `z0` (Ω, 50 by default). Immutable after creation; arrays are copied and write-protected. |
| `InstrumentInfo` | `backend` (adapter id), `idn`, `model` (device family, e.g. `LibreVNA`), `host_software` (intermediary the adapter talks to, empty for direct links), `gui_version` (version of that host software; field name kept for session compatibility), `serial`, `firmware`, `hardware_revision`, `host`/`port`, `limits`, `simulated`. |
| `CalibrationMeta` | `gui_reported_type` (the adapter's `calibration_type()`, `None` = unknown) plus operator-entered reference plane, span, date and kit. `status()` yields `NOT_VERIFIED`, `USER_CONFIRMED` or `VALID_METADATA`. |
| `SweepPlan` / `SweepSegmentSpec` | Device-independent request produced from presets and the instrument limits. |
| `Measurement` | Antenna, instrument, calibration, plan, traces and repeats. `merged()` gives the analysis arrays. |

Conventions:

- `TraceSegment.source` is the adapter id for live data (`librevna-scpi`), `simulation` for simulated data –
  including data a live adapter obtains from an emulated device (`InstrumentInfo.simulated` is `True`; the LibreVNA
  adapter sets it for serial numbers starting with `EMULATOR`) – or `touchstone-import`. `InstrumentInfo.backend`
  uses the same ids plus `import`.
- `check_in_limits` always validates `power_dbm`, `ifbw_hz` and `averages` against `InstrumentLimits`, so a device
  without settable power or IF bandwidth still needs plausible limit ranges.
- `calibration_type()` returning the string `"None"` means "no calibration active"; returning `None` means
  "unknown". The two are treated differently throughout.
- `AdapterSpec.factory` is `Callable[[str, int], VNABackend]` (host, port); a serial/USB adapter would need that
  signature and the connection panel extended.
- All RF mathematics in `rf/` takes `freq_hz` and complex Γ arrays; it never sees an adapter. The pipeline assumes
  a 50 Ω reference impedance; `z0` records the value for every trace and Touchstone import rejects other values.
- Raw versus corrected data: the LibreVNA adapter receives data that LibreVNA-GUI has already corrected when a
  calibration is active, and records the active type in `provenance["cal_type"]` and
  `CalibrationMeta.gui_reported_type`. Data with no reported calibration is labelled *uncorrected* in the UI and
  reports; nothing is ever re-corrected in AntennaFox.
- Do not invent metadata. Leave fields empty (`""`, `None`) when the device cannot provide them; the views print
  `–` or *unknown*.

## Calibration responsibilities

AntennaFox does not perform calibrations. An adapter

1. reports the active calibration through `calibration_type()` (or `None`),
2. states in `DeviceCapabilities.calibration` who applies the correction,
3. stores the type with every trace in `provenance["cal_type"]`.

The operator's confirmation, reference plane, span and date live in `CalibrationMeta` and are device-independent.
The *50 Ω load check* on the Setup page is a plausibility sweep through the normal `acquire()` path and works with
any adapter.

## Touchstone files

`measurement/session.py::measurement_from_touchstone` turns a `.s1p`/`.s2p` file into a `Measurement` with
`InstrumentInfo(backend="import")`, no limits, no sweep plan and an unknown calibration state. Imported data then
uses exactly the same analysis, plotting and report code as live sweeps. A file is never presented as a connected
instrument and never goes through an adapter.

## Adding an adapter

1. Create `src/antennafox/instrument/<device>.py` with a class fulfilling `VNABackend`. Define module constants
   `ADAPTER_ID`, `DEVICE_NAME`, `HOST_SOFTWARE` (empty string for direct links) and `CAPABILITIES`.
2. Add a profile to `DEVICE_PROFILES` if the device cannot report its limits.
3. Put the implementation in its own package `instrument/<adapter>/` (the LibreVNA adapter is the template:
   `backend.py` plus whatever transport it needs; test doubles such as an emulator live there too) and append one
   `AdapterSpec` to `LIVE_ADAPTERS` in `instrument/adapters.py` (id, selector title, device name,
   default host/port, setup help, connect hint, factory). The Setup page's source selector, the toolbar's connect
   action and the Test page's hints are generated from this tuple; `conn/mode` in the settings stores the adapter id.
4. Document protocol observations in a `docs/<DEVICE>_INTEGRATION.md`, as `LIBREVNA_INTEGRATION.md` does.

The registry is a plain tuple on purpose. There is no plugin discovery, no entry-point scanning and no runtime
module loading, and none is planned until a second adapter exists. The connection panel currently shows host and
port for every live adapter; a non-network adapter would extend `AdapterSpec` rather than special-case the panel.

## Minimum tests for a new adapter

Model the suite on `tests/test_scpi_integration.py` and `tests/test_device_adapters.py`, using an emulator or
mocked transport – never hardware – in CI:

- connect: identification, limits (device-reported and fallback), warnings for untested versions, wrong identity,
  no server;
- acquisition: correct point count, exact frequency grid, requested parameters only, provenance fields,
  `source`/`backend` ids, `z0`;
- failure paths: rejected command, timeout, disconnect mid-sweep, truncated or stale data, cancellation;
- `check_in_limits` with the adapter's capabilities (unsupported S-parameter, out-of-range settings);
- one UI pass through `InstrumentPanel.make_backend()` and a sweep, as `tests/test_ui.py::test_scpi_path_through_ui`
  does for LibreVNA.

Record real-hardware checks in `docs/HARDWARE_VALIDATION.md`; emulator-only coverage must be stated as such.
