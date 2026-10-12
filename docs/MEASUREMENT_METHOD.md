# Measurement method and limitations

## Reflection quantities

All derived values use the complex reflection coefficient Γ(f) = S11(f) as delivered by the instrument, never a dB
trace. Whether it is calibrated is tracked separately (see *Evidence grade*).

| Quantity | Formula | Notes |
|---|---|---|
| S11 (plotted) | 20·log10\|Γ\| | Negative for a matched antenna |
| Return loss | RL = −20·log10\|Γ\| | Positive convention |
| VSWR | (1 + \|Γ\|) / (1 − \|Γ\|) | ∞ for \|Γ\| ≥ 1 |
| Impedance | Z = Z0 (1 + Γ) / (1 − Γ), Z0 = 50 Ω | Singularity at Γ = 1 is guarded |
| Reflected power | \|Γ\|² | |
| Mismatch loss | −10·log10(1 − \|Γ\|²) | Physically valid for \|Γ\| < 1 only |

\|Γ\| > 1 cannot occur for a passive antenna. Values are reported unclipped (S11 > 0 dB, VSWR ∞); when \|Γ\| exceeds
1.02 (2 % tolerance for noise) a warning points to calibration or the reference plane, and such a band can never pass.

### Plain-language figures

The result panel and the PDF translate the worst point of each band into *power accepted* = 1 − \|Γ\|² and the
corresponding mismatch loss. Both are printed as bounds and rounded in the safe direction: "≥ 81 %" rounds the
accepted share down, "≤ 1.2 dB" rounds the loss up to 0.1 dB.

## Band qualification

A band consists of one or more required frequency intervals (e.g. three separate LoRaWAN channels). Two neighboring
sweep points cover the frequencies between them only if their spacing is at most

    max_gap = min( max(width / (n − 1), 0.1 % · f_c), 0.5 % · f_c ),   f_c = centre of the interval, n = 3 by default

and each interval must contain at least n real sweep points. Bands that are only partially covered are reported as
*Partially measured* with *Insufficient data*; nothing is extrapolated beyond the measured span.

Within covered intervals, |Γ| is evaluated at every sweep point plus interval edges interpolated linearly in |Γ|;
the matched fraction (80 % rule) and the −10 dB window edges are interpolated linearly in dB between nodes, exact
nulls (−∞ dB) being floored at −200 dB for that purpose only:

- **Well matched**: worst S11 ≤ −10 dB (configurable; VSWR ≈ 1.92)
- **Marginal**: worst S11 ≤ −6 dB, or at least 80 % of the band ≤ −10 dB
- **Poor match**: otherwise

The overall verdict is the worst conclusion among the selected target bands that were fully measured; target
bands with insufficient data are listed as a warning and do not contribute. The headline states in how many of the
n target bands the match is marginal or poor. Survey scans (no target bands selected) evaluate the preset's or the
user's survey band list without an overall verdict; the headline reports how many of these bands are well matched.
Loss suspicion still applies.

### Resonance, windows, hints

- A *resonance* is a local S11 minimum with at least 3 dB prominence. *Matched windows* are the contiguous ranges
  with S11 ≤ −10 dB, their edges interpolated linearly in dB; a gap larger than 4× the median point spacing is never
  bridged.
- *Tuning hint*: for a band that is not well matched, the deepest dip within ±35 % of the band centre is located. If
  it lies outside the band and is at least 6 dB deep (or S11 is still falling at the sweep edge), the hint names the
  direction and the usual remedy (a lower resonance needs a longer radiator, and vice versa).
- *Centring note*: for a passing band whose dip is off-centre or whose −10 dB window leaves little margin, the note
  reports the margins on both sides. Dielectric loading (a case, a hand, nearby objects) pulls the resonance
  **down**, so a tight upper margin shrinks in an enclosure and a tight lower margin widens.

## Loss suspicion

A broadband match – S11 ≤ −10 dB over a contiguous frequency ratio of at least 4:1, or at least 2:1 when the trace
shows no distinct resonance (median minus minimum S11 below 10 dB) – is typical of lossy antennas, attenuators, long
lossy cables or terminations (or of a genuinely broadband antenna). The rule needs at least 10 points with at least
half of them matched. Such results are rated *loss suspected* and never receive a positive statement. A sweep that is
matched throughout (every point ≤ −10 dB) without a dip of at least 6 dB prominence triggers a warning to run a wide
survey scan.

## Data quality checks

Findings that produce warnings, and grade D when severe: noise σ above 0.01 (linear, estimated from point-to-point
differences), ripple above 1.5 dB peak-to-peak away from resonances (a standing wave on the cable), repeated sweeps
with σ or drift above 0.5 dB, sweep segments captured more than 10 minutes apart, and mixed instrument or
calibration provenance within one measurement.

## Evidence grade

| Grade | Meaning |
|---|---|
| A | Calibration with valid metadata, sufficient sample density and – when sweeps were repeated – repeat σ and drift ≤ 0.5 dB |
| B | Calibration confirmed by the operator, or valid metadata without the conditions for A |
| C | Calibration not verified – values of limited reliability |
| D | Data problems detected |
| SIM | Simulated data |

## Comparative gain (substitution method)

    G_DUT,realized ≈ G_ref,realized + S21_DUT(dB) − S21_ref(dB) + (L_DUT,path − L_ref,path)

- The reference gain must come from a real characterization of the reference antenna in the measured direction. If
  it is IEEE gain (mismatch excluded), it is converted with the reference |Γ|: G_realized = G + 10·log10(1 − |Γ|²).
- Absolute dBi is only reported if reference data are trusted (source or certificate named, tolerance given) and
  cover the test frequency without extrapolation, the geometry is complete (distance and largest dimension > 0) and
  satisfies the far-field condition R ≥ max(2D²/λ, 5D, 3λ), polarization and position match are confirmed, and the
  signal is above −90 dB. Otherwise only the relative difference in dB is shown (its uncertainty excludes the
  reference tolerance); without an S21 value of both antennas at the test frequency the result is *unavailable*.
- Uncertainty (k = 1 terms combined by root-sum-square, reported with k = 2 and rounded up to 0.1 dB): reference
  tolerance, repeatability (from repeated captures, or 0.5 dB if none), path-loss uncertainty, positioning, and
  environment/multipath.
- The result applies to the measured direction and polarization only – not peak gain, not efficiency.
- Rotation cuts are normalized to the strongest direction; one or two planes do not form a 3D pattern.

## Limitations

- S11 alone does not demonstrate gain, efficiency, polarization purity, range, throughput or regulatory compliance.
- Band compatibility is not protocol or device compatibility (e.g. antenna certification for a radio module, diversity,
  transmit power limits).
- Active GNSS antennas: the LNA is neither powered nor assessed.
- Indoor gain measurements are affected by multipath by several dB.
