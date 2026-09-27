# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Weather forecasts from Open-Meteo (open-meteo.com): no account or key needed.
One place's forecast (chosen in Preferences) is shown under the days, and events
with a location get the forecast for when they start."""

from __future__ import annotations

import os
import time as clock
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable

from gi.repository import Gio, GLib, GObject

from . import online
from .i18n import N_, _

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 16
FRESH_S = 3600  # forecasts are fetched again after an hour
RETRY_S = 300  # after a failed download (offline, say)
# WMO weather codes: (icon, summary)
CODES = {
    0: ("weather-clear", N_("Clear")), 1: ("weather-few-clouds", N_("Mostly clear")),
    2: ("weather-few-clouds", N_("Partly cloudy")), 3: ("weather-overcast", N_("Cloudy")),
    45: ("weather-fog", N_("Fog")), 48: ("weather-fog", N_("Fog")),
    51: ("weather-showers-scattered", N_("Drizzle")), 53: ("weather-showers-scattered", N_("Drizzle")),
    55: ("weather-showers-scattered", N_("Drizzle")), 56: ("weather-showers-scattered", N_("Freezing drizzle")),
    57: ("weather-showers-scattered", N_("Freezing drizzle")),
    61: ("weather-showers", N_("Light rain")), 63: ("weather-showers", N_("Rain")),
    65: ("weather-showers", N_("Heavy rain")), 66: ("weather-showers", N_("Freezing rain")),
    67: ("weather-showers", N_("Freezing rain")),
    71: ("weather-snow", N_("Light snow")), 73: ("weather-snow", N_("Snow")), 75: ("weather-snow", N_("Heavy snow")),
    77: ("weather-snow", N_("Snow grains")),
    80: ("weather-showers", N_("Showers")), 81: ("weather-showers", N_("Showers")),
    82: ("weather-showers", N_("Heavy showers")), 85: ("weather-snow", N_("Snow showers")),
    86: ("weather-snow", N_("Snow showers")),
    95: ("weather-storm", N_("Thunderstorm")), 96: ("weather-storm", N_("Thunderstorm with hail")),
    99: ("weather-storm", N_("Thunderstorm with hail")),
}
FAHRENHEIT_COUNTRIES = {"US", "LR", "MM", "BS", "BZ", "KY", "PW"}
_unit = "auto"  # the Preferences choice: "auto" (from the region), "c" or "f"


def set_unit(unit: str) -> None:
    global _unit
    _unit = unit


def uses_fahrenheit() -> bool:
    if _unit in ("c", "f"):
        return _unit == "f"
    for value in (os.environ.get("LC_MEASUREMENT"), os.environ.get("LC_ALL"), os.environ.get("LANG")):
        if value and "_" in value:
            return value.split("_", 1)[1][:2].upper() in FAHRENHEIT_COUNTRIES
    return False


def degrees(celsius: float) -> int:
    """A temperature in the chosen unit (forecasts are fetched in Celsius)."""
    return round(celsius * 9 / 5 + 32 if uses_fahrenheit() else celsius)


@dataclass
class Conditions:
    code: int
    temperature: float  # °C; the high, for a whole day
    low: float | None = None  # °C, for a whole day
    day: bool = True  # daylight (for the sun or moon icon)

    @property
    def icon(self) -> str:
        name = CODES.get(self.code, ("weather-overcast", ""))[0]
        if not self.day and name in ("weather-clear", "weather-few-clouds"):
            name += "-night"
        return name + "-symbolic"

    @property
    def summary(self) -> str:
        return _(CODES.get(self.code, ("", N_("Cloudy")))[1])

    @property
    def temperature_text(self) -> str:
        return _("{degrees}°").format(degrees=degrees(self.temperature))

    @property
    def range_text(self) -> str:
        """The day's high and low: "19° / 10°"."""
        if self.low is None:
            return self.temperature_text
        return _("{high}° / {low}°").format(high=degrees(self.temperature), low=degrees(self.low))


