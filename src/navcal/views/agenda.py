# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Agenda view: the coming days as lists of events."""

from __future__ import annotations

import time as clock
from datetime import date, datetime, time, timedelta

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from ..draw import fmt_time, uses_12h_clock
from ..i18n import _
from ..models import Occurrence, days_covered
from ..style import color_css_class
from ..tasks import task_row

DAYS = 30
BUILD_SECONDS = 0.012  # time spent building rows before letting the window draw


def day_heading(d: date) -> str:
    today = date.today()
    if d == today:
        return _("Today, {date}").format(date=d.strftime(_("%B %-d")))
    if d == today + timedelta(days=1):
        return _("Tomorrow, {date}").format(date=d.strftime(_("%B %-d")))
    return d.strftime(_("%A, %B %-d") if d.year == today.year else _("%A, %B %-d, %Y"))


def when_text(occ: Occurrence, d: date) -> str:
    if occ.is_banner:
        days = list(days_covered(occ.start, occ.end))
        if len(days) > 1:
            return _("All day · day {n} of {total}").format(n=days.index(d) + 1, total=len(days))
        return _("All day")
    return f"{fmt_time(occ.start)} – {fmt_time(occ.end)}"


class AgendaView(Gtk.ScrolledWindow):
    def __init__(self, host):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.host = host
        self.anchor = date.today()
        self._rows: list[Adw.ActionRow] = []
        # date -> (what the day shows, its section widget, its event rows)
        self._days: dict[date, tuple[tuple, Gtk.Widget, list[Adw.ActionRow]]] = {}
        self._generation = 0  # of set_occurrences calls, to stop building for older ones
        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                            margin_top=12, margin_bottom=24, margin_start=12, margin_end=12)
        self._empty = Adw.StatusPage(icon_name="view-list-symbolic", title=_("No Events"),
                                     description=_("Nothing is planned for these 30 days"),
                                     vexpand=True)
        stack = Gtk.Stack()
        stack.add_named(Adw.Clamp(child=self._box, maximum_size=720), "list")
        stack.add_named(self._empty, "empty")
        self._stack = stack
        self.set_child(stack)

    # -- interface shared with the other views --------------------------------

    def set_anchor(self, d: date) -> None:
        self.anchor = d

    def set_target(self, target) -> None:
        pass

    def set_selected(self, keys) -> None:
        pass  # the focused row is the selection; its focus ring shows it

    def set_occurrences(self, occs: list[Occurrence]) -> None:
        by_day: dict[date, list[Occurrence]] = {}
        start, end = self.visible_range()
        for occ in occs:
            for d in days_covered(occ.start, occ.end):
                if start.date() <= d < end.date():
                    by_day.setdefault(d, []).append(occ)
        tasks_by_day: dict[date, list] = {}
        for task in self.host.backend.tasks():
            if task.due_date and start.date() <= task.due_date < end.date():
                tasks_by_day.setdefault(task.due_date, []).append(task)
        # Building rows is slow, so a day is only built again when what it shows
        # changed. Changed events and tasks are new objects, so identity tells.
        backend, today, twelve_hour = self.host.backend, date.today(), uses_12h_clock()
        old, self._days, self._rows = self._days, {}, []
        todo = []
        for d in sorted(set(by_day) | set(tasks_by_day)):
            shown = (today, twelve_hour, tuple(id(t) for t in tasks_by_day.get(d, [])), tuple(
                (id(o.event), o.start, o.end, o.color, getattr(backend.calendar(o.event.calendar), "name", ""),
                 self.host.place_warning(o)) for o in by_day.get(d, [])))
            day = old.get(d)
            todo.append((d, shown, day if day and day[0] == shown else None,
                         by_day.get(d, []), tasks_by_day.get(d, [])))
        kept = {day[1] for _d, _shown, day, _o, _t in todo if day}
        child = self._box.get_first_child()
        while child:
            following = child.get_next_sibling()
            if child not in kept:
                self._box.remove(child)
            child = following
        self._stack.set_visible_child_name("list" if todo else "empty")
        self._generation += 1
        self._build(self._generation, todo, None)

    def _build(self, generation: int, todo: list, previous: Gtk.Widget | None,
               ready: list[Gtk.Widget] | None = None) -> bool:
        """Put the days in todo in place, building the new ones. A long agenda is
        built a little at a time so the window keeps drawing: the first days
        appear at once, the rest are built between frames and shown together
        (adding them one by one would lay out the growing list each time)."""
        if generation != self._generation:
            return GLib.SOURCE_REMOVE  # the agenda changed again meanwhile
        stop = clock.perf_counter() + BUILD_SECONDS
        while todo:
            d, shown, day, occs, tasks = todo.pop(0)
            if day is None:
                day = (shown, *self._section(d, occs, tasks))
            self._days[d] = day
            self._rows.extend(day[2])
            if ready is None:
                previous = self._place(day[1], previous)
            else:
                ready.append(day[1])
            if todo and clock.perf_counter() > stop:
                GLib.idle_add(self._build, generation, todo, previous, [] if ready is None else ready)
                return GLib.SOURCE_REMOVE
        for section in ready or []:
            previous = self._place(section, previous)
        return GLib.SOURCE_REMOVE

    def _place(self, section: Gtk.Widget, previous: Gtk.Widget | None) -> Gtk.Widget:
        if section.get_parent() is None:
            self._box.insert_child_after(section, previous)
        elif section.get_prev_sibling() is not previous:
            self._box.reorder_child_after(section, previous)
        return section

    def _section(self, d: date, occs: list[Occurrence], tasks: list) -> tuple[Gtk.Widget, list]:
        """A day's heading and its list of events and tasks."""
        section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        section.append(Gtk.Label(label=day_heading(d), xalign=0, margin_top=12, css_classes=["heading"]))
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        listbox.connect("row-activated", self._on_row_activated)
        rows = [self._row(occ, d) for occ in occs]
        for row in rows:
            listbox.append(row)
        for task in tasks:
            listbox.append(task_row(task, self.host.toggle_task, self.host.edit_task))
        section.append(listbox)
        return section, rows

    def _on_row_activated(self, _list, row) -> None:
        if hasattr(row, "occurrence"):
            self.host.edit_occurrence(row.occurrence)

    def _row(self, occ: Occurrence, d: date) -> Adw.ActionRow:
        details = [when_text(occ, d)]
        if occ.event.location:
            details.append(occ.event.location)
        warning = self.host.place_warning(occ)
        if warning:  # its place won't be open then
            details.append(warning)
        cal = self.host.backend.calendar(occ.event.calendar)
        row = Adw.ActionRow(title=occ.event.title, subtitle=" · ".join(details),
                            use_markup=False, activatable=True)
        row.add_prefix(Gtk.Box(valign=Gtk.Align.CENTER,
                               css_classes=["event-dot", color_css_class(occ.color)]))
        if warning:
            row.add_suffix(Gtk.Image(icon_name="dialog-warning-symbolic", tooltip_text=warning,
                                     css_classes=["warning"]))
        if cal:
            row.add_suffix(Gtk.Label(label=cal.name, css_classes=["dim-label", "caption"],
                                     ellipsize=Pango.EllipsizeMode.END, max_width_chars=18))
        row.occurrence = occ
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_args: self.host.select(occ, d))
        row.add_controller(focus)
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        click.connect("pressed", lambda _g, _n, x, y: (self.host.select(occ, d),
                                                        self.host.show_context_menu(row, x, y)))
        row.add_controller(click)
        return row

    def visible_range(self) -> tuple[datetime, datetime]:
        start = datetime.combine(self.anchor, time())
        return start, start + timedelta(days=DAYS)

    def next_anchor(self, direction: int) -> date:
        return self.anchor + timedelta(days=DAYS * direction)

    def period_title(self, short: bool = False) -> str:
        a, b = self.anchor, self.anchor + timedelta(days=DAYS - 1)
        return f"{a.strftime(_('%b %-d'))} – {b.strftime(_('%b %-d'))}"

    def page_title(self) -> str:
        return _("Agenda")

    def tick(self) -> None:
        pass

    def grab_focus(self) -> bool:
        return self._rows[0].grab_focus() if self._rows else super().grab_focus()
