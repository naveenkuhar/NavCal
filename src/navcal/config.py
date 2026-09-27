# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""User preferences, stored as JSON in ~/.config/navcal/settings.json."""

from __future__ import annotations

import json
import os
from pathlib import Path

DEFAULTS = {
    "run-in-background": True,
    "view": "month",
    "width": 1100,
    "height": 760,
    "maximized": False,
    "told-background": False,
    "default-calendar": None,
    "default-task-list": None,
    "migrated": False,
    "migration-calendar": None,  # the calendar a (possibly unfinished) migration writes to
    "reminders-checked": None,
    "snoozed": [],
    # New events
    "default-reminder": 10,  # minutes before; -1 = none
    "default-duration": 60,  # minutes
    # Calendar display
    "first-weekday": -1,  # -1 = from the locale; else Python weekday (Mon=0)
    "week-numbers": False,
    "hide-weekends": False,
    "week-span": 7,  # days shown in the week view: 7, 4 or 3
    "hour-zoom": 1.0,  # how tall hours are in the day and week views, from 0.5 to 4
    "shade-work-hours": True,
    "work-start": 9,
    "work-end": 17,
    "footbar": True,
    "autostart": False,  # in a Flatpak (elsewhere, the autostart file is what counts)
    "dock": ["calendars"],  # what the bottom bar shows, in order (dock.ITEMS)
    "world-clocks": [],  # time zone ids shown in the sidebar
    "second-timezone": "",  # extra hour labels in the day and week views
    "print-options": {},  # the print dialog's last choices (printing.PrintOptions)
    # Online: place search, opening hours and weather (OpenStreetMap, Open-Meteo)
    "online": True,
    "weather": True,  # show the forecast under the days (needs a weather place)
    "weather-place": None,  # {"name", "lat", "lon"}
    "temperature-unit": "auto",  # "auto" (from the region), "c" or "f"
}


def _path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "navcal" / "settings.json"


class Config:
    def __init__(self):
        self._data = dict(DEFAULTS)
        try:
            self._data.update(json.loads(_path().read_text()))
        except (OSError, ValueError):
            pass

    def __getitem__(self, key):
        return self._data.get(key, DEFAULTS.get(key))

    def __setitem__(self, key, value):
        self._data[key] = value
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write a new file and swap it in, so a crash never leaves half a file.
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2))
        tmp.replace(path)
