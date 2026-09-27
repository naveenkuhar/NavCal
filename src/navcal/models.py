# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Core data model: events, repeat rules and occurrences. No GTK or EDS here."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from typing import Any, Iterator

from .i18n import N_

# The GNOME/Adwaita accent palette.
EVENT_COLORS = [  # names are translated where shown
    (N_("Blue"), "#3584e4"),
    (N_("Teal"), "#2190a4"),
    (N_("Green"), "#3a944a"),
    (N_("Yellow"), "#c88800"),
    (N_("Orange"), "#ed5b00"),
    (N_("Red"), "#e62d42"),
    (N_("Pink"), "#d56199"),
    (N_("Purple"), "#9141ac"),
    (N_("Slate"), "#6f8396"),
]
DEFAULT_COLOR = EVENT_COLORS[0][1]

WEEKDAY_CODES = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]  # index = Python weekday


class Freq:
    NONE = "none"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    ALL = (NONE, DAILY, WEEKLY, MONTHLY, YEARLY)


class Scope:
    """Which occurrences of a recurring event a change applies to."""

    THIS = "this"
    FUTURE = "future"  # this and following
    ALL = "all"


@dataclass
class Event:
    title: str
    start: datetime  # local wall-clock time (naive); all-day events use midnight
    end: datetime  # exclusive; all-day events end at midnight after the last day
    all_day: bool = False
    location: str = ""
    notes: str = ""
    color: str | None = None  # per-event override; None = use the calendar's color
    # Repeat rule (RFC 5545 subset we can edit).
    freq: str = Freq.NONE
    interval: int = 1
    byday: list[tuple[int, int]] = field(default_factory=list)  # (weekday Mon=0, ordinal; 0=every)
    bymonthday: int | None = None
    count: int | None = None
    until: date | None = None
    rrule_extra: str = ""  # RRULE parts we don't edit, kept verbatim
    exdates: set[date] = field(default_factory=set)  # only used when importing old data
    reminders: list[int] = field(default_factory=list)  # minutes before start
    url: str = ""  # the event's web page (iCalendar URL)
    travel_minutes: int = 0  # time to get there, shown before the event (X-APPLE-TRAVEL-DURATION)
    attachments: list[str] = field(default_factory=list)  # links and files (iCalendar ATTACH URIs)
    tzid: str | None = None  # event time zone; None = local time
    geo: tuple[float, float] | None = None  # where the location is (iCalendar GEO): latitude, longitude
    place: str = ""  # the location's OpenStreetMap id ("N123", "W45"), when picked from a search
    # Where it lives.
    calendar: str | None = None  # EDS source UID
    uid: str | None = None  # iCalendar UID
    readonly: bool = False
    raw: Any = field(default=None, repr=False, compare=False)  # original iCalendar component
    id: int | None = None  # legacy SQLite id (migration only)

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    @property
    def is_recurring(self) -> bool:
        return self.freq != Freq.NONE

    def clone(self, **changes) -> Event:
        """A detached copy that can be saved as a new event."""
        changes.setdefault("exdates", set(self.exdates))
        changes.setdefault("byday", list(self.byday))
        changes.setdefault("reminders", list(self.reminders))
        changes.setdefault("attachments", list(self.attachments))
        return replace(self, id=None, uid=None, raw=None, readonly=False, **changes)

    def same_rule(self, other: Event) -> bool:
        return (self.freq, self.interval, sorted(self.byday), self.bymonthday, self.count,
                self.until, self.rrule_extra) == (
            other.freq, other.interval, sorted(other.byday), other.bymonthday, other.count,
            other.until, other.rrule_extra)


@dataclass
class Task:
    title: str
    due: date | datetime | None = None  # a date, or a date and time
    done: bool = False
    notes: str = ""
    list: str | None = None  # EDS task list source UID
    new_list: str | None = None  # set to move the task to another list when saving
    uid: str | None = None
    readonly: bool = False
    raw: Any = field(default=None, repr=False, compare=False)

    @property
    def due_date(self) -> date | None:
        return self.due.date() if isinstance(self.due, datetime) else self.due


@dataclass(eq=False)
class Occurrence:
    """One concrete instance of an event (a recurring event has many)."""

    event: Event
    start: datetime
    end: datetime
    rid: Any = None  # recurrence id of this instance (ICalGLib.Time), None if not recurring
    color: str = DEFAULT_COLOR  # resolved display color

    @property
    def key(self) -> tuple:
        return (self.event.calendar, self.event.uid, self.event.id, self.start)

    @property
    def is_banner(self) -> bool:
        """Shown as a bar across days rather than as a timed block."""
        return self.event.all_day or self.end - self.start >= timedelta(days=1)

    @property
    def is_instance(self) -> bool:
        """Part of a recurring series (including individually edited ones)."""
        return self.rid is not None


def days_covered(start: datetime, end: datetime) -> Iterator[date]:
    """Calendar days touched by [start, end). An end at midnight doesn't count."""
    last = (end - timedelta(microseconds=1)).date() if end > start else start.date()
    d = start.date()
    while d <= last:
        yield d
        d += timedelta(days=1)


def overlaps(start: datetime, end: datetime, range_start: datetime, range_end: datetime) -> bool:
    # Zero-length events count when their instant falls inside the range.
    return start < range_end and (end > range_start or start >= range_start)


def sort_occurrences(occs: list[Occurrence]) -> list[Occurrence]:
    occs.sort(key=lambda o: (not o.is_banner, o.start, -(o.end - o.start).total_seconds(),
                             o.event.title))
    return occs
