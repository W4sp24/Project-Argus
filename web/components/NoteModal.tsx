"use client";

import { useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
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
  // The focus trap that used to live here is now ui/Dialog — this component
  // was the only one that had it right, so it became the shared version.
  const titleRef = useRef<HTMLInputElement>(null);

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
        show(
          `note :: save failed — ${error instanceof Error ? error.message : "backend offline?"}`,
          { tone: "error" },
        );
        setBusy(false);
      }
    }

    await attempt(`00-Inbox/${day}-${slug}.md`, 1);
  }

  return (
    <Dialog
      label="Add note"
      onClose={() => setNoteOpen(false)}
      initialFocusRef={titleRef}
      className="w-[32.5rem] max-w-[calc(100vw-2rem)] p-5"
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
          className="border border-line bg-sunken px-3 py-2 text-body text-ink placeholder:text-ink-faint focus:border-lineHi"
        />
        <textarea
          value={body}
          onChange={(event) => setBody(event.target.value)}
          placeholder="markdown — [[wikilinks]] and #tags work here"
          aria-label="Note body"
          rows={6}
          className="resize-none border border-line bg-sunken px-3 py-2 text-body leading-relaxed text-ink placeholder:text-ink-faint focus:border-lineHi"
        />
        <div className="flex items-center gap-3">
          <Button
            type="submit"
            size="md"
            variant="primary"
            disabled={busy || !(title.trim() || body.trim())}
          >
            {busy ? "SAVING…" : "SAVE NOTE"}
          </Button>
          <p className="min-w-0 flex-1 truncate font-mono text-meta text-ink-faint">
            → 00-Inbox/{todayIso()}-{slugify(title.trim() || "note")}.md
          </p>
        </div>
      </form>
    </Dialog>
  );
}
