"""World clocks: the time in other places, for the sidebar and the day/week views."""

from __future__ import annotations

from datetime import datetime

from gi.repository import GLib

from . import ical
from .draw import fmt_time
from .i18n import _, ngettext


def city_name(tzid: str) -> str:
    """"America/New_York" → "New York"."""
    return ical.zone_label(tzid).rsplit("/", 1)[-1]


def time_in(tzid: str, when: datetime | None = None) -> datetime:
    """A local wall-clock time as wall-clock time in tzid."""
    return ical.to_zone(when or datetime.now(), tzid)


def abbreviation(tzid: str, when: datetime | None = None) -> str:
    """"EDT", "CEST"… (or an offset like "+0530" when there's no abbreviation)."""
    zone = GLib.TimeZone.new_identifier(tzid) or GLib.TimeZone.new_utc()
    stamp = int((when or datetime.now()).timestamp())
    return zone.get_abbreviation(zone.find_interval(GLib.TimeType.UNIVERSAL, stamp))


def offset_hours(tzid: str, when: datetime | None = None) -> float:
    """How far ahead tzid is of local time, in hours."""
    now = when or datetime.now()
    return round((time_in(tzid, now) - now).total_seconds() / 900) / 4


def describe(tzid: str, when: datetime | None = None) -> tuple[str, str]:
    """(time, "Tomorrow · +6 hours")."""
    now = when or datetime.now()
    there = time_in(tzid, now)
    days = (there.date() - now.date()).days
    day = {0: _("Today"), 1: _("Tomorrow"), -1: _("Yesterday")}.get(days, there.strftime("%a"))
    hours = offset_hours(tzid, now)
    if hours == 0:
        diff = _("Same time")
    else:
        amount = f"{'+' if hours > 0 else '−'}{abs(hours):g}"
        diff = ngettext("{amount} hour", "{amount} hours", max(1, round(abs(hours)))).format(
            amount=amount)
    return fmt_time(there), f"{day} · {diff}"


