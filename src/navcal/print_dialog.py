# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""The print dialog: pick a layout, see the page as it will print, then print it
or save it as an image."""

from __future__ import annotations

from datetime import date
from typing import Callable

import cairo
from gi.repository import Adw, Gio, GLib, Gtk

from . import printing
from .i18n import N_, _
from .printing import PrintOptions, Printout
from .resources import RESOURCE_BASE

SHOW = [("both", N_("Calendar and agenda")), ("calendar", N_("Calendar")), ("agenda", N_("Agenda"))]
POSITIONS = [("left", N_("Left of the agenda")), ("right", N_("Right of the agenda")),
             ("top", N_("Above the agenda")), ("bottom", N_("Below the agenda"))]
PERIODS = [("day", N_("Day")), ("week", N_("Week")), ("month", N_("Month"))]
ORIENTATIONS = [(False, N_("Portrait")), (True, N_("Landscape"))]


@Gtk.Template(resource_path=f"{RESOURCE_BASE}/ui/print-dialog.ui")
class PrintDialog(Adw.Dialog):
    __gtype_name__ = "NavcalPrintDialog"

    cancel_button: Gtk.Button = Gtk.Template.Child()
    print_button: Gtk.Button = Gtk.Template.Child()
    preview: Gtk.DrawingArea = Gtk.Template.Child()
    pages_box: Gtk.Box = Gtk.Template.Child()
    previous_page: Gtk.Button = Gtk.Template.Child()
    pages_label: Gtk.Label = Gtk.Template.Child()
    next_page: Gtk.Button = Gtk.Template.Child()
    show_row: Adw.ComboRow = Gtk.Template.Child()
    position_row: Adw.ComboRow = Gtk.Template.Child()
    period_row: Adw.ComboRow = Gtk.Template.Child()
    orientation_row: Adw.ComboRow = Gtk.Template.Child()
    checkboxes_row: Adw.SwitchRow = Gtk.Template.Child()
    colors_row: Adw.SwitchRow = Gtk.Template.Child()
    image_row: Adw.ButtonRow = Gtk.Template.Child()

    def __init__(self, around: date, options: PrintOptions,
                 occurrences: Callable[[date, date], list], on_change: Callable[[PrintOptions], None],
                 toast: Callable[[str], None]):
        """around: a day in the period to print. occurrences(first, last): the
        events to print. on_change: remembers the options."""
        super().__init__()
        self._around, self._occurrences = around, occurrences
        self._on_change, self._toast = on_change, toast
        self._page = 0
        self._printout: Printout | None = None
        for row, choices, value in ((self.show_row, SHOW, options.show),
                                    (self.position_row, POSITIONS, options.calendar_at),
                                    (self.period_row, PERIODS, options.period),
                                    (self.orientation_row, ORIENTATIONS, options.landscape)):
            row.set_model(Gtk.StringList.new([_(label) for _value, label in choices]))
            row.set_selected(next((i for i, (v, _label) in enumerate(choices) if v == value), 0))
            row.connect("notify::selected", lambda *_args: self._update())
        self.checkboxes_row.set_active(options.checkboxes)
        self.colors_row.set_active(options.colors)
        for row in (self.checkboxes_row, self.colors_row):
            row.connect("notify::active", lambda *_args: self._update())

        self.preview.set_draw_func(self._draw_preview)
        self.previous_page.connect("clicked", lambda _b: self._turn(-1))
        self.next_page.connect("clicked", lambda _b: self._turn(1))
        self.cancel_button.connect("clicked", lambda _b: self.close())
        self.print_button.connect("clicked", lambda _b: self._print())
        self.image_row.connect("activated", lambda _r: self._ask_image_file())
        self._update()

    def options(self) -> PrintOptions:
        return PrintOptions(show=SHOW[self.show_row.get_selected()][0],
                            calendar_at=POSITIONS[self.position_row.get_selected()][0],
                            period=PERIODS[self.period_row.get_selected()][0],
                            landscape=ORIENTATIONS[self.orientation_row.get_selected()][0],
                            checkboxes=self.checkboxes_row.get_active(),
                            colors=self.colors_row.get_active())

    def printout(self) -> Printout:
        o = self.options()
        first, last = printing.period_range(o.period, self._around)
        return Printout(o.period, first, last, self._occurrences(first, last), o)

    def _update(self) -> None:
        o = self.options()
        self.position_row.set_visible(o.show == "both")
        self.checkboxes_row.set_sensitive(o.show != "calendar")
        self._printout = self.printout()
        width, height = printing.paper_size(o.landscape)
        self._printout.paginate(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32, 1, 1)),
                                width, height)
        self._page = min(self._page, len(self._printout.pages) - 1)
        self._update_pages()
        self._on_change(o)

    def _turn(self, step: int) -> None:
        self._page = max(0, min(self._page + step, len(self._printout.pages) - 1))
        self._update_pages()

    def _update_pages(self) -> None:
        count = len(self._printout.pages)
        self.pages_box.set_visible(count > 1)
        self.pages_label.set_label(_("Page {n} of {total}").format(n=self._page + 1, total=count))
        self.previous_page.set_sensitive(self._page > 0)
        self.next_page.set_sensitive(self._page < count - 1)
        self.preview.queue_draw()

    def _draw_preview(self, area, cr, width, height) -> None:
        page_w, page_h = self._printout.size
        scale = min((width - 12) / page_w, (height - 12) / page_h)
        x, y = (width - page_w * scale) / 2, (height - page_h * scale) / 2
        # A thin outline in the text color sets the (always white) paper apart.
        fg = area.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.2)
        cr.rectangle(x - 1, y - 1, page_w * scale + 2, page_h * scale + 2)
        cr.fill()
        cr.translate(x, y)
        cr.scale(scale, scale)
        printing.render(self._printout, cr, self._page)

    def _print(self) -> None:
        window = self.get_root()
        self.close()
        printing.print_out(self.printout(), window)

    def _ask_image_file(self) -> None:
        printout = self.printout()
        png = Gtk.FileFilter(name=_("PNG images"), mime_types=["image/png"])
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(png)
        dialog = Gtk.FileDialog(title=_("Save as Image"), initial_name=f"{printout.title}.png",
                                filters=filters, default_filter=png)

        def chosen(dialog, result):
            try:
                file = dialog.save_finish(result)
            except GLib.Error:
                return  # cancelled
            path = file.get_path()
            try:
                printing.save_image(printout, path)
            except OSError:
                self._toast(_("Couldn’t save the image"))
                return
            self._toast(_("Saved “{name}”").format(name=file.get_basename()))

        dialog.save(self.get_root(), None, chosen)
