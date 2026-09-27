"""Printouts: a calendar (month grid, week planner or day timeline), an agenda
checklist with a box to tick per event, or both on one page.

Everything is laid out in points (1/72 inch) on a page of any size, so the same
drawing serves the preview, the printer and "Save as Image".
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time, timedelta

import gi

gi.require_version("PangoCairo", "1.0")
from gi.repository import GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .draw import fmt_time, hex_rgb, week_start  # noqa: E402
from .i18n import _, ngettext  # noqa: E402
from .layout import layout_columns  # noqa: E402
from .models import Occurrence, days_covered, sort_occurrences  # noqa: E402

MARGIN = 36  # page margin
GAP = 18  # between the calendar and the agenda
BOX = 10  # checkbox size
LINE = (0.55, 0.55, 0.55)  # grid lines
DIM = (0.4, 0.4, 0.4)  # secondary text
INK = (0, 0, 0)
IMAGE_DPI = 150  # "Save as Image" resolution
# Paper, not screen: fixed fonts that print the same everywhere.
FONTS = {"title": "Sans Bold 17", "heading": "Sans Bold 11", "text": "Sans 10",
         "small": "Sans 8", "small-bold": "Sans Bold 8", "tiny": "Sans 7"}


@dataclass
class PrintOptions:
    show: str = "both"  # "calendar", "agenda" or "both"
    calendar_at: str = "left"  # the calendar's place next to the agenda: left, right, top, bottom
    period: str = "week"  # "day", "week" or "month"
    landscape: bool = True
    checkboxes: bool = True
    colors: bool = True

    @classmethod
    def from_dict(cls, values: dict | None) -> PrintOptions:
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (values or {}).items() if k in names})

    def to_dict(self) -> dict:
        return asdict(self)


def period_range(period: str, around: date) -> tuple[date, date]:
    """First and last day of the day, week or month around a date."""
    if period == "day":
        return around, around
    if period == "week":
        first = week_start(around)
        return first, first + timedelta(days=6)
    first = around.replace(day=1)
    return first, (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)


def period_title(period: str, first: date, last: date) -> str:
    if period == "day":
        return first.strftime(_("%A, %B %-d, %Y"))
    if period == "week":
        return _("{first} – {last}").format(first=first.strftime(_("%b %-d")),
                                             last=last.strftime(_("%b %-d, %Y")))
    return first.strftime(_("%B %Y"))


class Printout:
    """One printout: its pages, and drawing them."""

    def __init__(self, period: str, first: date, last: date, occurrences: list[Occurrence],
                 options: PrintOptions):
        self.options = options
        self.period, self.first, self.last = period, first, last
        self.title = period_title(period, first, last)
        self.days: dict[date, list[Occurrence]] = {}
        for occ in sort_occurrences(list(occurrences)):
            for d in days_covered(occ.start, occ.end):
                self.days.setdefault(d, []).append(occ)
        # The agenda: each day's heading, then its events.
        self.lines: list[tuple[str, object]] = []
        for d in sorted(d for d in self.days if first <= d <= last):
            self.lines.append(("day", d))
            self.lines.extend(("event", (occ, d)) for occ in self.days[d])
        if not self.lines:
            self.lines.append(("empty", None))
        self.pages: list[list[tuple]] = []
        self.size = (0.0, 0.0)

    # -- layout -----------------------------------------------------------------

    def paginate(self, cr, width: float, height: float, grow: bool = False) -> float:
        """Split into pages of width × height. With grow (for an image), make one
        page as tall as its content instead. Returns the page height."""
        o = self.options
        title_h = _text_height(cr, "title") + 12
        top, left = MARGIN + title_h, MARGIN
        content_w, content_h = width - 2 * MARGIN, height - 2 * MARGIN - title_h
        heights = [self._line_height(cr, kind) for kind, _item in self.lines]

        cal = agenda = None  # (x, y, w, h) on the first page
        if o.show == "calendar":
            cal = (left, top, content_w, content_h)
        elif o.show == "agenda":
            agenda = (left, top, content_w, content_h)
        elif o.calendar_at in ("left", "right"):
            cal_w = content_w * 0.62 - GAP / 2
            agenda_w = content_w - cal_w - GAP
            first_x, second_x = left, left + (agenda_w if o.calendar_at == "right" else cal_w) + GAP
            cal_x, agenda_x = (first_x, second_x) if o.calendar_at == "left" else (second_x, first_x)
            cal, agenda = (cal_x, top, cal_w, content_h), (agenda_x, top, agenda_w, content_h)
        else:
            cal_h = content_h * 0.58 - GAP / 2
            agenda_h = content_h - cal_h - GAP
            first_y, second_y = top, top + (agenda_h if o.calendar_at == "bottom" else cal_h) + GAP
            cal_y, agenda_y = (first_y, second_y) if o.calendar_at == "top" else (second_y, first_y)
            cal, agenda = (left, cal_y, content_w, cal_h), (left, agenda_y, content_w, agenda_h)

        if grow and agenda and sum(heights) > agenda[3]:
            extra = sum(heights) - agenda[3]
            height += extra
            agenda = (*agenda[:3], agenda[3] + extra)
            if cal and o.calendar_at == "bottom":
                cal = (cal[0], cal[1] + extra, *cal[2:])
        self.size = (width, height)

        # The first page, then as many more as the rest of the agenda needs.
        self.pages = [[("title", (left, MARGIN, content_w, title_h))]]
        if cal:
            self.pages[0].append(("calendar", cal))
        if not agenda:
            return height
        rest, box = list(range(len(self.lines))), agenda
        while rest:
            taken, used = [], 0.0
            for i in rest:
                # A day's heading stays with its first event.
                with_next = heights[i + 1] if self.lines[i][0] == "day" and i + 1 < len(heights) else 0
                if taken and used + heights[i] + with_next > box[3]:
                    break
                taken.append(i)
                used += heights[i]
            self.pages[-1].append(("agenda", box, taken))
            rest = rest[len(taken):]
            if rest:  # later pages are all agenda
                box = (left, MARGIN, content_w, height - 2 * MARGIN)
                self.pages.append([])
        return height

    def _line_height(self, cr, kind: str) -> float:
        if kind == "day":
            return _text_height(cr, "heading") + 12
        return _text_height(cr, "text") + 7

    # -- drawing ------------------------------------------------------------------

    def draw_page(self, cr, number: int) -> None:
        for kind, box, *rest in self.pages[number]:
            if kind == "title":
                _show(cr, box[0], box[1], box[2], "title", self.title)
            elif kind == "calendar":
                {"day": self._draw_day, "week": self._draw_week,
                 "month": self._draw_month}[self.period](cr, *box)
            else:
                self._draw_agenda(cr, box, rest[0])
        if len(self.pages) > 1:
            width, height = self.size
            label = _("Page {n} of {total}").format(n=number + 1, total=len(self.pages))
            _show(cr, MARGIN, height - MARGIN * 0.75, width - 2 * MARGIN, "tiny", label,
                  color=DIM, align="right")

    def _draw_agenda(self, cr, box, indexes: list[int]) -> None:
        x, y, w, _h = box
        o = self.options
        for n, i in enumerate(indexes):
            kind, item = self.lines[i]
            if kind == "day":
                _show(cr, x, y + (4 if n else 0), w, "heading", item.strftime(_("%A, %B %-d")))
            elif kind == "empty":
                _show(cr, x, y, w, "text", _("No events"), color=DIM)
            else:
                occ, d = item
                line_h = _text_height(cr, "text")
                tx = x + 4
                if o.checkboxes:
                    cr.set_source_rgb(*INK)
                    cr.set_line_width(0.8)
                    cr.rectangle(tx, y + (line_h - BOX) / 2, BOX, BOX)
                    cr.stroke()
                    tx += BOX + 8
                if o.colors:
                    cr.set_source_rgb(*hex_rgb(occ.color)[:3])
                    cr.arc(tx + 3, y + line_h / 2, 3, 0, 6.2832)
                    cr.fill()
                    tx += 10
                where = f" · {occ.event.location}" if occ.event.location else ""
                _show(cr, tx, y, x + w - tx, "text", occ.event.title, after=f"  {_when(occ)}{where}")
            y += self._line_height(cr, kind)

    def _draw_month(self, cr, x, y, w, h) -> None:
        first = week_start(self.first)
        weeks = (self.last - first).days // 7 + 1
        head_h = _text_height(cr, "small-bold") + 6
        cw, ch = w / 7, (h - head_h) / weeks
        for c in range(7):
            name = (first + timedelta(days=c)).strftime("%a")
            _show(cr, x + c * cw, y, cw, "small-bold", name, color=DIM, align="center")
        num_h = _text_height(cr, "small-bold") + 4
        line_h = _text_height(cr, "tiny") + 2
        for r in range(weeks):
            for c in range(7):
                d = first + timedelta(days=7 * r + c)
                cx, cy = x + c * cw, y + head_h + r * ch
                in_month = self.first <= d <= self.last
                _show(cr, cx + 4, cy + 3, cw - 8, "small-bold", str(d.day), color=INK if in_month else DIM)
                if not in_month:
                    continue
                occs = self.days.get(d, [])
                fits = max(0, int((ch - num_h - 4) // line_h))
                shown = occs if len(occs) <= fits else occs[:max(fits - 1, 0)]
                ly = cy + num_h + 2
                for occ in shown:
                    self._dot(cr, cx + 4, ly, line_h, occ)
                    _show(cr, cx + 11, ly, cw - 15, "tiny", _brief(occ, d))
                    ly += line_h
                if len(shown) < len(occs):
                    more = len(occs) - len(shown)
                    _show(cr, cx + 4, ly, cw - 8, "tiny",
                          ngettext("{n} more", "{n} more", more).format(n=more), color=DIM)
        _grid(cr, x, y + head_h, w, h - head_h, 7, weeks)

    def _draw_week(self, cr, x, y, w, h) -> None:
        dates = [self.first + timedelta(days=i) for i in range(7)]
        name_h, title_h = _text_height(cr, "small-bold"), _text_height(cr, "small-bold")
        when_h = _text_height(cr, "tiny")
        head_h = name_h + _text_height(cr, "heading") + 10
        cw = w / 7
        for c, d in enumerate(dates):
            cx = x + c * cw
            _show(cr, cx, y + 2, cw, "small-bold", d.strftime("%a"), color=DIM, align="center")
            _show(cr, cx, y + 4 + name_h, cw, "heading", str(d.day), align="center")
            ly, bottom = y + head_h + 6, y + h - 4
            occs = self.days.get(d, [])
            item_h = title_h + when_h + 8
            for n, occ in enumerate(occs):
                room_for_more = when_h + 4 if n < len(occs) - 1 else 0
                if ly + item_h + room_for_more > bottom:
                    more = len(occs) - n
                    _show(cr, cx + 4, ly, cw - 8, "tiny",
                          ngettext("{n} more", "{n} more", more).format(n=more), color=DIM)
                    break
                self._block(cr, cx + 3, ly, cw - 6, item_h - 3, occ)
                _show(cr, cx + 9, ly + 2, cw - 14, "small-bold", occ.event.title)
                _show(cr, cx + 9, ly + 3 + title_h, cw - 14, "tiny", _when(occ), color=DIM)
                ly += item_h
        _grid(cr, x, y, w, h, 7, 1)
        _hline(cr, x, x + w, y + head_h)

    def _draw_day(self, cr, x, y, w, h) -> None:
        d = self.first
        occs = self.days.get(d, [])
        timed = [o for o in occs if not o.is_banner]
        line_h = _text_height(cr, "small") + 6
        for occ in (o for o in occs if o.is_banner):  # all-day events first, as a list
            self._block(cr, x, y, w, line_h - 2, occ)
            _show(cr, x + 8, y + 2, w - 12, "small-bold", occ.event.title, after=f"  {_('All day')}")
            y, h = y + line_h, h - line_h
        day_start = datetime.combine(d, time())
        # From 7 to 19, or longer if events are earlier or later.
        first_hour = min([7] + [o.start.hour for o in timed if o.start.date() == d])
        last_hour = max([19] + [min(24, int((o.end - day_start).total_seconds() // 3600) + 1)
                                for o in timed])
        hours, gutter = last_hour - first_hour, 44
        hour_h = h / hours
        for i in range(hours + 1):
            _hline(cr, x + gutter, x + w, y + i * hour_h)
            if i < hours:
                label = fmt_time(datetime.combine(d, time(first_hour + i)))
                _show(cr, x, y + i * hour_h + 2, gutter - 6, "tiny", label, color=DIM, align="right")
        top = day_start + timedelta(hours=first_hour)
        bottom = day_start + timedelta(hours=last_hour)
        segs = [(o, max(o.start, top), min(o.end, bottom)) for o in timed]
        for occ, s, e, col, ncols in layout_columns([seg for seg in segs if seg[1] < seg[2]]):
            y0 = y + (s - top).total_seconds() / 3600 * hour_h
            y1 = max(y + (e - top).total_seconds() / 3600 * hour_h, y0 + line_h)
            col_w = (w - gutter - 4) / ncols
            bx = x + gutter + 2 + col * col_w
            self._block(cr, bx, y0 + 1, col_w - 3, y1 - y0 - 2, occ)
            _show(cr, bx + 7, y0 + 3, col_w - 12, "small-bold", occ.event.title)
            if y1 - y0 > 2 * line_h:
                where = f" · {occ.event.location}" if occ.event.location else ""
                _show(cr, bx + 7, y0 + line_h, col_w - 12, "tiny", _when(occ) + where, color=DIM)

    def _block(self, cr, x, y, w, h, occ: Occurrence) -> None:
        """An event's box: a light tint of its color with a bar at the side."""
        rgb = hex_rgb(occ.color)[:3] if self.options.colors else (0.5, 0.5, 0.5)
        tint = 0.18 if self.options.colors else 0.08  # mixed with the white paper, so lines don't show through
        cr.set_source_rgb(*(1 - tint + tint * c for c in rgb))
        cr.rectangle(x, y, w, h)
        cr.fill()
        cr.set_source_rgb(*rgb)
        cr.rectangle(x, y, 3, h)
        cr.fill()

    def _dot(self, cr, x, y, h, occ: Occurrence) -> None:
        if self.options.colors:
            cr.set_source_rgb(*hex_rgb(occ.color)[:3])
            cr.arc(x + 2.5, y + h / 2, 2.5, 0, 6.2832)
            cr.fill()


