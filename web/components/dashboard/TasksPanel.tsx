"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  actionFieldName,
  apiFetch,
  mutateJSON,
  runAutomationAction,
  useAgenda,
  useAutomationActions,
  useSourceProvenance,
  type AgendaTask,
} from "@/lib/api";
import { openExternalUrl } from "@/lib/quickLinks";
import { parseQuickAdd, splitQuickAdd } from "@/lib/taskQuickAdd";

function localToday(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

const anchor = (task: AgendaTask) => task.due ?? task.scheduled;
const taskKey = (task: AgendaTask, i: number) =>
  `${task.path ?? task.external_id ?? "todoist"}:${task.line ?? i}:${task.text}`;

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
  if (priority === "high") return { label: "P2", colorClass: "border-warn text-warn" };
  if (priority === "medium") return { label: "P3", colorClass: "border-ink-faint text-ink-faint" };
  if (priority === "low") return { label: "P4", colorClass: "border-ink-faint text-ink-faint" };
  return null;
}

function DueBadge({ task, today }: { task: AgendaTask; today: string }) {
  const date = anchor(task);
  if (!date) return null;
  const overdue = date < today;
  const isToday = date === today;
  return (
    <span
      className={`shrink-0 font-mono text-meta ${overdue ? "text-danger" : isToday ? "text-[var(--ac)]" : "text-ink-faint"}`}
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
  const { data: agenda, mutate } = useAgenda();
  const { data: provenance } = useSourceProvenance();
  const { actions } = useAutomationActions();
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [quickAdd, setQuickAdd] = useState("");
  const [adding, setAdding] = useState(false);
  const { show: flash } = useToast();

  const createTask = actions["task.create"];
  const completeTask = actions["task.complete"];
  const todoistError = agenda?.connector_errors?.todoist;
  // External tasks the user has just closed. The `tasks` widget only refreshes
  // on its workflow's own schedule — up to 15 minutes — so without this the
  // row a user just ticked reappears, unticked, on the very next revalidate.
  // Keyed by external id, and cleared once the task stops arriving, so it
  // never outlives the payload it is correcting.
  const suppressed = useRef<Set<string>>(new Set());
  // Tasks completed this session — /api/agenda only ever returns OPEN tasks
  // (backend filters `done = 0`), so a toggled-done task vanishes from the
  // next revalidate. We keep a local record so the DONE group stays
  // Todoist-shaped instead of flashing empty (§4). Real toggle data only —
  // never mock.
  const doneSession = useRef<Map<string, AgendaTask>>(new Map());
  // Toggles currently in flight, keyed like `doneSession`.
  //
  // Completing a *recurring* task makes this a correctness guard, not polish.
  // `writer.toggle_task_line` inserts the next instance ABOVE the one it
  // ticks, so every line below shifts down by one — and `task.line` here
  // still holds the pre-toggle number until `mutate()` lands. A second click
  // in that window re-reads the same line number, which now holds the *new*
  // instance, so the CAS `old_line` matches (it is comparing that line to
  // itself), the write is accepted, and the user gets a duplicate task in
  // their real vault plus a third instance spawned from it.
  //
  // A ref, not state: the check has to be synchronous and settled before the
  // first `await`, and a state update is neither. `forceRender` is only so
  // the control can *look* disabled; the ref is what makes it safe.
  const inFlight = useRef<Set<string>>(new Set());
  const [, forceRender] = useState(0);
  const { confirm, confirmDialog } = useConfirm();

  const today = localToday();

  async function withRawLine(task: AgendaTask, action: (raw: string) => Promise<void>): Promise<void> {
    if (!task.path || !task.line) return;
    const response = await apiFetch(`/api/note?path=${encodeURIComponent(task.path)}`);
    const note = (await response.json()) as { content: string };
    const raw = note.content.split("\n")[task.line - 1] ?? "";
    await action(raw);
  }

  async function toggle(task: AgendaTask, key: string) {
    if (inFlight.current.has(key)) return;
    inFlight.current.add(key);
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
    } finally {
      // Cleared only after `mutate()` resolves: until the refetch lands,
      // `task.line` is still the stale number that makes a second click
      // dangerous.
      await mutate();
      inFlight.current.delete(key);
      forceRender((n) => n + 1);
    }
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
    const answer = await confirm({
      label: "Delete task",
      message: `Delete "${task.text}"?`,
      detail: "A git snapshot makes this undoable.",
      confirmLabel: "Delete",
    });
    if (answer === null) return;
    try {
      await withRawLine(task, async (raw) => {
        await mutateJSON("/api/tasks/line/delete", { path: task.path, line: task.line, old_line: raw });
      });
    } catch (error) {
      flash(error instanceof Error ? error.message : "Delete failed");
    }
    mutate();
  }

  /**
   * Close an external task by firing the installed `task.complete` workflow.
   *
   * Separate from `toggle` because there is no line to rewrite: an n8n/Todoist
   * task has no `path`/`line`, which is exactly why these rows were disabled
   * before. The handle is `external_id`, carried through the widget payload.
   */
  async function completeExternal(task: AgendaTask, key: string) {
    if (!completeTask || !task.external_id) return;
    if (inFlight.current.has(key)) return;
    inFlight.current.add(key);
    const id = task.external_id;
    suppressed.current.add(id);
    doneSession.current.set(key, { ...task, done: true });
    forceRender((n) => n + 1);
    try {
      const idKey = actionFieldName(completeTask, "taskId") ?? "taskId";
      const result = await runAutomationAction(completeTask, { [idKey]: id });
      if (result.status !== "ok" && result.status !== "running") {
        throw new Error(result.message ?? `n8n reported ${result.status}`);
      }
      flash(`done :: ${task.text} — closed in Todoist`);
    } catch (error) {
      // Roll the row back: it is still open upstream, and leaving it struck
      // through would be the app lying about a write that did not happen.
      suppressed.current.delete(id);
      doneSession.current.delete(key);
      forceRender((n) => n + 1);
      flash(error instanceof Error ? error.message : "Could not close the task");
    } finally {
      inFlight.current.delete(key);
      forceRender((n) => n + 1);
    }
  }

  async function submitQuickAdd(event: React.FormEvent) {
    event.preventDefault();
    const raw = quickAdd.trim();
    if (!raw || adding) return;
    setAdding(true);
    setQuickAdd("");
    try {
      if (createTask) {
        // n8n path: discrete fields, keyed by the workflow's own labels.
        const parts = splitQuickAdd(raw);
        const values: Record<string, unknown> = {};
        const taskKeyName = actionFieldName(createTask, "Task");
        const dueKeyName = actionFieldName(createTask, "Due");
        const prioKeyName = actionFieldName(createTask, "Priority");
        if (taskKeyName) values[taskKeyName] = parts.text;
        if (dueKeyName && parts.due) values[dueKeyName] = parts.due;
        if (prioKeyName && parts.priority) values[prioKeyName] = `P${parts.priority}`;

        const result = await runAutomationAction(createTask, values);
        if (result.status !== "ok" && result.status !== "running") {
          throw new Error(result.message ?? `n8n reported ${result.status}`);
        }
        // The task now exists in Todoist, not in Argus — it appears here on
        // the tasks workflow's next poll, so say that rather than implying
        // the list is about to change.
        flash(`added to todoist :: ${parts.text} — appears on the next sync`);
      } else {
        const result = await mutateJSON<{ path: string }>("/api/capture", {
          text: parseQuickAdd(raw),
        });
        flash(`captured → ${result.path}`);
      }
    } catch (error) {
      flash(error instanceof Error ? error.message : "Capture failed");
    }
    setAdding(false);
    mutate();
  }

  const incoming = agenda?.tasks ?? [];
  // Drop anything the user closed upstream but the workflow has not re-polled
  // yet, and forget the suppression as soon as it stops arriving — otherwise a
  // task legitimately reopened in Todoist could never come back.
  const arrivingIds = new Set(incoming.map((t) => t.external_id).filter(Boolean) as string[]);
  for (const id of [...suppressed.current]) {
    if (!arrivingIds.has(id)) suppressed.current.delete(id);
  }
  const tasks = incoming.filter((t) => !(t.external_id && suppressed.current.has(t.external_id)));

  const overdue = tasks.filter((t) => {
    const date = anchor(t);
    return date !== null && date < today;
  });
  const dueToday = tasks.filter((t) => anchor(t) === today);
  // Undated tasks match neither filter above, and are excluded from UPCOMING
  // by `openKeys` — before this group they were fetched, counted, and rendered
  // nowhere. That is most of a normal Todoist inbox, and every fresh capture.
  const undated = tasks.filter((t) => anchor(t) === null && !t.done);
  const openKeys = new Set(tasks.map((t, i) => taskKey(t, i)));
  const upcoming = (agenda?.top_tasks ?? []).filter((t, i) => !openKeys.has(taskKey(t, i)));
  const done = [...doneSession.current.entries()]
    .filter(([key]) => !openKeys.has(key))
    .map(([, task]) => task);

  const groups: { label: string; items: AgendaTask[] }[] = [
    { label: "OVERDUE", items: overdue },
    { label: "TODAY", items: dueToday },
    { label: "NO DUE DATE", items: undated },
    { label: "UPCOMING", items: upcoming },
    { label: "DONE", items: done },
  ];

  return (
    <Panel
      label="TASKS.DUE"
      headerRight={
        // See PlannerTimeline: once a workflow supplies the tasks, naming the
        // connector would credit something that is no longer answering.
        provenance?.tasks === "n8n" ? (
          <span
            className="font-mono text-meta uppercase tracking-wide text-ok"
            title="Supplied by an n8n workflow pushing the tasks widget, not the built-in connector"
          >
            TODOIST: VIA N8N
          </span>
        ) : todoistError ? (
          // "WIRED" over an empty list is the app claiming you have no tasks
          // when it could not actually ask.
          <span
            className="font-mono text-meta uppercase tracking-wide text-danger"
            title={todoistError}
          >
            TODOIST: FAILING
          </span>
        ) : agenda?.configured.todoist ? (
          <span className="font-mono text-meta uppercase tracking-wide text-ok">TODOIST: WIRED</span>
        ) : (
          <Link
            href="/system"
            className="font-mono text-meta uppercase tracking-wide text-ink-faint hover:text-[var(--ac)]"
          >
            TODOIST: NOT CONNECTED →
          </Link>
        )
      }
    >
      {todoistError && (
        <p className="mb-3 font-mono text-meta text-danger" role="alert">
          todoist unavailable :: {todoistError}
        </p>
      )}

      <form
        onSubmit={submitQuickAdd}
        className="mb-1 flex items-center gap-2 border border-line px-3 py-2 focus-within:border-lineHi"
      >
        <input
          value={quickAdd}
          onChange={(event) => setQuickAdd(event.target.value)}
          placeholder={
            createTask ? "add to todoist — review PR p1 tomorrow" : "review PR p1 #argus tomorrow"
          }
          aria-label="Add a task"
          className="min-w-0 flex-1 bg-transparent text-body placeholder:text-ink-faint focus:outline-none"
        />
        {/* Was a bare <span>, so the form had no submit control at all:
            clicking ＋ did nothing and only Enter worked. */}
        <Button
          type="submit"
          variant="ghost"
          aria-label="Add task"
          disabled={adding || !quickAdd.trim()}
          className="shrink-0 text-[var(--ac)] hover:text-[var(--ac)]"
        >
          {adding ? "…" : "＋"}
        </Button>
      </form>
      {/* Which store the ＋ writes to is not guessable from the field, and the
          two are not interchangeable — one syncs to your phone, one does not. */}
      <p className="mb-4 font-mono text-micro uppercase tracking-[0.12em] text-ink-faint">
        {createTask ? (
          <>→ todoist, via {createTask.workflow_name}</>
        ) : (
          <>
            → vault capture ·{" "}
            <Link href="/automations" className="hover:text-[var(--ac)]">
              install the todoist action to write to todoist →
            </Link>
          </>
        )}
      </p>

      {groups.map((group) => (
        <div key={group.label} className="mb-4 last:mb-0">
          <div className="mb-1.5 flex items-center gap-2 border-b border-line pb-1">
            <p className="font-mono text-micro uppercase tracking-[0.16em] text-ink-faint">{group.label}</p>
            <span className="font-mono text-micro text-ink-faint">({group.items.length})</span>
          </div>
          {group.items.length === 0 ? (
            <p className="py-1 text-label text-ink-faint">nothing here</p>
          ) : (
            <ul>
              {group.items.map((task, i) => {
                const key = taskKey(task, i);
                const editable = task.source === "vault" && !!task.path && !!task.line;
                // An external task has no vault line to rewrite, so it can only
                // be closed by asking its own service — which is possible
                // exactly when the `task.complete` workflow is installed.
                const closable =
                  !editable && !task.done && !!task.external_id && !!completeTask;
                const prio = priorityMeta(task.priority);
                const tag = task.tags?.[0];
                return (
                  <li key={key} className="group flex items-center gap-2.5 py-1.5">
                    <button
                      aria-label={task.done ? "Mark not done" : "Mark done"}
                      disabled={(!editable && !closable) || inFlight.current.has(key)}
                      title={
                        closable
                          ? `Close in Todoist via ${completeTask!.workflow_name}`
                          : undefined
                      }
                      onClick={() => (closable ? completeExternal(task, key) : toggle(task, key))}
                      className={`flex h-[17px] w-[17px] shrink-0 items-center justify-center rounded-full border-2 text-meta leading-none transition-colors disabled:opacity-70 ${
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
                          className="min-w-0 flex-1 border border-lineHi bg-sunken px-2 py-1 text-body focus:outline-none"
                        />
                      </form>
                    ) : task.href ? (
                      <button
                        onClick={() => openExternalUrl(task.href!)}
                        title={`Open in ${task.source === "n8n" ? "Todoist" : task.source}`}
                        className={`min-w-0 flex-1 truncate text-left text-body hover:text-[var(--ac)] hover:underline ${task.done ? "text-ink-faint line-through" : "text-ink"}`}
                      >
                        {task.text}
                      </button>
                    ) : (
                      <span
                        className={`min-w-0 flex-1 truncate text-body ${task.done ? "text-ink-faint line-through" : "text-ink"}`}
                      >
                        {task.text}
                      </span>
                    )}
                    {prio && editing !== key && (
                      <span className={`shrink-0 font-mono text-micro ${prio.colorClass}`}>{prio.label}</span>
                    )}
                    {tag && editing !== key && (
                      <span className="shrink-0 font-mono text-meta text-ink-faint">#{tag}</span>
                    )}
                    {task.recurrence && editing !== key && (
                      // Ticking this one does not remove it — it rolls the
                      // series forward. Worth saying before the click, not
                      // after, or the reappearing row reads as a failed
                      // completion.
                      <span
                        className="shrink-0 font-mono text-micro text-ink-faint"
                        title={`Repeats ${task.recurrence} — completing this creates the next one`}
                      >
                        🔁
                      </span>
                    )}
                    {editing !== key && <DueBadge task={task} today={today} />}
                    {editable && editing !== key && (
                      // Revealed on hover, but also on keyboard focus and
                      // always present in the tab order — as `hidden` these
                      // were unreachable without a mouse.
                      <span className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
                        <Button
                          variant="ghost"
                          aria-label="Edit task"
                          onClick={() => {
                            setEditing(key);
                            setDraft(task.text);
                          }}
                          className="normal-case hover:text-[var(--ac)]"
                        >
                          edit
                        </Button>
                        <Button
                          variant="ghost"
                          aria-label="Delete task"
                          onClick={() => remove(task)}
                          className="hover:text-danger"
                        >
                          ×
                        </Button>
                      </span>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      ))}
      {confirmDialog}
    </Panel>
  );
}
