"use client";

import { useState } from "react";
import dynamic from "next/dynamic";
import Panel from "@/components/Panel";
import {
  useJournalNote,
  useJournalProjects,
  useJournalSessions,
  type JournalSession,
} from "@/lib/api";

const ReactMarkdown = dynamic(() => import("react-markdown"), {
  ssr: false,
  loading: () => <p className="text-sm text-ink-faint">Loading note…</p>,
});

function relativeDay(iso: string): string {
  const today = new Date();
  const date = new Date(`${iso}T00:00:00`);
  const diffDays = Math.round(
    (new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime() -
      date.getTime()) /
      86_400_000,
  );
  if (diffDays === 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  return date.toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" });
}

export default function JournalPage() {
  const [projectFilter, setProjectFilter] = useState<string | undefined>();
  const [selectedPath, setSelectedPath] = useState<string | undefined>();

  const { data: projects } = useJournalProjects();
  const { data: sessions, error } = useJournalSessions(projectFilter);
  const { data: note } = useJournalNote(selectedPath);

  const sessionsByDay: Record<string, JournalSession[]> = {};
  for (const session of sessions ?? []) {
    (sessionsByDay[session.date] ??= []).push(session);
  }

  return (
    <>
      <header className="mb-8 animate-rise">
        <div className="flex flex-wrap items-center gap-3">
          <p className="eyebrow">{`// JOURNAL`}</p>
          <span className="rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 font-mono text-[10px] uppercase tracking-[0.15em] text-ink-faint">
            dev-owned · view only
          </span>
        </div>
        <h1 className="mt-2 font-display text-3xl font-semibold tracking-tight md:text-4xl">
          Build journal
        </h1>
        <p className="mt-2 max-w-xl text-sm text-ink-muted">
          Every Claude Code session, journaled into your vault. Edits happen in Obsidian or via{" "}
          <span className="font-mono text-xs text-primary-soft">/log-session</span>.
        </p>
      </header>

      {error && (
        <Panel label="OFFLINE" title="Can't reach the journal">
          <p className="text-sm text-ink-muted">
            Start the backend with{" "}
            <span className="font-mono text-xs text-primary-soft">
              uvicorn backend.main:app --port 8000
            </span>
            .
          </p>
        </Panel>
      )}

      <div className="grid gap-4 lg:grid-cols-[280px_1fr]">
        <div className="space-y-3">
          <p className="eyebrow">{`// PROJECTS`}</p>
          <button
            onClick={() => setProjectFilter(undefined)}
            className={`w-full rounded-xl px-4 py-2 text-left text-sm transition-colors ${
              !projectFilter ? "bg-gradient-to-r from-primary/25 to-accent/15 text-ink" : "text-ink-muted hover:bg-white/5"
            }`}
          >
            All projects
          </button>
          {(projects ?? []).map((project) => (
            <button
              key={project.slug}
              onClick={() => {
                setProjectFilter(project.slug);
                setSelectedPath(project.path);
              }}
              className={`w-full border bg-panel p-4 text-left transition-colors hover:border-lineHi ${
                projectFilter === project.slug ? "border-[var(--ac)]" : "border-line"
              }`}
            >
              <p className="truncate font-display text-sm font-medium">{project.title}</p>
              <p className="mt-1 font-mono text-[11px] text-ink-faint">
                {project.sessions} session{project.sessions === 1 ? "" : "s"} ·{" "}
                {project.open_threads} open thread{project.open_threads === 1 ? "" : "s"}
              </p>
            </button>
          ))}
          {projects && projects.length === 0 && (
            <p className="text-sm text-ink-muted">
              No project notes yet — they live in{" "}
              <span className="font-mono text-xs">90-Meta/projects/</span>.
            </p>
          )}
        </div>

        <div className="space-y-4">
          <p className="eyebrow">{`// SESSIONS`}</p>
          {sessions && sessions.length === 0 && (
            <Panel label="EMPTY" title="No sessions yet">
              <p className="text-sm text-ink-muted">
                End a Claude Code session and its stub appears here automatically.
              </p>
            </Panel>
          )}
          {Object.entries(sessionsByDay).map(([day, daySessions]) => (
            <div key={day}>
              <p className="mb-2 font-mono text-[11px] uppercase tracking-[0.18em] text-ink-faint">
                {relativeDay(day)} · {day}
              </p>
              <div className="space-y-2">
                {daySessions.map((session) => (
                  <button
                    key={session.path}
                    onClick={() => setSelectedPath(session.path)}
                    className={`flex w-full items-center justify-between gap-4 border bg-panel px-4 py-3 text-left transition-colors hover:border-lineHi ${
                      selectedPath === session.path ? "border-[var(--ac)]" : "border-line"
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-3">
                      <span className="truncate text-sm">{session.project}</span>
                      {session.branch && (
                        <span className="hidden shrink-0 rounded-md bg-white/5 px-2 py-0.5 font-mono text-[11px] text-ink-muted sm:inline">
                          {session.branch}
                        </span>
                      )}
                    </span>
                    <span className="flex shrink-0 items-center gap-3">
                      <span className="font-mono text-[11px] text-ink-faint">
                        {session.files} file{session.files === 1 ? "" : "s"}
                      </span>
                      <span
                        className={`rounded-full px-2 py-0.5 font-mono text-[10px] uppercase tracking-wide ${
                          session.has_narrative
                            ? "bg-primary/20 text-primary-soft"
                            : "bg-white/5 text-ink-faint"
                        }`}
                      >
                        {session.has_narrative ? "narrative" : "stub"}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))}

          {note && (
            <Panel label="NOTE" className="animate-rise">
              <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
                <p className="min-w-0 truncate font-mono text-[11px] text-ink-faint">{note.path}</p>
                <a
                  href={note.obsidian_uri}
                  className="shrink-0 rounded-full bg-gradient-to-r from-primary/30 to-accent/20 px-3 py-1 font-mono text-[11px] text-primary-soft transition-opacity hover:opacity-80"
                >
                  Open in Obsidian ↗
                </a>
              </div>
              <article className="prose-journal max-w-none text-sm leading-relaxed text-ink-muted">
                <ReactMarkdown>{note.markdown.replace(/^---[\s\S]*?---/, "")}</ReactMarkdown>
              </article>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}