# -- text and lines -------------------------------------------------------------------


def _layout(cr, font: str) -> Pango.Layout:
    layout = PangoCairo.create_layout(cr)
    PangoCairo.context_set_resolution(layout.get_context(), 72)  # font sizes in points
    layout.set_font_description(Pango.FontDescription.from_string(FONTS[font]))
    return layout


def _text_height(cr, font: str) -> float:
    layout = _layout(cr, font)
    layout.set_text("Ag", -1)
    return layout.get_extents()[1].height / Pango.SCALE


def _show(cr, x, y, w, font: str, text: str, *, color=INK, align="left", after: str = "") -> None:
    """One line of text, cut short to fit w. after: dimmer text following it."""
    layout = _layout(cr, font)
    markup = GLib.markup_escape_text(text)
    if after:
        markup += f"<span alpha='60%'>{GLib.markup_escape_text(after)}</span>"
    layout.set_markup(markup, -1)
    layout.set_width(int(max(w, 1) * Pango.SCALE))
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_alignment({"left": Pango.Alignment.LEFT, "center": Pango.Alignment.CENTER,
                          "right": Pango.Alignment.RIGHT}[align])
    cr.set_source_rgb(*color)
    cr.move_to(x, y)
    PangoCairo.show_layout(cr, layout)


