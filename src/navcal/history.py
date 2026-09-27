# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Undo/redo history that survives restarting Navcal.

Each entry is a label plus the snapshots needed to restore the affected events
(see Backend.snapshot): (calendar uid, event uid, [iCalendar text, …]). An empty
text list means "the event didn't exist", so restoring removes it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

LIMIT = 50

Snapshot = tuple[str, str, list[str]]
Entry = tuple[str, list[Snapshot]]


def _path() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
    return Path(base) / "navcal" / "history.json"


class History:
    def __init__(self, path: Path | None = None):
        self.path = path or _path()
        self.undo: list[Entry] = []
        self.redo: list[Entry] = []
        try:
            data = json.loads(self.path.read_text())
            self.undo = [(label, [tuple(s) for s in snaps]) for label, snaps in data["undo"]]
            self.redo = [(label, [tuple(s) for s in snaps]) for label, snaps in data["redo"]]
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def record(self, label: str, snapshots: list[Snapshot]) -> None:
        """A new change: it can be undone, and anything undone before can't be redone."""
        self.undo.append((label, snapshots))
        del self.undo[:-LIMIT]
        self.redo.clear()
        self.save()

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"undo": self.undo[-LIMIT:], "redo": self.redo[-LIMIT:]}))
            tmp.replace(self.path)
        except OSError:
            pass  # history is a convenience; never fail a change because of it