@dataclass
class Forecast:
    offset: timedelta  # the place's time zone: its clock is UTC plus this
    hours: dict[datetime, Conditions]  # by the hour, in the place's own time
    days: dict[date, Conditions]
    current: Conditions | None
    fetched: float

    @classmethod
    def parse(cls, data: dict) -> Forecast:
        hourly, daily, now = data.get("hourly", {}), data.get("daily", {}), data.get("current")
        hours = {datetime.fromisoformat(t): Conditions(int(c), float(temp), day=bool(d))
                 for t, c, temp, d in zip(hourly.get("time", []), hourly.get("weather_code", []),
                                          hourly.get("temperature_2m", []), hourly.get("is_day", []))
                 if c is not None and temp is not None}
        days = {date.fromisoformat(t): Conditions(int(c), float(high), float(low))
                for t, c, high, low in zip(daily.get("time", []), daily.get("weather_code", []),
                                           daily.get("temperature_2m_max", []),
                                           daily.get("temperature_2m_min", []))
                if c is not None and high is not None and low is not None}
        current = (Conditions(int(now["weather_code"]), float(now["temperature_2m"]), day=bool(now.get("is_day", 1)))
                   if now and now.get("weather_code") is not None else None)
        return cls(timedelta(seconds=data.get("utc_offset_seconds", 0)), hours, days, current,
                   clock.monotonic())

    def at(self, when: datetime) -> Conditions | None:
        """The forecast for a local time (the computer's), wherever the place is."""
        utc = datetime.fromtimestamp(when.timestamp(), timezone.utc).replace(tzinfo=None)
        there = (utc + self.offset).replace(minute=0, second=0, microsecond=0)
        return self.hours.get(there)

    @property
    def fresh(self) -> bool:
        return clock.monotonic() - self.fetched < FRESH_S


_cache: dict[tuple[float, float], Forecast] = {}
_waiting: dict[tuple[float, float], list[Callable[[Forecast | None], None]]] = {}


def forecast(lat: float, lon: float, done: Callable[[Forecast | None], None]) -> None:
    """The forecast for a place: from the last hour's download, or fetched now."""
    key = (round(lat, 2), round(lon, 2))  # about a kilometer
    cached = _cache.get(key)
    if cached and cached.fresh:
        done(cached)
        return
    if key in _waiting:
        _waiting[key].append(done)
        return
    _waiting[key] = [done]
    params = {"latitude": key[0], "longitude": key[1], "timezone": "auto", "forecast_days": FORECAST_DAYS,
              "hourly": "temperature_2m,weather_code,is_day", "current": "temperature_2m,weather_code,is_day",
              "daily": "weather_code,temperature_2m_max,temperature_2m_min"}

    def answered(data):
        result = None
        if isinstance(data, dict):
            try:
                result = _cache[key] = Forecast.parse(data)
            except (KeyError, TypeError, ValueError):
                result = None
        for callback in _waiting.pop(key, []):
            callback(result or cached)

    online.get_json(FORECAST_URL, params, answered)


class WeatherService(GObject.Object):
    """The forecast for the place set in Preferences, kept fresh.
    Emits "changed" when it arrives or the place changes."""

    __gsignals__ = {"changed": (GObject.SignalFlags.RUN_LAST, None, ())}

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._forecast: Forecast | None = None
        self._place = None  # the place self._forecast is for
        self._retry = 0
        GLib.timeout_add_seconds(FRESH_S, self._hourly)
        network = Gio.NetworkMonitor.get_default()
        network.connect("network-changed", lambda _m, available: available and self.refresh())
        self.refresh()

    @property
    def enabled(self) -> bool:
        return bool(self.config["online"] and self.config["weather"] and self.config["weather-place"])

    def refresh(self) -> None:
        place = self.config["weather-place"] if self.enabled else None
        if place != self._place and self._forecast:  # turned off, or another place
            self._forecast = None
            self.emit("changed")
        self._place = place
        if not place:
            return

        def arrived(result):
            if result is not None and place == self._place:
                self._forecast = result
                self.emit("changed")
            elif result is None and not self._retry:  # try again soon, not in an hour
                self._retry = GLib.timeout_add_seconds(RETRY_S, self._retry_now)

        forecast(place["lat"], place["lon"], arrived)

    def _retry_now(self) -> bool:
        self._retry = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _hourly(self) -> bool:
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def now(self) -> Conditions | None:
        """The forecast for this hour (fresher than "current" from the last download)."""
        if not self.enabled or not self._forecast:
            return None
        return self._forecast.at(datetime.now()) or self._forecast.current

    def day(self, d: date) -> Conditions | None:
        return self._forecast.days.get(d) if self.enabled and self._forecast else None
