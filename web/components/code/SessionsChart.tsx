"use client";

import MiniLineChart from "@/components/charts/MiniLineChart";
import Panel from "@/components/Panel";
import { useJournalSessions } from "@/lib/api";

const DAYS = 14;

function isoDaysAgo(n: number): string {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return d.toISOString().slice(0, 10);
}

function shortDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00`);
  return `${d.getMonth() + 1}/${d.getDate()}`;
}

/**
 * SESSIONS.14D (§4 Code right rail) — the spec calls this panel
 * "COMMITS.14D", but `useJournalSessions` reports dev-journal *sessions*
 * (one entry per Claude Code session, from `90-Meta/sessions/`), not git
 * commits — there's no commit-count endpoint in this branch's ancestry.
 * Labeling it SESSIONS.14D keeps the chart honest about what it measures;
 * noted as a deviation in the phase report.
 */
export default function SessionsChart() {
  const { data: sessions } = useJournalSessions();

  const days = Array.from({ length: DAYS }, (_, i) => isoDaysAgo(DAYS - 1 - i));
  const counts = days.map((day) => (sessions ?? []).filter((session) => session.date === day).length);
  const total = counts.reduce((sum, n) => sum + n, 0);

  return (
    <Panel label="SESSIONS.14D">
      {!sessions ? (
        <p className="text-sm text-ink-faint">Loading…</p>
      ) : (
        <>
          <p className="font-mono text-[11px] text-ink-muted">{total} sessions · last 14 days</p>
          <div className="mt-2">
            <MiniLineChart values={counts} labels={[shortDate(days[0]), shortDate(days[days.length - 1])]} />
          </div>
        </>
      )}
    </Panel>
  );
}
