from datetime import date, datetime

import pytest

from navcal import weather
from navcal.weather import Conditions, Forecast


@pytest.fixture(autouse=True)
def celsius():
    weather.set_unit("c")
    yield
    weather.set_unit("auto")

DATA = {
    "utc_offset_seconds": 3600,
    "current": {"temperature_2m": 13.6, "weather_code": 1, "is_day": 0},
    "hourly": {"time": ["2026-09-27T14:00", "2026-09-27T15:00"], "temperature_2m": [18.4, 17.9],
               "weather_code": [2, 61], "is_day": [1, 1]},
    "daily": {"time": ["2026-09-27"], "weather_code": [61], "temperature_2m_max": [19.2],
              "temperature_2m_min": [9.8]},
}


def test_parse_forecast():
    f = Forecast.parse(DATA)
    assert f.current.icon == "weather-few-clouds-night-symbolic" and f.current.temperature_text == "14°"
    day = f.days[date(2026, 9, 27)]
    assert (day.icon, day.summary, day.range_text) == ("weather-showers-symbolic", "Light rain", "19° / 10°")


def test_forecast_at_a_local_time():
    f = Forecast.parse(DATA)
    # 13:xx UTC is 14:xx at a place one hour ahead.
    utc_13 = datetime.fromtimestamp(datetime(2026, 9, 27, 13, 20).timestamp() + _local_offset())
    assert f.at(utc_13).temperature == 18.4


def _local_offset() -> float:
    """Seconds to add to a naive UTC time to get the same moment in local time."""
    return datetime(2026, 9, 27, 13).astimezone().utcoffset().total_seconds()


def test_unknown_code():
    assert Conditions(1234, 5).icon == "weather-overcast-symbolic"


def test_units():
    day = Conditions(61, 19.2, 9.8)
    assert day.range_text == "19° / 10°"
    weather.set_unit("f")
    assert (day.temperature_text, day.range_text) == ("67°", "67° / 50°")
