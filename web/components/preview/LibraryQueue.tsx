"use client";

import { useEffect, useRef, useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { FLAGS } from "@/lib/flags";
import { useTypewriter } from "@/lib/useTypewriter";

type Status = "QUEUED" | "READING" | "DONE";

interface Paper {
  id: string;
  title: string;
  authorsVenue: string;
  status: Status;
  progress: number;
}

const SEED: Paper[] = [
  { id: "p1", title: "Attention Is All You Need", authorsVenue: "Vaswani et al. · NeurIPS 2017", status: "DONE", progress: 100 },
  { id: "p2", title: "Retrieval-Augmented Generation for Knowledge-Intensive NLP", authorsVenue: "Lewis et al. · NeurIPS 2020", status: "READING", progress: 55 },
  { id: "p3", title: "A Survey of Local-First Software", authorsVenue: "Kleppmann et al. · PLOS 2019", status: "QUEUED", progress: 0 },
];

const CYCLE: Record<Status, Status> = { QUEUED: "READING", READING: "DONE", DONE: "QUEUED" };
const STATUS_CLASS: Record<Status, string> = {
  QUEUED: "border-ink-faint text-ink-faint",
  READING: "border-[var(--ac)] text-[var(--ac)]",
  DONE: "border-ok text-ok",
};

function nextProgress(paper: Paper, next: Status): number {
  if (next === "QUEUED") return 0;
  if (next === "DONE") return 100;
  return paper.progress || 40; // READING: keep it, or seed a visible default from 0
}

export interface LibraryCounts {
  papers: number;
  queued: number;
  reading: number;
}

/**
 * LIBRARY.QUEUE (§4 Research) [PREVIEW] — mock reading-queue CRUD. Real
 * persistence would need `POST /api/note/create` to write new frontmatter
 * notes under `30-Areas/papers/`; `notes_api.py` only exposes GET / PUT
 * (CAS against an *existing* file's `expected_content`) / DELETE — there is
 * no create endpoint in this branch's ancestry, so "create-on-first-use" per
 * the spec isn't possible without a new backend route (out of scope, backend
 * redesign branch). Local component state only; `onCounts` reports it up for
 * the page's stat row. No `fetch(` in this file (§8 grep guard).
 */
export default function LibraryQueue({ onCounts }: { onCounts: (counts: LibraryCounts) => void }) {
  const [papers, setPapers] = useState<Paper[]>(SEED);
  const [title, setTitle] = useState("");
  const [meta, setMeta] = useState("");
  const [dragOver, setDragOver] = useState(false);
  const [ingestName, setIngestName] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const { show } = useToast();

  useEffect(() => {
    onCounts({
      papers: papers.length,
      queued: papers.filter((p) => p.status === "QUEUED").length,
      reading: papers.filter((p) => p.status === "READING").length,
    });
  }, [papers, onCounts]);

  const ingestStatus = ingestName ? `queued ${ingestName} for ingest · preview only` : "";
  const { output: ingestOutput } = useTypewriter(ingestStatus, 24);

  function addPaper(event: React.FormEvent) {
    event.preventDefault();
    const t = title.trim();
    if (!t) return;
    setPapers((current) => [
      { id: `p${Date.now()}`, title: t, authorsVenue: meta.trim(), status: "QUEUED", progress: 0 },
      ...current,
    ]);
    setTitle("");
    setMeta("");
  }

  function cycle(id: string) {
    setPapers((current) =>
      current.map((paper) => {
        if (paper.id !== id) return paper;
        const next = CYCLE[paper.status];
        return { ...paper, status: next, progress: nextProgress(paper, next) };
      }),
    );
  }

  function remove(id: string) {
    setPapers((current) => current.filter((paper) => paper.id !== id));
  }

  function pickFile(file: File | null | undefined) {
    if (!file) return;
    setIngestName(file.name);
    show(`ingest :: ${file.name} — preview only, no upload`);
  }

  return (
    <Panel label="LIBRARY.QUEUE" preview={FLAGS.library === "preview"}>
      <form onSubmit={addPaper} className="mb-4 space-y-2 border-b border-line pb-4">
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Paper title"
          className="w-full border border-line bg-sunken px-3 py-2 text-[13px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
        />
        <div className="flex gap-2">
          <input
            value={meta}
            onChange={(e) => setMeta(e.target.value)}
            placeholder="authors · venue"
            className="min-w-0 flex-1 border border-line bg-sunken px-3 py-2 text-[13px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
          />
          <button
            type="submit"
            disabled={!title.trim()}
            className="shrink-0 border border-line px-3 py-2 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
          >
            + ADD PAPER
          </button>
        </div>
      </form>

      <ul className="space-y-3">
        {papers.map((paper) => (
          <li key={paper.id} className="group border border-line p-3 transition-colors hover:border-lineHi">
            <div className="flex items-start gap-2">
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13.5px] text-ink">{paper.title}</p>
                {paper.authorsVenue && (
                  <p className="mt-0.5 truncate font-mono text-[10.5px] text-ink-faint">{paper.authorsVenue}</p>
                )}
              </div>
              <button
                type="button"
                onClick={() => cycle(paper.id)}
                aria-label={`Cycle status, currently ${paper.status}`}
                className={`shrink-0 border px-1.5 py-0.5 font-mono text-[9.5px] uppercase tracking-[0.12em] transition-colors ${STATUS_CLASS[paper.status]}`}
              >
                {paper.status}
              </button>
              <button
                type="button"
                aria-label={`Delete ${paper.title}`}
                onClick={() => remove(paper.id)}
                className="hidden shrink-0 font-mono text-xs text-ink-faint hover:text-danger group-hover:inline"
              >
                ×
              </button>
            </div>
            <div className="mt-2 h-1 w-full bg-sunken">
              <div className="h-1 bg-[var(--ac)] transition-[width]" style={{ width: `${paper.progress}%` }} />
            </div>
          </li>
        ))}
        {papers.length === 0 && <p className="text-sm text-ink-faint">Queue is empty.</p>}
      </ul>

      <div
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
        className={`mt-4 cursor-pointer border border-dashed px-4 py-4 text-center transition-[border-color,background-color] ${
          dragOver ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
        }`}
      >
        <p className="font-mono text-[11px] text-ink-muted">drop a paper PDF, or click to choose</p>
        <input
          ref={inputRef}
          type="file"
          accept=".pdf"
          className="hidden"
          onChange={(event) => pickFile(event.target.files?.[0])}
        />
        {ingestOutput && <p className="mt-2 font-mono text-[10.5px] text-ink-faint">{ingestOutput}</p>}
      </div>
    </Panel>
  );
}
