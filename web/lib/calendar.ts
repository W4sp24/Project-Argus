"use client";

import useSWR from "swr";
import { apiBase, fetcher, mutateJSON } from "@/lib/api";

/**
 * The `/api/calendar` surface — the local event store plus .ics subscriptions.
 *
 * Its own module rather than more of `lib/api.ts`: that file is every other
 * endpoint in the app, and this feature arrived with a whole router of its
 * own. Everything here mirrors `backend/features/calendar/router.py`. Reads go
 * through the shared SWR `fetcher` and writes through `mutateJSON`, so the
 * Electron base-URL rewrite that `apiFetch` performs applies to calendar
 * requests exactly as it does to the rest — a bare `fetch("/api/calendar/…")`
 * works in dev and 404s in the packaged desktop app.
 */

// --- models -----------------------------------------------------------------

/**
 * One calendar. `kind` is `"local"` or `"ics"` today, and is typed as a plain
 * string on purpose: the schema deliberately carries no CHECK constraint on
 * that column because it is the field a later source type will extend, and a
 * closed union here would be a copy of a constraint the backend declined to
 * make. Compare against `LOCAL_KIND` / `ICS_KIND` instead of exhausting it.
 */
export interface CalendarInfo {
  id: string;
  name: string;
  kind: string;
  /** Hex swatch chosen when subscribing, or `""` for "use the mode accent". */
  color: string;
  /** Redacted host of the feed. The URL itself is a credential and lives in
   *  the OS keyring — it never comes back out of the API. */
  url_display: string | null;
  refresh_interval_seconds: number;
  last_sync_at: string | null;
  last_sync_error: string | null;
  enabled: boolean;
  created_at: string;
}

export const LOCAL_KIND = "local";
export const ICS_KIND = "ics";

/**
 * One concrete occurrence, as `GET /api/calendar/events` returns it.
 *
 * `start`/`end` are ISO-8601: `YYYY-MM-DD` when `all_day`, otherwise a
 * datetime — naive wall-clock for a locally created event (stored exactly as
 * it was typed) and UTC-offset for one parsed out of a feed. Both forms are
 * what `new Date()` reads correctly, which is why every label in this feature
 * formats through it rather than slicing the string.
 *
 * `rrule` carries the occurrence's own recurrence rule, so an edit can hand
 * it back untouched instead of having to invent one — see `EventPatch`.
 */
export interface CalendarEvent {
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  /** Which producer this came from: `"local"`, or a feed's calendar id. */
  source: string;
  location: string | null;
  /**
   * The stored row id — or, for one occurrence of a recurring series,
   * `"<rowId>::<occurrenceStart>"`. Pass whichever came back verbatim: the
   * router splits it, so a PATCH edits the series and
   * `DELETE ?scope=one` cancels just that instance.
   */
  id: string | null;
  calendar_id: string | null;
  notes: string | null;
  /**
   * Whether Argus may write this event back — `false` for a subscribed .ics
   * event, which the API refuses with a 422.
   *
   * Prefer `isWritable()` over reading this directly. Both answer from the
   * owning calendar's `kind` — the same fact the backend's `_require_writable`
   * gate consults — but `isWritable()` also handles an event whose calendar is
   * not in hand yet, and it is one place to change if the rule ever moves.
   *
   * (This flag was briefly wrong: the list path returned `false` for every
   * event, local ones included, because the expansion built events from store
   * rows that carry no such column. Fixed in the backend; `isWritable()`
   * remains the seam rather than being inlined at each call site.)
   */
  editable: boolean;
}

/** Body of `POST /events`; also the patchable subset of `PATCH /events/{id}`. */
export interface EventDraft {
  title: string;
  start: string;
  end: string;
  all_day?: boolean;
  location?: string | null;
  notes?: string | null;
  /** RFC 5545 rule body without the `RRULE:` prefix, e.g. `FREQ=WEEKLY`. */
  rrule?: string | null;
  /** Omit to land in the default local calendar. */
  calendar_id?: string;
}

/**
 * A partial event update.
 *
 * The backend applies `exclude_unset`, so an omitted key keeps its stored
 * value. That still matters for `rrule`: the router rebuilds the row from
 * `{**existing, **patch}`, so a form that always sent every field would
 * overwrite the repeat rule with whatever the control happened to show.
 * Omit the key unless the user actually changed it.
 */
export type EventPatch = Partial<EventDraft>;

/** Body of `POST /subscriptions`. */
export interface SubscriptionDraft {
  name: string;
  url: string;
  color?: string;
}

/**
 * Body of `POST /subscriptions/probe` — the URL alone.
 *
 * The route's own `ProbeRequest` is *not* `SubscriptionRequest`: a dialog
 * probes while the user is still deciding what to call the calendar, so
 * requiring a name would turn "does this address work?" into a validation
 * error about a different field. Written as a superset of the subscribe body
 * so a caller that already has the name can hand over the same object.
 */
export type ProbeDraft = Pick<SubscriptionDraft, "url"> & Partial<SubscriptionDraft>;

/** What a probe found, so the dialog can say more than "ok". */
export interface SubscriptionProbe {
  events: number;
  /** Entries the parser skipped — a malformed VEVENT, or one with no DTSTART. */
  skipped: number;
  /** The feed's own `X-WR-CALNAME`, when it publishes one — most exporters
   *  do. Prefill the name box with it: the calendar then ends up called what
   *  it is called everywhere else. `null` when the feed omits it. */
  name_hint: string | null;
}

// --- reads ------------------------------------------------------------------

