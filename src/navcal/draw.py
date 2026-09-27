"""Cairo/Pango drawing helpers and locale-aware formatting for the views."""

from __future__ import annotations

import functools
import math
import subprocess
from datetime import date, datetime, timedelta

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("PangoCairo", "1.0")
gi.require_version("GdkPixbuf", "2.0")
import cairo  # noqa: E402
from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .i18n import _, ngettext, pgettext  # noqa: E402

# -- locale & clock ----------------------------------------------------------


@functools.cache
def _interface_settings() -> Gio.Settings | None:
    source = Gio.SettingsSchemaSource.get_default()
    if source and source.lookup("org.gnome.desktop.interface", True):
        return Gio.Settings(schema_id="org.gnome.desktop.interface")
    return None


@functools.cache
def _clock() -> dict:
    """The clock format setting, kept up to date (reading it each time is slow)."""
    settings, clock = _interface_settings(), {"12h": False}

    def update(*_args):
        clock["12h"] = bool(settings and settings.get_string("clock-format") == "12h")

    if settings:
        settings.connect("changed::clock-format", update)
    update()
    return clock


def uses_12h_clock() -> bool:
    return _clock()["12h"]


def fmt_time(dt: datetime) -> str:
    if uses_12h_clock():
        return f"{(dt.hour % 12) or 12}:{dt.minute:02d} {_('PM') if dt.hour >= 12 else _('AM')}"
    return f"{dt.hour:02d}:{dt.minute:02d}"


def fmt_date_time(day_text: str, dt: datetime) -> str:
    """A formatted day followed by dt's time: "Mon, Sep 21, 3:00 PM"."""
    return pgettext("day and time", "{day}, {time}").format(day=day_text, time=fmt_time(dt))


def fmt_time_short(dt: datetime) -> str:
    """Compact time for tight spaces: "9:30" or "9:30a"."""
    if uses_12h_clock():
        minutes = f":{dt.minute:02d}" if dt.minute else ""
        suffix = pgettext("short PM", "p") if dt.hour >= 12 else pgettext("short AM", "a")
        return f"{(dt.hour % 12) or 12}{minutes}{suffix}"
    return f"{dt.hour}:{dt.minute:02d}"


def fmt_hour(hour: int) -> str:
    if uses_12h_clock():
        return f"{(hour % 12) or 12} {_('PM') if hour >= 12 else _('AM')}"
    return f"{hour:02d}:00"


_first_weekday_override: int | None = None


def set_first_weekday(weekday: int | None) -> None:
    """Use a fixed first day of the week (Python weekday), or None for the locale's."""
    global _first_weekday_override
    _first_weekday_override = weekday


def first_weekday() -> int:
    """First day of the week, as a Python weekday (Mon=0)."""
    if _first_weekday_override is not None:
        return _first_weekday_override
    return locale_first_weekday()


@functools.cache
def locale_first_weekday() -> int:
    """First day of the week for the user's locale."""
    try:
        out = subprocess.run(["locale", "week-1stday", "first_weekday"],
                             capture_output=True, text=True, timeout=2).stdout.split()
        base = datetime.strptime(out[0], "%Y%m%d").date()  # a Sunday in glibc locales
        return (base + timedelta(days=int(out[1]) - 1)).weekday()
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return 0


def week_start(d: date) -> date:
    return d - timedelta(days=(d.weekday() - first_weekday()) % 7)


def is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def week_number(d: date) -> int:
    """ISO week number of the week containing d."""
    return d.isocalendar()[1]


# -- theme -------------------------------------------------------------------

RGB = tuple[float, float, float, float]

# Style classes the canvases draw text in (see Theme.font).
TEXT_STYLES = ("caption", "caption-heading", "heading", "title-3", "body")


def _rgb(c: Gdk.RGBA, alpha: float | None = None) -> RGB:
    clamp = lambda v: min(1.0, max(0.0, v))  # noqa: E731 - oklab colors can fall outside sRGB
    return (clamp(c.red), clamp(c.green), clamp(c.blue), c.alpha if alpha is None else alpha)


class Theme:
    """Fonts and named colors for custom drawing, read from real widgets with
    libadwaita style classes, so the views follow the system style: light and
    dark, accent color, high contrast and large text."""

    def __init__(self, probes: Gtk.Box):
        self._labels: dict[str, Gtk.Label] = {}
        for name in TEXT_STYLES + ("accent", "error", "warning"):
            label = Gtk.Label(css_classes=[] if name == "body" else [name])
            probes.append(label)
            self._labels[name] = label
        self._suggested = Gtk.Button(css_classes=["suggested-action"])
        probes.append(self._suggested)

    def font(self, style: str = "body") -> Pango.FontDescription:
        return self._labels[style].get_pango_context().get_font_description()

    def color(self, style: str) -> RGB:
        return _rgb(self._labels[style].get_color())

    @property
    def accent_fg(self) -> RGB:
        return _rgb(self._suggested.get_color())


