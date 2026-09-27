"""Quick add: turn "Lunch with Sam tomorrow 1pm at Café Luna" into an event.

English phrasing only. Understands dates (today, tomorrow, friday, next friday,
sep 30, 30 september, 9/30, in 3 days), times (1pm, 13:00, noon, 1-2pm,
from 9 to 11am, morning), lengths (for 2 hours), "all day", repeats (every
day, every monday and thursday, weekly, every other week) and places
(@ Room 4, or "at" followed by a capitalized name).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from .models import Freq

WEEKDAYS = {"monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1, "wednesday": 2, "wed": 2,
            "thursday": 3, "thu": 3, "thur": 3, "thurs": 3, "friday": 4, "fri": 4,
            "saturday": 5, "sat": 5, "sunday": 6, "sun": 6}
MONTHS = {name: i + 1 for i, names in enumerate([
    ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
    ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sep", "sept"),
    ("october", "oct"), ("november", "nov"), ("december", "dec")]) for name in names}
PARTS_OF_DAY = {"morning": time(9), "afternoon": time(14), "evening": time(18),
                "tonight": time(19), "noon": time(12), "midnight": time(0)}

WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
MO = "|".join(sorted(MONTHS, key=len, reverse=True))
T = r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm|a|p)\b|\d{1,2}:\d{2}|noon|midnight)"
T_LOOSE = r"(?:\d{1,2}(?::\d{2})?\s*(?:am|pm|a|p)?|noon|midnight)"


@dataclass
class QuickEvent:
    title: str
    start: datetime
    end: datetime
    all_day: bool = False
    location: str = ""
    freq: str = Freq.NONE
    interval: int = 1
    byday: list[tuple[int, int]] = field(default_factory=list)
    date_given: bool = False
    time_given: bool = False


class _Text:
    """The input with recognized phrases blanked out as they're consumed."""

    def __init__(self, text: str):
        self.original = text
        # Lowercase char by char, keeping positions: "İ".lower() is two characters.
        self.lower = "".join(c.lower() if len(c.lower()) == 1 else c for c in text)
        self.free = [True] * len(text)

    def find(self, pattern: str):
        for m in re.finditer(pattern, self.lower):
            if all(self.free[m.start():m.end()]):
                return m
        return None

    def take(self, m) -> None:
        for i in range(m.start(), m.end()):
            self.free[i] = False

    def rest(self) -> str:
        return "".join(c if f else " " for c, f in zip(self.original, self.free))


def _parse_time(s: str, meridiem_hint: str | None = None) -> tuple[time, str | None] | None:
    s = s.strip().lower()
    if s in PARTS_OF_DAY:
        return PARTS_OF_DAY[s], None
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?", s)
    if not m:
        return None
    hour, minute = int(m.group(1)), int(m.group(2) or 0)
    meridiem = (m.group(3) or meridiem_hint or "")[:1]
    if hour > 23 or minute > 59:
        return None
    if meridiem == "p" and hour < 12:
        hour += 12
    elif meridiem == "a" and hour == 12:
        hour = 0
    return time(hour, minute), meridiem or None


def _next_weekday(today: date, weekday: int, skip_week: bool) -> date:
    days = (weekday - today.weekday()) % 7
    return today + timedelta(days=days + (7 if skip_week else 0))


def _with_year(month: int, day: int, year: int | None, today: date) -> date | None:
    try:
        d = date(year or today.year, month, day)
    except ValueError:
        return None
    if year is None and d < today:  # "jan 5" in December means next January
        d = d.replace(year=d.year + 1)
    return d


