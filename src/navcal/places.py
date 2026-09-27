# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Places: address search as you type, finding where an event's location is,
and opening hours, all from OpenStreetMap (Photon for search, Nominatim for
details). Data © OpenStreetMap contributors."""

from __future__ import annotations

import json
import os
import re
import time as clock
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from gi.repository import Gio, GLib

from . import ical, online
from .draw import fmt_time
from .i18n import N_, _

SEARCH_URL = "https://photon.komoot.io/api/"
DETAILS_URL = "https://nominatim.openstreetmap.org/lookup"
DETAILS_INTERVAL_S = 1.1  # Nominatim allows one request a second
LOOK_AHEAD = timedelta(days=14)  # how far ahead events' places are checked for the views
RETRY_S = 300  # a lookup that failed (offline, say) is tried again after this
KEEP_S = {"found": 30 * 86400, "details": 7 * 86400}  # how long answers are remembered on disk
# Locations that aren't places to look up (checked in lowercase, without punctuation).
NOT_PLACES = {"home", "office", "work", "the office", "my office", "online", "remote", "virtual", "zoom",
              "teams", "microsoft teams", "google meet", "meet", "skype", "webex", "discord", "slack",
              "phone", "call", "phone call", "video call", "tbd", "tba", "n a", "here", "my place"}
# Kinds of places that have opening hours worth showing.
ESTABLISHMENTS = {"amenity", "shop", "tourism", "leisure", "office", "craft", "healthcare", "club"}
# Countries that write the house number after the street name.
NUMBER_AFTER = set("AT BE BR CH CL CZ DE DK EE ES FI HR IT LT LV MX NL NO PL PT RO RS SE SI SK TR".split())
KINDS = {  # (OpenStreetMap key, value): what kind of place it is
    ("amenity", "cafe"): N_("Café"), ("amenity", "restaurant"): N_("Restaurant"),
    ("amenity", "fast_food"): N_("Fast food"), ("amenity", "bar"): N_("Bar"), ("amenity", "pub"): N_("Pub"),
    ("amenity", "ice_cream"): N_("Ice cream shop"), ("amenity", "bank"): N_("Bank"),
    ("amenity", "pharmacy"): N_("Pharmacy"), ("amenity", "library"): N_("Library"),
    ("amenity", "cinema"): N_("Cinema"), ("amenity", "theatre"): N_("Theater"),
    ("amenity", "hospital"): N_("Hospital"), ("amenity", "clinic"): N_("Clinic"),
    ("amenity", "dentist"): N_("Dentist"), ("amenity", "doctors"): N_("Doctor’s office"),
    ("amenity", "post_office"): N_("Post office"), ("amenity", "fuel"): N_("Gas station"),
    ("amenity", "community_centre"): N_("Community center"),
    ("shop", "supermarket"): N_("Supermarket"), ("shop", "convenience"): N_("Convenience store"),
    ("shop", "bakery"): N_("Bakery"), ("shop", "mall"): N_("Shopping mall"),
    ("shop", "clothes"): N_("Clothing store"), ("shop", "hairdresser"): N_("Hairdresser"),
    ("tourism", "museum"): N_("Museum"), ("tourism", "hotel"): N_("Hotel"),
    ("tourism", "gallery"): N_("Gallery"), ("tourism", "attraction"): N_("Attraction"),
    ("tourism", "zoo"): N_("Zoo"), ("leisure", "fitness_centre"): N_("Gym"),
    ("leisure", "sports_centre"): N_("Sports center"), ("leisure", "park"): N_("Park"),
    ("leisure", "swimming_pool"): N_("Swimming pool"),
}
KEY_KINDS = {"amenity": N_("Place"), "shop": N_("Shop"), "tourism": N_("Tourist spot"),
             "leisure": N_("Leisure"), "office": N_("Office"), "craft": N_("Workshop"),
             "healthcare": N_("Health care"), "club": N_("Club")}


def kind_label(key: str, value: str) -> str:
    """"Café", "Supermarket"… or "" if it isn't a kind of business."""
    label = KINDS.get((key, value)) or KEY_KINDS.get(key)
    return _(label) if label else ""


@dataclass
class Place:
    name: str
    address: str  # the rest: street, city, country
    lat: float
    lon: float
    osm: str = ""  # OpenStreetMap id: "N123" (node), "W45" (way), "R6" (relation)
    kind: str = ""  # its OpenStreetMap key, like "amenity" or "shop"

    @property
    def label(self) -> str:
        """What goes in the location field."""
        return f"{self.name}, {self.address}" if self.address else self.name

    @property
    def is_establishment(self) -> bool:
        return self.kind in ESTABLISHMENTS


