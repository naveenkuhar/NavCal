# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""The event editor dialog and the recurring-event scope prompt."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import PurePosixPath
from typing import Callable
from urllib.parse import unquote, urlparse

from . import resources  # noqa: F401  (registers the UI templates)
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import ical, places, weather  # noqa: E402
from .draw import first_weekday, fmt_time  # noqa: E402
from .i18n import N_, _, ngettext  # noqa: E402
from .models import EVENT_COLORS, Event, Freq, Scope  # noqa: E402
from .place_search import PlaceSearch  # noqa: E402
from .places import OpeningHours, Place  # noqa: E402
from .resources import RESOURCE_BASE  # noqa: E402
from .style import color_css_class  # noqa: E402

# Labels are marked with N_() and translated where they're shown.
REPEAT_CHOICES = [  # label, frequency
    (N_("Never"), Freq.NONE),
    (N_("Daily"), Freq.DAILY),
    (N_("Weekly"), Freq.WEEKLY),
    (N_("Monthly"), Freq.MONTHLY),
    (N_("Yearly"), Freq.YEARLY),
]
TRAVEL_CHOICES = [(N_("None"), 0), (N_("5 minutes"), 5), (N_("10 minutes"), 10),
                  (N_("15 minutes"), 15), (N_("30 minutes"), 30), (N_("45 minutes"), 45),
                  (N_("1 hour"), 60), (N_("1½ hours"), 90), (N_("2 hours"), 120)]
STOPS_CHOICES = [N_("Never"), N_("On a date"), N_("After a number of times")]

REMINDER_CHOICES = [
    (N_("At start time"), 0),
    (N_("5 minutes before"), 5),
    (N_("10 minutes before"), 10),
    (N_("15 minutes before"), 15),
    (N_("30 minutes before"), 30),
    (N_("1 hour before"), 60),
    (N_("2 hours before"), 120),
    (N_("1 day before"), 1440),
    (N_("1 week before"), 10080),
]


def _interval_text(freq: str, n: int) -> str:
    return {
        Freq.DAILY: ngettext("{n} day", "{n} days", n),
        Freq.WEEKLY: ngettext("{n} week", "{n} weeks", n),
        Freq.MONTHLY: ngettext("{n} month", "{n} months", n),
        Freq.YEARLY: ngettext("{n} year", "{n} years", n),
    }.get(freq, "").format(n=n)


@dataclass
class CalendarChoice:
    uid: str
    name: str
    color: str


def fmt_date(d: date) -> str:
    """Short enough for narrow screens; the year only when it isn't this year."""
    return d.strftime(_("%a, %b %-d") if d.year == date.today().year else _("%a, %b %-d, %Y"))


def _reminder_label(minutes: int) -> str:
    for label, value in REMINDER_CHOICES:
        if value == minutes:
            return _(label)
    if minutes % 1440 == 0:
        n = minutes // 1440
        return ngettext("{n} day before", "{n} days before", n).format(n=n)
    if minutes % 60 == 0:
        n = minutes // 60
        return ngettext("{n} hour before", "{n} hours before", n).format(n=n)
    return ngettext("{n} minute before", "{n} minutes before", minutes).format(n=minutes)


def link_label(uri: str) -> tuple[str, str]:
    """A short title and a subtitle for a link or attached file."""
    parsed = urlparse(uri)
    if parsed.scheme == "file":
        path = PurePosixPath(unquote(parsed.path))
        return path.name or str(path), str(path.parent)
    host = parsed.netloc or parsed.scheme
    rest = unquote(parsed.path).strip("/")
    return (rest.rsplit("/", 1)[-1] or host), host


def normalize_link(text: str) -> str | None:
    """A usable link from what someone typed, or None."""
    text = text.strip()
    if not text or " " in text:
        return None
    if urlparse(text).scheme in ("http", "https", "mailto", "file", "ftp", "tel"):
        return text
    return f"https://{text}" if "." in text else None


def open_uri(uri: str, parent: Gtk.Widget, fallback: str | None = None) -> None:
    """Open a link in the default app; try fallback if nothing handles it."""
    def done(launcher, result):
        try:
            launcher.launch_finish(result)
        except GLib.Error:
            if fallback:
                open_uri(fallback, parent)
    Gtk.UriLauncher(uri=uri).launch(parent.get_root(), None, done)


# "The second Tuesday": ordinals with the weekday, so languages can reorder them.
ORDINALS = {1: N_("The first {weekday}"), 2: N_("The second {weekday}"),
            3: N_("The third {weekday}"), 4: N_("The fourth {weekday}"),
            5: N_("The fifth {weekday}"), -1: N_("The last {weekday}")}


def _week_of_month(d: date) -> tuple[int, bool]:
    """Which occurrence of its weekday d is in its month, and whether it's the last."""
    n = (d.day - 1) // 7 + 1
    return n, (d + timedelta(days=7)).month != d.month


