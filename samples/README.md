# Sample data

All files in this directory are **simulated** (synthetic antenna models, labeled as simulation inside the data) and
serve only to explore AntennaFox without hardware. Regenerate them with `uv run python scripts/generate_samples.py`.

| File | Content |
|---|---|
| `sim_dualband_whip.afox` / `.s1p` / `.pdf` | Dual-band 2.4/5 GHz whip, Dual-band Wi-Fi preset, with example report |
| `sim_lora868_helix.afox` / `.s1p` | Narrowband 868 MHz helix, Sub-GHz EU preset |
| `sim_gnss_patch.afox` / `.s1p` | Passive GNSS L1 patch |
| `sim_lossy_matched.afox` / `.s1p` | Lossy, well-matched load – demonstrates the loss-suspicion warning |
