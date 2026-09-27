from datetime import date, datetime, timedelta

import gi
import pytest

gi.require_version("ICalGLib", "3.0")
from gi.repository import ICalGLib as I

from navcal import ical
from navcal.models import Event, Freq


def roundtrip(ev: Event) -> Event:
    comp = ical.component_from_event(ev)
    return ical.event_from_component(I.Component.new_from_string(comp.as_ical_string()),
                                     ical.builtin_zone)


def test_timed_roundtrip_with_rule_and_reminders():
    ev = Event("Standup", datetime(2026, 9, 21, 9), datetime(2026, 9, 21, 9, 30),
               location="Room 4", notes="daily sync", freq=Freq.WEEKLY, interval=2,
               byday=[(0, 0), (2, 0), (4, 0)], count=10, reminders=[10, 1440], color="#e62d42")
    back = roundtrip(ev)
    assert (back.title, back.start, back.end, back.location, back.notes) == (
        "Standup", ev.start, ev.end, "Room 4", "daily sync")
    assert (back.freq, back.interval, sorted(back.byday), back.count) == (
        Freq.WEEKLY, 2, [(0, 0), (2, 0), (4, 0)], 10)
    assert back.reminders == [10, 1440]
    assert back.color == "#e62d42"
    assert back.tzid == ical.local_tzid()


def test_all_day_and_until():
    ev = Event("Trip", datetime(2026, 9, 25), datetime(2026, 9, 28), all_day=True,
               freq=Freq.YEARLY, until=date(2030, 1, 1))
    back = roundtrip(ev)
    assert back.all_day and (back.start, back.end) == (ev.start, ev.end)
    assert back.until == date(2030, 1, 1)


def test_nth_weekday_monthly_expansion():
    ev = Event("Board", datetime(2026, 9, 8, 18), datetime(2026, 9, 8, 19), freq=Freq.MONTHLY,
               byday=[(1, 2)], count=4)  # 2nd Tuesday
    comp = ical.component_from_event(ev)
    starts = [s for s, _, _ in ical.expand(comp, datetime(2026, 9, 1), datetime(2027, 3, 1),
                                           ical.builtin_zone)]
    assert starts == [datetime(2026, 9, 8, 18), datetime(2026, 10, 13, 18),
                      datetime(2026, 11, 10, 18), datetime(2026, 12, 8, 18)]


def test_last_friday_rule_string():
    ev = Event("x", datetime(2026, 9, 25, 9), datetime(2026, 9, 25, 10), freq=Freq.MONTHLY,
               byday=[(4, -1)])
    assert ical.rrule_string(ev) == "FREQ=MONTHLY;BYDAY=-1FR"


def test_other_time_zone_is_converted_to_local():
    # 09:00 in Tokyo, stored with its zone, shows up at the right local time.
    tokyo_start = datetime(2026, 9, 24, 9)
    local_start = ical.from_zone(tokyo_start, "Asia/Tokyo")
    ev = Event("Tokyo call", local_start, local_start + timedelta(hours=1), tzid="Asia/Tokyo")
    comp = ical.component_from_event(ev)
    assert "Asia/Tokyo" in comp.as_ical_string()
    assert "20260924T090000" in comp.as_ical_string()
    back = roundtrip(ev)
    assert (back.start, back.tzid) == (local_start, "Asia/Tokyo")


def test_unknown_properties_and_rule_parts_survive_edits():
    src = """BEGIN:VEVENT
UID:keep-1
SUMMARY:Planning
DTSTART:20260924T140000Z
DTEND:20260924T150000Z
RRULE:FREQ=YEARLY;BYMONTH=3,9;BYDAY=2TH
ATTENDEE;CN=Sam:mailto:sam@example.com
X-CUSTOM:hello
END:VEVENT"""
    base = I.Component.new_from_string(src)
    ev = ical.event_from_component(base, ical.builtin_zone)
    assert ev.freq == Freq.YEARLY and ev.rrule_extra == "BYMONTH=3,9"
    ev.title = "Planning (renamed)"
    out = ical.component_from_event(ev, base).as_ical_string()
    assert "ATTENDEE" in out and "X-CUSTOM:hello" in out and "BYMONTH=3,9" in out
    assert "Planning (renamed)" in out and "UID:keep-1" in out


def test_calendar_text_and_parse():
    ev = Event("A", datetime(2026, 9, 24, 10), datetime(2026, 9, 24, 11), tzid="Europe/Berlin")
    comp = ical.component_from_event(ev)
    text = ical.calendar_text([comp], [ical.builtin_zone("Europe/Berlin")])
    events, zones = ical.parse_calendar(text)
    assert len(events) == 1 and len(zones) == 1
    assert events[0].get_summary() == "A"


def test_time_zone_list():
    zones = ical.all_tzids()
    assert "Europe/Berlin" in zones and len(zones) > 300


def test_links_and_attachments_roundtrip():
    ev = Event("Trip", datetime(2026, 9, 24, 10), datetime(2026, 9, 24, 11),
               url="https://example.com/trip",
               attachments=["https://example.com/tickets.pdf", "file:///home/me/plan.odt"])
    back = roundtrip(ev)
    assert back.url == "https://example.com/trip"
    assert back.attachments == ["https://example.com/tickets.pdf", "file:///home/me/plan.odt"]
    back.attachments = back.attachments[:1]
    again = ical.event_from_component(
        I.Component.new_from_string(ical.component_from_event(back, back.raw).as_ical_string()),
        ical.builtin_zone)
    assert again.attachments == ["https://example.com/tickets.pdf"]


