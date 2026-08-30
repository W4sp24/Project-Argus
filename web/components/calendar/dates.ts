import type { CalendarEvent } from "@/lib/calendar";

/**
 * Date arithmetic and formatting for the calendar surfaces.
 *
 * Beside its only consumers rather than in `lib/`: none of this is the wire
 * contract, it is how the month grid and the day rail agree about which cell
 * an event belongs in. That agreement is the whole reason the file exists —
 * a grid that computes coverage one way and a rail that computes it another
 * is a calendar that shows an event on Tuesday and refuses to list it there.
 *
 * Everything is **local wall time**, and days are ISO `YYYY-MM-DD` strings.
 * Strings rather than `Date` objects as the currency because they are what
 * the API takes, what React keys want, and what compares correctly with `<`
 * without a timezone entering the argument.
 */

/** en-US, matching every other date the app prints. Sunday-first follows. */
const LOCALE = "en-US";

export const WEEKDAYS = ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"];

/** Six weeks, always. A grid that changes height between months makes every
 *  control below it jump when you page through the year. */
const GRID_DAYS = 42;

function pad(value: number): string {
  return String(value).padStart(2, "0");
}

/** One `Date` as its local `YYYY-MM-DD`. Never `toISOString()`, which is UTC
 *  and quietly reports yesterday for anyone west of Greenwich after 5pm. */