class DateButton(Gtk.MenuButton):
    """Shows a date; opens a calendar popover to change it."""

    def __init__(self, d: date, on_change: Callable[[], None]):
        super().__init__(valign=Gtk.Align.CENTER, tooltip_text=_("Choose date"),
                         css_classes=["flat"])
        self._on_change = on_change
        self._calendar = Gtk.Calendar()
        self._calendar.connect("day-selected", self._on_day_selected)
        self.set_popover(Gtk.Popover(child=self._calendar))
        self._date = d
        self.set_date(d)

    def get_date(self) -> date:
        return self._date

    def set_date(self, d: date) -> None:
        self._date = d
        self.set_label(fmt_date(d))
        self._calendar.handler_block_by_func(self._on_day_selected)
        dt = GLib.DateTime.new_local(d.year, d.month, d.day, 0, 0, 0)
        if hasattr(self._calendar, "set_date"):  # GTK 4.20+
            self._calendar.set_date(dt)
        else:
            self._calendar.select_day(dt)
        self._calendar.handler_unblock_by_func(self._on_day_selected)

    def _on_day_selected(self, calendar):
        dt = calendar.get_date()
        self._date = date(dt.get_year(), dt.get_month(), dt.get_day_of_month())
        self.set_label(fmt_date(self._date))
        self.get_popover().popdown()
        self._on_change()


class TimeButton(Gtk.MenuButton):
    """Shows a time; opens hour and minute spinners to change it."""

    def __init__(self, t: time, on_change: Callable[[], None]):
        super().__init__(valign=Gtk.Align.CENTER, tooltip_text=_("Choose time"),
                         css_classes=["flat"])
        self._on_change = on_change
        self._hours = self._spinner(23, 1, _("Hour"))
        self._minutes = self._spinner(59, 5, _("Minute"))
        box = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.append(self._hours)
        box.append(Gtk.Label(label=":", css_classes=["title-2"]))
        box.append(self._minutes)
        self.set_popover(Gtk.Popover(child=box))
        self.set_time(t)
        for spin in (self._hours, self._minutes):
            spin.connect("value-changed", self._changed)

    @staticmethod
    def _spinner(upper: int, step: int, name: str) -> Gtk.SpinButton:
        spin = Gtk.SpinButton(orientation=Gtk.Orientation.VERTICAL, numeric=True, wrap=True,
                              adjustment=Gtk.Adjustment(upper=upper, step_increment=step),
                              css_classes=["title-2", "numeric"])
        spin.update_property([Gtk.AccessibleProperty.LABEL], [name])
        spin.connect("output", lambda s: s.set_text(f"{int(s.get_value()):02d}") or True)
        return spin

    def get_time(self) -> time:
        return time(int(self._hours.get_value()), int(self._minutes.get_value()))

    def set_time(self, t: time) -> None:
        # Callers that must not react to this change guard it themselves.
        self._hours.set_value(t.hour)
        self._minutes.set_value(t.minute)
        self._update_label()

    def _update_label(self) -> None:
        self.set_label(fmt_time(datetime.combine(date.today(), self.get_time())))

    def _changed(self, _spin):
        self._update_label()
        self._on_change()


