# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""The bottom bar, like a dock: calendar buttons, the next event, tasks to do,
weather, world clocks and shortcut buttons. Which of them and in what order is
up to the user (Preferences → Bottom Bar)."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from gi.repository import Adw, GObject, Gtk, Pango

from . import worldclock
from .draw import fmt_time
from .i18n import N_, _, ngettext
from .style import color_css_class

if TYPE_CHECKING:
    from .window import Window

# id, name, description (in Preferences)
ITEMS = [
    ("calendars", N_("Calendars"), N_("A button for each calendar, to show or hide it")),
    ("next-event", N_("Next event"), N_("What’s coming up, and when")),
    ("tasks-due", N_("Tasks for today"), N_("How many tasks are due today or overdue")),
    ("weather", N_("Weather"), N_("The weather now, for the place set under Weather")),
    ("world-clocks", N_("World clocks"), N_("The time in the cities under World Clocks")),
    ("new-event", N_("New event button"), None),
    ("today", N_("Today button"), None),
    ("search", N_("Search button"), None),
    ("jump", N_("Go to date button"), None),
    ("print", N_("Print button"), None),
]
BUTTONS = {  # icon, tooltip, action
    "new-event": ("list-add-symbolic", N_("New event"), "win.new-event"),
    "today": ("navcal-today-symbolic", N_("Go to today"), "win.today"),
    "search": ("system-search-symbolic", N_("Search"), "win.search"),
    "jump": ("go-jump-symbolic", N_("Go to date"), "win.jump"),
    "print": ("document-print-symbolic", N_("Print"), "win.print"),
}
NEXT_EVENT_DAYS = 7  # how far ahead "Next event" looks


def shown_items(config) -> list[str]:
    known = {item for item, _name, _description in ITEMS}
    return [item for item in (config["dock"] or []) if item in known]


