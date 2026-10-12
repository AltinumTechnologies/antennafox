# Hardware verification procedure

The automated tests use a simulator and an emulated LibreVNA-GUI SCPI server. The following manual checks confirm
operation with a real LibreVNA. Record the date, AntennaFox version, LibreVNA-GUI version, firmware and hardware
revision for each run.

## Status

Last run: 2026-10-11 · AntennaFox 1.0.0 · LibreVNA-GUI 1.6.5 · firmware 1.6.5 · hardware V1 Rev.B (macOS).

| # | Check | Status |
|---|---|---|
| 1 | Connection to LibreVNA-GUI with a device attached | Passed 2026-10-11 |
| 2 | Calibrated 50 Ω load, open and short behave as expected | Passed 2026-10-11 |
| 3 | Antenna bare and in enclosure (single-band 2.4 GHz whip; no dual-band antenna available) | Passed 2026-10-11 |
| 4 | Comparison with vendor-specified frequency range | Passed with limitation 2026-10-11 – unbranded antenna, no datasheet; result consistent with the 2.4 GHz ISM class |
| 5 | Optional comparative S21 gain test with a characterized reference | Pending – requires reference antenna |
| 6 | Export PDF and Touchstone, reopen the session | Passed for sessions, Touchstone and PDF from real data (2026-10-11); LibreVNA-GUI import of the exported `.s1p` not yet checked |

### Results of 2026-10-11 (15 cm cable from port 1 into port 2, no calibration active)

- Identity, serial, firmware and hardware revision read correctly. The device reports a minimum frequency of 0 Hz;
  AntennaFox applies the documented 100 kHz. The reported power range of −40…0 dBm matches the spec sheet.
- S11 + S21 sweep 1 MHz – 6 GHz, 401 points, 1 kHz IFBW: 1.2 s, exact frequency grid, values identical to
  LibreVNA-GUI's own `:VNA:TRAC:AT?` readout. Two consecutive sweeps differ by ≤ 0.05 dB.
- 100 Hz IFBW sweep completed without false timeout; cancelling a long sweep stopped acquisition within 1 s and left
  the connection usable. GUI sweep settings were restored afterwards.
- Full survey scan through the UI (7 segments, 9257 points with the preset as it was that day – the shipped preset
  now builds 10 107 points –, ≈ 23 s of sweep time) produced a session, PDF and
  Touchstone export; the session reopened with all checksums valid and the Touchstone round trip of a segment was
  exact (lossless save/load is additionally covered by automated tests). The report showed
  *Gain: Not measured* and labeled the data as uncorrected.
- The terminated cable is a non-radiating matched load. It is flagged as *loss suspected* (match spanning ≥ 4:1 in
  frequency), not rated as a good antenna. The overview segment of this capture, decimated 10:1 (451 points,
  100 kHz – 6 GHz), is kept as `tests/data/librevna_v1_cable_into_port2_uncal.s1p` and used by several test files.
  CSV export was not exercised in this run.

### Results of 2026-10-11 (OSL calibration on port 1, ideal-standard kit, 1 MHz – 6 GHz, 1001 points)

- LibreVNA-GUI reported the active calibration as `OSL_1` with three port-1 measurements (Open, Short, Load);
  AntennaFox read and displayed it over SCPI.
- Load: worst S11 −42.6 dB at 4.3 GHz, below −49 dB under 3 GHz, impedance 50 ± 1 Ω over the whole span.
- Open: |S11| within ±0.1 dB of 0 dB, phase 0° ± 0.3° at all checked frequencies (right-hand rim).
- Short: |S11| within ±0.1 dB of 0 dB except one point of −0.56 dB near 6 GHz, phase 180° ± 0.7° (left-hand rim).
- Sweeps were taken through the SCPI backend with the calibration applied by the GUI; AntennaFox does not correct
  data itself.

### Results of 2026-10-11 (SCPI error attribution, LibreVNA-GUI 1.6.5 without device)

- A rejected optional query (`:DEV:INF:NOPE?`) followed by a valid command: the command is accepted – the GUI's
  CME bit from the query is cleared by the `*CLS` the transport now sends before every verified command. A genuinely
  invalid command is still reported as rejected, and the next command after it succeeds. Verified against the
  running GUI; the device was not attached at the time, so no sweep was involved.

### Results of 2026-10-11 (2.4 GHz whip antenna, OSL-calibrated port 1, Test page "Wi-Fi / Bluetooth 2.4 GHz")

