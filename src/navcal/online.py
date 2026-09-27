# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Small web requests (place search, opening hours, weather), made in the
background on the main loop. Nothing is sent unless the user turned the online
features on in Preferences (they are on by default)."""

from __future__ import annotations

import json
from typing import Callable
from urllib.parse import urlencode

import gi

gi.require_version("Soup", "3.0")
from gi.repository import Gio, GLib, Soup  # noqa: E402

from . import VERSION  # noqa: E402

# The services ask apps to name themselves.
USER_AGENT = f"Navcal/{VERSION} (GNOME calendar app)"
TIMEOUT_S = 15

_session: Soup.Session | None = None


def session() -> Soup.Session:
    global _session
    if _session is None:
        _session = Soup.Session(user_agent=USER_AGENT, timeout=TIMEOUT_S)
    return _session


def get_json(url: str, params: dict, done: Callable[[object | None], None],
             cancellable=None) -> None:
    """Fetch url?params and call done with the decoded JSON, or None if it failed."""
    message = Soup.Message.new("GET", f"{url}?{urlencode(params)}")

    def finished(sess, result):
        try:
            data = sess.send_and_read_finish(result).get_data()
        except GLib.Error as e:
            if not e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):  # not on purpose
                print(f"Navcal: {url} failed: {e.message}")
            done(None)
            return
        if message.get_status() != 200:
            print(f"Navcal: {url} answered {message.get_status()}")
            done(None)
            return
        try:
            done(json.loads(data))
        except ValueError:
            done(None)

    session().send_and_read_async(message, GLib.PRIORITY_DEFAULT, cancellable, finished)
