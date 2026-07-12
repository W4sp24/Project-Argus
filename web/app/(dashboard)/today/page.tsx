"use client";

import { useState } from "react";
import useSWR from "swr";
import GlassCard from "@/components/GlassCard";
import { fetcher, useNotes } from "@/lib/api";

interface CalendarEvent {
  title: string;
  start: string;
  end: string;
  all_day: boolean;
}

interface TaskItem {
  text: string;
  due: string | null;
  priority: string | null;
  tags: string[];
  source: string;
}

interface Agenda {
  date: string;
  events: CalendarEvent[];
  tasks: TaskItem[];
  top_tasks: TaskItem[];
  configured: { gcal: boolean; todoist: boolean };
}

const PRIORITY_COLORS: Record<string, string> = {
  highest: "text-accent",
  high: "text-accent",
  medium: "text-primary-soft",
  low: "text-ink-faint",
};

function formatToday(): string {
  return new Date().toLocaleDateString("en-US", {
    weekday: "long",
    month: "long",
    day: "numeric",
    year: "numeric",
  });
}

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 5) return "Burning the midnight oil";
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function eventTime(iso: string): string {
  if (!iso.includes("T")) return "all day";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

export default function TodayPage() {
  const { data: agenda, error, mutate } = useSWR<Agenda>("/api/agenda", fetcher);
  const { data: notes } = useNotes();
  const [capture, setCapture] = useState("");
  const [captureStatus, setCaptureStatus] = useState<string | null>(null);

  async function submitCapture(event: React.FormEvent) {
    event.preventDefault();
    const text = capture.trim();
    if (!text) return;
    setCapture("");
    const response = await fetch("/api/capture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    const payload = await response.json();
    setCaptureStatus(
      response.ok ? `Captured → ${payload.path}` : `Capture failed: ${payload.detail}`,
    );
    mutate();
    setTimeout(() => setCaptureStatus(null), 5000);
  }

  return (
    <>
      <header className="mb-8 animate-rise">
        <p className="eyebrow mb-2">{`// TODAY · ${formatToday()}`}</p>
        <h1 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">
          {greeting()}, <span className="gradient-text">Ethan</span>.
        </h1>
      </header>

      {error && (
        <GlassCard label="OFFLINE" title="Backend unreachable" className="mb-4">
          <p className="text-sm text-ink-muted">
            Start it with{" "}
            <span className="font-mono text-xs text-primary-soft">
              uvicorn backend.main:app --port 8000
            </span>
          </p>
        </GlassCard>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <GlassCard label="AGENDA" title="Schedule" className="xl:col-span-2">
          {agenda && agenda.events.length === 0 && (
            <p className="text-sm text-ink-muted">
              {agenda.configured.gcal
                ? "Nothing on the calendar today."
                : "Connect Google Calendar to see your day: create a Desktop OAuth client, save credentials.json in the repo, then run "}
              {!agenda.configured.gcal && (
                <span className="font-mono text-xs text-primary-soft">friday connect gcal</span>
              )}
            </p>
          )}
          <ul className="space-y-2">
            {(agenda?.events ?? []).map((event, i) => (
              <li key={i} className="flex items-center gap-3">
                <span className="w-20 shrink-0 font-mono text-[11px] text-primary-soft">
                  {eventTime(event.start)}
                </span>
                <span className="h-8 w-px bg-gradient-to-b from-primary/60 to-accent/40" />
                <span className="text-sm">{event.title}</span>
              </li>
            ))}
          </ul>
          {(agenda?.tasks?.length ?? 0) > 0 && (
            <div className="mt-4 border-t border-white/5 pt-3">
              <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
                due today
              </p>
              <ul className="space-y-1.5">
                {agenda!.tasks.map((task, i) => (
                  <li key={i} className="flex items-baseline gap-2 text-sm">
                    <span className="text-ink-faint">○</span>
                    <span className="text-ink-muted">{task.text}</span>
                    {task.source === "todoist" && (
                      <span className="rounded bg-white/5 px-1.5 font-mono text-[10px] text-ink-faint">
                        todoist
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </GlassCard>

        <GlassCard label="CAPTURE" title="Quick capture">
          <p className="mb-3 text-sm text-ink-muted">
            Drop a thought — it files into <span className="font-mono text-xs">00-Inbox/</span>.
          </p>
          <form onSubmit={submitCapture} className="flex gap-2">
            <input
              value={capture}
              onChange={(event) => setCapture(event.target.value)}
              placeholder="e.g. email prof about thesis"
              className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm placeholder:text-ink-faint focus:border-primary-soft/50 focus:outline-none"
            />
            <button
              type="submit"
              disabled={!capture.trim()}
              className="shrink-0 rounded-xl bg-gradient-to-r from-primary to-accent px-4 py-2.5 font-display text-sm text-white disabled:opacity-40"
            >
              Save
            </button>
          </form>
          {captureStatus && (
            <p className="mt-3 font-mono text-[11px] text-primary-soft">{captureStatus}</p>
          )}
        </GlassCard>

        <GlassCard label="TASKS" title="Top 3 today">
          {agenda && agenda.top_tasks.length === 0 && (
            <p className="text-sm text-ink-muted">
              Nothing urgent. Add tasks in your notes with{" "}
              <span className="font-mono text-xs">- [ ] task 📅 date</span>
            </p>
          )}
          <ul className="space-y-2">
            {(agenda?.top_tasks ?? []).map((task, i) => (
              <li key={i} className="flex items-baseline gap-3">
                <span className="font-display text-lg font-semibold text-primary-soft">
                  {i + 1}
                </span>
                <span className="min-w-0">
                  <span className="block truncate text-sm">{task.text}</span>
                  <span
                    className={`font-mono text-[11px] ${PRIORITY_COLORS[task.priority ?? ""] ?? "text-ink-faint"}`}
                  >
                    {task.due ?? "no date"}
                    {task.priority ? ` · ${task.priority}` : ""}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </GlassCard>

        <GlassCard label="VAULT" title="Recent notes" className="md:col-span-2 xl:col-span-2">
          {notes && notes.length > 0 ? (
            <ul className="divide-y divide-white/5">
              {notes.slice(0, 6).map((note) => (
                <li key={note.path} className="flex items-baseline justify-between gap-4 py-2">
                  <span className="truncate text-sm">{note.title}</span>
                  <span className="shrink-0 font-mono text-[11px] text-ink-faint">
                    {note.folder || "vault root"}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-ink-muted">The vault is quiet.</p>
          )}
        </GlassCard>
      </div>
    </>
  );
}
