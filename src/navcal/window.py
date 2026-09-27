# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""The main window: header bar, sidebar, the views and all event actions."""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta

from . import resources  # noqa: F401  (registers the UI templates)
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from .config import Config  # noqa: E402
from . import ical, worldclock  # noqa: E402
from .draw import Theme, fmt_date_time, fmt_time, set_first_weekday, spoken_event  # noqa: E402
from .editor import CalendarChoice, EventEditor, ask_scope  # noqa: E402
from .eds import Backend  # noqa: E402
from .history import History  # noqa: E402
from .models import (Event, Freq, Occurrence, Scope, Task, days_covered, overlaps,  # noqa: E402
                     sort_occurrences)
from . import printing  # noqa: E402
from .print_dialog import PrintDialog  # noqa: E402
from .i18n import _, ngettext  # noqa: E402
from .quickadd import QuickEvent, parse as quick_parse  # noqa: E402
from .resources import RESOURCE_BASE  # noqa: E402
from .style import color_css_class  # noqa: E402
from .tasks import TaskDialog, task_row  # noqa: E402
from .views import AgendaView, MonthView, TimeGridView, YearView  # noqa: E402
from .views.canvas import DragLayer  # noqa: E402
from .dock import Dock  # noqa: E402
from .weather import WeatherService  # noqa: E402
from . import places, weather  # noqa: E402
from .views.timegrid import ZOOM_STEP  # noqa: E402

VIEWS = [  # name, icon, shortcut
    ("day", "view-paged-symbolic", "<Control>1"),
    ("week", "view-continuous-symbolic", "<Control>2"),
    ("month", "view-grid-symbolic", "<Control>3"),
    ("year", "view-app-grid-symbolic", "<Control>4"),
    ("agenda", "view-list-symbolic", "<Control>5"),
]
UPCOMING_DAYS = 14
SIDEBAR_ROWS = 5  # upcoming events and open tasks shown before "Show More"
LOCAL_BACKENDS = ("local", "contacts", "weather")
DROP_WAIT_MS = 5000  # longest to show a saved drop the calendar hasn’t reported yet

# Event shortcuts that would clash with text editing (copy, paste, delete,
# undo, select all) only apply while the calendar has focus, not in dialogs.
EVENT_SHORTCUTS = [
    ("<Control>c", "win.copy"), ("<Control>x", "win.cut"), ("<Control>v", "win.paste"),
    ("<Control>d", "win.duplicate"), ("<Control>a", "win.select-all"),
    ("<Control>z", "win.undo"), ("<Shift><Control>z", "win.redo"),
    ("Delete", "win.delete"), ("KP_Delete", "win.delete"), ("BackSpace", "win.delete"),
    ("Return", "win.open"), ("KP_Enter", "win.open"), ("Escape", "win.select-none"),
]


def as_date(target: date | datetime) -> date:
    return target.date() if isinstance(target, datetime) else target


def relative_day(d: date) -> str:
    today = date.today()
    if d == today:
        return _("Today")
    if d == today + timedelta(days=1):
        return _("Tomorrow")
    if today < d < today + timedelta(days=7):
        return d.strftime("%A")
    return d.strftime(_("%a, %b %-d"))


def describe_occurrence(occ: Occurrence, calendar_name: str = "") -> str:
    """A spoken description for screen readers."""
    text = f"{spoken_event(occ)}, {occ.start.strftime(_('%A, %B %-d'))}"
    if calendar_name:
        text += ", " + _("{name} calendar").format(name=calendar_name)
    return text


def month_first() -> bool:
    """Does the locale write dates month first (9/30) rather than day first (30/9)?"""
    import locale
    try:
        fmt = locale.nl_langinfo(locale.D_FMT)
    except (AttributeError, ValueError):
        return True
    return fmt.find("%m") < fmt.find("%d")


def describe_when(start: datetime, end: datetime, all_day: bool) -> str:
    day = start.strftime(_("%a, %b %-d"))
    if all_day:
        days = (end.date() - start.date()).days
        if days <= 1:
            return _("{day}, all day").format(day=day)
        last = (end - timedelta(days=1)).strftime(_("%a, %b %-d"))
        return _("{first} – {last}").format(first=day, last=last)
    same_day = end.date() == start.date() or end == datetime.combine(start.date() + timedelta(days=1), time())
    if same_day:
        return _("{day}, {start} – {end}").format(day=day, start=fmt_time(start), end=fmt_time(end))
    return _("{day}, {start} – {end} ({end_day})").format(
        day=day, start=fmt_time(start), end=fmt_time(end), end_day=end.strftime("%a"))


def describe_repeat(freq: str, interval: int, byday: list) -> str:
    if freq == Freq.WEEKLY and sorted(d for d, _n in byday) == list(range(5)) and interval == 1:
        return _("Repeats every weekday")
    text = {
        Freq.DAILY: ngettext("Repeats every day", "Repeats every {n} days", interval),
        Freq.WEEKLY: ngettext("Repeats every week", "Repeats every {n} weeks", interval),
        Freq.MONTHLY: ngettext("Repeats every month", "Repeats every {n} months", interval),
        Freq.YEARLY: ngettext("Repeats every year", "Repeats every {n} years", interval),
    }[freq].format(n=interval)
    if freq == Freq.WEEKLY and byday:
        names = ", ".join(date(2026, 9, 21 + d).strftime("%a") for d, _n in sorted(byday))
        text = _("{repeats} on {days}").format(repeats=text, days=names)
    return text


def count_label(n: int) -> str:
    return ngettext("{n} event", "{n} events", n).format(n=n)


@dataclass(eq=False)
class Drop:
    """A dragged event, shown where it was dropped until the calendar reports the change."""

    old_key: tuple | None  # the occurrence it replaces; None for a copy
    shown: Occurrence
    saved: bool = False


def one_off(ev: Event) -> Event:
    """ev without a repeat rule: a single event (e.g. a copy of one occurrence)."""
    ev.freq, ev.interval, ev.byday, ev.bymonthday = Freq.NONE, 1, [], None
    ev.count, ev.until, ev.rrule_extra = None, None, ""
    return ev


