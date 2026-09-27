from datetime import datetime

from navcal import ical, worldclock


def test_city_name():
    assert worldclock.city_name("America/New_York") == "New York"


def test_describe_same_zone():
    local = ical.local_tzid()
    now = datetime(2026, 9, 24, 12, 0)
    time_text, detail = worldclock.describe(local, now)
    assert detail == "Today · Same time"


def test_offset_from_known_pair():
    # Tokyo is 13 hours ahead of Toronto in late September (EDT, no DST in Japan).
    now = datetime(2026, 9, 24, 12, 0)
    expected = (ical.to_zone(now, "Asia/Tokyo") - now).total_seconds() / 3600
    assert worldclock.offset_hours("Asia/Tokyo", now) == round(expected * 4) / 4
    assert worldclock.abbreviation("Asia/Tokyo", now) == "JST"
