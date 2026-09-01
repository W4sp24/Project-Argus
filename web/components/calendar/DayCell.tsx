"use client";

import { daySpoken, timeLabel } from "@/components/calendar/dates";
import {
  calendarColor,
  calendarOf,
  isWritable,
  type CalendarEvent,
  type CalendarInfo,
} from "@/lib/calendar";

/** How many chips fit in a cell before the rest collapse into a count. */
const CHIP_LIMIT = 3;

/**
 * One day in the month grid.
 *
 * The clickable surface is a single absolutely-positioned button covering the
 * cell, with the date number and the event chips painted on top of it. That
 * keeps the hit target the whole cell without nesting a button inside a
 * button, which is invalid markup and which browsers and screen readers
 * resolve by quietly dropping the inner one.
 *
 * **Chips are `tabIndex={-1}` deliberately.** A month holds a hundred-odd
 * events and putting every one in the tab order buries every control after
 * the grid behind them. Each event is a real, focusable button in the day
 * rail — which is exactly where Tab goes next — so the chips are the pointer
 * shortcut to a dialog the keyboard already reaches.
 */
export default function DayCell({
  day,
  events,
  calendars,
  inMonth,
  isToday,
  isSelected,
  tabbable,
  onSelect,
  onOpenEvent,
  onCreate,
}: {
  day: string;
  events: CalendarEvent[];
  calendars: CalendarInfo[] | undefined;
  inMonth: boolean;
  isToday: boolean;
  isSelected: boolean;
  /** The grid's roving tabindex — exactly one cell carries it. */
  tabbable: boolean;
  onSelect: (day: string) => void;
  onOpenEvent: (event: CalendarEvent) => void;
  onCreate: (day: string) => void;
}) {
  const shown = events.slice(0, CHIP_LIMIT);
  const hidden = events.length - shown.length;
  const count =
    events.length === 0 ? "no events" : events.length === 1 ? "1 event" : `${events.length} events`;

  return (
    <div
      role="gridcell"
      aria-selected={isSelected}
      className={`relative min-h-[5.5rem] border-b border-l border-line first:border-l-0 ${
        isSelected ? "bg-[var(--ac-bg)]" : ""
      }`}
    >
      {/* Single click selects, double click creates. Selecting has to stay
          cheap because it is also how you read a day; the rail's "＋ EVENT"
          is the keyboard-reachable route to the same dialog. */}
      <button
        data-day={day}
        tabIndex={tabbable ? 0 : -1}
        aria-current={isToday ? "date" : undefined}
        aria-label={`${daySpoken(day)}, ${count}`}
        onClick={() => onSelect(day)}
        onDoubleClick={() => onCreate(day)}
        className={`absolute inset-0 h-full w-full transition-colors hover:bg-[var(--ac-bg)] ${
          isSelected ? "ring-1 ring-inset ring-[var(--ac)]" : ""
        }`}
      />

      <div className="pointer-events-none relative flex h-full flex-col gap-px p-1">
        <span
          className={`font-mono text-meta ${
            isToday
              ? "self-start bg-[var(--ac)] px-1 text-void"
              : inMonth
                ? "text-ink"
                : "text-ink-faint opacity-60"
          }`}
        >
          {Number(day.slice(8, 10))}
        </span>

        {shown.map((event, index) => (
          <EventChip
            key={event.id ?? `${event.start}-${index}`}
            event={event}
            calendars={calendars}
            onOpen={onOpenEvent}
          />
        ))}
        {hidden > 0 && (
          <span className="px-1 font-mono text-micro text-ink-faint">+{hidden} more</span>
        )}
      </div>
    </div>
  );
}

function EventChip({
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
      tabIndex={-1}
      onClick={() => onOpen(event)}
      title={
        writable
          ? `${event.title} — ${timeLabel(event.start, event.all_day)}`
          : `${event.title} — read-only, from ${calendar?.name ?? "a subscribed calendar"}`
      }
      // Dashed border on a read-only event, so a feed reads as a feed at a
      // glance rather than only once you open it.
      className={`pointer-events-auto flex w-full min-w-0 items-center gap-1 border-l-2 bg-[rgba(255,255,255,0.03)] px-1 py-px text-left font-mono text-micro text-ink hover:text-ink-bright ${
        writable ? "" : "border-dashed opacity-80"
      }`}
      style={{ borderLeftColor: calendarColor(calendar) }}
    >
      {!event.all_day && (
        <span className="shrink-0 text-ink-faint">
          {timeLabel(event.start).replace(/\s?[AP]M$/, "")}
        </span>
      )}
      <span className="truncate">{event.title}</span>
    </button>
  );
}
