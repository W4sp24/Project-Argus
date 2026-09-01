"use client";

import { useEffect, useRef } from "react";
import DayCell from "@/components/calendar/DayCell";
import {
  WEEKDAYS,
  addDays,
  addMonths,
  isSameMonth,
  monthHeading,
  monthMatrix,
  parseIsoDate,
} from "@/components/calendar/dates";
import type { CalendarEvent, CalendarInfo } from "@/lib/calendar";

/**
 * The month grid.
 *
 * A real `role="grid"` with a roving tabindex: one cell is tabbable, the arrow
 * keys move the selection and the focus with it, PageUp/PageDown change month,
 * Home/End reach the ends of the week.
 *
 * Selection and focus are the same thing here on purpose. Keeping them apart
 * is the `aria-activedescendant` pattern and a second mental model to
 * maintain, for a calendar in which "the focused day" and "the day the rail is
 * showing" are always the same day anyway.
 */
export default function MonthGrid({
  month,
  selected,
  today,
  byDay,
  calendars,
  onSelect,
  onOpenEvent,
  onCreate,
}: {
  /** Any day in the month being shown. */
  month: string;
  selected: string;
  /** `null` until the client has read the clock — see `todayIso`. */
  today: string | null;
  byDay: Map<string, CalendarEvent[]>;
  calendars: CalendarInfo[] | undefined;
  onSelect: (day: string) => void;
  onOpenEvent: (event: CalendarEvent) => void;
  onCreate: (day: string) => void;
}) {
  const days = monthMatrix(month);
  const gridRef = useRef<HTMLDivElement>(null);

  // Only a keyboard move drags focus along with the selection. A click has
  // already put focus where the user pointed, and a selection changed from the
  // rail or the month buttons must not yank it back into the grid.
  const followFocus = useRef(false);
  useEffect(() => {
    if (!followFocus.current) return;
    followFocus.current = false;
    gridRef.current?.querySelector<HTMLButtonElement>(`[data-day="${selected}"]`)?.focus();
  }, [selected]);

  // The one tabbable cell. Falls back to the 1st when the selection is in
  // another month — paging with the header buttons keeps the selected day, and
  // a grid with no tabbable cell at all is a keyboard dead end.
  const tabbable = days.includes(selected)
    ? selected
    : (days.find((day) => isSameMonth(day, month)) ?? days[0]);

  function onKeyDown(pressed: React.KeyboardEvent<HTMLDivElement>) {
    // `data-day` is only on the cell buttons, so a key pressed on a chip (or
    // on the grid itself) falls through to the browser untouched.
    const day = (pressed.target as HTMLElement).dataset?.day;
    if (!day) return;
    let next: string;
    switch (pressed.key) {
      case "ArrowLeft":
        next = addDays(day, -1);
        break;
      case "ArrowRight":
        next = addDays(day, 1);
        break;
      case "ArrowUp":
        next = addDays(day, -7);
        break;
      case "ArrowDown":
        next = addDays(day, 7);
        break;
      case "Home":
        next = addDays(day, -parseIsoDate(day).getDay());
        break;
      case "End":
        next = addDays(day, 6 - parseIsoDate(day).getDay());
        break;
      case "PageUp":
        next = addMonths(day, -1);
        break;
      case "PageDown":
        next = addMonths(day, 1);
        break;
      default:
        return;
    }
    // Arrows scroll the page and Home/End jump it; a grid that moves the
    // selection *and* scrolls has done two things for one keystroke.
    pressed.preventDefault();
    followFocus.current = true;
    onSelect(next);
  }

  return (
    <div
      ref={gridRef}
      role="grid"
      aria-label={`${monthHeading(month)} month grid`}
      className="border border-line"
      onKeyDown={onKeyDown}
    >
      <div role="row" className="grid grid-cols-7 border-b border-line">
        {WEEKDAYS.map((weekday) => (
          <div
            key={weekday}
            role="columnheader"
            className="px-1 py-1 text-center font-mono text-micro uppercase tracking-[0.14em] text-ink-faint"
          >
            {weekday}
          </div>
        ))}
      </div>

      {Array.from({ length: days.length / 7 }, (_, week) => (
        <div key={week} role="row" className="grid grid-cols-7">
          {days.slice(week * 7, week * 7 + 7).map((day) => (
            <DayCell
              key={day}
              day={day}
              events={byDay.get(day) ?? []}
              calendars={calendars}
              inMonth={isSameMonth(day, month)}
              isToday={day === today}
              isSelected={day === selected}
              tabbable={day === tabbable}
              onSelect={onSelect}
              onOpenEvent={onOpenEvent}
              onCreate={onCreate}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
