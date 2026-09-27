# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Public holiday calendars (Google's public iCalendar feeds, no account needed)."""

from __future__ import annotations

import locale
import os

from .i18n import N_, _

# (name, Google calendar id, ISO country codes it applies to). Every id here was
# checked to exist.
HOLIDAYS = [
    (N_("Argentina"), "ar", "AR"), (N_("Australia"), "australian", "AU"), (N_("Austria"), "austrian", "AT"),
    (N_("Belgium"), "be", "BE"), (N_("Brazil"), "brazilian", "BR"), (N_("Bulgaria"), "bulgarian", "BG"),
    (N_("Canada"), "canadian", "CA"), (N_("Chile"), "cl", "CL"), (N_("China"), "china", "CN"),
    (N_("Colombia"), "co", "CO"), (N_("Croatia"), "croatian", "HR"), (N_("Czechia"), "czech", "CZ"),
    (N_("Denmark"), "danish", "DK"), (N_("Egypt"), "eg", "EG"), (N_("Finland"), "finnish", "FI"),
    (N_("France"), "french", "FR"), (N_("Germany"), "german", "DE"), (N_("Greece"), "greek", "GR"),
    (N_("Hong Kong"), "hong_kong", "HK"), (N_("Hungary"), "hungarian", "HU"), (N_("India"), "indian", "IN"),
    (N_("Indonesia"), "indonesian", "ID"), (N_("Ireland"), "irish", "IE"), (N_("Italy"), "italian", "IT"),
    (N_("Japan"), "japanese", "JP"), (N_("Kenya"), "ke", "KE"), (N_("Malaysia"), "malaysia", "MY"),
    (N_("Mexico"), "mexican", "MX"), (N_("Netherlands"), "dutch", "NL"), (N_("New Zealand"), "new_zealand", "NZ"),
    (N_("Nigeria"), "ng", "NG"), (N_("Norway"), "norwegian", "NO"), (N_("Peru"), "pe", "PE"),
    (N_("Philippines"), "philippines", "PH"), (N_("Poland"), "polish", "PL"), (N_("Portugal"), "portuguese", "PT"),
    (N_("Romania"), "romanian", "RO"), (N_("Russia"), "russian", "RU"), (N_("Saudi Arabia"), "saudiarabian", "SA"),
    (N_("Singapore"), "singapore", "SG"), (N_("Slovakia"), "slovak", "SK"), (N_("South Africa"), "sa", "ZA"),
    (N_("South Korea"), "south_korea", "KR"), (N_("Spain"), "spain", "ES"), (N_("Sweden"), "swedish", "SE"),
    (N_("Switzerland"), "ch", "CH"), (N_("Taiwan"), "taiwan", "TW"), (N_("Thailand"), "th", "TH"),
    (N_("Turkey"), "turkish", "TR"), (N_("Ukraine"), "ukrainian", "UA"),
    (N_("United Arab Emirates"), "ae", "AE"), (N_("United Kingdom"), "uk", "GB"),
    (N_("United States"), "usa", "US"), (N_("Vietnam"), "vietnamese", "VN"),
    # Religious calendars
    (N_("Christian holidays"), "christian", ""), (N_("Islamic holidays"), "islamic", ""),
    (N_("Jewish holidays"), "jewish", "IL"),
]


def feed_url(calendar_id: str) -> str:
    return ("https://calendar.google.com/calendar/ical/"
            f"en.{calendar_id}%23holiday%40group.v.calendar.google.com/public/basic.ics")


def calendar_name(country: str) -> str:
    """The subscribed calendar's name, e.g. "Holidays in Canada"."""
    if country.endswith("holidays"):
        return _(country)
    return _("Holidays in {country}").format(country=_(country))


def default_index() -> int:
    """The user's country, guessed from the locale (en_CA.UTF-8 → Canada)."""
    for value in (os.environ.get("LC_ALL"), os.environ.get("LC_TIME"), os.environ.get("LANG"),
                  locale.getlocale()[0]):
        if value and "_" in value:
            country = value.split("_", 1)[1][:2].upper()
            for i, (_name, _id, codes) in enumerate(HOLIDAYS):
                if country and country in codes.split(","):
                    return i
    return next(i for i, h in enumerate(HOLIDAYS) if h[2] == "US")