@Gtk.Template(resource_path=f"{RESOURCE_BASE}/ui/window.ui")
class Window(Adw.ApplicationWindow):
    __gtype_name__ = "NavcalWindow"

    toasts: Adw.ToastOverlay = Gtk.Template.Child()
    split_view: Adw.OverlaySplitView = Gtk.Template.Child()
    calendar: Gtk.Calendar = Gtk.Template.Child()
    upcoming: Gtk.ListBox = Gtk.Template.Child()
    upcoming_empty: Adw.StatusPage = Gtk.Template.Child()
    tasks_section: Gtk.Box = Gtk.Template.Child()
    tasks_list: Gtk.ListBox = Gtk.Template.Child()
    clocks_list: Gtk.ListBox = Gtk.Template.Child()
    today_button: Gtk.Button = Gtk.Template.Child()
    new_event_button: Gtk.MenuButton = Gtk.Template.Child()
    quick_add_popover: Gtk.Popover = Gtk.Template.Child()
    quick_add_entry: Gtk.Entry = Gtk.Template.Child()
    quick_add_preview: Gtk.Label = Gtk.Template.Child()
    quick_edit_button: Gtk.Button = Gtk.Template.Child()
    quick_add_button: Gtk.Button = Gtk.Template.Child()
    search_bar: Gtk.SearchBar = Gtk.Template.Child()
    search_entry: Gtk.SearchEntry = Gtk.Template.Child()
    banner: Adw.Banner = Gtk.Template.Child()
    content_stack: Gtk.Stack = Gtk.Template.Child()
    search_stack: Gtk.Stack = Gtk.Template.Child()
    search_results: Gtk.ListBox = Gtk.Template.Child()
    period_label: Gtk.Label = Gtk.Template.Child()
    year_label: Gtk.Label = Gtk.Template.Child()
    spinner: Adw.Spinner = Gtk.Template.Child()
    stack: Adw.ViewStack = Gtk.Template.Child()
    views_overlay: Gtk.Overlay = Gtk.Template.Child()
    style_probes: Gtk.Box = Gtk.Template.Child()
    calendar_bar: Gtk.ScrolledWindow = Gtk.Template.Child()
    dock_box: Gtk.Box = Gtk.Template.Child()
    narrow_title: Adw.WindowTitle = Gtk.Template.Child()

    def __init__(self, app: Adw.Application, backend: Backend, config: Config):
        super().__init__(application=app)
        self.backend = backend
        self.config = config
        self.theme = Theme(self.style_probes)
        self.drag_layer = DragLayer()  # the copy under the pointer while duplicating
        self.hour_zoom = config["hour-zoom"]  # day and week views; saved a moment after changing
        weather.set_unit(config["temperature-unit"])
        self.weather = WeatherService(config)  # the forecast shown under the days
        # Whether the coming events' places are open then (looked up in the background).
        places.load_cache()
        self.place_checker = places.PlaceChecker(config, self.refresh)
        self._zoom_save = 0
        self.views_overlay.add_overlay(self.drag_layer)
        self.current = date.today()  # the date the visible period is built around
        self.target: date | datetime = self.current  # focused day/slot, for paste & new
        self.selection: list[Occurrence] = []
        self.clipboard: list[tuple[Event, timedelta]] = []  # events and offsets from the first
        self.history = History()
        self._visible_range = None
        self._select_after_refresh: set | None = None
        self._drops: list[Drop] = []  # dragged events waiting for the calendar to save them

        self.set_default_size(config["width"], config["height"])
        if config["maximized"]:
            self.maximize()
        self._apply_first_weekday()

        self.views = {"day": TimeGridView(self, "day"), "week": TimeGridView(self, "week"),
                      "month": MonthView(self), "year": YearView(self), "agenda": AgendaView(self)}
        for name, icon, _accel in VIEWS:
            self.stack.add_titled_with_icon(self.views[name], name, self.views[name].page_title(),
                                            icon)

        self._calendar_handler = self.calendar.connect("day-selected", self._on_calendar_day)
        for prop in ("notify::month", "notify::year"):
            self.calendar.connect(prop, lambda *_args: self._mark_calendar_days())
        self.upcoming.connect("row-activated", self._on_upcoming_activated)
        self.upcoming.set_header_func(self._upcoming_header)
        self.banner.connect("button-clicked",
                            lambda *_args: self.get_application().activate_action("calendars"))

        # Search: Ctrl+F, the search button, or just start typing.
        self.search_bar.set_key_capture_widget(self)
        self.search_bar.connect_entry(self.search_entry)
        self.search_entry.connect("search-changed", lambda *_args: self._update_search())
        self.search_bar.connect("notify::search-mode-enabled", lambda *_args: self._update_search())
        self.search_results.connect("row-activated", self._on_result_activated)

        # Tasks in the sidebar: a "New task" field, then open tasks.
        self._show_done_tasks = False
        self._show_all_upcoming = False
        self._show_all_tasks = False
        self._task_entry = Adw.EntryRow(title=_("New task"), show_apply_button=True,
                                        use_underline=False)
        self._task_entry.connect("apply", lambda e: self.add_task(e.get_text()))
        self._task_entry.connect("entry-activated", lambda e: self.add_task(e.get_text()))
        self.tasks_list.append(self._task_entry)

        # Quick add: type "Lunch with Sam tomorrow 1pm" and press Enter.
        self.quick_add_popover.set_default_widget(self.quick_add_button)
        self.quick_add_popover.connect("show", lambda *_args: self._reset_quick_add())
        self.quick_add_entry.connect("changed", lambda *_args: self._update_quick_add())
        self.quick_add_entry.connect("activate", lambda *_args: self._quick_add())  # Enter
        self.quick_add_button.connect("clicked", lambda *_args: self._quick_add())
        self.quick_edit_button.connect("clicked", lambda *_args: self._quick_edit())

        shortcuts = Gtk.ShortcutController()
        for accel, action in EVENT_SHORTCUTS:
            shortcuts.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(accel),
                                                    Gtk.NamedAction.new(action)))
        self.split_view.add_controller(shortcuts)
        self._install_actions()

        mode = config["view"] if config["view"] in self.views else "month"
        self.stack.set_visible_child_name(mode)
        self.stack.connect("notify::visible-child-name", self._on_view_changed)
        self.calendar_bar.set_visible(config["footbar"])
        self._lists_until = datetime.max  # when the sidebar lists go out of date
        self.dock = Dock(self, self.dock_box)
        self.refresh()
        self._refresh_lists()
        backend.connect("changed", self._on_backend_changed)
        self.weather.connect("changed", lambda *_args: self._on_weather())
        backend.connect("calendars-changed", lambda *_args: (self._update_banner(), self.dock.update()))
        backend.connect("notify::busy", lambda *_args: self.spinner.set_visible(backend.busy))
        self._network = Gio.NetworkMonitor.get_default()
        self._network.connect("notify::network-available", lambda *_args: self._update_banner())
        self.spinner.set_visible(backend.busy)
        self._update_banner()
        self._refresh_clocks()
        GLib.timeout_add_seconds(60, self._tick)

    # -- actions --------------------------------------------------------------------

    def _install_actions(self) -> None:
        simple = {
            "today": self.go_today,
            "previous": lambda: self.step(-1),
            "next": lambda: self.step(1),
            "jump": self.show_jump,
            "search": lambda: self.search_bar.set_search_mode(
                not self.search_bar.get_search_mode()),
            "new-event": self.new_event,
            "open": self.open_target,
            "edit": lambda: self._with_selected(self.edit_occurrence),
            "copy": self.copy,
            "cut": self.cut,
            "paste": lambda: self.paste(self.target),
            "duplicate": self.duplicate,
            "delete": self.delete_selection,
            "select-all": self.select_all,
            "select-none": lambda: self.select(None, None),
            "undo": self.undo,
            "redo": self.redo,
            "print": self.print_checklist,
            "close": self.close,
            "zoom-in": lambda: self._zoom(ZOOM_STEP),
            "zoom-out": lambda: self._zoom(1 / ZOOM_STEP),
            "zoom-reset": lambda: self._zoom(1 / self.hour_zoom),
        }
        for name, fn in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, fn=fn: fn())
            self.add_action(action)
        for name, *_rest in VIEWS:
            action = Gio.SimpleAction.new(f"view-{name}", None)
            action.connect("activate", lambda _a, _p, n=name: self.stack.set_visible_child_name(n))
            self.add_action(action)

    def _zoom(self, factor: float) -> None:
        view = self.view()
        if isinstance(view, TimeGridView):
            view.zoom(factor)

    def set_hour_zoom(self, zoom: float) -> None:
        """Both the day and week view follow; the setting is saved once it stops changing."""
        self.hour_zoom = zoom
        for name in ("day", "week"):
            self.views[name]._redraw()
        if self._zoom_save:
            GLib.source_remove(self._zoom_save)

        def save():
            self._zoom_save = 0
            self.config["hour-zoom"] = self.hour_zoom
            return GLib.SOURCE_REMOVE

        self._zoom_save = GLib.timeout_add(500, save)

    def _update_actions(self) -> None:
        for name in ("zoom-in", "zoom-out", "zoom-reset"):
            self.lookup_action(name).set_enabled(self.mode in ("day", "week"))
        has = bool(self.selection)
        editable = has and any(not o.event.readonly for o in self.selection)
        for name in ("edit", "copy", "duplicate"):
            self.lookup_action(name).set_enabled(has)
        for name in ("cut", "delete"):
            self.lookup_action(name).set_enabled(editable)
        self.lookup_action("paste").set_enabled(bool(self.clipboard))
        self.lookup_action("undo").set_enabled(bool(self.history.undo))
        self.lookup_action("redo").set_enabled(bool(self.history.redo))

    @property
    def selected(self) -> Occurrence | None:
        """The primary selection: the most recently selected event."""
        return self.selection[-1] if self.selection else None

    def _with_selected(self, fn) -> None:
        if self.selected:
            fn(self.selected)

    # -- settings -------------------------------------------------------------------

    def _apply_first_weekday(self) -> None:
        weekday = self.config["first-weekday"]
        set_first_weekday(None if weekday is None or weekday < 0 else weekday)

    def apply_settings(self) -> None:
        """Preferences changed: redraw everything with the new settings."""
        self._apply_first_weekday()
        weather.set_unit(self.config["temperature-unit"])
        for view in self.views.values():
            self.stack.get_page(view).set_title(view.page_title())
        self.calendar_bar.set_visible(self.config["footbar"])
        self.refresh()
        self._refresh_lists()
        for name in ("day", "week"):
            self.views[name].header.queue_draw()
        self.dock.update()

    # -- navigation -------------------------------------------------------------

    @property
    def mode(self) -> str:
        return self.stack.get_visible_child_name()

    def view(self):
        return self.views[self.mode]

    def _on_view_changed(self, *_args):
        # Follow the focused day into the new view if it was on screen.
        if self._visible_range:
            start, end = self._visible_range
            if start.date() <= as_date(self.target) < end.date():
                self.current = as_date(self.target)
        self.config["view"] = self.mode
        self.refresh()
        self.view().grab_focus()

    def step(self, direction: int) -> None:
        self.go_to(self.view().next_anchor(direction))

    def go_today(self) -> None:
        self.go_to(date.today())

    def go_to(self, d: date) -> None:
        self.current = d
        self.target = d
        self.refresh()

    def open_day(self, d: date) -> None:
        self.target = d
        self.current = d
        if self.mode == "day":
            self.refresh()
        else:
            self.stack.set_visible_child_name("day")

    def show_jump(self) -> None:
        """Ctrl+G: pick any date."""
        cal = Gtk.Calendar()
        cal.select_day(GLib.DateTime.new_local(self.current.year, self.current.month,
                                               self.current.day, 0, 0, 0))
        popover = Gtk.Popover(child=cal)
        popover.set_parent(self.today_button)

        def picked(c):
            dt = c.get_date()
            popover.popdown()
            self.go_to(date(dt.get_year(), dt.get_month(), dt.get_day_of_month()))
            self.view().grab_focus()

        cal.connect("day-selected", picked)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()
        cal.grab_focus()

    def move_target(self, target: date | datetime) -> None:
        """Keyboard focus moved to another day or time slot."""
        self.target = target
        self.selection = []
        self._select_after_refresh = None
        start, end = self._visible_range
        if not start <= datetime.combine(as_date(target), time()) < end:
            self.current = as_date(target)
            self.refresh()
        else:
            view = self.view()
            view.set_selected(set())
            view.set_target(target)
            self._update_actions()
        if hasattr(self.view(), "reveal"):
            self.view().reveal(target)
        self._announce(self._describe_target())

    def cycle_selection(self, direction: int) -> None:
        """Space / Shift+Space: select the next or previous event on the focused day."""
        day = as_date(self.target)
        start = datetime.combine(day, time())
        occs = self.backend.occurrences(start, start + timedelta(days=1))
        if not occs:
            self._announce(_("No events on {day}").format(day=day.strftime(_("%A, %B %-d"))))
            return
        keys = [o.key for o in occs]
        current = keys.index(self.selected.key) if self.selected and self.selected.key in keys else -1
        index = (current + direction) % len(occs) if current >= 0 else (0 if direction > 0 else -1)
        occ = occs[index]
        self.select(occ, occ.start if not occ.is_banner and self.mode in ("day", "week") else day)
        cal = self.backend.calendar(occ.event.calendar)
        self._announce(describe_occurrence(occ, cal.name if cal else ""))

    def open_target(self) -> None:
        """Enter: open the selected event, or create one at the focused day or time."""
        if self.selected:
            self.edit_occurrence(self.selected)
        else:
            self.new_event()

    def _describe_target(self) -> str:
        target = self.target
        day = as_date(target)
        start = datetime.combine(day, time())
        count = len(self.backend.occurrences(start, start + timedelta(days=1)))
        events = count_label(count) if count else _("no events")
        day = target.strftime(_("%A, %B %-d"))
        if isinstance(target, datetime):
            day = fmt_date_time(day, target)
        return _("{day}, {events}").format(day=day, events=events)

    def _announce(self, message: str) -> None:
        widget = self.view()
        if isinstance(widget, TimeGridView):
            widget = widget.grid
        elif isinstance(widget, YearView):
            widget = widget.canvas
        if hasattr(widget, "announce"):  # GTK 4.14+
            widget.announce(message, Gtk.AccessibleAnnouncementPriority.MEDIUM)

    # -- display ------------------------------------------------------------------

    def _period_year(self) -> str:
        if self.mode == "year":
            return ""
        start, end = self._visible_range
        if self.mode == "month":
            return str(self.current.year)
        first, last = start.date(), (end - timedelta(days=1)).date()
        return str(first.year) if first.year == last.year else f"{first.year} – {last.year}"

    def refresh(self) -> None:
        view = self.view()
        view.set_anchor(self.current)
        view.set_target(self.target)
        start, end = self._visible_range = view.visible_range()
        occs = self._with_drops(self.backend.occurrences(start, end), start, end)
        pending = self._select_after_refresh  # events just created, once they appear
        found = [o for o in occs if o.key in pending] if pending else []
        if found:
            self.selection = found
            self._select_after_refresh = None
        else:  # keep the selection, reflecting edits
            keys = {o.key for o in self.selection}
            self.selection = [o for o in occs if o.key in keys]
        view.set_occurrences(occs)
        view.set_selected({o.key for o in self.selection})

        period, year = view.period_title(), self._period_year()
        self.period_label.set_label(period)
        self.year_label.set_label(year)
        self.narrow_title.set_title(view.period_title(short=True))
        self.set_title(f"{period} {year} – Navcal".replace("  ", " "))
        self.calendar.handler_block(self._calendar_handler)
        dt = GLib.DateTime.new_local(self.current.year, self.current.month, self.current.day, 0, 0, 0)
        if hasattr(self.calendar, "set_date"):  # GTK 4.20+
            self.calendar.set_date(dt)
        else:
            self.calendar.select_day(dt)
        self.calendar.handler_unblock(self._calendar_handler)
        self._update_actions()

    def _refresh_lists(self) -> None:
        """Upcoming events, tasks and search results: they follow the calendars and
        the time of day, not the period on screen."""
        self._mark_calendar_days()
        self.dock.update()
        self._refresh_upcoming()
        self._refresh_tasks()
        if self.search_bar.get_search_mode():
            self._update_search()

    def _update_banner(self) -> None:
        failed = [c for c in self.backend.calendars.values() if c.error]
        online = [c for c in self.backend.calendars.values() if c.backend_name not in LOCAL_BACKENDS]
        if failed:
            title = (_("“{name}” couldn’t be loaded").format(name=failed[0].name) if len(failed) == 1
                     else ngettext("{n} calendar couldn’t be loaded",
                                   "{n} calendars couldn’t be loaded", len(failed)).format(n=len(failed)))
            self.banner.set_title(title)
            self.banner.set_button_label(_("_Manage Calendars"))
            self.banner.set_revealed(True)
        elif online and not self._network.get_network_available():
            self.banner.set_title(_("You’re offline. Changes to online calendars sync when "
                                    "you’re back online."))
            self.banner.set_button_label("")
            self.banner.set_revealed(True)
        else:
            self.banner.set_revealed(False)

    def _refresh_upcoming(self) -> None:
        self.upcoming.remove_all()
        now = datetime.now()
        end = datetime.combine(date.today() + timedelta(days=UPCOMING_DAYS), time())
        occs = [o for o in self.backend.occurrences(now, end) if o.end > now]
        occs.sort(key=lambda o: (o.start.date(), not o.is_banner, o.start))
        # Update again when a listed event is over, or tomorrow ("Today" and due dates change).
        tomorrow = datetime.combine(date.today() + timedelta(days=1), time())
        occs = occs[:40]
        self._lists_until = min([o.end for o in occs] + [tomorrow])
        # A few at first, so tasks and world clocks stay in view.
        shown = occs if self._show_all_upcoming else occs[:SIDEBAR_ROWS]
        for occ in shown:
            when = _("All day") if occ.is_banner else f"{fmt_time(occ.start)} – {fmt_time(occ.end)}"
            row = Adw.ActionRow(title=occ.event.title, subtitle=when, activatable=True,
                                use_markup=False, title_lines=1)
            row.add_prefix(Gtk.Box(valign=Gtk.Align.CENTER,
                                   css_classes=["event-dot", color_css_class(occ.color)]))
            row.occurrence = occ
            self.upcoming.append(row)
        if len(occs) > SIDEBAR_ROWS:
            self.upcoming.append(self._more_row(len(occs) - len(shown), self._toggle_upcoming))
        self.upcoming.set_visible(bool(occs))
        self.upcoming_empty.set_visible(not occs)

    def _more_row(self, hidden: int, toggle: Callable[[], None]) -> Adw.ButtonRow:
        """"Show 3 More", or "Show Less" when everything is shown."""
        label = (ngettext("Show {n} More", "Show {n} More", hidden).format(n=hidden) if hidden
                 else _("Show Less"))
        row = Adw.ButtonRow(title=label)
        row.connect("activated", lambda _r: toggle())
        return row

    def _toggle_upcoming(self) -> None:
        self._show_all_upcoming = not self._show_all_upcoming
        self._refresh_upcoming()

    def _toggle_tasks(self) -> None:
        self._show_all_tasks = not self._show_all_tasks
        self._refresh_tasks()

    def _refresh_tasks(self) -> None:
        self.tasks_section.set_visible(bool(self.backend.task_lists))
        self._task_entry.set_sensitive(bool(self.backend.writable_task_lists()))
        while (row := self._task_entry.get_next_sibling()) is not None:
            self.tasks_list.remove(row)
        tasks = self.backend.tasks()
        finished = [t for t in tasks if t.done]
        open_tasks = [t for t in tasks if not t.done]
        shown = open_tasks if self._show_all_tasks else open_tasks[:SIDEBAR_ROWS]
        for task in shown + (finished if self._show_done_tasks else []):
            self.tasks_list.append(task_row(task, self.toggle_task, self.edit_task))
        if len(open_tasks) > SIDEBAR_ROWS:
            self.tasks_list.append(self._more_row(len(open_tasks) - len(shown), self._toggle_tasks))
        if finished:
            label = (_("Hide Finished Tasks") if self._show_done_tasks
                     else ngettext("Show {n} Finished Task", "Show {n} Finished Tasks",
                                   len(finished)).format(n=len(finished)))
            toggle = Adw.ButtonRow(title=label)
            toggle.connect("activated", lambda _r: self._toggle_done_tasks())
            self.tasks_list.append(toggle)

    def _toggle_done_tasks(self) -> None:
        self._show_done_tasks = not self._show_done_tasks
        self._refresh_tasks()

    def _upcoming_header(self, row, before):
        if not hasattr(row, "occurrence"):  # "Show More"
            row.set_header(None)
            return
        day = row.occurrence.start.date()
        if before and before.occurrence.start.date() == day:
            row.set_header(None)
        else:
            row.set_header(Gtk.Label(label=relative_day(max(day, date.today())), xalign=0,
                                     css_classes=["dim-label", "caption-heading"],
                                     margin_start=12, margin_top=12, margin_bottom=6))

    def _show_occurrence(self, occ: Occurrence) -> None:
        """Go to an event and select it."""
        self.current = occ.start.date()
        self.target = occ.start if not occ.is_banner else occ.start.date()
        self.selection = [occ]
        self.refresh()
        if self.split_view.get_collapsed():
            self.split_view.set_show_sidebar(False)
        self.view().grab_focus()

    def place_warning(self, occ: Occurrence) -> str | None:
        """"Closed at 9:00 PM"… if the event's place won't be open then (as far as known)."""
        return self.place_checker.warning(occ)

    def spoken_event(self, occ: Occurrence) -> str:
        """How a screen reader reads an event, with the warning about its place."""
        warning = self.place_warning(occ)
        spoken = spoken_event(occ)
        return _("{event}. {warning}").format(event=spoken, warning=warning) if warning else spoken

    def _on_weather(self) -> None:
        for name in ("day", "week"):
            self.views[name]._redraw()
        self.views["month"].queue_draw()
        self.dock.update()

    def show_sidebar_section(self, section: str) -> None:
        """Open the sidebar at the tasks or the world clocks."""
        self.split_view.set_show_sidebar(True)
        if section == "tasks":
            self._task_entry.grab_focus()
        elif row := self.clocks_list.get_row_at_index(0):
            row.grab_focus()

    def _on_upcoming_activated(self, _list, row) -> None:
        if hasattr(row, "occurrence"):
            self._show_occurrence(row.occurrence)

    def _mark_calendar_days(self) -> None:
        """Mark the days with events in the sidebar calendar's month."""
        cal = self.calendar
        first = date(cal.get_year(), cal.get_month() + 1, 1)  # the month counts from 0
        last = (first + timedelta(days=31)).replace(day=1)
        days = set()
        for occ in self.backend.occurrences(datetime.combine(first, time()), datetime.combine(last, time())):
            days.update(d.day for d in days_covered(occ.start, occ.end) if first <= d < last)
        cal.clear_marks()
        for day in days:
            cal.mark_day(day)

    def _on_calendar_day(self, calendar) -> None:
        dt = calendar.get_date()
        self.go_to(date(dt.get_year(), dt.get_month(), dt.get_day_of_month()))
        if self.split_view.get_collapsed():
            self.split_view.set_show_sidebar(False)

    def _tick(self) -> bool:
        self.view().tick()
        self._refresh_clocks()
        self.dock.update()
        if datetime.now() >= self._lists_until:
            self._refresh_lists()
        return GLib.SOURCE_CONTINUE

    # -- world clocks -----------------------------------------------------------

    def _refresh_clocks(self) -> None:
        self.clocks_list.remove_all()
        for tzid in self.config["world-clocks"]:
            time_text, detail = worldclock.describe(tzid)
            row = Adw.ActionRow(title=worldclock.city_name(tzid), subtitle=detail, use_markup=False)
            clock = Gtk.Label(label=time_text, css_classes=["title-4", "numeric"])
            row.add_suffix(clock)
            remove = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text=_("Remove clock"), css_classes=["flat"])
            remove.connect("clicked", lambda _b, z=tzid: self._remove_clock(z))
            row.add_suffix(remove)
            row.update_property([Gtk.AccessibleProperty.LABEL],
                                [f"{worldclock.city_name(tzid)}, {time_text}, {detail}"])
            self.clocks_list.append(row)
        add = Adw.ButtonRow(title=_("_Add World Clock…"), use_underline=True,
                            start_icon_name="list-add-symbolic")
        add.connect("activated", lambda _r: self._ask_clock())
        self.clocks_list.append(add)

    def _remove_clock(self, tzid: str) -> None:
        self.config["world-clocks"] = [z for z in self.config["world-clocks"] if z != tzid]
        self._refresh_clocks()

    def _ask_clock(self) -> None:
        zones = [z for z in ical.all_tzids() if z not in self.config["world-clocks"]]
        row = Adw.ComboRow(title=_("_City or time zone"), use_underline=True, enable_search=True,
                           model=Gtk.StringList.new([ical.zone_label(z) for z in zones]),
                           expression=Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(row)
        dialog = Adw.AlertDialog(heading=_("Add World Clock"),
                                 body=_("Search for a city in the time zone you want."))
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("add", _("_Add"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")

        def on_response(_d, response):
            if response == "add":
                self.config["world-clocks"] = self.config["world-clocks"] + [zones[row.get_selected()]]
                self._refresh_clocks()

        dialog.connect("response", on_response)
        dialog.present(self)

    # -- search -------------------------------------------------------------------

    def _update_search(self) -> None:
        text = self.search_entry.get_text().strip()
        searching = self.search_bar.get_search_mode() and bool(text)
        self.content_stack.set_visible_child_name("search" if searching else "calendar")
        self.search_results.remove_all()
        if not searching:
            return
        results = self.backend.search(text, datetime.now())
        for occ in results:
            cal = self.backend.calendar(occ.event.calendar)
            when = occ.start.strftime(_("%a, %b %-d, %Y"))
            if not occ.is_banner:
                when = fmt_date_time(when, occ.start)
            parts = [when] + ([occ.event.location] if occ.event.location else []) + (
                [cal.name] if cal else [])
            row = Adw.ActionRow(title=occ.event.title, subtitle=" · ".join(parts),
                                use_markup=False, activatable=True)
            row.add_prefix(Gtk.Box(valign=Gtk.Align.CENTER,
                                   css_classes=["event-dot", color_css_class(occ.color)]))
            if occ.end <= datetime.now():
                row.add_suffix(Gtk.Label(label=_("Past"), css_classes=["dim-label", "caption"]))
            row.occurrence = occ
            self.search_results.append(row)
        self.search_stack.set_visible_child_name("results" if results else "empty")

    def _on_result_activated(self, _list, row) -> None:
        self.search_bar.set_search_mode(False)
        self._show_occurrence(row.occurrence)

    # -- selection (called by the views) ------------------------------------------

    def select(self, occ: Occurrence | None, target, mode: str = "replace") -> None:
        """mode: "replace", "extend" (Ctrl/Shift+click toggles occ), or "context"
        (right-click: keep the selection if occ is already part of it)."""
        self._select_after_refresh = None  # an explicit choice replaces a pending one
        keys = [o.key for o in self.selection]
        if mode == "extend" and occ is not None:
            if occ.key in keys:
                self.selection = [o for o in self.selection if o.key != occ.key]
            else:
                self.selection = self.selection + [occ]
        elif mode == "context" and occ is not None and occ.key in keys:
            pass
        else:
            self.selection = [occ] if occ else []
        if target is not None:
            self.target = target
        view = self.view()
        view.set_selected({o.key for o in self.selection})
        view.set_target(self.target)
        self._update_actions()
        if len(self.selection) > 1:
            self._announce(ngettext("{n} event selected", "{n} events selected",
                                    len(self.selection)).format(n=len(self.selection)))

    def select_all(self) -> None:
        start, end = self._visible_range
        self.selection = self.backend.occurrences(start, end)
        self.view().set_selected({o.key for o in self.selection})
        self._update_actions()
        self._announce(ngettext("{n} event selected", "{n} events selected",
                                len(self.selection)).format(n=len(self.selection)))

    def show_context_menu(self, widget: Gtk.Widget, x: float, y: float) -> None:
        menu = Gio.Menu()
        if self.selection:
            many = len(self.selection) > 1
            edit = Gio.Menu()
            if not many:
                edit.append(_("_Edit…") if not self.selected.event.readonly else _("_Open"),
                            "win.edit")
            edit.append(_("D_uplicate") if many else _("D_uplicate…"), "win.duplicate")
            edit.append(_("_Copy"), "win.copy")
            edit.append(_("Cu_t"), "win.cut")
            menu.append_section(None, edit)
            if any(not o.event.readonly for o in self.selection):
                delete = Gio.Menu()
                delete.append(_("_Delete"), "win.delete")
                menu.append_section(None, delete)
        else:
            menu.append(_("_New Event…"), "win.new-event")
            menu.append(_("_Paste"), "win.paste")
        popover = Gtk.PopoverMenu.new_from_model(menu)
        popover.set_parent(widget)
        popover.set_has_arrow(False)
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)
        popover.set_halign(Gtk.Align.START)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))
        popover.popup()

    # -- writing ------------------------------------------------------------------

    def _write(self, ops: list, label: str, snapshots, on_success=None,
               failure: str | None = None, drops: list[Drop] = ()) -> None:
        """Run backend operations off the UI thread, in order; record undo on success.

        label: names the change in the history. snapshots: what to restore on
        undo, or a function of the ops' results. failure: the toast if it fails.
        drops: dragged events shown in place until this write is saved.
        """
        def run_all():
            return [op() for op in ops]

        def done(results, error):
            if error is not None:
                print(f"Navcal: {label} failed: {error.message}", file=sys.stderr)
                self.toast(failure or _("Couldn’t save the change"))
                self._forget_drops(drops)
                self.refresh()
                return
            self._saved_drops(drops)
            snaps = snapshots(results) if callable(snapshots) else snapshots
            self.history.record(label, snaps)
            if on_success:
                on_success(results)
            self._update_actions()

        self.backend.run(run_all, done)

    def _default_duration(self) -> timedelta:
        return timedelta(minutes=self.config["default-duration"])

    def default_calendar(self) -> str | None:
        writable = self.backend.writable_calendars()
        uids = [c.uid for c in writable]
        preferred = self.config["default-calendar"]
        if preferred in uids:
            return preferred
        navcal = next((c.uid for c in writable if c.name == "Navcal"), None)
        return navcal or (uids[0] if uids else None)

    def _calendar_choices(self) -> list[CalendarChoice]:
        # Name online calendars with their account (“Work · Google”); local ones by name.
        return [CalendarChoice(c.uid, f"{c.name} · {c.account}"
                               if c.account and c.backend_name != "local" else c.name, c.color)
                for c in self.backend.writable_calendars()]

    def _overlaps_for(self, uid: str | None):
        """For the editor: titles of other timed events between two times."""
        def overlaps(start: datetime, end: datetime) -> list[str]:
            return [o.event.title for o in self.backend.occurrences(start, end)
                    if not o.is_banner and o.event.uid != uid
                    and o.start < end and o.end > start]
        return overlaps

    def _editor(self, draft: Event, heading: str, on_save, on_delete=None, new=False) -> None:
        cal = self.backend.calendar(draft.calendar)
        EventEditor(draft, heading=heading, calendars=self._calendar_choices(), on_save=on_save,
                    on_delete=on_delete, calendar_color=cal.color if cal else None, new=new,
                    overlaps=self._overlaps_for(draft.uid), online=self.config["online"],
                    near=places.home(self.config)).present(self)

    def new_event(self) -> None:
        """Ctrl+N, Enter, the + button: the quick add popover."""
        self.new_event_button.popup()

    # -- quick add -------------------------------------------------------------------

    def _quick_parse(self) -> QuickEvent:
        target = self.target
        return quick_parse(self.quick_add_entry.get_text(), datetime.now(),
                           default_duration=self._default_duration(), month_first=month_first(),
                           default_day=as_date(target),
                           default_time=target.time() if isinstance(target, datetime) else None)

    def _reset_quick_add(self) -> None:
        self.quick_add_entry.set_text("")
        self._update_quick_add()
        self.quick_add_entry.grab_focus()

    def _update_quick_add(self) -> None:
        q = self._quick_parse()
        if not q.title:
            self.quick_add_preview.set_label(_("Type what, when and where"))
        else:
            parts = [describe_when(q.start, q.end, q.all_day)]
            if q.location:
                parts.append(q.location)
            if q.freq != Freq.NONE:
                parts.append(describe_repeat(q.freq, q.interval, q.byday))
            self.quick_add_preview.set_label(" · ".join(parts))
        self.quick_add_button.set_sensitive(bool(q.title))

    def _quick_event(self) -> Event | None:
        q = self._quick_parse()
        calendar = self.default_calendar()
        if calendar is None:
            self.toast(_("There’s no calendar you can add events to"))
            return None
        reminder = self.config["default-reminder"]
        return Event(title=q.title, start=q.start, end=q.end, all_day=q.all_day,
                     location=q.location, freq=q.freq, interval=q.interval, byday=q.byday,
                     calendar=calendar,
                     reminders=[] if q.all_day or reminder is None or reminder < 0 else [reminder])

    def _quick_add(self) -> None:
        if not self.quick_add_button.get_sensitive():
            return
        ev = self._quick_event()
        self.quick_add_popover.popdown()
        if ev:
            self._add([ev], _("Add “{title}”").format(title=ev.title))

    def _quick_edit(self) -> None:
        ev = self._quick_event()
        self.quick_add_popover.popdown()
        if ev:
            self._editor(ev, _("New Event"),
                         lambda e: self._add([e], _("Add “{title}”").format(title=e.title)), new=True)

    def create_event(self, start: datetime, end: datetime | None, all_day: bool) -> None:
        """Open the editor on a new event; end=None means the default length."""
        calendar = self.default_calendar()
        if calendar is None:
            self.toast(_("There’s no calendar you can add events to"))
            return
        if end is None:
            end = start + self._default_duration()
        reminder = self.config["default-reminder"]
        draft = Event(title="", start=start, end=end, all_day=all_day, calendar=calendar,
                      reminders=[] if all_day or reminder is None or reminder < 0 else [reminder])
        self._editor(draft, _("New Event"),
                     lambda ev: self._add([ev], _("Add “{title}”").format(title=ev.title)), new=True)

    def _add(self, events: list[Event], label: str, drops: list[Drop] = ()) -> None:
        """Create events; select them once they appear. drops: one per event, if dragged."""
        for ev in events:
            cal = self.backend.calendar(ev.calendar)
            if cal is None or cal.readonly:
                ev.calendar = self.default_calendar()
        self.config["default-calendar"] = events[0].calendar
        ops = [self.backend.plan_create(ev.calendar, ev) for ev in events]

        def created(uids):
            for drop, uid in zip(drops, uids):  # so the saved copy replaces the dropped one
                drop.shown.event.uid = uid
            self._select_after_refresh = {(ev.calendar, uid, None, ev.start)
                                          for ev, uid in zip(events, uids)}
            self.refresh()

        self._write(ops, label, lambda uids: [(ev.calendar, uid, []) for ev, uid in zip(events, uids)],
                    created, failure=ngettext("Couldn’t add the event", "Couldn’t add the events",
                                              len(events)), drops=drops)  # undo = remove them

    def _with_series_rule(self, occ: Occurrence, ev: Event) -> Event:
        """ev with the whole repeat rule of occ's series. An individually edited
        occurrence has no rule of its own, and a partial one would look like a
        rule change (which rewrites the whole series)."""
        if occ.is_instance:
            s = self.backend.series(occ)
            ev.freq, ev.interval, ev.byday, ev.bymonthday = s.freq, s.interval, list(s.byday), s.bymonthday
            ev.count, ev.until, ev.rrule_extra = s.count, s.until, s.rrule_extra
        return ev

    def edit_occurrence(self, occ: Occurrence) -> None:
        # Show the clicked occurrence's own times in the editor.
        draft = self._with_series_rule(occ, occ.event.clone(start=occ.start, end=occ.end))
        draft.uid, draft.raw, draft.readonly = occ.event.uid, occ.event.raw, occ.event.readonly
        self._editor(draft, _("Event Details") if draft.readonly else _("Edit Event"),
                     lambda ev: self._save_occurrence(occ, ev, "change"),
                     None if draft.readonly else lambda: self.delete_occurrence(occ))

    def move_occurrence(self, occ: Occurrence, start: datetime, end: datetime,
                        copy: bool = False, all_day: bool | None = None) -> None:
        """A drag ended: move the event, or with Ctrl held, copy it there.

        all_day: set when the drag crossed between the all-day row and the time grid.
        """
        change = {"start": start, "end": end}
        if all_day is not None:
            change.update(all_day=all_day, tzid=None)
        if copy:
            source = occ.event
            ev = source.clone(**change)
            if occ.is_instance:  # copy just this occurrence, not the series
                one_off(ev)
            # Read-only until saved: it has no place in the calendar to drag it from yet.
            drop = self._drop(None, Occurrence(replace(ev, readonly=True), start, end, color=occ.color))
            self._add([ev], _("Duplicate “{title}”").format(title=source.title), [drop])
            return
        moved = self._with_series_rule(occ, occ.event.clone(**change))
        moved.uid, moved.raw = occ.event.uid, occ.event.raw
        drop = self._drop(occ, Occurrence(replace(occ.event, **change), start, end,
                                          rid=occ.rid, color=occ.color))
        self._save_occurrence(occ, moved, "move", drop)

    # -- dropped events ---------------------------------------------------------------
    # A drop is written in the background, and the calendar reports the change a
    # moment later. Until then the event is shown where it was dropped, so it doesn't
    # jump back to where it came from.

    def _drop(self, occ: Occurrence | None, shown: Occurrence) -> Drop:
        """Show shown in place of occ (None for a new copy) until the drop is saved."""
        drop = Drop(occ.key if occ else None, shown)
        self._drops.append(drop)
        if occ and any(o.key == occ.key for o in self.selection):  # it stays selected
            self.selection = [shown if o.key == occ.key else o for o in self.selection]
        self.refresh()
        return drop

    def _with_drops(self, occs: list[Occurrence], start: datetime, end: datetime) -> list[Occurrence]:
        if not self._drops:
            return occs
        hidden = {d.old_key for d in self._drops}
        shown = [o for o in occs if o.key not in hidden]
        # Once the calendar has the change, the event itself is in occs.
        present = {(o.event.calendar, o.event.uid, o.start, o.end) for o in shown}
        for d in self._drops:
            o = d.shown
            if (o.event.calendar, o.event.uid, o.start, o.end) not in present and overlaps(
                    o.start, o.end, start, end):
                shown.append(o)
        return sort_occurrences(shown)

    def _saved_drops(self, drops: list[Drop]) -> None:
        """The calendar reports the saved change right after the write returns; if it
        doesn't (a slow or remote calendar), stop waiting after a while."""
        for drop in drops:
            drop.saved = True

        def give_up():
            self._forget_drops(drops, refresh=True)
            return GLib.SOURCE_REMOVE

        if drops:
            GLib.timeout_add(DROP_WAIT_MS, give_up)

    def _forget_drops(self, drops: list[Drop], refresh: bool = False) -> None:
        left = [d for d in self._drops if d not in drops]
        if len(left) != len(self._drops):
            self._drops = left
            if refresh:
                self.refresh()

    def _on_backend_changed(self, *_args) -> None:
        self._drops = [d for d in self._drops if not d.saved]  # the calendar shows them now
        self.refresh()
        self._refresh_lists()

    def _save_occurrence(self, occ: Occurrence, edited: Event, kind: str,
                         drop: Drop | None = None) -> None:
        """kind: "change" (from the editor) or "move" (a drag, shown in place as drop)."""
        ev = occ.event
        snapshots = [self.backend.snapshot(ev.calendar, ev.uid)]
        if edited.calendar and edited.calendar != ev.calendar:
            snapshots.append((edited.calendar, ev.uid, []))  # undo removes the moved copy
        drops = [drop] if drop else []

        def apply(scope: str | None):
            if scope is None:
                self._forget_drops(drops)
                self.refresh()  # put a dragged event back where it was
                return
            try:
                op = self.backend.plan_save(occ, edited, scope)
            except KeyError:
                self._forget_drops(drops, refresh=True)
                self.toast(_("That calendar isn’t available right now"))
                return
            label = (_("Move “{title}”") if kind == "move" else _("Change “{title}”")).format(title=ev.title)
            self._write([op], label, snapshots, failure=_("Couldn’t save “{title}”").format(title=ev.title),
                        drops=drops)

        series = self.backend.series(occ)
        if not occ.is_instance or not series.same_rule(edited) or edited.calendar != ev.calendar:
            apply(Scope.ALL)  # changing the rule or calendar only makes sense for the series
        else:
            ask_scope(self, kind, ev.title, apply)

    def delete_occurrence(self, occ: Occurrence) -> None:
        """Delete one event; a recurring one asks which occurrences."""
        if occ.event.readonly:
            self.toast(_("This calendar is read-only"))
            return
        if occ.is_instance:
            ask_scope(self, "delete", occ.event.title,
                      lambda scope: scope and self._remove([occ], scope, cut=False))
        else:
            self._remove([occ], Scope.ALL, cut=False)  # no confirmation: the toast offers Undo

    def delete_selection(self) -> None:
        editable = [o for o in self.selection if not o.event.readonly]
        if len(editable) == 1:
            self.delete_occurrence(editable[0])
        elif editable:
            # Several events: occurrences of recurring ones are deleted on their own.
            self._remove(editable, Scope.THIS, cut=False)

    def _remove(self, occs: list[Occurrence], scope: str, cut: bool) -> None:
        seen, snapshots = set(), []
        for o in occs:
            if (o.event.calendar, o.event.uid) not in seen:
                seen.add((o.event.calendar, o.event.uid))
                snapshots.append(self.backend.snapshot(o.event.calendar, o.event.uid))
        ops = [self.backend.plan_remove(o, scope if o.is_instance else Scope.ALL) for o in occs]
        n, title = len(occs), occs[0].event.title
        # One event is named; several are counted.
        if n == 1:
            label = (_("Cut “{title}”") if cut else _("Delete “{title}”")).format(title=title)
            done = (_("“{title}” cut") if cut else _("“{title}” deleted")).format(title=title)
            failure = _("Couldn’t delete “{title}”").format(title=title)
        else:
            label = (ngettext("Cut {n} event", "Cut {n} events", n) if cut
                     else ngettext("Delete {n} event", "Delete {n} events", n)).format(n=n)
            done = (ngettext("{n} event cut", "{n} events cut", n) if cut
                    else ngettext("{n} event deleted", "{n} events deleted", n)).format(n=n)
            failure = _("Couldn’t delete the events")
        self.selection = []
        self._write(ops, label, snapshots, lambda _r: self.toast(done, undo=True), failure=failure)

    def copy(self, announce: bool = True, occurrences_only: bool = False) -> None:
        """occurrences_only: copy occurrences of recurring events as single events
        (for cut, which removes just those occurrences)."""
        if not self.selection:
            return
        occs = sorted(self.selection, key=lambda o: o.start)
        first = occs[0].start
        self.clipboard = []
        for occ in occs:
            source = occ.event
            ev = source.clone(start=occ.start, end=occ.end)
            if occ.is_instance and occurrences_only:
                one_off(ev)
            elif occ.is_instance:
                series = self.backend.series(occ)
                ev = series.clone(start=occ.start, end=occ.end)
            if source.readonly:
                ev.calendar = self.default_calendar()
            self.clipboard.append((ev, occ.start - first))
        self._update_actions()
        if announce:
            n = len(occs)
            self.toast(_("“{title}” copied").format(title=occs[0].event.title) if n == 1
                       else ngettext("{n} event copied", "{n} events copied", n).format(n=n))

    def cut(self) -> None:
        editable = [o for o in self.selection if not o.event.readonly]
        if not editable:
            return
        self.selection = editable
        self.copy(announce=False, occurrences_only=True)  # only these occurrences are removed
        self._remove(editable, Scope.THIS, cut=True)

    def paste(self, target: date | datetime) -> None:
        if not self.clipboard:
            return
        first = self.clipboard[0][0]
        if isinstance(target, datetime) and not first.all_day:
            base = target
        else:
            # Pasting onto a whole day keeps the copied events' time of day.
            base = datetime.combine(as_date(target), first.start.time())
        events = []
        for ev, offset in self.clipboard:
            start = base + offset
            copy = ev.clone(start=start, end=start + ev.duration)
            if copy.until and copy.until < start.date():
                copy.until = None
            events.append(copy)
        n = len(events)
        self._add(events, _("Paste “{title}”").format(title=events[0].title) if n == 1
                  else ngettext("Paste {n} event", "Paste {n} events", n).format(n=n))

    def duplicate(self) -> None:
        if len(self.selection) == 1:
            occ = self.selection[0]
            draft = occ.event.clone(start=occ.start, end=occ.end)
            if occ.event.readonly:
                draft.calendar = self.default_calendar()
            self._editor(draft, _("Duplicate Event"),
                         lambda ev: self._add([ev], _("Duplicate “{title}”").format(title=ev.title)),
                         new=True)
        elif self.selection:
            self.copy(announce=False)
            events = [ev.clone() for ev, _offset in self.clipboard]
            self._add(events, ngettext("Duplicate {n} event", "Duplicate {n} events",
                                       len(events)).format(n=len(events)))

    # -- tasks --------------------------------------------------------------------

    def default_task_list(self) -> str | None:
        uids = [t.uid for t in self.backend.writable_task_lists()]
        preferred = self.config["default-task-list"]
        return preferred if preferred in uids else (uids[0] if uids else None)

    def add_task(self, text: str) -> None:
        """From the "New task" field; understands "Buy milk tomorrow" or "Call Sam 5pm"."""
        if not text.strip():
            return
        list_uid = self.default_task_list()
        if list_uid is None:
            self.toast(_("There’s no task list you can add tasks to"))
            return
        q = quick_parse(text, datetime.now(), month_first=month_first())
        due = q.start if q.time_given else (q.start.date() if q.date_given else None)
        task = Task(title=q.title or text.strip(), due=due, list=list_uid)
        self._task_entry.set_text("")
        self._write([self.backend.plan_create_task(list_uid, task)],
                    _("Add “{title}”").format(title=task.title), lambda uids: [(list_uid, uids[0], [])],
                    failure=_("Couldn’t add “{title}”").format(title=task.title))

    def toggle_task(self, task: Task, done: bool) -> None:
        if done == task.done:
            return
        snapshot = self.backend.snapshot(task.list, task.uid)
        changed = replace(task, done=done)
        label = _("Finish “{title}”") if done else _("Reopen “{title}”")
        # The Undo toast only once the change is in the history, or it would undo another one.
        on_success = (lambda _r: self.toast(_("“{title}” done").format(title=task.title), undo=True)
                      ) if done else None
        self._write([self.backend.plan_save_task(changed)], label.format(title=task.title), [snapshot],
                    on_success, failure=_("Couldn’t save “{title}”").format(title=task.title))

    def edit_task(self, task: Task) -> None:
        lists = [CalendarChoice(t.uid, t.name, t.color) for t in self.backend.writable_task_lists()]

        def save(edited: Task):
            snapshots = [self.backend.snapshot(task.list, task.uid)]
            if edited.new_list:
                snapshots.append((edited.new_list, task.uid, []))
            self._write([self.backend.plan_save_task(edited)],
                        _("Change “{title}”").format(title=task.title), snapshots,
                        failure=_("Couldn’t save “{title}”").format(title=task.title))

        TaskDialog(task, lists=lists, on_save=save,
                   on_delete=None if task.readonly else lambda: self.delete_task(task)).present(self)

    def delete_task(self, task: Task) -> None:
        snapshot = self.backend.snapshot(task.list, task.uid)
        self._write([self.backend.plan_remove_task(task)], _("Delete “{title}”").format(title=task.title),
                    [snapshot], lambda _r: self.toast(_("“{title}” deleted").format(title=task.title),
                                                      undo=True),
                    failure=_("Couldn’t delete “{title}”").format(title=task.title))

    # -- undo / redo --------------------------------------------------------------

    def _swap(self, source: list, target: list) -> None:
        """Restore the newest entry of source, saving the current state to target."""
        if not source:
            return
        label, snapshots = source.pop()
        current = [self.backend.snapshot(cal, uid) for cal, uid, _texts in snapshots
                   if self.backend.calendar(cal)]
        target.append((label, current))
        self.history.save()
        for snap in snapshots:
            self.backend.run(self.backend.plan_restore(snap), self._restored)
        self.selection = []
        self._update_actions()

    def _restored(self, _result, error) -> None:
        if error is not None:
            print(f"Navcal: undo failed: {error.message}", file=sys.stderr)
            self.toast(_("Couldn’t undo the change"))

    def undo(self) -> None:
        self._swap(self.history.undo, self.history.redo)

    def redo(self) -> None:
        self._swap(self.history.redo, self.history.undo)

    def toast(self, title: str, undo: bool = False) -> None:
        toast = Adw.Toast(title=title, timeout=5, use_markup=False)
        if undo:
            toast.set_button_label(_("_Undo"))
            toast.set_action_name("win.undo")
        self.toasts.add_toast(toast)

    # -- printing -------------------------------------------------------------------

    def print_checklist(self, export_to: str | None = None):
        """Ctrl+P: the print dialog, for the period on screen. export_to: print
        straight to that PDF file with the last options (for tests)."""
        options = printing.PrintOptions.from_dict(self.config["print-options"])
        options.period = {"day": "day", "week": "week"}.get(self.mode, "month")

        def occurrences(first: date, last: date) -> list[Occurrence]:
            return self.backend.occurrences(datetime.combine(first, time()),
                                            datetime.combine(last + timedelta(days=1), time()))

        if export_to:
            first, last = printing.period_range(options.period, self.current)
            printout = printing.Printout(options.period, first, last, occurrences(first, last), options)
            return printing.print_out(printout, self, export_to)
        def remember(options: printing.PrintOptions) -> None:
            self.config["print-options"] = options.to_dict()

        PrintDialog(self.current, options, occurrences, on_change=remember, toast=self.toast).present(self)

    # -- lifecycle --------------------------------------------------------------

    def save_state(self) -> None:
        width, height = self.get_default_size()
        self.config["width"], self.config["height"] = width, height
        self.config["maximized"] = self.is_maximized()
