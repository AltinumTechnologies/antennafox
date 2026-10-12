# Calibration

AntennaFox does **not** calibrate the LibreVNA. Calibration is performed and applied in **LibreVNA-GUI**, and
AntennaFox measures through the GUI's calibrated traces. AntennaFox reads which calibration is active, records
your calibration details as metadata, and labels uncorrected data accordingly.

## Why it matters

Without calibration, the measured S11 includes the VNA's own errors and the cable between port and antenna. Values
can be off by several dB and the Smith chart is rotated by the cable delay. A one-port (OSL/SOLT) calibration at the
point where the antenna is attached removes these effects.

## Procedure in LibreVNA-GUI

1. Let the LibreVNA warm up for 10–15 minutes.
2. Attach every cable, adapter and pigtail that will be used for the antenna measurement.
3. Open the calibration dialog and select a calibration kit that matches your standards.
4. Set the sweep span to cover all frequencies you intend to measure. A calibration is only valid inside its span.
5. Measure **open**, **short** and **load** at the reference plane, ideally the antenna connector including
   adapters.
6. Activate the calibration (one-port, port 1) and save it.
7. Do not move or bend the test cable between calibration and measurement.

See the [LibreVNA documentation](https://github.com/jankae/LibreVNA/tree/master/Documentation/UserManual) for
details of the calibration dialog.

## Verification in AntennaFox

- The **Setup** page shows the calibration type reported by LibreVNA-GUI (for example `OSL_1`, `SOLT_1`, or `None`;
  with `None` the card states that data will be labeled as uncorrected). The type is read again for every
  measurement and stored with it.
- *50 Ω load check* sweeps the currently configured span with the load attached and reports the worst S11.
  −30 dB or lower is plausible for a good load on a calibrated port; on a LibreVNA V1 with an ideal-standard OSL
  calibration the real-device check gave −42.6 dB worst over 1 MHz – 6 GHz (see
  [hardware validation](HARDWARE_VALIDATION.md)). The result is stored with a timestamp as the *Verification*
  entry of the measurement and printed in the PDF. The check does not certify the calibration and does not mark
  the instrument as calibrated.
- Open and short should lie on the outer rim of the Smith chart, at the right and left respectively.

## Calibration metadata

On the **Setup** page you can record:

| Field | Meaning |
|---|---|
| Instrument calibrated (OSL/SOLT) at the reference plane | Your confirmation that a calibration was performed |
| Reference plane | VNA port, end of test cable, or antenna connector including adapter/pigtail |
| Calibrated at | Date and time of the calibration (tick *known*; otherwise treated as unknown) |
| Calibrated span | Frequency range of the calibration |
| Calibration kit | Name of the kit used |

These fields determine the calibration status shown with each result:

| Status | Condition |
|---|---|
| Not verified | No confirmation recorded |
| User confirmed | Confirmation recorded, but at least one *Valid metadata* condition is missing (incomplete metadata, no active calibration reported by LibreVNA-GUI, or span not covering the measurement) |
| Valid metadata | Confirmed, active in LibreVNA-GUI, span covers the measurement, date and reference plane known |

The status and all metadata (instrument-reported calibration, reference plane, span, date and age, kit, load-check
result, notes) are stored with every measurement and printed in the PDF section *Calibration and test setup*,
which also carries an indicative, non-traceable S11 uncertainty note – AntennaFox does not compute a measurement
uncertainty. The status feeds the evidence grade of every result (*Not verified* → C, *User confirmed* → B,
*Valid metadata* with sufficient sampling → A; see [measurement method](MEASUREMENT_METHOD.md)). Uncalibrated
results are still shown, but with reduced evidence quality and an explicit note. Imported Touchstone files have an
unknown calibration state and are rated *Not verified*.
