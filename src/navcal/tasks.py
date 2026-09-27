"""Tasks: the task editor dialog and task rows (with a check box) for lists."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Callable

from . import resources  # noqa: F401  (registers the UI templates)
from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from .draw import fmt_time  # noqa: E402
from .editor import CalendarChoice, DateButton, TimeButton, fmt_date  # noqa: E402
from .i18n import _  # noqa: E402
from .models import Task  # noqa: E402
from .resources import RESOURCE_BASE  # noqa: E402


def due_text(task: Task, today: date | None = None) -> str:
    """"Due today", "Overdue · Mon, Sep 21", "Due Fri, Sep 25 at 3:00 PM"…"""
    if task.due is None:
        return ""
    today = today or date.today()
    d = task.due_date
    time_text = fmt_time(task.due) if isinstance(task.due, datetime) else None
    if d == today:
        text = _("Due today at {time}") if time_text else _("Due today")
    elif d == today + timedelta(days=1):
        text = _("Due tomorrow at {time}") if time_text else _("Due tomorrow")
    elif not task.done and d < today:
        text = _("Overdue · {day} at {time}") if time_text else _("Overdue · {day}")
    else:
        text = _("Due {day} at {time}") if time_text else _("Due {day}")
    return text.format(day=fmt_date(d), time=time_text)


def task_row(task: Task, on_toggle: Callable[[Task, bool], None],
             on_open: Callable[[Task], None]) -> Adw.ActionRow:
    """A row with a check box. Finished tasks are struck through and dimmed, so
    their state doesn't rely on color."""
    title = GLib.markup_escape_text(task.title)
    row = Adw.ActionRow(title=f"<s>{title}</s>" if task.done else title, use_markup=True,
                        subtitle=due_text(task), activatable=True)
    if task.done:
        row.add_css_class("dim-label")
    elif task.due_date and task.due_date < date.today():
        row.add_css_class("warning")
    check = Gtk.CheckButton(active=task.done, valign=Gtk.Align.CENTER,
                            sensitive=not task.readonly, tooltip_text=_("Mark as done"))
    check.update_property([Gtk.AccessibleProperty.LABEL], [task.title])
    check.connect("toggled", lambda b: on_toggle(task, b.get_active()))
    row.add_prefix(check)
    row.connect("activated", lambda _r: on_open(task))
    row.task = task
    return row


@Gtk.Template(resource_path=f"{RESOURCE_BASE}/ui/task-dialog.ui")
class TaskDialog(Adw.Dialog):
    __gtype_name__ = "NavcalTaskDialog"

    header: Adw.HeaderBar = Gtk.Template.Child()
    cancel_button: Gtk.Button = Gtk.Template.Child()
    save_button: Gtk.Button = Gtk.Template.Child()
    readonly_banner: Adw.Banner = Gtk.Template.Child()
    title_row: Adw.EntryRow = Gtk.Template.Child()
    done_row: Adw.SwitchRow = Gtk.Template.Child()
    due_row: Adw.ExpanderRow = Gtk.Template.Child()
    date_row: Adw.ActionRow = Gtk.Template.Child()
    timed_row: Adw.SwitchRow = Gtk.Template.Child()
    time_row: Adw.ActionRow = Gtk.Template.Child()
    list_row: Adw.ComboRow = Gtk.Template.Child()
    notes_view: Gtk.TextView = Gtk.Template.Child()
    delete_group: Adw.PreferencesGroup = Gtk.Template.Child()
    delete_row: Adw.ButtonRow = Gtk.Template.Child()

    def __init__(self, task: Task, *, lists: list[CalendarChoice], on_save: Callable[[Task], None],
                 on_delete: Callable[[], None] | None = None, new: bool = False):
        super().__init__(title=_("New Task") if new else _("Edit Task"))
        self._task, self._on_save, self._on_delete = task, on_save, on_delete
        self.cancel_button.connect("clicked", lambda _b: self.close())
        self.save_button.connect("clicked", lambda _b: self._save())
        if new:
            self.save_button.set_label(_("_Add"))

        self.title_row.set_text(task.title)
        self.title_row.connect("changed", lambda *_args: self._validate())
        self.done_row.set_active(task.done)
        due = task.due or datetime.combine(date.today(), time(9))
        self._date = DateButton(task.due_date or date.today(), lambda: None)
        self._time = TimeButton(due.time() if isinstance(due, datetime) else time(9), lambda: None)
        self.date_row.add_suffix(self._date)
        self.date_row.set_activatable_widget(self._date)
        self.time_row.add_suffix(self._time)
        self.time_row.set_activatable_widget(self._time)
        self.due_row.set_enable_expansion(task.due is not None)
        self.due_row.set_expanded(task.due is not None)
        self.timed_row.set_active(isinstance(task.due, datetime))
        self.timed_row.bind_property("active", self.time_row, "visible",
                                     GObject.BindingFlags.SYNC_CREATE)

        if task.list and task.list not in [c.uid for c in lists]:
            lists = lists + [CalendarChoice(task.list, _("Read-only list"), "")]
        self._lists = lists
        self.list_row.set_model(Gtk.StringList.new([c.name for c in lists]))
        uids = [c.uid for c in lists]
        self.list_row.set_selected(uids.index(task.list) if task.list in uids else 0)
        self.notes_view.get_buffer().set_text(task.notes)
        self.delete_group.set_visible(on_delete is not None and not task.readonly)
        self.delete_row.connect("activated", lambda _r: self._delete())

        if task.readonly:
            self.cancel_button.set_visible(False)
            self.save_button.set_visible(False)
            self.header.set_show_end_title_buttons(True)
            self.readonly_banner.set_revealed(True)
            for row in (self.done_row, self.due_row, self.list_row):
                row.set_sensitive(False)
            self.title_row.set_editable(False)
            self.notes_view.set_editable(False)
        else:
            self.set_default_widget(self.save_button)
            self.set_focus(self.title_row)
        self._validate()

    def _validate(self) -> None:
        self.save_button.set_sensitive(bool(self.title_row.get_text().strip()))

    def result(self) -> Task:
        buffer = self.notes_view.get_buffer()
        due = None
        if self.due_row.get_enable_expansion():
            due = self._date.get_date()
            if self.timed_row.get_active():
                due = datetime.combine(due, self._time.get_time())
        chosen = self._lists[self.list_row.get_selected()].uid
        t = self._task
        return Task(title=self.title_row.get_text().strip(), due=due, done=self.done_row.get_active(),
                    notes=buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).strip(),
                    list=t.list or chosen, new_list=chosen if chosen != t.list else None,
                    uid=t.uid, raw=t.raw)

    def _save(self) -> None:
        if not self.save_button.get_sensitive():
            return
        task = self.result()
        self.close()
        self._on_save(task)

    def _delete(self) -> None:
        self.close()
        self._on_delete()

