"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useNotes } from "@/lib/api";

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

function typeChip(path: string): string {
  return path.toLowerCase().endsWith(".pdf") ? "PDF" : "MD";
}

/**
 * SOURCES rail (§4 Course Hub, left 300px) — real data from `GET /api/notes`
 * (backend/notes.py `list_notes`), filtered to `15-Courses/<CODE>/`. There's
 * no chunk-count concept in the data model (no per-file RAG metadata is
 * exposed), so the meta line shows folder + last-modified instead, as the
 * task allows. Checkbox selection is a client-only RAG-context toggle — no
 * query is actually scoped by it yet (Phase F wires retrieval).
 */
export default function CourseSourcesPanel({ code }: { code: string }) {
  const { data: notes } = useNotes();
  const prefix = `15-Courses/${code}/`;
  const sources = (notes ?? []).filter((note) => note.path.startsWith(prefix));

  const [selected, setSelected] = useState<Set<string>>(() => new Set(sources.map((s) => s.path)));

  function toggle(path: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  const selectedCount = sources.filter((s) => selected.has(s.path)).length;

  return (
    <Panel label={`SOURCES · ${selectedCount}/${sources.length} selected`}>
      {sources.length === 0 ? (
        <p className="text-[12.5px] text-ink-faint">
          No indexed files under <span className="font-mono text-[11px]">{prefix}</span> yet.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {sources.map((source) => (
            <li key={source.path} className="flex items-start gap-2 border border-line px-2.5 py-2 transition-colors hover:border-lineHi">
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
                <p className="truncate text-[12.5px] text-ink">{source.title}</p>
                <p className="mt-0.5 font-mono text-[10px] text-ink-faint">
                  {source.folder || "/"} · {relativeTime(source.modified)}
                </p>
              </div>
              <span className="shrink-0 border border-line px-1 py-px font-mono text-[9px] text-ink-faint">
                {typeChip(source.path)}
              </span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 border border-dashed border-line px-3 py-4 text-center">
        <p className="font-mono text-[10.5px] text-ink-faint">
          drop files to ingest <span className="text-[8px] uppercase tracking-[0.16em] text-[#8b7bc0]">[preview]</span>
        </p>
      </div>
    </Panel>
  );
}
