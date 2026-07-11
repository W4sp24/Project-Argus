"use client";

import GlassCard from "@/components/GlassCard";
import { useNotes } from "@/lib/api";

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

export default function TodayPage() {
  const { data: notes, error, isLoading } = useNotes();

  return (
    <>
      <header className="mb-8 animate-rise">
        <p className="eyebrow mb-2">{`// TODAY · ${formatToday()}`}</p>
        <h1 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">
          {greeting()}, <span className="gradient-text">Ethan</span>.
        </h1>
        <p className="mt-2 max-w-xl text-sm text-ink-muted">
          Your day at a glance — notes, tasks, and schedule in one place.
        </p>
      </header>

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <GlassCard label="AGENDA" title="Schedule" className="xl:col-span-2">
          <p className="text-sm text-ink-muted">
            Your merged calendar lands here once Google Calendar and Todoist are connected
            (Phase P2). Until then, today is a blank canvas.
          </p>
        </GlassCard>

        <GlassCard label="CAPTURE" title="Quick capture">
          <p className="mb-3 text-sm text-ink-muted">
            Drop a thought — it files into <span className="font-mono text-xs">00-Inbox/</span>.
          </p>
          <input
            disabled
            placeholder="Capture arrives in Phase P2"
            className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm placeholder:text-ink-faint disabled:cursor-not-allowed"
          />
        </GlassCard>

        <GlassCard label="TASKS" title="Top 3 today">
          <p className="text-sm text-ink-muted">
            FRIDAY will surface your three most important tasks once the task engine is online
            (Phase P2).
          </p>
        </GlassCard>

        <GlassCard label="VAULT" title="Recent notes" className="md:col-span-2 xl:col-span-2">
          {isLoading && <p className="text-sm text-ink-muted">Reading your vault…</p>}
          {error && (
            <p className="text-sm text-ink-muted">
              Can&apos;t reach the vault. Start the backend with{" "}
              <span className="font-mono text-xs text-primary-soft">
                uvicorn backend.main:app --port 8000
              </span>{" "}
              and make sure <span className="font-mono text-xs">VAULT_PATH</span> is set in{" "}
              <span className="font-mono text-xs">.env</span>.
            </p>
          )}
          {notes && notes.length === 0 && (
            <p className="text-sm text-ink-muted">
              The vault is empty. Create a note in Obsidian and it shows up here.
            </p>
          )}
          {notes && notes.length > 0 && (
            <ul className="divide-y divide-white/5">
              {notes.slice(0, 8).map((note) => (
                <li key={note.path} className="flex items-baseline justify-between gap-4 py-2">
                  <span className="truncate text-sm">{note.title}</span>
                  <span className="shrink-0 font-mono text-[11px] text-ink-faint">
                    {note.folder || "vault root"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </GlassCard>
      </div>
    </>
  );
}
