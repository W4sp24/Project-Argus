"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  ApiError,
  apiFetch,
  fetchNoteOrNull,
  mutateJSON,
  updateNoteWithRetry,
  useNotesIn,
  useVault,
  type IngestResponse,
  type NoteInfo,
} from "@/lib/api";
import { useTypewriter } from "@/lib/useTypewriter";

type Status = "QUEUED" | "READING" | "DONE";

interface Paper {
  path: string;
  title: string;
  authorsVenue: string;
  status: Status;
  progress: number;
}

const FIELDS = ["type", "authors_venue", "status", "progress", "file_path"];
const CYCLE: Record<Status, Status> = { QUEUED: "READING", READING: "DONE", DONE: "QUEUED" };
const STATUS_CLASS: Record<Status, string> = {
  QUEUED: "border-ink-faint text-ink-faint",
  READING: "border-[var(--ac)] text-[var(--ac)]",
  DONE: "border-ok text-ok",
};

function nextProgress(current: number, next: Status): number {
  if (next === "QUEUED") return 0;
  if (next === "DONE") return 100;
  return current || 40; // READING: keep it, or seed a visible default from 0
}

function slugify(title: string): string {
  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "paper";
}

function yamlStr(value: string): string {
  return `"${value.replace(/\\/g, "\\\\").replace(/"/g, '\\"')}"`;
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function renderPaperNote(title: string, authorsVenue: string, filePath?: string): string {
  const lines = [
    "---",
    "type: paper",
    `title: ${yamlStr(title)}`,
    `authors_venue: ${yamlStr(authorsVenue)}`,
    "status: queued",
    "progress: 0",
    `created: "${todayIso()}"`,
  ];
  if (filePath) lines.push(`file_path: ${yamlStr(filePath)}`);
  lines.push("---", "", `# ${title}`, "");
  return lines.join("\n") + "\n";
}

function withStatus(content: string, status: Status, progress: number): string {
  return content
    .replace(/^status: .*$/m, `status: ${status.toLowerCase()}`)
    .replace(/^progress: .*$/m, `progress: ${progress}`);
}

function statusFrom(raw: unknown): Status {
  const value = typeof raw === "string" ? raw.toUpperCase() : "";
  return value === "READING" || value === "DONE" ? value : "QUEUED";
}

function progressFrom(raw: unknown): number {
  const n = typeof raw === "number" ? raw : Number(raw);
  return Number.isFinite(n) ? n : 0;
}

function toPaper(note: NoteInfo): Paper {
  const fm = note.frontmatter ?? {};
  return {
    path: note.path,
    title: note.title,
    authorsVenue: typeof fm.authors_venue === "string" ? fm.authors_venue : "",
    status: statusFrom(fm.status),
    progress: progressFrom(fm.progress),
  };
}

/** Create a paper note, retrying with a numbered suffix on a 409 (path taken)
 * the same way `NoteModal` does for quick notes. */
async function createPaperWithRetry(
  dir: string,
  baseSlug: string,
  content: () => string,
): Promise<string> {
  async function attempt(suffix: number): Promise<string> {
    const path = suffix === 1 ? `${dir}/${baseSlug}.md` : `${dir}/${baseSlug}-${suffix}.md`;
    try {
      await mutateJSON<{ path: string }>("/api/note/create", { path, content: content() });
      return path;
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && suffix < 20) {
        return attempt(suffix + 1);
      }
      throw error;
    }
  }
  return attempt(1);
}

export interface LibraryCounts {
  papers: number;
  queued: number;
  reading: number;
}

