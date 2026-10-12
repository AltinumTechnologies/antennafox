# Contributing to AntennaFox

Thanks for your interest in improving AntennaFox. Bug reports, measurement feedback, documentation fixes and code
contributions are all welcome.

## Reporting bugs

Open an [issue](https://github.com/AltinumTechnologies/antennafox/issues/new/choose) using the bug report template.
Helpful details:

- AntennaFox version, operating system, Python version
- LibreVNA-GUI version, firmware and hardware revision (shown on the Setup page)
- Steps to reproduce, expected and actual behavior
- The file from *Help → Save diagnostics…* (environment, instrument state and recent SCPI log)
- If possible, the saved `.afox` session or an exported `.s1p` file

## Suggesting features

Open an issue with the feature request template and describe the measurement problem you want to solve. Proposals
for new frequency bands are easiest to review with a reference to the regulation or protocol specification.

## Development setup

```bash
git clone https://github.com/AltinumTechnologies/antennafox.git
cd antennafox
uv sync
QT_QPA_PLATFORM=offscreen uv run pytest --cov
uv run ruff check src tests
uv run pyright
```

CI runs the same three commands on Linux, macOS and Windows and fails below 85 % line coverage; a test that hangs
(for example on an unexpected modal dialog) is aborted after 180 s. Tests drive the application through its user
entry points – buttons, menu actions, dialogs and the command line – not only through the functions behind them.

No hardware is needed for the test suite. An emulated LibreVNA-GUI SCPI server is available for manual testing:
`uv run python -m antennafox.instrument.librevna.emulator --port 19542`. See [Architecture](docs/ARCHITECTURE.md) for an
overview of the code.

## Pull requests

1. Fork the repository and create a branch from `main`.
2. Keep changes focused. Separate refactoring from functional changes.
3. Add or update tests. RF calculations need tests against independently known reference values.
4. Run the test suite and the linter.
5. Describe what changed and how you verified it, including hardware tests if you performed any.

## Measurement honesty

AntennaFox only reports what a measurement supports. Please keep it that way:

- S11 describes impedance match. It does not establish gain, efficiency, radiation pattern or range.
- Do not extrapolate beyond the measured span or present estimates as measurements.
- Label simulated data clearly.

## License

AntennaFox is licensed under the GNU General Public License, version 3 or (at your option) any later version
([LICENSE](LICENSE)). By contributing, you agree that your contributions are licensed under the same terms.

Every commit must carry a Developer Certificate of Origin sign-off (`git commit -s`, which adds
`Signed-off-by: Your Name <you@example.com>`), certifying that you have the right to submit the contribution under
the project license (see https://developercertificate.org). New source files start with the two SPDX lines used
throughout the repository:

```python
# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
```
