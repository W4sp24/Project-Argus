"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
import SubscribeDialog from "@/components/calendar/SubscribeDialog";
import {
  ICS_KIND,
  calendarColor,
  exportIcsUrl,
  removeSubscription,
  syncSubscription,
  type CalendarInfo,
} from "@/lib/calendar";
import { formatRelativeTime } from "@/lib/relativeTime";

/**
 * Every calendar: which ones the grid shows, and the state of the feeds behind
 * the subscribed ones.
 *
 * The visibility ticks are the calendar picker — client state owned by the
 * page, because hiding a calendar is a view preference and not something the
 * server needs to hear about.
 *
 * A subscription's row carries its last sync and, when there is one, the error
 * from it. That pairing is the point: a feed that stopped answering yesterday
 * renders as an empty calendar, which is indistinguishable from a working one
 * with nothing in it unless the failure is written down somewhere.
 */
export default function CalendarsPanel({
  calendars,
  loadError,
  hidden,
  onToggle,
  onChanged,
}: {
  calendars: CalendarInfo[] | undefined;
  loadError: unknown;
  /** Calendar ids the grid is *not* showing. */
  hidden: Set<string>;
  onToggle: (id: string) => void;
  /** Revalidate calendars and events — a sync or a removal changes both. */
  onChanged: () => void;
}) {
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function sync(calendar: CalendarInfo) {
    setBusy(calendar.id);
    setError(null);
    try {
      const synced = await syncSubscription(calendar.id);
      // The route answers 200 with the error recorded on the row when the feed
      // parsed but had nothing to say, so read the result rather than assuming
      // a 2xx meant success.
      if (synced.last_sync_error) setError(`${calendar.name} :: ${synced.last_sync_error}`);
      else show(`synced :: ${calendar.name}`);
      onChanged();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : `could not sync ${calendar.name}`);
    }
    setBusy(null);
  }

  async function remove(calendar: CalendarInfo) {
    const answer = await confirm({
      label: "Remove subscription",
      message: `Stop subscribing to "${calendar.name}"?`,
      detail: "Its events go from the calendar, and the stored feed URL is deleted from the keyring.",
      confirmLabel: "Remove",
    });
    if (answer === null) return;
    setBusy(calendar.id);
    setError(null);
    try {
      await removeSubscription(calendar.id);
      show(`removed :: ${calendar.name}`);
      onChanged();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : `could not remove ${calendar.name}`);
    }
    setBusy(null);
  }

  return (
    <Panel
      label="CALENDARS"
      headerRight={
        <div className="flex items-center gap-2">
          {/* Absolute URL: in the desktop shell the backend is not where this
              page is served from, so a relative href would 404. */}
          <a
            href={exportIcsUrl()}
            download="argus.ics"
            className="font-mono text-meta uppercase tracking-wide text-ink-faint hover:text-[var(--ac)]"
            title="Every local event as standard iCalendar"
          >
            EXPORT .ICS
          </a>
          <Button onClick={() => setAdding(true)}>＋ SUBSCRIBE</Button>
        </div>
      }
    >
      {loadError ? (
        <p className="text-sm text-danger" role="alert">
          Couldn&apos;t load your calendars — is the backend running?
        </p>
      ) : !calendars ? (
        <p className="text-sm text-ink-muted">Loading…</p>
      ) : (
        <ul className="space-y-2">
          {calendars.map((calendar) => (
            <li key={calendar.id} className="border border-line px-2 py-1.5">
              <div className="flex items-center gap-2">
                <label className="flex min-w-0 flex-1 items-center gap-2">
                  <input
                    type="checkbox"
                    checked={!hidden.has(calendar.id)}
                    onChange={() => onToggle(calendar.id)}
                    className="accent-[var(--ac)]"
                    aria-label={`Show ${calendar.name}`}
                  />
                  <span
                    aria-hidden
                    className="h-3 w-1 shrink-0"
                    style={{ background: calendarColor(calendar) }}
                  />
                  <span className="truncate text-label text-ink">{calendar.name}</span>
                </label>

                {calendar.kind === ICS_KIND && (
                  <>
                    <Button
                      variant="quiet"
                      disabled={busy === calendar.id}
                      onClick={() => sync(calendar)}
                      aria-label={`Sync ${calendar.name}`}
                    >
                      {busy === calendar.id ? "…" : "SYNC"}
                    </Button>
                    <Button
                      variant="ghost"
                      disabled={busy === calendar.id}
                      onClick={() => remove(calendar)}
                      aria-label={`Remove ${calendar.name}`}
                    >
                      ×
                    </Button>
                  </>
                )}
              </div>

              <p className="mt-0.5 flex flex-wrap items-center gap-x-2 pl-6 font-mono text-micro text-ink-faint">
                <span className="uppercase tracking-[0.1em]">{calendar.kind}</span>
                {calendar.url_display && <span className="truncate">{calendar.url_display}</span>}
                {calendar.kind === ICS_KIND && (
                  <span>synced {formatRelativeTime(calendar.last_sync_at)}</span>
                )}
              </p>
              {calendar.last_sync_error && (
                <p className="pl-6 font-mono text-micro text-danger">
                  {calendar.last_sync_error}
                </p>
              )}
            </li>
          ))}
        </ul>
      )}

      {error && (
        <p className="mt-3 font-mono text-meta text-danger" role="alert">
          {error}
        </p>
      )}

      {adding && (
        <SubscribeDialog
          onClose={() => setAdding(false)}
          onAdded={(calendar) => {
            setAdding(false);
            show(`subscribed :: ${calendar.name}`);
            onChanged();
          }}
        />
      )}
      {confirmDialog}
    </Panel>
  );
}