def _hline(cr, x0, x1, y) -> None:
    cr.set_source_rgb(*LINE)
    cr.set_line_width(0.5)
    cr.move_to(x0, y)
    cr.line_to(x1, y)
    cr.stroke()


def _grid(cr, x, y, w, h, cols: int, rows: int) -> None:
    cr.set_source_rgb(*LINE)
    cr.set_line_width(0.5)
    cr.rectangle(x, y, w, h)
    for c in range(1, cols):
        cr.move_to(x + c * w / cols, y)
        cr.line_to(x + c * w / cols, y + h)
    for r in range(1, rows):
        cr.move_to(x, y + r * h / rows)
        cr.line_to(x + w, y + r * h / rows)
    cr.stroke()


def _when(occ: Occurrence) -> str:
    if occ.is_banner:
        return _("All day")
    return f"{fmt_time(occ.start)} – {fmt_time(occ.end)}"


def _brief(occ: Occurrence, d: date) -> str:
    """"9:00 AM Standup", for a month cell."""
    if occ.is_banner or occ.start.date() != d:
        return occ.event.title
    return _("{time} {title}").format(time=fmt_time(occ.start), title=occ.event.title)


# -- output -------------------------------------------------------------------------


def paper_size(landscape: bool) -> tuple[float, float]:
    """The locale's usual paper (A4 or Letter), in points."""
    paper = Gtk.PaperSize.new(None)
    w, h = paper.get_width(Gtk.Unit.POINTS), paper.get_height(Gtk.Unit.POINTS)
    return (h, w) if landscape else (w, h)


