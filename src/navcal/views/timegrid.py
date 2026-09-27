"""Week and day views: an all-day header above a scrollable 24-hour grid."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from gi.repository import Gdk, GLib, Gtk

from ..draw import (darker, event_bar, fill_rect, focus_ring, fmt_hour, fmt_time, hex_rgb, hline,
                    is_weekend, landing, line_height, symbolic_icon, warning_badge, spoken_day, spoken_event, stroke_rect, text, text_on,
                    text_width, vline, week_number, week_start)
from .. import ical, worldclock
from ..i18n import _, ngettext, pgettext
from ..layout import MIN_BLOCK, DragState, assign_lanes, layout_columns
from ..models import Occurrence, days_covered, overlaps
from .canvas import Canvas, Hits

SNAP_MIN = 15
KEY_STEP = timedelta(minutes=30)  # arrow keys move the focused time by this much
MIN_ZOOM, MAX_ZOOM = 0.5, 4.0  # how short or tall hours can be, times the usual height
ZOOM_STEP = 1.25  # one Ctrl+plus or Ctrl+minus
RESIZE_EDGE = 8  # px at the bottom of a block that act as the resize handle


@dataclass
class Metrics:
    """Sizes derived from the theme's fonts, so large text gets room to grow."""

    hour_h: int
    gutter: int
    second_w: int  # width of the second time zone's hour labels (0 if off)
    name_h: int
    lane_h: int
    caption_h: int
    weather_h: int = 0  # the weather line under the day numbers (0 if off)