def parse_feature(feature: dict) -> Place:
    """A Photon (GeoJSON) search result."""
    p = feature.get("properties", {})
    lon, lat = feature["geometry"]["coordinates"][:2]
    number, street = p.get("housenumber"), p.get("street")
    if street and number:
        street = f"{street} {number}" if p.get("countrycode") in NUMBER_AFTER else f"{number} {street}"
    city = p.get("city") or p.get("town") or p.get("village") or p.get("district") or p.get("county")
    parts = [street, city, p.get("state"), p.get("country")]
    name = p.get("name") or street or city or p.get("country") or ""
    seen, address = {name}, []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            address.append(part)
    osm = f"{p['osm_type']}{p['osm_id']}" if p.get("osm_type") and p.get("osm_id") else ""
    return Place(name, ", ".join(address), float(lat), float(lon), osm, p.get("osm_key", ""))


def _language() -> str:
    """Photon answers in English, German, French or Italian, or in the local language."""
    for value in (os.environ.get("LANGUAGE"), os.environ.get("LC_ALL"), os.environ.get("LANG")):
        if value:
            code = value.split(":")[0][:2]
            return code if code in ("en", "de", "fr", "it") else "default"
    return "default"


def home(config) -> tuple[float, float] | None:
    """Where to look first: the weather place, or else the city of the time zone.
    Without it, "Starbucks" could be one on another continent."""
    place = config["weather-place"]
    if place:
        return place["lat"], place["lon"]
    zone = ical.builtin_zone(ical.local_tzid())
    if zone and (zone.get_latitude() or zone.get_longitude()):
        return zone.get_latitude(), zone.get_longitude()
    return None


def search(text: str, near: tuple[float, float] | None, done: Callable[[list[Place] | None], None],
           cancellable: Gio.Cancellable | None = None, limit: int = 6) -> None:
    """Places matching text, closest to near first (if given); None if the search failed."""
    params = {"q": text, "limit": limit, "lang": _language()}
    if near:
        params["lat"], params["lon"] = f"{near[0]:.4f}", f"{near[1]:.4f}"

    def answered(data):
        if data is None:
            done(None)
            return
        try:
            done([parse_feature(f) for f in data.get("features", [])])
        except (AttributeError, KeyError, TypeError, ValueError, IndexError):
            done([])

    online.get_json(SEARCH_URL, params, answered, cancellable)


def _words(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def worth_looking_up(text: str) -> bool:
    """Could this location be a place on a map? Not "Zoom", a link or a phone number."""
    words = _words(text)
    return (len(words) >= 3 and words not in NOT_PLACES and "://" not in text and "www." not in text
            and not re.fullmatch(r"[\d\s+()\-.]+", text.strip()))


def names_match(text: str, place: Place) -> bool:
    """Does typed text name this place: its whole name, maybe followed by more
    ("Starbucks, Queen St" for a Starbucks, but not "Office" for Office Depot)?"""
    typed, name = _words(text), _words(place.name)
    return len(name) >= 3 and (typed == name or typed.startswith(name + " "))


def best_match(text: str, results: list[Place]) -> Place | None:
    """What a typed location most likely means: a business with that name (streets
    and towns with the name rank about as high), or else the first result."""
    for place in results:
        if place.is_establishment and names_match(text, place):
            return place
    return results[0] if results else None


# -- remembered answers ---------------------------------------------------------------
# What typed locations turned out to be, and places' details, kept for a while
# on disk too: the same places come up again and again.

_found: dict[str, Place | None] = {}
_details: dict[str, PlaceDetails | None] = {}
_saved_at: dict[str, float] = {}  # "found <key>" / "details <osm>": when it was answered
_save_timer = 0


def _cache_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "navcal" / "places.json"


def load_cache() -> None:
    """Read the answers remembered on disk (the ones not too old)."""
    try:
        data = json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return
    now = clock.time()
    for kind, table, cls in (("found", _found, Place), ("details", _details, PlaceDetails)):
        for key, (when, value) in (data.get(kind) or {}).items():
            if now - when < KEEP_S[kind]:
                try:
                    table[key] = cls(**value) if value else None
                except TypeError:
                    continue  # from an older version
                _saved_at[f"{kind} {key}"] = when


def _remember(kind: str, key: str) -> None:
    global _save_timer
    _saved_at[f"{kind} {key}"] = clock.time()
    if not _save_timer:
        _save_timer = GLib.timeout_add_seconds(2, _save_cache)


def _save_cache() -> bool:
    global _save_timer
    _save_timer = 0
    data = {kind: {key: [_saved_at.get(f"{kind} {key}", clock.time()), asdict(value) if value else None]
                   for key, value in table.items()}
            for kind, table in (("found", _found), ("details", _details))}
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)
    except OSError:
        pass  # only a cache
    return GLib.SOURCE_REMOVE


