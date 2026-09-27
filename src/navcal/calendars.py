# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Managing calendars: the Preferences page, adding calendars, import/export."""

from __future__ import annotations

from typing import Callable

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .eds import Backend, Calendar
from .i18n import _, ngettext
from .models import EVENT_COLORS


def _rgba(hex_color: str) -> Gdk.RGBA:
    rgba = Gdk.RGBA()
    rgba.parse(hex_color)
    return rgba


def _hex(rgba: Gdk.RGBA) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(round(c * 255) for c in (rgba.red, rgba.green, rgba.blue)))


def open_online_accounts() -> None:
    """GNOME Settings → Online Accounts (Google, Microsoft 365, Nextcloud, CalDAV…).

    Asks GNOME Settings over D-Bus, which also works from a Flatpak; the command
    line is the fallback for older GNOME versions.
    """
    def done(bus, result):
        try:
            bus.call_finish(result)
        except GLib.Error:
            try:
                Gio.AppInfo.create_from_commandline("gnome-control-center online-accounts", None,
                                                    Gio.AppInfoCreateFlags.NONE).launch([], None)
            except GLib.Error:
                pass

    panel = GLib.Variant("(sav)", ("online-accounts", []))
    Gio.bus_get_sync(Gio.BusType.SESSION).call(
        "org.gnome.Settings", "/org/gnome/Settings", "org.freedesktop.Application", "ActivateAction",
        GLib.Variant("(sava{sv})", ("launch-panel", [panel], {})), None, Gio.DBusCallFlags.NONE, -1,
        None, done)


def next_color(backend: Backend) -> str:
    used = {c.color for c in backend.calendars.values()}
    return next((c for _name, c in EVENT_COLORS if c not in used), EVENT_COLORS[0][1])


class CalendarsPage(Adw.PreferencesPage):
    """Every calendar grouped by account, with color, visibility and removal,
    plus adding calendars and importing or exporting events."""

    def __init__(self, backend: Backend, window: Gtk.Window, toast: Callable[[str], None]):
        super().__init__(title=_("Calendars"), icon_name="x-office-calendar-symbolic",
                         name="calendars")
        self.backend = backend
        self.window = window
        self._groups: list[Adw.PreferencesGroup] = []
        # Follow calendar changes only while shown: a page that is never shown (or
        # whose dialog is closed) must not stay connected, rebuilding itself forever.
        self._handler: int | None = None
        self.connect("map", lambda *_args: self._follow_changes(True))
        self.connect("unmap", lambda *_args: self._follow_changes(False))

        add = Adw.PreferencesGroup(title=_("Add Calendars"))
        for title, icon, handler in [
            (_("_Online Accounts…"), "system-users-symbolic", lambda *_args: open_online_accounts()),
            (_("_New Calendar…"), "list-add-symbolic", lambda *_args: new_calendar_dialog(backend, self)),
            (_("_Subscribe to Calendar Link…"), "emblem-shared-symbolic",
             lambda *_args: subscribe_dialog(backend, self)),
            (_("Add Public _Holidays…"), "x-office-calendar-symbolic",
             lambda *_args: holidays_dialog(backend, self)),
        ]:
            row = Adw.ButtonRow(title=title, use_underline=True, start_icon_name=icon)
            row.connect("activated", handler)
            add.add(row)
        add.set_description(_("Google, Microsoft 365, Exchange, Nextcloud and iCloud or other "
                              "CalDAV servers are added in Online Accounts and appear here "
                              "automatically."))
        files = Adw.PreferencesGroup(title=_("Import and Export"))
        files.set_description(_("Exporting all calendars makes a backup you can import later."))
        for title, icon, handler in [
            (_("_Import Events…"), "document-open-symbolic",
             lambda *_args: import_flow(backend, self, window.default_calendar(), toast)),
            (_("_Export Events…"), "document-save-symbolic",
             lambda *_args: export_flow(backend, self, toast)),
        ]:
            row = Adw.ButtonRow(title=title, use_underline=True, start_icon_name=icon)
            row.connect("activated", handler)
            files.add(row)
        self._fixed_groups = [add, files]
        self._rebuild()

    def _follow_changes(self, follow: bool) -> None:
        if follow and self._handler is None:
            self._handler = self.backend.connect("calendars-changed", lambda *_args: self._rebuild())
            self._rebuild()  # catch up on changes made while hidden
        elif not follow and self._handler is not None:
            self.backend.disconnect(self._handler)
            self._handler = None

    def _rebuild(self) -> None:
        for group in self._groups:
            self.remove(group)
        self._groups.clear()
        for group in self._fixed_groups:
            if group.get_parent():
                self.remove(group)

        by_account: dict[str, list[Calendar]] = {}
        for cal in self.backend.sorted_calendars():
            by_account.setdefault(cal.account or _("On This Computer"), []).append(cal)
        for account, cals in by_account.items():
            group = Adw.PreferencesGroup(title=GLib.markup_escape_text(account))
            for cal in cals:
                group.add(self._row(cal))
            self.add(group)
            self._groups.append(group)
        for group in self._fixed_groups:
            self.add(group)

    def _row(self, cal: Calendar) -> Adw.ActionRow:
        status = cal.error or (_("Loading…") if not cal.loaded else _("Read-only") if cal.readonly else "")
        row = Adw.ActionRow(title=cal.name, subtitle=status, use_markup=False)
        color = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False),
                                      rgba=_rgba(cal.color), valign=Gtk.Align.CENTER,
                                      tooltip_text=_("Calendar color"))
        color.connect("notify::rgba", lambda b, _p: self.backend.set_color(cal, _hex(b.get_rgba())))
        row.add_prefix(color)
        if cal.removable and cal.backend_name in ("local", "webcal"):
            remove = Gtk.Button(icon_name="user-trash-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text=_("Remove calendar"), css_classes=["flat"])
            remove.connect("clicked", lambda _b: self._confirm_remove(cal))
            row.add_suffix(remove)
        switch = Gtk.Switch(active=cal.visible, valign=Gtk.Align.CENTER,
                            tooltip_text=_("Show in Navcal and the top bar"))
        switch.connect("notify::active", lambda s, _p: self.backend.set_visible(cal, s.get_active()))
        row.add_suffix(switch)
        row.set_activatable_widget(switch)
        return row

    def _confirm_remove(self, cal: Calendar) -> None:
        local = cal.backend_name == "local"
        dialog = Adw.AlertDialog(
            heading=_("Remove Calendar?"),
            body=(_("“{name}” and all its events will be permanently deleted.") if local else
                  _("You will no longer be subscribed to “{name}”.")).format(name=cal.name))
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("remove", _("_Remove"))
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: r == "remove" and self.backend.remove_calendar(cal))
        dialog.present(self)


