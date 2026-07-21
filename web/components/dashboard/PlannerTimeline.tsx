"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import useSWR from "swr";
import Panel from "@/components/Panel";
import { apiFetch, fetcher } from "@/lib/api";

interface CalendarEvent {
  title: string;
  start: string;
  end: string;
  all_day: boolean;
}

interface Agenda {
  date: string;
  events: CalendarEvent[];
  configured: { gcal: boolean; todoist: boolean };
}

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
  | { key: string; kind: "event"; start: string; end: string; title: string }
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

/**
 * PLANNER.TIMELINE (§4 General, left column) — agenda events + review-queue
 * schedule suggestions on one chronological rail, with a now-line computed
 * once on mount (§10: no second perpetual timer — TopBar already owns the
 * clock interval).
 */
export default function PlannerTimeline() {
  const { data: agenda } = useSWR<Agenda>("/api/agenda", fetcher);
  const { data: suggestions, mutate: mutateReview } = useSWR<Suggestion[]>("/api/review", fetcher);
  const [busy, setBusy] = useState<number | null>(null);
  const [results, setResults] = useState<Record<number, string>>({});
  const [now] = useState(() => new Date());

  const scheduleSuggestions = (suggestions ?? []).filter((s) => s.kind === "schedule");

  const rows: Row[] = useMemo(() => {
    const list: Row[] = [];
    (agenda?.events ?? []).forEach((event, i) => {
      list.push({ key: `event-${i}`, kind: "event", start: event.start, end: event.end, title: event.title });
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
  }, [agenda, scheduleSuggestions]);

  const nowIndex = rows.findIndex((row) => new Date(row.start).getTime() > now.getTime());
  const nowPosition = nowIndex === -1 ? rows.length : nowIndex;
  const nowLabel = now.toLocaleTimeString("en-US", { hour12: false });

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

  const gcalConfigured = agenda?.configured.gcal ?? false;

  return (
    <Panel
      label="PLANNER.TIMELINE"
      headerRight={
        gcalConfigured ? (
          <span className="font-mono text-[10px] uppercase tracking-wide text-ok">GCAL: WIRED</span>
        ) : (
          <Link
            href="/system"
            className="font-mono text-[10px] uppercase tracking-wide text-ink-faint hover:text-[var(--ac)]"
          >
            GCAL: NOT CONNECTED →
          </Link>
        )
      }
    >
      {rows.length === 0 && (
        <p className="text-sm text-ink-muted">Nothing scheduled today.</p>
      )}
      <ol className="space-y-0">
        {rows.map((row, i) => (
          <li key={row.key}>
            {i === nowPosition && <NowLine label={nowLabel} />}
            <div className="flex gap-3 py-1.5">
              <div className="w-16 shrink-0 text-right">
                <p className="font-mono text-[12px] font-semibold text-ink">{timeLabel(row.start)}</p>
                <p className="font-mono text-[10px] text-ink-faint">{timeLabel(row.end)}</p>
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
                  <p className="min-w-0 flex-1 truncate text-[14px] text-ink-bright">{row.title}</p>
                  <span className="shrink-0 font-mono text-[9px] uppercase tracking-wide text-ink-faint">
                    {classifyKind(row.title)}
                  </span>
                  <span className="shrink-0 font-mono text-[10px] text-ink-faint">
                    {durationLabel(row.start, row.end)}
                  </span>
                </div>
                {row.kind === "suggestion" && row.isFirstOfGroup && (
                  <div className="mt-1.5">
                    {results[row.suggestionId] ? (
                      <p className="font-mono text-[11px] text-ink-muted">{results[row.suggestionId]}</p>
                    ) : (
                      <div className="flex items-center gap-3">
                        <p className="font-mono text-[10px] text-ink-faint">{row.rationale}</p>
                        <button
                          disabled={busy !== null}
                          onClick={() => act(row.suggestionId, "approve")}
                          className="font-mono text-[10px] uppercase tracking-wide text-ok hover:underline disabled:opacity-40"
                        >
                          [Y] APPROVE
                        </button>
                        <button
                          disabled={busy !== null}
                          onClick={() => act(row.suggestionId, "dismiss")}
                          className="font-mono text-[10px] uppercase tracking-wide text-danger hover:underline disabled:opacity-40"
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
      <span className="font-mono text-[10px] text-[var(--ac)]">now {label}</span>
    </div>
  );
}
