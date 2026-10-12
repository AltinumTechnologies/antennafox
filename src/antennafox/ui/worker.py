# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Run blocking instrument work off the GUI thread with progress and cancellation."""

from __future__ import annotations

import logging
import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..instrument.base import Cancelled, CancelToken, InstrumentError

log = logging.getLogger("antennafox.ui")


class _Runner(QObject):
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(object, str)

    def __init__(self, fn: Callable, token: CancelToken):
        super().__init__()
        self.fn = fn
        self.token = token

    def run(self) -> None:
        try:
            result = self.fn(self.token, lambda f, m: self.progress.emit(float(f), str(m)))
        except Cancelled as e:
            log.info("job cancelled")
            self.failed.emit(e, traceback.format_exc())
            return
        except InstrumentError as e:
            log.warning("job failed: %s", e)
            self.failed.emit(e, traceback.format_exc())
            return
        except Exception as e:  # noqa: BLE001 - surfaced to the UI, never swallowed
            log.exception("unexpected error in background job")
            self.failed.emit(e, traceback.format_exc())
            return
        self.finished.emit(result)


class Job(QObject):
    """One background job. fn(cancel_token, progress_cb) runs in a QThread; signals arrive on the GUI thread."""

    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(object, str)
    done = Signal()

    def __init__(self, fn: Callable, parent: QObject | None = None):
        super().__init__(parent)
        self.token = CancelToken()
        self._thread = QThread()
        self._runner = _Runner(fn, self.token)
        self._runner.moveToThread(self._thread)
        self._thread.started.connect(self._runner.run)
        self._runner.progress.connect(self.progress)
        self._runner.finished.connect(self._on_finished)
        self._runner.failed.connect(self._on_failed)

    def start(self) -> Job:
        self._thread.start()
        return self

    def cancel(self) -> None:
        self.token.cancel()

    def running(self) -> bool:
        return self._thread.isRunning()

    def wait(self, ms: int = 10000) -> bool:
        return self._thread.wait(ms)

    def _stop_thread(self) -> None:
        self._thread.quit()
        self._thread.wait(5000)

    def _on_finished(self, result) -> None:
        self._stop_thread()
        self.finished.emit(result)
        self.done.emit()

    def _on_failed(self, exc, tb) -> None:
        self._stop_thread()
        self.failed.emit(exc, tb)
        self.done.emit()