def _entry_dialog(parent: Gtk.Widget, heading: str, body: str, fields: list[tuple[str, str]],
                  action: str, on_done: Callable[[list[str]], str | None]) -> None:
    """An alert dialog with entry rows; on_done returns an error message or None."""
    dialog = Adw.AlertDialog(heading=heading, body=body)
    box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
    rows = []
    for title, text in fields:
        row = Adw.EntryRow(title=title, text=text, use_underline=True)
        box.append(row)
        rows.append(row)
    dialog.set_extra_child(box)
    dialog.add_response("cancel", _("_Cancel"))
    dialog.add_response("ok", action)
    dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("ok")
    dialog.set_close_response("cancel")

    def validate(*_args):
        dialog.set_response_enabled("ok", all(r.get_text().strip() for r in rows))

    for row in rows:
        row.connect("changed", validate)
    validate()

    def on_response(_d, response):
        if response == "ok":
            error = on_done([r.get_text().strip() for r in rows])
            if error:
                show_error(parent, error)

    dialog.connect("response", on_response)
    dialog.present(parent)
    rows[0].grab_focus()


def show_error(parent: Gtk.Widget, message: str, heading: str | None = None) -> None:
    dialog = Adw.AlertDialog(heading=heading or _("Something Went Wrong"), body=message)
    dialog.add_response("close", _("_Close"))
    dialog.present(parent)


def new_calendar_dialog(backend: Backend, parent: Gtk.Widget) -> None:
    def create(values):
        try:
            backend.add_local_calendar(values[0], next_color(backend))
        except GLib.Error as e:
            return e.message
        return None

    _entry_dialog(parent, _("New Calendar"), _("The calendar is stored on this computer."),
                  [(_("_Name"), "")], _("_Create"), create)


def subscribe_dialog(backend: Backend, parent: Gtk.Widget) -> None:
    def subscribe(values):
        url, name = values
        if not url.startswith(("http://", "https://", "webcal://")):
            return _("Calendar links start with https:// or webcal://")
        try:
            backend.subscribe(url, name, next_color(backend))
        except GLib.Error as e:
            return e.message
        return None

    _entry_dialog(parent, _("Subscribe to Calendar"),
                  _("Add a read-only calendar from a link, such as public holidays or a sports "
                    "schedule. It’s refreshed every hour."),
                  [(_("_Link"), ""), (_("_Name"), "")], _("_Subscribe"), subscribe)


