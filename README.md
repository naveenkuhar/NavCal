# Navcal

A calendar for GNOME, written in Python with GTK 4 and libadwaita. It follows the GNOME Human Interface Guidelines: adaptive layout, header bar, Adwaita dialogs, toasts with Undo, light and dark styles.

Navcal stores events in GNOME's calendar service (Evolution Data Server), the same one GNOME Calendar and the top-bar clock menu use. So it shows every calendar you already have, and syncs with accounts added in **Settings → Online Accounts**: Google, Microsoft 365, Exchange, Nextcloud, and iCloud or any CalDAV server (via "Calendar, Contacts and Files").

It stays quick with big calendars: years of history, many repeating events, thousands of events (see [Large calendars](#large-calendars)).

## Setup

Navcal uses the system's GTK bindings, so the virtual environment must see system packages:

```sh
sudo dnf install python3-gobject gtk4 libadwaita evolution-data-server   # all present on Fedora Workstation
uv venv --python /usr/bin/python3 --system-site-packages .venv
uv sync
```

## Run

```sh
uv run navcal              # or: .venv/bin/python -m navcal
uv run navcal --background # start hidden (used for start at login)
```

To try another language: `LANGUAGE=fr uv run navcal`.

The UI follows the GNOME Human Interface Guidelines; the rules the project holds itself to are in `CONTRIBUTING.md`.

On first run Navcal registers itself with the desktop: `~/.local/share/applications/io.github.navcal.Navcal.desktop` and a matching icon. That puts Navcal in the app grid, and GNOME Shell needs it to display notifications.

Preferences are in `~/.config/navcal/settings.json`. Events live in the calendar service. On first start, events from older Navcal versions are moved into a new "Navcal" calendar, and the old database is kept as `~/.local/share/navcal/navcal.db.backup`.

## Calendars

- **Preferences → Calendars** (`Ctrl+,`) lists every calendar by account. You can change colors, show or hide calendars (this also affects GNOME Calendar and the top bar), remove local calendars and subscriptions, create a new calendar on this computer, subscribe to a calendar link (webcal/https `.ics`), and open Online Accounts.
- **Import Events… / Export Events…** (on the same page) read and write standard `.ics` files. Exporting "All calendars" is a full backup, and importing it restores it. Importing takes seconds even for thousands of events, and importing a file again updates the events it added before instead of adding them twice.
- Events use their calendar's color unless you pick one for an event.
- **Add Public Holidays…** subscribes to your country’s holidays (Google’s public feed, no account needed).
- Birthdays & Anniversaries (from your contacts), holidays and subscribed calendars are read-only.

## Activities search

Navcal can show events when you search in the GNOME Activities overview. GNOME Shell only reads search providers from system folders, so this needs one command with administrator rights:

```sh
tools/install-search-provider.sh   # then log out and back in
```

## Using it

Views: **Day**, **Week** (or 3–4 days), **Month**, **Year** and **Agenda** (`Ctrl+1`…`Ctrl+5`).

| Action | How |
|---|---|
| New event | `Ctrl+N` or the + button, then type it: “Lunch with Sam tomorrow 1pm at Café Luna” (quick add; **Edit Details…** opens the full editor). Or double-click a day or time slot, or drag across time slots |
| Edit | Double-click an event, or select it and press `Enter` |
| Move | Drag an event to another time or day (any view) |
| Make all-day / timed | Drag an event between the all-day row and the time grid |
| Resize | Drag the bottom edge of an event in Day/Week view |
| Duplicate | `Ctrl`+drag an event (a faded copy follows the pointer and an outline shows where it lands; press or let go of `Ctrl` mid-drag to switch between moving and copying), or `Ctrl+D` |
| Select several | `Ctrl`+click or `Shift`+click events; `Ctrl+A` selects all visible, `Esc` clears |
| Copy / cut / paste | `Ctrl+C` / `Ctrl+X`, click a day or time slot, `Ctrl+V` (several events keep their spacing) |
| Delete | `Delete`; the toast offers Undo |
| Undo / redo | `Ctrl+Z` / `Ctrl+Shift+Z` (last 50 changes, kept after restarting) |
| Search | `Ctrl+F`, or just start typing |
| Go to a date | `Ctrl+G`; `Ctrl+T` for today, `Alt+←` / `Alt+→` for previous / next |
| Zoom the hours | Pinch, `Ctrl`+scroll, or `Ctrl++` / `Ctrl+−` / `Ctrl+0` in the day and week views |
| Print | `Ctrl+P` (main menu), see [Printing](#printing) |
| Keyboard only | Arrow keys move between days (and times in Day/Week view), `Space` selects the next event on that day, `Enter` opens it or creates one |
| All shortcuts | `Ctrl+?` |

Right-click (or long-press) for a context menu. Recurring events ask whether a change applies to only this event, this and following, or all events. When several events are selected, deleting or cutting affects only the selected occurrences.

The **sidebar** (shown or hidden with the button at the top left) has a small month calendar to jump to any day (days with events have a dot), **Upcoming** events for the next 14 days, your **Tasks** and **World Clocks**. Upcoming events and tasks show the first five, with **Show More** for the rest. In a narrow window the sidebar slides over the calendar instead of sitting beside it.

The event editor supports:
- **Repeat rules:** every N days, weeks, months or years; specific weekdays (Mon/Wed/Fri); monthly on a day number, "the second Tuesday" or "the last Friday"; ending never, on a date, or after N times. Rules made in other apps that Navcal can't edit are kept as they are.
- **Time zones:** each event can have its own zone. The editor shows the time in that zone plus "Your time", and daylight saving is handled.
- **Several reminders per event.**
- **Overlap warning** when the event clashes with another one.
- **Location** with address suggestions as you type, **Show on map** (GNOME Maps, or OpenStreetMap in the browser), the **forecast** there when the event starts, and for shops, cafés and the like, their **opening hours**, with a warning when they're closed or close before the event ends (see [Weather and places](#weather-and-places)).
- **Links and files** attached to the event.
- **Travel time:** a preset or any number of minutes (**Custom…**), shown as a block before the event in the day and week views, with a “Time to Leave” notification. Stored the way Apple Calendar does, so it syncs with iPhones and Macs.

Quick add understands English phrasing: dates (today, tomorrow, friday, next friday, sep 30, 9/30, in 3 days), times (1pm, 13:00, noon, 1-2pm, from 9 to 11am, morning), lengths (for 2 hours), “all day”, repeats (every day, every monday and thursday, weekly, every other week) and places (@ Room 4, or “at” followed by a name). The preview under the text shows what it understood.

## Printing

`Ctrl+P` opens a preview of the page as it will print. Choose what to show (a calendar, an agenda, or both), where the calendar goes next to the agenda (left, right, above or below), the period (a day, a week or a month; it starts as the view you're in), portrait or landscape, checkboxes to tick off in the agenda, and event colors. The calendar part is a timeline for a day, a planner for a week, and a grid for a month. A long agenda carries on over more pages. **Print…** opens the system print dialog, where you can also print to a PDF file. **Save as Image…** saves a PNG instead, as tall as its content needs.

## Bottom bar

The bar at the bottom of the window works like a dock: in **Preferences → Bottom Bar** (or its gear menu, **Customize Bottom Bar…**), choose what it shows and in what order. It can hold a button per calendar to show or hide it, the next event, how many tasks are due today, the weather now, your world clocks, and New Event, Today, Search, Go to Date and Print buttons. When it's full, it scrolls sideways.

## Weather and places

With a place set under **Preferences → Weather and Places**, the forecast for about the next two weeks shows under each date in the day, week and month views, with the day's high and low (just the high where there's no room), and the weather now, if the bottom bar has it. Temperatures are in °C or °F depending on your region, or as you choose there.

Events with a location get the forecast for when they start. When the location is a business (a café, a shop, a pharmacy…), the event editor says what it found ("Café · 12 Main Street, Montreal") and whether it's open then; if it's closed, or closes before the event ends, a warning sign shows on the event in the day, week and month views, with the reason in the Agenda view (and inside the event, in the day and week views, when there's room). A location typed by hand is matched to the nearest business with that name: near your weather place, or else your time zone's city. Pick a suggestion while typing to be sure it's the right one. Not every place lists its opening hours on OpenStreetMap. Locations such as “Office”, “Zoom” or “Online”, web links and phone numbers are never looked up, and answers are remembered for a few days (in `~/.cache/navcal/places.json`) so the same place isn't looked up again.

These need the internet: address suggestions and opening hours come from OpenStreetMap (Photon and Nominatim), forecasts from Open-Meteo. The locations you type are sent to them. Turn off **Look up places and weather online** to keep everything on your computer. A place picked from the suggestions is saved with the event (as the standard iCalendar `GEO` property), so the lookup doesn't have to guess. Places © OpenStreetMap contributors; weather data by Open-Meteo.com.

## Tasks

The sidebar lists your tasks with a check box each, from the same task lists GNOME’s task apps and Nextcloud or Google Tasks use. Type in **New task** to add one; it understands due dates too (“Buy milk tomorrow”, “Call Sam 5pm”). Click a task to change its due date, list or notes. Tasks due on a day also appear in the Agenda view next to that day’s events.

## World clocks

Add cities under **World Clocks** in the sidebar to see their current time and how far ahead or behind they are. Preferences can also show a second time zone next to the hours in the day and week views.

## Accessibility

Everything works from the keyboard (see `Ctrl+?`). The custom-drawn views describe themselves to screen readers: each day is a list of its events, and moving with the arrow keys announces the day, time or event.

## Translations

All text is translatable (gettext; `po/`). French is included, and still needs a review by a native speaker. To add a language, add its code to `po/LINGUAS` and run `tools/update-translations.sh`, then fill in `po/<code>.po`.

## Preferences

Preferences (`Ctrl+,`) apply right away:
- **New events:** default reminder and default length.
- **Calendar view:** first day of the week, whether the week view shows 7, 4 or 3 days, hiding weekends, week numbers, and a second time zone next to the hours.
- **Weather and places:** looking things up online, showing the weather, and the weather's place.
- **Working hours:** shade the time outside them in the day and week views, with their start and end.
- **Background:** run in background and start at login (see below).
- **Bottom Bar:** showing the bar, and what's in it (see [Bottom bar](#bottom-bar)).
- **Calendars:** everything under [Calendars](#calendars).

## Background mode

Reminders are desktop notifications with a **Snooze 5 Minutes** button, sent while Navcal is running. They include reminders set in other apps (such as Google Calendar on your phone). Reminders that came due while Navcal was closed are shown as "Missed" on the next start (up to 2 days back). If GNOME's own reminder service (evolution-alarm-notify) is running, Navcal leaves reminders to it.

By default closing the window (`Ctrl+W`) keeps Navcal running hidden; opening it again shows the window (only one copy ever runs). Quit with `Ctrl+Q`.

In Preferences (`Ctrl+,`):
- **Run in Background**: turn off to make closing the window quit Navcal.
- **Start at Login**: adds `~/.config/autostart/io.github.navcal.Navcal.desktop`, which starts Navcal hidden.

## Large calendars

Navcal is tested with a calendar of about 8,500 events: ten years of history, 150 repeating events with edited and skipped occurrences, and 300 yearly birthdays. With it, moving between days, weeks and months, redrawing while dragging, and saving a change each take a few milliseconds up to a few tens of milliseconds, and importing all of it takes about 3 seconds. What makes that work:

- **Repeating events** are worked out a year at a time and kept until the event changes. For a series that began years ago, Navcal skips ahead to that year instead of stepping through every occurrence since the first (rules that end after a number of times are stepped through as usual).
- **One-off events** are kept sorted by start time, so finding a week's events doesn't look at all of them.
- **Drawn text** is laid out once and reused between frames.
- **The Agenda** only rebuilds the days whose events changed. A long agenda shows its first days at once and builds the rest between frames.
- **Imports** go to the calendar service 200 events at a time.

## Development

```sh
uv run pytest              # unit tests: event model, iCalendar conversion, repeat rules,
                           # quick add, world clocks, places, weather, UI files, translations
tests/run-eds-tests.sh     # calendar-service tests: saving, repeating events, import/export,
                           # reminders, subscriptions and tasks, in a private throwaway session
tools/build-resources.sh   # after editing src/navcal/ui/*.ui, style.css or icons
tools/update-translations.sh  # after changing any user-visible text
```

The calendar-service tests never touch your own calendars: they run their own D-Bus session with temporary folders. Among the unit tests, `test_ical.py` checks that skipping ahead in long-running series gives exactly the same occurrences as stepping through them, for many kinds of repeat rules, time zones (including daylight saving changes) and all-day events.

## Layout

```
src/navcal/
  models.py       Event / Occurrence model (no UI)
  history.py      undo/redo history saved to ~/.local/state/navcal/history.json
  printing.py     printouts: calendar and agenda layouts, pages, PDF and image output
  print_dialog.py the print dialog with its preview
  dock.py         the bottom bar and its Preferences page
  places.py       address search, opening hours (OpenStreetMap)
  place_search.py address suggestions under an entry
  weather.py      forecasts (Open-Meteo)
  online.py       small background web requests
  holidays.py     public holiday feeds
  search_provider.py  GNOME Shell search provider (D-Bus)
  quickadd.py     parses “Lunch with Sam tomorrow 1pm”
  tasks.py        task dialog and task rows
  worldclock.py   world clock helpers
  i18n.py         gettext setup (po/ has the translations)
  eds.py          calendar-service backend: calendars, live cache, recurrence, saving, undo, import/export
  ical.py         Event <-> iCalendar conversion, time zones, repeat rules
  calendars.py    Manage Calendars page, new calendar / subscribe dialogs, import/export
  legacy.py       reads the pre-0.4 SQLite database for migration
  layout.py       overlap/lane layout and drag state (no UI)
  ui/             GtkBuilder files: window, editor, preferences, task dialog, print dialog,
                  shortcuts dialog
  icons/          custom symbolic icons
  style.css       size rules for color swatches (compiled into the GResource)
  resources.py    loads data/navcal.gresource (built by tools/build-resources.sh)
  locale/         compiled translations (built by tools/update-translations.sh)
  application.py  Adw.Application: actions, preferences, about, shortcuts
  window.py       main window: header bar, sidebar, event actions, undo
  editor.py       event editor dialog, recurring-event prompt
  views/          canvas.py (pointer and keyboard handling), timegrid.py (day, week),
                  month.py, year.py, agenda.py
  draw.py         drawing helpers; fonts and colors read from libadwaita style classes
  reminders.py    notification timer
  desktop.py      .desktop entry, icon, start at login
  style.py        generated CSS classes for user-chosen calendar/event colors
  config.py       preferences file
po/               translations (navcal.pot template, fr.po)
tests/            unit tests, and calendar-service tests run by run-eds-tests.sh
tools/            build-resources.sh, update-translations.sh, install-search-provider.sh
```
