# SPDX-FileCopyrightText: 2026 Altinum Technologies e.U.
# SPDX-License-Identifier: GPL-3.0-or-later
"""PyInstaller entry point (absolute import, as frozen scripts have no parent package)."""

from antennafox.__main__ import main

raise SystemExit(main())
