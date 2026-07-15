"use client";

import useSWR from "swr";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { fetcher, useJournalSessions } from "@/lib/api";
import { formatRelativeTime } from "@/lib/relativeTime";

interface AgendaConfigured {
  configured: { gcal: boolean; todoist: boolean };
}

/**
 * INTEGRATIONS (§12) — claude code hooks status is real (derived from the
 * newest `useJournalSessions` row: a session exists only because a hook
 * fired); gcal/todoist status is the real `configured` flag off
 * `/api/agenda`; email capture is a static MANUAL rationale (§11 — no inbox
 * access by design, never wired to a connector).
 */
export default function Integrations() {
  const { data: agenda } = useSWR<AgendaConfigured>("/api/agenda", fetcher);
  const { data: sessions } = useJournalSessions();
  const { show } = useToast();

  const lastSession = sessions?.[0];

  const rows: {
    name: string;
    status: "WIRED" | "NOT CONNECTED" | "MANUAL" | "LOADING";
    detail: string;
    connectCommand?: string;
  }[] = [
    {
      name: "claude code hooks",
      status: sessions ? (lastSession ? "WIRED" : "NOT CONNECTED") : "LOADING",
      detail: lastSession
        ? `last session ${formatRelativeTime(lastSession.date)} · ${lastSession.project}`
        : "no session stamps yet — run claude-integration/setup.ps1",
    },
    {
      name: "google calendar",
      status: agenda ? (agenda.configured.gcal ? "WIRED" : "NOT CONNECTED") : "LOADING",
      detail: agenda?.configured.gcal
        ? "merged into PLANNER.TIMELINE"
        : "needs OAuth credentials.json, then the connect command",
      connectCommand: "argus connect gcal",
    },
    {
      name: "todoist",
      status: agenda ? (agenda.configured.todoist ? "WIRED" : "NOT CONNECTED") : "LOADING",
      detail: agenda?.configured.todoist ? "merged into TASKS.DUE" : "needs a personal API token",
      connectCommand: "argus connect todoist <token>",
    },
    {
      name: "email capture",
      status: "MANUAL",
      detail: "paste-only by design — no inbox access, ever (§11)",
    },
  ];

  const STATUS_CLASS: Record<string, string> = {
    WIRED: "border-ok text-ok",
    "NOT CONNECTED": "border-ink-faint text-ink-faint",
    MANUAL: "border-amber-400 text-amber-400",
    LOADING: "border-ink-faint text-ink-faint",
  };

  return (
    <Panel label="INTEGRATIONS">
      <ul className="space-y-2.5">
        {rows.map((row) => (
          <li key={row.name} className="flex flex-wrap items-center gap-2.5 border-b border-line pb-2.5 last:border-b-0 last:pb-0">
            <span className={`shrink-0 border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-[0.1em] ${STATUS_CLASS[row.status]}`}>
              {row.status}
            </span>
            <span className="shrink-0 font-mono text-[12.5px] text-ink">{row.name}</span>
            <span className="min-w-0 flex-1 text-[11.5px] text-ink-muted">{row.detail}</span>
            {row.status === "NOT CONNECTED" && row.connectCommand && (
              <button
                type="button"
                onClick={() => show(`connect :: ${row.connectCommand}`)}
                className="shrink-0 border border-line px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-ink-faint transition-colors hover:border-lineHi hover:text-ink"
              >
                {row.connectCommand}
              </button>
            )}
          </li>
        ))}
      </ul>
    </Panel>
  );
}
