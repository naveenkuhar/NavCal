"""The Adw.Application: app-wide actions, dialogs, background mode."""

from __future__ import annotations

import sys
from datetime import date

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, GObject, Gtk  # noqa: E402

from . import resources  # noqa: E402,F401  (must come before the UI modules)
from . import desktop, style  # noqa: E402
from . import ical  # noqa: E402
from .config import Config  # noqa: E402
from .i18n import _, ngettext  # noqa: E402
from .draw import fmt_hour  # noqa: E402
from .editor import REMINDER_CHOICES  # noqa: E402
from .reminders import ReminderService  # noqa: E402
from .calendars import CalendarsPage  # noqa: E402
from .dock import DockPage  # noqa: E402
from .place_search import PlaceSearch  # noqa: E402
from . import places  # noqa: E402
from .eds import Backend  # noqa: E402
from .legacy import legacy_db_path, load_legacy_events  # noqa: E402
from .resources import RESOURCE_BASE  # noqa: E402
from .search_provider import SearchProvider  # noqa: E402
from .window import VIEWS, Window  # noqa: E402

from . import VERSION  # noqa: E402,F401  (shown in About)


class Application(Adw.Application):
    def __init__(self):
        super().__init__(application_id=desktop.APP_ID,
                         flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.add_main_option(desktop.BACKGROUND_OPTION, 0, GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE, _("Start hidden, only showing reminders"), None)
        self.window: Window | None = None
        self._start_hidden = False
        self._held = False
        self.search_provider = SearchProvider(self)

    # -- lifecycle ---------------------------------------------------------------

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        self._start_hidden = options.contains(desktop.BACKGROUND_OPTION)
        return -1  # continue normal startup

    def do_dbus_register(self, connection, object_path) -> bool:
        Adw.Application.do_dbus_register(self, connection, object_path)
        self.search_provider.register(connection, object_path)
        return True

    def do_dbus_unregister(self, connection, object_path) -> None:
        self.search_provider.unregister(connection)
        Adw.Application.do_dbus_unregister(self, connection, object_path)

    def show_search(self, text: str) -> None:
        """Open the window with a search (from GNOME Shell)."""
        self.activate()
        self.window.search_bar.set_search_mode(True)
        self.window.search_entry.set_text(text)

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name("Navcal")
        style.install()
        desktop.install()
        self.config = Config()
        self.backend = Backend()
        self._migrated_count = self._migrate_legacy_events()
        self.reminders = ReminderService(self, self.backend, self.config)
        self._install_actions()

    def do_activate(self):
        if self.window is None:
            self.window = Window(self, self.backend, self.config)
            self.window.connect("close-request", self._on_close_request)
            self._apply_background_setting()
            if self._migrated_count:
                self.window.toast(ngettext("Moved {n} event to the “Navcal” calendar",
                                           "Moved {n} events to the “Navcal” calendar",
                                           self._migrated_count).format(n=self._migrated_count))
            if self._start_hidden:
                self._start_hidden = False
                return
        self.window.present()

    def do_shutdown(self):
        if self.window:
            self.window.save_state()
        self.reminders.save()
        Adw.Application.do_shutdown(self)

    def _migrate_legacy_events(self) -> int:
        """Move events from the old SQLite database into a "Navcal" calendar (once)."""
        path = legacy_db_path()
        if self.config["migrated"] or not path.exists():
            return 0
        events = load_legacy_events(path)
        if events:
            # Reuse the calendar made by an earlier attempt that couldn't finish.
            uid = self.config["migration-calendar"]
            if not uid or self.backend.calendar(uid) is None:
                uid = self.backend.add_local_calendar("Navcal", "#3584e4")
                self.config["migration-calendar"] = uid
            if self.backend.connect_calendar_sync(uid) is None:
                return 0  # try again next start
            for ev in events:
                ev.calendar = uid
                self.backend.create(uid, ev)
            self.config["default-calendar"] = uid
        path.rename(path.with_name(path.name + ".backup"))
        self.config["migrated"] = True
        return len(events)

    def _on_close_request(self, window: Window) -> bool:
        window.save_state()
        if self.config["run-in-background"] and not self.config["told-background"]:
            self.config["told-background"] = True
            note = Gio.Notification.new(_("Navcal Is Running in the Background"))
            note.set_body(_("Reminders will keep appearing. Open Navcal again to see your "
                            "calendar, or press Ctrl+Q in Navcal to quit."))
            self.send_notification("background", note)
        return False  # hide (background) or close (quit), per hide-on-close

    def _apply_background_setting(self) -> None:
        """Keep the app alive with the window hidden, if the user wants that."""
        background = self.config["run-in-background"]
        if self.window:
            self.window.set_hide_on_close(background)
        if background and not self._held:
            self.hold()
            self._held = True
        elif not background and self._held:
            self.release()
            self._held = False

    # -- actions -----------------------------------------------------------------

    def _install_actions(self) -> None:
        for name, handler in [("quit", self.quit), ("about", self._show_about),
                              ("preferences", self._show_preferences),
                              ("shortcuts", self._show_shortcuts)]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            self.add_action(action)

        show_date = Gio.SimpleAction.new("show-date", GLib.VariantType.new("s"))
        show_date.connect("activate", self._on_show_date)
        self.add_action(show_date)
        snooze = Gio.SimpleAction.new("snooze", GLib.VariantType.new("s"))
        snooze.connect("activate", lambda _a, p: self.reminders.snooze(p.get_string()))
        self.add_action(snooze)
        for name, handler in [("calendars", lambda: self._show_preferences("calendars")),
                              ("customize-dock", lambda: self._show_preferences("dock"))]:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            self.add_action(action)

        accels = {
            "app.quit": ["<Control>q"],
            "app.preferences": ["<Control>comma"],
            "app.shortcuts": ["<Control>question"],
            "win.close": ["<Control>w"],
            "win.search": ["<Control>f"],
            "win.jump": ["<Control>g"],
            "win.print": ["<Control>p"],
            "win.new-event": ["<Control>n"],
            "win.today": ["<Control>t"],
            "win.previous": ["<Alt>Left", "<Control>Page_Up"],
            "win.next": ["<Alt>Right", "<Control>Page_Down"],
            "win.zoom-in": ["<Control>plus", "<Control>equal", "<Control>KP_Add"],
            "win.zoom-out": ["<Control>minus", "<Control>KP_Subtract"],
            "win.zoom-reset": ["<Control>0", "<Control>KP_0"],
        }
        accels.update({f"win.view-{name}": [accel] for name, _icon, accel in VIEWS})
        # Copy, paste, delete, undo and redo are bound in the window, only while
        # the calendar has focus (see window.EVENT_SHORTCUTS).
        for action, keys in accels.items():
            self.set_accels_for_action(action, keys)

    def _on_show_date(self, _action, param) -> None:
        self.activate()
        self.window.open_day(date.fromisoformat(param.get_string()))

    def _show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name="Navcal",
            application_icon=desktop.APP_ID,
            version=VERSION,
            comments=_("Plan your days and get reminders."),
            translator_credits=_("translator-credits"),
            developers=[_("Navcal contributors")],
        )
        about.present(self.window)

    def _show_preferences(self, page_name: str | None = None) -> None:
        self.activate()
        builder = Gtk.Builder.new_from_resource(f"{RESOURCE_BASE}/ui/preferences.ui")
        get = builder.get_object
        config, window = self.config, self.window

        def combo(row_id, key, choices, apply=True):
            """Fill a combo row with (label, value) choices bound to a config key."""
            row = get(row_id)
            row.set_model(Gtk.StringList.new([label for label, _value in choices]))
            values = [value for _label, value in choices]
            row.set_selected(values.index(config[key]) if config[key] in values else 0)

            def changed(r, _p):
                config[key] = values[r.get_selected()]
                if apply:
                    window.apply_settings()
            row.connect("notify::selected", changed)

        def switch(row_id, key, apply=True):
            row = get(row_id)
            row.set_active(bool(config[key]))

            def changed(r, _p):
                config[key] = r.get_active()
                if key in ("online", "weather"):
                    window.weather.refresh()
                if apply:
                    window.apply_settings()
            row.connect("notify::active", changed)
            return row

        combo("default_reminder", "default-reminder",
              [(_("None"), -1)] + [(_(label), value) for label, value in REMINDER_CHOICES],
              apply=False)
        combo("default_duration", "default-duration",
              [(_("15 minutes"), 15), (_("30 minutes"), 30), (_("45 minutes"), 45),
               (_("1 hour"), 60), (_("90 minutes"), 90), (_("2 hours"), 120)], apply=False)
        combo("first_weekday", "first-weekday",
              [(_("Use the system setting"), -1), (_("Sunday"), 6), (_("Monday"), 0),
               (_("Saturday"), 5)])
        combo("week_span", "week-span", [(_("A full week"), 7), (_("4 days"), 4), (_("3 days"), 3)])
        switch("hide_weekends", "hide-weekends")
        switch("week_numbers", "week-numbers")
        zones = [(_("None"), "")] + [(ical.zone_label(z), z) for z in ical.all_tzids()]
        combo("second_timezone", "second-timezone", zones)
        get("second_timezone").set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        shade = switch("shade_work_hours", "shade-work-hours")
        hours = [(fmt_hour(h) if h < 24 else fmt_hour(0), h) for h in range(25)]
        combo("work_start", "work-start", hours[:24])
        combo("work_end", "work-end", hours[1:])
        for row_id in ("work_start", "work_end"):
            shade.bind_property("active", get(row_id), "sensitive",
                                GObject.BindingFlags.SYNC_CREATE)

        online = switch("online", "online")
        show_weather = switch("show_weather", "weather")
        place_row = get("weather_place")
        combo("temperature_unit", "temperature-unit",
              [(_("From the region"), "auto"), (_("Celsius (°C)"), "c"), (_("Fahrenheit (°F)"), "f")])
        for row in (show_weather, place_row, get("temperature_unit")):
            online.bind_property("active", row, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        place = config["weather-place"]

        def picked(p) -> None:
            config["weather-place"] = {"name": p.label, "lat": p.lat, "lon": p.lon}
            window.weather.refresh()

        search = PlaceSearch(place_row, picked, near=lambda: places.home(config),
                             enabled=lambda: config["online"])
        search.set_text(place["name"] if place else "")

        background = get("run_in_background")
        background.set_active(config["run-in-background"])
        background.connect("notify::active", self._on_background_toggled)
        autostart = get("autostart")
        autostart.set_active(desktop.autostart_enabled())
        autostart.connect("notify::active", lambda row, _p: desktop.set_autostart(row.get_active()))

        dialog = get("dialog")
        dialog.add(CalendarsPage(self.backend, window, window.toast))
        dialog.add(DockPage(window))
        if page_name:
            dialog.set_visible_page_name(page_name)
        dialog.present(window)

    def _on_background_toggled(self, row, _pspec) -> None:
        self.config["run-in-background"] = row.get_active()
        self._apply_background_setting()

    def _show_shortcuts(self) -> None:
        builder = Gtk.Builder.new_from_resource(f"{RESOURCE_BASE}/ui/shortcuts-dialog.ui")
        builder.get_object("shortcuts_dialog").present(self.window)


def main() -> int:
    return Application().run(sys.argv)
