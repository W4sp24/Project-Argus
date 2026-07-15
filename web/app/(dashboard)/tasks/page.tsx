"use client";

import useSWR from "swr";
import Panel from "@/components/Panel";
import PageHeader from "@/components/PageHeader";
import { fetcher } from "@/lib/api";

interface TaskItem {
  text: string;
  due: string | null;
  priority: string | null;
  tags: string[];
  source: string;
  path: string | null;
}

type Board = Record<"overdue" | "today" | "week" | "someday", TaskItem[]>;

const COLUMNS: { key: keyof Board; label: string; accent: string }[] = [
  { key: "overdue", label: "Overdue", accent: "text-accent" },
  { key: "today", label: "Today", accent: "text-primary-soft" },
  { key: "week", label: "This week", accent: "text-signal" },
  { key: "someday", label: "Someday", accent: "text-ink-faint" },
];

export default function TasksPage() {
  const { data: board, error } = useSWR<Board>("/api/tasks", fetcher);

  return (
    <>
      <PageHeader
        label="TASKS"
        title="Task board"
        subtitle="Every open task from your vault and Todoist. Edits flow through the approval queue (Phase P3)."
      />
      {error && (
        <p className="mb-4 text-sm text-ink-muted">
          Backend unreachable — start it with{" "}
          <span className="font-mono text-xs text-primary-soft">
            uvicorn backend.main:app --port 8000
          </span>
        </p>
      )}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        {COLUMNS.map((column) => {
          const tasks = board?.[column.key] ?? [];
          return (
            <Panel key={column.key} label={`${column.label.toUpperCase()} · ${tasks.length}`}>
              {tasks.length === 0 && <p className="text-sm text-ink-faint">Empty.</p>}
              <ul className="space-y-2.5">
                {tasks.map((task, i) => (
                  <li key={i} className="rounded-xl border border-white/5 bg-white/[0.03] p-3">
                    <p className="text-sm text-ink">{task.text}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-2 font-mono text-[10px] text-ink-faint">
                      {task.due && <span className={column.accent}>{task.due}</span>}
                      {task.priority && <span>{task.priority}</span>}
                      {task.tags.map((tag) => (
                        <span key={tag} className="rounded bg-primary/15 px-1.5 py-0.5 text-primary-soft">
                          #{tag}
                        </span>
                      ))}
                      <span className="ml-auto">{task.source === "todoist" ? "todoist" : task.path}</span>
                    </p>
                  </li>
                ))}
              </ul>
            </Panel>
          );
        })}
      </div>
    </>
  );
}