def holidays_dialog(backend: Backend, parent: Gtk.Widget) -> None:
    from .holidays import HOLIDAYS, calendar_name, default_index, feed_url

    row = Adw.ComboRow(title=_("_Country or region"), use_underline=True, enable_search=True,
                       model=Gtk.StringList.new([_(name) for name, _id, _codes in HOLIDAYS]),
                       expression=Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
    row.set_selected(default_index())
    box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
    box.append(row)
    dialog = Adw.AlertDialog(
        heading=_("Add Public Holidays"),
        body=_("Holidays are added as a read-only calendar, using Google’s public holiday "
               "feed. No account is needed."))
    dialog.set_extra_child(box)
    dialog.add_response("cancel", _("_Cancel"))
    dialog.add_response("add", _("_Add"))
    dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("add")
    dialog.set_close_response("cancel")

    def on_response(_d, response):
        if response != "add":
            return
        name, calendar_id, _codes = HOLIDAYS[row.get_selected()]
        try:
            backend.subscribe(feed_url(calendar_id), calendar_name(name), next_color(backend))
        except GLib.Error as e:
            show_error(parent, e.message, _("Couldn’t Add Holidays"))

    dialog.connect("response", on_response)
    dialog.present(parent)


def _calendar_picker(choices: list[tuple[str, str]], selected: int = 0) -> Adw.ComboRow:
    row = Adw.ComboRow(title=_("_Calendar"), use_underline=True,
                       model=Gtk.StringList.new([n for _uid, n in choices]))
    row.set_selected(selected)
    box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
    box.append(row)
    row.box = box
    return row


def import_flow(backend: Backend, parent: Gtk.Window, default_uid: str | None,
                on_done: Callable[[str], None]) -> None:
    """Pick an .ics file, pick a calendar, import."""
    filters = Gio.ListStore.new(Gtk.FileFilter)
    ics = Gtk.FileFilter(name=_("Calendar Files"))
    ics.add_pattern("*.ics")
    ics.add_mime_type("text/calendar")
    filters.append(ics)
    file_dialog = Gtk.FileDialog(title=_("Import Events"), filters=filters)

    def on_file(dialog, result):
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return  # cancelled
        try:
            text = file.load_contents(None)[1].decode("utf-8", "replace")
        except GLib.Error as e:
            show_error(parent, e.message, _("Couldn’t Open File"))
            return
        writable = backend.writable_calendars()
        if not writable:
            show_error(parent, _("There’s no calendar you can add events to. Create one first."))
            return
        uids = [c.uid for c in writable]
        picker = _calendar_picker([(c.uid, c.name) for c in writable],
                                  uids.index(default_uid) if default_uid in uids else 0)
        alert = Adw.AlertDialog(heading=_("Import Events?"),
                                body=_("Choose a calendar for the events in “{file}”.").format(
                                    file=file.get_basename()))
        alert.set_extra_child(picker.box)
        alert.add_response("cancel", _("_Cancel"))
        alert.add_response("import", _("_Import"))
        alert.set_response_appearance("import", Adw.ResponseAppearance.SUGGESTED)
        alert.set_close_response("cancel")

        def on_response(_d, response):
            if response != "import":
                return
            try:
                op = backend.plan_import(uids[picker.get_selected()], text)
            except ValueError:
                show_error(parent, _("“{file}” isn’t a calendar file.").format(file=file.get_basename()),
                           _("Couldn’t Import Events"))
                return

            def imported(count, error):
                if error is not None:
                    show_error(parent, error.message, _("Couldn’t Import Events"))
                else:
                    on_done(ngettext("Imported {n} event", "Imported {n} events", count).format(n=count))

            backend.run(op, imported)

        alert.connect("response", on_response)
        alert.present(parent)

    file_dialog.open(parent, None, on_file)


def export_flow(backend: Backend, parent: Gtk.Window, on_done: Callable[[str], None]) -> None:
    """Pick calendars, pick a file, export. "All Calendars" doubles as a backup."""
    cals = backend.sorted_calendars()
    picker = _calendar_picker([("", _("All calendars (backup)"))] + [(c.uid, c.name) for c in cals])
    alert = Adw.AlertDialog(heading=_("Export Events?"),
                            body=_("Events are saved as an .ics file that other calendar apps "
                                   "can open."))
    alert.set_extra_child(picker.box)
    alert.add_response("cancel", _("_Cancel"))
    alert.add_response("export", _("_Export…"))
    alert.set_response_appearance("export", Adw.ResponseAppearance.SUGGESTED)
    alert.set_close_response("cancel")

    def on_response(_d, response):
        if response != "export":
            return
        index = picker.get_selected()
        chosen = cals if index == 0 else [cals[index - 1]]
        name = _("Navcal Backup") if index == 0 else chosen[0].name
        file_dialog = Gtk.FileDialog(title=_("Export Events"),
                                     initial_name=f"{GLib.filename_display_name(name)}.ics")

        def on_file(dialog, result):
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return
            text = backend.export_text(chosen)
            try:
                file.replace_contents(text.encode(), None, False, Gio.FileCreateFlags.NONE, None)
            except GLib.Error as e:
                show_error(parent, e.message, _("Couldn’t Save File"))
                return
            on_done(_("Exported to “{file}”").format(file=file.get_basename()))

        file_dialog.save(parent, None, on_file)

    alert.connect("response", on_response)
    alert.present(parent)