class TimeGridView(Gtk.Box):
    """kind "day" shows one day; kind "week" shows a week, or 3 or 4 days
    (Preferences), optionally without weekends."""

    def __init__(self, host, kind: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.host = host
        self.kind = kind
        self._anchor = date.today()
        self.target: date | datetime | None = None
        self.selected_keys: set = set()
        self.banners: list[Occurrence] = []
        self.timed: list[Occurrence] = []
        self._metrics: Metrics | None = None
        self._metrics_key = None  # what the metrics were measured with

        name = _("Day") if kind == "day" else _("Week")
        self.header = _Header(host, self, _("{view}, all-day events").format(view=name))
        self.grid = _Grid(host, self, name)
        self.scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.scroller.set_child(self.grid)
        self.append(self.header)
        self.append(Gtk.Separator())
        self.append(self.scroller)

        # Start around the working day (or now, if earlier), and keep the scroll
        # position when switching views: GTK resets it while the view is hidden.
        self._scroll_hours: float = max(0, min(datetime.now().hour - 1, 7))  # the time at the top
        self._restoring = True
        adj = self.scroller.get_vadjustment()
        adj.connect("value-changed", self._remember_scroll)
        self.scroller.connect("unmap", lambda *_args: setattr(self, "_restoring", True))
        self.scroller.connect("map", lambda *_args: self.scroller.add_tick_callback(self._restore_scroll))

    # -- interface shared with MonthView ------------------------------------

    def set_anchor(self, d: date) -> None:
        self._anchor = d
        self._redraw()

    def set_target(self, target) -> None:
        self.target = target
        self._redraw()

    def set_selected(self, keys) -> None:
        self.selected_keys = set(keys)
        self._redraw()

    def set_occurrences(self, occs: list[Occurrence]) -> None:
        self.banners = [o for o in occs if o.is_banner]
        self.timed = [o for o in occs if not o.is_banner]
        self._redraw()

    def visible_range(self) -> tuple[datetime, datetime]:
        dates = self.dates()
        return (datetime.combine(dates[0], time()),
                datetime.combine(dates[-1] + timedelta(days=1), time()))

    def next_anchor(self, direction: int) -> date:
        """The anchor date one page before or after the current one."""
        if self.kind == "day":
            return self._anchor + timedelta(days=direction)
        if self.span() == 7:
            return self._anchor + timedelta(weeks=direction)
        dates = self.dates()
        d = dates[-1] + timedelta(days=1) if direction > 0 else dates[0] - timedelta(days=1)
        count = 0
        while True:  # step over as many (visible) days as the view shows
            if not (self.hide_weekends() and is_weekend(d)):
                count += 1
                if count == self.span():
                    return d if direction < 0 else dates[-1] + timedelta(days=1)
            d += timedelta(days=direction)

    def period_title(self, short: bool = False) -> str:
        dates = self.dates()
        a, b = dates[0], dates[-1]
        if self.kind == "day":
            return a.strftime(_("%a, %b %-d") if short else _("%A, %B %-d"))
        if a.month == b.month:  # "September 20 – 26"; translations may reorder the parts
            return _("{month} {start} – {end}").format(
                month=a.strftime("%b" if short else "%B"), start=a.day, end=b.day)
        return f"{a.strftime(_('%b %-d'))} – {b.strftime(_('%b %-d'))}"

    def page_title(self) -> str:
        span = self.span()
        if self.kind == "day":
            return _("Day")
        return _("Week") if span == 7 else ngettext("{n} Day", "{n} Days", span).format(n=span)

    def tick(self) -> None:
        self.grid.queue_draw()

    def grab_focus(self) -> bool:
        return self.grid.grab_focus()

    def reveal(self, target) -> None:
        """Scroll so a keyboard-focused time is visible."""
        if not isinstance(target, datetime):
            return
        m = self.metrics()
        adj = self.scroller.get_vadjustment()
        y0 = (target.hour + target.minute / 60) * m.hour_h
        y1 = y0 + m.hour_h * KEY_STEP.total_seconds() / 3600
        if y0 < adj.get_value():
            adj.set_value(y0)
        elif y1 > adj.get_value() + adj.get_page_size():
            adj.set_value(y1 - adj.get_page_size())

    def zoom(self, factor: float, focus_y: float | None = None) -> None:
        """Make the hours taller or shorter, keeping the time at focus_y (in the
        visible part; the middle if None) in place: under the fingers or pointer."""
        adj = self.scroller.get_vadjustment()
        if focus_y is None:
            focus_y = adj.get_page_size() / 2
        at = (adj.get_value() + focus_y) / self.metrics().hour_h
        zoom = round(_clamp_zoom(self.host.hour_zoom * factor), 3)  # so resetting gives exactly 1
        if zoom == self.host.hour_zoom:
            return
        self.host.set_hour_zoom(zoom)
        m = self.metrics()
        self.grid.set_content_height(24 * m.hour_h + 1)
        # The scroll range grows at the next layout; allow the new position now.
        adj.set_upper(max(adj.get_upper(), 24 * m.hour_h + 1))
        adj.set_value(at * m.hour_h - focus_y)
        self._redraw()

    # -- helpers for the child canvases -------------------------------------

    def metrics(self) -> Metrics:
        theme, w = self.host.theme, self.grid
        fonts = [theme.font(style) for style in ("caption", "caption-heading", "title-3")]
        noon, second_zone = fmt_hour(12), self.host.config["second-timezone"] or None
        # Measuring text is slow and this runs for every drawn frame and hit test,
        # so only measure again when the fonts, clock format or settings change.
        zoom, weather = self.host.hour_zoom, self.host.weather.enabled
        key = (tuple(f.to_string() for f in fonts), noon, second_zone, zoom, weather)
        if key == self._metrics_key and self._metrics:
            return self._metrics
        self._metrics_key = key
        caption_font, heading_font, title_font = fonts
        caption = line_height(w, caption_font)
        weather_h = caption + 6 if weather else 0
        name_h = line_height(w, heading_font) + line_height(w, title_font) + 18 + weather_h
        label_w = text_width(w, noon, caption_font)
        second_w = 0
        if second_zone:
            # Zones half an hour off (India, say) label their hours with minutes.
            on_the_half = any(worldclock.time_in(second_zone, datetime(2000, month, 1, 12)).minute
                              for month in (1, 7))
            widest = fmt_time(datetime(2000, 1, 1, 12, 30)) if on_the_half else noon
            second_w = text_width(w, widest, caption_font) + 18
        gutter = max(60, label_w + 18) + second_w
        m = Metrics(hour_h=round(max(48, 3 * caption + 12) * zoom), gutter=gutter, second_w=second_w,
                    weather_h=weather_h,
                    name_h=name_h, lane_h=caption + 8, caption_h=caption)
        if m != self._metrics:
            self._metrics = m
            # Heights depend on the fonts; resize outside of drawing.
            GLib.idle_add(self._apply_metrics)
        return m

    def _apply_metrics(self) -> bool:
        self.grid.set_content_height(24 * self._metrics.hour_h + 1)
        self.header.relayout()
        return GLib.SOURCE_REMOVE

    def span(self) -> int:
        return 1 if self.kind == "day" else self.host.config["week-span"]

    def hide_weekends(self) -> bool:
        return self.kind != "day" and self.host.config["hide-weekends"]

    def dates(self) -> list[date]:
        if self.kind == "day":
            return [self._anchor]
        if self.span() == 7:
            start = week_start(self._anchor)
            days = [start + timedelta(days=i) for i in range(7)]
            return [d for d in days if not is_weekend(d)] if self.hide_weekends() else days
        days, d = [], self._anchor
        if self.hide_weekends():
            while is_weekend(d):
                d += timedelta(days=1)
        while len(days) < self.span():
            if not (self.hide_weekends() and is_weekend(d)):
                days.append(d)
            d += timedelta(days=1)
        return days

    @property
    def days(self) -> int:
        return len(self.dates())

    def col_width(self, width: float) -> float:
        return (width - self.metrics().gutter) / self.days

    def col_at(self, x: float, width: float) -> int:
        gutter = self.metrics().gutter
        return max(0, min(self.days - 1, int((x - gutter) / self.col_width(width))))

    def _redraw(self) -> None:
        self.header.relayout()
        self.header.queue_draw()
        self.grid.queue_draw()

    def _remember_scroll(self, adj) -> None:
        if not self._restoring:
            self._scroll_hours = adj.get_value() / self.metrics().hour_h

    def _restore_scroll(self, _widget, _clock) -> bool:
        """Runs each frame after the view is shown, until the layout has settled.

        GTK resets the scroll position while a view is hidden and re-laid out,
        so re-apply the remembered position (or the start of the working day).
        """
        adj = self.scroller.get_vadjustment()
        hour_h = self.metrics().hour_h
        if adj.get_upper() < 24 * hour_h:
            return GLib.SOURCE_CONTINUE  # not laid out yet
        adj.set_value(self._scroll_hours * hour_h)
        self._settled = getattr(self, "_settled", 0) + 1
        if self._settled < 3:
            return GLib.SOURCE_CONTINUE
        self._settled = 0
        self._restoring = False
        return GLib.SOURCE_REMOVE


def _clamp_zoom(zoom: float) -> float:
    return min(MAX_ZOOM, max(MIN_ZOOM, zoom))


def _key_target(view: TimeGridView, direction: str) -> datetime:
    """The next keyboard-focused time slot."""
    target = view.target
    dates = view.dates()
    if not isinstance(target, datetime):
        day = target if isinstance(target, date) and target in dates else dates[0]
        return datetime.combine(day, time(9))
    if direction in ("left", "right"):
        day = target.date() + timedelta(days=-1 if direction == "left" else 1)
        while view.hide_weekends() and is_weekend(day):
            day += timedelta(days=-1 if direction == "left" else 1)
        return datetime.combine(day, target.time())
    moved = target + (KEY_STEP if direction == "down" else -KEY_STEP)
    return target if moved.date() != target.date() else moved  # stay within the day


class _Header(Canvas):
    """Day names plus lanes of all-day and multi-day events."""

    def __init__(self, host, view: TimeGridView, name: str):
        super().__init__(host, name)
        self.view = view
        self.set_vexpand(False)
        self._items: list[tuple[Occurrence, int, int, int]] = []
        self._hits = Hits()
        self._move: DragState | None = None
        self._ghost: Occurrence | None = None
        self._ghost_copy = False
        self.external_ghost: Occurrence | None = None
        self._lanes = 1

    def relayout(self) -> None:
        dates = self.view.dates()
        column = {d: i for i, d in enumerate(dates)}
        banners = self.view.banners
        self._ghost = None
        self._ghost_copy = False  # the ghost is a copy being placed: drawn as an outline
        if self._move and self._move.active:
            if not self.drag_copy:
                banners = [o for o in banners if o is not self._move.occ]
            if not self._move.crossed:  # dragged into the grid: the grid previews it
                self._ghost, self._ghost_copy = self._move.preview(), self.drag_copy
                banners.append(self._ghost)
        if self.external_ghost:  # a timed event dragged up from the grid
            self._ghost, self._ghost_copy = self.external_ghost, self.view.grid.drag_copy
            banners = banners + [self._ghost]
        spans = []
        for occ in banners:
            cols = [column[d] for d in days_covered(occ.start, occ.end) if d in column]
            if cols:
                spans.append((occ, min(cols), max(cols)))
        self._items, lanes = assign_lanes(spans)
        self._lanes = max(lanes, 1)
        if self.view._metrics:
            m = self.view._metrics
            self.set_content_height(m.name_h + self._lanes * m.lane_h + 6)

    def draw(self, cr, w, h):
        pal, m = self.palette(), self.view.metrics()
        caption, caption_heading = self.theme.font("caption"), self.theme.font("caption-heading")
        title = self.theme.font("title-3")
        cw = self.view.col_width(w)
        today = date.today()
        target = self.view.target
        target_day = target if type(target) is date else None
        day_name_h = line_height(self, caption_heading)
        num_h = m.name_h - m.weather_h - day_name_h - 18

        # Translators: the label of the all-day row, in a narrow column; keep it short.
        text(cr, self, pgettext("all-day row", "All day"), 0, m.name_h, m.gutter - 12, m.lane_h,
             pal.dim, font=caption,
             align="right")
        second = self.host.config["second-timezone"]
        if second:  # which column of hours is which
            here = worldclock.abbreviation(ical.local_tzid())
            text(cr, self, here, m.second_w, m.name_h - m.caption_h - 6,
                 m.gutter - m.second_w - 12, m.caption_h, pal.dim, font=caption, align="right")
            text(cr, self, worldclock.abbreviation(second), 0, m.name_h - m.caption_h - 6,
                 m.second_w - 12, m.caption_h, pal.fg_alpha(0.4), font=caption, align="right")
        if self.host.config["week-numbers"]:
            dates = self.view.dates()
            weeks = sorted({week_number(d) for d in dates})
            label = (_("Week {n}").format(n=weeks[0]) if len(weeks) == 1
                     else _("Weeks {first}–{last}").format(first=weeks[0], last=weeks[-1]))
            text(cr, self, label, 6, 6, m.gutter - 12, day_name_h * 2 + 6, pal.dim,
                 font=caption, align="right", valign="top")
        for c, d in enumerate(self.view.dates()):
            x = m.gutter + c * cw
            if d == target_day:
                fill_rect(cr, x, m.name_h, cw, h - m.name_h, pal.accent_alpha(0.1))
            is_today = d == today
            text(cr, self, d.strftime("%a"), x, 6, cw, day_name_h,
                 pal.accent if is_today else pal.dim, font=caption_heading, align="center")
            num_y = 6 + day_name_h + 6
            if is_today:
                pill_w = max(num_h + 6, text_width(self, str(d.day), title) + 18)
                fill_rect(cr, x + (cw - pill_w) / 2, num_y - 3, pill_w, num_h + 6,
                          pal.accent_bg, (num_h + 6) / 2)
            text(cr, self, str(d.day), x, num_y, cw, num_h,
                 pal.accent_fg if is_today else pal.fg, font=title, align="center")
            cond = self.host.weather.day(d) if m.weather_h else None
            if cond:  # the day's forecast: icon, high and low, centered under the date
                icon = m.caption_h
                label = cond.range_text
                tw = text_width(self, label, caption)
                if icon + 4 + tw > cw - 8:  # a narrow column: just the high
                    label = cond.temperature_text
                    tw = text_width(self, label, caption)
                wx = x + (cw - icon - 4 - tw) / 2
                wy = m.name_h - m.weather_h - 2
                symbolic_icon(cr, self, cond.icon, wx, wy + (m.caption_h - icon) / 2, icon, pal.dim)
                text(cr, self, label, wx + icon + 4, wy, tw + 2, m.caption_h, pal.dim, font=caption)

        self._hits.clear()
        for occ, c0, c1, lane in self._items:
            x0 = m.gutter + c0 * cw + 2
            bw = (c1 - c0 + 1) * cw - 4
            y = m.name_h + lane * m.lane_h + 1
            if occ is self._ghost and self._ghost_copy:
                landing(cr, x0, y, bw, m.lane_h - 3, occ.color)
                continue
            selected = occ is self._ghost or occ.key in self.view.selected_keys
            event_bar(cr, self, x0, y, bw, m.lane_h - 3, occ.color, occ.event.title, selected,
                      caption)
            if occ is not self._ghost:
                self._hits.add(x0, y, bw, m.lane_h - 3, "occ", occ)

        for c in range(1, self.view.days):
            vline(cr, m.gutter + c * cw, m.name_h, h, pal.line)
        banners = [occ for occ, *_rest in self._items if occ is not self._ghost]
        self.set_accessible_items([(
            ngettext("{n} all-day event", "{n} all-day events", len(banners)).format(n=len(banners)),
            (0, m.name_h, w, h - m.name_h),
            [f"{spoken_event(o)}, {o.start:%A}" for o in banners])])
        if self.show_focus() and target_day in self.view.dates():
            c = self.view.dates().index(target_day)
            focus_ring(cr, m.gutter + c * cw, m.name_h, cw, h - m.name_h, pal)

    def _day_at(self, x) -> date | None:
        if x < self.view.metrics().gutter:
            return None
        return self.view.dates()[self.view.col_at(x, self.get_width())]

    def on_move(self, direction: str) -> None:
        dates = self.view.dates()
        target = self.view.target
        day = target if type(target) is date and target in dates else (
            target.date() if isinstance(target, datetime) else dates[0])
        if direction == "down":  # into the time grid
            self.view.grid.grab_focus()
            self.host.move_target(datetime.combine(day, time(9)))
            return
        if direction in ("left", "right"):
            day += timedelta(days=-1 if direction == "left" else 1)
        self.host.move_target(day)

    def on_press(self, x, y):
        d = self._day_at(x)
        if d is None:
            return
        if y < self.view.metrics().name_h:
            if self.view.kind != "day":
                self.host.select(None, d, self.press_mode)
                self.host.open_day(d)
            return
        _kind, occ, _box = self._hits.find(x, y)
        self.host.select(occ, d, self.press_mode)

    def on_double(self, x, y):
        self._move = None
        if y < self.view.metrics().name_h:
            return
        _kind, occ, _box = self._hits.find(x, y)
        if occ:
            self.host.edit_occurrence(occ)
        elif d := self._day_at(x):
            start = datetime.combine(d, time())
            self.host.create_event(start, start + timedelta(days=1), True)

    def on_drag_begin(self, x, y):
        _kind, occ, box = self._hits.find(x, y)
        self._move = (DragState(occ, self.view.col_at(x, self.get_width()),
                                grab=(x - box[0], y - box[1], box[2], box[3]))
                      if occ and not occ.event.readonly else None)

    def on_drag_update(self, x, y, dx, dy):
        m = self._move
        if not m or not m.update_active(dx, dy):
            return
        grid = self.view.grid
        m.crossed = y > self.get_height()
        if m.crossed:  # into the time grid: becomes a timed event there
            adj = self.view.scroller.get_vadjustment()
            slot = grid._slot_at(x, y - self.get_height() + adj.get_value())
            duration = (self.host._default_duration() if m.occ.event.all_day
                        else min(m.occ.end - m.occ.start, timedelta(hours=23)))
            m.start, m.end = slot, slot + duration
            grid.external_ghost = m.preview()
        else:
            dates = self.view.dates()
            shift = dates[self.view.col_at(x, self.get_width())] - dates[m.anchor]  # columns may skip weekends
            m.start, m.end = m.occ.start + shift, m.occ.end + shift
            grid.external_ghost = None
        grid.queue_draw()
        self.set_cursor_from_name(self.drag_cursor())
        self.relayout()
        self.queue_draw()

    def on_drag_end(self, x, y):
        m, self._move = self._move, None
        self.view.grid.external_ghost = None
        self.view.grid.queue_draw()
        self.set_cursor_from_name(None)
        self.relayout()
        self.queue_draw()
        if m and (m.changed or (m.active and self.drag_copy)):
            self.host.move_occurrence(m.occ, m.start, m.end, copy=self.drag_copy,
                                      all_day=False if m.crossed else None)


class _Grid(Canvas):
    """The 24-hour grid with timed events."""

    def __init__(self, host, view: TimeGridView, name: str):
        super().__init__(host, name)
        self.view = view
        self.set_content_height(24 * 48 + 1)
        # Hits carry (occurrence, whether the block's bottom is the event's real end).
        self._hits = Hits()
        self._create: list[datetime] | None = None  # drag-to-create: [anchor slot, current slot]
        self._move: DragState | None = None
        self._ghost: Occurrence | None = None
        self.external_ghost: Occurrence | None = None  # an all-day event dragged down
        self._pointer_y: float | None = None

        # Zoom: pinch (touchpad or screen), or Ctrl+scroll.
        pinch = Gtk.GestureZoom(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        pinch.connect("begin", lambda *_args: setattr(self, "_pinch_zoom", host.hour_zoom))
        pinch.connect("scale-changed", self._on_pinch)
        self.add_controller(pinch)
        scroll = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def _slot_at(self, x, y, day: date | None = None) -> datetime:
        if day is None:
            day = self.view.dates()[self.view.col_at(x, self.get_width())]
        minutes = int(y / self.view.metrics().hour_h * 60) // SNAP_MIN * SNAP_MIN
        minutes = max(0, min(minutes, 24 * 60 - SNAP_MIN))
        return datetime.combine(day, time()) + timedelta(minutes=minutes)

    @staticmethod
    def _y(dt: datetime, day_start: datetime, m: Metrics) -> float:
        return (dt - day_start).total_seconds() / 3600 * m.hour_h

    def draw(self, cr, w, h):
        pal, m = self.palette(), self.view.metrics()
        caption, bold = self.theme.font("caption"), self.theme.font("caption-heading")
        cw = self.view.col_width(w)
        dates = self.view.dates()
        now = datetime.now()

        config = self.host.config
        if config["shade-work-hours"]:
            shade = pal.fg_alpha(0.08 if pal.high_contrast else 0.035)
            work_start, work_end = config["work-start"], max(config["work-end"], config["work-start"])
            for c, d in enumerate(dates):
                x = m.gutter + c * cw
                if is_weekend(d):
                    fill_rect(cr, x, 0, cw, h, shade)
                    continue
                fill_rect(cr, x, 0, cw, work_start * m.hour_h, shade)
                fill_rect(cr, x, work_end * m.hour_h, cw, (24 - work_end) * m.hour_h, shade)
        for hour in range(24):
            y = hour * m.hour_h
            hline(cr, m.gutter - 6, w, y, pal.line)
            hline(cr, m.gutter, w, y + m.hour_h / 2, pal.faint_line)
            if hour:
                text(cr, self, fmt_hour(hour), m.second_w, y - m.caption_h / 2,
                     m.gutter - m.second_w - 12, m.caption_h, pal.dim, font=caption, align="right")
                if m.second_w:
                    other = worldclock.time_in(self.host.config["second-timezone"],
                                               datetime.combine(dates[0], time(hour)))
                    label = fmt_hour(other.hour) if other.minute == 0 else fmt_time(other)
                    text(cr, self, label, 0, y - m.caption_h / 2, m.second_w - 12, m.caption_h,
                         pal.fg_alpha(0.4 if not pal.high_contrast else 0.8), font=caption,
                         align="right")
        for c in range(self.view.days):
            vline(cr, m.gutter + c * cw, 0, h, pal.line)

        target = self.view.target
        slot_h = m.hour_h * SNAP_MIN / 60
        if isinstance(target, datetime) and target.date() in dates:
            c = dates.index(target.date())
            y = self._y(target, datetime.combine(target.date(), time()), m)
            fill_rect(cr, m.gutter + c * cw + 1, y, cw - 1, slot_h, pal.accent_alpha(0.15))

        timed = self.view.timed
        self._ghost = None
        landing_at = None  # a copy being placed: outlined over its whole column, not in lanes
        if self._move and self._move.active:
            if self.drag_copy and self._move.mode == "move":
                if not self._move.crossed:
                    landing_at = self._move.preview()
            else:
                timed = [o for o in timed if o is not self._move.occ]
                if not self._move.crossed:  # dragged into the all-day row: the header previews it
                    self._ghost = self._move.preview()
                    timed.append(self._ghost)
        if self.external_ghost:
            if self.view.header.drag_copy:
                landing_at = self.external_ghost
            else:
                self._ghost = self.external_ghost
                timed = timed + [self._ghost]

        self._hits.clear()
        min_h = MIN_BLOCK.total_seconds() / 3600 * m.hour_h
        accessible = []
        for c, d in enumerate(dates):
            day_start = datetime.combine(d, time())
            day_end = day_start + timedelta(days=1)
            segs = [(o, max(o.start, day_start), min(o.end, day_end)) for o in timed
                    if o.start < day_end and (o.end > day_start or o.start >= day_start)]
            real = [o for o, _s, _e in segs if o is not self._ghost]
            accessible.append((spoken_day(d, len(real)), (m.gutter + c * cw, 0, cw, h),
                               [self.host.spoken_event(o) for o in real]))
            for occ, s, e, col, ncols in layout_columns(segs):
                sub_w = (cw - 6) / ncols
                if occ.event.travel_minutes and s == occ.start:
                    self._draw_travel(cr, pal, occ, m.gutter + c * cw + 3 + col * sub_w,
                                      sub_w - 2, day_start, caption, m)
                y0 = self._y(s, day_start, m)
                y1 = max(self._y(e, day_start, m), y0 + min_h)
                box = (m.gutter + c * cw + 3 + col * sub_w, y0 + 1, sub_w - 2, y1 - y0 - 2)
                self._draw_block(cr, *box, occ, caption, bold, m.caption_h)
                if occ is not self._ghost:
                    self._hits.add(*box, "occ", (occ, e == occ.end))

        if landing_at:
            for c, d in enumerate(dates):
                day_start = datetime.combine(d, time())
                day_end = day_start + timedelta(days=1)
                if overlaps(landing_at.start, landing_at.end, day_start, day_end):
                    s, e = max(landing_at.start, day_start), min(landing_at.end, day_end)
                    y0 = self._y(s, day_start, m)
                    y1 = max(self._y(e, day_start, m), y0 + min_h)
                    landing(cr, m.gutter + c * cw + 3, y0 + 1, cw - 6, y1 - y0 - 2, landing_at.color)

        self.set_accessible_items(accessible)
        if self._create:
            a, b = sorted(self._create)
            end = b + timedelta(minutes=SNAP_MIN)
            c = dates.index(a.date())
            day_start = datetime.combine(a.date(), time())
            y0, y1 = self._y(a, day_start, m), self._y(end, day_start, m)
            fill_rect(cr, m.gutter + c * cw + 3, y0 + 1, cw - 6, y1 - y0 - 2, pal.accent_bg, 6)
            text(cr, self, f"{fmt_time(a)} – {fmt_time(end)}", m.gutter + c * cw + 12, y0 + 6,
                 cw - 18, m.caption_h, pal.accent_fg, font=caption, valign="top")

        if now.date() in dates:
            c = dates.index(now.date())
            y = self._y(now, datetime.combine(now.date(), time()), m)
            cr.set_source_rgba(*pal.error)
            cr.rectangle(m.gutter + c * cw, y - 1, cw, 2)
            cr.fill()
            cr.arc(m.gutter + c * cw, y, 5, 0, 6.2832)
            cr.fill()

        if self.show_focus() and isinstance(target, datetime) and target.date() in dates:
            c = dates.index(target.date())
            y = self._y(target, datetime.combine(target.date(), time()), m)
            focus_ring(cr, m.gutter + c * cw, y, cw, m.hour_h * KEY_STEP.total_seconds() / 3600, pal)

    def _draw_travel(self, cr, pal, occ: Occurrence, x, w, day_start, font, m: Metrics) -> None:
        """A faint block for the travel time before an event, labeled "Travel"."""
        travel = timedelta(minutes=occ.event.travel_minutes)
        y0 = max(0.0, self._y(occ.start - travel, day_start, m))
        y1 = self._y(occ.start, day_start, m)
        if y1 - y0 < 2:
            return
        color = hex_rgb(occ.color)
        fill_rect(cr, x, y0 + 1, w, y1 - y0, (*color[:3], 0.18), 6)
        cr.set_source_rgba(*color[:3], 0.8)
        cr.set_line_width(1)
        cr.set_dash([3, 3])
        cr.move_to(x + 6, y1 - 0.5)
        cr.line_to(x + w - 6, y1 - 0.5)
        cr.stroke()
        cr.set_dash([])
        lh = m.caption_h
        if y1 - y0 >= lh + 2:
            minutes = occ.event.travel_minutes
            label = (ngettext("Travel · {n} min", "Travel · {n} min", minutes).format(n=minutes)
                     if minutes < 60 else _("Travel · {n} h").format(n=f"{minutes / 60:g}"))
            text(cr, self, label, x + 6, y0 + 1, w - 12, min(y1 - y0, lh + 4), pal.dim, font=font)

    def draw_drag_copy(self, cr, occ, x, y, w, h) -> None:
        m = self.view.metrics()
        self._draw_block(cr, x, y, w, h, occ, self.theme.font("caption"),
                         self.theme.font("caption-heading"), m.caption_h, selected=False)

    def _draw_block(self, cr, x, y, w, h, occ: Occurrence, font, bold, lh, selected=None):
        bg = hex_rgb(occ.color)
        if selected is None:
            selected = occ is self._ghost or occ.key in self.view.selected_keys
        fill_rect(cr, x, y, w, h, darker(bg) if selected else bg, 6)
        if selected:
            stroke_rect(cr, x + 1, y + 1, w - 2, h - 2, text_on(bg), 5, 1.5)
        fg = text_on(bg)
        when = f"{fmt_time(occ.start)} – {fmt_time(occ.end)}"
        if occ.is_instance:
            when += "  ↻"
        # Its place won't be open: a warning sign at the top right, and why, if there's room.
        badge = 0
        warning = self.host.place_warning(occ)
        if warning:
            badge = warning_badge(cr, self, x + w - 4, y + (0 if h < 2 * lh + 6 else 3),
                                  min(h, lh), fg)
        if h < 2 * lh + 6:
            text(cr, self, f"{occ.event.title}, {fmt_time(occ.start)}", x + 6, y, w - 12 - badge, h,
                 fg, font=font)
            return
        # Title, then as many detail lines as fit, each ellipsized on its own.
        lines = [(occ.event.title, bold), (when, font)]
        if occ.event.location:
            lines.append((occ.event.location, font))
        if warning:
            lines.append((warning, bold))
        ly = y + 3
        for n, (line, line_font) in enumerate(lines):
            if ly + lh > y + h - 2:
                break
            text(cr, self, line, x + 6, ly, w - 12 - (badge if n == 0 else 0), lh, fg, font=line_font,
                 valign="top")
            ly += lh

    # -- keyboard -----------------------------------------------------------

    def on_move(self, direction: str) -> None:
        self.host.move_target(_key_target(self.view, direction))

    # -- pointer ------------------------------------------------------------

    def _hit(self, x, y) -> tuple[Occurrence | None, bool]:
        """The event under the pointer, and whether it's on the resize handle."""
        _kind, payload, box = self._hits.find(x, y, reverse=True)  # topmost first
        if not payload:
            return None, False
        occ, resizable = payload
        return occ, resizable and y >= box[1] + box[3] - RESIZE_EDGE

    def on_press(self, x, y):
        if x < self.view.metrics().gutter:
            return
        occ, _edge = self._hit(x, y)
        self.host.select(occ, self._slot_at(x, y), self.press_mode)

    def on_double(self, x, y):
        self._create = self._move = None
        if x < self.view.metrics().gutter:
            return
        occ, _edge = self._hit(x, y)
        if occ:
            self.host.edit_occurrence(occ)
        else:
            slot = self._slot_at(x, y)
            self.host.create_event(slot, None, False)

    def _visible_y(self, y: float) -> float:
        return y - self.view.scroller.get_vadjustment().get_value()

    def _on_pinch(self, gesture, scale) -> None:
        ok, _x, y = gesture.get_bounding_box_center()
        factor = _clamp_zoom(self._pinch_zoom * scale) / self.host.hour_zoom
        self.view.zoom(factor, self._visible_y(y) if ok else None)

    def _on_scroll(self, controller, _dx, dy) -> bool:
        if not controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False  # an ordinary scroll
        focus = None if self._pointer_y is None else self._visible_y(self._pointer_y)
        self.view.zoom(1.1 ** -dy, focus)
        return True

    def on_hover(self, x, y):
        self._pointer_y = y
        if self._move or self._create:
            return
        occ, on_edge = self._hit(x, y)
        self.set_cursor_from_name("ns-resize" if on_edge and not occ.event.readonly else None)

    def on_drag_begin(self, x, y):
        if x < self.view.metrics().gutter:
            return
        occ, on_edge = self._hit(x, y)
        slot = self._slot_at(x, y)
        if occ:
            if not occ.event.readonly:
                _kind, _payload, box = self._hits.find(x, y, reverse=True)
                self._move = DragState(occ, slot, "resize" if on_edge else "move",
                                       grab=(x - box[0], y - box[1], box[2], box[3]))
        else:
            self._create = [slot, slot]

    def on_drag_update(self, x, y, dx, dy):
        if self._create:
            self._create[1] = self._slot_at(x, y, self._create[0].date())
            self.queue_draw()
            return
        m = self._move
        if not m or not m.update_active(dx, dy):
            return
        snap = timedelta(minutes=SNAP_MIN)
        header = self.view.header
        if m.mode == "resize":
            end = self._slot_at(x, y, m.anchor.date()) + snap
            m.start, m.end = m.occ.start, max(end, m.occ.start + snap)
        else:
            # Above the scrolled grid means over the all-day row: becomes all-day.
            m.crossed = y < self.view.scroller.get_vadjustment().get_value()
            if m.crossed:
                day = self.view.dates()[self.view.col_at(max(x, self.view.metrics().gutter),
                                                         self.get_width())]
                m.start = datetime.combine(day, time())
                m.end = m.start + timedelta(days=1)
                header.external_ghost = Occurrence(m.occ.event, m.start, m.end, color=m.occ.color)
            else:
                shift = self._slot_at(max(x, self.view.metrics().gutter), y) - m.anchor
                m.start, m.end = m.occ.start + shift, m.occ.end + shift
                header.external_ghost = None
            header.relayout()
            header.queue_draw()
            self.set_cursor_from_name(self.drag_cursor())
        self.queue_draw()

    def on_drag_end(self, x, y):
        create, self._create = self._create, None
        m, self._move = self._move, None
        header = self.view.header
        header.external_ghost = None
        header.relayout()
        header.queue_draw()
        self.set_cursor_from_name(None)
        self.queue_draw()
        if m and (m.changed or (m.active and self.drag_copy)):
            self.host.move_occurrence(m.occ, m.start, m.end, copy=self.drag_copy,
                                      all_day=True if m.crossed else None)
        elif create and create[0] != create[1]:
            a, b = sorted(create)
            self.host.create_event(a, b + timedelta(minutes=SNAP_MIN), False)
