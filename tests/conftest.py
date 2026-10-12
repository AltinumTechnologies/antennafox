# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Close and delete every window after each test, so Qt objects are not collected at a random later moment."""

import gc

import pytest


@pytest.fixture(autouse=True)
def _qt_teardown():
    yield
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if app is None:
        return
    for w in app.topLevelWidgets():
        w.close()
        w.deleteLater()
    app.processEvents()
    gc.collect()
    app.processEvents()
