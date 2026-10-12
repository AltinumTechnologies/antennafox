# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Application logging and the diagnostics bundle users can attach to a bug report.

Two sinks: a rotating log file in the application data directory and an in-memory ring buffer of recent records.
The SCPI transport logs every line exchanged with LibreVNA-GUI at DEBUG level, so a diagnostics bundle shows exactly
what the instrument said when something went wrong.
"""

from __future__ import annotations

import collections
import logging
import logging.handlers
import platform
import sys
from datetime import datetime
from pathlib import Path

from . import __version__

LOG_FILE = "antennafox.log"
RING_SIZE = 4000
_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"

_ring: RingHandler | None = None
_log_path: Path | None = None


class RingHandler(logging.Handler):
    """Keeps the most recent formatted records so a bundle can be written even if file logging failed."""

    def __init__(self, size: int = RING_SIZE):
        super().__init__(level=logging.DEBUG)
        self.records: collections.deque[str] = collections.deque(maxlen=size)
        self.setFormatter(logging.Formatter(_FORMAT))

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(self.format(record))


def setup(log_dir: Path, level: int = logging.INFO, console: bool = False) -> Path | None:
    """Install the handlers once. Returns the log file path, or None if the directory is not writable."""
    global _ring, _log_path
    root = logging.getLogger("antennafox")
    root.setLevel(logging.DEBUG)
    if _ring is None:
        _ring = RingHandler()
        root.addHandler(_ring)
    if console:
        sh = logging.StreamHandler(sys.stderr)
        sh.setLevel(level)
        sh.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(sh)
    if _log_path is None:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(log_dir / LOG_FILE, maxBytes=2_000_000, backupCount=3,
                                                      encoding="utf-8")
            fh.setLevel(level)
            fh.setFormatter(logging.Formatter(_FORMAT))
            root.addHandler(fh)
            _log_path = log_dir / LOG_FILE
        except OSError as e:
            root.warning("file logging disabled: %s", e)
    root.info("AntennaFox %s starting on %s, Python %s", __version__, platform.platform(), platform.python_version())
    return _log_path


def log_path() -> Path | None:
    return _log_path


def recent_records() -> list[str]:
    return list(_ring.records) if _ring is not None else []


def write_bundle(path: Path, context: dict[str, str] | None = None) -> Path:
    """Write a plain-text diagnostics file: environment, optional context lines, then the recent log."""
    lines = [
        f"AntennaFox diagnostics – {datetime.now().isoformat(timespec='seconds')}",
        f"Version: {__version__}",
        f"Platform: {platform.platform()}",
        f"Python: {platform.python_version()}",
    ]
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion

        lines.append(f"Qt: {qVersion()} / PySide6 {pyside_version}")
    except ImportError:
        pass
    for key, value in (context or {}).items():
        lines.append(f"{key}: {value}")
    lines += ["", f"Recent log ({len(recent_records())} records)", "-" * 72, *recent_records()]
    path = Path(path)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
