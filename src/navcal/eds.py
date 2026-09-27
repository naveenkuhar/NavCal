"""Evolution Data Server backend: the same calendars GNOME Calendar and the
GNOME Shell clock menu use, including online accounts (Google, Microsoft 365,
Nextcloud, CalDAV/iCloud) configured in GNOME Settings.

Each calendar keeps a live cache of its components (via an ECalClientView) and
we expand recurrences ourselves, handling individually edited instances.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

import gi

gi.require_version("EDataServer", "1.2")
gi.require_version("ECal", "2.0")
gi.require_version("ICalGLib", "3.0")
from gi.repository import ECal, EDataServer as E, GLib, GObject, ICalGLib as I  # noqa: E402

from . import ical  # noqa: E402
from .models import DEFAULT_COLOR, Event, Occurrence, Scope, Task, overlaps, sort_occurrences  # noqa: E402

MOD_TYPES = {Scope.THIS: ECal.ObjModType.THIS, Scope.FUTURE: ECal.ObjModType.THIS_AND_FUTURE,
             Scope.ALL: ECal.ObjModType.ALL}
FLAGS = ECal.OperationFlags.NONE
EXPANDED_YEARS = 6  # years of instances kept per repeating event
LONG_EVENT = timedelta(days=7)  # events at least this long are checked one by one
IMPORT_BATCH = 200  # events sent to the calendar service at once when importing


EXTENSIONS = {"events": E.SOURCE_EXTENSION_CALENDAR, "tasks": E.SOURCE_EXTENSION_TASK_LIST}
SOURCE_TYPES = {"events": ECal.ClientSourceType.EVENTS, "tasks": ECal.ClientSourceType.TASKS}


@dataclass
class Calendar:
    """A calendar, or a task list (kind "tasks")."""

    source: E.Source
    kind: str = "events"
    client: ECal.Client | None = None
    view: ECal.ClientView | None = None
    loaded: bool = False
    error: str | None = None
    # uid -> {rid key or None -> (component, Event)}
    items: dict = field(default_factory=lambda: defaultdict(dict))
    # Repeating events' instances, a year at a time: uid -> {year -> Expansion}
    expansions: dict = field(default_factory=dict)
    index: TimeIndex | None = None  # built when needed, dropped when items change
    zones: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return self.source.get_uid()

    @property
    def name(self) -> str:
        return self.source.get_display_name()

    @property
    def _ext(self) -> E.SourceSelectable:
        return self.source.get_extension(EXTENSIONS[self.kind])

    @property
    def color(self) -> str:
        return self._ext.get_color() or DEFAULT_COLOR

    @property
    def visible(self) -> bool:
        return self._ext.get_selected()

    @property
    def backend_name(self) -> str:
        return self._ext.get_backend_name() or ""

    @property
    def readonly(self) -> bool:
        return self.client is None or self.client.is_readonly()

    @property
    def removable(self) -> bool:
        return self.source.get_removable()

    @property
    def account(self) -> str:
        """Name of the account (collection) this calendar belongs to."""
        return self._account_name

    _account_name: str = ""

    def resolve_zone(self, tzid: str) -> I.Timezone | None:
        if tzid in self.zones:
            return self.zones[tzid]
        zone = ical.builtin_zone(tzid)
        if zone is None and self.client is not None:
            try:
                zone = self.client.get_timezone_sync(tzid, None)[1]
            except GLib.Error:
                zone = None
        self.zones[tzid] = zone
        return zone


@dataclass
class Expansion:
    """A repeating event's instances in one year, sorted by start."""

    starts: list[datetime]
    instances: list[tuple[datetime, datetime, I.Time]]
    longest: timedelta  # the longest instance

    @classmethod
    def of_year(cls, comp: I.Component, year: int, resolve) -> Expansion:
        # A day more on each side, so instances right at the year's edges aren't missed.
        first, last = datetime(year, 1, 1) - timedelta(days=1), datetime(year + 1, 1, 2)
        series = ical.fast_forward(comp, first, resolve)
        instances = sorted(ical.expand(series, first, last, resolve), key=lambda inst: inst[0])
        longest = max((e - s for s, e, _rid in instances), default=timedelta(0))
        return cls([inst[0] for inst in instances], instances, longest)

    def overlapping(self, start: datetime, end: datetime):
        # Only instances starting within longest before start can reach into the range.
        first = bisect_left(self.starts, start - self.longest)
        last = bisect_left(self.starts, end)
        return [inst for inst in self.instances[first:last] if overlaps(inst[0], inst[1], start, end)]


