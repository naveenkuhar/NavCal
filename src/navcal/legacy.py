"""Reads events from the SQLite database used before Navcal moved to EDS."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path

from .models import Event


def legacy_db_path() -> Path:
    if override := os.environ.get("NAVCAL_DB"):
        return Path(override)
    data_home = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(data_home) / "navcal" / "navcal.db"


def load_legacy_events(path: Path) -> list[Event]:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT * FROM events").fetchall()
    except sqlite3.OperationalError:
        return []
    finally:
        db.close()
    events = []
    for row in rows:
        start = datetime.fromisoformat(row["start_at"])
        freq = row["freq"]
        events.append(Event(
            id=row["id"],
            title=row["title"],
            start=start,
            end=datetime.fromisoformat(row["end_at"]),
            all_day=bool(row["all_day"]),
            location=row["location"],
            notes=row["notes"],
            color=row["color"],
            freq=freq,
            interval=row["interval"],
            # The old app repeated weekly on the start's weekday only.
            byday=[(start.weekday(), 0)] if freq == "weekly" else [],
            until=date.fromisoformat(row["until"]) if row["until"] else None,
            reminders=[] if row["reminder_minutes"] is None else [row["reminder_minutes"]],
            exdates={date.fromisoformat(d) for d in json.loads(row["exdates"])},
        ))
    return events
