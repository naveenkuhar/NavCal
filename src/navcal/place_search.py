# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Address suggestions under an entry row while typing (GTK has no completion
popover for entry rows)."""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import places
from .i18n import _
from .places import Place

TYPING_PAUSE_MS = 350  # search once typing stops for this long
MIN_CHARS = 3


class PlaceSearch:
    """Shows places matching an entry row's text; picking one calls on_pick."""

    def __init__(self, entry: Adw.EntryRow, on_pick: Callable[[Place], None],
                 near: Callable[[], tuple[float, float] | None] = lambda: None,
                 enabled: Callable[[], bool] = lambda: True):
        self.entry, self.on_pick, self.near, self.enabled = entry, on_pick, near, enabled
        self._timer = 0
        self._cancellable: Gio.Cancellable | None = None
        self._quiet = False  # the text is being set by us, not typed

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["navigation-sidebar"])
        self.list.connect("row-activated", self._on_activated)
        self.list.update_property([Gtk.AccessibleProperty.LABEL], [_("Suggested places")])
        credit = Gtk.Label(label=_("Places from OpenStreetMap contributors"), xalign=0, margin_start=12,
                           margin_end=12, margin_top=6, margin_bottom=6, css_classes=["dim-label", "caption"])
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Gtk.ScrolledWindow(child=self.list, propagate_natural_height=True, max_content_height=320,
                                      hscrollbar_policy=Gtk.PolicyType.NEVER))
        box.append(credit)
        self.popover = Gtk.Popover(child=box, has_arrow=False, autohide=False, position=Gtk.PositionType.BOTTOM)
        self.popover.set_parent(entry)
        entry.connect("destroy", lambda *_args: self.popover.unparent())
        entry.connect("unmap", lambda *_args: self.popover.popdown())
        entry.connect("changed", lambda *_args: self._on_changed())

        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_entry_key)
        entry.add_controller(keys)
        list_keys = Gtk.EventControllerKey()
        list_keys.connect("key-pressed", self._on_list_key)
        self.popover.add_controller(list_keys)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_args: GLib.timeout_add(150, self._close_if_unfocused))
        entry.add_controller(focus)

    def set_text(self, text: str) -> None:
        """Change the text without searching for it."""
        self._quiet = True
        self.entry.set_text(text)
        self._quiet = False

    def close(self) -> None:
        self.popover.popdown()

    def _on_changed(self) -> None:
        if self._quiet:
            return
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        text = self.entry.get_text().strip()
        if len(text) < MIN_CHARS or not self.enabled():
            self.close()
            return
        self._timer = GLib.timeout_add(TYPING_PAUSE_MS, self._search, text)

    def _search(self, text: str) -> bool:
        self._timer = 0
        if self._cancellable:
            self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()
        places.search(text, self.near(), self._show, self._cancellable)
        return GLib.SOURCE_REMOVE

    def _show(self, results: list[Place] | None) -> None:
        results = results or []  # None: the search failed
        self.list.remove_all()
        for place in results:
            row = Adw.ActionRow(title=place.name, subtitle=place.address, activatable=True, use_markup=False,
                                title_lines=1, subtitle_lines=1)
            row.add_prefix(Gtk.Image(icon_name="mark-location-symbolic"))
            row.place = place
            self.list.append(row)
        typing = self.entry.has_focus() or self.entry.get_focus_child() is not None
        if results and typing:
            self.popover.set_size_request(max(self.entry.get_width(), 240), -1)
            self.popover.popup()
        else:
            self.close()

    def _on_activated(self, _list, row) -> None:
        self.close()
        self.set_text(row.place.label)
        self.entry.grab_focus()
        self.entry.set_position(-1)
        self.on_pick(row.place)

    def _on_entry_key(self, _controller, keyval, _code, _state) -> bool:
        if not self.popover.get_visible():
            return False
        if keyval in (Gdk.KEY_Down, Gdk.KEY_KP_Down):
            first = self.list.get_row_at_index(0)
            if first:
                first.grab_focus()
            return True
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True
        return False

    def _on_list_key(self, _controller, keyval, _code, _state) -> bool:
        if keyval == Gdk.KEY_Escape:
            self.close()
            self.entry.grab_focus()
            return True
        first = self.list.get_row_at_index(0)
        if keyval in (Gdk.KEY_Up, Gdk.KEY_KP_Up) and first and first.has_focus():
            self.entry.grab_focus()  # back to typing
            return True
        return False

    def _close_if_unfocused(self) -> bool:
        focus = self.entry.get_root().get_focus() if self.entry.get_root() else None
        inside = focus is not None and (focus.is_ancestor(self.popover) or focus.is_ancestor(self.entry))
        if not inside:
            self.close()
        return GLib.SOURCE_REMOVE
