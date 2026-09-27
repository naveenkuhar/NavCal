# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""GNOME Shell search provider: Navcal events in the Activities overview search.

GNOME Shell only reads search providers from system folders, so this needs a
one-time install of search-provider.ini (see tools/install-search-provider.sh).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Callable

from gi.repository import Gio, GLib

from .draw import fmt_date_time
from .i18n import _

if TYPE_CHECKING:
    from .application import Application

OBJECT_PATH_SUFFIX = "/SearchProvider"
MAX_RESULTS = 8
MAX_KEPT = 200  # remembered results, for metas and activation
LOAD_WAIT_MS = 3000  # wait this long for calendars when started just for a search

INTERFACE = """
<node>
  <interface name="org.gnome.Shell.SearchProvider2">
    <method name="GetInitialResultSet">
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/>
    </method>
    <method name="GetSubsearchResultSet">
      <arg type="as" name="previous_results" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="as" name="results" direction="out"/>
    </method>
    <method name="GetResultMetas">
      <arg type="as" name="identifiers" direction="in"/>
      <arg type="aa{sv}" name="metas" direction="out"/>
    </method>
    <method name="ActivateResult">
      <arg type="s" name="identifier" direction="in"/>
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="LaunchSearch">
      <arg type="as" name="terms" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
  </interface>
</node>
"""


class SearchProvider:
    def __init__(self, app: Application):
        self.app = app
        self._results = {}  # id -> Occurrence, from the latest searches
        self._registration = None

    def register(self, connection: Gio.DBusConnection, app_path: str) -> None:
        info = Gio.DBusNodeInfo.new_for_xml(INTERFACE).interfaces[0]
        self._registration = connection.register_object(
            app_path + OBJECT_PATH_SUFFIX, info, self._on_call, None, None)

    def unregister(self, connection: Gio.DBusConnection) -> None:
        if self._registration:
            connection.unregister_object(self._registration)
            self._registration = None

    # -- D-Bus --------------------------------------------------------------------

    def _on_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        args = params.unpack()
        if method == "GetInitialResultSet":
            self._when_loaded(lambda: invocation.return_value(
                GLib.Variant("(as)", (self._search(args[0]),))))
        elif method == "GetSubsearchResultSet":
            self._when_loaded(lambda: invocation.return_value(
                GLib.Variant("(as)", (self._search(args[1]),))))
        elif method == "GetResultMetas":
            invocation.return_value(GLib.Variant("(aa{sv})", (self._metas(args[0]),)))
        elif method == "ActivateResult":
            self._activate(args[0], args[1])
            invocation.return_value(None)
        elif method == "LaunchSearch":
            self.app.show_search(" ".join(args[0]))
            invocation.return_value(None)

    def _when_loaded(self, reply: Callable[[], None]) -> None:
        """Answer once calendars have loaded (the Shell may have just started us)."""
        backend = self.app.backend
        if not backend.busy:
            reply()
            return
        waited = [0]

        def check():
            waited[0] += 100
            if backend.busy and waited[0] < LOAD_WAIT_MS:
                return GLib.SOURCE_CONTINUE
            reply()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, check)

    def _search(self, terms: list[str]) -> list[str]:
        self.app.hold()  # keep the service alive a little while it's being used
        GLib.timeout_add_seconds(10, lambda: self.app.release() or False)
        ids = []
        if len(self._results) > MAX_KEPT:  # results of older searches aren't needed
            self._results.clear()
        for occ in self.app.backend.search(" ".join(terms), datetime.now(), MAX_RESULTS):
            ident = "\x1f".join((occ.event.calendar or "", occ.event.uid or "",
                                 occ.start.isoformat()))
            self._results[ident] = occ
            ids.append(ident)
        return ids

    def _metas(self, ids: list[str]) -> list[dict]:
        metas = []
        icon = Gio.ThemedIcon.new("x-office-calendar").to_string()
        for ident in ids:
            occ = self._results.get(ident)
            if occ is None:
                continue
            when = occ.start.strftime(_("%a, %b %-d, %Y"))
            if not occ.is_banner:
                when = fmt_date_time(when, occ.start)
            description = when + (f" · {occ.event.location}" if occ.event.location else "")
            metas.append({"id": GLib.Variant("s", ident),
                          "name": GLib.Variant("s", occ.event.title),
                          "description": GLib.Variant("s", description),
                          "gicon": GLib.Variant("s", icon)})
        return metas

    def _activate(self, ident: str, terms: list[str]) -> None:
        occ = self._results.get(ident)
        self.app.activate()
        if occ is not None:
            self.app.window._show_occurrence(occ)
        else:
            self.app.show_search(" ".join(terms))
