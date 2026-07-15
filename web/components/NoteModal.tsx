"use client";

import { useEffect, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import { ApiError, mutateJSON } from "@/lib/api";
import { useUi } from "@/lib/ui";

/**
 * Quick add-note modal (§13), opened from the TopBar `+ NOTE` and the
 * palette. Renders nothing while closed (§10).
 *
 * Persistence: `POST /api/note/create` (backend/notes_api.py, backed by
 * `writer.create_note`) writes a title-derived `00-Inbox/YYYY-MM-DD-<slug>.md`
 * with the markdown body intact; the toast shows the saved path. A 409
 * (a note already exists at that exact path — e.g. two notes with the same
 * title on the same day) falls back to a numbered slug.
 */
function slugify(title: string): string {
  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "note";
}

function todayIso(): string {
  const now = new Date();
  const month = String(now.getMonth() + 1).padStart(2, "0");
  const day = String(now.getDate()).padStart(2, "0");
  return `${now.getFullYear()}-${month}-${day}`;
}

export default function NoteModal() {
  const { noteOpen } = useUi();
  if (!noteOpen) return null;
  return <NoteModalBody />;
}

function NoteModalBody() {
  const { setNoteOpen } = useUi();
  const { show } = useToast();
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const titleRef = useRef<HTMLInputElement>(null);
  const restoreRef = useRef<HTMLElement | null>(null);

  useEffect(() => {
    restoreRef.current = document.activeElement as HTMLElement | null;
    titleRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setNoteOpen(false);
      } else if (event.key === "Tab" && dialogRef.current) {
        // focus trap: cycle within the dialog's focusable controls
        const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
          "input, textarea, button:not([disabled])",
        );
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    const restore = restoreRef.current;
    return () => {
      window.removeEventListener("keydown", onKey);
      restore?.focus?.();
    };
  }, [setNoteOpen]);

  async function save() {
    const trimmedTitle = title.trim();
    const trimmedBody = body.trim();
    if (!trimmedTitle && !trimmedBody) return;
    if (busy) return;
    setBusy(true);

    const day = todayIso();
    const slug = slugify(trimmedTitle || trimmedBody.slice(0, 40));
    const content = trimmedTitle ? `# ${trimmedTitle}\n\n${trimmedBody}\n` : `${trimmedBody}\n`;

    async function attempt(path: string, suffix: number): Promise<void> {
      try {
        const { path: saved } = await mutateJSON<{ path: string }>("/api/note/create", {
          path,
          content,
        });
        show(`note :: saved → ${saved}`);
        setNoteOpen(false);
      } catch (error) {
        if (error instanceof ApiError && error.status === 409 && suffix < 20) {
          await attempt(`00-Inbox/${day}-${slug}-${suffix + 1}.md`, suffix + 1);
          return;
        }
        show(`note :: save failed — ${error instanceof Error ? error.message : "backend offline?"}`);
        setBusy(false);
      }
    }

    await attempt(`00-Inbox/${day}-${slug}.md`, 1);
  }

  return (
    <div
      className="fixed inset-0 z-50 bg-[rgba(3,2,8,0.72)]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) setNoteOpen(false);
      }}
    >
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label="Add note"
        className="animate-palette mx-auto mt-[16vh] w-[520px] max-w-[calc(100vw-2rem)] border border-lineHi bg-panel p-5"
      >
        <p className="eyebrow mb-3">▍QUICK.NOTE</p>
        <form
          onSubmit={(event) => {
            event.preventDefault();
            save();
          }}
          className="flex flex-col gap-3"
        >
          <input
            ref={titleRef}
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            placeholder="title"
            aria-label="Note title"
            className="border border-line bg-sunken px-3 py-2 text-sm text-ink placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
          />
          <textarea
            value={body}
            onChange={(event) => setBody(event.target.value)}
            placeholder="markdown — [[wikilinks]] and #tags work here"
            aria-label="Note body"
            rows={6}
            className="resize-none border border-line bg-sunken px-3 py-2 text-sm leading-relaxed text-ink placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
          />
          <div className="flex items-center gap-3">
            <button
              type="submit"
              disabled={busy || !(title.trim() || body.trim())}
              className="border border-line bg-[var(--ac-bg)] px-4 py-2 font-mono text-[11px] uppercase tracking-[0.12em] text-[var(--ac)] transition-colors hover:border-lineHi disabled:opacity-40"
            >
              {busy ? "SAVING…" : "SAVE NOTE"}
            </button>
            <p className="font-mono text-[10px] text-ink-faint">
              → 00-Inbox/{todayIso()}-{slugify(title.trim() || "note")}.md
            </p>
          </div>
        </form>
      </div>
    </div>
  );
}
