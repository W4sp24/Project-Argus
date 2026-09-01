"use client";

import Panel from "@/components/Panel";
import Button from "@/components/ui/Button";
import { dayHeading, timeLabel } from "@/components/calendar/dates";
import {
  calendarColor,
  calendarOf,
  isOccurrence,
  isWritable,
  type CalendarEvent,
  type CalendarInfo,
} from "@/lib/calendar";

/**
 * The selected day, in full.
 *
 * The grid can only afford three truncated chips per cell, so this is where an
 * event is actually legible — and where it is *operable*: every row is a real
 * button in the tab order, which is what lets the grid's chips stay out of it.
 *
 * Reads the same `byDay` bucket the grid renders from, so the two can never
 * disagree about which day an event lands on.
 */
export default function DayRail({
  day,
  events,
  calendars,
  loading,
  onOpenEvent,
  onCreate,
}: {
  day: string;
  events: CalendarEvent[];
  calendars: CalendarInfo[] | undefined;
  loading: boolean;
  onOpenEvent: (event: CalendarEvent) => void;
  onCreate: (day: string) => void;
}) {
  return (
    <Panel
      label={`DAY · ${dayHeading(day)}`}
      headerRight={
        <Button variant="primary" onClick={() => onCreate(day)}>
          ＋ EVENT
        </Button>
      }
    >
      {/* "Still fetching" and "fetched, and the day is empty" have to look
          different — one blank panel for both is how a dead backend passes for
          a free afternoon. */}
      {loading && events.length === 0 ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : events.length === 0 ? (
        <p className="text-sm text-ink-faint">Nothing on this day.</p>
      ) : (
        <ul className="space-y-1.5">
          {events.map((event, index) => (
            <li key={event.id ?? `${event.start}-${index}`}>
              <DayRow event={event} calendars={calendars} onOpen={onOpenEvent} />
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function DayRow({
  event,
  calendars,
  onOpen,
}: {
  event: CalendarEvent;
  calendars: CalendarInfo[] | undefined;
  onOpen: (event: CalendarEvent) => void;
}) {
  const calendar = calendarOf(event, calendars);
  const writable = isWritable(event, calendars);
  return (
    <button
      onClick={() => onOpen(event)}
      // Read-only events open the same dialog — it shows the notes and the
      // location, and simply offers nothing to save. Hiding them behind no
      // affordance at all would make a subscribed calendar unreadable rather
      // than unwritable.
      aria-label={`${writable ? "Edit" : "View"} ${event.title}`}
      className="flex w-full gap-3 border border-line px-2 py-1.5 text-left transition-colors hover:border-lineHi"
    >
      <div className="w-14 shrink-0 text-right">
        <p className="font-mono text-meta font-semibold text-ink">
          {timeLabel(event.start, event.all_day)}
        </p>
        {!event.all_day && (
          <p className="font-mono text-micro text-ink-faint">{timeLabel(event.end)}</p>
        )}
      </div>

      <div
        className="min-w-0 flex-1 border-l-[3px] pl-2"
        style={{ borderColor: calendarColor(calendar) }}
      >
        <p className="truncate text-body text-ink-bright">{event.title}</p>
        {event.location && (
          <p className="truncate font-mono text-micro text-ink-faint">@ {event.location}</p>
        )}
        <p className="flex flex-wrap items-center gap-1.5 font-mono text-micro text-ink-faint">
          {calendar && <span className="truncate">{calendar.name}</span>}
          {isOccurrence(event.id) && (
            <span className="border border-line px-1 uppercase tracking-[0.1em]">repeats</span>
          )}
          {!writable && (
            // The one badge this feature exists to be honest about: an .ics
            // feed is read-only by protocol, and the API answers a write to
            // one with a 422.
            <span
              className="border border-auto-line px-1 uppercase tracking-[0.1em] text-auto"
              title="Subscribed calendars are read-only — edit this where it is published"
            >
              read-only
            </span>
          )}
        </p>
      </div>
    </button>
  );
}
