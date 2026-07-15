"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import useSWR from "swr";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { fetcher, mutateJSON } from "@/lib/api";
import { parseQuickAdd } from "@/lib/taskQuickAdd";

interface AgendaTask {
  text: string;
  done: boolean;
  due: string | null;
  scheduled: string | null;
  priority: string | null;
  tags: string[];
  source: string;
  path: string | null;
  line: number | null;
}

interface Agenda {
  tasks: AgendaTask[];
  top_tasks: AgendaTask[];
  configured: { gcal: boolean; todoist: boolean };
}

function localToday(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

const anchor = (task: AgendaTask) => task.due ?? task.scheduled;
const taskKey = (task: AgendaTask, i: number) => `${task.path ?? "todoist"}:${task.line ?? i}:${task.text}`;

/** Rebuild a task line with a new description, preserving checkbox + metadata verbatim. */
function spliceDescription(raw: string, text: string): string | null {
  const match = raw.match(/^(\s*[-*]\s+\[[ xX]\]\s+)(.*)$/);
  if (!match) return null;
  const metaRe =
    /(?:📅|🗓|⏳|✅|➕)\s*\d{4}-\d{2}-\d{2}|\[(?:due|scheduled|prio(?:rity)?):[^\]]*\]|[🔺⏫🔼🔽]|#[\w/-]+|<!--.*?-->/giu;
  const metas = match[2].match(metaRe) ?? [];
  return `${match[1]}${text}${metas.length ? ` ${metas.join(" ")}` : ""}`;
}

function priorityMeta(priority: string | null): { label: string; colorClass: string } | null {
  if (priority === "highest") return { label: "P1", colorClass: "border-danger text-danger" };
  if (priority === "high") return { label: "P2", colorClass: "border-amber-400 text-amber-400" };
  if (priority === "medium" || priority === "low") return { label: "P3", colorClass: "border-ink-faint text-ink-faint" };
  return null;
}

function DueBadge({ task, today }: { task: AgendaTask; today: string }) {
  const date = anchor(task);
  if (!date) return null;
  const overdue = date < today;
  const isToday = date === today;
  return (
    <span
      className={`shrink-0 font-mono text-[10px] ${overdue ? "text-danger" : isToday ? "text-[var(--ac)]" : "text-ink-faint"}`}
    >
      {date}
    </span>
  );
}

/**
 * TASKS.DUE (§4 General, Todoist-style) — OVERDUE / TODAY / UPCOMING / DONE
 * groups over the same `/api/agenda` data + CRUD (`AgendaCard`'s
 * withRawLine/CAS-old_line/optimistic-mutate/spliceDescription machinery,
 * kept verbatim; only the rendering changed).
 */
