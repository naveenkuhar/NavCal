# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Generated CSS classes for user-chosen calendar and event colors.

Everything else is styled with libadwaita style classes, plus the few size
rules in style.css (loaded automatically by Adw.Application). Arbitrary user
colors have no style class, so each one gets a generated class here.
"""

from __future__ import annotations

from gi.repository import Gdk, Gtk

from .models import EVENT_COLORS

_provider = Gtk.CssProvider()
_colors: set[str] = set()


def _foreground(hex_color: str) -> str:
    """Dark or light, whichever is readable on the given color."""
    c = Gdk.RGBA()
    c.parse(f"#{hex_color}")
    lum = 0.299 * c.red + 0.587 * c.green + 0.114 * c.blue
    return "rgb(0 0 6 / 80%)" if lum > 0.65 else "white"


def color_css_class(hex_color: str) -> str:
    """CSS class that paints a swatch or dot in hex_color, registering it if new."""
    key = hex_color.lstrip("#").lower()
    if key not in _colors:
        _colors.add(key)
        _reload()
    return f"event-color-{key}"


def _reload() -> None:
    _provider.load_from_string("\n".join(
        f".event-color-{c} {{ background: #{c}; color: {_foreground(c)}; }}"
        for c in sorted(_colors)))


def install() -> None:
    for _name, hex_color in EVENT_COLORS:
        _colors.add(hex_color.lstrip("#").lower())
    _reload()
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), _provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
