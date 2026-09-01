"use client";

import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import { KEEP_REPEAT, REPEATS, withAllDay, type Form } from "@/components/calendar/eventForm";
import type { CalendarInfo } from "@/lib/calendar";

/**
 * The event form's controls.
 *
 * Presentational — it owns no state, so `EventDialog` keeps the one copy of
 * the draft and the save/delete logic, and this file stays the part you read
 * when the question is "what can you type into an event".
 */
export default function EventFields({
  form,
  onChange,
  isNew,
  isSeries,
  backwards,
  writableCalendars,
}: {
  form: Form;
  onChange: (next: Form) => void;
  isNew: boolean;
  /** One occurrence of a recurring series — the edit applies to all of them. */
  isSeries: boolean;
  /** End is before start; the End field carries the message. */
  backwards: boolean;
  writableCalendars: CalendarInfo[];
}) {
  function set<K extends keyof Form>(key: K, value: Form[K]) {
    onChange({ ...form, [key]: value });
  }

  return (
    <div className="flex flex-col gap-3">
      <Field label="Title">
        {(props) => (
          <input
            {...props}
            autoFocus
            value={form.title}
            onChange={(changed) => set("title", changed.target.value)}
            placeholder="Event title"
            className={FIELD_CONTROL}
          />
        )}
      </Field>

      <label className="flex items-center gap-2 font-mono text-meta uppercase tracking-[0.1em] text-ink-faint">
        <input
          type="checkbox"
          checked={form.allDay}
          onChange={(changed) => onChange(withAllDay(form, changed.target.checked))}
          className="accent-[var(--ac)]"
        />
        All day
      </label>

      <div className="flex flex-wrap gap-2">
        <Field label="Start" className="min-w-[12rem] flex-1">
          {(props) => (
            <input
              {...props}
              type={form.allDay ? "date" : "datetime-local"}
              value={form.start}
              onChange={(changed) => set("start", changed.target.value)}
              className={FIELD_CONTROL}
            />
          )}
        </Field>
        <Field
          label="End"
          className="min-w-[12rem] flex-1"
          error={backwards ? "End is before start." : undefined}
          hint={form.allDay ? "The last day, included." : undefined}
        >
          {(props) => (
            <input
              {...props}
              type={form.allDay ? "date" : "datetime-local"}
              value={form.end}
              onChange={(changed) => set("end", changed.target.value)}
              className={FIELD_CONTROL}
            />
          )}
        </Field>
      </div>

      <Field
        label="Repeats"
        hint={
          isSeries
            ? "One occurrence of a series — a change here applies to all of them."
            : undefined
        }
      >
        {(props) => (
          <select
            {...props}
            value={form.repeat}
            onChange={(changed) => set("repeat", changed.target.value)}
            className={FIELD_CONTROL}
          >
            {/* Editing only: the stored rule is not on the wire, so "leave it
                alone" is the only safe default. See KEEP_REPEAT. */}
            {!isNew && <option value={KEEP_REPEAT}>Leave unchanged</option>}
            {REPEATS.map((repeat) => (
              <option key={repeat.value} value={repeat.value}>
                {repeat.label}
              </option>
            ))}
          </select>
        )}
      </Field>

      <Field label="Location">
        {(props) => (
          <input
            {...props}
            value={form.location}
            onChange={(changed) => set("location", changed.target.value)}
            className={FIELD_CONTROL}
          />
        )}
      </Field>

      <Field label="Notes">
        {(props) => (
          <textarea
            {...props}
            rows={3}
            value={form.notes}
            onChange={(changed) => set("notes", changed.target.value)}
            className={`${FIELD_CONTROL} resize-none leading-relaxed`}
          />
        )}
      </Field>

      {/* Only when there is a choice to make: one local calendar is the default
          install, and a select with a single option is a control that cannot
          be used. Creation only — moving an event between calendars is not
          something the API offers. */}
      {isNew && writableCalendars.length > 1 && (
        <Field label="Calendar">
          {(props) => (
            <select
              {...props}
              value={form.calendarId}
              onChange={(changed) => set("calendarId", changed.target.value)}
              className={FIELD_CONTROL}
            >
              {writableCalendars.map((calendar) => (
                <option key={calendar.id} value={calendar.id}>
                  {calendar.name}
                </option>
              ))}
            </select>
          )}
        </Field>
      )}
    </div>
  );
}
