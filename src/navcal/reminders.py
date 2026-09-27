# SPDX-FileCopyrightText: 2026 Nave Kuhar
# SPDX-License-Identifier: GPL-3.0-or-later

"""Desktop notifications for event reminders.

Reminders come from the events' VALARMs, so reminders set in other apps
(Google Calendar on a phone, Evolution, …) work too. Reminders that came due
while Navcal wasn't running are shown on the next start, and every
notification has a Snooze button.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta

from gi.repository import Gio, GLib

from .config import Config
from .draw import fmt_time
from .i18n import _, ngettext
from .eds import Backend
from .models import Occurrence

CHECK_INTERVAL_S = 20
MAX_CATCH_UP = timedelta(days=2)  # don't replay reminders older than this
SNOOZE_MINUTES = 5
MAX_REMINDER = timedelta(weeks=2)  # how far ahead to look for reminders
SAVE_INTERVAL = timedelta(minutes=5)  # how often to remember the last check when nothing fired
GNOME_ALARMS = "org.gnome.Evolution-alarm-notify"


def describe(occ: Occurrence, now: datetime) -> str:
    ev = occ.event
    if ev.all_day:
        when = _("Today") if occ.start.date() == now.date() else occ.start.strftime(_("%A, %B %-d"))
    else:
        mins = math.ceil((occ.start - now).total_seconds() / 60)
        if mins < -1:
            if occ.start.date() == now.date():
                when = _("Started at {time}").format(time=fmt_time(occ.start))
            else:
                when = _("Started at {time} on {day}").format(
                    time=fmt_time(occ.start), day=occ.start.strftime(_("%A, %B %-d")))
        elif mins <= 0:
            when = _("Now, {time}").format(time=fmt_time(occ.start))
        elif mins < 60:
            when = ngettext("In {n} minute, at {time}", "In {n} minutes, at {time}", mins).format(
                n=mins, time=fmt_time(occ.start))
        else:
            when = _("{day} at {time}").format(day=occ.start.strftime(_("%A, %B %-d")),
                                               time=fmt_time(occ.start))
    return f"{when}\n{ev.location}" if ev.location else when


def evolution_alarms_running() -> bool:
    """GNOME's own reminder daemon; if it runs, it already shows these reminders."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                              "org.freedesktop.DBus", "NameHasOwner",
                              GLib.Variant("(s)", (GNOME_ALARMS,)),
                              GLib.VariantType("(b)"), Gio.DBusCallFlags.NONE, 500, None)
        return reply.unpack()[0]
    except GLib.Error:
        return False


class ReminderService:
    def __init__(self, app: Gio.Application, backend: Backend, config: Config):
        self.app = app
        self.backend = backend
        self.config = config
        last = config["reminders-checked"]
        now = datetime.now()
        self._last_check = max(datetime.fromisoformat(last), now - MAX_CATCH_UP) if last else now
        self._started = now
        self._saved_check = self._last_check
        self._snoozed: list[dict] = list(config["snoozed"] or [])
        self._ready = False
        # Checked once now, then kept up to date as the daemon starts and stops.
        self.gnome_alarms = evolution_alarms_running()
        Gio.bus_watch_name(Gio.BusType.SESSION, GNOME_ALARMS, Gio.BusNameWatcherFlags.NONE,
                           lambda *_args: setattr(self, "gnome_alarms", True),
                           lambda *_args: setattr(self, "gnome_alarms", False))
        # Wait for calendars to load before looking for missed reminders.
        backend.connect("calendars-changed", self._on_calendars)
        GLib.timeout_add_seconds(CHECK_INTERVAL_S, self._tick)

    def _on_calendars(self, *_args):
        if not self._ready and self.backend.calendars and all(
                c.loaded or c.error for c in self.backend.calendars.values()):
            self._ready = True
            GLib.idle_add(lambda: self.check() and False)

    def _tick(self) -> bool:
        if self._ready:
            self.check()
        return GLib.SOURCE_CONTINUE

    def check(self) -> list[Occurrence]:
        now = datetime.now()
        since = self._last_check
        self._last_check = now
        self._fire_snoozed(now)
        # GNOME's daemon shows the events' own reminders, but knows nothing of travel time.
        reminders = not self.gnome_alarms
        due = []
        for occ in self.backend.occurrences(since - timedelta(days=1), now + MAX_REMINDER):
            travel = occ.event.travel_minutes
            leave_at = occ.start - timedelta(minutes=travel)
            if travel and not occ.event.all_day and since < leave_at <= now:
                self.notify_leave(occ, now)
                due.append(occ)
            if not reminders:
                continue
            for minutes in occ.event.reminders:
                fire_at = occ.start - timedelta(minutes=minutes)
                if since < fire_at <= now:
                    missed = fire_at < self._started
                    self.notify(occ, now, missed)
                    due.append(occ)
                    break  # one notification per occurrence per check
        # Save at once after a notification, so a restart doesn't show it again.
        if due or now - self._saved_check >= SAVE_INTERVAL:
            self.save()
        return due

    def save(self) -> None:
        """Remember when reminders were last checked, for finding missed ones on the next start."""
        self._saved_check = self._last_check
        self.config["reminders-checked"] = self._last_check.isoformat(timespec="seconds")

    def notify(self, occ: Occurrence, now: datetime, missed: bool = False) -> None:
        title = _("Missed: {title}").format(title=occ.event.title) if missed else occ.event.title
        self._send(title, describe(occ, now), occ.start.date().isoformat(),
                   f"reminder-{occ.event.calendar}-{occ.event.uid}-{occ.start.isoformat()}")

    def notify_leave(self, occ: Occurrence, now: datetime) -> None:
        """The travel time has started: time to set off."""
        body = _("“{title}” starts at {time}").format(title=occ.event.title, time=fmt_time(occ.start))
        if occ.event.location:
            body += f"\n{occ.event.location}"
        self._send(_("Time to Leave"), body, occ.start.date().isoformat(),
                   f"leave-{occ.event.calendar}-{occ.event.uid}-{occ.start.isoformat()}")

    def _send(self, title: str, body: str, day: str, notification_id: str) -> None:
        note = Gio.Notification.new(title)
        note.set_body(body)
        note.set_default_action_and_target("app.show-date", GLib.Variant.new_string(day))
        payload = json.dumps({"title": title, "body": body, "day": day,
                              "id": notification_id})
        note.add_button_with_target(_("Snooze {n} Minutes").format(n=SNOOZE_MINUTES), "app.snooze",
                                          GLib.Variant.new_string(payload))
        self.app.send_notification(notification_id, note)

    def snooze(self, payload: str) -> None:
        item = json.loads(payload)
        item["at"] = (datetime.now() + timedelta(minutes=SNOOZE_MINUTES)).isoformat(timespec="seconds")
        self.app.withdraw_notification(item["id"])
        self._snoozed.append(item)
        self.config["snoozed"] = self._snoozed

    def _fire_snoozed(self, now: datetime) -> None:
        due = [s for s in self._snoozed if datetime.fromisoformat(s["at"]) <= now]
        if not due:
            return
        self._snoozed = [s for s in self._snoozed if s not in due]
        self.config["snoozed"] = self._snoozed
        for item in due:
            self._send(item["title"], item["body"], item["day"], item["id"])
