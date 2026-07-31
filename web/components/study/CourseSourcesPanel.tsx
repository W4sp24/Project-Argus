"use client";

import { useEffect, useRef, useState, type DragEvent } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { apiFetch, useCourseSources, type CourseSource } from "@/lib/api";

const ACCEPTED_EXTENSIONS = [".pdf", ".pptx", ".docx", ".md"];

function relativeTime(iso: string): string {
  const then = new Date(iso).getTime();
  const diffMs = Date.now() - then;
  const days = Math.floor(diffMs / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "1d ago";
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  return `${months}mo ago`;
}

function isAcceptedFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

/**
 * SOURCES rail (§4 Course Hub, left 300px) — real data from the dedicated
 * `GET /api/study/courses/<code>/sources` (backend/features/study/corpus.py
 * `course_sources`), scoped to the `materials` and `notes` zones. Replaces
 * filtering `useNotes()` by a hardcoded `15-Courses/<CODE>/` prefix, which
 * only ever listed markdown — a PDF/PPTX/DOCX material (the common case for
 * course materials) could never appear here even though it was really saved
 * and indexed. The dropzone is real too: it POSTs to `/api/study/upload`
 * (the same endpoint `CoursesPanel`'s + FILES button uses), not a decorative
 * `[preview]` div with no drop handler and no file input.
 *
 * Checkbox selection is still a client-only RAG-context toggle — no query is
 * actually scoped by it yet (course-level scoping is real now, via chat's
 * `course` field; per-file selection remains a follow-up).
 */
export default function CourseSourcesPanel({ code }: { code: string }) {
  const { data: sources, mutate: refreshSources } = useCourseSources(code);
  const { show } = useToast();

  const [selected, setSelected] = useState<Set<string>>(new Set());
  const initialized = useRef(false);
  useEffect(() => {
    if (initialized.current || !sources) return;
    initialized.current = true;
    setSelected(new Set(sources.filter((s) => s.zone !== "study").map((s) => s.path)));
  }, [sources]);

  const [dragOver, setDragOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const visibleSources: CourseSource[] = (sources ?? []).filter(
    (source) => source.zone === "materials" || source.zone === "notes",
  );

  function toggle(path: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  async function upload(file: File) {
    if (!isAcceptedFile(file)) {
      show(`"${file.name}" isn't supported — use ${ACCEPTED_EXTENSIONS.join(", ")}`);
      return;
    }
    setBusy(true);
    const body = new FormData();
    body.append("course", code);
    body.append("file", file);
    try {
      const response = await apiFetch("/api/study/upload", { method: "POST", body });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) {
        throw new Error(
          typeof payload.detail === "string" ? payload.detail : `upload failed (${response.status})`,
        );
      }
      show(`saved ${payload.path} — indexing in the background`);
      refreshSources();
    } catch (error) {
      show(`upload failed — ${error instanceof Error ? error.message : "backend offline?"}`);
    } finally {
      setBusy(false);
    }
  }

  function pickFile(picked: File | null | undefined) {
    if (!picked || busy) return;
    upload(picked);
  }

  function handleDrop(event: DragEvent<HTMLElement>) {
    event.preventDefault();
    setDragOver(false);
    pickFile(event.dataTransfer.files?.[0]);
  }

  const selectedCount = visibleSources.filter((s) => selected.has(s.path)).length;

  return (
    <Panel label={`SOURCES · ${selectedCount}/${visibleSources.length} selected`}>
      {visibleSources.length === 0 ? (
        <p className="text-label text-ink-faint">No indexed files for this course yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {visibleSources.map((source) => (
            <li
              key={source.path}
              className="flex items-start gap-2 border border-line px-2.5 py-2 transition-colors hover:border-lineHi"
            >
              <button
                role="checkbox"
                aria-checked={selected.has(source.path)}
                aria-label={`Include ${source.title} in retrieval`}
                onClick={() => toggle(source.path)}
                className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center border transition-colors ${
                  selected.has(source.path) ? "border-[var(--ac)] bg-[var(--ac)] text-void" : "border-line"
                }`}
              >
                {selected.has(source.path) && "✓"}
              </button>
              <div className="min-w-0 flex-1">
                <p className="truncate text-label text-ink">{source.title}</p>
                <p className="mt-0.5 font-mono text-meta text-ink-faint">
                  {source.zone} · {relativeTime(source.modified)}
                  {source.chunks !== null && ` · ${source.chunks} chunk${source.chunks === 1 ? "" : "s"}`}
                </p>
              </div>
              <span className="shrink-0 border border-line px-1 py-px font-mono text-micro text-ink-faint">
                {source.kind}
              </span>
            </li>
          ))}
        </ul>
      )}

      <button
        type="button"
        onDragOver={(event) => {
          event.preventDefault();
          if (!busy) setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => inputRef.current?.click()}
        disabled={busy}
        className={`mt-3 w-full cursor-pointer border border-dashed px-3 py-4 text-center transition-[border-color,background-color] disabled:cursor-wait disabled:opacity-60 ${
          dragOver ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
        }`}
      >
        <span className="font-mono text-meta text-ink-faint">
          {busy ? "uploading…" : `drop a file, or click to choose (${ACCEPTED_EXTENSIONS.join(" ")})`}
        </span>
      </button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED_EXTENSIONS.join(",")}
        aria-hidden
        tabIndex={-1}
        className="hidden"
        onChange={(event) => {
          pickFile(event.target.files?.[0]);
          event.target.value = "";
        }}
      />
    </Panel>
  );
}
