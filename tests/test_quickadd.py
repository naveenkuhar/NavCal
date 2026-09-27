from datetime import datetime, time, timedelta

import pytest

from navcal.models import Freq
from navcal.quickadd import parse

NOW = datetime(2026, 9, 24, 10, 17)  # a Thursday


def p(text, **kw):
    return parse(text, NOW, **kw)


def test_title_date_time():
    ev = p("Lunch with Sam tomorrow 1pm")
    assert (ev.title, ev.start, ev.end, ev.all_day) == (
        "Lunch with Sam", datetime(2026, 9, 25, 13), datetime(2026, 9, 25, 14), False)


def test_place_with_at_capitalized():
    ev = p("Coffee with Ana at Café Luna friday 9:30am")
    assert (ev.title, ev.location, ev.start) == ("Coffee with Ana", "Café Luna",
                                                 datetime(2026, 9, 25, 9, 30))


def test_at_lowercase_is_not_a_place():
    ev = p("Look at the report at 3")
    assert (ev.title, ev.location, ev.start.time()) == ("Look at the report", "", time(15))


def test_place_with_at_sign():
    ev = p("Standup @ Room 4 9am")
    assert (ev.title, ev.location, ev.start.time()) == ("Standup", "Room 4", time(9))


@pytest.mark.parametrize("text,start,end", [
    ("Meeting 1-2pm", time(13), time(14)),
    ("Workshop 9-5pm", time(9), time(17)),
    ("Party from 10pm to 1am", time(22), time(1)),
    ("Call 14:00", time(14), time(15)),
    ("Lunch noon", time(12), time(13)),
])
def test_times(text, start, end):
    ev = p(text)
    assert (ev.start.time(), ev.end.time()) == (start, end)


def test_duration():
    ev = p("Run tomorrow 7am for 45 min")
    assert (ev.title, ev.end - ev.start) == ("Run", timedelta(minutes=45))


def test_date_only_is_all_day():
    ev = p("Mom's birthday oct 3")
    assert (ev.title, ev.all_day, ev.start, ev.end) == (
        "Mom's birthday", True, datetime(2026, 10, 3), datetime(2026, 10, 4))


def test_past_date_means_next_year():
    assert p("Trip march 1").start.year == 2027


def test_weekdays():
    assert p("Gym friday 6pm").start == datetime(2026, 9, 25, 18)
    assert p("Gym next friday 6pm").start == datetime(2026, 10, 2, 18)
    assert p("Gym thursday 6pm").start == datetime(2026, 9, 24, 18)  # today


def test_numeric_dates_follow_locale_order():
    assert p("Dentist 10/3 2pm").start == datetime(2026, 10, 3, 14)
    assert p("Dentist 10/3 2pm", month_first=False).start == datetime(2026, 3, 10, 14) + timedelta(days=365)


def test_repeats():
    ev = p("Standup every weekday 9am")
    assert (ev.title, ev.freq, ev.byday) == ("Standup", Freq.WEEKLY, [(d, 0) for d in range(5)])
    ev = p("Guitar every tuesday and thursday 5pm")
    assert (ev.freq, ev.byday, ev.start) == (Freq.WEEKLY, [(1, 0), (3, 0)], datetime(2026, 9, 24, 17))
    ev = p("Rent monthly on the 1st of october")
    assert (ev.freq, ev.start.date().day) == (Freq.MONTHLY, 1)
    ev = p("Review every other week friday 3pm")
    assert (ev.freq, ev.interval) == (Freq.WEEKLY, 2)


def test_no_date_or_time_is_next_hour():
    ev = p("Call the bank")
    assert (ev.title, ev.start, ev.all_day) == ("Call the bank", datetime(2026, 9, 24, 11), False)


def test_tonight_and_in_n_days():
    assert p("Movie tonight").start == datetime(2026, 9, 24, 19)
    assert p("Follow up in 3 days 10am").start == datetime(2026, 9, 27, 10)


def test_focused_day_and_slot_are_defaults():
    from datetime import date
    ev = parse("Call the bank", NOW, default_day=date(2026, 9, 30))
    assert ev.all_day and ev.start == datetime(2026, 9, 30)
    ev = parse("Call the bank", NOW, default_day=date(2026, 9, 30), default_time=time(15))
    assert ev.start == datetime(2026, 9, 30, 15)
    ev = parse("Call the bank 4pm", NOW, default_day=date(2026, 9, 30))
    assert ev.start == datetime(2026, 9, 30, 16)
