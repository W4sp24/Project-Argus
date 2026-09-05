"use client";

import {
  useMemo,
  useRef,
  useState,
  useSyncExternalStore,
  type KeyboardEvent,
} from "react";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  deleteChatThread,
  patchChatThread,
  useChatThreads,
  type ThreadInfo,
} from "@/lib/api";
import { useChatActions, useChatMeta } from "@/lib/chat";

// --- collapse persistence --------------------------------------------------
//
// Same tiny external-store idiom as web/lib/models.ts's selected-model
// persistence: a bare localStorage key plus a listener set, so two mounted
// rails (e.g. the drawer's and a future second surface) stay in sync without
// prop drilling or a context provider just for one boolean.

const COLLAPSE_KEY = "argus-thread-rail-collapsed";
const collapseListeners = new Set<() => void>();

function getCollapsed(): boolean {
  try {
    return window.localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function setCollapsedStore(value: boolean): void {
  try {
    window.localStorage.setItem(COLLAPSE_KEY, value ? "1" : "0");
  } catch {
    // best-effort persistence (private browsing etc.) — in-memory still updates
  }
  collapseListeners.forEach((listener) => listener());
}

function subscribeCollapse(callback: () => void): () => void {
  collapseListeners.add(callback);
  window.addEventListener("storage", callback);
  return () => {
    collapseListeners.delete(callback);
    window.removeEventListener("storage", callback);
  };
}

/** SSR-safe reactive collapse flag (server snapshot = expanded). */
function useRailCollapsed(): boolean {
  return useSyncExternalStore(subscribeCollapse, getCollapsed, () => false);
}

// --- local-day bucketing ----------------------------------------------------

type Bucket = "TODAY" | "YESTERDAY" | "EARLIER";
const BUCKET_ORDER: Bucket[] = ["TODAY", "YESTERDAY", "EARLIER"];

/**
 * `updated_at` is a SQLite `datetime('now')` string — UTC, no timezone
 * suffix ("2026-08-19 14:03:11"). Handing that straight to `new Date(...)`
 * gets parsed as LOCAL time by every browser (it isn't ISO 8601 without a
 * "T"/"Z"), which is off by the reader's UTC offset. Splicing in the "T" and
 * a "Z" makes it an unambiguous UTC instant, so the local-day math below
 * lands on the calendar day the reader actually sees, not a shifted one.
 */
/**
 * Parse a stored timestamp as the UTC instant it actually is.
 *
 * Two formats reach here. `backend/features/chat/store.py` writes ISO 8601
 * with an explicit offset ("2026-08-19T11:43:18.302269+00:00") for every row
 * it creates, while the columns' SQL default is `datetime('now')` — a naive
 * UTC string with a space ("2026-08-19 11:43:18") — which any row written
 * outside the store would carry.
 *
 * The naive form must be marked as UTC, because a browser reads an unzoned
 * timestamp as *local* time and would file a late-evening thread under the
 * wrong day. The offset form must be left alone: appending "Z" to a string
 * that already ends in "+00:00" produces Invalid Date, and every bucket
 * silently collapses into EARLIER.
 */
function parseUtc(timestamp: string): Date {
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/.test(timestamp);
  return new Date(hasZone ? timestamp : `${timestamp.replace(" ", "T")}Z`);
}

function startOfLocalDay(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

function bucketFor(updatedAt: string, now: Date): Bucket {
  const then = parseUtc(updatedAt);
  const dayDiff = Math.round(
    (startOfLocalDay(now).getTime() - startOfLocalDay(then).getTime()) / 86_400_000,
  );
  // `<= 0` rather than `=== 0` absorbs clock skew (a thread updated a few
  // seconds in the future by a server/client drift) into TODAY instead of
  // inventing a fourth bucket nobody asked for.
  if (dayDiff <= 0) return "TODAY";
  if (dayDiff === 1) return "YESTERDAY";
  return "EARLIER";
}

/** Threads arrive `updated_at DESC` from the backend — bucketing here must
 *  not re-sort, only partition, or "newest first within each group" would
 *  come from luck rather than from the order the server already guarantees. */
function groupThreads(threads: ThreadInfo[]): [Bucket, ThreadInfo[]][] {
  const now = new Date();
  const groups: Record<Bucket, ThreadInfo[]> = { TODAY: [], YESTERDAY: [], EARLIER: [] };
  for (const thread of threads) groups[bucketFor(thread.updated_at, now)].push(thread);
  return BUCKET_ORDER.map((bucket): [Bucket, ThreadInfo[]] => [bucket, groups[bucket]]).filter(
    ([, items]) => items.length > 0,
  );
}

// --- component ---------------------------------------------------------------

/**
 * The left session rail for /chat: every persisted thread, grouped by local
 * day, with rename/delete/new-thread affordances. Reads the chat context for the
 * live thread and its actions, and `useChatThreads()` (SWR) for the list —
 * the two are separate data sources on purpose, so this rail keeps working
 * (and revalidates independently) even while a turn is streaming.
 */
export default function ThreadRail({ course }: { course?: string }) {
  const { data, isLoading, mutate } = useChatThreads(course);
  const { threadId } = useChatMeta();
  const { openThread, newThread } = useChatActions();
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();
  const collapsed = useRailCollapsed();

  const [editingId, setEditingId] = useState<number | null>(null);
  const [editValue, setEditValue] = useState("");
  // Rename can be committed by Enter *or* by the field losing focus; Escape
  // must win when both fire from the same keystroke (it closes the field,
  // which blurs it). Tracking "did the user just cancel" here means the
  // trailing blur is a no-op instead of a second, unwanted save.
  const cancelledRef = useRef(false);

  const threads = useMemo(() => data ?? [], [data]);
  // Re-bucketing the whole list on every render was cheap per pass and paid on
  // every one -- including, before the context split, every batched delta of a
  // streaming answer.
  const groups = useMemo(() => groupThreads(threads), [threads]);

  function handleOpen(thread: ThreadInfo) {
    if (editingId === thread.id) return;
    openThread(thread.id).catch((error: unknown) => {
      show(error instanceof Error ? error.message : "failed to load thread", { tone: "error" });
    });
  }

  function startRename(thread: ThreadInfo) {
    setEditingId(thread.id);
    setEditValue(thread.title);
  }

  function cancelRename() {
    cancelledRef.current = true;
    setEditingId(null);
  }

  async function commitRename(thread: ThreadInfo) {
    if (cancelledRef.current) {
      cancelledRef.current = false;
      return;
    }
    setEditingId(null);
    const title = editValue.trim();
    // Blank means "cancel", not "clear the title" — the backend 400s on an
    // empty title, so sending it would just bounce back as an error.
    if (!title || title === thread.title) return;
    try {
      await patchChatThread(thread.id, { title });
    } catch (error) {
      show(error instanceof Error ? error.message : "rename failed", { tone: "error" });
    } finally {
      mutate();
    }
  }

  function handleEditKeyDown(event: KeyboardEvent<HTMLInputElement>, thread: ThreadInfo) {
    if (event.key === "Enter") {
      event.preventDefault();
      void commitRename(thread);
    } else if (event.key === "Escape") {
      event.preventDefault();
      cancelRename();
    }
  }

  async function handleDelete(thread: ThreadInfo) {
    const answer = await confirm({
      label: "Delete thread",
      message: `Delete "${thread.title || "this conversation"}"?`,
      detail: thread.message_count === 1 ? "1 message" : `${thread.message_count} messages`,
      confirmLabel: "Delete",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await deleteChatThread(thread.id);
      // The transcript on screen belongs to a row that no longer exists —
      // leaving it up would let the next send() try to append to a dead
      // thread_id.
      if (thread.id === threadId) newThread();
    } catch (error) {
      show(error instanceof Error ? error.message : "delete failed", { tone: "error" });
    } finally {
      mutate();
    }
  }

  if (collapsed) {
    return (
      <nav
        aria-label="Conversations"
        className="animate-rise flex w-12 shrink-0 flex-col items-center gap-3 border-r border-line bg-panel py-3"
      >
        <button
          type="button"
          aria-label="New thread"
          onClick={() => newThread()}
          className="flex h-8 w-8 shrink-0 items-center justify-center border border-line font-mono text-lead text-ink-faint transition-colors hover:border-lineHi hover:text-[var(--ac)]"
        >
          ⊕
        </button>
        <div className="flex-1" />
        <button
          type="button"
          aria-label="Expand thread list"
          onClick={() => setCollapsedStore(false)}
          className="flex h-8 w-8 shrink-0 items-center justify-center font-mono text-label text-ink-faint transition-colors hover:text-ink"
        >
          ›
        </button>
        {confirmDialog}
      </nav>
    );
  }

  return (
    <nav
      aria-label="Conversations"
      className="animate-rise flex w-64 shrink-0 flex-col border-r border-line bg-panel"
    >
      <div className="flex items-center gap-2 border-b border-line px-3 py-3">
        <p className="eyebrow min-w-0 flex-1 truncate">▍THREADS</p>
        <button
          type="button"
          aria-label="New thread"
          onClick={() => newThread()}
          className="flex h-7 w-7 shrink-0 items-center justify-center border border-line font-mono text-label text-ink-faint transition-colors hover:border-lineHi hover:text-[var(--ac)]"
        >
          ⊕
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-1.5 py-1.5">
        {isLoading ? (
          <p className="px-1.5 py-2 text-label text-ink-faint">loading…</p>
        ) : threads.length === 0 ? (
          <p className="px-1.5 py-2 text-label text-ink-faint">no conversations yet</p>
        ) : (
          groups.map(([bucket, items]) => (
            <div key={bucket} className="mb-1">
              <p className="px-1.5 pb-1 pt-2 font-mono text-micro uppercase tracking-[0.14em] text-ink-faint">
                {bucket}
              </p>
              <ul>
                {items.map((thread) => {
                  const active = thread.id === threadId;
                  const editing = editingId === thread.id;
                  return (
                    <li key={thread.id} className="group">
                      {editing ? (
                        <input
                          autoFocus
                          value={editValue}
                          onChange={(event) => setEditValue(event.target.value)}
                          onKeyDown={(event) => handleEditKeyDown(event, thread)}
                          onFocus={() => {
                            cancelledRef.current = false;
                          }}
                          onBlur={() => void commitRename(thread)}
                          aria-label="Thread title"
                          className="w-full border border-lineHi bg-sunken px-2 py-1.5 text-body text-ink focus:outline-none"
                        />
                      ) : (
                        <div
                          className={`flex items-center gap-1 border-l-2 pl-1.5 pr-1 transition-colors ${
                            active
                              ? "border-[var(--ac)] bg-[var(--ac-bg)]"
                              : "border-transparent hover:border-line hover:bg-sunken"
                          }`}
                        >
                          <button
                            type="button"
                            onClick={() => handleOpen(thread)}
                            onDoubleClick={() => startRename(thread)}
                            aria-current={active ? "true" : undefined}
                            className={`min-w-0 flex-1 truncate py-1.5 text-left text-body ${
                              active ? "text-ink-bright" : "text-ink-muted hover:text-ink"
                            }`}
                          >
                            {thread.title || "untitled"}
                          </button>
                          {thread.course && (
                            <span className="shrink-0 border border-line px-1 py-px font-mono text-micro uppercase tracking-[0.1em] text-ink-faint">
                              {thread.course}
                            </span>
                          )}
                          <span className="hidden shrink-0 items-center gap-0.5 group-hover:flex group-focus-within:flex">
                            <button
                              type="button"
                              aria-label="Rename thread"
                              onClick={() => startRename(thread)}
                              className="flex h-6 w-6 items-center justify-center font-mono text-meta text-ink-faint hover:text-[var(--ac)]"
                            >
                              ✎
                            </button>
                            <button
                              type="button"
                              aria-label="Delete thread"
                              onClick={() => void handleDelete(thread)}
                              className="flex h-6 w-6 items-center justify-center font-mono text-label text-ink-faint hover:text-danger"
                            >
                              ×
                            </button>
                          </span>
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          ))
        )}
      </div>

      <button
        type="button"
        onClick={() => setCollapsedStore(true)}
        className="border-t border-line px-3 py-2 text-left font-mono text-meta uppercase tracking-[0.12em] text-ink-faint transition-colors hover:text-ink"
      >
        ‹ collapse
      </button>

      {confirmDialog}
    </nav>
  );
}