- The antenna is an unbranded single-band 2.4 GHz whip, so check 3 was run on one band with two mountings instead
  of two bands (deviation from procedure step 3). Both runs used the *Wi-Fi / Bluetooth 2.4 GHz* tile plan:
  2 – 3 GHz, 1001 points, IFBW 1 kHz, −20 dBm. The 50 Ω load check button and the USB-disconnect step of the
  procedure were not exercised in these runs; the load figures of check 2 were taken over SCPI. Both runs were driven entirely through the application (toolbar Connect, Setup calibration fields,
  Test page tile, Save as, Compare, PDF report).
- **Bare, free space**: Good match; worst S11 −11.97 dB at 2.4835 GHz (upper band edge), max VSWR 1.67; resonance
  2.363 GHz at −35.6 dB; −10 dB window 2.265–2.525 GHz. The result panel added the note that the dip sits 79 MHz
  below the band centre with only 41 MHz margin above the band.
- **In a plastic enclosure**: Borderline; worst S11 −6.42 dB at 2.4835 GHz, max VSWR 2.83; resonance moved down
  126 MHz to 2.237 GHz; −10 dB window 2.135–2.391 GHz, i.e. the whole ISM band now lies above the window. The
  tuning hint named the resonance as lying below the band.
- Cross-check: S11 of the saved enclosure session agrees with LibreVNA-GUI's `:VNA:TRAC:AT?` readout to within
  0.01 dB at 2.200, 2.237, 2.400, 2.450, 2.4835 and 2.600 GHz.
- The enclosure run showed that the earlier wording of the centring note had the direction wrong ("a case widens
  the tight upper margin"); it now warns that dielectric loading eats into a tight upper margin. Both captures are
  kept as regression data (`tests/data/librevna_v1_whip24_*_cal.s1p`, `tests/test_hardware_captures.py`).
- Check 4: without a vendor datasheet the comparison is limited to the antenna class: a −10 dB window of 260 MHz
  centred near 2.4 GHz is what a 2.4 GHz ISM whip is expected to show; the report states "Gain: Not measured".
- Check 6: sessions saved and reopened from the Compare page; PDF generated from real data with the comparison
  table (resonance shift −126 MHz, Δ worst S11 +5.6 dB – the PDF subtracts the rounded values it prints; unrounded
  +5.5 dB) and the instrument line (serial, firmware 1.6.5,
  hardware 1 Rev.B). Importing the exported `.s1p` into LibreVNA-GUI remains to be checked by hand.

## Procedure

1. **Connection** – Start LibreVNA-GUI with the device attached and the SCPI server enabled. In AntennaFox click
   *Connect* and *Test connection*. Expect: GUI version, serial number, firmware, hardware revision and a 100 kHz –
   6 GHz instrument range (the V1 itself reports 0 Hz; AntennaFox applies the documented 100 kHz lower limit).
   Disconnect the USB cable briefly: AntennaFox must report the missing device
   instead of measuring.
2. **Calibration sanity** – Calibrate port 1 (OSL) in LibreVNA-GUI across a span covering the sweeps (1 MHz – 6 GHz
   was used) at the end of the test
   cable. Attach the load and run *50 Ω load check*: worst S11 should be below about −30 dB. Measure open and short
   with a user-defined sweep: the Smith chart traces should lie on the outer rim at the right (open) and left
   (short). Without a calibration, AntennaFox must label the data as uncorrected.
3. **Dual-band antenna** – Run the *Dual-band Wi-Fi* preset on a 2.4/5 GHz antenna. Verify the band table, the
   −10 dB windows and the Smith chart against LibreVNA-GUI's own display at a few markers (S11 within about 0.1 dB at
   identical frequencies). Repeat with the antenna in the target enclosure and compare using *Compare* and the graph
   overlays.
4. **Vendor comparison** – Compare the measured matched ranges with the vendor's specified frequency range. Confirm
   that the report states *Gain: Not measured*.
5. **Comparative gain (optional)** – With a characterized reference antenna, follow the gain wizard: fixed TX
   antenna on port 1, reference and DUT at the same marked position on port 2, at least three captures each. Record
   the room geometry and cable handling. Repeat on another day and compare.
6. **Exports** – Save the session, export PDF, CSV and Touchstone. Reopen the session and import the `.s1p`; the
   values must match the original measurement, and LibreVNA-GUI must be able to import the Touchstone file.

Update the status table with results and deviations before each release.
