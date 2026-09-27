"""Conversion between Navcal events and iCalendar (libical) components.

Unknown properties (attendees, organizer, custom X- fields, unsupported RRULE
parts) are preserved: edits start from a clone of the original component.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable

import gi

gi.require_version("ICalGLib", "3.0")
gi.require_version("ECal", "2.0")
from gi.repository import ECal, GLib, ICalGLib as I  # noqa: E402

from .models import WEEKDAY_CODES, Event, Freq, Task  # noqa: E402

Resolver = Callable[[str], "I.Timezone | None"]


# -- time zones --------------------------------------------------------------


def local_tzid() -> str:
    return GLib.TimeZone.new_local().get_identifier() or "UTC"


def builtin_zone(tzid: str | None) -> I.Timezone | None:
    if not tzid:
        return None
    if tzid == "UTC":
        return I.Timezone.get_utc_timezone()
    return (I.Timezone.get_builtin_timezone(tzid)
            or I.Timezone.get_builtin_timezone_from_tzid(tzid))


def local_zone() -> I.Timezone:
    return builtin_zone(local_tzid()) or I.Timezone.get_utc_timezone()


def all_tzids() -> list[str]:
    zones = I.Timezone.get_builtin_timezones()
    names = {I.Timezone.array_element_at(zones, i).get_location() for i in range(zones.size())}
    return sorted(n for n in names if n)


def zone_label(tzid: str) -> str:
    return tzid.replace("/freeassociation.sourceforge.net/", "").replace("_", " ")


def plain_tzid(tzid: str | None) -> str | None:
    return tzid.replace("/freeassociation.sourceforge.net/", "") if tzid else tzid


# -- times ---------------------------------------------------------------------


def to_local(t: I.Time, zone: I.Timezone | None = None) -> datetime:
    """An iCalendar time as a naive local datetime."""
    if t.is_date():
        return datetime(t.get_year(), t.get_month(), t.get_day())
    zone = zone or t.get_timezone()
    if zone is None and not t.is_utc():  # floating time: already local
        return datetime(t.get_year(), t.get_month(), t.get_day(),
                        t.get_hour(), t.get_minute(), t.get_second())
    return datetime.fromtimestamp(t.as_timet_with_zone(zone or I.Timezone.get_utc_timezone()))


def to_zone(dt: datetime, tzid: str | None) -> datetime:
    """Local wall time -> wall time in tzid (both naive)."""
    zone = builtin_zone(tzid)
    if not zone or tzid == local_tzid():
        return dt
    t = I.Time.new_from_timet_with_zone(int(dt.timestamp()), False, zone)
    return datetime(t.get_year(), t.get_month(), t.get_day(), t.get_hour(), t.get_minute())


def from_zone(dt: datetime, tzid: str | None) -> datetime:
    """Wall time in tzid -> local wall time (both naive)."""
    zone = builtin_zone(tzid)
    if not zone or tzid == local_tzid():
        return dt
    return to_local(make_time(dt, zone))


def make_time(dt: datetime, zone: I.Timezone | None) -> I.Time:
    t = I.Time.new_null_time()
    t.set_date(dt.year, dt.month, dt.day)
    t.set_time(dt.hour, dt.minute, 0)
    t.set_is_date(False)
    if zone:
        t.set_timezone(zone)
    return t


def make_date(d: date) -> I.Time:
    t = I.Time.new_null_time()
    t.set_date(d.year, d.month, d.day)
    t.set_is_date(True)
    return t


def timet_range(start: datetime, end: datetime) -> tuple[I.Time, I.Time]:
    zone = local_zone()
    return (I.Time.new_from_timet_with_zone(int(start.timestamp()), False, zone),
            I.Time.new_from_timet_with_zone(int(end.timestamp()), False, zone))


def prop_time(comp: I.Component, kind, resolve: Resolver) -> tuple[I.Time | None, str | None]:
    """A date/time property with its time zone attached, and the TZID."""
    prop = comp.get_first_property(kind)
    if prop is None:
        return None, None
    t = prop.get_datetime_with_component(comp) if hasattr(prop, "get_datetime_with_component") \
        else prop.get_value().get_datetime()
    param = prop.get_first_parameter(I.ParameterKind.TZID_PARAMETER)
    tzid = param.get_tzid() if param else ("UTC" if t.is_utc() else None)
    if tzid and tzid != "UTC" and t.get_timezone() is None:
        zone = resolve(tzid)
        if zone:
            t = t.clone()
            t.set_timezone(zone)
    return t, tzid


def time_key(t: I.Time) -> int:
    """Comparable instant for recurrence ids. Floating times are local times."""
    if t.is_date():
        return int(datetime(t.get_year(), t.get_month(), t.get_day()).timestamp())
    zone = t.get_timezone() or (I.Timezone.get_utc_timezone() if t.is_utc() else local_zone())
    return t.as_timet_with_zone(zone)


# -- component -> Event ----------------------------------------------------------


def _parse_rrule(value: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in value.split(";") if "=" in part)


def _parse_byday(value: str) -> list[tuple[int, int]]:
    out = []
    for item in value.split(","):
        item = item.strip()
        code, n = item[-2:], item[:-2]
        if code in WEEKDAY_CODES:
            out.append((WEEKDAY_CODES.index(code), int(n) if n not in ("", "+") else 0))
    return out


def event_from_component(comp: I.Component, resolve: Resolver, calendar: str | None = None,
                         readonly: bool = False) -> Event:
    start_t, tzid = prop_time(comp, I.PropertyKind.DTSTART_PROPERTY, resolve)
    end_t, _end_tzid = prop_time(comp, I.PropertyKind.DTEND_PROPERTY, resolve)
    all_day = bool(start_t and start_t.is_date())
    start = to_local(start_t) if start_t else datetime.now().replace(second=0, microsecond=0)
    if end_t is not None:
        end = to_local(end_t)
    elif comp.get_first_property(I.PropertyKind.DURATION_PROPERTY):
        end = start + timedelta(seconds=comp.get_duration().as_int())
    else:
        end = start + (timedelta(days=1) if all_day else timedelta(0))

    ev = Event(title=comp.get_summary() or "", start=start, end=max(end, start), all_day=all_day,
               location=comp.get_location() or "", notes=comp.get_description() or "",
               tzid=None if all_day else plain_tzid(tzid), calendar=calendar,
               uid=comp.get_uid(), readonly=readonly, raw=comp)

    color = comp.get_first_property(I.PropertyKind.COLOR_PROPERTY)
    if color:
        ev.color = color.get_color()
    url = comp.get_first_property(I.PropertyKind.URL_PROPERTY)
    if url:
        ev.url = url.get_url() or ""
    ev.attachments = _attachment_uris(comp)
    geo = comp.get_first_property(I.PropertyKind.GEO_PROPERTY)
    if geo:
        g = geo.get_geo()
        ev.geo = (g.get_lat(), g.get_lon())
    place = _x_property(comp, PLACE_PROPERTY)
    if place:
        ev.place = place.get_value_as_string() or ""
    travel = _x_property(comp, TRAVEL_PROPERTY)
    if travel:
        try:
            ev.travel_minutes = max(0, I.Duration.new_from_string(travel.get_value_as_string()).as_int() // 60)
        except (TypeError, ValueError):
            pass

    rrule = comp.get_first_property(I.PropertyKind.RRULE_PROPERTY)
    if rrule:
        parts = _parse_rrule(rrule.get_value_as_string())
        freq = parts.pop("FREQ", "").lower()
        ev.freq = freq if freq in Freq.ALL else Freq.NONE
        ev.interval = int(parts.pop("INTERVAL", "1") or 1)
        if "BYDAY" in parts:
            ev.byday = _parse_byday(parts.pop("BYDAY"))
        if "BYMONTHDAY" in parts and "," not in parts["BYMONTHDAY"]:
            ev.bymonthday = int(parts.pop("BYMONTHDAY"))
        if "COUNT" in parts:
            ev.count = int(parts.pop("COUNT"))
        if "UNTIL" in parts:
            until = I.Time.new_from_string(parts.pop("UNTIL"))
            ev.until = to_local(until).date()
        parts.pop("WKST", None)
        ev.rrule_extra = ";".join(f"{k}={v}" for k, v in parts.items())
        if ev.freq == Freq.NONE:  # e.g. FREQ=HOURLY: keep, but we can't edit it
            ev.rrule_extra = rrule.get_value_as_string()

    ev.reminders = sorted(m for m in (_alarm_minutes(a) for a in _alarms(comp)) if m is not None)
    return ev


TRAVEL_PROPERTY = "X-APPLE-TRAVEL-DURATION"  # Apple's name, so travel time syncs with iPhones
PLACE_PROPERTY = "X-NAVCAL-PLACE"  # the location's OpenStreetMap id, for its opening hours


def _x_property(comp: I.Component, name: str) -> I.Property | None:
    prop = comp.get_first_property(I.PropertyKind.X_PROPERTY)
    while prop:
        if prop.get_x_name() == name:
            return prop
        prop = comp.get_next_property(I.PropertyKind.X_PROPERTY)
    return None


def _set_place(comp: I.Component, geo: tuple[float, float] | None, place: str) -> None:
    _remove_all(comp, I.PropertyKind.GEO_PROPERTY)
    while prop := _x_property(comp, PLACE_PROPERTY):
        comp.remove_property(prop)
    if geo:
        comp.add_property(I.Property.new_geo(I.Geo.new(*geo)))
    if place:
        prop = I.Property.new_x(place)
        prop.set_x_name(PLACE_PROPERTY)
        comp.add_property(prop)


def _set_travel(comp: I.Component, minutes: int) -> None:
    while prop := _x_property(comp, TRAVEL_PROPERTY):
        comp.remove_property(prop)
    if minutes > 0:
        prop = I.Property.new_x(I.Duration.new_from_int(minutes * 60).as_ical_string())
        prop.set_x_name(TRAVEL_PROPERTY)
        prop.add_parameter(I.Parameter.new_value(I.ParameterValue.DURATION))
        comp.add_property(prop)


def _attach_props(comp: I.Component) -> list[I.Property]:
    """ATTACH properties that are links (not embedded file data)."""
    out = []
    prop = comp.get_first_property(I.PropertyKind.ATTACH_PROPERTY)
    while prop:
        attach = prop.get_attach()
        if attach and attach.get_is_url():
            out.append(prop)
        prop = comp.get_next_property(I.PropertyKind.ATTACH_PROPERTY)
    return out


def _attachment_uris(comp: I.Component) -> list[str]:
    return [p.get_attach().get_url() for p in _attach_props(comp)]


def _alarms(comp: I.Component) -> list[I.Component]:
    out = []
    alarm = comp.get_first_component(I.ComponentKind.VALARM_COMPONENT)
    while alarm:
        out.append(alarm)
        alarm = comp.get_next_component(I.ComponentKind.VALARM_COMPONENT)
    return out


def _alarm_minutes(alarm: I.Component) -> int | None:
    prop = alarm.get_first_property(I.PropertyKind.TRIGGER_PROPERTY)
    if prop is None:
        return None
    related = prop.get_first_parameter(I.ParameterKind.RELATED_PARAMETER)
    if related and related.get_related() == I.ParameterRelated.END:
        return None
    trigger = prop.get_trigger()
    if trigger.is_bad_trigger() or not trigger.get_time().is_null_time():
        return None  # absolute trigger: kept as-is, but not shown as "minutes before"
    return max(0, -trigger.get_duration().as_int() // 60)


# -- Event -> component ----------------------------------------------------------


def _set_text(comp: I.Component, kind, value: str, new_prop) -> None:
    prop = comp.get_first_property(kind)
    if prop:
        comp.remove_property(prop)
    if value:
        comp.add_property(new_prop(value))


def _all(comp: I.Component, kind) -> list[I.Property]:
    props, prop = [], comp.get_first_property(kind)
    while prop:
        props.append(prop)
        prop = comp.get_next_property(kind)
    return props


def _remove_all(comp: I.Component, kind) -> None:
    prop = comp.get_first_property(kind)
    while prop:
        comp.remove_property(prop)
        prop = comp.get_first_property(kind)


def rrule_string(ev: Event) -> str | None:
    if ev.freq == Freq.NONE:
        return ev.rrule_extra or None
    parts = [f"FREQ={ev.freq.upper()}"]
    if ev.interval > 1:
        parts.append(f"INTERVAL={ev.interval}")
    if ev.byday:
        parts.append("BYDAY=" + ",".join(f"{n if n else ''}{WEEKDAY_CODES[d]}"
                                         for d, n in sorted(ev.byday, key=lambda x: (x[0], x[1]))))
    if ev.bymonthday is not None:
        parts.append(f"BYMONTHDAY={ev.bymonthday}")
    if ev.count:
        parts.append(f"COUNT={ev.count}")
    elif ev.until:
        if ev.all_day:
            parts.append(f"UNTIL={ev.until:%Y%m%d}")
        else:
            # End of that day in local time, as UTC (required when DTSTART has a zone).
            end_of_day = datetime.combine(ev.until, time(23, 59, 59))
            parts.append(f"UNTIL={end_of_day.astimezone(timezone.utc):%Y%m%dT%H%M%SZ}")
    if ev.rrule_extra:
        parts.append(ev.rrule_extra)
    return ";".join(parts)


def _add_alarm(comp: I.Component, minutes: int, title: str) -> None:
    alarm = I.Component.new_valarm()
    alarm.add_property(I.Property.new_action(I.PropertyAction.DISPLAY))
    trigger = I.Trigger.new_from_int(-minutes * 60)  # relative to the start
    alarm.add_property(I.Property.new_trigger(trigger))
    alarm.add_property(I.Property.new_description(title or "Reminder"))
    comp.add_component(alarm)


def component_from_event(ev: Event, base: I.Component | None = None,
                         zone_for: Resolver = builtin_zone) -> I.Component:
    """Build a VEVENT for ev, starting from base (the original) if given."""
    comp = base.clone() if base is not None else I.Component.new_vevent()
    if not comp.get_uid():
        comp.set_uid(ev.uid or str(uuid.uuid4()))
    comp.set_summary(ev.title)
    _set_text(comp, I.PropertyKind.LOCATION_PROPERTY, ev.location, I.Property.new_location)
    _set_text(comp, I.PropertyKind.DESCRIPTION_PROPERTY, ev.notes, I.Property.new_description)
    _set_text(comp, I.PropertyKind.COLOR_PROPERTY, ev.color or "", I.Property.new_color)
    _set_text(comp, I.PropertyKind.URL_PROPERTY, ev.url, I.Property.new_url)
    _set_travel(comp, ev.travel_minutes)
    _set_place(comp, ev.geo, ev.place)
    if _attachment_uris(comp) != ev.attachments:
        for prop in _attach_props(comp):  # embedded attachments are left alone
            comp.remove_property(prop)
        for uri in ev.attachments:
            comp.add_property(I.Property.new_attach(I.Attach.new_from_url(uri)))

    _remove_all(comp, I.PropertyKind.DTSTART_PROPERTY)
    _remove_all(comp, I.PropertyKind.DTEND_PROPERTY)
    _remove_all(comp, I.PropertyKind.DURATION_PROPERTY)
    if ev.all_day:
        comp.set_dtstart(make_date(ev.start.date()))
        comp.set_dtend(make_date(max(ev.end.date(), ev.start.date() + timedelta(days=1))))
    else:
        tzid = ev.tzid or local_tzid()
        zone = zone_for(tzid) or builtin_zone(tzid)
        comp.set_dtstart(make_time(to_zone(ev.start, tzid), zone))
        comp.set_dtend(make_time(to_zone(ev.end, tzid), zone))

    base_ev = None
    if base is not None:
        base_ev = event_from_component(base, lambda _t: None)
    if base_ev is None or not base_ev.same_rule(ev) or ev.freq == Freq.NONE:
        _remove_all(comp, I.PropertyKind.RRULE_PROPERTY)
        rule = rrule_string(ev)
        if rule:
            comp.add_property(I.Property.new_rrule(I.Recurrence.new_from_string(rule)))
    for d in sorted(ev.exdates):
        if ev.all_day:
            comp.add_property(I.Property.new_exdate(make_date(d)))
        else:
            tzid = ev.tzid or local_tzid()
            when = to_zone(datetime.combine(d, ev.start.time()), tzid)
            prop = I.Property.new_exdate(make_time(when, None))
            prop.add_parameter(I.Parameter.new_tzid(tzid))
            comp.add_property(prop)

    if base_ev is None or sorted(base_ev.reminders) != sorted(ev.reminders):
        for alarm in _alarms(comp):
            comp.remove_component(alarm)
        for minutes in sorted(set(ev.reminders)):
            _add_alarm(comp, minutes, ev.title)
    return comp


def strip_recurrence(comp: I.Component) -> None:
    for kind in (I.PropertyKind.RRULE_PROPERTY, I.PropertyKind.RDATE_PROPERTY,
                 I.PropertyKind.EXDATE_PROPERTY, I.PropertyKind.EXRULE_PROPERTY):
        _remove_all(comp, kind)


def set_recurrence_id(comp: I.Component, rid: I.Time) -> None:
    """Set RECURRENCE-ID with a proper TZID parameter (libical's setter drops it)."""
    _remove_all(comp, I.PropertyKind.RECURRENCEID_PROPERTY)
    ecomp = ECal.Component.new_from_icalcomponent(comp)
    zone = rid.get_timezone()
    tzid = None if rid.is_date() or rid.is_utc() or zone is None else plain_tzid(zone.get_tzid())
    ecomp.set_recurid(ECal.ComponentRange.new(ECal.ComponentRangeKind.SINGLE,
                                              ECal.ComponentDateTime.new(rid, tzid)))


def rid_string(rid: I.Time) -> str:
    return rid.as_ical_string()


FAST_FORWARD_AFTER = timedelta(days=90)  # skip ahead in series that began longer ago
DAILY_OR_LONGER = (I.RecurrenceFrequency.DAILY_RECURRENCE, I.RecurrenceFrequency.WEEKLY_RECURRENCE,
                   I.RecurrenceFrequency.MONTHLY_RECURRENCE, I.RecurrenceFrequency.YEARLY_RECURRENCE)


def _wall(t: I.Time) -> datetime:
    return datetime(t.get_year(), t.get_month(), t.get_day(), t.get_hour(), t.get_minute(), t.get_second())


def _zone_of(prop: I.Property, t: I.Time, resolve: Resolver) -> I.Timezone | None:
    param = prop.get_first_parameter(I.ParameterKind.TZID_PARAMETER)
    if param:
        return resolve(param.get_tzid())
    return I.Timezone.get_utc_timezone() if t.is_utc() else local_zone()


def fast_forward(comp: I.Component, before: datetime, resolve: Resolver) -> I.Component:
    """comp, or a copy that starts at one of its instances shortly before `before`.

    Expanding steps through every instance since the first, which is slow for a
    series that began years ago. Moving the start to a later instance gives the
    same instances from there on, as long as the rule doesn't count instances
    (COUNT) and the event is an ordinary one (one RRULE, no EXRULE).
    """
    rrules = _all(comp, I.PropertyKind.RRULE_PROPERTY)
    start_prop = comp.get_first_property(I.PropertyKind.DTSTART_PROPERTY)
    if len(rrules) != 1 or start_prop is None or comp.get_first_property(I.PropertyKind.EXRULE_PROPERTY):
        return comp
    rule = rrules[0].get_rrule()
    if rule.get_count() or rule.get_freq() not in DAILY_OR_LONGER:
        return comp
    dtstart = start_prop.get_dtstart()
    end_prop = comp.get_first_property(I.PropertyKind.DTEND_PROPERTY)
    dtend = end_prop.get_dtend() if end_prop else None
    timed_end = dtend is not None and not dtend.is_date()
    if timed_end:
        # Instances last as long as the event in absolute time; keep that exactly.
        if (end_prop.get_first_parameter(I.ParameterKind.TZID_PARAMETER) is None) != (
                start_prop.get_first_parameter(I.ParameterKind.TZID_PARAMETER) is None):
            return comp
        start_zone, end_zone = _zone_of(start_prop, dtstart, resolve), _zone_of(end_prop, dtend, resolve)
        if start_zone is None or end_zone is None or start_zone.get_tzid() != end_zone.get_tzid():
            return comp
        seconds = dtend.as_timet_with_zone(end_zone) - dtstart.as_timet_with_zone(start_zone)
        length = timedelta(seconds=max(seconds, 0))
    else:
        length = _wall(dtend) - _wall(dtstart) if dtend else timedelta(0)
    # Two days' margin covers any difference between the event's and the local time zone.
    anchor = before - max(length, timedelta(0)) - timedelta(days=2)
    if _wall(dtstart) > anchor - FAST_FORWARD_AFTER:
        return comp
    # Near a daylight saving change the copy's start and end could be an hour more
    # or less apart than the event's, so then start from an earlier instance.
    for back in (0, 8, 40, 200, 400):
        at = anchor - timedelta(days=back)
        recur = I.RecurIterator.new(rule, dtstart)
        if not recur.set_start(make_date(at.date()) if dtstart.is_date() else make_time(at, None)):
            return comp
        first = recur.next()
        if first is None or first.is_null_time():
            return comp  # the rule ends before then (only RDATEs, if any, remain)
        shift = _wall(first) - _wall(dtstart)
        start = dtstart.clone()
        start.adjust(shift.days, 0, 0, shift.seconds)
        end = None
        if dtend is not None:
            end = dtend.clone()
            end.adjust(shift.days, 0, 0, shift.seconds)
            if timed_end and (end.as_timet_with_zone(end_zone) - start.as_timet_with_zone(start_zone)
                              != length.total_seconds()):
                continue
        copy = comp.clone()
        copy.get_first_property(I.PropertyKind.DTSTART_PROPERTY).set_dtstart(start)
        if end is not None:
            copy.get_first_property(I.PropertyKind.DTEND_PROPERTY).set_dtend(end)
        return copy
    return comp


def expand(comp: I.Component, start: datetime, end: datetime, resolve: Resolver
           ) -> list[tuple[datetime, datetime, I.Time]]:
    """Instances of a recurring component overlapping [start, end): (start, end, rid)."""
    out = []

    def on_instance(_icomp, s, e, *_rest):
        out.append((to_local(s), to_local(e), s.clone()))
        return True

    s_t, e_t = timet_range(start, end)
    ECal.recur_generate_instances_sync(comp, s_t, e_t, on_instance, None,
                                       lambda tzid, *_r: resolve(tzid), None, local_zone(), None)
    return out


def calendar_text(components: list[I.Component], zones: list[I.Timezone]) -> str:
    """A complete VCALENDAR (for export)."""
    cal = I.Component.new_vcalendar()
    cal.add_property(I.Property.new_version("2.0"))
    cal.add_property(I.Property.new_prodid("-//Navcal//Navcal//EN"))
    seen = set()
    for zone in zones:
        if zone and zone.get_tzid() not in seen and zone.get_tzid() != "UTC":
            seen.add(zone.get_tzid())
            vtz = zone.get_component()
            if vtz:
                cal.add_component(vtz.clone())
    for comp in components:
        cal.add_component(comp.clone())
    return cal.as_ical_string()


def parse_calendar(text: str) -> tuple[list[I.Component], list[I.Component]]:
    """Events and VTIMEZONEs from .ics text."""
    root = I.Component.new_from_string(text)
    if root is None:
        raise ValueError("Not an iCalendar file")
    if root.isa() == I.ComponentKind.VEVENT_COMPONENT:
        return [root], []
    events, zones = [], []
    for kind, bucket in ((I.ComponentKind.VEVENT_COMPONENT, events),
                         (I.ComponentKind.VTIMEZONE_COMPONENT, zones)):
        c = root.get_first_component(kind)
        while c:
            bucket.append(c.clone())
            c = root.get_next_component(kind)
    return events, zones


# -- tasks (VTODO) ---------------------------------------------------------------


def task_from_component(comp: I.Component, resolve: Resolver, task_list: str | None = None,
                        readonly: bool = False) -> Task:
    due_t, _tzid = prop_time(comp, I.PropertyKind.DUE_PROPERTY, resolve)
    due = None
    if due_t is not None and not due_t.is_null_time():
        due = to_local(due_t).date() if due_t.is_date() else to_local(due_t)
    status = comp.get_first_property(I.PropertyKind.STATUS_PROPERTY)
    done = bool(status and status.get_status() == I.PropertyStatus.COMPLETED) or bool(
        comp.get_first_property(I.PropertyKind.COMPLETED_PROPERTY))
    return Task(title=comp.get_summary() or "", due=due, done=done,
                notes=comp.get_description() or "", list=task_list, uid=comp.get_uid(),
                readonly=readonly, raw=comp)


def component_from_task(task: Task, base: I.Component | None = None) -> I.Component:
    comp = base.clone() if base is not None else I.Component.new_vtodo()
    if not comp.get_uid():
        comp.set_uid(task.uid or str(uuid.uuid4()))
    comp.set_summary(task.title)
    _set_text(comp, I.PropertyKind.DESCRIPTION_PROPERTY, task.notes, I.Property.new_description)
    _remove_all(comp, I.PropertyKind.DUE_PROPERTY)
    if isinstance(task.due, datetime):
        tzid = local_tzid()
        comp.add_property(_zoned(I.Property.new_due, make_time(task.due, builtin_zone(tzid)), tzid))
    elif task.due is not None:
        comp.add_property(I.Property.new_due(make_date(task.due)))
    was_done = task_from_component(base, lambda _t: None).done if base is not None else False
    if task.done != was_done or base is None:
        for kind in (I.PropertyKind.STATUS_PROPERTY, I.PropertyKind.COMPLETED_PROPERTY,
                     I.PropertyKind.PERCENTCOMPLETE_PROPERTY):
            _remove_all(comp, kind)
        comp.add_property(I.Property.new_status(
            I.PropertyStatus.COMPLETED if task.done else I.PropertyStatus.NEEDSACTION))
        comp.add_property(I.Property.new_percentcomplete(100 if task.done else 0))
        if task.done:
            now = I.Time.new_current_with_zone(I.Timezone.get_utc_timezone())
            comp.add_property(I.Property.new_completed(now))
    return comp


def _zoned(new_prop, t: I.Time, tzid: str) -> I.Property:
    prop = new_prop(t)
    if not prop.get_first_parameter(I.ParameterKind.TZID_PARAMETER):
        prop.add_parameter(I.Parameter.new_tzid(tzid))
    return prop
