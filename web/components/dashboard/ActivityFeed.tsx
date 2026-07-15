"use client";

import useSWR from "swr";
import Panel from "@/components/Panel";
import { fetcher, mutateJSON, useActivity } from "@/lib/api";

const KIND_BADGE: Record<string, string> = {
  note: "text-[var(--ac)]",
  approval: "text-ok",
  exam: "text-danger",
};

function relative(when: string): string {
  const then = new Date(when.replace(" ", "T"));
  const minutes = Math.max(0, Math.round((Date.now() - then.getTime()) / 60000));
  if (minutes < 60) return `${minutes}m ago`;
  if (minutes < 60 * 24) return `${Math.round(minutes / 60)}h ago`;
  return `${Math.round(minutes / (60 * 24))}d ago`;
}

/** ACTIVITY.FEED (§4 General, right rail) — restyled, same data + delete flow. */
export default function ActivityFeed() {
  const { data: events, mutate } = useActivity();
  const { data: vault } = useSWR<{ name: string }>("/api/vault", fetcher);

  async function removeNote(path: string) {
    if (!window.confirm(`Delete ${path}? A git snapshot makes this undoable.`)) return;
    try {
      await mutateJSON(`/api/note?path=${encodeURIComponent(path)}`, undefined, "DELETE");
    } catch {
      // Feed refresh below surfaces the current truth either way.
    }
    mutate();
  }

  return (
    <Panel label="ACTIVITY.FEED">
      {!events && <p className="text-sm text-ink-faint">Loading…</p>}
      {events && events.length === 0 && <p className="text-sm text-ink-muted">All quiet.</p>}
      <ul className="divide-y divide-line">
        {(events ?? []).map((event, i) => (
          <li key={i} className="flex items-baseline gap-2 py-2 text-[13px]">
            <span className={`shrink-0 font-mono text-[9.5px] uppercase ${KIND_BADGE[event.kind] ?? "text-ink-faint"}`}>
              {event.kind}
            </span>
            {event.path && vault ? (
              <a
                href={`obsidian://open?vault=${encodeURIComponent(vault.name)}&file=${encodeURIComponent(event.path)}`}
                className="min-w-0 flex-1 truncate text-ink-muted underline-offset-2 hover:text-ink hover:underline"
              >
                {event.title}
              </a>
            ) : (
              <span className="min-w-0 flex-1 truncate text-ink-muted">{event.title}</span>
            )}
            {event.kind === "note" && event.path?.startsWith("00-Inbox/") && (
              <button
                aria-label={`Delete ${event.path}`}
                onClick={() => removeNote(event.path!)}
                className="shrink-0 font-mono text-[10px] text-ink-faint hover:text-danger"
              >
                ×
              </button>
            )}
            <span className="shrink-0 font-mono text-[10px] text-ink-faint">{relative(event.when)}</span>
          </li>
        ))}
      </ul>
    </Panel>
  );
}
