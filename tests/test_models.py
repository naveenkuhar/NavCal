import sqlite3
from datetime import date, datetime

from navcal.layout import assign_lanes, layout_columns
from navcal.legacy import load_legacy_events
from navcal.models import Event, Freq, Occurrence, days_covered, sort_occurrences


def test_days_covered_excludes_midnight_end():
    s, e = datetime(2026, 9, 24), datetime(2026, 9, 26)
    assert list(days_covered(s, e)) == [date(2026, 9, 24), date(2026, 9, 25)]


def test_banners_sort_first():
    timed = Event("t", datetime(2026, 9, 24, 8), datetime(2026, 9, 24, 9))
    allday = Event("a", datetime(2026, 9, 24), datetime(2026, 9, 25), all_day=True)
    occs = sort_occurrences([Occurrence(timed, timed.start, timed.end),
                             Occurrence(allday, allday.start, allday.end)])
    assert [o.event.title for o in occs] == ["a", "t"]


def test_clone_is_a_new_event():
    ev = Event("x", datetime(2026, 9, 24, 8), datetime(2026, 9, 24, 9), uid="abc", readonly=True,
               byday=[(0, 0)])
    copy = ev.clone(title="y")
    assert (copy.uid, copy.readonly, copy.title) == (None, False, "y")
    copy.byday.append((1, 0))
    assert ev.byday == [(0, 0)]


def test_layout_columns_overlap():
    base = datetime(2026, 9, 24)
    segs = [("a", base.replace(hour=9), base.replace(hour=11)),
            ("b", base.replace(hour=10), base.replace(hour=12)),
            ("c", base.replace(hour=11), base.replace(hour=12)),
            ("d", base.replace(hour=14), base.replace(hour=15))]
    out = {o: (col, n) for o, _, _, col, n in layout_columns(segs)}
    assert out == {"a": (0, 2), "b": (1, 2), "c": (0, 2), "d": (0, 1)}


def test_assign_lanes():
    items, lanes = assign_lanes([("a", 0, 3), ("b", 1, 2), ("c", 4, 6)])
    assert lanes == 2
    assert {o: lane for o, _, _, lane in items} == {"a": 0, "b": 1, "c": 0}


def test_legacy_loader(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.execute("""CREATE TABLE events (id INTEGER PRIMARY KEY, title TEXT, start_at TEXT,
        end_at TEXT, all_day INTEGER, location TEXT, notes TEXT, color TEXT, freq TEXT,
        interval INTEGER, until TEXT, reminder_minutes INTEGER, exdates TEXT)""")
    db.execute("INSERT INTO events VALUES (1, 'Gym', '2026-09-23T18:00:00', '2026-09-23T19:00:00',"
               " 0, '', '', '#2190a4', 'weekly', 1, NULL, 15, '[\"2026-09-30\"]')")
    db.commit()
    [ev] = load_legacy_events(tmp_path / "old.db")
    assert (ev.title, ev.freq, ev.byday, ev.reminders, ev.exdates) == (
        "Gym", Freq.WEEKLY, [(2, 0)], [15], {date(2026, 9, 30)})
