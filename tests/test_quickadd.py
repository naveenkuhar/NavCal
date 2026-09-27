# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import shutil
import subprocess
from datetime import datetime, time, timedelta
from pathlib import Path

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


# The website's demo (docs/quickadd.js) has the same rules in JavaScript: both must agree.
WEBSITE_PHRASES = [
    "Lunch with Sam tomorrow 1pm at Café Luna", "Dentist friday 9am", "Team meeting next monday 10:30-11:45",
    "Call mom at 3", "Yoga every monday and thursday 6pm", "Standup every weekday 9:15",
    "Standup every weekday 9am for 15 min", "Conference sep 30 all day",
    "Flight to Lisbon 17 october 7:40am @ Pearson Terminal 1", "Dinner tonight at Pizzeria Libretto",
    "Review the budget in 3 days afternoon", "Coffee with Alex 9-10am", "Night shift 10pm-6am",
    "Night shift friday 10pm-6am", "Book club every other week thursday 7:30pm",
    "Book club next friday 7:30pm",
    "Gym for 90 minutes tomorrow morning", "Pay rent monthly", "Party saturday from 8 to 11pm at Mia's place",
    "Doctor 12/10 at 2:30pm", "Dentist sep 30 8:30am for 45 min", "Birthday dinner 3 days from now",
    "Presentation the day after tomorrow at noon", "Plan the week", "Standup 9-5pm", "Meeting at 10 on 3/4",
    "Lunch @ The Keg, downtown tomorrow", "Retro every friday 4pm for 45 min",
    "Workshop 14:00-16:30 tomorrow at Main Office", "Conference oct 12 all day", "Call Mom in 3 days evening",
    "Mia’s birthday dec 3 every year", "Sam's birthday dec 3 yearly",
    "Team retro every other week friday 3pm",
]
NODE_SCRIPT = """
const {parse} = require(process.argv[1]);
const now = new Date(2026, 8, 24, 10, 17);
const pad = n => String(n).padStart(2, "0");
const iso = d => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
  + `T${pad(d.getHours())}:${pad(d.getMinutes())}`;
console.log(JSON.stringify(JSON.parse(process.argv[2]).map(text => {
  const e = parse(text, now);
  return [e.title, iso(e.start), iso(e.end), e.allDay, e.location, e.freq, e.interval, e.byday];
})));
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_website_demo_agrees():
    js_file = Path(__file__).parent.parent / "docs" / "quickadd.js"
    env = dict(os.environ, LC_ALL="en_US.UTF-8")  # month before day, as below
    out = subprocess.run(["node", "-e", NODE_SCRIPT, str(js_file), json.dumps(WEBSITE_PHRASES)],
                         capture_output=True, text=True, check=True, env=env).stdout
    for text, got in zip(WEBSITE_PHRASES, json.loads(out), strict=True):
        ev = parse(text, NOW, month_first=True)
        want = [ev.title, ev.start.strftime("%Y-%m-%dT%H:%M"), ev.end.strftime("%Y-%m-%dT%H:%M"),
                ev.all_day, ev.location, ev.freq, ev.interval, [d for d, _n in ev.byday]]
        assert got == want, text
