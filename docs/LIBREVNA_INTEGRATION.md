# LibreVNA integration notes

AntennaFox talks exclusively to **LibreVNA-GUI**'s SCPI server over TCP. It never opens the USB device, so it can run
alongside the official GUI, which remains responsible for device control and calibration.

The LibreVNA adapter (`src/antennafox/instrument/librevna/backend.py`, transport in `librevna/scpi_transport.py`) is the first
implementation of the device-independent contract described in [device adapters](DEVICE_ADAPTERS.md). Everything
below is specific to this adapter and does not leak into the rest of the application.

Reference material: the [LibreVNA programming guide](https://github.com/jankae/LibreVNA/blob/master/Documentation/UserManual/ProgrammingGuide.pdf)
and the [SCPI examples](https://github.com/jankae/LibreVNA/tree/master/Documentation/UserManual/SCPI_Examples).
Behavior was verified against LibreVNA-GUI **1.6.5** (command list via `*LST?`, responses with and without a device;
the dated hardware runs are in [hardware validation](HARDWARE_VALIDATION.md)). Observations marked *(emulator)* below
reproduce upstream behaviour in `instrument/librevna/emulator.py` but have not been recorded on the real GUI. The adapter
id is `librevna-scpi`; defaults are `127.0.0.1:19542`.

## Enabling the server

*Window → Preferences → General → SCPI Server*, default port **19542**. Alternatively start the GUI with
`LibreVNA-GUI --port 19542`. The GUI accepts one client at a time; a new connection closes the previous one.

Note that LibreVNA-GUI listens on all network interfaces. AntennaFox defaults to `127.0.0.1`; restrict access with a
firewall if the computer is on an untrusted network.

## Protocol details that matter

| Observation (GUI 1.6.5) | Consequence in AntennaFox |
|---|---|
| A rejected command *or query* produces **no response line**; only the `*ESR?` CME bit (32) is set, and it stays set until read. | Every setting is preceded by `*CLS` and followed by `*ESR?`, so a CME bit left over from an earlier rejected optional query is never attributed to the command (verified on hardware). Every query is followed by a `*IDN?` sentinel: lines before the sentinel are the reply, none means "rejected". |
| A reply to a timed-out query can still arrive later. | After any timeout or malformed response (and once at connect time) the connection is resynchronized with a random two-value `*ESE` handshake; if the GUI stays silent for 6 s the connection is closed rather than risking misaligned data. |
| Sweep settings changed while the acquisition is running are applied asynchronously (timer) *(from the upstream sources)*. | Acquisition is stopped (`:VNA:ACQ:STOP`) before configuration, every value is read back and compared, then exactly one sweep is triggered with `:VNA:ACQ:SINGLE TRUE`. |
| The GUI may clamp values to the device limits without an error *(emulator)*. | Read-back mismatches abort the measurement with an explicit message. Requests outside the known limits are rejected before anything is sent. |
| Without a device, `:DEV:INF:LIM:MAXF?` reports 100 GHz and firmware queries fail *(emulator)*; with a device, firmware 1.6.5 reports a minimum frequency of 0 Hz. | Limits are trusted only when `:DEV:CONN?` reports a serial number; otherwise measurements are refused. Implausible values fall back to the built-in LibreVNA V1 profile with a warning, and the 0 Hz minimum is raised to the documented 100 kHz. |
| `:VNA:TRACE:DATA?` returns `[f,re,im],…` or `EMPTY`. | Point count and the exact linear frequency grid are verified; truncated, stale or partially updated traces are rejected. |
| Traces in the GUI can be renamed, paused or reassigned. | `:VNA:TRAC:PARAM?` and `:VNA:TRAC:PAUSED?` are checked; de-embedding state is recorded as provenance. |

## Acquisition sequence

```
connect: *IDN? · resync · *LST? · :DEV:CONN? (if "Not connected": :DEV:LIST?, :DEV:CONN <serial>, wait ≤ 8 s)
         :DEV:INF:FWREV? / HWREV? / LIM:*? · :VNA:CAL:ACTIVE?
:DEV:MODE VNA · :VNA:ACQ:STOP · :VNA:SWEEP FREQUENCY · :VNA:SWEEPTYPE LIN (if listed by *LST?)
:VNA:STIM:LVL · :VNA:ACQ:IFBW · :VNA:ACQ:AVG · :VNA:ACQ:POINTS · :VNA:FREQ:START/STOP   (each read back)
:VNA:TRAC:PARAM? / :VNA:TRAC:PAUSED? / :VNA:TRAC:DEEMB:ACT? per trace · :VNA:CAL:ACTIVE?
:VNA:ACQ:SINGLE TRUE
poll :VNA:ACQ:FIN? and :VNA:ACQ:RUN? until finished (:VNA:ACQ:FREQ? for progress; cancellable;
     deadline 15 s + 4× the estimated sweep time; :VNA:ACQ:STOP on cancel, timeout or error)
:VNA:TRAC:DATA? <parameter> for each requested S-parameter (S11; S21 for gain captures)
```

Timeouts: 3 s per exchange by default, 5 s for `*LST?`, 15 s for `:DEV:CONN`, 6 s for the resync handshake, and
max(10 s, 2 ms per point) for a trace read. Connecting makes the GUI attach the device if it is listed but not yet
connected – a user-visible side effect.

## Calibration

AntennaFox reads `:VNA:CAL:ACTIVE?` (e.g. `OSL_1`, `SOLT_1` or `None`) and stores it with every trace. It does not run or
emulate calibrations. The UI records the operator's confirmation, reference plane, calibrated span and date; data
from an uncalibrated GUI state is labeled as uncorrected.

## Instrument details

Clicking the connection status in the toolbar opens *Instrument details*. Besides the identification, limits and
calibration state it shows live values read on demand: `:DEV:INF:TEMP?` (source / LO / MCU temperatures),
`:DEV:STA:UNLO?`, `:DEV:STA:ADCO?`, `:DEV:STA:UNLE?` (PLL lock, ADC overload, source level) and `:DEV:REF:IN?`
(reference clock). These are read only while no sweep is running, because the single SCPI connection is then in
use by the background sweep; the dialog says "unavailable while a measurement is running".

## Logging and the emulator

The logger `antennafox.scpi` records every line sent (`>>`) and received (`<<`, truncated to 160 characters) at DEBUG
level, and rejects, timeouts and disconnects at WARNING level. Start with `antennafox --debug` to keep the traffic in
the log file, or use *Help → Save diagnostics…* to collect it for a bug report. For development without hardware,
`uv run python -m antennafox.instrument.librevna.emulator --port 19542 --model dualband_24_5` reproduces the 1.6.5
behaviour including fault injection; its serial number starts with `EMULATOR`, which marks all data as simulated.

## Version risks

- Command names and trace formatting may change between GUI releases. AntennaFox reads `*LST?` at connect time and
  refuses to connect when a required command is missing (if the GUI does not answer `*LST?`, the check is skipped
  and every command is verified individually via `*ESR?`). GUI versions other than those in `TESTED_GUI_VERSIONS`
  (1.6.5) produce a warning, not a refusal.
- Firmware and GUI versions are recorded in every session and report.
