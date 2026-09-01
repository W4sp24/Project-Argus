"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import useSWR from "swr";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { fromDateTimeInput } from "@/components/calendar/dates";
import Button from "@/components/ui/Button";
import Field, { FIELD_CONTROL } from "@/components/ui/Field";
import {
  actionFieldName,
  apiFetch,
  fetcher,
  runAutomationAction,
  useAgenda,
  useAutomationActions,
  useSourceProvenance,
} from "@/lib/api";
import { createEvent, isWritable, useCalendars } from "@/lib/calendar";

interface ScheduleBlock {
  title: string;
  start: string;
  end: string;
}

interface Suggestion {
  id: number;
  created_at: string;
  kind: "schedule" | "task" | "note";
  payload: Record<string, unknown>;
  rationale: string;
}

type Row =
  | {
      key: string;
      kind: "event";
      start: string;
      end: string;
      title: string;
      allDay: boolean;
      location?: string | null;
      /** From a calendar Argus holds but may not write to — an .ics feed. */
      readOnly: boolean;
    }
  | {
      key: string;
      kind: "suggestion";
      start: string;
      end: string;
      title: string;
      suggestionId: number;
      rationale: string;
      isFirstOfGroup: boolean;
    };

const KIND_WORDS: [RegExp, string][] = [
  [/study|read|review|exam|flashcard/i, "STUDY"],
  [/code|debug|build|ship|deploy|pr\b/i, "CODE"],
  [/break|lunch|rest|walk|nap/i, "REST"],
];

function classifyKind(title: string): "DEEP" | "STUDY" | "CODE" | "REST" {
  for (const [re, kind] of KIND_WORDS) {
    if (re.test(title)) return kind as "STUDY" | "CODE" | "REST";
  }
  return "DEEP";
}

function timeLabel(iso: string, allDay = false): string {
  if (allDay || !iso.includes("T")) return "all day";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

function durationLabel(start: string, end: string): string {
  const a = new Date(start).getTime();
  const b = new Date(end).getTime();
  if (isNaN(a) || isNaN(b) || b <= a) return "";
  const minutes = Math.round((b - a) / 60000);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours}h${rest}m` : `${hours}h`;
}

/** ISO date `offset` days from today, in the *viewer's* timezone. */
function isoDay(offset: number): string {
  const d = new Date();
  d.setDate(d.getDate() + offset);
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${month}-${day}`;
}

/** "TODAY" / "TOMORROW" / "MON 11 AUG" for the day-nav readout. */
function dayLabel(offset: number, iso: string): string {
  if (offset === 0) return "TODAY";
  if (offset === 1) return "TOMORROW";
  if (offset === -1) return "YESTERDAY";
  const d = new Date(`${iso}T00:00:00`);
  if (isNaN(d.getTime())) return iso;
  return d
    .toLocaleDateString("en-US", { weekday: "short", day: "numeric", month: "short" })
    .toUpperCase();
}

/**
 * PLANNER.TIMELINE (§4 General, left column) — agenda events + review-queue
 * schedule suggestions on one chronological rail, with a now-line computed
 * once on mount (§10: no second perpetual timer — TopBar already owns the
 * clock interval).
 *
 * `＋ EVENT` writes to **Argus's own calendar** unless an n8n `calendar.create`
 * action is installed, in which case that workflow still wins — it is pointed
 * at a Google calendar the user chose, and quietly writing somewhere else
 * would be the more surprising answer. Before the local store existed this
 * button was a link to /automations: with no n8n there was nothing behind it
 * at all.
 */
