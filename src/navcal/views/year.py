"""Year view: twelve small months; days with events get a dot."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

from gi.repository import GLib, Gtk

from ..draw import (fill_rect, focus_ring, hex_rgb, is_weekend, line_height, text, week_start)
from ..models import Occurrence, days_covered
from ..i18n import _, ngettext
from ..draw import spoken_day
from .canvas import Canvas, Hits

MIN_MONTH_W = 168


class YearView(Gtk.ScrolledWindow):
    def __init__(self, host):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.host = host
        self.canvas = _YearCanvas(host, self)
        self.set_child(self.canvas)
        self.anchor = date.today()
        self.target: date = self.anchor
        self.colors: dict[date, list[str]] = {}

    # -- interface shared with the other views --------------------------------

    def set_anchor(self, d: date) -> None:
        self.anchor = d
        self.canvas.queue_draw()

    def set_target(self, target) -> None:
        self.target = target.date() if isinstance(target, datetime) else target
        self.canvas.queue_draw()

    def set_selected(self, keys) -> None:
        pass  # individual events aren't shown

    def set_occurrences(self, occs: list[Occurrence]) -> None:
        colors: dict[date, list[str]] = defaultdict(list)
        for occ in occs:
            for d in days_covered(occ.start, occ.end):
                colors[d].append(occ.color)
        self.colors = colors
        self.canvas.queue_draw()

    def visible_range(self) -> tuple[datetime, datetime]:
        return (datetime(self.anchor.year, 1, 1), datetime(self.anchor.year + 1, 1, 1))

    def next_anchor(self, direction: int) -> date:
        year = self.anchor.year + direction
        return date(year, self.anchor.month, min(self.anchor.day, 28))

    def period_title(self, short: bool = False) -> str:
        return str(self.anchor.year)

    def page_title(self) -> str:
        return _("Year")

    def tick(self) -> None:
        self.canvas.queue_draw()

    def grab_focus(self) -> bool:
        return self.canvas.grab_focus()

    def reveal(self, target) -> None:
        box = self.canvas.cell_box(target if isinstance(target, date) else target.date())
        if box:
            adj = self.get_vadjustment()
            y, h = box[1], box[3]
            if y < adj.get_value():
                adj.set_value(y)
            elif y + h > adj.get_value() + adj.get_page_size():
                adj.set_value(y + h - adj.get_page_size())


class _YearCanvas(Canvas):
    def __init__(self, host, view: YearView):
        super().__init__(host, _("Year"))
        self.view = view
        self._hits = Hits()
        self._height = 0

    def _metrics(self):
        caption, heading = self.theme.font("caption"), self.theme.font("heading")
        title_h = line_height(self, heading) + 12
        names_h = line_height(self, self.theme.font("caption-heading")) + 6
        cell_h = line_height(self, caption) + 12
        return title_h, names_h, cell_h, title_h + names_h + 6 * cell_h + 18

    def draw(self, cr, w, h):
        pal = self.palette()
        caption, heading = self.theme.font("caption"), self.theme.font("heading")
        caption_heading = self.theme.font("caption-heading")
        title_h, names_h, cell_h, block_h = self._metrics()
        cols = max(1, min(4, int((w - 12) // MIN_MONTH_W)))
        rows = -(-12 // cols)
        needed = int(rows * block_h + 12)
        if needed != self._height:  # grow with large text; resize outside drawing
            self._height = needed
            GLib.idle_add(lambda: self.set_content_height(needed) and False)
        month_w = (w - 12) / cols
        cell_w = (month_w - 12) / 7
        year, today = self.view.anchor.year, date.today()
        self._hits.clear()
        accessible = []

        for m in range(12):
            mx = 6 + (m % cols) * month_w + 6
            my = 6 + (m // cols) * block_h
            first = date(year, m + 1, 1)
            text(cr, self, first.strftime("%B"), mx + 6, my + 6, month_w - 24, title_h - 6,
                 pal.accent if first.month == today.month and year == today.year else pal.fg,
                 font=heading)
            busy = [d for d in sorted(self.view.colors) if d.year == year and d.month == m + 1]
            accessible.append((
                ngettext("{month}, {n} day with events", "{month}, {n} days with events",
                         len(busy)).format(month=first.strftime("%B %Y"), n=len(busy)),
                (mx, my, month_w, block_h),
                [spoken_day(d, len(self.view.colors[d])) for d in busy]))
            start = week_start(first)
            for c in range(7):
                name = (start + timedelta(days=c)).strftime("%a")[:1]
                text(cr, self, name, mx + c * cell_w, my + title_h, cell_w, names_h, pal.dim,
                     font=caption_heading, align="center")
            for i in range(42):
                d = start + timedelta(days=i)
                if d.month != first.month:
                    continue
                x = mx + (i % 7) * cell_w
                y = my + title_h + names_h + (i // 7) * cell_h
                self._draw_day(cr, pal, d, x, y, cell_w, cell_h, today, caption)
                self._hits.add(x, y, cell_w, cell_h, "day", d)

        self.set_accessible_items(accessible)
        if self.show_focus():
            box = self.cell_box(self.view.target)
            if box:
                focus_ring(cr, *box, pal)

    def _draw_day(self, cr, pal, d, x, y, w, h, today, font):
        label = str(d.day)
        size = min(w, h) - 4
        cx, cy = x + w / 2, y + (h - 6) / 2
        if d == self.view.target:
            fill_rect(cr, cx - size / 2, cy - size / 2, size, size, pal.accent_alpha(0.15), size / 2)
        if d == today:
            fill_rect(cr, cx - size / 2, cy - size / 2, size, size, pal.accent_bg, size / 2)
        color = pal.accent_fg if d == today else pal.dim if is_weekend(d) else pal.fg
        text(cr, self, label, x, cy - h / 2 + 3, w, h - 6, color, font=font, align="center")
        colors = self.view.colors.get(d)
        if colors:
            cr.set_source_rgba(*hex_rgb(colors[0]))
            cr.arc(cx, y + h - 5, 2.5, 0, 6.2832)
            cr.fill()

    def cell_box(self, d: date):
        return next(((x, y, w, h) for x, y, w, h, kind, day in self._hits.items if day == d), None)

    # -- input --------------------------------------------------------------

    def on_move(self, direction: str) -> None:
        days = {"left": -1, "right": 1, "up": -7, "down": 7}[direction]
        self.host.move_target(self.view.target + timedelta(days=days))

    def on_press(self, x, y):
        _kind, d, _box = self._hits.find(x, y)
        if d:
            self.host.select(None, d, self.press_mode)

    def on_double(self, x, y):
        _kind, d, _box = self._hits.find(x, y)
        if d:
            self.host.open_day(d)