class Palette:
    """The colors one view draws with, for the current style."""

    def __init__(self, widget: Gtk.Widget, theme: Theme):
        manager = Adw.StyleManager.get_default()
        self.high_contrast = manager.get_high_contrast()
        self.fg: RGB = _rgb(widget.get_color(), 1.0)
        self.accent: RGB = theme.color("accent")  # accent for text and lines
        self.accent_bg: RGB = _rgb(Adw.AccentColor.to_rgba(manager.get_accent_color()), 1.0)
        self.accent_fg: RGB = theme.accent_fg
        self.error: RGB = theme.color("error")
        self.warning: RGB = theme.color("warning")

    def fg_alpha(self, alpha: float) -> RGB:
        return (*self.fg[:3], alpha)

    def accent_alpha(self, alpha: float) -> RGB:
        return (*self.accent_bg[:3], min(1.0, alpha * (2 if self.high_contrast else 1)))

    @property
    def dim(self) -> RGB:  # matches libadwaita's --dim-opacity
        return self.fg_alpha(0.9 if self.high_contrast else 0.55)

    @property
    def line(self) -> RGB:
        return self.fg_alpha(0.5 if self.high_contrast else 0.15)

    @property
    def faint_line(self) -> RGB:
        return self.fg_alpha(0.25 if self.high_contrast else 0.06)


# -- event colors (user data: calendar and event colors) ---------------------


def hex_rgb(hex_color: str) -> RGB:
    c = Gdk.RGBA()
    c.parse(hex_color)
    return (c.red, c.green, c.blue, 1.0)


def text_on(rgb: RGB) -> RGB:
    """Dark or light text, whichever reads better on a user-chosen color."""
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    return (0, 0, 0.02, 0.8) if lum > 0.65 else (1, 1, 1, 1)


def darker(rgb: RGB, factor: float = 0.8) -> RGB:
    return (rgb[0] * factor, rgb[1] * factor, rgb[2] * factor, rgb[3])


# -- primitives --------------------------------------------------------------


def rounded_rect(cr, x, y, w, h, r):
    r = max(0, min(r, w / 2, h / 2))
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    cr.close_path()


def fill_rect(cr, x, y, w, h, rgba: RGB, radius: float = 0):
    cr.set_source_rgba(*rgba)
    if radius:
        rounded_rect(cr, x, y, w, h, radius)
    else:
        cr.rectangle(x, y, w, h)
    cr.fill()


def stroke_rect(cr, x, y, w, h, rgba: RGB, radius: float = 0, width: float = 2):
    cr.set_source_rgba(*rgba)
    cr.set_line_width(width)
    rounded_rect(cr, x + width / 2, y + width / 2, w - width, h - width, radius)
    cr.stroke()


def hline(cr, x0, x1, y, rgba: RGB):
    cr.set_source_rgba(*rgba)
    cr.set_line_width(1)
    cr.move_to(x0, math.floor(y) + 0.5)
    cr.line_to(x1, math.floor(y) + 0.5)
    cr.stroke()


def vline(cr, x, y0, y1, rgba: RGB):
    cr.set_source_rgba(*rgba)
    cr.set_line_width(1)
    cr.move_to(math.floor(x) + 0.5, y0)
    cr.line_to(math.floor(x) + 0.5, y1)
    cr.stroke()


LAYOUTS_KEPT = 3000  # text layouts kept per widget
ALIGNMENTS = {"left": Pango.Alignment.LEFT, "center": Pango.Alignment.CENTER,
              "right": Pango.Alignment.RIGHT}


def _layout(widget: Gtk.Widget, s: str, font: Pango.FontDescription | None,
            width: int = -1, align: str = "left") -> Pango.Layout:
    """A laid out text, reused from earlier frames when possible: laying out text
    is much slower than drawing it. Font or display changes make a new one."""
    context = widget.get_pango_context()
    cache = getattr(widget, "_navcal_layouts", None)
    if cache is None or cache[0] is not context or len(cache[1]) > LAYOUTS_KEPT:
        cache = widget._navcal_layouts = (context, {})
    key = (context.get_serial(), s, font.to_string() if font else None, width, align)
    layout = cache[1].get(key)
    if layout is None:
        layout = widget.create_pango_layout(s)
        if font is not None:
            layout.set_font_description(font)
        if width >= 0:
            layout.set_width(width)
            layout.set_ellipsize(Pango.EllipsizeMode.END)
        layout.set_alignment(ALIGNMENTS[align])
        cache[1][key] = layout
    return layout


def text(cr, widget: Gtk.Widget, s: str, x, y, w, h, rgba: RGB, *,
         font: Pango.FontDescription | None = None, align="left", valign="center"):
    """Draw s inside the box (x, y, w, h), ellipsized to fit."""
    if w <= 0 or h <= 0:
        return
    layout = _layout(widget, s, font, int(w * Pango.SCALE), align)
    _tw, th = layout.get_pixel_size()
    ty = {"top": y, "center": y + (h - th) / 2, "bottom": y + h - th}[valign]
    cr.save()
    cr.rectangle(x, y, w, h)
    cr.clip()
    cr.set_source_rgba(*rgba)
    cr.move_to(x, ty)
    PangoCairo.show_layout(cr, layout)
    cr.restore()


