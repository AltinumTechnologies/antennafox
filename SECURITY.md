# Security policy

## Supported versions

Security fixes are applied to the latest version on the `main` branch.

## Reporting a vulnerability

Please do not open a public issue for security problems. Use GitHub's private vulnerability reporting for this
repository (*Security* tab → *Report a vulnerability*) and include a description, affected version and steps to
reproduce.

## Scope notes

- AntennaFox connects to LibreVNA-GUI's SCPI server, which is unauthenticated and, in LibreVNA-GUI, listens on all
  network interfaces. AntennaFox defaults to `127.0.0.1`. Restrict the port with a firewall on untrusted networks.
- Diagnostics files and the log contain the LibreVNA serial number and the SCPI host/port, never measurement data
  or file paths outside the application data directory.
- Session files (`.afox`) are ZIP archives with JSON and CSV content. They are parsed, never executed, and their
  checksums are verified on load.
