"""Base drawing area with the pointer and keyboard handling every calendar view needs."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gi.repository import Gdk, GLib, Graphene, Gtk

from ..draw import Palette, event_bar, stroke_rect

MAX_COPY_HEIGHT = 120  # the faded copy under the pointer is at most this tall
CONTROL_KEYS = (Gdk.KEY_Control_L, Gdk.KEY_Control_R)

if TYPE_CHECKING:
    from ..window import Window


class Canvas(Gtk.Overlay):
    """A custom-drawn calendar surface.

    Routes clicks, double-clicks, context clicks, drags and keys to on_* methods.
    What's drawn is invisible to screen readers, so views also describe it with
    set_accessible_items(): an invisible, non-interactive layer of widgets on
    top that screen readers can read (days as lists, events as list items).
    """

    def __init__(self, host: Window, accessible_name: str):
        super().__init__()
        self.host = host
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True, can_target=False)
        self.area.set_draw_func(lambda _a, cr, w, h: self.draw(cr, w, h))
        self.set_child(self.area)
        self._a11y_layer = Gtk.Fixed(can_target=False, can_focus=False)
        self.add_overlay(self._a11y_layer)
        self.set_measure_overlay(self._a11y_layer, False)
        self._a11y_items = None
        self.set_focusable(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.update_property([Gtk.AccessibleProperty.LABEL], [accessible_name])
        self._origin = (0.0, 0.0)
        # How the current click selects: "replace", "extend" (Ctrl/Shift held) or
        # "context" (right-click keeps a selection it lands in).
        self.press_mode = "replace"
        self.drag_copy = False  # Ctrl held while dragging: copy instead of move
        self._drag_offset: tuple[float, float] | None = None  # while dragging

        click = Gtk.GestureClick(button=0)
        click.connect("pressed", self._on_pressed)
        self.add_controller(click)

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        long_press = Gtk.GestureLongPress(touch_only=True)
        long_press.connect("pressed", lambda _g, x, y: self._context(x, y))
        self.add_controller(long_press)

        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: self.on_hover(x, y))
        self.add_controller(motion)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", lambda _c, keyval, _code, state: self._on_key(keyval, state))
        keys.connect("key-released", lambda _c, keyval, _code, _state: self._on_control(keyval, False))
        self.add_controller(keys)

        focus = Gtk.EventControllerFocus()
        focus.connect("enter", lambda *_args: self.queue_draw())
        focus.connect("leave", lambda *_args: self.queue_draw())
        self.add_controller(focus)

    # The drawing area does the drawing; these keep views written as if drawing directly.

    def queue_draw(self) -> None:
        self.area.queue_draw()

    def set_content_height(self, height: int) -> None:
        self.area.set_content_height(height)

    def set_accessible_items(self, groups) -> None:
        """Describe what's drawn for screen readers.

        groups: [(label, (x, y, w, h), [item label, …])] — e.g. a day and its events.
        Rebuilt after drawing (never during it), and only when something changed.
        """
        groups = [(label, tuple(int(v) for v in box), tuple(items)) for label, box, items in groups]
        if groups == self._a11y_items:
            return
        self._a11y_items = groups
        GLib.idle_add(self._rebuild_accessible_items, groups)

    def _rebuild_accessible_items(self, groups) -> bool:
        if groups != self._a11y_items:
            return GLib.SOURCE_REMOVE  # superseded
        layer = self._a11y_layer
        while child := layer.get_first_child():
            layer.remove(child)
        for label, (x, y, w, h), items in groups:
            group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, can_target=False,
                            accessible_role=Gtk.AccessibleRole.LIST)
            group.update_property([Gtk.AccessibleProperty.LABEL], [label])
            group.set_size_request(max(w, 1), max(h, 1))
            for text in items:
                item = Gtk.Box(can_target=False, accessible_role=Gtk.AccessibleRole.LIST_ITEM)
                item.update_property([Gtk.AccessibleProperty.LABEL], [text])
                group.append(item)
            layer.put(group, x, y)
        return GLib.SOURCE_REMOVE

    @property
    def theme(self):
        return self.host.theme

    def palette(self) -> Palette:
        return Palette(self, self.host.theme)

    def show_focus(self) -> bool:
        """Draw a focus ring? Only while focused via the keyboard."""
        root = self.get_root()
        return self.has_focus() and bool(root and root.get_focus_visible())

    def _on_pressed(self, gesture, n_press, x, y):
        self.grab_focus()
        state = gesture.get_current_event_state()
        extend = state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK)
        self.press_mode = "extend" if extend else "replace"
        if gesture.get_current_button() == Gdk.BUTTON_SECONDARY:
            self._context(x, y)
        elif n_press == 2:
            self.on_double(x, y)
        elif n_press == 1:
            self.on_press(x, y)

    def _context(self, x, y):
        self.press_mode = "context"
        self.on_press(x, y)
        self.host.show_context_menu(self, x, y)

    def _on_drag_begin(self, _g, x, y):
        self._origin = (x, y)
        self.on_drag_begin(x, y)

    def _on_drag_update(self, gesture, dx, dy):
        self._update_drag_copy(gesture)
        self._drag_to(dx, dy)

    def _drag_to(self, dx, dy):
        self._drag_offset = (dx, dy)
        x, y = self._origin[0] + dx, self._origin[1] + dy
        self.on_drag_update(x, y, dx, dy)
        # Duplicating: a faded copy of the event follows the pointer.
        m = getattr(self, "_move", None)
        if m and m.active and m.mode == "move" and m.grab and self.drag_copy:
            gx, gy, w, h = m.grab
            # With the times it will get, unless it's changing between all-day and timed.
            copy = m.preview() if m.has_target and not m.crossed else m.occ
            self.host.drag_layer.show(self, copy, x - gx, y - min(gy, MAX_COPY_HEIGHT / 2),
                                      w, min(h, MAX_COPY_HEIGHT))
        else:
            self.host.drag_layer.hide()

    def _on_drag_end(self, gesture, dx, dy):
        self._update_drag_copy(gesture)
        self._drag_offset = None
        self.host.drag_layer.hide()
        self.on_drag_end(self._origin[0] + dx, self._origin[1] + dy)

    def _on_control(self, keyval: int, pressed: bool) -> bool:
        """Pressing or releasing Ctrl mid-drag switches between moving and copying."""
        if keyval in CONTROL_KEYS and self._drag_offset is not None and self.drag_copy != pressed:
            self.drag_copy = pressed
            self._drag_to(*self._drag_offset)
        return False

    def _update_drag_copy(self, gesture) -> None:
        state = gesture.get_current_event_state()
        self.drag_copy = bool(state & Gdk.ModifierType.CONTROL_MASK)

    def drag_cursor(self) -> str:
        return "copy" if self.drag_copy else "grabbing"

    def _on_key(self, keyval: int, state: Gdk.ModifierType) -> bool:
        if keyval in CONTROL_KEYS:
            return self._on_control(keyval, True)
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK):
            return False  # leave Ctrl/Alt shortcuts to the window
        if keyval == Gdk.KEY_space:
            self.host.cycle_selection(-1 if state & Gdk.ModifierType.SHIFT_MASK else 1)
            return True
        moves = {Gdk.KEY_Left: "left", Gdk.KEY_Right: "right", Gdk.KEY_Up: "up",
                 Gdk.KEY_Down: "down", Gdk.KEY_KP_Left: "left", Gdk.KEY_KP_Right: "right",
                 Gdk.KEY_KP_Up: "up", Gdk.KEY_KP_Down: "down"}
        if keyval in moves:
            self.on_move(moves[keyval])
            return True
        return False

    # -- overridden by views ----------------------------------------------

    def draw(self, cr, width, height): ...

    def draw_drag_copy(self, cr, occ, x, y, w, h) -> None:
        """The event as it looks in this view, for the copy under the pointer."""
        event_bar(cr, self, x, y, w, h, occ.color, occ.event.title, False, self.theme.font("caption"))

    def on_press(self, x, y): ...

    def on_double(self, x, y): ...

    def on_hover(self, x, y): ...

    def on_move(self, direction: str): ...

    def on_drag_begin(self, x, y): ...

    def on_drag_update(self, x, y, dx, dy): ...

    def on_drag_end(self, x, y): ...


class Hits:
    """Rectangles recorded while drawing, for hit-testing pointer events."""

    def __init__(self):
        self.items: list[tuple[float, float, float, float, str, object]] = []

    def clear(self):
        self.items.clear()

    def add(self, x, y, w, h, kind, payload):
        self.items.append((x, y, w, h, kind, payload))

    def find(self, px, py, kind=None, reverse=False):
        items = reversed(self.items) if reverse else self.items
        for x, y, w, h, k, payload in items:
            if (kind is None or k == kind) and x <= px < x + w and y <= py < y + h:
                return k, payload, (x, y, w, h)
        return None, None, None


class DragLayer(Gtk.DrawingArea):
    """The faded copy of an event that follows the pointer while it's being
    duplicated (Ctrl+drag). It lies over all the views, so the copy can move
    between the all-day row and the time grid."""

    def __init__(self):
        super().__init__(can_target=False, can_focus=False)
        self._copy = None  # (source canvas, occurrence, x, y, w, h), in this widget
        self.set_draw_func(lambda _a, cr, _w, _h: self._draw(cr))

    def show(self, source: Canvas, occ, x, y, w, h) -> None:
        ok, point = source.compute_point(self, Graphene.Point().init(x, y))
        if ok:
            self._copy = (source, occ, point.x, point.y, w, h)
            self.queue_draw()

    def hide(self) -> None:
        if self._copy:
            self._copy = None
            self.queue_draw()

    def _draw(self, cr) -> None:
        if not self._copy:
            return
        source, occ, x, y, w, h = self._copy
        cr.push_group()
        source.draw_drag_copy(cr, occ, x, y, w, h)
        # A thin outline in the text color keeps it apart from the events below.
        stroke_rect(cr, x, y, w, h, source.palette().fg_alpha(0.5), 6, 1)
        cr.pop_group_to_source()
        cr.paint_with_alpha(0.75)
