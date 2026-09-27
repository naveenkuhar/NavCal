// SPDX-FileCopyrightText: 2026 Nave Kuhar
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Navcal's quick add, for the website's demo: the same rules as
// src/navcal/quickadd.py, turning "Lunch with Sam tomorrow 1pm at Café Luna"
// into an event. English phrasing only.

const NavcalQuickAdd = (() => {
const WEEKDAYS = {monday: 0, mon: 0, tuesday: 1, tue: 1, tues: 1, wednesday: 2, wed: 2,
  thursday: 3, thu: 3, thur: 3, thurs: 3, friday: 4, fri: 4, saturday: 5, sat: 5, sunday: 6, sun: 6};
const MONTHS = {};
[["january", "jan"], ["february", "feb"], ["march", "mar"], ["april", "apr"], ["may"], ["june", "jun"],
 ["july", "jul"], ["august", "aug"], ["september", "sep", "sept"], ["october", "oct"],
 ["november", "nov"], ["december", "dec"]].forEach((names, i) => names.forEach(n => { MONTHS[n] = i + 1; }));
const PARTS_OF_DAY = {morning: [9, 0], afternoon: [14, 0], evening: [18, 0], tonight: [19, 0],
  noon: [12, 0], midnight: [0, 0]};

const byLength = obj => Object.keys(obj).sort((a, b) => b.length - a.length).join("|");
const WD = byLength(WEEKDAYS);
const MO = byLength(MONTHS);
const T = String.raw`(?:\d{1,2}(?::\d{2})?\s*(?:am|pm|a|p)\b|\d{1,2}:\d{2}|noon|midnight)`;
const T_LOOSE = String.raw`(?:\d{1,2}(?::\d{2})?\s*(?:am|pm|a|p)?|noon|midnight)`;

// Days are plain dates; "weekday" counts from Monday = 0, as in Python.
const addDays = (d, n) => new Date(d.getFullYear(), d.getMonth(), d.getDate() + n);
const weekday = d => (d.getDay() + 6) % 7;
const atTime = (d, [h, m]) => new Date(d.getFullYear(), d.getMonth(), d.getDate(), h, m);
const minutes = n => n * 60000;

// The input, with recognized phrases blanked out as they're used.
class Text {
  constructor(text) {
    this.original = text;
    this.lower = [...text].map(c => (c.toLowerCase().length === 1 ? c.toLowerCase() : c)).join("");
    this.free = Array(text.length).fill(true);
  }
  find(pattern) {
    const re = new RegExp(pattern, "g");
    let m;
    while ((m = re.exec(this.lower)) !== null) {
      if (this.free.slice(m.index, m.index + m[0].length).every(Boolean)) return m;
      if (m[0].length === 0) re.lastIndex++;
    }
    return null;
  }
  take(m) {
    this.takeRange(m.index, m.index + m[0].length);
  }
  takeRange(start, end) {
    for (let i = start; i < end; i++) this.free[i] = false;
  }
  rest() {
    return [...this.original].map((c, i) => (this.free[i] ? c : " ")).join("");
  }
}

function parseTime(s, hint) {
  s = s.trim().toLowerCase();
  if (s in PARTS_OF_DAY) return [PARTS_OF_DAY[s], null];
  const m = /^(\d{1,2})(?::(\d{2}))?\s*(am|pm|a|p)?$/.exec(s);
  if (!m) return null;
  let hour = +m[1];
  const minute = +(m[2] || 0);
  const meridiem = (m[3] || hint || "").slice(0, 1);
  if (hour > 23 || minute > 59) return null;
  if (meridiem === "p" && hour < 12) hour += 12;
  else if (meridiem === "a" && hour === 12) hour = 0;
  return [[hour, minute], meridiem || null];
}

const cmpTime = (a, b) => a[0] * 60 + a[1] - (b[0] * 60 + b[1]);

function nextWeekday(today, wd, skipWeek) {
  return addDays(today, ((wd - weekday(today)) % 7 + 7) % 7 + (skipWeek ? 7 : 0));
}

function withYear(month, dom, year, today) {
  const d = new Date(year || today.getFullYear(), month - 1, dom);
  if (d.getMonth() !== month - 1) return null; // no such day
  if (!year && d < today) d.setFullYear(d.getFullYear() + 1); // "jan 5" in December: next January
  return d;
}

// Does this locale write dates month first (9/30) or day first (30/9)?
function monthFirst() {
  const parts = new Intl.DateTimeFormat().formatToParts(new Date(2000, 11, 31));
  return parts.findIndex(p => p.type === "month") < parts.findIndex(p => p.type === "day");
}

function parse(text, now = new Date(), defaultMinutes = 60) {
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const t = new Text(text);
  let day = null, startTime = null, endTime = null, duration = null, allDay = false;
  const ev = {title: "", start: now, end: now, allDay: false, location: "", freq: "none", interval: 1, byday: []};
  let m;

  // Repeats
  if ((m = t.find(String.raw`\bevery\s+((?:(?:${WD})s?)(?:\s*(?:,|and)\s*(?:${WD})s?)*)\b`))) {
    const days = m[1].match(new RegExp(WD, "g"));
    ev.freq = "weekly";
    ev.byday = [...new Set(days.map(d => WEEKDAYS[d]))].sort();
    t.take(m);
  } else if ((m = t.find(String.raw`\bevery\s+(other\s+)?(day|weekday|week|month|year)\b|\b(daily|weekly|monthly|yearly|annually)\b`))) {
    const unit = m[2] || m[3];
    ev.freq = {day: "daily", daily: "daily", weekday: "weekly", week: "weekly", weekly: "weekly",
      month: "monthly", monthly: "monthly", year: "yearly", yearly: "yearly", annually: "yearly"}[unit];
    if (unit === "weekday") ev.byday = [0, 1, 2, 3, 4];
    ev.interval = m[1] ? 2 : 1;
    t.take(m);
  }

  // Length and all-day
  if ((m = t.find(String.raw`\ball[- ]day\b`))) { allDay = true; t.take(m); }
  if ((m = t.find(String.raw`\bfor\s+(half an hour|an hour|(\d+(?:\.\d+)?)\s*(h|hrs?|hours?|m|mins?|minutes?))\b`))) {
    if (m[1] === "half an hour") duration = 30;
    else if (m[1] === "an hour") duration = 60;
    else duration = m[3].startsWith("h") ? parseFloat(m[2]) * 60 : parseFloat(m[2]);
    t.take(m);
  }

  // Times: a range first, then a single time, then parts of the day
  if ((m = t.find(String.raw`\b(?:from\s+)?(${T_LOOSE})\s*(?:-|–|to|until|till)\s*(${T})`))) {
    const second = parseTime(m[2]), first = parseTime(m[1]);
    if (first && second) {
      startTime = first[0];
      endTime = second[0];
      if (first[1] === null && second[1]) { // "1-2pm" is 1 PM, but "9-5pm" is 9 AM
        const hinted = parseTime(m[1], second[1])[0];
        if (cmpTime(hinted, endTime) <= 0) startTime = hinted;
      }
      t.take(m);
    }
  }
  if (startTime === null && (m = t.find(String.raw`\b(?:at\s+)?(${T})`))) {
    const parsed = parseTime(m[1]);
    if (parsed) { startTime = parsed[0]; t.take(m); }
  }
  if (startTime === null && (m = t.find(String.raw`\bat\s+(\d{1,2})\b(?!\s*[/.:])`))) {
    const hour = +m[1];
    if (hour >= 1 && hour <= 12) { startTime = [hour < 8 ? hour + 12 : hour, 0]; t.take(m); } // "at 3" is 3 PM
  }
  if (startTime === null && (m = t.find(String.raw`\b(?:in the\s+|this\s+)?(morning|afternoon|evening|tonight)\b`))) {
    startTime = PARTS_OF_DAY[m[1]];
    if (m[1] === "tonight") day = today;
    t.take(m);
  }

  // Dates
  if ((m = t.find(String.raw`\b(?:the\s+)?day after tomorrow\b`))) {
    day = addDays(today, 2); t.take(m);
  } else if ((m = t.find(String.raw`\b(today|tomorrow|tmrw|tmr)\b`))) {
    day = m[1] === "today" ? today : addDays(today, 1); t.take(m);
  } else if ((m = t.find(String.raw`\b(?:on\s+)?(?:(next|this)\s+)?(${WD})\b`))) {
    day = nextWeekday(today, WEEKDAYS[m[2]], m[1] === "next"); t.take(m);
  } else if ((m = t.find(String.raw`\b(?:on\s+)?(${MO})\.?\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s+(\d{4}))?\b`))) {
    day = withYear(MONTHS[m[1]], +m[2], +(m[3] || 0) || null, today); t.take(m);
  } else if ((m = t.find(String.raw`\b(?:on\s+)?(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(${MO})\b(?:,?\s+(\d{4}))?`))) {
    day = withYear(MONTHS[m[2]], +m[1], +(m[3] || 0) || null, today); t.take(m);
  } else if ((m = t.find(String.raw`\b(?:on\s+)?(\d{1,2})[/.](\d{1,2})(?:[/.](\d{2,4}))?\b`))) {
    const a = +m[1], b = +m[2];
    const [month, dom] = monthFirst() ? [a, b] : [b, a];
    let year = m[3] ? +m[3] : null;
    if (year !== null && year < 100) year += 2000;
    day = withYear(month, dom, year, today); t.take(m);
  } else if ((m = t.find(String.raw`\bin\s+(\d+|a|one|two|three)\s+(day|days|week|weeks)\b`))) {
    const n = {a: 1, one: 1, two: 2, three: 3}[m[1]] || +m[1];
    day = addDays(today, m[2].startsWith("week") ? 7 * n : n); t.take(m);
  } else if ((m = t.find(String.raw`\bnext week\b`))) {
    day = addDays(today, 7 - weekday(today)); t.take(m);
  }

  // Place: "@ Room 4", or "at" followed by a capitalized name, up to a comma or a recognized part.
  const places = /@\s*|\bat\s+(?=\S)/g;
  while ((m = places.exec(t.lower)) !== null) {
    const nameStart = m.index + m[0].length;
    if (!t.free.slice(m.index, nameStart).every(Boolean) || nameStart >= t.original.length) continue;
    const first = t.original[nameStart];
    if (!m[0].startsWith("@") && !(first !== first.toLowerCase() && first === first.toUpperCase())) continue;
    let end = nameStart;
    while (end < t.original.length && t.free[end] && t.original[end] !== ",") end++;
    ev.location = t.original.slice(nameStart, end).trim();
    t.takeRange(m.index, end);
    break;
  }

  let title = t.rest().replace(/\s+/g, " ").replace(/^[\s,.\-–]+|[\s,.\-–]+$/g, "");
  title = title.replace(/\b(on|at|from|for|the)$/i, "").replace(/^[\s,.\-–]+|[\s,.\-–]+$/g, "");
  ev.title = title;

  // Put it together
  day = day || today;
  if (startTime === null && !allDay && +day === +today) { // nothing given: the next full hour
    ev.start = new Date(now.getFullYear(), now.getMonth(), now.getDate(), now.getHours() + 1);
    ev.end = new Date(+ev.start + minutes(duration || defaultMinutes));
  } else if (startTime === null || allDay) {
    ev.allDay = true;
    ev.start = day;
    ev.end = addDays(day, Math.max(1, Math.round((duration || 1440) / 1440)));
  } else {
    ev.start = atTime(day, startTime);
    if (endTime !== null) {
      ev.end = atTime(day, endTime);
      if (ev.end <= ev.start) ev.end.setDate(ev.end.getDate() + 1); // "10pm-1am"
    } else {
      ev.end = new Date(+ev.start + minutes(duration || defaultMinutes));
    }
  }
  if (ev.freq === "weekly" && !ev.byday.length) ev.byday = [weekday(ev.start)];
  if (ev.byday.length && ev.freq === "weekly") {
    // "every tuesday": start on the first matching day
    const firstDay = new Date(Math.min(...ev.byday.map(d => +nextWeekday(ev.start, d, false))));
    const shift = +atTime(firstDay, [ev.start.getHours(), ev.start.getMinutes()]) - +ev.start;
    ev.start = new Date(+ev.start + shift);
    ev.end = new Date(+ev.end + shift);
  }
  return ev;
}

return {parse};
})();

if (typeof module === "object") module.exports = NavcalQuickAdd;  // for tests in Node
