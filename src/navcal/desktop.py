# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Desktop integration: the .desktop entry, app icon and start-at-login.

Run from the source folder, Navcal registers itself with the desktop. Installed
as a Flatpak, the package provides all that, and start-at-login goes through
the Background portal.
"""

from __future__ import annotations

import os
import sys
from importlib.resources import files
from pathlib import Path

from gi.repository import Gio, GLib

APP_ID = "io.github.navcal.Navcal"
BACKGROUND_OPTION = "background"
IN_FLATPAK = os.path.exists("/.flatpak-info")
PORTAL = ("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop")


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
    if IN_FLATPAK:
        return  # the Flatpak brings its own launcher, icons and D-Bus service
    if flatpak_installed():
        # Run from the source folder while the Flatpak is installed: leave the
        # launcher to the Flatpak, or this copy would open from the app grid.
        for path in (LAUNCHER_PATH, DBUS_SERVICE_PATH, ICON_PATH, SYMBOLIC_ICON_PATH):
            path.unlink(missing_ok=True)
        return
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


def flatpak_installed() -> bool:
    """Is Navcal installed as a Flatpak (for this user or everyone)?"""
    exported = Path("exports") / "share" / "applications" / f"{APP_ID}.desktop"
    return any((base / exported).exists() for base in (_data_home() / "flatpak", Path("/var/lib/flatpak")))


def autostart_enabled(config) -> bool:
    if IN_FLATPAK:  # the portal can't be asked, so remember what was chosen
        return bool(config["autostart"])
    return AUTOSTART_PATH.exists()


def set_autostart(enabled: bool, config=None) -> None:
    """config: needed in a Flatpak, where the choice is remembered there."""
    if IN_FLATPAK:
        config["autostart"] = enabled
        request_background(enabled)
    elif enabled:
        _write_if_changed(AUTOSTART_PATH, _desktop_entry(background=True))
    else:
        AUTOSTART_PATH.unlink(missing_ok=True)


def request_background(autostart: bool) -> None:
    """In a Flatpak: ask to keep running without a window, and whether to start at login."""
    from .i18n import _
    options = {"reason": GLib.Variant("s", _("Navcal shows reminders while its window is closed")),
               "autostart": GLib.Variant("b", autostart),
               "commandline": GLib.Variant("as", ["navcal", f"--{BACKGROUND_OPTION}"])}
    try:
        Gio.bus_get_sync(Gio.BusType.SESSION).call(
            *PORTAL, "org.freedesktop.portal.Background", "RequestBackground",
            GLib.Variant("(sa{sv})", ("", options)), None, Gio.DBusCallFlags.NONE, -1, None, None)
    except GLib.Error:
        pass  # no portal: nothing to ask
