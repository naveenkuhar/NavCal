# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

from datetime import datetime, timedelta

import pytest

from navcal import draw, places
from navcal.places import OpeningHours, parse_feature


@pytest.fixture(autouse=True)
def clock_24h(monkeypatch):
    monkeypatch.setattr(draw, "uses_12h_clock", lambda: False)


def feature(**props):
    return {"geometry": {"coordinates": [-73.5674, 45.5019]}, "properties": props}


def test_search_result():
    place = parse_feature(feature(name="Café Luna", housenumber="12", street="Main Street", city="Montreal",
                                  country="Canada", countrycode="CA", osm_type="N", osm_id=42,
                                  osm_key="amenity"))
    assert place.label == "Café Luna, 12 Main Street, Montreal, Canada"
    assert (place.lat, place.lon, place.osm) == (45.5019, -73.5674, "N42")
    assert place.is_establishment


def test_search_result_street_first_and_no_name():
    place = parse_feature(feature(housenumber="5", street="Hauptstraße", city="Berlin", country="Germany",
                                  countrycode="DE", osm_key="place"))
    assert place.name == "Hauptstraße 5" and place.address == "Berlin, Germany"
    assert not place.is_establishment


MON = datetime(2026, 9, 28)  # a Monday


def at(day, h, m=0):
    return MON + timedelta(days=day, hours=h, minutes=m)


def test_weekday_hours():
    hours = OpeningHours.parse("Mo-Fr 09:00-12:00,13:00-18:00; Sa 10:00-14:00; Su off; PH off")
    assert hours.status(at(0, 10), at(0, 11)) == (True, "Open until 12:00")
    assert hours.status(at(0, 11), at(0, 13)) == (False, "Closes at 12:00, before the event ends")
    assert hours.status(at(0, 12, 30), at(0, 13, 30)) == (False, "Closed at 12:30, opens at 13:00")
    assert hours.status(at(5, 15), at(5, 16)) == (False, "Closed at 15:00")
    assert hours.status(at(6, 11), at(6, 12)) == (False, "Closed at 11:00")


def test_past_midnight_and_always_open():
    bar = OpeningHours.parse("Tu-Sa 18:00-02:00")
    assert bar.status(at(2, 1), at(2, 1, 30)) == (True, "Open until 02:00")  # Wednesday night
    assert bar.status(at(0, 1), at(0, 2))[0] is False  # nothing from Sunday
    always = OpeningHours.parse("24/7")
    assert always.status(at(0, 23), at(1, 3)) == (True, "Open until 00:00")  # open for days on


def test_unknown_formats_give_up():
    for text in ("Jan-Mar Mo-Fr 09:00-17:00", "sunrise-sunset", "Mo-Fr 09:00+", "by appointment"):
        assert OpeningHours.parse(text) is None, text
    assert OpeningHours.parse("09:00-17:00").status(at(6, 10), at(6, 11))[0]  # every day


def test_best_match_prefers_the_named_business():
    street = parse_feature(feature(name="McDonald Street", osm_key="highway"))
    shop = parse_feature(feature(name="McDonald's", osm_key="amenity", osm_type="N", osm_id=7))
    assert places.best_match("McDonald's", [street, shop]) is shop
    assert places.best_match("Starbucks, Queen St", [parse_feature(feature(name="Starbucks", osm_key="amenity"))])
    assert places.best_match("12 Main Street", [street]) is street  # no business named: the first


def test_answers_are_remembered_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setattr(places, "_found", {"costco": parse_feature(feature(name="Costco", osm_key="shop"))})
    monkeypatch.setattr(places, "_details", {"W1": places.PlaceDetails("Costco", "Shop", "42 Overlea", "Mo-Fr 10:00-20:30"),
                                             "W2": None})
    monkeypatch.setattr(places, "_saved_at", {"details W2": 0})  # long ago: forgotten on loading
    places._save_cache()
    places._found.clear()
    places._details.clear()
    places.load_cache()
    assert places._found["costco"].name == "Costco"
    assert places._details["W1"].hours == "Mo-Fr 10:00-20:30" and "W2" not in places._details


def test_checker_asks_again_only_after_a_while(monkeypatch):
    checker = places.PlaceChecker({"online": True, "weather-place": None}, lambda: None)
    now = [1000.0]
    monkeypatch.setattr(places.clock, "monotonic", lambda: now[0])
    assert checker._ask_now("W1") and not checker._ask_now("W1")
    now[0] += places.RETRY_S + 1
    assert checker._ask_now("W1")


def test_what_is_worth_looking_up():
    for text in ("Zoom", "Office", "Google Meet", "https://meet.example.com/abc", "+1 (555) 010-2000", "TBD"):
        assert not places.worth_looking_up(text), text
    for text in ("Starbucks", "Café Luna, Montreal", "12 Main Street"):
        assert places.worth_looking_up(text), text


def test_names_match_whole_names_only():
    depot = parse_feature(feature(name="Office Depot", osm_key="shop"))
    tims = parse_feature(feature(name="Tim Hortons", osm_key="amenity"))
    assert not places.names_match("Office", depot) and not places.names_match("Tim", tims)
    assert places.names_match("Tim Hortons", tims) and places.names_match("tim hortons, college st", tims)
