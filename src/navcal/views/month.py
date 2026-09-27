"""Month grid: six weeks of day cells with event chips."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from ..draw import (event_bar, fill_rect, focus_ring, fmt_time_short, hex_rgb, hline,
                    is_weekend, landing, line_height, symbolic_icon, warning_badge, spoken_day, text, text_width, vline,
                    week_number, week_start)
from ..layout import DragState
from ..i18n import _, ngettext
from ..models import Occurrence, days_covered
from .canvas import Canvas, Hits

GAP = 2  # between chips


class MonthView(Canvas):
    def __init__(self, host):
        super().__init__(host, _("Month"))
        self._anchor = date.today()
        self._target: date = self._anchor
        self._occs: list[Occurrence] = []
        self._selected_keys: set = set()
        self._hits = Hits()
        self._move: DragState | None = None  # anchor is the day the chip was grabbed on
        self._landing: Occurrence | None = None  # a copy being placed, at its drop day
        self._drop_day: date | None = None

    # -- interface shared with TimeGridView ---------------------------------

    def set_anchor(self, d: date) -> None:
        self._anchor = d
        self.queue_draw()

    def set_target(self, target) -> None:
        self._target = target.date() if isinstance(target, datetime) else target
        self.queue_draw()

    def set_selected(self, keys) -> None:
        self._selected_keys = set(keys)
        self.queue_draw()

    def set_occurrences(self, occs: list[Occurrence]) -> None:
        self._occs = occs
        self.queue_draw()

    def visible_range(self) -> tuple[datetime, datetime]:
        start = datetime.combine(self._grid_start(), time())
        return start, start + timedelta(days=42)

    def tick(self) -> None:
        self.queue_draw()

    def next_anchor(self, direction: int) -> date:
        month = self._anchor.month - 1 + direction
        return date(self._anchor.year + month // 12, month % 12 + 1, 1)

    def period_title(self, short: bool = False) -> str:
        return self._anchor.strftime(_("%b %Y") if short else "%B")

    def page_title(self) -> str:
        return _("Month")

    def _columns(self, start: date) -> list[int]:
        """Weekday columns shown (0–6 from the week start), without weekends if hidden."""
        hide = self.host.config["hide-weekends"]
        return [c for c in range(7) if not (hide and is_weekend(start + timedelta(days=c)))]

    # -- drawing ------------------------------------------------------------

    def _grid_start(self) -> date:
        return week_start(self._anchor.replace(day=1))

    def _bucket(self, start: date) -> dict[date, list[Occurrence]]:
        end = start + timedelta(days=42)
        by_day: dict[date, list[Occurrence]] = defaultdict(list)
        m = self._move
        self._landing = None  # a copy being placed: shown first in its days, as an outline
        if m and m.active and m.has_target and self.drag_copy:
            self._landing = m.preview()
        for occ in ([self._landing] if self._landing else []) + self._occs:  # bars first, then by time
            for d in days_covered(occ.start, occ.end):
                if start <= d < end:
                    by_day[d].append(occ)
        return by_day

    def draw(self, cr, w, h):
        pal = self.palette()
        body, heading = self.theme.font("body"), self.theme.font("heading")
        caption, caption_heading = self.theme.font("caption"), self.theme.font("caption-heading")
        header_h = line_height(self, caption_heading) + 12
        daynum_h = line_height(self, heading) + 12
        chip_h = line_height(self, caption) + 6
        start = self._grid_start()
        cols = self._columns(start)
        numbers_w = (text_width(self, "53", caption_heading) + 18
                     if self.host.config["week-numbers"] else 0)
        cw, ch = (w - numbers_w) / len(cols), (h - header_h) / 6
        today = date.today()
        by_day = self._bucket(start)
        self._hits.clear()

        for ci, c in enumerate(cols):
            name = (start + timedelta(days=c)).strftime("%a")
            text(cr, self, name, numbers_w + ci * cw, 0, cw, header_h, pal.dim,
                 font=caption_heading, align="center")

        slots = max(0, int((ch - daynum_h - GAP) // (chip_h + GAP)))
        compact = slots == 0  # too short for chips: show colored dots instead
        accessible = []
        for r in range(6):
            if numbers_w:
                week = week_number(start + timedelta(days=7 * r))
                text(cr, self, str(week), 0, header_h + r * ch + 6, numbers_w - 6,
                     daynum_h - 12, pal.dim, font=caption_heading, align="center")
            for ci, c in enumerate(cols):
                d = start + timedelta(days=7 * r + c)
                x, y = numbers_w + ci * cw, header_h + r * ch
                day_occs = by_day.get(d, [])
                accessible.append((spoken_day(d, len(day_occs)), (x, y, cw, ch),
                                   [self.host.spoken_event(o) for o in day_occs]))
                self._draw_cell(cr, pal, d, x, y, cw, ch, by_day.get(d, []), today, slots,
                                compact, daynum_h, chip_h, body, heading, caption,
                                caption_heading)

        for ci in range(1, len(cols)):
            vline(cr, numbers_w + ci * cw, header_h, h, pal.line)
        if numbers_w:
            vline(cr, numbers_w, header_h, h, pal.line)
        for r in range(6):
            hline(cr, 0, w, header_h + r * ch, pal.line)

        self.set_accessible_items(accessible)
        if self.show_focus():
            offset = (self._target - start).days
            if 0 <= offset < 42 and offset % 7 in cols:
                r, ci = offset // 7, cols.index(offset % 7)
                focus_ring(cr, numbers_w + ci * cw, header_h + r * ch, cw, ch, pal)

    def _draw_cell(self, cr, pal, d, x, y, cw, ch, occs, today, slots, compact, daynum_h,
                   chip_h, body, heading, caption, caption_heading):
        in_month = d.month == self._anchor.month
        cr.save()
        cr.rectangle(x, y, cw, ch)
        cr.clip()
        if not in_month:
            fill_rect(cr, x, y, cw, ch, pal.fg_alpha(0.03))
        if d == self._target:
            fill_rect(cr, x, y, cw, ch, pal.accent_alpha(0.1))
        if d == self._drop_day:
            fill_rect(cr, x, y, cw, ch, pal.accent_alpha(0.25))

        label = d.strftime(_("%b %-d")) if d.day == 1 and not compact else str(d.day)
        num_h = min(daynum_h - 12, ch - 8)
        if d == today:
            pill_w = max(num_h + 6, text_width(self, label, heading) + 12)
            fill_rect(cr, x + 6, y + 6, pill_w, num_h + 2, pal.accent_bg, (num_h + 2) / 2)
            text(cr, self, label, x + 6, y + 6, pill_w, num_h + 2, pal.accent_fg,
                 font=heading, align="center")
        else:
            text(cr, self, label, x + 12, y + 6, cw - 18, num_h + 2,
                 pal.fg if in_month else pal.dim, font=heading if d.day == 1 else body)
        cond = None if compact else self.host.weather.day(d)
        if cond:  # the day's forecast (high and low, or just the high), on the date's line at the right
            icon, room = line_height(self, caption), cw - text_width(self, label, heading) - 36
            temp = cond.range_text
            tw = text_width(self, temp, caption)
            if tw + icon > room:
                temp = cond.temperature_text
                tw = text_width(self, temp, caption)
            if tw + icon <= room:
                right = x + cw - 8
                text(cr, self, temp, right - tw, y + 6, tw, num_h + 2, pal.dim, font=caption)
                symbolic_icon(cr, self, cond.icon, right - tw - icon - 4, y + 6 + (num_h + 2 - icon) / 2,
                              icon, pal.dim)

        if compact:
            self._draw_dots(cr, x, y, cw, ch, occs, label, heading)
        else:
            shown = occs if len(occs) <= slots else occs[: max(slots - 1, 0)]
            cy = y + daynum_h
            for occ in shown:
                self._draw_chip(cr, pal, x + 6, cy, cw - 12, chip_h, occ, d, caption)
                self._hits.add(x + 6, cy, cw - 12, chip_h, "occ", (occ, d))
                cy += chip_h + GAP
            if len(shown) < len(occs):
                more = len(occs) - len(shown)
                text(cr, self, ngettext("{n} more", "{n} more", more).format(n=more), x + 12, cy,
                     cw - 18, chip_h,
                     pal.dim, font=caption_heading)
                self._hits.add(x + 6, cy, cw - 12, chip_h, "more", d)
        self._hits.add(x, y, cw, ch, "day", d)
        cr.restore()

    def _draw_dots(self, cr, x, y, w, h, occs: list[Occurrence], label: str, font):
        """Small screens: a colored dot per event (up to what fits) after the day number."""
        if not occs:
            return
        size, gap = 6, 3
        dot_x = x + 12 + text_width(self, label, font) + 12
        room = int((x + w - dot_x) // (size + gap))
        cy = y + 6 + min(line_height(self, font), h - 8) / 2 + 1
        if room < 1:  # no room beside the number: one row under it
            dot_x, room = x + 6, int((w - 6) // (size + gap))
            cy = y + h - size - 2
        for occ in occs[:room]:
            cr.set_source_rgba(*hex_rgb(occ.color))
            cr.arc(dot_x + size / 2, cy, size / 2, 0, 6.2832)
            cr.fill()
            dot_x += size + gap

    def _chip_label(self, occ: Occurrence, d: date) -> str:
        timed = not occ.is_banner and occ.start.date() == d
        return f"{fmt_time_short(occ.start)} {occ.event.title}" if timed else occ.event.title

    def draw_drag_copy(self, cr, occ, x, y, w, h) -> None:
        event_bar(cr, self, x, y, w, h, occ.color, self._chip_label(occ, occ.start.date()), False,
                  self.theme.font("caption"))

    def _draw_chip(self, cr, pal, x, y, w, h, occ: Occurrence, d: date, font):
        if occ is self._landing:
            landing(cr, x, y, w, h, occ.color)
            text(cr, self, self._chip_label(occ, d), x + 6, y, w - 12, h, pal.dim, font=font)
            return
        selected = occ.key in self._selected_keys
        title = occ.event.title
        warning = bool(self.host.place_warning(occ))  # its place won't be open
        if occ.is_banner or selected:
            label = title if occ.is_banner else f"{fmt_time_short(occ.start)} {title}"
            event_bar(cr, self, x, y, w, h, occ.color, label, selected, font, radius=6, warning=warning)
            return
        # Timed event: colored dot, then time and title.
        cr.set_source_rgba(*hex_rgb(occ.color))
        cr.arc(x + 6, y + h / 2, 4, 0, 6.2832)
        cr.fill()
        badge = warning_badge(cr, self, x + w, y, h, pal.warning) if warning else 0
        label = f"{fmt_time_short(occ.start)} {title}" if occ.start.date() == d else title
        text(cr, self, label, x + 14, y, w - 16 - badge, h, pal.fg, font=font)

    # -- keyboard -----------------------------------------------------------

    def on_move(self, direction: str) -> None:
        days = {"left": -1, "right": 1, "up": -7, "down": 7}[direction]
        target = self._target + timedelta(days=days)
        while self.host.config["hide-weekends"] and is_weekend(target):
            target += timedelta(days=-1 if days < 0 else 1)
        self.host.move_target(target)

    # -- pointer ------------------------------------------------------------

    def on_press(self, x, y):
        kind, payload, _box = self._hits.find(x, y)
        if kind is None:
            return
        occ, d = payload if kind == "occ" else (None, payload)
        self.host.select(occ, d, self.press_mode)
        if kind == "more":
            self.host.open_day(d)

    def on_double(self, x, y):
        kind, payload, _box = self._hits.find(x, y)
        self._move = None
        if kind == "occ":
            self.host.edit_occurrence(payload[0])
        elif kind == "day":
            start = datetime.combine(payload, time(9))
            self.host.create_event(start, None, False)

    def on_drag_begin(self, x, y):
        kind, payload, box = self._hits.find(x, y)
        self._move = (DragState(payload[0], payload[1], grab=(x - box[0], y - box[1], box[2], box[3]))
                      if kind == "occ" and not payload[0].event.readonly else None)

    def on_drag_update(self, x, y, dx, dy):
        m = self._move
        if not m or not m.update_active(dx, dy):
            return
        _kind, day, _box = self._hits.find(x, y, "day")
        if day:
            shift = timedelta(days=(day - m.anchor).days)
            m.start, m.end = m.occ.start + shift, m.occ.end + shift
            self._drop_day = day
        self.set_cursor_from_name(self.drag_cursor())
        self.queue_draw()

    def on_drag_end(self, x, y):
        m, self._move = self._move, None
        self._drop_day = None
        self.set_cursor_from_name(None)
        self.queue_draw()
        if m and m.has_target and (m.changed or (m.active and self.drag_copy)):
            self.host.move_occurrence(m.occ, m.start, m.end, copy=self.drag_copy)