def parse(text: str, now: datetime, default_duration: timedelta = timedelta(hours=1),
          month_first: bool = True, default_day: date | None = None,
          default_time: time | None = None) -> QuickEvent:
    """default_day/default_time: the day or time slot focused in the calendar, used
    when the text names no date or time."""
    today = now.date()
    t = _Text(text)
    day: date | None = None
    start_time: time | None = None
    end_time: time | None = None
    duration: timedelta | None = None
    all_day = False
    ev = QuickEvent(title="", start=now, end=now)

    # Repeats
    if m := t.find(rf"\bevery\s+((?:(?:{WD})s?)(?:\s*(?:,|and)\s*(?:{WD})s?)*)\b"):
        days = re.findall(WD, m.group(1))
        ev.freq, ev.byday = Freq.WEEKLY, sorted({(WEEKDAYS[d], 0) for d in days})
        t.take(m)
    elif m := t.find(r"\bevery\s+(other\s+)?(day|weekday|week|month|year)\b|\b(daily|weekly|monthly|yearly|annually)\b"):
        unit = m.group(2) or m.group(3)
        ev.freq = {"day": Freq.DAILY, "daily": Freq.DAILY, "weekday": Freq.WEEKLY,
                   "week": Freq.WEEKLY, "weekly": Freq.WEEKLY, "month": Freq.MONTHLY,
                   "monthly": Freq.MONTHLY, "year": Freq.YEARLY, "yearly": Freq.YEARLY,
                   "annually": Freq.YEARLY}[unit]
        if unit == "weekday":
            ev.byday = [(d, 0) for d in range(5)]
        ev.interval = 2 if m.group(1) else 1
        t.take(m)

    # Length and all-day
    if m := t.find(r"\ball[- ]day\b"):
        all_day = True
        t.take(m)
    if m := t.find(r"\bfor\s+(half an hour|an hour|(\d+(?:\.\d+)?)\s*(h|hrs?|hours?|m|mins?|minutes?))\b"):
        if m.group(1) == "half an hour":
            duration = timedelta(minutes=30)
        elif m.group(1) == "an hour":
            duration = timedelta(hours=1)
        else:
            amount = float(m.group(2))
            duration = timedelta(hours=amount) if m.group(3).startswith("h") else timedelta(minutes=amount)
        t.take(m)

    # Times: a range first, then a single time, then parts of the day
    if m := t.find(rf"\b(?:from\s+)?({T_LOOSE})\s*(?:-|–|to|until|till)\s*({T})"):
        second = _parse_time(m.group(2))
        first = _parse_time(m.group(1))
        if first and second:
            start_time, end_time = first[0], second[0]
            if first[1] is None and second[1]:
                # "1-2pm" is 1 PM, but "9-5pm" is 9 AM: borrow the meridiem if it fits.
                hinted = _parse_time(m.group(1), second[1])[0]
                if hinted <= end_time:
                    start_time = hinted
            t.take(m)
    if start_time is None and (m := t.find(rf"\b(?:at\s+)?({T})")):
        parsed = _parse_time(m.group(1))
        if parsed:
            start_time = parsed[0]
            t.take(m)
    if start_time is None and (m := t.find(r"\bat\s+(\d{1,2})\b(?!\s*[/.:])")):
        hour = int(m.group(1))
        if 1 <= hour <= 12:
            start_time = time(hour + 12 if hour < 8 else hour)  # "at 3" means 3 PM
            t.take(m)
    if start_time is None and (m := t.find(r"\b(?:in the\s+|this\s+)?(morning|afternoon|evening|tonight)\b")):
        start_time = PARTS_OF_DAY[m.group(1)]
        if m.group(1) == "tonight":
            day = today
        t.take(m)

    # Dates
    if m := t.find(r"\b(?:the\s+)?day after tomorrow\b"):
        day = today + timedelta(days=2)
        t.take(m)
    elif m := t.find(r"\b(today|tomorrow|tmrw|tmr)\b"):
        day = today if m.group(1) == "today" else today + timedelta(days=1)
        t.take(m)
    elif m := t.find(rf"\b(?:on\s+)?(?:(next|this)\s+)?({WD})\b"):
        day = _next_weekday(today, WEEKDAYS[m.group(2)], m.group(1) == "next")
        t.take(m)
    elif m := t.find(rf"\b(?:on\s+)?({MO})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(\d{{4}}))?\b"):
        day = _with_year(MONTHS[m.group(1)], int(m.group(2)), int(m.group(3) or 0) or None, today)
        t.take(m)
    elif m := t.find(rf"\b(?:on\s+)?(?:the\s+)?(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?({MO})\b(?:,?\s+(\d{{4}}))?"):
        day = _with_year(MONTHS[m.group(2)], int(m.group(1)), int(m.group(3) or 0) or None, today)
        t.take(m)
    elif m := t.find(r"\b(?:on\s+)?(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b"):
        a, b = int(m.group(1)), int(m.group(2))
        month, dom = (a, b) if month_first else (b, a)
        year = int(m.group(3)) if m.group(3) else None
        if year is not None and year < 100:
            year += 2000
        day = _with_year(month, dom, year, today)
        t.take(m)
    elif m := t.find(r"\bin\s+(\d+|a|one|two|three)\s+(day|days|week|weeks)\b"):
        n = {"a": 1, "one": 1, "two": 2, "three": 3}.get(m.group(1)) or int(m.group(1))
        day = today + (timedelta(weeks=n) if m.group(2).startswith("week") else timedelta(days=n))
        t.take(m)
    elif m := t.find(r"\bnext week\b"):
        day = today + timedelta(days=7 - today.weekday())
        t.take(m)

    # Place: "@ Room 4" anywhere, or "at" followed by a capitalized name. It runs
    # until a comma or text already recognized (a date or time).
    for m in re.finditer(r"@\s*|\bat\s+(?=\S)", t.lower):
        name_start = m.end()
        if not all(t.free[m.start():m.end()]) or name_start >= len(t.original):
            continue
        if not m.group().startswith("@") and not t.original[name_start].isupper():
            continue
        end = name_start
        while end < len(t.original) and t.free[end] and t.original[end] != ",":
            end += 1
        ev.location = t.original[name_start:end].strip()
        t.take(_Span(m.start(), end))
        break

    title = re.sub(r"\s+", " ", t.rest()).strip(" ,.-–")
    title = re.sub(r"\b(on|at|from|for|the)$", "", title, flags=re.IGNORECASE).strip(" ,.-–")
    ev.title = title

    # Put it together
    ev.date_given, ev.time_given = day is not None, start_time is not None
    if start_time is None and not all_day and day is None:
        if default_time is not None:  # the focused time slot
            start_time, day = default_time, default_day
        elif default_day and default_day != today:  # a focused day: all-day there
            day, all_day = default_day, True
    day = day or default_day or today
    if start_time is None and not all_day and day == today and default_day in (None, today):
        # Nothing given: the next full hour today.
        ev.start = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        ev.end = ev.start + (duration or default_duration)
    elif start_time is None or all_day:
        ev.all_day = True
        ev.start = datetime.combine(day, time())
        days = max(1, round((duration or timedelta(days=1)) / timedelta(days=1)))
        ev.end = ev.start + timedelta(days=days)
    else:
        ev.start = datetime.combine(day, start_time)
        if end_time is not None:
            ev.end = datetime.combine(day, end_time)
            if ev.end <= ev.start:
                ev.end += timedelta(days=1)  # "10pm-1am"
        else:
            ev.end = ev.start + (duration or default_duration)
    if ev.freq == Freq.WEEKLY and not ev.byday:
        ev.byday = [(ev.start.weekday(), 0)]
    if ev.byday and ev.freq == Freq.WEEKLY:
        # "every tuesday": start on the first matching day, or the start would be an
        # extra occurrence on a day the rule doesn't include.
        first = min(_next_weekday(ev.start.date(), d, False) for d, _n in ev.byday)
        shift = datetime.combine(first, ev.start.time()) - ev.start
        ev.start, ev.end = ev.start + shift, ev.end + shift
    return ev


class _Span:
    def __init__(self, start: int, end: int):
        self._start, self._end = start, end

    def start(self) -> int:
        return self._start

    def end(self) -> int:
        return self._end