def line_height(widget: Gtk.Widget, font: Pango.FontDescription | None = None) -> int:
    return _layout(widget, "Ag", font).get_pixel_size()[1]


def text_width(widget: Gtk.Widget, s: str, font: Pango.FontDescription | None = None) -> int:
    return _layout(widget, s, font).get_pixel_size()[0]


def event_bar(cr, widget, x, y, w, h, color: str, label: str, selected: bool,
              font: Pango.FontDescription, radius=6, warning: bool = False):
    """A filled, rounded event bar with its label. Selection is shown with an
    outline as well as a darker fill, so it doesn't rely on color alone.
    warning: a badge at the end (its place is closed then, say)."""
    bg = hex_rgb(color)
    fill_rect(cr, x, y, w, h, darker(bg) if selected else bg, radius)
    if selected:
        stroke_rect(cr, x + 1, y + 1, w - 2, h - 2, text_on(bg), radius - 1, 1.5)
    badge = warning_badge(cr, widget, x + w - 4, y, h, text_on(bg)) if warning else 0
    text(cr, widget, label, x + 6, y, w - 12 - badge, h, text_on(bg), font=font)


def warning_badge(cr, widget, right, y, h, rgba: RGB) -> float:
    """A warning sign ending at right, centered in a line of height h. Returns the room it took."""
    size = int(min(16, max(12, h - 2)))
    symbolic_icon(cr, widget, "dialog-warning-symbolic", right - size, y + (h - size) / 2, size, rgba)
    return size + 4


_icon_shapes: dict[tuple[str, int], tuple | None] = {}


def symbolic_icon(cr, widget: Gtk.Widget, name: str, x, y, size: int, rgba: RGB) -> None:
    """A symbolic icon from the theme, in rgba, the way GTK recolors them."""
    scale = widget.get_scale_factor()
    key = (name, size * scale)
    if key not in _icon_shapes:
        _icon_shapes[key] = _icon_shape(widget, name, size, scale)
    shape = _icon_shapes[key]
    if shape is None:
        return
    cr.save()
    cr.translate(x, y)
    cr.scale(1 / scale, 1 / scale)
    cr.set_source_rgba(*rgba)
    cr.mask_surface(shape[0], 0, 0)
    cr.restore()


def _icon_shape(widget: Gtk.Widget, name: str, size: int, scale: int):
    """The icon's opacity as an A8 surface (and its pixel data, kept alive with it)."""
    theme = Gtk.IconTheme.get_for_display(widget.get_display())
    file = theme.lookup_icon(name, None, size, scale, Gtk.TextDirection.NONE, 0).get_file()
    if file is None:
        return None
    px = size * scale
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(file.read(None), px, px, True, None)
    except GLib.Error:
        return None
    if not pixbuf.get_has_alpha():
        return None
    w, h, rowstride = pixbuf.get_width(), pixbuf.get_height(), pixbuf.get_rowstride()
    pixels = pixbuf.get_pixels()
    stride = cairo.ImageSurface.format_stride_for_width(cairo.FORMAT_A8, w)
    data = bytearray(stride * h)
    for row in range(h):
        data[row * stride:row * stride + w] = pixels[row * rowstride + 3:row * rowstride + 4 * w:4]
    return cairo.ImageSurface.create_for_data(data, cairo.FORMAT_A8, w, h, stride), data


def landing(cr, x, y, w, h, color: str, radius=6):
    """Where a duplicated event will land: a faint fill and a dashed outline."""
    rgb = hex_rgb(color)
    fill_rect(cr, x, y, w, h, (*rgb[:3], 0.18), radius)
    cr.set_dash([4, 3])
    stroke_rect(cr, x, y, w, h, (*rgb[:3], 0.9), radius, 1.5)
    cr.set_dash([])


def focus_ring(cr, x, y, w, h, pal: Palette):
    stroke_rect(cr, x + 1, y + 1, w - 2, h - 2, pal.accent_bg, 6, 2)


def spoken_event(occ) -> str:
    """How a screen reader reads an event: "Lunch, 12:30 PM to 1:30 PM, Café Luna"."""
    when = (_("all day") if occ.is_banner else
            _("{start} to {end}").format(start=fmt_time(occ.start), end=fmt_time(occ.end)))
    parts = [occ.event.title, when]
    if occ.event.location:
        parts.append(occ.event.location)
    if occ.is_instance:
        parts.append(_("repeats"))
    return ", ".join(parts)


def spoken_day(d: date, count: int) -> str:
    events = _("no events") if count == 0 else ngettext("{n} event", "{n} events", count).format(n=count)
    return f"{d.strftime(_('%A, %B %-d'))}, {events}"