export function toIsoDate(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** An ISO day as local midnight. `new Date("2026-09-01")` alone would parse
 *  as UTC midnight — the same off-by-one from the other direction. */
export function parseIsoDate(day: string): Date {
  return new Date(`${day.slice(0, 10)}T00:00:00`);
}

/**
 * Today, local.
 *
 * Never call this during render: it bakes the server's date into the HTML and
 * the client hydrates against a different one, which React can only resolve
 * by throwing the whole server tree away. Read it in an effect, as the page
 * does.
 */
export function todayIso(): string {
  return toIsoDate(new Date());
}

export function addDays(day: string, count: number): string {
  const date = parseIsoDate(day);
  date.setDate(date.getDate() + count);
  return toIsoDate(date);
}

/**
 * The same day-of-month `count` months away, clamped to that month's length.
 *
 * The `setDate(1)` first is the whole trick: setting the month while the date
 * is the 31st rolls 31 January into 3 March, which is how a "previous month"
 * button ends up skipping February.
 */
export function addMonths(day: string, count: number): string {
  const date = parseIsoDate(day);
  const dayOfMonth = date.getDate();
  date.setDate(1);
  date.setMonth(date.getMonth() + count);
  const lastOfMonth = new Date(date.getFullYear(), date.getMonth() + 1, 0).getDate();
  date.setDate(Math.min(dayOfMonth, lastOfMonth));
  return toIsoDate(date);
}

export function startOfMonth(day: string): string {
  return `${day.slice(0, 7)}-01`;
}

export function isSameMonth(day: string, month: string): boolean {
  return day.slice(0, 7) === month.slice(0, 7);
}

/**
 * The 42 days a month grid shows: the month, padded out to whole weeks with
 * the tail of the previous month and the head of the next.
 */
export function monthMatrix(month: string): string[] {
  const first = parseIsoDate(startOfMonth(month));
  const lead = first.getDay(); // 0 = Sunday, matching WEEKDAYS above.
  const start = addDays(toIsoDate(first), -lead);
  return Array.from({ length: GRID_DAYS }, (_, index) => addDays(start, index));
}

/**
 * The first and last day an event covers, inclusive.
 *
 * Two conventions are pinned here, both of them the store's rather than ours:
 *
 * **An all-day `end` is exclusive.** That is what `ics.parse` writes (RFC 5545
 * DTEND) and what `recurrence._overlaps` reads, with one exception it also
 * handles: an `end` at or before the `start` means a single day. So a one-day
 * event is `{start: D, end: D}` and a three-day one is `{start: D, end: D+3}`.
 *
 * **A timed event ending exactly at midnight belongs to the day it started.**
 * Hence the millisecond taken off the end before it is turned into a day:
 * 09:00–24:00 is one day on the grid, not two.
 */
export function eventSpan(event: CalendarEvent): { first: string; last: string } {
  if (event.all_day) {
    const first = event.start.slice(0, 10);
    const end = event.end.slice(0, 10);
    return { first, last: end > first ? addDays(end, -1) : first };
  }
  const startMs = new Date(event.start).getTime();
  const endMs = new Date(event.end).getTime();
  if (Number.isNaN(startMs)) {
    // A stamp `Date` cannot read still has a day in its first ten characters,
    // and losing one row off the grid is a better failure than losing the grid.
    const first = event.start.slice(0, 10);
    return { first, last: first };
  }
  const first = toIsoDate(new Date(startMs));
  const last =
    !Number.isNaN(endMs) && endMs > startMs ? toIsoDate(new Date(endMs - 1)) : first;
  return { first, last };
}

/**
 * Events bucketed by the days they cover, computed once for the whole window.
 *
 * The grid and the rail both read this map, so they cannot disagree, and a
 * month of events is walked once instead of once per cell.
 */
export function groupByDay(events: CalendarEvent[]): Map<string, CalendarEvent[]> {
  const byDay = new Map<string, CalendarEvent[]>();
  for (const event of events) {
    const { first, last } = eventSpan(event);
    // Bounded: a row with a mangled `end` far in the future must not turn one
    // event into a million map entries. Two months is more than any grid shows.
    for (let day = first, guard = 0; day <= last && guard < 62; day = addDays(day, 1), guard++) {
      const bucket = byDay.get(day);
      if (bucket) bucket.push(event);
      else byDay.set(day, [event]);
    }
  }
  for (const bucket of byDay.values()) bucket.sort(compareForDay);
  return byDay;
}

/** All-day first, then by start instant, then by title — the order the day
 *  rail reads in and the order the chips stack in a cell. */
function compareForDay(a: CalendarEvent, b: CalendarEvent): number {
  if (a.all_day !== b.all_day) return a.all_day ? -1 : 1;
  const byStart = new Date(a.start).getTime() - new Date(b.start).getTime();
  if (byStart) return byStart;
  return a.title.localeCompare(b.title);
}

// --- labels -----------------------------------------------------------------

/** `9:30 AM`, or `ALL DAY`. Matches PLANNER.TIMELINE's formatting. */
export function timeLabel(iso: string, allDay = false): string {
  if (allDay || !iso.includes("T")) return "ALL DAY";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleTimeString(LOCALE, { hour: "numeric", minute: "2-digit" });
}

/** `SEPTEMBER 2026` for the month header. */
export function monthHeading(month: string): string {
  return parseIsoDate(month)
    .toLocaleDateString(LOCALE, { month: "long", year: "numeric" })
    .toUpperCase();
}

/** `THU, SEP 3` for the day rail's header. */
export function dayHeading(day: string): string {
  return parseIsoDate(day)
    .toLocaleDateString(LOCALE, { weekday: "short", day: "numeric", month: "short" })
    .toUpperCase();
}

/** `Wednesday, September 3` — the accessible name of a grid cell, spelled out
 *  because a screen reader reading "WED 3 SEP" says "wed". */
export function daySpoken(day: string): string {
  return parseIsoDate(day).toLocaleDateString(LOCALE, {
    weekday: "long",
    month: "long",
    day: "numeric",
  });
}

// --- form <-> wire ----------------------------------------------------------

function hasZone(iso: string): boolean {
  return /(?:Z|[+-]\d{2}:?\d{2})$/.test(iso);
}

/** An event's start/end as a `datetime-local` value. A naive stamp — every
 *  locally created event — round-trips by slicing; a feed's UTC one goes
 *  through `Date` so the box shows the viewer's wall clock. */
export function toDateTimeInput(iso: string): string {
  if (!hasZone(iso)) return iso.slice(0, 16);
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 16);
  return `${toIsoDate(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** A `datetime-local` value as the wire wants it. Left naive deliberately:
 *  the store keeps what the user typed, and an offset stamped on here would
 *  be this browser's, not the one the event means. */
export function fromDateTimeInput(value: string): string {
  return value.length === 16 ? `${value}:00` : value;
}

/**
 * The inclusive last day of an all-day event, for the form's END box.
 * Inverse of `allDayWireEnd`; see `eventSpan` for why the wire value is
 * exclusive and why a single day is stored as `end === start`.
 */
export function allDayLastDay(start: string, end: string): string {
  const first = start.slice(0, 10);
  const last = end.slice(0, 10);
  return last > first ? addDays(last, -1) : first;
}

/** The inclusive END the user picked, as the exclusive value the store reads. */
export function allDayWireEnd(start: string, lastDay: string): string {
  return lastDay > start ? addDays(lastDay, 1) : start;
}