def test_travel_time_uses_apple_property():
    ev = Event("Dinner", datetime(2026, 9, 24, 19), datetime(2026, 9, 24, 21), travel_minutes=30)
    text = ical.component_from_event(ev).as_ical_string()
    assert "X-APPLE-TRAVEL-DURATION;VALUE=DURATION:PT30M" in text
    assert roundtrip(ev).travel_minutes == 30
    ev.travel_minutes = 0
    assert "TRAVEL" not in ical.component_from_event(ev).as_ical_string()


def test_task_roundtrip_and_completion():
    from datetime import date as d
    from navcal.models import Task
    task = Task("Buy milk", due=d(2026, 9, 25), notes="2%")
    comp = ical.component_from_task(task)
    back = ical.task_from_component(I.Component.new_from_string(comp.as_ical_string()), ical.builtin_zone)
    assert (back.title, back.due, back.done, back.notes) == ("Buy milk", d(2026, 9, 25), False, "2%")
    back.done = True
    done = ical.component_from_task(back, back.raw).as_ical_string()
    assert "STATUS:COMPLETED" in done and "COMPLETED:" in done and "PERCENT-COMPLETE:100" in done
    timed = Task("Call", due=datetime(2026, 9, 25, 15, 30))
    again = ical.task_from_component(
        I.Component.new_from_string(ical.component_from_task(timed).as_ical_string()), ical.builtin_zone)
    assert again.due == datetime(2026, 9, 25, 15, 30)


# -- skipping ahead in long-running series -----------------------------------------

RULES = [
    "FREQ=DAILY", "FREQ=DAILY;INTERVAL=3", "FREQ=WEEKLY",
    "FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE,FR;WKST=SU", "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
    "FREQ=MONTHLY;BYDAY=2TU", "FREQ=MONTHLY;BYDAY=-1FR", "FREQ=MONTHLY;BYMONTHDAY=31", "FREQ=MONTHLY",
    "FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=-1", "FREQ=MONTHLY;BYDAY=MO,TU,WE,TH,FR;BYSETPOS=-1",
    "FREQ=YEARLY", "FREQ=YEARLY;BYMONTH=11;BYDAY=4TH", "FREQ=YEARLY;INTERVAL=2",
    "FREQ=DAILY;UNTIL=20261015T000000Z", "FREQ=WEEKLY;UNTIL=20200101T000000Z", "FREQ=WEEKLY;COUNT=500",
]
# (DTSTART, DTEND) property text; every series begins on Feb 29 or Jan 31, years ago.
TIMES = {
    "zone": ("DTSTART;TZID=America/Toronto:20160131T090000", "DTEND;TZID=America/Toronto:20160131T100000"),
    "overnight": ("DTSTART;TZID=America/Toronto:20160229T230000", "DTEND;TZID=America/Toronto:20160301T013000"),
    "utc": ("DTSTART:20160131T150000Z", "DTEND:20160131T160000Z"),
    "floating": ("DTSTART:20160229T083000", "DTEND:20160229T083000"),  # zero length
    "kolkata": ("DTSTART;TZID=Asia/Kolkata:20160131T193000", "DTEND;TZID=Asia/Kolkata:20160131T203000"),
    "lord-howe": ("DTSTART;TZID=Australia/Lord_Howe:20160229T013000",
                  "DTEND;TZID=Australia/Lord_Howe:20160229T030000"),
    "all-day": ("DTSTART;VALUE=DATE:20160229", "DTEND;VALUE=DATE:20160303"),
}
RANGES = [(datetime(2026, 1, 1), datetime(2027, 1, 1)), (datetime(2026, 10, 25), datetime(2026, 11, 8)),
          (datetime(2028, 2, 20), datetime(2028, 3, 20)), (datetime(2027, 3, 10), datetime(2027, 3, 18))]


def _series(rule: str, times: tuple[str, str], extra: str = "") -> I.Component:
    return I.Component.new_from_string(
        f"BEGIN:VEVENT\r\nUID:ff\r\n{times[0]}\r\n{times[1]}\r\nRRULE:{rule}\r\n{extra}END:VEVENT\r\n")


def _instances(comp, start, end):
    return [(s, e, rid.as_ical_string()) for s, e, rid in ical.expand(comp, start, end, ical.builtin_zone)]


@pytest.mark.parametrize("rule", RULES)
@pytest.mark.parametrize("kind", TIMES)
def test_fast_forward_gives_the_same_instances(rule, kind):
    comp = _series(rule, TIMES[kind])
    for start, end in RANGES:
        moved = ical.fast_forward(comp, start, ical.builtin_zone)
        if "COUNT" not in rule and "UNTIL" not in rule:
            assert moved is not comp, "should skip ahead"
        assert _instances(moved, start, end) == _instances(comp, start, end), (start, end)


def test_fast_forward_keeps_skipped_and_extra_dates():
    comp = _series("FREQ=DAILY", TIMES["zone"], "EXDATE;TZID=America/Toronto:20261014T090000\r\n"
                   "RDATE;TZID=America/Toronto:20261020T150000\r\n")
    start, end = datetime(2026, 10, 1), datetime(2026, 11, 1)
    got = _instances(ical.fast_forward(comp, start, ical.builtin_zone), start, end)
    assert got == _instances(comp, start, end)
    starts = [s for s, _e, _r in got]
    assert datetime(2026, 10, 14, 9) not in starts and datetime(2026, 10, 20, 15) in starts


def test_place_roundtrip():
    ev = Event("Dinner", datetime(2026, 10, 2, 19), datetime(2026, 10, 2, 21), location="Café Luna",
               geo=(45.5019, -73.5674), place="N123456")
    back = roundtrip(ev)
    assert back.geo == (45.5019, -73.5674) and back.place == "N123456"
    back.geo, back.place = None, ""
    again = roundtrip(back)
    assert again.geo is None and again.place == ""