class Dock:
    """Builds the bar's items and keeps them up to date."""

    def __init__(self, window: Window, box: Gtk.Box):
        self.window, self.box = window, box
        self._items: list[tuple[str, Gtk.Widget]] = []
        self._chips_state = None
        self.rebuild()

    def rebuild(self) -> None:
        """The chosen items changed."""
        while child := self.box.get_first_child():
            self.box.remove(child)
        self._items, self._chips_state = [], None
        for item in shown_items(self.window.config):
            widget = self._make(item)
            self.box.append(widget)
            self._items.append((item, widget))
        self.update()

    def update(self) -> None:
        """Events, tasks, calendars or the time changed."""
        for item, widget in self._items:
            update = getattr(self, "_update_" + item.replace("-", "_"), None)
            if update:
                update(widget)

    def _make(self, item: str) -> Gtk.Widget:
        if item in BUTTONS:
            icon, tooltip, action = BUTTONS[item]
            return Gtk.Button(icon_name=icon, tooltip_text=_(tooltip), action_name=action,
                              valign=Gtk.Align.CENTER, css_classes=["flat"])
        if item == "calendars":
            box = Gtk.Box(spacing=6)
            box.update_property([Gtk.AccessibleProperty.LABEL], [_("Calendars")])
            return box
        if item == "weather":
            box = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, margin_start=6, margin_end=6)
            box.append(Gtk.Image())
            box.append(Gtk.Label(css_classes=["numeric"]))
            return box
        # Next event, tasks and clocks: a flat button with a label, opening what it's about.
        # Never squeezed to nothing: on a narrow window the bar scrolls instead.
        label = Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=36, width_chars=12)
        content = Gtk.Box(spacing=6)
        if item == "next-event":
            content.append(Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["event-dot"]))
        else:
            icon = "checkbox-checked-symbolic" if item == "tasks-due" else "preferences-system-time-symbolic"
            content.append(Gtk.Image(icon_name=icon))
        content.append(label)
        button = Gtk.Button(child=content, valign=Gtk.Align.CENTER, css_classes=["flat"])
        button.label = label
        button.connect("clicked", lambda b, item=item: self._activate(item, b))
        return button

    def _activate(self, item: str, button: Gtk.Button) -> None:
        w = self.window
        if item == "next-event" and getattr(button, "occurrence", None):
            w._show_occurrence(button.occurrence)
        elif item in ("tasks-due", "world-clocks"):
            w.show_sidebar_section("tasks" if item == "tasks-due" else "clocks")

    # -- items --------------------------------------------------------------------

    def _update_calendars(self, box: Gtk.Box) -> None:
        cals = self.window.backend.sorted_calendars()
        state = [(c.uid, c.name, c.color, c.visible) for c in cals]
        if state == self._chips_state:
            return
        self._chips_state = state
        while child := box.get_first_child():
            box.remove(child)
        for cal in cals:
            dot = Gtk.Box(valign=Gtk.Align.CENTER, css_classes=["event-dot", color_css_class(cal.color)])
            label = Gtk.Label(label=cal.name, max_width_chars=16)
            if len(cal.name) > 10:  # short names are never cut; long ones keep a few letters
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_width_chars(8)
            content = Gtk.Box(spacing=6)
            content.append(dot)
            content.append(label)
            chip = Gtk.ToggleButton(child=content, active=cal.visible,
                                    tooltip_text=_("Show or hide this calendar"))
            chip.update_property([Gtk.AccessibleProperty.LABEL], [cal.name])
            # When hidden the dot disappears and the name dims, not just the color.
            dot.set_opacity(1 if cal.visible else 0)
            (label.remove_css_class if cal.visible else label.add_css_class)("dim-label")
            chip.connect("toggled", lambda b, c=cal: self.window.backend.set_visible(c, b.get_active()))
            box.append(chip)

    def _update_next_event(self, button: Gtk.Button) -> None:
        now = datetime.now()
        occs = self.window.backend.occurrences(now, now + timedelta(days=NEXT_EVENT_DAYS))
        upcoming = [o for o in occs if not o.is_banner and o.start > now]
        occ = min(upcoming, key=lambda o: o.start) if upcoming else None
        button.occurrence = occ
        dot = button.get_child().get_first_child()
        for css in [c for c in dot.get_css_classes() if c.startswith("event-color-")]:
            dot.remove_css_class(css)
        dot.set_visible(occ is not None)
        if occ is None:
            button.label.set_label(_("Nothing coming up"))
            button.set_tooltip_text(None)
            return
        dot.add_css_class(color_css_class(occ.color))
        title, minutes = occ.event.title, int((occ.start - now).total_seconds() // 60) + 1
        if minutes <= 60:
            text = ngettext("{title} in {n} minute", "{title} in {n} minutes", minutes).format(
                title=title, n=minutes)
        elif occ.start.date() == now.date():
            text = _("{title} at {time}").format(title=title, time=fmt_time(occ.start))
        elif occ.start.date() == now.date() + timedelta(days=1):
            text = _("{title} tomorrow at {time}").format(title=title, time=fmt_time(occ.start))
        else:
            text = _("{title} on {day} at {time}").format(title=title, day=occ.start.strftime("%A"),
                                                          time=fmt_time(occ.start))
        button.label.set_label(text)
        button.set_tooltip_text(_("Show the next event"))

    def _update_tasks_due(self, button: Gtk.Button) -> None:
        today = date.today()
        due = [t for t in self.window.backend.tasks() if not t.done and t.due_date and t.due_date <= today]
        button.label.set_label(ngettext("{n} task for today", "{n} tasks for today", len(due)).format(n=len(due))
                               if due else _("No tasks for today"))
        button.set_tooltip_text(_("Show tasks"))

    def _update_world_clocks(self, button: Gtk.Button) -> None:
        zones = self.window.config["world-clocks"]
        if not zones:
            button.label.set_label(_("No world clocks"))
        else:
            button.label.set_label(" · ".join(
                _("{city} {time}").format(city=worldclock.city_name(z), time=worldclock.describe(z)[0])
                for z in zones))
        button.set_tooltip_text(_("Show world clocks"))

    def _update_weather(self, box: Gtk.Box) -> None:
        weather = getattr(self.window, "weather", None)
        now = weather.now() if weather else None
        box.set_visible(now is not None)
        if now:
            image, label = box.get_first_child(), box.get_last_child()
            image.set_from_icon_name(now.icon)
            label.set_label(now.temperature_text)
            box.set_tooltip_text(now.summary)


class DockPage(Adw.PreferencesPage):
    """Preferences → Bottom Bar: show the bar, and pick and order its items."""

    def __init__(self, window: Window):
        super().__init__(title=_("Bottom Bar"), icon_name="view-grid-symbolic", name="dock")
        self.window, self.config = window, window.config
        top = Adw.PreferencesGroup()
        show = Adw.SwitchRow(title=_("Show the _bottom bar"), use_underline=True,
                             active=self.config["footbar"])
        show.connect("notify::active", self._on_show)
        top.add(show)
        self.add(top)
        self._group = Adw.PreferencesGroup(title=_("Items"),
                                           description=_("What the bar shows, from left to right"))
        show.bind_property("active", self._group, "sensitive", GObject.BindingFlags.SYNC_CREATE)
        self.add(self._group)
        self._rows: list[Adw.ActionRow] = []
        self._fill()

    def _on_show(self, row, _pspec) -> None:
        self.config["footbar"] = row.get_active()
        self.window.apply_settings()

    def _fill(self, focus: tuple[str, str] | None = None) -> None:
        """Rows for the shown items in their order, then the others."""
        for row in self._rows:
            self._group.remove(row)
        self._rows = []
        shown = shown_items(self.config)
        names = {item: (name, description) for item, name, description in ITEMS}
        order = shown + [item for item, _n, _d in ITEMS if item not in shown]
        for item in order:
            name, description = names[item]
            row = Adw.ActionRow(title=_(name), subtitle=_(description) if description else "")
            index = shown.index(item) if item in shown else -1
            up = Gtk.Button(icon_name="go-up-symbolic", tooltip_text=_("Move up"), valign=Gtk.Align.CENTER,
                            sensitive=index > 0, css_classes=["flat"])
            down = Gtk.Button(icon_name="go-down-symbolic", tooltip_text=_("Move down"),
                              valign=Gtk.Align.CENTER, sensitive=0 <= index < len(shown) - 1,
                              css_classes=["flat"])
            switch = Gtk.Switch(active=index >= 0, valign=Gtk.Align.CENTER)
            up.connect("clicked", lambda _b, it=item: self._move(it, -1))
            down.connect("clicked", lambda _b, it=item: self._move(it, 1))
            switch.connect("notify::active", lambda s, _p, it=item: self._toggle(it, s.get_active()))
            for widget in (up, down, switch):
                row.add_suffix(widget)
            row.set_activatable_widget(switch)
            self._group.add(row)
            self._rows.append(row)
            if focus and focus[0] == item:  # keep the keyboard where it was
                target = {"up": up, "down": down, "switch": switch}[focus[1]]
                (target if target.get_sensitive() else switch).grab_focus()

    def _save(self, shown: list[str], focus: tuple[str, str]) -> None:
        self.config["dock"] = shown
        self.window.dock.rebuild()
        self._fill(focus)

    def _move(self, item: str, step: int) -> None:
        shown = shown_items(self.config)
        i = shown.index(item)
        j = max(0, min(len(shown) - 1, i + step))
        shown[i], shown[j] = shown[j], shown[i]
        self._save(shown, (item, "up" if step < 0 else "down"))

    def _toggle(self, item: str, on: bool) -> None:
        shown = shown_items(self.config)
        if on and item not in shown:
            shown.append(item)
        elif not on and item in shown:
            shown.remove(item)
        else:
            return
        self._save(shown, (item, "switch"))
