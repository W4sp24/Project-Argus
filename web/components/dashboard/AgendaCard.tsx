"use client";

import { useState } from "react";
import useSWR from "swr";
import GlassCard from "@/components/GlassCard";
import { fetcher, mutateJSON } from "@/lib/api";

interface CalendarEvent {
  title: string;
  start: string;
  end: string;
  all_day: boolean;
}

interface AgendaTask {
  text: string;
  done: boolean;
  due: string | null;
  priority: string | null;
  source: string;
  path: string | null;
  line: number | null;
}

interface Agenda {
  date: string;
  events: CalendarEvent[];
  tasks: AgendaTask[];
  configured: { gcal: boolean; todoist: boolean };
}

function eventTime(iso: string): string {
  if (!iso.includes("T")) return "all day";
  return new Date(iso).toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit" });
}

/** Rebuild a task line with a new description, preserving checkbox + metadata verbatim. */
function spliceDescription(raw: string, text: string): string | null {
  const match = raw.match(/^(\s*[-*]\s+\[[ xX]\]\s+)(.*)$/);
  if (!match) return null;
  const metaRe =
    /(?:📅|🗓|⏳|✅|➕)\s*\d{4}-\d{2}-\d{2}|\[(?:due|scheduled|prio(?:rity)?):[^\]]*\]|[🔺⏫🔼🔽]|#[\w/-]+|<!--.*?-->/giu;
  const metas = match[2].match(metaRe) ?? [];
  return `${match[1]}${text}${metas.length ? ` ${metas.join(" ")}` : ""}`;
}

export default function AgendaCard() {
  const { data: agenda, mutate } = useSWR<Agenda>("/api/agenda", fetcher);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [toast, setToast] = useState<string | null>(null);

  function flash(message: string) {
    setToast(message);
    setTimeout(() => setToast(null), 4000);
  }

  async function withRawLine(
    task: AgendaTask,
    action: (raw: string) => Promise<void>,
  ): Promise<void> {
    if (!task.path || !task.line) return;
    // Read the exact current line so the server drift check compares like with like.
    const response = await fetch(`/api/note?path=${encodeURIComponent(task.path)}`);
    const note = (await response.json()) as { content: string };
    const raw = note.content.split("\n")[task.line - 1] ?? "";
    await action(raw);
  }

  async function toggle(task: AgendaTask) {
    // Optimistic: flip locally, reconcile after the API call.
    mutate(
      (current) =>
        current && {
          ...current,
          tasks: current.tasks.map((item) =>
            item.path === task.path && item.line === task.line
              ? { ...item, done: !item.done }
              : item,
          ),
        },
      { revalidate: false },
    );
    try {
      await withRawLine(task, async (raw) => {
        await mutateJSON("/api/tasks/toggle", { path: task.path, line: task.line, old_line: raw });
      });
    } catch (error) {
      flash(error instanceof Error ? error.message : "Toggle failed");
    }
    mutate();
  }

  async function saveEdit(task: AgendaTask) {
    const text = draft.trim();
    setEditing(null);
    if (!text) return;
    try {
      await withRawLine(task, async (raw) => {
        const newLine = spliceDescription(raw, text);
        if (newLine === null) {
          flash("Couldn't edit this line — it isn't a task checkbox anymore");
          return;
        }
        await mutateJSON("/api/tasks/line/update", {
          path: task.path,
          line: task.line,
          old_line: raw,
          new_line: newLine,
        });
      });
    } catch (error) {
      flash(error instanceof Error ? error.message : "Edit failed");
    }
    mutate();
  }

  async function remove(task: AgendaTask) {
    if (!window.confirm(`Delete “${task.text}”? A git snapshot makes this undoable.`)) return;
    try {
      await withRawLine(task, async (raw) => {
        await mutateJSON("/api/tasks/line/delete", {
          path: task.path,
          line: task.line,
          old_line: raw,
        });
      });
    } catch (error) {
      flash(error instanceof Error ? error.message : "Delete failed");
    }
    mutate();
  }

  return (
    <GlassCard label="AGENDA" title="Schedule">
      {agenda && agenda.events.length === 0 && (
        <p className="mb-2 text-sm text-ink-muted">
          {agenda.configured.gcal ? "Nothing on the calendar today." : "Google Calendar not connected."}
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

      <div className="mt-4 border-t border-white/5 pt-3">
        <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.18em] text-ink-faint">
          due · overdue
        </p>
        {agenda && agenda.tasks.length === 0 && (
          <p className="text-sm text-ink-muted">Nothing due. Capture something?</p>
        )}
        <ul className="space-y-1.5">
          {(agenda?.tasks ?? []).map((task, i) => {
            const key = `${task.path}:${task.line}:${i}`;
            const editable = task.source === "vault" && task.path && task.line;
            return (
              <li key={key} className="group flex items-center gap-2 text-sm">
                <button
                  aria-label={task.done ? "Mark not done" : "Mark done"}
                  disabled={!editable}
                  onClick={() => toggle(task)}
                  className="text-ink-faint transition-colors hover:text-primary-soft disabled:opacity-40"
                >
                  {task.done ? "◉" : "○"}
                </button>
                {editing === key ? (
                  <form
                    className="flex min-w-0 flex-1 gap-2"
                    onSubmit={(event) => {
                      event.preventDefault();
                      saveEdit(task);
                    }}
                  >
                    <input
                      autoFocus
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      onBlur={() => setEditing(null)}
                      className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-sm focus:border-primary-soft/50 focus:outline-none"
                    />
                  </form>
                ) : (
                  <span className={`min-w-0 flex-1 truncate ${task.done ? "text-ink-faint line-through" : "text-ink-muted"}`}>
                    {task.text}
                    {task.due && <span className="ml-2 font-mono text-[10px] text-ink-faint">{task.due}</span>}
                  </span>
                )}
                {editable && editing !== key && (
                  <span className="hidden shrink-0 gap-2 group-hover:flex">
                    <button
                      aria-label="Edit task"
                      onClick={() => {
                        setEditing(key);
                        setDraft(task.text);
                      }}
                      className="font-mono text-[10px] text-ink-faint hover:text-primary-soft"
                    >
                      edit
                    </button>
                    <button
                      aria-label="Delete task"
                      onClick={() => remove(task)}
                      className="font-mono text-[10px] text-ink-faint hover:text-accent"
                    >
                      delete
                    </button>
                  </span>
                )}
                {task.source === "todoist" && (
                  <span className="rounded bg-white/5 px-1.5 font-mono text-[10px] text-ink-faint">todoist</span>
                )}
              </li>
            );
          })}
        </ul>
      </div>
      {toast && <p className="mt-3 font-mono text-[11px] text-accent">{toast}</p>}
    </GlassCard>
  );
}
