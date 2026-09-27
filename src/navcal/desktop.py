# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Desktop integration: the .desktop entry, app icon and start-at-login."""

from __future__ import annotations

import os
import sys
from importlib.resources import files
from pathlib import Path

APP_ID = "io.github.navcal.Navcal"
BACKGROUND_OPTION = "background"


def _config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def _data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


LAUNCHER_PATH = _data_home() / "applications" / f"{APP_ID}.desktop"
ICON_PATH = _data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
SYMBOLIC_ICON_PATH = (_data_home() / "icons" / "hicolor" / "symbolic" / "apps"
                      / f"{APP_ID}-symbolic.svg")
AUTOSTART_PATH = _config_home() / "autostart" / f"{APP_ID}.desktop"
# Lets GNOME Shell start Navcal for searches when it isn't running.
DBUS_SERVICE_PATH = _data_home() / "dbus-1" / "services" / f"{APP_ID}.service"
# Files written by the earlier Qt version.
_LEGACY = [_data_home() / "applications" / "navcal.desktop",
           _config_home() / "autostart" / "navcal.desktop"]


def _desktop_entry(background: bool) -> str:
    # The project's own interpreter, so the entry works without `uv run`.
    command = f'"{sys.executable}" -m navcal'
    if background:
        command += f" --{BACKGROUND_OPTION}"
    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Name=Navcal",
        "Comment=Plan your days and get reminders",
        "Keywords=calendar;event;reminder;schedule;",
        f"Exec={command}",
        f"Icon={APP_ID}",
        "Categories=GNOME;GTK;Office;Calendar;",
        "StartupNotify=true",
    ]
    if background:
        lines += ["NoDisplay=true", "X-GNOME-Autostart-enabled=true"]
    return "\n".join(lines) + "\n"


def _write_if_changed(path: Path, content: str) -> None:
    if not path.exists() or path.read_text() != content:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


def install() -> None:
    """Register Navcal with the desktop so it has a name and icon.

    GNOME Shell needs the .desktop file to display the app's notifications.
    """
    had_legacy_autostart = _LEGACY[1].exists()
    for path in _LEGACY:
        path.unlink(missing_ok=True)
    _write_if_changed(LAUNCHER_PATH, _desktop_entry(background=False))
    _write_if_changed(ICON_PATH, files("navcal").joinpath("data/icon.svg").read_text())
    _write_if_changed(SYMBOLIC_ICON_PATH,
                      files("navcal").joinpath("data/icon-symbolic.svg").read_text())
    _write_if_changed(DBUS_SERVICE_PATH, "[D-BUS Service]\n"
                      f"Name={APP_ID}\n"
                      f'Exec="{sys.executable}" -m navcal --gapplication-service\n')
    if had_legacy_autostart or AUTOSTART_PATH.exists():
        set_autostart(True)  # also refreshes the command if the interpreter moved


def autostart_enabled() -> bool:
    return AUTOSTART_PATH.exists()


def set_autostart(enabled: bool) -> None:
    if enabled:
        _write_if_changed(AUTOSTART_PATH, _desktop_entry(background=True))
    else:
        AUTOSTART_PATH.unlink(missing_ok=True)
