"use client";

import { useEffect, useMemo, useState } from "react";
import PageHeader from "@/components/PageHeader";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import CalendarsPanel from "@/components/calendar/CalendarsPanel";
import DayRail from "@/components/calendar/DayRail";
import EventDialog from "@/components/calendar/EventDialog";
import MonthGrid from "@/components/calendar/MonthGrid";
import {
  addDays,
  addMonths,
  eventSpan,
  groupByDay,
  isSameMonth,
  monthHeading,
  monthMatrix,
  startOfMonth,
  todayIso,
} from "@/components/calendar/dates";
import { useCalendarEvents, useCalendars, type CalendarEvent } from "@/lib/calendar";

/**
 * The calendar: a month grid over a day rail, on the local event store.
 *
 * All three date states start as `null` and are filled in an effect. Reading
 * the clock during render bakes the *server's* date into the HTML and the
 * client hydrates against a different one — the mismatch documented on
 * `PLANNER.TIMELINE`, which React can only resolve by throwing the server tree
 * away and re-rendering the page. It also keeps the SWR key null until the
 * window is known, so the server never fires a request for a month it guessed.
 *
 * One fetch feeds both surfaces. The events for the whole visible grid are
 * bucketed by day once, and the grid and the rail read the same map, so they
 * cannot disagree about which day an event lands on.
 */
export default function CalendarPage() {
  const { show } = useToast();
  const [today, setToday] = useState<string | null>(null);
  const [month, setMonth] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [dialog, setDialog] = useState<{ event: CalendarEvent | null; day: string } | null>(null);

  useEffect(() => {
    const day = todayIso();
    setToday(day);
    setSelected(day);
    setMonth(startOfMonth(day));
  }, []);

  const days = month ? monthMatrix(month) : [];
  // Padded by a day at each edge. The window is compared against stored
  // strings — UTC for anything that came out of a feed — while the grid places
  // events by the viewer's own clock, so an event near midnight belongs to a
  // cell one day either side of the raw window.
  const windowStart = days.length ? addDays(days[0], -1) : null;
  const windowEnd = days.length ? addDays(days[days.length - 1], 2) : null;

  const {
    data: calendars,
    error: calendarsError,
    mutate: refreshCalendars,
  } = useCalendars();

  // `undefined` while nothing is hidden, so the key stays the unfiltered URL
  // and every calendar answers; a list once the picker is actually filtering.
  const visibleIds = useMemo(() => {
    if (hidden.size === 0) return undefined;
    return (calendars ?? []).filter((one) => !hidden.has(one.id)).map((one) => one.id);
  }, [calendars, hidden]);

  const {
    data: events,
    error: eventsError,
    isLoading,
    mutate: refreshEvents,
  } = useCalendarEvents(windowStart, windowEnd, visibleIds);

  const byDay = useMemo(() => groupByDay(events ?? []), [events]);
  const allHidden = visibleIds !== undefined && visibleIds.length === 0;

  function select(day: string) {
    setSelected(day);
    // Arrowing or clicking off the edge of the grid brings the month with it,
    // otherwise the selection lands somewhere the user cannot see.
    if (month && !isSameMonth(day, month)) setMonth(startOfMonth(day));
  }

  function openEvent(event: CalendarEvent) {
    const day = eventSpan(event).first;
    select(day);
    setDialog({ event, day });
  }

  function done(message: string) {
    setDialog(null);
    show(message);
    refreshEvents();
  }

  return (
    <>
      <PageHeader
        label="CALENDAR"
        title="Calendar"
        subtitle="Your own events, stored locally, plus any .ics feeds you subscribe to. Nothing to connect and nothing leaves the machine."
      />

      <div className="grid gap-4 xl:grid-cols-shell">
        <Panel
          label="CALENDAR.MONTH"
          headerRight={
            <Button
              variant="quiet"
              onClick={() => today && select(today)}
              disabled={!today || selected === today}
            >
              TODAY
            </Button>
          }
        >
          {!month || !selected ? (
            // Pre-mount: the month is not known yet (see the docstring), and a
            // guessed one would be the server's.
            <p className="text-sm text-ink-muted">Loading calendar…</p>
          ) : (
            <>
              <div className="mb-3 flex items-center gap-2">
                <Button
                  aria-label="Previous month"
                  onClick={() => setMonth(addMonths(month, -1))}
                >
                  ◀
                </Button>
                <p className="min-w-[10rem] text-center font-mono text-meta uppercase tracking-[0.16em] text-[var(--ac)]">
                  {monthHeading(month)}
                </p>
                <Button aria-label="Next month" onClick={() => setMonth(addMonths(month, 1))}>
                  ▶
                </Button>
              </div>

              {eventsError && (
                <p className="mb-3 font-mono text-meta text-danger" role="alert">
                  calendar unavailable :: {(eventsError as Error).message}
                </p>
              )}
              {allHidden && (
                <p className="mb-3 font-mono text-meta text-ink-faint">
                  Every calendar is hidden — tick one in CALENDARS to see it here.
                </p>
              )}

              <MonthGrid
                month={month}
                selected={selected}
                today={today}
                byDay={byDay}
                calendars={calendars}
                onSelect={select}
                onOpenEvent={openEvent}
                onCreate={(day) => {
                  select(day);
                  setDialog({ event: null, day });
                }}
              />
              <p className="mt-2 font-mono text-micro text-ink-faint">
                Click a day to open it · double-click to add an event · arrow keys move, PgUp/PgDn
                change month
              </p>
            </>
          )}
        </Panel>

        <div className="flex flex-col gap-4">
          {selected && (
            <DayRail
              day={selected}
              events={byDay.get(selected) ?? []}
              calendars={calendars}
              loading={isLoading}
              onOpenEvent={openEvent}
              onCreate={(day) => setDialog({ event: null, day })}
            />
          )}

          <CalendarsPanel
            calendars={calendars}
            loadError={calendarsError}
            hidden={hidden}
            onToggle={(id) =>
              setHidden((current) => {
                const next = new Set(current);
                if (next.has(id)) next.delete(id);
                else next.add(id);
                return next;
              })
            }
            onChanged={() => {
              refreshCalendars();
              refreshEvents();
            }}
          />
        </div>
      </div>

      {dialog && (
        <EventDialog
          // Keyed by the event being edited so switching from one event to
          // another rebuilds the form rather than reusing the first one's
          // captured initial state.
          key={dialog.event?.id ?? `new-${dialog.day}`}
          event={dialog.event}
          day={dialog.day}
          calendars={calendars}
          onClose={() => setDialog(null)}
          onDone={done}
        />
      )}
    </>
  );
}
