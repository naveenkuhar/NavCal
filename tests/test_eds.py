"""Integration tests for the EDS backend. Run via tests/run-eds-tests.sh."""

import os
from datetime import datetime, timedelta

import pytest

if not os.environ.get("NAVCAL_EDS_TEST"):
    pytest.skip("needs a private EDS session: run tests/run-eds-tests.sh", allow_module_level=True)

from gi.repository import GLib  # noqa: E402

from navcal.eds import Backend  # noqa: E402
from navcal.models import Event, Freq, Scope  # noqa: E402

RANGE = (datetime(2026, 9, 1), datetime(2026, 12, 1))


def pump(backend, until, timeout=10.0):
    """Run the main loop until until() is true (view updates are async)."""
    deadline = GLib.get_monotonic_time() + int(timeout * 1e6)
    ctx = GLib.MainContext.default()
    while not until() and GLib.get_monotonic_time() < deadline:
        ctx.iteration(True)
    assert until(), "timed out waiting for the calendar service"


@pytest.fixture(scope="module")
def backend():
    return Backend()


@pytest.fixture
def cal(backend, request):
    uid = backend.add_local_calendar(request.node.name[:40], "#3584e4")
    return backend.connect_calendar_sync(uid)


def occs(backend, cal, title=None):
    out = backend.occurrences(*RANGE, calendars=[cal])
    return [o for o in out if title is None or o.event.title.startswith(title)]


def test_create_and_read(backend, cal):
    backend.create(cal.uid, Event("Lunch", datetime(2026, 9, 24, 12), datetime(2026, 9, 24, 13),
                                  reminders=[10]))
    pump(backend, lambda: occs(backend, cal))
    [o] = occs(backend, cal)
    assert (o.event.title, o.start, o.event.reminders, o.color) == (
        "Lunch", datetime(2026, 9, 24, 12), [10], "#3584e4")


def standup(backend, cal):
    backend.create(cal.uid, Event("Standup", datetime(2026, 9, 21, 9), datetime(2026, 9, 21, 9, 30),
                                  freq=Freq.WEEKLY, byday=[(0, 0), (2, 0), (4, 0)], count=10))
    pump(backend, lambda: len(occs(backend, cal)) == 10)
    return occs(backend, cal)


def test_edit_only_this_occurrence(backend, cal):
    o = standup(backend, cal)[1]  # Wed Sep 23
    edited = o.event.clone(title="Standup (moved)", start=o.start + timedelta(hours=1),
                           end=o.end + timedelta(hours=1))
    backend.save(o, edited, Scope.THIS)
    pump(backend, lambda: occs(backend, cal, "Standup (moved)"))
    all_ = occs(backend, cal)
    assert len(all_) == 10
    moved = [x for x in all_ if x.event.title == "Standup (moved)"]
    assert [x.start for x in moved] == [datetime(2026, 9, 23, 10)]
    assert datetime(2026, 9, 23, 9) not in [x.start for x in all_]


def test_this_and_following(backend, cal):
    o = standup(backend, cal)[6]  # Mon Oct 5
    backend.save(o, o.event.clone(title="Standup v2"), Scope.FUTURE)
    pump(backend, lambda: occs(backend, cal, "Standup v2"))
    titles = [x.event.title for x in occs(backend, cal)]
    assert titles.count("Standup") == 6 and titles.count("Standup v2") == 4


def test_edit_all_shifts_series(backend, cal):
    o = standup(backend, cal)[2]
    edited = o.event.clone(start=o.start + timedelta(minutes=30), end=o.end + timedelta(minutes=30))
    backend.save(o, edited, Scope.ALL)
    pump(backend, lambda: occs(backend, cal)[0].start.minute == 30)
    assert all(x.start.minute == 30 for x in occs(backend, cal))


def test_delete_one_occurrence_then_undo(backend, cal):
    all_ = standup(backend, cal)
    snap = backend.snapshot(cal.uid, all_[0].event.uid)
    backend.remove(all_[3], Scope.THIS)
    pump(backend, lambda: len(occs(backend, cal)) == 9)
    backend.restore(snap)
    pump(backend, lambda: len(occs(backend, cal)) == 10)


def test_move_to_other_calendar(backend, cal):
    other = backend.connect_calendar_sync(backend.add_local_calendar("Other", "#e62d42"))
    backend.create(cal.uid, Event("Dentist", datetime(2026, 10, 1, 8), datetime(2026, 10, 1, 9)))
    pump(backend, lambda: occs(backend, cal))
    o = occs(backend, cal)[0]
    backend.save(o, o.event.clone(calendar=other.uid), Scope.ALL)
    pump(backend, lambda: not occs(backend, cal) and occs(backend, other))
    assert occs(backend, other)[0].color == "#e62d42"


def test_export_import_roundtrip(backend, cal):
    standup(backend, cal)
    text = backend.export_text([cal])
    target = backend.connect_calendar_sync(backend.add_local_calendar("Imported", "#9141ac"))
    assert backend.import_text(target.uid, text) == 1
    pump(backend, lambda: len(occs(backend, target)) == 10)


