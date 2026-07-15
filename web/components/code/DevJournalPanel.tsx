"use client";

import Link from "next/link";
import { useJournalSessions } from "@/lib/api";
import Panel from "@/components/Panel";
import { formatRelativeTime } from "@/lib/relativeTime";

const MAX_ROWS = 8;

/**
 * DEV.JOURNAL (§4 Code) — real `90-Meta/` data via `useJournalSessions`,
 * restyled as compact mono rows (the full detail view stays at `/journal`).
 */
export default function DevJournalPanel() {
  const { data: sessions } = useJournalSessions();
  const rows = (sessions ?? []).slice(0, MAX_ROWS);

  return (
    <Panel
      label="DEV.JOURNAL"
      headerRight={
        <Link href="/journal" className="font-mono text-[10px] uppercase tracking-wide text-ink-faint hover:text-[var(--ac)]">
          open →
        </Link>
      }
    >
      {!sessions ? (
        <p className="text-sm text-ink-faint">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="text-sm text-ink-muted">No sessions yet — they land here automatically from Claude Code hooks.</p>
      ) : (
        <ul className="divide-y divide-line">
          {rows.map((session) => (
            <li key={session.path} className="flex items-center justify-between gap-3 py-1.5 font-mono text-[11.5px]">
              <span className="shrink-0 text-ink-faint">{session.date}</span>
              <span className="min-w-0 flex-1 truncate text-ink">{session.project}</span>
              {session.branch && <span className="hidden shrink-0 text-ink-faint sm:inline">{session.branch}</span>}
              <span className="shrink-0 text-ink-faint">{session.files}f</span>
              <span className={`shrink-0 text-[9.5px] uppercase ${session.has_narrative ? "text-[var(--ac)]" : "text-ink-faint"}`}>
                {session.has_narrative ? "narrative" : "stub"}
              </span>
            </li>
          ))}
        </ul>
      )}
      {rows[0] && <p className="mt-2 font-mono text-[10px] text-ink-faint">last session {formatRelativeTime(rows[0].date)}</p>}
    </Panel>
  );
}