def find(text: str, near: tuple[float, float] | None, done: Callable[[Place | None], None]) -> None:
    """Where a location typed as text is: the best match, remembered."""
    key = text.strip().casefold()
    if key in _found:
        done(_found[key])
        return

    def answered(results):
        if results is None:  # failed: don't remember that as "nothing"
            done(None)
            return
        _found[key] = best_match(text, results)
        _remember("found", key)
        done(_found[key])

    search(text, near, answered)


# -- details: what kind of place, and opening hours -----------------------------------


@dataclass
class PlaceDetails:
    name: str
    kind: str  # "Café", "Supermarket"…; "" if it isn't a business
    address: str  # street and city
    hours: str | None  # OpenStreetMap opening_hours, if listed

    @property
    def summary(self) -> str:
        """"Café · 12 Main Street, Montreal"."""
        return " · ".join(part for part in (self.kind, self.address) if part)


def parse_details(data: dict) -> PlaceDetails:
    """A Nominatim lookup result."""
    a = data.get("address") or {}
    street = " ".join(part for part in (a.get("house_number"), a.get("road")) if part)
    city = a.get("city") or a.get("town") or a.get("village") or a.get("suburb")
    address = ", ".join(part for part in (street, city) if part)
    return PlaceDetails(data.get("name") or "", kind_label(data.get("category", ""), data.get("type", "")),
                        address, (data.get("extratags") or {}).get("opening_hours"))


_details_queue: list[tuple[str, list[Callable[[PlaceDetails | None], None]]]] = []
_last_details = 0.0


def details(osm: str, done: Callable[[PlaceDetails | None], None]) -> None:
    """What a place is and its opening hours, from OpenStreetMap (remembered)."""
    if osm in _details:
        done(_details[osm])
        return
    for queued, callbacks in _details_queue:
        if queued == osm:
            callbacks.append(done)
            return
    _details_queue.append((osm, [done]))
    if len(_details_queue) == 1:
        _next_details()


def _next_details() -> bool:
    global _last_details
    if not _details_queue:
        return GLib.SOURCE_REMOVE
    wait = _last_details + DETAILS_INTERVAL_S - clock.monotonic()
    if wait > 0:
        GLib.timeout_add(int(wait * 1000) + 1, _next_details)
        return GLib.SOURCE_REMOVE
    _last_details = clock.monotonic()
    osm, callbacks = _details_queue[0]

    def answered(data):
        found = parse_details(data[0]) if isinstance(data, list) and data else None
        if data is not None:  # not a network failure: remember
            _details[osm] = found
            _remember("details", osm)
        _details_queue.pop(0)
        for callback in callbacks:
            callback(found)
        _next_details()

    online.get_json(DETAILS_URL, {"osm_ids": osm, "format": "jsonv2", "extratags": 1,
                                  "addressdetails": 1}, answered)
    return GLib.SOURCE_REMOVE


class PlaceChecker:
    """Whether the places of the coming two weeks' events are open when they
    happen, found out in the background; changed() is called as answers come."""

    def __init__(self, config, changed: Callable[[], None]):
        self.config, self._changed = config, changed
        self._hours: dict[str, OpeningHours | None] = {}  # by OpenStreetMap id
        self._asked: dict[str, float] = {}  # what was looked up, and when
        self._timer = 0

    def _ask_now(self, what: str) -> bool:
        """Look this up now? Not if it was asked lately (and the answer is on its way or failed)."""
        asked = self._asked.get(what)
        if asked is not None and clock.monotonic() - asked < RETRY_S:
            return False
        self._asked[what] = clock.monotonic()
        return True

    def warning(self, occ) -> str | None:
        """"Closed at 9:00 PM"… for an event, if its place won't be open (and it's known)."""
        ev, now = occ.event, datetime.now()
        if (not self.config["online"] or not ev.location.strip() or occ.is_banner or occ.end < now
                or occ.start > now + LOOK_AHEAD):
            return None
        osm = ev.place or self._match(ev.location)
        if not osm:
            return None
        if osm not in self._hours:
            if self._ask_now(osm):
                details(osm, lambda found, osm=osm: self._got_details(osm, found))
            return None
        hours = self._hours[osm]
        if hours is None:
            return None
        fine, text = hours.status(occ.start, occ.end)
        return None if fine else text

    def _match(self, text: str) -> str:
        """The business a typed location names, if found (looked up once)."""
        if not worth_looking_up(text):
            return ""
        key = text.strip().casefold()
        if key in _found:
            place = _found[key]
            return place.osm if place and place.is_establishment and names_match(text, place) else ""
        if self._ask_now("find " + key):
            find(text, home(self.config), lambda _place: self._soon())
        return ""

    def _got_details(self, osm: str, found: PlaceDetails | None) -> None:
        if osm in _details:  # answered (a failure isn't remembered, and is tried again later)
            self._hours[osm] = OpeningHours.parse(found.hours) if found and found.hours else None
        self._soon()

    def _soon(self) -> None:
        """Answers often come in a row: redraw once."""
        if not self._timer:
            self._timer = GLib.timeout_add(300, self._fire)

    def _fire(self) -> bool:
        self._timer = 0
        self._changed()
        return GLib.SOURCE_REMOVE


DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
_TIME = r"(\d{1,2}):(\d{2})"
_RANGE = re.compile(rf"^{_TIME}-{_TIME}$")


class OpeningHours:
    """The common part of OpenStreetMap's opening_hours format: weekdays, times,
    "24/7" and "off". Anything more (months, holidays, sunset…) makes parse()
    give up rather than guess; public-holiday rules are skipped."""

    def __init__(self, week: list[list[tuple[int, int]]]):
        self.week = week  # for Monday to Sunday: [(open minute, close minute)]; close may pass midnight

    @classmethod
    def parse(cls, text: str) -> OpeningHours | None:
        text = text.strip()
        if text == "24/7":
            return cls([[(0, 24 * 60)] for _day in range(7)])
        week: list[list[tuple[int, int]]] = [[] for _day in range(7)]
        for rule in re.split(r";|\|\|", text):
            rule = rule.strip()
            if not rule:
                continue
            if rule.startswith(("PH", "SH")):
                continue  # public or school holidays: can't tell when those are
            days, rest = cls._days(rule)
            if days is None:
                return None
            rest = rest.strip().strip('"')
            if rest in ("off", "closed"):
                spans = []
            else:
                spans = []
                for part in rest.split(","):
                    m = _RANGE.match(part.strip())
                    if not m:
                        return None
                    h1, m1, h2, m2 = (int(g) for g in m.groups())
                    start, end = h1 * 60 + m1, h2 * 60 + m2
                    if end <= start:
                        end += 24 * 60  # past midnight
                    spans.append((start, end))
            for d in days:  # a later rule replaces an earlier one for its days
                week[d] = list(spans)
        return cls(week)

    @staticmethod
    def _days(rule: str) -> tuple[list[int] | None, str]:
        """The weekdays a rule is for (every day if it names none), and the rest."""
        m = re.match(r"^((?:[A-Z][a-z](?:-[A-Z][a-z])?)(?:,(?:[A-Z][a-z](?:-[A-Z][a-z])?))*)\s+(.*)$", rule)
        if not m:
            return (list(range(7)), rule) if re.match(r"^[\d\s:,\-]+$|^(off|closed)$", rule) else (None, "")
        days = []
        for part in m.group(1).split(","):
            ends = part.split("-")
            if any(e not in DAYS for e in ends):
                return None, ""
            a = DAYS.index(ends[0])
            b = DAYS.index(ends[-1])
            days.extend((a + i) % 7 for i in range((b - a) % 7 + 1))
        return days, m.group(2)

    def spans(self, d: date) -> list[tuple[datetime, datetime]]:
        """When it's open on a day, counting hours that go on from the day before."""
        out = []
        for day, offset in ((d - timedelta(days=1), -1), (d, 0)):
            base = datetime.combine(day, time())
            for start, end in self.week[day.weekday()]:
                s, e = base + timedelta(minutes=start), base + timedelta(minutes=end)
                if offset == 0 or e > datetime.combine(d, time()):
                    out.append((s, e))
        return sorted(out)

    def _closing(self, e: datetime) -> datetime:
        """When it really closes, if the next opening starts right as this one ends (24/7, say)."""
        for _day in range(8):
            follow = [s2e for s2e in self.spans(e.date()) + self.spans(e.date() + timedelta(days=1))
                      if s2e[0] == e and s2e[1] > e]
            if not follow:
                break
            e = max(end for _s, end in follow)
        return e

    def status(self, start: datetime, end: datetime) -> tuple[bool, str]:
        """(fine, what to say) about going there from start to end."""
        for s, e in self.spans(start.date()):
            if s <= start < e:
                e = self._closing(e)
                if e >= end:
                    return True, _("Open until {time}").format(time=fmt_time(e))
                return False, _("Closes at {time}, before the event ends").format(time=fmt_time(e))
        later = [s for s, _e in self.spans(start.date()) if s > start]
        if later:
            return False, _("Closed at {time}, opens at {opens}").format(
                time=fmt_time(start), opens=fmt_time(min(later)))
        return False, _("Closed at {time}").format(time=fmt_time(start))