def render(printout: Printout, cr, number: int = 0) -> None:
    """Draw a page (already paginated) on white paper."""
    width, height = printout.size
    cr.save()
    cr.set_source_rgb(1, 1, 1)
    cr.rectangle(0, 0, width, height)
    cr.fill()
    printout.draw_page(cr, number)
    cr.restore()


def save_image(printout: Printout, path: str) -> None:
    """Save as one PNG image, as tall as its content needs."""
    import cairo
    width, height = paper_size(printout.options.landscape)
    height = printout.paginate(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)),
                               width, height, grow=True)
    scale = IMAGE_DPI / 72
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(width * scale), int(height * scale))
    cr = cairo.Context(surface)
    cr.scale(scale, scale)
    render(printout, cr)
    surface.write_to_png(path)


def print_out(printout: Printout, parent: Gtk.Window, export_to: str | None = None
              ) -> Gtk.PrintOperationResult:
    """Print through the print dialog (or straight to a PDF file, for tests)."""
    op = Gtk.PrintOperation(job_name=printout.title, embed_page_setup=True, unit=Gtk.Unit.POINTS)
    setup = Gtk.PageSetup()
    setup.set_orientation(Gtk.PageOrientation.LANDSCAPE if printout.options.landscape
                          else Gtk.PageOrientation.PORTRAIT)
    op.set_default_page_setup(setup)

    def begin(op, context):
        printout.paginate(context.get_cairo_context(), context.get_width(), context.get_height())
        op.set_n_pages(len(printout.pages))

    op.connect("begin-print", begin)
    op.connect("draw-page", lambda _op, context, number: printout.draw_page(context.get_cairo_context(),
                                                                            number))
    if export_to:
        op.set_export_filename(export_to)
        return op.run(Gtk.PrintOperationAction.EXPORT, parent)
    return op.run(Gtk.PrintOperationAction.PRINT_DIALOG, parent)
