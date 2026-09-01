import {
  allDayLastDay,
  allDayWireEnd,
  fromDateTimeInput,
  toDateTimeInput,
} from "@/components/calendar/dates";
import {
  calendarOf,
  type CalendarEvent,
  type CalendarInfo,
  type EventDraft,
  type EventPatch,
} from "@/lib/calendar";

/**
 * The event form's state, and the two conversions that flank it.
 *
 * Split out of `EventDialog` because the mapping is the part with the rules in
 * it — an all-day end that is inclusive in the box and exclusive on the wire,
 * a recurrence the wire model cannot report, and a patch that has to be a
 * genuine diff — while the dialog itself is inputs and buttons.
 */

/** The repeat rules offered. The value is an RFC 5545 rule body. */
export const REPEATS: { value: string; label: string }[] = [
  { value: "", label: "Does not repeat" },
  { value: "FREQ=DAILY", label: "Daily" },
  { value: "FREQ=WEEKLY", label: "Weekly" },
  { value: "FREQ=MONTHLY", label: "Monthly" },
  { value: "FREQ=YEARLY", label: "Yearly" },
];

/**
 * "Leave the stored rule alone" — the default whenever an event is edited.
 *
 * `CalendarEvent` carries no `rrule`, so the dialog cannot know what the rule
 * currently is, and a select that defaulted to "Does not repeat" would quietly
 * delete a weekly class the first time someone fixed its title. The leading
 * space keeps it out of the value space of a real rule.
 */
export const KEEP_REPEAT = " keep";

export interface Form {
  title: string;
  allDay: boolean;
  /** `YYYY-MM-DD` when `allDay`, else a `datetime-local` value. */
  start: string;
  /** Same — and **inclusive** in all-day mode; `allDayWireEnd` converts. */
  end: string;
  location: string;
  notes: string;
  repeat: string;
  calendarId: string;
}

export function formFor(
  event: CalendarEvent | null,
  day: string,
  calendars: CalendarInfo[] | undefined,
): Form {
  if (!event) {
    // 09:00–10:00 rather than "the next free hour": a predictable default is
    // one edit away, and a clever one is a surprise every time it guesses.
    return {
      title: "",
      allDay: false,
      start: `${day}T09:00`,
      end: `${day}T10:00`,
      location: "",
      notes: "",
      repeat: "",
      calendarId: "",
    };
  }
  return {
    title: event.title,
    allDay: event.all_day,
    start: event.all_day ? event.start.slice(0, 10) : toDateTimeInput(event.start),
    end: event.all_day ? allDayLastDay(event.start, event.end) : toDateTimeInput(event.end),
    location: event.location ?? "",
    notes: event.notes ?? "",
    repeat: KEEP_REPEAT,
    calendarId: calendarOf(event, calendars)?.id ?? "",
  };
}

/** Switching all-day converts what is in the boxes rather than clearing it —
 *  losing the date you just picked because you ticked a box is its own bug. */
export function withAllDay(form: Form, allDay: boolean): Form {
  return {
    ...form,
    allDay,
    start: allDay ? form.start.slice(0, 10) : `${form.start.slice(0, 10)}T09:00`,
    end: allDay ? form.end.slice(0, 10) : `${form.end.slice(0, 10)}T10:00`,
  };
}

export function toWire(form: Form): EventDraft {
  return {
    title: form.title.trim(),
    start: form.allDay ? form.start : fromDateTimeInput(form.start),
    end: form.allDay ? allDayWireEnd(form.start, form.end) : fromDateTimeInput(form.end),
    all_day: form.allDay,
    location: form.location.trim() || null,
    notes: form.notes.trim() || null,
  };
}

/**
 * Only the fields the user actually touched.
 *
 * Not an optimisation. Editing any occurrence of a series edits the series,
 * and the router rebuilds the row from `{**existing, **patch}` — so sending
 * back the occurrence's own `start` would move the series' anchor to that
 * date and take every earlier occurrence with it. A diff cannot do that.
 */
export function diffEvent(
  before: EventDraft,
  after: EventDraft,
  repeatBefore: string,
  repeatAfter: string,
): EventPatch {
  const patch: EventPatch = {};
  if (after.title !== before.title) patch.title = after.title;
  if (after.start !== before.start) patch.start = after.start;
  if (after.end !== before.end) patch.end = after.end;
  if (after.all_day !== before.all_day) patch.all_day = after.all_day;
  if (after.location !== before.location) patch.location = after.location;
  if (after.notes !== before.notes) patch.notes = after.notes;
  if (repeatAfter !== repeatBefore && repeatAfter !== KEEP_REPEAT) {
    patch.rrule = repeatAfter || null;
  }
  return patch;
}