@dataclass
class TimeIndex:
    """A calendar's one-off events sorted by start, to find a range's events quickly."""

    starts: list[datetime]
    events: list[Event]  # one-off events shorter than LONG_EVENT, by start
    long: list[Event]  # longer one-off events
    groups: list[dict]  # everything else: repeating events and edited instances

    @classmethod
    def of(cls, items: dict) -> TimeIndex:
        events, long, groups = [], [], []
        for group in items.values():
            master = group.get(None)
            if len(group) == 1 and master and not (master[1].is_recurring or master[1].rrule_extra):
                ev = master[1]
                (long if ev.end - ev.start >= LONG_EVENT else events).append(ev)
            else:
                groups.append(group)
        events.sort(key=lambda ev: ev.start)
        return cls([ev.start for ev in events], events, long, groups)

    def events_in(self, start: datetime, end: datetime):
        first = bisect_left(self.starts, start - LONG_EVENT)
        last = bisect_left(self.starts, end)
        for ev in self.events[first:last]:
            if overlaps(ev.start, ev.end, start, end):
                yield ev
        for ev in self.long:
            if overlaps(ev.start, ev.end, start, end):
                yield ev


class Backend(GObject.Object):
    """All enabled calendars. Emits "changed" when events or calendars change."""

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_LAST, None, ()),
        "calendars-changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self):
        super().__init__()
        self.registry = E.SourceRegistry.new_sync(None)
        self.calendars: dict[str, Calendar] = {}
        self.task_lists: dict[str, Calendar] = {}
        self._changed_pending = False
        self._expansion_zone = ical.local_tzid()  # expansions are in this time zone
        # One worker keeps writes in order.
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="navcal-eds")
        self._pending = 0
        for signal in ("source-added", "source-removed", "source-changed",
                       "source-enabled", "source-disabled"):
            self.registry.connect(signal, lambda *_args: GLib.idle_add(self._sync_sources))
        self._sync_sources()

    # -- calendars -------------------------------------------------------------------

    def _sync_sources(self) -> bool:
        for kind, known in (("events", self.calendars), ("tasks", self.task_lists)):
            wanted = {s.get_uid(): s for s in self.registry.list_enabled(EXTENSIONS[kind])}
            for uid in list(known):
                if uid not in wanted:
                    cal = known.pop(uid)
                    if cal.view:
                        cal.view.stop()
            for uid, source in wanted.items():
                if uid not in known:
                    cal = Calendar(source, kind)
                    known[uid] = cal
                    ECal.Client.connect(source, SOURCE_TYPES[kind], 30, None,
                                        self._on_connected, cal)
        for cal in (*self.calendars.values(), *self.task_lists.values()):
            collection = self.registry.find_extension(cal.source, E.SOURCE_EXTENSION_COLLECTION)
            parent = self.registry.ref_source(cal.source.get_parent()) if cal.source.get_parent() else None
            cal._account_name = (collection or parent).get_display_name() if (collection or parent) else ""
        self.notify("busy")
        self.emit("calendars-changed")
        self._queue_changed()
        return GLib.SOURCE_REMOVE

    def _on_connected(self, _source, result, cal: Calendar) -> None:
        try:
            cal.client = ECal.Client.connect_finish(result)
        except GLib.Error as e:
            cal.error = e.message
            self.notify("busy")
            self.emit("calendars-changed")
            return
        cal.client.get_view("#t", None, self._on_view, cal)

    def _on_view(self, client, result, cal: Calendar) -> None:
        try:
            ok, view = client.get_view_finish(result)
        except GLib.Error as e:
            cal.error = e.message
            # No longer loading: stop the spinner, show the error, let reminders start.
            self.notify("busy")
            self.emit("calendars-changed")
            return
        cal.view = view
        view.connect("objects-added", lambda _v, comps: self._store(cal, comps))
        view.connect("objects-modified", lambda _v, comps: self._store(cal, comps))
        view.connect("objects-removed", lambda _v, ids: self._forget(cal, ids))
        view.connect("complete", lambda *_args: self._on_complete(cal))
        view.start()

    def _on_complete(self, cal: Calendar) -> None:
        cal.loaded = True
        self.notify("busy")
        self.emit("calendars-changed")
        self._queue_changed()

    def _store(self, cal: Calendar, comps) -> None:
        for comp in comps:
            comp = comp.clone()
            rid = comp.get_recurrenceid()
            key = None if rid.is_null_time() else self._rid_key(cal, comp)
            if cal.kind == "tasks":
                item = ical.task_from_component(comp, cal.resolve_zone, cal.uid, cal.readonly)
            else:
                item = ical.event_from_component(comp, cal.resolve_zone, cal.uid, cal.readonly)
            cal.items[comp.get_uid()][key] = (comp, item)
            cal.expansions.pop(comp.get_uid(), None)
        cal.index = None
        self._queue_changed()

    def _forget(self, cal: Calendar, ids) -> None:
        for cid in ids:
            uid, rid = cid.get_uid(), cid.get_rid()
            cal.expansions.pop(uid, None)
            cal.index = None
            group = cal.items.get(uid)
            if group is None:
                continue
            if not rid:
                cal.items.pop(uid, None)
            else:
                for key, (comp, _ev) in list(group.items()):
                    if key is not None and ical.rid_string(comp.get_recurrenceid()) == rid:
                        group.pop(key)
                if not group:
                    cal.items.pop(uid, None)
        self._queue_changed()

    def _rid_key(self, cal: Calendar, comp: I.Component) -> int:
        t, _tzid = ical.prop_time(comp, I.PropertyKind.RECURRENCEID_PROPERTY, cal.resolve_zone)
        return ical.time_key(t)

    def _queue_changed(self) -> None:
        # Views often report many objects in a row; redraw once.
        if not self._changed_pending:
            self._changed_pending = True
            GLib.timeout_add(50, self._emit_changed)

    def _emit_changed(self) -> bool:
        self._changed_pending = False
        self.emit("changed")
        return GLib.SOURCE_REMOVE

    def sorted_calendars(self) -> list[Calendar]:
        return sorted(self.calendars.values(), key=lambda c: (c.account, c.name.lower()))

    def writable_calendars(self) -> list[Calendar]:
        return [c for c in self.sorted_calendars() if c.client and not c.readonly]

    def calendar(self, uid: str | None) -> Calendar | None:
        """A calendar or task list by its source UID."""
        return (self.calendars.get(uid) or self.task_lists.get(uid)) if uid else None

    def set_visible(self, cal: Calendar, visible: bool) -> None:
        # The same flag GNOME Calendar and the Shell clock menu use.
        cal._ext.set_selected(visible)
        cal.source.write(None, None, None)
        self._queue_changed()

    def set_color(self, cal: Calendar, color: str) -> None:
        cal._ext.set_color(color)
        cal.source.write(None, None, None)
        self._queue_changed()

    def rename(self, cal: Calendar, name: str) -> None:
        cal.source.set_display_name(name)
        cal.source.write(None, None, None)

    def add_local_calendar(self, name: str, color: str, kind: str = "events") -> str:
        """A new calendar (or task list) stored on this computer."""
        source = E.Source.new(None, None)
        source.set_parent("local-stub")
        source.set_display_name(name)
        ext = source.get_extension(EXTENSIONS[kind])
        ext.set_backend_name("local")
        ext.set_color(color)
        self.registry.commit_source_sync(source, None)
        return source.get_uid()

    def subscribe(self, url: str, name: str, color: str) -> str:
        """Add a read-only calendar from a link (webcal:// or https://…ics)."""
        source = E.Source.new(None, None)
        source.set_parent("webcal-stub")
        source.set_display_name(name)
        ext = source.get_extension(E.SOURCE_EXTENSION_CALENDAR)
        ext.set_backend_name("webcal")
        ext.set_color(color)
        # Keep the link percent-encoded: EDS rebuilds it as-is ("%23" must not become "#").
        uri = GLib.Uri.parse(url.replace("webcal://", "https://", 1), GLib.UriFlags.ENCODED)
        if uri.get_port() == -1:  # EDS would otherwise assume port 80, even for https
            uri = GLib.Uri.build(GLib.UriFlags.ENCODED, uri.get_scheme(), uri.get_userinfo(),
                                 uri.get_host(), 443 if uri.get_scheme() == "https" else 80,
                                 uri.get_path(), uri.get_query(), uri.get_fragment())
        source.get_extension(E.SOURCE_EXTENSION_WEBDAV_BACKEND).set_uri(uri)
        source.get_extension(E.SOURCE_EXTENSION_REFRESH).set_interval_minutes(60)
        self.registry.commit_source_sync(source, None)
        return source.get_uid()

    def remove_calendar(self, cal: Calendar) -> None:
        cal.source.remove_sync(None)

    def connect_calendar_sync(self, uid: str, timeout_s: int = 10) -> Calendar | None:
        """Wait until a calendar (e.g. one we just created) is connected and loaded.

        None if it isn't ready in time (it may still exist, just not be usable yet).
        """
        deadline = GLib.get_monotonic_time() + timeout_s * 1_000_000
        context = GLib.MainContext.default()
        while True:
            cal = self.calendar(uid)
            if cal and cal.client and cal.loaded:
                return cal
            if GLib.get_monotonic_time() >= deadline:
                return None
            context.iteration(True)

    # -- reading ------------------------------------------------------------------

    def occurrences(self, start: datetime, end: datetime,
                    calendars: list[Calendar] | None = None) -> list[Occurrence]:
        self._check_expansion_zone()
        out: list[Occurrence] = []
        for cal in calendars if calendars is not None else self.calendars.values():
            if calendars is None and not cal.visible:
                continue
            if cal.index is None:
                cal.index = TimeIndex.of(cal.items)
            color = cal.color
            out.extend(Occurrence(ev, ev.start, ev.end, color=ev.color or color)
                       for ev in cal.index.events_in(start, end))
            for group in cal.index.groups:
                out.extend(self._group_occurrences(cal, group, start, end))
        return sort_occurrences(out)

    def _group_occurrences(self, cal: Calendar, group: dict, start, end):
        master = group.get(None)
        detached = {k: v for k, v in group.items() if k is not None}
        if master and (master[1].is_recurring or master[1].rrule_extra):
            comp, ev = master
            color = ev.color or cal.color
            for s, e, rid in self._instances(cal, comp, start, end):
                if not detached or ical.time_key(rid) not in detached:
                    yield Occurrence(ev, s, e, rid=rid, color=color)
        elif master:
            comp, ev = master
            if overlaps(ev.start, ev.end, start, end):
                yield Occurrence(ev, ev.start, ev.end, color=ev.color or cal.color)
        for comp, ev in detached.values():
            if overlaps(ev.start, ev.end, start, end):
                rid, _tzid = ical.prop_time(comp, I.PropertyKind.RECURRENCEID_PROPERTY, cal.resolve_zone)
                yield Occurrence(ev, ev.start, ev.end, rid=rid, color=ev.color or cal.color)

    def _instances(self, cal: Calendar, comp: I.Component, start: datetime, end: datetime):
        """A repeating event's instances overlapping [start, end): (start, end, rid).

        Expanding is slow (it steps through every instance since the first), so
        instances are expanded a year at a time and kept until the event changes.
        """
        years = cal.expansions.setdefault(comp.get_uid(), {})
        span = range(start.year, (end - timedelta(microseconds=1)).year + 1)
        out, seen = [], set()
        for year in span:
            # Taken out and put back, so the dict's order is least recently used first.
            expansion = years.pop(year, None) or Expansion.of_year(comp, year, cal.resolve_zone)
            years[year] = expansion
            for inst in expansion.overlapping(start, end):
                if len(span) == 1 or inst[0] not in seen:  # neighboring years share their edges
                    seen.add(inst[0])
                    out.append(inst)
        while len(years) > EXPANDED_YEARS:
            del years[next(iter(years))]
        return out

    def _check_expansion_zone(self) -> None:
        """Instances are in local time: expand again if the time zone changed."""
        zone = ical.local_tzid()
        if zone != self._expansion_zone:
            self._expansion_zone = zone
            for cal in self.calendars.values():
                cal.expansions.clear()
                for group in cal.items.values():  # floating recurrence ids moved too
                    edited = [item for key, item in group.items() if key is not None]
                    for key in [key for key in group if key is not None]:
                        del group[key]
                    for comp, ev in edited:
                        group[self._rid_key(cal, comp)] = (comp, ev)

    def series(self, occ: Occurrence) -> Event:
        """The master event of occ's series (occ.event may be an edited instance)."""
        cal = self.calendars[occ.event.calendar]
        master = cal.items.get(occ.event.uid, {}).get(None)
        return master[1] if master else occ.event

    def search(self, text: str, around: datetime, limit: int = 100) -> list[Occurrence]:
        """Events in visible calendars whose title, location or notes contain text.

        Each event appears once, as its next occurrence after around (or its
        most recent one, if it's over): upcoming first, then past, newest first.
        """
        words = text.casefold().split()
        if not words:
            return []
        self._check_expansion_zone()
        start, end = around - timedelta(days=365), around + timedelta(days=730)
        upcoming, past = [], []
        for cal in self.calendars.values():
            if not cal.visible:
                continue
            for group in cal.items.values():
                if not any(all(w in f"{ev.title}\n{ev.location}\n{ev.notes}".casefold()
                               for w in words) for _comp, ev in group.values()):
                    continue
                occs = list(self._group_occurrences(cal, group, start, end))
                later = [o for o in occs if o.end > around]
                if later:
                    upcoming.append(min(later, key=lambda o: o.start))
                elif occs:
                    past.append(max(occs, key=lambda o: o.start))
        upcoming.sort(key=lambda o: o.start)
        past.sort(key=lambda o: o.start, reverse=True)
        return (upcoming + past)[:limit]

    # -- writing --------------------------------------------------------------------
    #
    # Each change is planned on the UI thread (reading the live cache) and returns
    # an operation that only talks to the calendar service. Operations run on a
    # background worker (see run) so slow online calendars never block the UI.
    # The plain methods (create, save, …) run them immediately, for tests and scripts.

    def run(self, op: Callable[[], object], on_done: Callable[[object, GLib.Error | None], None]):
        """Run op on the worker; call on_done(result, error) on the UI thread."""
        self._pending += 1
        self.notify("busy")

        def finish(future):
            error = future.exception()
            result = None if error else future.result()
            GLib.idle_add(self._finish, on_done, result, error)

        self._worker.submit(op).add_done_callback(finish)

    def _finish(self, on_done, result, error) -> bool:
        self._pending -= 1
        self.notify("busy")
        if error is not None and not isinstance(error, GLib.Error):
            raise error
        on_done(result, error)
        return GLib.SOURCE_REMOVE

    @GObject.Property(type=bool, default=False)
    def busy(self) -> bool:
        """Writing, or still loading a calendar."""
        return self._pending > 0 or any(
            not c.loaded and not c.error
            for c in (*self.calendars.values(), *self.task_lists.values()))

    @staticmethod
    def _add_zones(client: ECal.Client, tzids: list[str | None]) -> None:
        for tzid in tzids:
            zone = ical.builtin_zone(tzid)
            if zone is not None and tzid != "UTC":
                try:
                    client.add_timezone_sync(zone, None)
                except GLib.Error:
                    pass

    def plan_create(self, calendar_uid: str, ev: Event) -> Callable[[], str]:
        client = self.calendars[calendar_uid].client
        comp = ical.component_from_event(ev)
        tzids = [ev.tzid or ical.local_tzid()] if not ev.all_day else []

        def op() -> str:
            self._add_zones(client, tzids)
            return client.create_object_sync(comp, FLAGS, None)[1]
        return op

    def plan_save(self, occ: Occurrence, edited: Event, scope: str) -> Callable[[], None]:
        """Store edited, which describes occ with new values."""
        cal = self.calendars[occ.event.calendar]
        if edited.calendar and edited.calendar != cal.uid and scope == Scope.ALL:
            return self._plan_move_to_calendar(occ, edited, cal)
        client = cal.client
        tzids = [edited.tzid or ical.local_tzid()] if not edited.all_day else []
        if not occ.is_instance:
            comp, mod = ical.component_from_event(edited, occ.event.raw), ECal.ObjModType.ALL
        elif scope == Scope.ALL:
            # Shift the whole series by however far this occurrence was moved.
            master = self.series(occ)
            shift, duration = edited.start - occ.start, edited.duration
            edited.start = master.start + shift
            edited.end = edited.start + duration
            comp = ical.component_from_event(edited, master.raw)
            _keep_exdates(master.raw, comp)
            mod = ECal.ObjModType.ALL
        else:
            base = occ.event.raw if occ.event.raw.get_first_property(
                I.PropertyKind.RECURRENCEID_PROPERTY) else self.series(occ).raw
            comp = ical.component_from_event(edited, base)
            if scope == Scope.THIS:
                ical.strip_recurrence(comp)
            ical.set_recurrence_id(comp, occ.rid)
            mod = MOD_TYPES[scope]

        def op() -> None:
            self._add_zones(client, tzids)
            client.modify_object_sync(comp, mod, FLAGS, None)
        return op

    def _plan_move_to_calendar(self, occ: Occurrence, edited: Event, old: Calendar):
        new = self.calendars[edited.calendar]
        extras = [c.clone() for key, (c, _item) in old.items.get(occ.event.uid, {}).items()
                  if key is not None]
        master = self.series(occ)
        if occ.is_instance:
            shift, duration = edited.start - occ.start, edited.duration
            edited.start, edited.end = master.start + shift, master.start + shift + duration
        comp = ical.component_from_event(edited, master.raw)
        _keep_exdates(master.raw, comp)
        uid = occ.event.uid
        tzids = [edited.tzid or ical.local_tzid()] if not edited.all_day else []

        def op() -> None:
            self._add_zones(new.client, tzids)
            new.client.create_object_sync(comp, FLAGS, None)
            for extra in extras:
                new.client.modify_object_sync(extra, ECal.ObjModType.THIS, FLAGS, None)
            old.client.remove_object_sync(uid, None, ECal.ObjModType.ALL, FLAGS, None)
        return op

    def plan_remove(self, occ: Occurrence, scope: str) -> Callable[[], None]:
        client = self.calendars[occ.event.calendar].client
        uid = occ.event.uid
        if not occ.is_instance or scope == Scope.ALL:
            rid, mod = None, ECal.ObjModType.ALL
        else:
            rid, mod = ical.rid_string(occ.rid), MOD_TYPES[scope]
        return lambda: client.remove_object_sync(uid, rid, mod, FLAGS, None)

    def create(self, calendar_uid: str, ev: Event) -> str:
        return self.plan_create(calendar_uid, ev)()

    def save(self, occ: Occurrence, edited: Event, scope: str) -> None:
        self.plan_save(occ, edited, scope)()

    def remove(self, occ: Occurrence, scope: str) -> None:
        self.plan_remove(occ, scope)()

    # -- tasks ----------------------------------------------------------------------

    def tasks(self) -> list[Task]:
        """Tasks from visible task lists: open ones by due date, then finished ones."""
        out = [item for tl in self.task_lists.values() if tl.visible
               for group in tl.items.values() for key, (_c, item) in group.items() if key is None]
        far = datetime.max

        def due_key(t: Task):
            if t.due is None:
                return far
            return t.due if isinstance(t.due, datetime) else datetime.combine(t.due, datetime.min.time())

        out.sort(key=lambda t: (t.done, due_key(t), t.title.casefold()))
        return out

    def writable_task_lists(self) -> list[Calendar]:
        return sorted((t for t in self.task_lists.values() if t.client and not t.readonly),
                      key=lambda t: (t.account, t.name.lower()))

    def plan_create_task(self, list_uid: str, task: Task) -> Callable[[], str]:
        client = self.task_lists[list_uid].client
        comp = ical.component_from_task(task)
        return lambda: client.create_object_sync(comp, FLAGS, None)[1]

    def plan_save_task(self, task: Task) -> Callable[[], None]:
        old = self.task_lists[task.list]
        target = self.task_lists.get(task.new_list or task.list, old)
        comp = ical.component_from_task(task, task.raw)
        if target is old:
            return lambda: old.client.modify_object_sync(comp, ECal.ObjModType.ALL, FLAGS, None)

        def move():  # to another task list
            target.client.create_object_sync(comp, FLAGS, None)
            old.client.remove_object_sync(task.uid, None, ECal.ObjModType.ALL, FLAGS, None)
        return move

    def plan_remove_task(self, task: Task) -> Callable[[], None]:
        client = self.task_lists[task.list].client
        return lambda: client.remove_object_sync(task.uid, None, ECal.ObjModType.ALL, FLAGS, None)

    # -- undo snapshots ----------------------------------------------------------

    def snapshot(self, calendar_uid: str, uid: str) -> tuple[str, str, list[str]]:
        """Everything stored for one event (series plus edited instances)."""
        cal = self.calendar(calendar_uid)
        group = cal.items.get(uid, {})
        ordered = sorted(group.items(), key=lambda kv: kv[0] is not None)  # master first
        return (calendar_uid, uid, [comp.as_ical_string() for _key, (comp, _ev) in ordered])

    def plan_restore(self, snap: tuple[str, str, list[str]]) -> Callable[[], None]:
        calendar_uid, uid, texts = snap
        cal = self.calendar(calendar_uid)
        if cal is None or cal.client is None:
            return lambda: None
        client, exists = cal.client, uid in cal.items

        def op() -> None:
            if exists:
                try:
                    client.remove_object_sync(uid, None, ECal.ObjModType.ALL, FLAGS, None)
                except GLib.Error:
                    pass
            for i, text in enumerate(texts):
                comp = I.Component.new_from_string(text)
                if i == 0 and comp.get_recurrenceid().is_null_time():
                    client.create_object_sync(comp, FLAGS, None)
                else:
                    client.modify_object_sync(comp, ECal.ObjModType.THIS, FLAGS, None)
        return op

    def restore(self, snap: tuple[str, str, list[str]]) -> None:
        self.plan_restore(snap)()

    # -- import / export ---------------------------------------------------------

    def export_text(self, calendars: list[Calendar]) -> str:
        comps, zones = [], []
        for cal in calendars:
            for group in cal.items.values():
                for comp, _item in group.values():
                    comps.append(comp)
                    _t, tzid = ical.prop_time(comp, I.PropertyKind.DTSTART_PROPERTY, cal.resolve_zone)
                    if tzid and tzid != "UTC":
                        zones.append(cal.resolve_zone(tzid))
        return ical.calendar_text(comps, zones)

    def plan_import(self, calendar_uid: str, text: str) -> Callable[[], int]:
        """Raises ValueError (on the UI thread) if text isn't an iCalendar file."""
        cal = self.calendars[calendar_uid]
        client = cal.client
        events, vtimezones = ical.parse_calendar(text)
        # Events first, then individually edited instances of them. Events the
        # calendar already has (say, restoring a backup) are updated instead.
        masters = [c for c in events if c.get_recurrenceid().is_null_time()]
        instances = [c for c in events if not c.get_recurrenceid().is_null_time()]
        known = [c for c in masters if c.get_uid() in cal.items]
        new = [c for c in masters if c.get_uid() not in cal.items]
        every, this = ECal.ObjModType.ALL, ECal.ObjModType.THIS

        def op() -> int:
            for vtz in vtimezones:
                zone = I.Timezone.new()
                if zone.set_component(vtz):
                    try:
                        client.add_timezone_sync(zone, None)
                    except GLib.Error:
                        pass

            # In batches: one at a time is slow, as a calendar may save after each.
            # If a batch fails, its events go one by one.
            def in_batches(comps, send_batch, send_one):
                for i in range(0, len(comps), IMPORT_BATCH):
                    batch = comps[i:i + IMPORT_BATCH]
                    try:
                        send_batch(batch)
                    except GLib.Error:
                        for comp in batch:
                            send_one(comp)

            def create(comp):
                try:
                    client.create_object_sync(comp, FLAGS, None)
                except GLib.Error:  # already there
                    client.modify_object_sync(comp, every, FLAGS, None)

            def update(comp):
                try:
                    client.modify_object_sync(comp, every, FLAGS, None)
                except GLib.Error:  # not there after all
                    client.create_object_sync(comp, FLAGS, None)

            in_batches(new, lambda batch: client.create_objects_sync(batch, FLAGS, None), create)
            in_batches(known, lambda batch: client.modify_objects_sync(batch, every, FLAGS, None), update)
            in_batches(instances, lambda batch: client.modify_objects_sync(batch, this, FLAGS, None),
                       lambda comp: client.modify_object_sync(comp, this, FLAGS, None))
            return len(masters)
        return op

    def import_text(self, calendar_uid: str, text: str) -> int:
        return self.plan_import(calendar_uid, text)()


def _keep_exdates(source: I.Component | None, target: I.Component) -> None:
    """Carry EXDATEs over when a series is rewritten (they are not editable)."""
    if source is None or target.get_first_property(I.PropertyKind.EXDATE_PROPERTY):
        return
    if not target.get_first_property(I.PropertyKind.RRULE_PROPERTY):
        return
    prop = source.get_first_property(I.PropertyKind.EXDATE_PROPERTY)
    while prop:
        target.add_property(prop.clone())
        prop = source.get_next_property(I.PropertyKind.EXDATE_PROPERTY)