/**
 * LIBRARY.QUEUE (§4 Research) — real persistence: one note per paper under
 * `<areas>/papers/` (the path comes from `useVault().papers_dir`, derived
 * server-side from the configured taxonomy — never hardcoded here), listed
 * through `GET /api/notes?folder=&fields=` so the whole queue's
 * status/progress costs one request, not one per paper. Add creates via
 * `POST /api/note/create`; the status chip cycles QUEUED → READING → DONE by
 * reading the note's raw content and writing it back through the
 * compare-and-swap `PUT /api/note`; delete goes behind `useConfirm()` and
 * really removes the note (`DELETE /api/note`, git-snapshotted first).
 * Dropping a PDF really uploads it (`POST /api/ingest`, targeted at the
 * papers folder) and then creates a note referencing the stored path.
 *
 * Previously this was local `useState` seeded from a hardcoded `SEED` array:
 * `×` looked like it deleted a paper, but nothing was ever persisted, so
 * every navigation, reload, or restart brought the same three papers back —
 * and there was never a real paper to delete in the first place. That is the
 * reported "Research — deleting seems to not work" bug.
 */
export default function LibraryQueue({ onCounts }: { onCounts: (counts: LibraryCounts) => void }) {
  const { data: vault } = useVault();
  const papersDir = vault?.papers_dir ?? null;
  const { data: noteList, mutate } = useNotesIn(papersDir, FIELDS);

  const papers = useMemo(
    () => (noteList ?? []).filter((note) => note.frontmatter?.type === "paper").map(toPaper),
    [noteList],
  );

  const [title, setTitle] = useState("");
  const [meta, setMeta] = useState("");
  const [creating, setCreating] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [ingestName, setIngestName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();

  useEffect(() => {
    onCounts({
      papers: papers.length,
      queued: papers.filter((p) => p.status === "QUEUED").length,
      reading: papers.filter((p) => p.status === "READING").length,
    });
  }, [papers, onCounts]);

  const ingestStatus = ingestName ? `ingesting ${ingestName} :: extract → save` : "";
  const { output: ingestOutput } = useTypewriter(ingestStatus, 24);

  async function addPaper(event: React.FormEvent) {
    event.preventDefault();
    const t = title.trim();
    if (!t || !papersDir || creating) return;
    setCreating(true);
    try {
      const path = await createPaperWithRetry(papersDir, slugify(t), () =>
        renderPaperNote(t, meta.trim()),
      );
      show(`paper :: added → ${path}`);
      setTitle("");
      setMeta("");
      mutate();
    } catch (error) {
      show(`paper :: add failed — ${error instanceof Error ? error.message : "backend offline?"}`);
    } finally {
      setCreating(false);
    }
  }

  async function cycle(paper: Paper) {
    if (busyPath) return;
    setBusyPath(paper.path);
    const next = CYCLE[paper.status];
    const progress = nextProgress(paper.progress, next);
    try {
      const current = await fetchNoteOrNull(paper.path);
      if (current === null) {
        show(`paper :: ${paper.title} no longer exists — refreshing`);
        mutate();
        return;
      }
      await updateNoteWithRetry(paper.path, current, (content) => withStatus(content, next, progress));
      mutate();
    } catch (error) {
      show(`paper :: status update failed — ${error instanceof Error ? error.message : "backend offline?"}`);
    } finally {
      setBusyPath(null);
    }
  }

  async function remove(paper: Paper) {
    const answer = await confirm({
      label: `Delete ${paper.title}`,
      message: `Delete "${paper.title}"?`,
      detail: `This removes ${paper.path} from the vault (a git snapshot makes it undoable).`,
      confirmLabel: "DELETE",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await mutateJSON(`/api/note?path=${encodeURIComponent(paper.path)}`, undefined, "DELETE");
      show(`paper :: ${paper.title} deleted`);
      mutate();
    } catch (error) {
      show(`paper :: delete failed — ${error instanceof Error ? error.message : "backend offline?"}`);
    }
  }

  async function pickFile(file: File | null | undefined) {
    if (!file || !papersDir || ingestName) return;
    setIngestName(file.name);
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("target", papersDir);
      const response = await apiFetch("/api/ingest", { method: "POST", body });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string" ? payload.detail : `upload failed (${response.status})`,
        );
      }
      const { path: filePath, index_error } = payload as IngestResponse;
      const guessedTitle =
        file.name.replace(/\.[^.]+$/, "").replace(/[_-]+/g, " ").trim() || file.name;
      const notePath = await createPaperWithRetry(papersDir, slugify(guessedTitle), () =>
        renderPaperNote(guessedTitle, "", filePath),
      );
      show(
        index_error
          ? `ingest :: saved — indexing failed: ${index_error}`
          : `ingest :: ${file.name} added → ${notePath}`,
      );
      mutate();
    } catch (error) {
      show(`ingest :: failed — ${error instanceof Error ? error.message : "backend offline?"}`);
    } finally {
      setIngestName(null);
    }
  }

  return (
    <Panel label="LIBRARY.QUEUE">
      <form onSubmit={addPaper} className="mb-4 space-y-2 border-b border-line pb-4">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Paper title"
          aria-label="Paper title"
          className="w-full border border-line bg-sunken px-3 py-2 text-body placeholder:text-ink-faint focus:border-lineHi"
        />
        <div className="flex gap-2">
          <input
            value={meta}
            onChange={(e) => setMeta(e.target.value)}
            placeholder="authors · venue"
            aria-label="Authors and venue"
            className="min-w-0 flex-1 border border-line bg-sunken px-3 py-2 text-body placeholder:text-ink-faint focus:border-lineHi"
          />
          <Button
            type="submit"
            size="md"
            disabled={!title.trim() || !papersDir || creating}
            className="shrink-0"
          >
            {creating ? "ADDING…" : "+ ADD PAPER"}
          </Button>
        </div>
      </form>

      <ul className="space-y-3">
        {papers.map((paper) => (
          <li key={paper.path} className="group border border-line p-3 transition-colors hover:border-lineHi">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-body text-ink">{paper.title}</p>
                {paper.authorsVenue && (
                  <p className="mt-0.5 truncate font-mono text-meta text-ink-faint">{paper.authorsVenue}</p>
                )}
              </div>
              <button
                type="button"
                onClick={() => cycle(paper)}
                disabled={busyPath === paper.path}
                aria-label={`Cycle status, currently ${paper.status}`}
                className={`shrink-0 border px-1.5 py-0.5 font-mono text-micro uppercase tracking-[0.12em] transition-colors disabled:opacity-50 ${STATUS_CLASS[paper.status]}`}
              >
                {paper.status}
              </button>
              <Button
                variant="ghost"
                aria-label={`Delete ${paper.title}`}
                onClick={() => remove(paper)}
                className="shrink-0 opacity-0 transition-opacity hover:text-danger focus-visible:opacity-100 group-hover:opacity-100"
              >
                ×
              </Button>
            </div>
            <div className="mt-2 h-1 w-full bg-sunken">
              <div className="h-1 bg-[var(--ac)] transition-[width]" style={{ width: `${paper.progress}%` }} />
            </div>
          </li>
        ))}
        {papers.length === 0 && (
          <p className="text-sm text-ink-faint">
            {noteList === undefined && papersDir ? "Loading…" : "Queue is empty."}
          </p>
        )}
      </ul>

      {/* A <button>, not a clickable <div> — see IngestPanel. */}
      <button
        type="button"
        onDragOver={(event) => {
          event.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          pickFile(event.dataTransfer.files?.[0]);
        }}
        onClick={() => inputRef.current?.click()}
        disabled={!!ingestName}
        className={`mt-4 w-full cursor-pointer border border-dashed px-4 py-4 text-center transition-[border-color,background-color] disabled:cursor-wait ${
          dragOver ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
        }`}
      >
        <span className="block font-mono text-label text-ink-muted">
          drop a paper PDF, or click to choose
        </span>
        {ingestOutput && (
          <span className="mt-2 block font-mono text-meta text-ink-faint">{ingestOutput}</span>
        )}
      </button>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf"
        aria-hidden
        tabIndex={-1}
        className="hidden"
        onChange={(event) => pickFile(event.target.files?.[0])}
      />
      {confirmDialog}
    </Panel>
  );
}
