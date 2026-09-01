"use client";

import { useState } from "react";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import { useConfirm } from "@/components/ui/useConfirm";
import EventDetails from "@/components/calendar/EventDetails";
import EventFields from "@/components/calendar/EventFields";
import { diffEvent, formFor, toWire, type Form } from "@/components/calendar/eventForm";
import {
  LOCAL_KIND,
  createEvent,
  deleteEvent,
  isOccurrence,
  isWritable,
  updateEvent,
  type CalendarEvent,
  type CalendarInfo,
} from "@/lib/calendar";

/**
 * Create, edit or delete one event.
 *
 * Two behaviours are load-bearing and easy to lose:
 *
 * - **A subscribed event gets no form**, only `EventDetails`. The API refuses
 *   the write with a 422, so offering the edit would be a lie the user only
 *   finds out about after typing.
 * - **Deleting a recurring event asks which.** `scope=one` cancels that
 *   occurrence and leaves the series running; `scope=series` deletes the row
 *   and every occurrence with it. Guessing either one is destructive.
 */
export default function EventDialog({
  event,
  day,
  calendars,
  onClose,
  onDone,
}: {
  /** `null` to create a new event on `day`. */
  event: CalendarEvent | null;
  day: string;
  calendars: CalendarInfo[] | undefined;
  onClose: () => void;
  onDone: (message: string) => void;
}) {
  const writable = event === null || isWritable(event, calendars);
  // Captured once: the diff a save sends is measured against what was loaded,
  // not against whatever the props are by the time Save is pressed.
  const [initial] = useState<Form>(() => formFor(event, day, calendars));
  const [form, setForm] = useState<Form>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { confirm, confirmDialog } = useConfirm();

  const wire = toWire(form);
  const backwards = wire.end < wire.start;
  const blocked = busy || form.title.trim() === "" || backwards;
  const series = isOccurrence(event?.id);

  async function save(submitted: React.FormEvent) {
    submitted.preventDefault();
    if (blocked) return;
    setBusy(true);
    setError(null);
    try {
      if (event?.id) {
        const patch = diffEvent(toWire(initial), wire, initial.repeat, form.repeat);
        // Nothing changed: closing is the honest outcome, and a PATCH here
        // would still bump the row's `updated_at` for no reason.
        if (Object.keys(patch).length === 0) {
          onClose();
          return;
        }
        await updateEvent(event.id, patch);
        onDone(`updated :: ${wire.title}`);
      } else {
        await createEvent({
          ...wire,
          ...(form.repeat ? { rrule: form.repeat } : {}),
          ...(form.calendarId ? { calendar_id: form.calendarId } : {}),
        });
        onDone(`created :: ${wire.title}`);
      }
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "the save failed");
      setBusy(false);
    }
  }

  async function remove(scope: "one" | "series") {
    if (!event?.id) return;
    const whole = scope === "series" && series;
    const answer = await confirm({
      label: whole ? "Delete series" : "Delete event",
      message: whole ? `Delete every occurrence of "${event.title}"?` : `Delete "${event.title}"?`,
      detail: series
        ? whole
          ? "The whole repeating series goes, past occurrences included."
          : "Only this occurrence is cancelled; the rest of the series stays."
        : undefined,
      confirmLabel: "Delete",
    });
    // `null` is cancelled; `""` is confirmed with no reason given.
    if (answer === null) return;
    setBusy(true);
    setError(null);
    try {
      await deleteEvent(event.id, scope);
      onDone(`deleted :: ${event.title}`);
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "the delete failed");
      setBusy(false);
    }
  }

  const label = event === null ? "New event" : writable ? "Edit event" : "Event details";

  return (
    <Dialog
      label={label}
      onClose={busy ? () => {} : onClose}
      className="w-[34rem] max-w-[calc(100vw-2rem)] p-5"
    >
      <p className="eyebrow mb-3">{`▍${label.toUpperCase()}`}</p>

      {!writable && event ? (
        <EventDetails event={event} calendars={calendars} onClose={onClose} />
      ) : (
        <form onSubmit={save}>
          <EventFields
            form={form}
            onChange={setForm}
            isNew={event === null}
            isSeries={series}
            backwards={backwards}
            writableCalendars={(calendars ?? []).filter((one) => one.kind === LOCAL_KIND)}
          />

          {error && (
            <p className="mt-3 font-mono text-meta text-danger" role="alert">
              {error}
            </p>
          )}

          <div className="mt-5 flex flex-wrap items-center gap-2 border-t border-line pt-4">
            <Button size="md" onClick={onClose} disabled={busy}>
              Cancel
            </Button>
            {event?.id && series && (
              <Button size="md" variant="danger" disabled={busy} onClick={() => remove("one")}>
                Delete this
              </Button>
            )}
            {event?.id && (
              <Button size="md" variant="danger" disabled={busy} onClick={() => remove("series")}>
                {series ? "Delete series" : "Delete"}
              </Button>
            )}
            <Button type="submit" size="md" variant="primary" disabled={blocked} className="ml-auto">
              {busy ? "SAVING…" : event ? "SAVE" : "CREATE"}
            </Button>
          </div>
        </form>
      )}

      {confirmDialog}
    </Dialog>
  );
}