def _ics(numbers, edited=False) -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//test//EN"]
    for n in numbers:
        day = datetime(2026, 9, 1) + timedelta(hours=n)
        lines += ["BEGIN:VEVENT", f"UID:bulk-{n}", f"SUMMARY:Bulk {n}", "DTSTAMP:20260101T000000Z",
                  f"DTSTART:{day:%Y%m%dT%H%M%S}", f"DTEND:{day + timedelta(minutes=30):%Y%m%dT%H%M%S}",
                  "END:VEVENT"]
    lines += ["BEGIN:VEVENT", "UID:bulk-weekly", "SUMMARY:Weekly", "DTSTAMP:20260101T000000Z",
              "DTSTART:20260901T080000", "DTEND:20260901T090000", "RRULE:FREQ=WEEKLY;COUNT=4",
              "END:VEVENT"]
    if edited:  # the second one moved to the afternoon
        lines += ["BEGIN:VEVENT", "UID:bulk-weekly", "SUMMARY:Weekly (moved)", "DTSTAMP:20260101T000000Z",
                  "RECURRENCE-ID:20260908T080000", "DTSTART:20260908T150000", "DTEND:20260908T160000",
                  "END:VEVENT"]
    return "\r\n".join(lines + ["END:VCALENDAR", ""])


def test_import_many_and_again(backend, cal):
    assert backend.import_text(cal.uid, _ics(range(450), edited=True)) == 451
    pump(backend, lambda: any(o.event.title == "Weekly (moved)" for o in occs(backend, cal)))
    weekly = [(o.event.title, o.start) for o in occs(backend, cal) if "Weekly" in o.event.title]
    # The edited instance (in floating time, as some apps export) replaces the original.
    assert weekly == [("Weekly", datetime(2026, 9, 1, 8)), ("Weekly (moved)", datetime(2026, 9, 8, 15)),
                      ("Weekly", datetime(2026, 9, 15, 8)), ("Weekly", datetime(2026, 9, 22, 8))]
    assert len(occs(backend, cal)) == 454
    # Importing again, with some new events: nothing doubles, the new ones arrive.
    assert backend.import_text(cal.uid, _ics(range(400, 500))) == 101
    pump(backend, lambda: len(occs(backend, cal)) == 504)
    titles = [o.event.title for o in occs(backend, cal)]
    assert len(titles) == 504 and titles.count("Bulk 420") == 1


def test_hidden_calendar_is_skipped(backend, cal):
    backend.create(cal.uid, Event("Secret", datetime(2026, 9, 24, 12), datetime(2026, 9, 24, 13)))
    pump(backend, lambda: occs(backend, cal))
    backend.set_visible(cal, False)
    assert not [o for o in backend.occurrences(*RANGE) if o.event.title == "Secret"]
    backend.set_visible(cal, True)
    assert [o for o in backend.occurrences(*RANGE) if o.event.title == "Secret"]


class FakeApp:
    def __init__(self):
        self.sent = []

    def send_notification(self, nid, note):
        self.sent.append(nid)

    def withdraw_notification(self, nid):
        pass


def test_reminders_fire_and_catch_up(backend, cal, tmp_path, monkeypatch):
    from navcal import reminders
    from navcal.config import Config

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    now = datetime.now().replace(second=0, microsecond=0)
    # Due now, and one that came due an hour ago while "Navcal was closed".
    backend.create(cal.uid, Event("Soon", now + timedelta(minutes=10), now + timedelta(minutes=40),
                                  reminders=[10]))
    backend.create(cal.uid, Event("Earlier", now - timedelta(minutes=30), now,
                                  reminders=[30]))
    pump(backend, lambda: len(backend.occurrences(now - timedelta(days=1), now + timedelta(days=1),
                                                  calendars=[cal])) == 2)
    config = Config()
    config["reminders-checked"] = (now - timedelta(hours=2)).isoformat()
    app = FakeApp()
    service = reminders.ReminderService(app, backend, config)
    service._ready = True
    service.gnome_alarms = False
    due = service.check()
    assert sorted(o.event.title for o in due if o.event.calendar == cal.uid) == ["Earlier", "Soon"]
    assert service.check() == []  # nothing fires twice


def test_subscribe_keeps_encoded_link(backend):
    url = "https://example.com/cal/en.usa%23holiday%40group/basic.ics?a=1%202"
    uid = backend.subscribe(url, "Encoded", "#3584e4")
    from gi.repository import EDataServer as E
    source = backend.registry.ref_source(uid)
    webdav = source.get_extension(E.SOURCE_EXTENSION_WEBDAV_BACKEND)
    assert webdav.dup_uri().to_string() == url  # no "#", no ":80"


def test_tasks(backend):
    from navcal.models import Task
    from datetime import date as d
    uid = backend.add_local_calendar("Chores", "#3a944a", kind="tasks")
    tl = backend.connect_calendar_sync(uid)
    assert tl.kind == "tasks" and tl in backend.writable_task_lists()
    task_uid = backend.plan_create_task(uid, Task("Buy milk", due=d(2026, 9, 25)))()
    pump(backend, lambda: any(t.uid == task_uid for t in backend.tasks()))
    task = next(t for t in backend.tasks() if t.uid == task_uid)
    snap = backend.snapshot(uid, task_uid)
    task.done = True
    backend.plan_save_task(task)()
    pump(backend, lambda: next(t for t in backend.tasks() if t.uid == task_uid).done)
    assert backend.tasks()[-1].uid == task_uid  # finished tasks sort last
    backend.plan_remove_task(task)()
    pump(backend, lambda: not any(t.uid == task_uid for t in backend.tasks()))
    backend.restore(snap)  # undo brings back the unfinished task
    pump(backend, lambda: any(t.uid == task_uid and not t.done for t in backend.tasks()))