/** Every calendar, local and subscribed. The default local one is created on
 *  first read, so this is never empty. */
export function useCalendars() {
  return useSWR<CalendarInfo[]>("/api/calendar/calendars", fetcher);
}

/**
 * Every occurrence in the half-open window `[start, end)`, both dates
 * `YYYY-MM-DD`.
 *
 * `start`/`end` are nullable so a caller can hold the key back until it knows
 * what window it wants: the month grid cannot compute one during render
 * without reading the clock, which bakes the *server's* date into the HTML and
 * hydrates against a different one.
 *
 * `calendarIds` maps to the repeatable `calendar_id` query parameter.
 * Omitting it means every calendar; an **empty array means none**, and it has
 * to be handled here rather than sent — a request with zero `calendar_id`
 * parameters is indistinguishable from one that never filtered, so "the user
 * hid every calendar" would come back as "show everything".
 */
export function useCalendarEvents(
  start: string | null,
  end: string | null,
  calendarIds?: string[],
) {
  const none = calendarIds !== undefined && calendarIds.length === 0;
  let key: string | null = null;
  if (start && end && !none) {
    const params = new URLSearchParams({ start, end });
    for (const id of calendarIds ?? []) params.append("calendar_id", id);
    key = `/api/calendar/events?${params.toString()}`;
  }
  return useSWR<CalendarEvent[]>(key, fetcher);
}

// --- writes -----------------------------------------------------------------

export function createEvent(draft: EventDraft) {
  return mutateJSON<CalendarEvent>("/api/calendar/events", draft, "POST");
}

/**
 * Edit one event. An occurrence id edits the whole series — the router splits
 * the id and there is no per-instance edit — which the dialog says out loud
 * rather than letting the user discover it next week.
 */
export function updateEvent(id: string, patch: EventPatch) {
  return mutateJSON<CalendarEvent>(`/api/calendar/events/${eventPath(id)}`, patch, "PATCH");
}

/**
 * Delete an event. `scope: "one"` on an occurrence id cancels just that
 * instance (it becomes an EXDATE and the series survives); `"series"` — and
 * `"one"` on a plain row id, which has no instance to single out — deletes the
 * row outright.
 */
export function deleteEvent(id: string, scope: "one" | "series" = "series") {
  return mutateJSON<{ ok: boolean }>(
    `/api/calendar/events/${eventPath(id)}?scope=${scope}`,
    undefined,
    "DELETE",
  );
}

/** Fetch and parse a feed without saving anything. */
export function probeSubscription(draft: ProbeDraft) {
  return mutateJSON<SubscriptionProbe>("/api/calendar/subscriptions/probe", draft, "POST");
}

/** Subscribe, and sync once — the returned calendar already carries the result. */
export function addSubscription(draft: SubscriptionDraft) {
  return mutateJSON<CalendarInfo>("/api/calendar/subscriptions", draft, "POST");
}

export function removeSubscription(id: string) {
  return mutateJSON<{ ok: boolean }>(
    `/api/calendar/subscriptions/${encodeURIComponent(id)}`,
    undefined,
    "DELETE",
  );
}

/** Re-fetch one feed now. Answers with the calendar, including its new
 *  `last_sync_at` / `last_sync_error`. */
export function syncSubscription(id: string) {
  return mutateJSON<CalendarInfo>(
    `/api/calendar/subscriptions/${encodeURIComponent(id)}/sync`,
    undefined,
    "POST",
  );
}

/**
 * Download URL for every local event as standard iCalendar.
 *
 * Absolute via `apiBase()` because this one is an `<a href>` rather than a
 * fetch, so the Electron shell's injected origin has to be applied by hand —
 * a relative href would resolve against the Next server, which is not where
 * the backend lives in the packaged app.
 */
export function exportIcsUrl(): string {
  return `${apiBase()}/api/calendar/export.ics`;
}

// --- id and permission helpers ----------------------------------------------

/** How `recurrence.py` joins a row id to the occurrence it produced. */
export const OCCURRENCE_SEPARATOR = "::";

/** `true` when this id addresses one instance of a recurring series. */
export function isOccurrence(id: string | null | undefined): boolean {
  return typeof id === "string" && id.includes(OCCURRENCE_SEPARATOR);
}

/**
 * Whether the API will accept a write to this event.
 *
 * Answered from the owning calendar's `kind` — the same fact the backend's
 * `_require_writable` gate consults — with the event's own flag as a fast
 * path. An event whose calendar is unknown (still loading, or an id no longer
 * in the list) is treated as read-only: offering an edit that 422s is worse
 * than briefly withholding one.
 */
export function isWritable(
  event: CalendarEvent,
  calendars: CalendarInfo[] | undefined,
): boolean {
  if (event.editable) return true;
  const calendar = calendarOf(event, calendars);
  return calendar?.kind === LOCAL_KIND;
}

export function calendarOf(
  event: CalendarEvent,
  calendars: CalendarInfo[] | undefined,
): CalendarInfo | undefined {
  return calendars?.find((calendar) => calendar.id === event.calendar_id);
}

/** A calendar's swatch, falling back to the live mode accent. */
export function calendarColor(calendar: CalendarInfo | undefined): string {
  return calendar?.color || "var(--ac)";
}

/**
 * One event id as a path segment.
 *
 * An occurrence id carries `::` and, for a feed-sourced series, a `+hh:mm`
 * offset. Both are legal in a path segment, but encoding them costs nothing
 * and takes the question off the table for whatever proxy sits in front.
 */
function eventPath(id: string): string {
  return encodeURIComponent(id);
}
