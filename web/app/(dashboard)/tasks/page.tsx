"use client";

import useSWR from "swr";
import Panel from "@/components/Panel";
import PageHeader from "@/components/PageHeader";
import { fetcher, useIntegrations } from "@/lib/api";

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
  { key: "overdue", label: "Overdue", accent: "text-danger" },
  { key: "today", label: "Today", accent: "text-[var(--ac)]" },
  { key: "week", label: "This week", accent: "text-mode-study" },
  { key: "someday", label: "Someday", accent: "text-ink-faint" },
];

export default function TasksPage() {
  const { data: board, error } = useSWR<Board>("/api/tasks", fetcher);
  const { data: integrations } = useIntegrations();
  const failing = integrations?.connectors.filter((c) => c.status === "failing") ?? [];

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
          <span className="font-mono text-xs text-[var(--ac)]">
            uvicorn backend.main:app --port 8000
          </span>
        </p>
      )}
      {failing.length > 0 && (
        <p className="mb-4 border border-danger px-3 py-2 text-sm text-danger">
          {failing.map((c) => `${c.name}: ${c.error ?? "not answering right now"}`).join(" · ")} —
          this board may be missing tasks from it. See INTEGRATIONS.
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
                  <li key={i} className="border border-line bg-panel p-3">
                    <p className="text-sm text-ink">{task.text}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-2 font-mono text-meta text-ink-faint">
                      {task.due && <span className={column.accent}>{task.due}</span>}
                      {task.priority && <span>{task.priority}</span>}
                      {task.tags.map((tag) => (
                        <span key={tag} className="border border-line px-1.5 py-0.5 text-[var(--ac)]">
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
