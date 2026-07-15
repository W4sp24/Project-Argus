"use client";

import { useEffect, useState } from "react";
import Panel from "@/components/Panel";
import { FLAGS } from "@/lib/flags";

interface Highlight {
  id: string;
  text: string;
}

const SEED: Highlight[] = [
  { id: "h1", text: "“Self-attention lets every position attend to every other position in O(1) sequential ops.”" },
  { id: "h2", text: "“RAG grounds generation in retrieved passages instead of parametric memory alone.”" },
];

/**
 * HIGHLIGHTS.RECENT (§4 Research) — spec'd as a real append into
 * `30-Areas/papers/inbox.md`, but that write needs a create-on-first-use
 * endpoint that doesn't exist yet (see LIBRARY.QUEUE's note — `notes_api.py`
 * has no POST /api/note/create). Local state only, same as the queue, so the
 * two panels behave consistently; `onCount` feeds the page's stat row.
 */
export default function HighlightsRecent({ onCount }: { onCount: (count: number) => void }) {
  const [highlights, setHighlights] = useState<Highlight[]>(SEED);
  const [draft, setDraft] = useState("");

  useEffect(() => onCount(highlights.length), [highlights, onCount]);

  function add(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    setHighlights((current) => [{ id: `h${Date.now()}`, text }, ...current]);
    setDraft("");
  }

  function remove(id: string) {
    setHighlights((current) => current.filter((h) => h.id !== id));
  }

  return (
    <Panel label="HIGHLIGHTS.RECENT" preview={FLAGS.library === "preview"}>
      <form onSubmit={add} className="mb-3 flex items-center gap-2 border border-line px-3 py-2 focus-within:border-lineHi">
        <span className="shrink-0 font-mono text-[var(--ac)]">＋</span>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="paste a highlight…"
          className="min-w-0 flex-1 bg-transparent text-[13px] placeholder:text-ink-faint focus:outline-none"
        />
      </form>
      <ul className="divide-y divide-line">
        {highlights.map((h) => (
          <li key={h.id} className="group flex items-start gap-2 py-2">
            <span className="min-w-0 flex-1 text-[13px] leading-relaxed text-ink-muted">{h.text}</span>
            <button
              type="button"
              aria-label="Delete highlight"
              onClick={() => remove(h.id)}
              className="shrink-0 font-mono text-xs text-ink-faint opacity-0 transition-opacity hover:text-danger group-hover:opacity-100"
            >
              ×
            </button>
          </li>
        ))}
        {highlights.length === 0 && <p className="py-2 text-sm text-ink-faint">No highlights yet.</p>}
      </ul>
    </Panel>
  );
}