export default function PlannerTimeline() {
  // `offset` is the only day state. At 0 the SWR key stays the bare
  // `/api/agenda` so the server-rendered markup and the first client render
  // agree; a date enters the key only once the user navigates, which cannot
  // happen before mount. Computing "today" during render to build the key
  // would reimport the hydration bug documented on `now` below.
  const [offset, setOffset] = useState(0);
  const day = offset === 0 ? null : isoDay(offset);
  const { data: agenda, error, isLoading, mutate: mutateAgenda } = useAgenda(day);
  const { data: provenance } = useSourceProvenance();
  const { actions } = useAutomationActions();
  // Which calendar an event belongs to is what decides whether it can be
  // written back, and the event alone cannot answer that — see `isWritable`.
  const { data: calendars } = useCalendars();
  const { data: suggestions, mutate: mutateReview } = useSWR<Suggestion[]>("/api/review", fetcher);
  const [busy, setBusy] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, string>>({});
  const [adding, setAdding] = useState(false);
  const [draft, setDraft] = useState({ title: "", start: "", end: "" });
  const [saving, setSaving] = useState(false);
  const { show: flash } = useToast();

  const n8nCreate = actions["calendar.create"];
  const gcalError = agenda?.connector_errors?.gcal;
  // The now-line is client-only. Reading the clock during render bakes the
  // *server's* second into the HTML, and the client hydrates a moment later
  // with a different one — a text mismatch React cannot patch, so it threw
  // away the whole server tree and re-rendered the entire page on the client.
  // Both the label and the line's position depend on `now`, so neither can be
  // rendered until after mount.
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => setNow(new Date()), []);

  const scheduleSuggestions = (suggestions ?? []).filter((s) => s.kind === "schedule");

  const rows: Row[] = useMemo(() => {
    const list: Row[] = [];
    (agenda?.events ?? []).forEach((event, i) => {
      list.push({
        key: `event-${i}`,
        kind: "event",
        start: event.start,
        end: event.end,
        title: event.title,
        allDay: event.all_day,
        location: event.location,
        // Only events belonging to a calendar Argus manages can be marked:
        // `calendar_id` is null for the Google connector and for an n8n
        // timeline entry, and neither is "read-only" in the sense this chip
        // means — one is edited in Google, the other via its workflow, and
        // neither ever offered an edit here to withhold.
        readOnly: !!event.calendar_id && !isWritable(event, calendars),
      });
    });
    scheduleSuggestions.forEach((suggestion) => {
      const blocks = (suggestion.payload.blocks as ScheduleBlock[]) ?? [];
      blocks.forEach((block, i) => {
        list.push({
          key: `sugg-${suggestion.id}-${i}`,
          kind: "suggestion",
          start: block.start,
          end: block.end,
          title: block.title,
          suggestionId: suggestion.id,
          rationale: suggestion.rationale,
          isFirstOfGroup: i === 0,
        });
      });
    });
    return list.sort((a, b) => new Date(a.start).getTime() - new Date(b.start).getTime());
  }, [agenda, calendars, scheduleSuggestions]);

  // -1 until mounted, which matches no row and no tail — so the server renders
  // the agenda without a now-line and the client adds it. Also -1 on any day
  // but today: "now" is not on that rail at all, and drawing it there would
  // claim a position in a day it does not belong to.
  const showNow = now !== null && offset === 0;
  const nowIndex = showNow
    ? rows.findIndex((row) => new Date(row.start).getTime() > now.getTime())
    : -1;
  const nowPosition = showNow ? (nowIndex === -1 ? rows.length : nowIndex) : -1;
  const nowLabel = now ? now.toLocaleTimeString("en-US", { hour12: false }) : "";

  async function act(id: number, action: "approve" | "dismiss") {
    setBusy(id);
    try {
      const response = await apiFetch(`/api/review/${id}/${action}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: action === "dismiss" ? JSON.stringify({ reason: "" }) : "{}",
      });
      const payload = await response.json().catch(() => ({}));
      setResults((prev) => ({
        ...prev,
        [id]: response.ok
          ? action === "approve"
            ? "applied via writer — vault snapshot taken first"
            : "dismissed"
          : String(payload.detail ?? "action failed"),
      }));
    } catch {
      setResults((prev) => ({ ...prev, [id]: "action failed — is the backend running?" }));
    }
    setBusy(null);
    mutateReview();
  }

  /**
   * Open the form with defaults, or close it.
   *
   * The defaults are computed here rather than during render for the reason
   * documented on `now`: a click is necessarily after mount, so reading the
   * viewed day (and, through it, the clock) cannot desync the two renders.
   * They seed the *local* path only — the n8n workflow treats start and end
   * as optional and its own defaults are the workflow's business.
   */
  function toggleAdding() {
    if (adding) {
      setAdding(false);
      return;
    }
    const day = agenda?.date ?? isoDay(offset);
    // 09:00–10:00, matching the calendar page's new-event default: a
    // predictable time is one edit away, a clever one is a surprise every time.
    setDraft(
      n8nCreate
        ? { title: "", start: "", end: "" }
        : { title: "", start: `${day}T09:00`, end: `${day}T10:00` },
    );
    setAdding(true);
  }

  /**
   * Create an event — locally, or by firing the installed `calendar.create`
   * workflow when there is one.
   *
   * On the n8n path the payload keys come from the action's own declared
   * fields rather than being hardcoded: n8n's Form Trigger has no machine name
   * apart from the visible label, so renaming "Title" in n8n renames the
   * payload key, and a hardcoded one would post a body the workflow reads as
   * empty.
   */
  async function submitEvent(event: React.FormEvent) {
    event.preventDefault();
    if (saving) return;
    const title = draft.title.trim();
    if (!title) return;
    setSaving(true);
    try {
      if (!n8nCreate) {
        await createEvent({
          title,
          start: fromDateTimeInput(draft.start),
          end: fromDateTimeInput(draft.end),
        });
        flash(`added :: ${title}`);
        setDraft({ title: "", start: "", end: "" });
        setAdding(false);
        // Unlike the n8n path this one is already durable, so the rail shows
        // it on this revalidate rather than on some workflow's next poll.
        mutateAgenda();
      } else {
        const values: Record<string, unknown> = {};
        const titleKey = actionFieldName(n8nCreate, "Title");
        const startKey = actionFieldName(n8nCreate, "Start");
        const endKey = actionFieldName(n8nCreate, "End");
        if (titleKey) values[titleKey] = title;
        if (startKey && draft.start) values[startKey] = draft.start;
        if (endKey && draft.end) values[endKey] = draft.end;

        const result = await runAutomationAction(n8nCreate, values);
        if (result.status === "ok" || result.status === "running") {
          flash(`sent to n8n :: ${n8nCreate.workflow_name}`);
          setDraft({ title: "", start: "", end: "" });
          setAdding(false);
          // The event lands in Google, not in Argus — it only appears here on
          // the calendar workflow's next poll (up to 15 minutes). Revalidate
          // anyway in case that poll has already been and gone.
          mutateAgenda();
        } else {
          flash(result.message ?? `n8n reported ${result.status}`);
        }
      }
    } catch (err) {
      flash(
        err instanceof Error
          ? err.message
          : n8nCreate
            ? "Could not reach n8n"
            : "Could not save the event",
      );
    }
    setSaving(false);
  }

  // An end at or before the start is not an event; the store would take it and
  // the rail would show a zero-length row with no duration at all.
  const badWindow = !n8nCreate && (!draft.start || !draft.end || draft.end <= draft.start);

  const gcalConfigured = agenda?.configured.gcal ?? false;

  return (
    <Panel
      label="PLANNER.TIMELINE"
      headerRight={
        // Provenance beats configuration once a workflow is supplying the
        // data: with the calendar coming over n8n, "GCAL: WIRED" would name
        // a connector that is no longer answering. The backend decides which
        // path wins; this only reports it.
        provenance?.calendar === "n8n" ? (
          <span
            className="font-mono text-meta uppercase tracking-wide text-ok"
            title="Supplied by an n8n workflow pushing the calendar widget, not the built-in connector"
          >
            GCAL: VIA N8N
          </span>
        ) : gcalError ? (
          // A connector that answered with an error is not "wired" — that
          // badge over an empty rail is the app telling the user nothing is
          // scheduled when in fact it could not look.
          <span
            className="font-mono text-meta uppercase tracking-wide text-danger"
            title={gcalError}
          >
            GCAL: FAILING
          </span>
        ) : gcalConfigured ? (
          <span className="font-mono text-meta uppercase tracking-wide text-ok">GCAL: WIRED</span>
        ) : (
          <Link
            href="/system"
            className="font-mono text-meta uppercase tracking-wide text-ink-faint hover:text-[var(--ac)]"
          >
            GCAL: NOT CONNECTED →
          </Link>
        )
      }
    >
      <div className="mb-3 flex items-center gap-2">
        <Button aria-label="Previous day" onClick={() => setOffset((n) => n - 1)}>
          ◀
        </Button>
        <button
          onClick={() => setOffset(0)}
          disabled={offset === 0}
          className="min-w-[7.5rem] font-mono text-meta uppercase tracking-[0.16em] text-ink-faint transition-colors enabled:hover:text-[var(--ac)] disabled:text-[var(--ac)]"
        >
          {dayLabel(offset, agenda?.date ?? "")}
        </button>
        <Button aria-label="Next day" onClick={() => setOffset((n) => n + 1)}>
          ▶
        </Button>
        <span className="flex-1" />
        {/* One button and one accessible name whichever store is behind it:
            the label says what the user gets, not which path delivers it.
            This used to be a link to /automations without n8n — an affordance
            for installing a workflow, standing where the create button goes. */}
        <Button
          variant="primary"
          aria-label="Add calendar event"
          onClick={toggleAdding}
          title={
            !n8nCreate
              ? "Creates an event in your own Argus calendar"
              : n8nCreate.active
                ? `Fires ${n8nCreate.workflow_name} on ${n8nCreate.instance_name || "n8n"}`
                : "Installed but not active — grant its credential in n8n first"
          }
        >
          ＋ EVENT
        </Button>
      </div>

      {adding && (
        <form onSubmit={submitEvent} className="mb-4 border border-line p-3">
          {n8nCreate && !n8nCreate.active && (
            <p className="mb-2 font-mono text-meta text-warn" role="status">
              This workflow is installed but not active — grant its Google Calendar
              credential in n8n, then activate it, or the run will fail.
            </p>
          )}
          <div className="flex flex-col gap-2">
            <Field label="Title">
              {(props) => (
                <input
                  {...props}
                  autoFocus
                  value={draft.title}
                  onChange={(e) => setDraft((d) => ({ ...d, title: e.target.value }))}
                  placeholder="Event title"
                  className={FIELD_CONTROL}
                />
              )}
            </Field>
            <div className="flex gap-2">
              {/* A date for n8n, a datetime for the local store. The workflow's
                  fields are whatever its Form Trigger declared and are not
                  ours to redefine; a local event on a timeline rail without a
                  time of day would land at midnight every time. */}
              <Field label="Start" className="flex-1">
                {(props) => (
                  <input
                    {...props}
                    type={n8nCreate ? "date" : "datetime-local"}
                    value={draft.start}
                    onChange={(e) => setDraft((d) => ({ ...d, start: e.target.value }))}
                    className={FIELD_CONTROL}
                  />
                )}
              </Field>
              <Field label="End" className="flex-1">
                {(props) => (
                  <input
                    {...props}
                    type={n8nCreate ? "date" : "datetime-local"}
                    value={draft.end}
                    onChange={(e) => setDraft((d) => ({ ...d, end: e.target.value }))}
                    className={FIELD_CONTROL}
                  />
                )}
              </Field>
            </div>
          </div>
          {badWindow && draft.start && draft.end && (
            <p className="mt-2 font-mono text-meta text-warn" role="status">
              END must be after START.
            </p>
          )}
          <div className="mt-2 flex items-center gap-2">
            <Button
              type="submit"
              variant="primary"
              disabled={saving || !draft.title.trim() || badWindow}
            >
              {saving ? (n8nCreate ? "SENDING…" : "SAVING…") : "CREATE"}
            </Button>
            <Button onClick={() => setAdding(false)}>CANCEL</Button>
            <p className="font-mono text-meta text-ink-faint">
              {n8nCreate ? (
                <>via {n8nCreate.workflow_name}</>
              ) : (
                <>→ your own calendar · edit it on /calendar</>
              )}
            </p>
          </div>
        </form>
      )}

      {gcalError && (
        <p className="mb-3 font-mono text-meta text-danger" role="alert">
          calendar unavailable :: {gcalError}
        </p>
      )}

      {/* Distinguish "still fetching" from "fetched, and there is nothing" —
          collapsing both into one blank panel is what made a broken backend
          and a free afternoon look identical. */}
      {error && !agenda ? (
        <p className="text-sm text-danger" role="alert">
          Couldn&apos;t load the agenda — is the backend running?
        </p>
      ) : isLoading && !agenda ? (
        <p className="text-sm text-ink-muted">Loading agenda…</p>
      ) : (
        rows.length === 0 && (
          <p className="text-sm text-ink-muted">
            {offset === 0 ? "Nothing scheduled today." : "Nothing scheduled."}
          </p>
        )
      )}
      <ol className="space-y-0">
        {rows.map((row, i) => (
          <li key={row.key}>
            {i === nowPosition && <NowLine label={nowLabel} />}
            <div className="flex gap-3 py-1.5">
              <div className="w-16 shrink-0 text-right">
                <p className="font-mono text-label font-semibold text-ink">
                  {timeLabel(row.start, row.kind === "event" && row.allDay)}
                </p>
                {!(row.kind === "event" && row.allDay) && (
                  <p className="font-mono text-meta text-ink-faint">{timeLabel(row.end)}</p>
                )}
              </div>
              <div
                className="min-w-0 flex-1 border-l-[3px] px-3 py-1.5"
                style={{
                  borderColor: "var(--ac)",
                  borderLeftStyle: row.kind === "suggestion" ? "dashed" : "solid",
                  background: "var(--ac-bg)",
                  opacity: row.kind === "suggestion" ? 0.8 : 1,
                }}
              >
                <div className="flex items-center gap-2">
                  <p className="min-w-0 flex-1 truncate text-lead text-ink-bright">{row.title}</p>
                  {row.kind === "event" && row.readOnly && (
                    // An .ics feed is a published file with no way to write
                    // back, so this row has no edit affordance and says why —
                    // rather than offering one that would 422.
                    <span
                      className="shrink-0 border border-line px-1 font-mono text-micro uppercase tracking-wide text-ink-faint"
                      title="From a subscribed feed — read-only here. Change it where it is published."
                    >
                      READ-ONLY
                    </span>
                  )}
                  <span className="shrink-0 font-mono text-micro uppercase tracking-wide text-ink-faint">
                    {classifyKind(row.title)}
                  </span>
                  <span className="shrink-0 font-mono text-meta text-ink-faint">
                    {durationLabel(row.start, row.end)}
                  </span>
                </div>
                {row.kind === "event" && row.location && (
                  <p className="mt-0.5 truncate font-mono text-meta text-ink-faint">
                    @ {row.location}
                  </p>
                )}
                {row.kind === "suggestion" && row.isFirstOfGroup && (
                  <div className="mt-1.5">
                    {results[row.suggestionId] ? (
                      <p className="font-mono text-label text-ink-muted">{results[row.suggestionId]}</p>
                    ) : (
                      <div className="flex items-center gap-3">
                        <p className="font-mono text-meta text-ink-faint">{row.rationale}</p>
                        <button
                          disabled={busy !== null}
                          onClick={() => act(row.suggestionId, "approve")}
                          className="font-mono text-meta uppercase tracking-wide text-ok hover:underline disabled:opacity-70"
                        >
                          [Y] APPROVE
                        </button>
                        <button
                          disabled={busy !== null}
                          onClick={() => act(row.suggestionId, "dismiss")}
                          className="font-mono text-meta uppercase tracking-wide text-danger hover:underline disabled:opacity-70"
                        >
                          [N] DISMISS
                        </button>
                      </div>
                    )}
                  </div>
                )}
              </div>
            </div>
          </li>
        ))}
        {nowPosition === rows.length && <NowLine label={nowLabel} />}
      </ol>
    </Panel>
  );
}

function NowLine({ label }: { label: string }) {
  return (
    <div className="my-1 flex items-center gap-2 pl-[76px]">
      <span className="h-px flex-1 bg-[var(--ac)]" />
      <span className="font-mono text-meta text-[var(--ac)]">now {label}</span>
    </div>
  );
}
