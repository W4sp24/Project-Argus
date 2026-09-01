"use client";

import Button from "@/components/ui/Button";
import { dayHeading, timeLabel } from "@/components/calendar/dates";
import { calendarOf, type CalendarEvent, type CalendarInfo } from "@/lib/calendar";

/**
 * A subscribed event: everything it says, and nothing to press.
 *
 * `.ics` is read-only by protocol — the API answers a write to a subscribed
 * calendar with a 422 naming it — so this shows the event rather than offering
 * a form whose save can only fail. Read-only is not the same as invisible:
 * the notes and location are usually the reason the feed was subscribed to.
 */
export default function EventDetails({
  event,
  calendars,
  onClose,
}: {
  event: CalendarEvent;
  calendars: CalendarInfo[] | undefined;
  onClose: () => void;
}) {
  const calendar = calendarOf(event, calendars);
  return (
    <>
      <p className="text-lead text-ink-bright">{event.title}</p>
      <p className="mt-1 font-mono text-meta text-ink-muted">
        {event.all_day
          ? `${dayHeading(event.start)} · all day`
          : `${dayHeading(event.start)} · ${timeLabel(event.start)} – ${timeLabel(event.end)}`}
      </p>
      {event.location && (
        <p className="mt-1 font-mono text-meta text-ink-faint">@ {event.location}</p>
      )}
      {event.notes && <p className="mt-3 whitespace-pre-wrap text-body text-ink">{event.notes}</p>}

      <p className="mt-4 border border-auto-line px-3 py-2 text-label text-auto">
        Read-only. This comes from {calendar?.name ?? "a subscribed calendar"}, and an .ics feed is
        one-way by protocol — change it where it is published and the next sync brings it over.
      </p>

      <div className="mt-5 flex items-center gap-2 border-t border-line pt-4">
        <Button size="md" onClick={onClose} className="ml-auto">
          Close
        </Button>
      </div>
    </>
  );
}