@Gtk.Template(resource_path=f"{RESOURCE_BASE}/ui/editor.ui")
class EventEditor(Adw.Dialog):
    __gtype_name__ = "NavcalEventEditor"

    header: Adw.HeaderBar = Gtk.Template.Child()
    cancel_button: Gtk.Button = Gtk.Template.Child()
    save_button: Gtk.Button = Gtk.Template.Child()
    readonly_banner: Adw.Banner = Gtk.Template.Child()
    title_row: Adw.EntryRow = Gtk.Template.Child()
    location_row: Adw.EntryRow = Gtk.Template.Child()
    map_button: Gtk.Button = Gtk.Template.Child()
    weather_row: Adw.ActionRow = Gtk.Template.Child()
    weather_icon: Gtk.Image = Gtk.Template.Child()
    hours_row: Adw.ActionRow = Gtk.Template.Child()
    hours_icon: Gtk.Image = Gtk.Template.Child()
    overlap_row: Adw.ActionRow = Gtk.Template.Child()
    travel_row: Adw.ComboRow = Gtk.Template.Child()
    travel_minutes_row: Adw.SpinRow = Gtk.Template.Child()
    links_group: Adw.PreferencesGroup = Gtk.Template.Child()
    add_link_row: Adw.ButtonRow = Gtk.Template.Child()
    attach_row: Adw.ButtonRow = Gtk.Template.Child()
    all_day_row: Adw.SwitchRow = Gtk.Template.Child()
    start_row: Adw.ActionRow = Gtk.Template.Child()
    end_row: Adw.ActionRow = Gtk.Template.Child()
    tz_row: Adw.ComboRow = Gtk.Template.Child()
    repeat_row: Adw.ComboRow = Gtk.Template.Child()
    interval_row: Adw.SpinRow = Gtk.Template.Child()
    weekday_row: Adw.ActionRow = Gtk.Template.Child()
    weekday_box: Gtk.Box = Gtk.Template.Child()
    monthly_row: Adw.ComboRow = Gtk.Template.Child()
    ends_row: Adw.ComboRow = Gtk.Template.Child()
    until_row: Adw.ActionRow = Gtk.Template.Child()
    count_row: Adw.SpinRow = Gtk.Template.Child()
    reminders_group: Adw.PreferencesGroup = Gtk.Template.Child()
    add_reminder_row: Adw.ButtonRow = Gtk.Template.Child()
    calendar_row: Adw.ComboRow = Gtk.Template.Child()
    color_box: Adw.WrapBox = Gtk.Template.Child()
    notes_view: Gtk.TextView = Gtk.Template.Child()
    delete_group: Adw.PreferencesGroup = Gtk.Template.Child()
    delete_row: Adw.ButtonRow = Gtk.Template.Child()

    def __init__(self, event: Event, *, heading: str, calendars: list[CalendarChoice],
                 on_save: Callable[[Event], None], on_delete: Callable[[], None] | None = None,
                 calendar_color: str | None = None, new: bool = False,
                 overlaps: Callable[[datetime, datetime], list[str]] | None = None,
                 online: bool = False, near: tuple[float, float] | None = None):
        """online: look up the location (suggestions, forecast, opening hours);
        near: where to look first."""
        super().__init__(title=heading)
        self._overlaps = overlaps
        self._online, self._near = online, near
        self._geo, self._place = event.geo, event.place  # where the location is, when known
        self._place_timer = 0
        self._place_generation = 0  # to drop answers about an older location or time
        self._links: list[str] = list(event.attachments)
        self._link_rows: list[Adw.ActionRow] = []
        self._event = event
        self._on_save = on_save
        self._on_delete = on_delete
        self._loading = True
        self._readonly = event.readonly
        self._tzid = event.tzid or ical.local_tzid()
        self._monthly_options: list = []
        self._reminder_rows: list[Adw.ComboRow] = []

        self.cancel_button.connect("clicked", lambda _b: self.close())
        self.save_button.connect("clicked", lambda _b: self._save())
        if new:
            self.save_button.set_label(_("_Add"))
        if self._readonly:
            # Nothing to save: just the standard close button.
            self.cancel_button.set_visible(False)
            self.save_button.set_visible(False)
            self.header.set_show_end_title_buttons(True)
            self.readonly_banner.set_revealed(True)
        else:
            self.set_default_widget(self.save_button)
            self.set_focus(self.title_row)

        self.title_row.set_text(event.title)
        self.location_row.set_text(event.location)
        self._setup_schedule(event)
        self._setup_repeat(event)
        for minutes in event.reminders:
            self._add_reminder(minutes)
        self.add_reminder_row.connect("activated", lambda _r: self._add_reminder(10))
        self._setup_calendar(event, calendars, calendar_color)
        self.notes_view.get_buffer().set_text(event.notes)
        self.delete_group.set_visible(bool(on_delete) and not self._readonly)
        self.delete_row.connect("activated", lambda _r: self._delete())
        self.map_button.connect("clicked", lambda _b: self._show_map())
        self.location_row.connect("changed", lambda *_args: self._update_map_button())
        self._update_map_button()
        self._place_search = None
        if online and not self._readonly:
            self._place_search = PlaceSearch(self.location_row, self._on_place_picked,
                                             near=lambda: self._near, enabled=lambda: self._online)
        self.location_row.connect("changed", lambda *_args: self._on_location_typed())
        self.add_link_row.connect("activated", lambda _r: self._ask_link())
        self.attach_row.connect("activated", lambda _r: self._ask_file())
        if event.url:
            self._add_link_row(event.url, removable=False)
        for uri in self._links:
            self._add_link_row(uri)

        # As shown in the pickers: an all-day end is the last day itself, not the day after.
        self._duration = self._end() - self._start()
        self._loading = False
        self._on_all_day_changed()
        self._update_repeat_rows()
        self._initial_rule = self._rule()  # to tell whether the repeat rule was changed
        self._validate()
        if self._readonly:
            self._make_readonly()

    def _make_readonly(self) -> None:
        """Nothing can be changed, but links and the map still open."""
        for row in (self.title_row, self.location_row):
            row.set_editable(False)
        self.notes_view.set_editable(False)
        for widget in (self.all_day_row, self.start_row, self.end_row, self.tz_row, self.travel_row,
                       self.travel_minutes_row, self.repeat_row, self.interval_row, self.weekday_row, self.monthly_row,
                       self.ends_row, self.until_row, self.count_row, self.reminders_group,
                       self.calendar_row, self.color_box):
            widget.set_sensitive(False)
        self.add_link_row.set_visible(False)
        self.attach_row.set_visible(False)
        self.links_group.set_visible(bool(self._link_rows))

    # -- setup --------------------------------------------------------------------

    def _setup_schedule(self, ev: Event) -> None:
        self.all_day_row.set_active(ev.all_day)
        self.all_day_row.connect("notify::active", lambda *_args: self._on_all_day_changed(toggled=True))

        # The pickers show wall-clock time in the event's time zone.
        start = ev.start if ev.all_day else ical.to_zone(ev.start, self._tzid)
        end = ev.end if ev.all_day else ical.to_zone(ev.end, self._tzid)
        if ev.all_day:  # stored exclusive (midnight after the last day), shown inclusive
            end = max(end - timedelta(days=1), start)
        self._start_date, self._start_time = self._when(self.start_row, start, self._on_start_changed)
        self._end_date, self._end_time = self._when(self.end_row, end, self._on_end_changed)

        # The presets, then "Custom…", which shows a row to type any number of minutes.
        values = [v for _label, v in TRAVEL_CHOICES]
        self._travel_values = values
        self.travel_row.set_model(Gtk.StringList.new(
            [_(label) for label, _v in TRAVEL_CHOICES] + [_("Custom…")]))
        if ev.travel_minutes in values:
            self.travel_row.set_selected(values.index(ev.travel_minutes))
        else:
            self.travel_minutes_row.set_value(ev.travel_minutes)
            self.travel_row.set_selected(len(values))
        self._on_travel_changed()
        self.travel_row.connect("notify::selected", lambda *_args: self._on_travel_changed(True))

        zones = ical.all_tzids()
        if self._tzid not in zones:
            zones.insert(0, self._tzid)
        self._zones = zones
        self.tz_row.set_model(Gtk.StringList.new([ical.zone_label(z) for z in zones]))
        self.tz_row.set_expression(Gtk.PropertyExpression.new(Gtk.StringObject, None, "string"))
        self.tz_row.set_selected(zones.index(self._tzid))
        self.tz_row.connect("notify::selected", lambda *_args: self._on_tz_changed())

    @staticmethod
    def _when(row: Adw.ActionRow, dt: datetime, on_change):
        date_button = DateButton(dt.date(), on_change)
        time_button = TimeButton(dt.time(), on_change)
        row.add_suffix(date_button)
        row.add_suffix(time_button)
        row.set_activatable_widget(date_button)  # the row's mnemonic opens the date picker
        return date_button, time_button

    def _setup_repeat(self, ev: Event) -> None:
        self._custom_rule = ev.rrule_extra if ev.freq == Freq.NONE else ""
        labels = [_(c[0]) for c in REPEAT_CHOICES]
        if self._custom_rule:
            labels.append(_("Custom (kept as is)"))
        self.repeat_row.set_model(Gtk.StringList.new(labels))
        if self._custom_rule:
            self.repeat_row.set_selected(len(REPEAT_CHOICES))
        else:
            self.repeat_row.set_selected(
                next(i for i, c in enumerate(REPEAT_CHOICES) if c[1] == ev.freq))
        self.repeat_row.connect("notify::selected", lambda *_args: self._on_freq_changed())
        self.interval_row.set_value(ev.interval)
        self.interval_row.connect("notify::value", lambda *_args: self._update_repeat_rows())

        chosen = {d for d, n in ev.byday if n == 0} or {ev.start.weekday()}
        monday = date(2026, 9, 21)  # any Monday, just for day names
        self._weekday_buttons = []
        for i in range(7):
            wd = (i + first_weekday()) % 7
            day = monday + timedelta(days=wd)
            button = Gtk.ToggleButton(label=day.strftime("%a")[:2], active=wd in chosen,
                                      tooltip_text=day.strftime("%A"))
            button.weekday = wd
            button.connect("toggled", lambda *_args: self._validate())
            self._weekday_buttons.append(button)
            self.weekday_box.append(button)

        self._initial_monthly = [b for b in ev.byday if b[1] != 0]
        self.ends_row.set_model(Gtk.StringList.new([_(c) for c in STOPS_CHOICES]))
        self.ends_row.set_selected(2 if ev.count else 1 if ev.until else 0)
        self.ends_row.connect("notify::selected", lambda *_args: self._update_repeat_rows())
        self._until_date = DateButton(ev.until or (ev.start + timedelta(days=90)).date(),
                                      lambda: None)
        self.until_row.add_suffix(self._until_date)
        self.until_row.set_activatable_widget(self._until_date)
        self.count_row.set_value(ev.count or 10)

    def _add_reminder(self, minutes: int) -> None:
        values = [v for _label, v in REMINDER_CHOICES]
        labels = [_(label) for label, _v in REMINDER_CHOICES]
        if minutes not in values:
            values.append(minutes)
            labels.append(_reminder_label(minutes))
        row = Adw.ComboRow(title=_("Reminder"), model=Gtk.StringList.new(labels))
        row.values = values
        row.set_selected(values.index(minutes))
        remove = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=_("Remove reminder"), css_classes=["flat"])
        remove.connect("clicked", lambda _b: self._remove_reminder(row))
        row.add_suffix(remove)
        # Keep "Add Reminder" last.
        self.reminders_group.remove(self.add_reminder_row)
        self.reminders_group.add(row)
        self.reminders_group.add(self.add_reminder_row)
        self._reminder_rows.append(row)

    def _remove_reminder(self, row: Adw.ComboRow) -> None:
        self._reminder_rows.remove(row)
        self.reminders_group.remove(row)

    def _setup_calendar(self, ev: Event, calendars: list[CalendarChoice],
                        calendar_color: str | None) -> None:
        if ev.calendar and ev.calendar not in [c.uid for c in calendars]:
            calendars = calendars + [CalendarChoice(ev.calendar, _("Read-only calendar"),
                                                    calendar_color or EVENT_COLORS[-1][1])]
        self._calendar_choices = calendars
        self.calendar_row.set_model(Gtk.StringList.new([c.name for c in calendars]))
        uids = [c.uid for c in calendars]
        self.calendar_row.set_selected(uids.index(ev.calendar) if ev.calendar in uids else 0)

        self._color = ev.color
        default = Gtk.ToggleButton(tooltip_text=_("Calendar color"), active=ev.color is None,
                                   css_classes=["event-color", color_css_class(
                                       calendar_color or EVENT_COLORS[0][1])])
        default.connect("toggled", self._on_color_toggled, None)
        self._on_color_toggled(default, None)
        self.color_box.append(default)
        colors = list(EVENT_COLORS)
        if ev.color and ev.color not in (c[1] for c in colors):
            colors.append((N_("Custom"), ev.color))
        for name, hex_color in colors:
            button = Gtk.ToggleButton(tooltip_text=_(name), active=hex_color == ev.color,
                                      group=default,
                                      css_classes=["event-color", color_css_class(hex_color)])
            button.connect("toggled", self._on_color_toggled, hex_color)
            self._on_color_toggled(button, hex_color)
            self.color_box.append(button)

    # -- location and links -------------------------------------------------------

    def _update_map_button(self) -> None:
        self.map_button.set_visible(bool(self.location_row.get_text().strip()))

    def _show_map(self) -> None:
        place = self.location_row.get_text().strip()
        query = GLib.Uri.escape_string(place, None, False)
        # GNOME Maps if installed, otherwise OpenStreetMap in the browser.
        open_uri(f"maps:q={query}", self,
                 fallback=f"https://www.openstreetmap.org/search?query={query}")

    def _add_link_row(self, uri: str, removable: bool = True) -> None:
        title, subtitle = link_label(uri)
        row = Adw.ActionRow(title=title, subtitle=subtitle, use_markup=False, activatable=True,
                            tooltip_text=uri)
        row.add_prefix(Gtk.Image(icon_name="mail-attachment-symbolic" if uri.startswith("file:")
                                 else "insert-link-symbolic", accessible_role=Gtk.AccessibleRole.PRESENTATION))
        row.connect("activated", lambda _r: open_uri(uri, self))
        row.add_suffix(Gtk.Image(icon_name="adw-external-link-symbolic",
                                 accessible_role=Gtk.AccessibleRole.PRESENTATION))
        if removable and not self._readonly:
            remove = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER,
                                tooltip_text=_("Remove link"), css_classes=["flat"])
            remove.connect("clicked", lambda _b: self._remove_link(row, uri))
            row.add_suffix(remove)
        # Keep the "add" buttons last.
        for button in (self.add_link_row, self.attach_row):
            self.links_group.remove(button)
        self.links_group.add(row)
        for button in (self.add_link_row, self.attach_row):
            self.links_group.add(button)
        self._link_rows.append(row)

    def _remove_link(self, row: Adw.ActionRow, uri: str) -> None:
        self._links.remove(uri)
        self._link_rows.remove(row)
        self.links_group.remove(row)

    def _ask_link(self) -> None:
        dialog = Adw.AlertDialog(heading=_("Add Link"),
                                 body=_("Add a web page or other link to the event."))
        entry = Adw.EntryRow(title=_("_Link"), use_underline=True, activates_default=True)
        box = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        box.append(entry)
        dialog.set_extra_child(box)
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("add", _("_Add"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")
        dialog.set_response_enabled("add", False)
        entry.connect("changed", lambda e: dialog.set_response_enabled(
            "add", normalize_link(e.get_text()) is not None))

        def on_response(_d, response):
            link = normalize_link(entry.get_text())
            if response == "add" and link and link not in self._links:
                self._links.append(link)
                self._add_link_row(link)

        dialog.connect("response", on_response)
        dialog.present(self)
        entry.grab_focus()

    def _ask_file(self) -> None:
        def on_file(dialog, result):
            try:
                file = dialog.open_finish(result)
            except GLib.Error:
                return  # cancelled
            uri = file.get_uri()
            if uri not in self._links:
                self._links.append(uri)
                self._add_link_row(uri)

        Gtk.FileDialog(title=_("Attach File")).open(self.get_root(), None, on_file)

    def _update_overlaps(self) -> None:
        titles = []
        if self._overlaps and not self.all_day_row.get_active() and not self._readonly:
            start, end = ical.from_zone(self._start(), self._tzid), ical.from_zone(self._end(), self._tzid)
            if end > start:
                titles = self._overlaps(start, end)
        if titles:
            text = (_("Overlaps with “{title}”").format(title=titles[0]) if len(titles) == 1
                    else ngettext("Overlaps with “{title}” and {n} more",
                                  "Overlaps with “{title}” and {n} more",
                                  len(titles) - 1).format(title=titles[0], n=len(titles) - 1))
            self.overlap_row.set_title(text)
        self.overlap_row.set_visible(bool(titles))

    # -- behaviour --------------------------------------------------------------

    def _on_color_toggled(self, button: Gtk.ToggleButton, hex_color: str | None) -> None:
        # The "calendar color" swatch shows a calendar icon, and the chosen one a
        # check mark, so the choice doesn't rely on color alone.
        icon = "object-select-symbolic" if button.get_active() else (
            "x-office-calendar-symbolic" if hex_color is None else None)
        button.set_child(Gtk.Image(icon_name=icon) if icon else None)
        if button.get_active():
            self._color = hex_color

    def _start(self) -> datetime:
        return datetime.combine(self._start_date.get_date(), self._start_time.get_time())

    def _end(self) -> datetime:
        return datetime.combine(self._end_date.get_date(), self._end_time.get_time())

    def _set_end(self, dt: datetime) -> None:
        self._loading = True
        self._end_date.set_date(dt.date())
        self._end_time.set_time(dt.time())
        self._loading = False

    def _custom_travel(self) -> bool:
        return self.travel_row.get_selected() == len(self._travel_values)

    def _on_travel_changed(self, picked: bool = False) -> None:
        custom = self._custom_travel() and self.travel_row.get_visible()
        self.travel_minutes_row.set_visible(custom)
        if custom and picked:
            self.travel_minutes_row.grab_focus()

    def _travel_minutes(self) -> int:
        if self._custom_travel():
            return int(self.travel_minutes_row.get_value())
        return self._travel_values[self.travel_row.get_selected()]

    def _on_all_day_changed(self, toggled: bool = False) -> None:
        all_day = self.all_day_row.get_active()
        self._start_time.set_visible(not all_day)
        self._end_time.set_visible(not all_day)
        self.tz_row.set_visible(not all_day)
        self.travel_row.set_visible(not all_day)
        self._on_travel_changed()
        if toggled and not all_day:
            self._loading = True
            self._start_time.set_time(time(9))
            self._loading = False
            self._set_end(self._start() + timedelta(hours=1))
            self._duration = timedelta(hours=1)
        self._update_tz_hint()
        self._validate()

    # -- the location: forecast and opening hours -------------------------------------

    def _on_place_picked(self, place: Place) -> None:
        self._geo = (place.lat, place.lon)
        self._place = place.osm if place.is_establishment else ""
        self._update_place_info()

    def _on_location_typed(self) -> None:
        if self._loading or (self._place_search and self._place_search._quiet):
            return
        # Typed by hand: the picked place (if any) no longer matches.
        self._geo, self._place = None, ""
        self._schedule_place_info(1000)

    def _schedule_place_info(self, delay_ms: int = 300) -> None:
        if self._place_timer:
            GLib.source_remove(self._place_timer)

        def run():
            self._place_timer = 0
            self._update_place_info()
            return GLib.SOURCE_REMOVE

        self._place_timer = GLib.timeout_add(delay_ms, run)

    def _update_place_info(self) -> None:
        """The forecast when the event starts and, for shops and the like, whether
        they're open then. For a location typed by hand, its best match is used."""
        self._place_generation += 1
        generation = self._place_generation
        text = self.location_row.get_text().strip()
        start, end = self._start(), self._end()
        all_day = self.all_day_row.get_active()
        if all_day:
            start = datetime.combine(self._start_date.get_date(), time(12))
        if not self._online or not places.worth_looking_up(text) or end < datetime.now():
            self.weather_row.set_visible(False)
            self.hours_row.set_visible(False)
            return

        def current() -> bool:
            return generation == self._place_generation

        def show_weather(forecast) -> None:
            if not current():
                return
            cond = forecast and (forecast.days.get(start.date()) if all_day else forecast.at(start))
            self.weather_row.set_visible(bool(cond))
            if cond:
                self.weather_icon.set_from_icon_name(cond.icon)
                temperature = cond.range_text if all_day else cond.temperature_text
                self.weather_row.set_title(_("{temperature}, {summary}").format(
                    temperature=temperature, summary=cond.summary))

        def show_place(found) -> None:
            """What the place is and, for a business, whether it's open then."""
            if not current():
                return
            if found is None or not found.kind:  # an address, a town…: nothing to say
                self.hours_row.set_visible(False)
                return
            hours = OpeningHours.parse(found.hours) if found.hours else None
            if all_day:
                fine, status = None, found.name or found.kind
            elif hours:
                fine, status = hours.status(start, end)
            else:
                fine, status = None, _("Opening hours not listed")
            self.hours_row.set_title(status)
            summary = found.summary
            if found.hours:
                summary = f"{summary}\n{found.hours}" if summary else found.hours
            self.hours_row.set_subtitle(summary)
            self.hours_icon.set_from_icon_name({True: "emblem-ok-symbolic", False: "dialog-warning-symbolic",
                                                None: "dialog-information-symbolic"}[fine])
            self.hours_row.set_visible(True)

        def located(lat: float, lon: float, osm: str) -> None:
            if not current():
                return
            weather.forecast(lat, lon, show_weather)
            if osm:
                places.details(osm, show_place)
            else:
                self.hours_row.set_visible(False)

        if self._geo:
            located(*self._geo, self._place)
        else:
            def matched(place) -> None:
                if place is None:
                    self.weather_row.set_visible(False)
                    self.hours_row.set_visible(False)
                    return
                business = place.is_establishment and places.names_match(text, place)
                located(place.lat, place.lon, place.osm if business else "")

            places.find(text, self._near, matched)

    def _on_start_changed(self) -> None:
        if self._loading:
            return
        # Moving the start keeps the duration, like other calendar apps.
        self._set_end(self._start() + self._duration)
        self._update_monthly_choices()
        self._update_tz_hint()
        self._validate()

    def _on_end_changed(self) -> None:
        if self._loading:
            return
        if self._end() >= self._start():
            self._duration = self._end() - self._start()
        self._validate()

    def _on_tz_changed(self) -> None:
        # Keep the wall-clock time; it's now in the newly chosen zone.
        self._tzid = self._zones[self.tz_row.get_selected()]
        self._update_tz_hint()

    def _update_tz_hint(self) -> None:
        if self.all_day_row.get_active() or self._tzid == ical.local_tzid():
            self.tz_row.set_subtitle("")
            return
        start_local = ical.from_zone(self._start(), self._tzid)
        self.tz_row.set_subtitle(_("Your time: {day} {time}").format(
            day=start_local.strftime("%a"), time=fmt_time(start_local)))

    def _freq(self) -> str | None:
        """The chosen frequency, or None for a kept custom rule."""
        i = self.repeat_row.get_selected()
        return REPEAT_CHOICES[i][1] if i < len(REPEAT_CHOICES) else None

    def _on_freq_changed(self) -> None:
        self._update_repeat_rows()
        self._validate()

    def _update_monthly_choices(self) -> None:
        d = self._start_date.get_date()
        n, is_last = _week_of_month(d)
        day = d.strftime("%A")
        options = [(_("Day {n}").format(n=d.day), ("day", d.day)),
                   (_(ORDINALS[n]).format(weekday=day), ("nth", n))]
        if is_last:
            options.append((_(ORDINALS[-1]).format(weekday=day), ("nth", -1)))
        previous = (self._monthly_options[self.monthly_row.get_selected()][1]
                    if self._monthly_options else None)
        self._monthly_options = options
        self.monthly_row.set_model(Gtk.StringList.new([o[0] for o in options]))
        keys = [o[1] for o in options]
        if previous in keys:
            self.monthly_row.set_selected(keys.index(previous))
        elif previous and previous[0] == "nth":
            self.monthly_row.set_selected(len(options) - 1 if previous[1] == -1 and is_last else 1)
        else:
            self.monthly_row.set_selected(0)

    def _update_repeat_rows(self) -> None:
        if not self._monthly_options:
            self._update_monthly_choices()
            if self._initial_monthly:
                want = ("nth", self._initial_monthly[0][1])
                keys = [o[1] for o in self._monthly_options]
                if want in keys:
                    self.monthly_row.set_selected(keys.index(want))
        freq = self._freq()
        editable = freq not in (None, Freq.NONE)
        self.interval_row.set_subtitle(_interval_text(freq, int(self.interval_row.get_value())))
        self.interval_row.set_visible(editable)
        self.weekday_row.set_visible(freq == Freq.WEEKLY)
        self.monthly_row.set_visible(freq == Freq.MONTHLY)
        self.ends_row.set_visible(editable)
        stops = self.ends_row.get_selected()
        self.until_row.set_visible(editable and stops == 1)
        self.count_row.set_visible(editable and stops == 2)

    def _validate(self) -> None:
        self._update_overlaps()
        self._schedule_place_info()  # the times changed
        if self.all_day_row.get_active():
            valid = self._end_date.get_date() >= self._start_date.get_date()
        else:
            valid = self._end() >= self._start()
        (self.end_row.remove_css_class if valid else self.end_row.add_css_class)("error")
        self.end_row.set_subtitle("" if valid else _("Ends before it starts"))
        weekly_ok = (self._freq() != Freq.WEEKLY
                     or any(b.get_active() for b in self._weekday_buttons))
        self.save_button.set_sensitive(valid and weekly_ok and not self._readonly)

    def result_event(self) -> Event:
        """The edited event. Keeps the original identity and iCalendar data."""
        all_day = self.all_day_row.get_active()
        if all_day:
            start = datetime.combine(self._start_date.get_date(), time())
            end = datetime.combine(self._end_date.get_date() + timedelta(days=1), time())
            tzid = None
        else:
            tzid = self._tzid
            start = ical.from_zone(self._start(), tzid)
            end = ical.from_zone(self._end(), tzid)
        buffer = self.notes_view.get_buffer()
        notes = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)
        ev = self._event.clone(
            title=self.title_row.get_text().strip() or _("Untitled Event"),
            start=start, end=end, all_day=all_day, tzid=tzid,
            location=self.location_row.get_text().strip(), notes=notes.strip(), color=self._color,
            reminders=sorted({r.values[r.get_selected()] for r in self._reminder_rows}),
            attachments=list(self._links),
            travel_minutes=0 if all_day else self._travel_minutes(),
            geo=self._geo, place=self._place,
            calendar=self._calendar_choices[self.calendar_row.get_selected()].uid,
        )
        ev.uid, ev.raw, ev.id = self._event.uid, self._event.raw, self._event.id

        rule = self._rule()
        if rule is None or rule == self._initial_rule:
            # A custom rule, or one left as it was: keep it verbatim (implicit days,
            # RRULE parts we don't edit), so it still matches the series.
            return ev
        for name, value in rule.items():
            setattr(ev, name, value)
        return ev

    def _rule(self) -> dict | None:
        """The repeat rule as chosen in the editor, or None for a kept custom rule."""
        freq = self._freq()
        if freq is None:
            return None
        rule = {"freq": freq, "interval": 1, "byday": [], "bymonthday": None,
                "count": None, "until": None, "rrule_extra": ""}
        if freq == Freq.NONE:
            return rule
        rule["interval"] = int(self.interval_row.get_value())
        if freq == Freq.WEEKLY:
            rule["byday"] = [(b.weekday, 0) for b in self._weekday_buttons if b.get_active()]
        elif freq == Freq.MONTHLY:
            kind, n = self._monthly_options[self.monthly_row.get_selected()][1]
            if kind == "nth":  # the weekday of the start date as shown (in the event's zone)
                rule["byday"] = [(self._start_date.get_date().weekday(), n)]
            else:
                rule["bymonthday"] = n
        elif freq == Freq.YEARLY and self._event.freq == Freq.YEARLY:
            rule["rrule_extra"] = self._event.rrule_extra  # e.g. BYMONTH we don't edit
        stops = self.ends_row.get_selected()
        rule["until"] = self._until_date.get_date() if stops == 1 else None
        rule["count"] = int(self.count_row.get_value()) if stops == 2 else None
        return rule

    def _save(self) -> None:
        if not self.save_button.get_sensitive():
            return
        event = self.result_event()
        self.close()
        self._on_save(event)

    def _delete(self) -> None:
        self.close()
        self._on_delete()


SCOPE_TEXTS = {  # kind: (heading, body)
    "change": (N_("Change Recurring Event?"), N_("“{title}” repeats. Choose which events to change.")),
    "move": (N_("Move Recurring Event?"), N_("“{title}” repeats. Choose which events to move.")),
    "delete": (N_("Delete Recurring Event?"), N_("“{title}” repeats. Choose which events to delete.")),
}


def ask_scope(parent: Gtk.Widget, kind: str, title: str,
              callback: Callable[[str | None], None]) -> None:
    """Ask which occurrences of a recurring event a change applies to.

    kind: "change", "move" or "delete". Calls callback with a Scope value, or
    None when cancelled.
    """
    heading, body = SCOPE_TEXTS[kind]
    dialog = Adw.AlertDialog(heading=_(heading), body=_(body).format(title=title))
    dialog.add_response("cancel", _("_Cancel"))
    dialog.add_response(Scope.ALL, _("_All Events"))
    dialog.add_response(Scope.FUTURE, _("This and _Following"))
    dialog.add_response(Scope.THIS, _("_Only This Event"))
    dialog.set_response_appearance(
        Scope.THIS, Adw.ResponseAppearance.DESTRUCTIVE if kind == "delete"
        else Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response(Scope.THIS)
    dialog.set_close_response("cancel")
    dialog.connect("response", lambda _d, r: callback(None if r == "cancel" else r))
    dialog.present(parent)