export default function TasksPanel() {
  const { data: agenda, mutate } = useSWR<Agenda>("/api/agenda", fetcher);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [quickAdd, setQuickAdd] = useState("");
  const { show: flash } = useToast();
  // Tasks completed this session — /api/agenda only ever returns OPEN tasks
  // (backend filters `done = 0`), so a toggled-done task vanishes from the
  // next revalidate. We keep a local record so the DONE group stays
  // Todoist-shaped instead of flashing empty (§4). Real toggle data only —
  // never mock.
  const doneSession = useRef<Map<string, AgendaTask>>(new Map());
  const [, forceRender] = useState(0);

  const today = localToday();

  async function withRawLine(task: AgendaTask, action: (raw: string) => Promise<void>): Promise<void> {
    if (!task.path || !task.line) return;
    const response = await fetch(`/api/note?path=${encodeURIComponent(task.path)}`);
    const note = (await response.json()) as { content: string };
    const raw = note.content.split("\n")[task.line - 1] ?? "";
    await action(raw);
  }

  async function toggle(task: AgendaTask, key: string) {
    const goingDone = !task.done;
    mutate(
      (current) =>
        current && {
          ...current,
          tasks: current.tasks.map((item) =>
            item.path === task.path && item.line === task.line ? { ...item, done: goingDone } : item,
          ),
        },
      { revalidate: false },
    );
    try {
      await withRawLine(task, async (raw) => {
        await mutateJSON("/api/tasks/toggle", { path: task.path, line: task.line, old_line: raw });
      });
      if (goingDone) {
        doneSession.current.set(key, { ...task, done: true });
        flash(`done :: ${task.text} — vault line updated`);
      } else {
        doneSession.current.delete(key);
      }
      forceRender((n) => n + 1);
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
    if (!window.confirm(`Delete "${task.text}"? A git snapshot makes this undoable.`)) return;
    try {
      await withRawLine(task, async (raw) => {
        await mutateJSON("/api/tasks/line/delete", { path: task.path, line: task.line, old_line: raw });
      });
    } catch (error) {
      flash(error instanceof Error ? error.message : "Delete failed");
    }
    mutate();
  }

  async function submitQuickAdd(event: React.FormEvent) {
    event.preventDefault();
    const raw = quickAdd.trim();
    if (!raw) return;
    setQuickAdd("");
    const composed = parseQuickAdd(raw);
    try {
      const result = await mutateJSON<{ path: string }>("/api/capture", { text: composed });
      flash(`captured → ${result.path}`);
    } catch (error) {
      flash(error instanceof Error ? error.message : "Capture failed");
    }
    mutate();
  }

  const tasks = agenda?.tasks ?? [];
  const overdue = tasks.filter((t) => {
    const date = anchor(t);
    return date !== null && date < today;
  });
  const dueToday = tasks.filter((t) => anchor(t) === today);
  const openKeys = new Set(tasks.map((t, i) => taskKey(t, i)));
  const upcoming = (agenda?.top_tasks ?? []).filter((t, i) => !openKeys.has(taskKey(t, i)));
  const done = [...doneSession.current.entries()]
    .filter(([key]) => !openKeys.has(key))
    .map(([, task]) => task);

  const groups: { label: string; items: AgendaTask[] }[] = [
    { label: "OVERDUE", items: overdue },
    { label: "TODAY", items: dueToday },
    { label: "UPCOMING", items: upcoming },
    { label: "DONE", items: done },
  ];

  return (
    <Panel
      label="TASKS.DUE"
      headerRight={
        agenda?.configured.todoist ? (
          <span className="font-mono text-[10px] uppercase tracking-wide text-ok">TODOIST: WIRED</span>
        ) : (
          <Link
            href="/system"
            className="font-mono text-[10px] uppercase tracking-wide text-ink-faint hover:text-[var(--ac)]"
          >
            TODOIST: NOT CONNECTED →
          </Link>
        )
      }
    >
      <form onSubmit={submitQuickAdd} className="mb-4 flex items-center gap-2 border border-line px-3 py-2 focus-within:border-lineHi">
        <span className="shrink-0 font-mono text-[var(--ac)]">＋</span>
        <input
          value={quickAdd}
          onChange={(event) => setQuickAdd(event.target.value)}
          placeholder="review PR p1 #argus tomorrow"
          className="min-w-0 flex-1 bg-transparent text-[13.5px] placeholder:text-ink-faint focus:outline-none"
        />
      </form>

      {groups.map((group) => (
        <div key={group.label} className="mb-4 last:mb-0">
          <div className="mb-1.5 flex items-center gap-2 border-b border-line pb-1">
            <p className="font-mono text-[9px] uppercase tracking-[0.16em] text-ink-faint">{group.label}</p>
            <span className="font-mono text-[9px] text-ink-faint">({group.items.length})</span>
          </div>
          {group.items.length === 0 ? (
            <p className="py-1 text-[12px] text-ink-faint">nothing here</p>
          ) : (
            <ul>
              {group.items.map((task, i) => {
                const key = taskKey(task, i);
                const editable = task.source === "vault" && !!task.path && !!task.line;
                const prio = priorityMeta(task.priority);
                const tag = task.tags?.[0];
                return (
                  <li key={key} className="group flex items-center gap-2.5 py-1.5">
                    <button
                      aria-label={task.done ? "Mark not done" : "Mark done"}
                      disabled={!editable}
                      onClick={() => toggle(task, key)}
                      className={`flex h-[17px] w-[17px] shrink-0 items-center justify-center rounded-full border-2 text-[10px] leading-none transition-colors disabled:opacity-40 ${
                        task.done
                          ? "border-ok bg-ok text-void"
                          : (prio?.colorClass ?? "border-line") + " hover:border-lineHi"
                      }`}
                    >
                      {task.done && "✓"}
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
                          className="min-w-0 flex-1 border border-lineHi bg-sunken px-2 py-1 text-[13.5px] focus:outline-none"
                        />
                      </form>
                    ) : (
                      <span
                        className={`min-w-0 flex-1 truncate text-[13.5px] ${task.done ? "text-ink-faint line-through" : "text-ink"}`}
                      >
                        {task.text}
                      </span>
                    )}
                    {prio && editing !== key && (
                      <span className={`shrink-0 font-mono text-[9px] ${prio.colorClass}`}>{prio.label}</span>
                    )}
                    {tag && editing !== key && (
                      <span className="shrink-0 font-mono text-[10px] text-ink-faint">#{tag}</span>
                    )}
                    {editing !== key && <DueBadge task={task} today={today} />}
                    {editable && editing !== key && (
                      <span className="hidden shrink-0 items-center gap-2 group-hover:flex">
                        <button
                          aria-label="Edit task"
                          onClick={() => {
                            setEditing(key);
                            setDraft(task.text);
                          }}
                          className="font-mono text-[10px] text-ink-faint hover:text-[var(--ac)]"
                        >
                          edit
                        </button>
                        <button
                          aria-label="Delete task"
                          onClick={() => remove(task)}
                          className="font-mono text-xs text-ink-faint hover:text-danger"
                        >
                          ×
                        </button>
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ))}
    </Panel>
  );
}
