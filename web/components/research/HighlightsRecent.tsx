"use client";

import { useEffect, useState } from "react";
import useSWR from "swr";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  ApiError,
  fetchNoteOrNull,
  mutateJSON,
  updateNoteWithRetry,
  useVault,
} from "@/lib/api";

interface Highlight {
  id: string;
  text: string;
}

const HEADING = "# Highlights";
const LINE_RE = /^- (.*) <!-- (\d+) -->\s*$/;

function parseHighlights(content: string | null | undefined): Highlight[] {
  if (!content) return [];
  const highlights: Highlight[] = [];
  for (const line of content.split("\n")) {
    const match = line.match(LINE_RE);
    if (match) highlights.push({ text: match[1], id: match[2] });
  }
  return highlights.reverse(); // newest (highest id, appended last) first
}

function renderInitial(line: string): string {
  return `---\ntype: paper-highlights\n---\n\n${HEADING}\n\n${line}\n`;
}

function appendLine(content: string, line: string): string {
  return `${content.replace(/\n+$/, "")}\n${line}\n`;
}

function removeLine(content: string, id: string): string {
  return content
    .split("\n")
    .filter((line) => !line.includes(`<!-- ${id} -->`))
    .join("\n");
}

/**
 * HIGHLIGHTS.RECENT (§4 Research) — real persistence: every highlight is one
 * bullet line appended to a single running note, `<areas>/papers/highlights.md`
 * (`useVault().highlights_path` — derived server-side from the configured
 * taxonomy, never hardcoded here). The note is created on first use
 * (`POST /api/note/create`); every append or delete after that is a
 * read-modify-write through the compare-and-swap `PUT /api/note`
 * (`updateNoteWithRetry` in lib/api.ts), which retries against the current
 * content a 409 (`WriterConflict`) carries rather than clobbering it.
 *
 * Previously this was local `useState` seeded from a hardcoded `SEED` array —
 * `×` looked like it deleted a highlight, but nothing was ever written to the
 * vault, so it came back on the next reload, route change, or app restart.
 */
export default function HighlightsRecent({ onCount }: { onCount: (count: number) => void }) {
  const { data: vault } = useVault();
  const highlightsPath = vault?.highlights_path ?? null;
  const {
    data: content,
    mutate,
  } = useSWR(
    highlightsPath ? ["research-highlights", highlightsPath] : null,
    () => fetchNoteOrNull(highlightsPath as string),
  );

  const highlights = parseHighlights(content);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();

  useEffect(() => onCount(highlights.length), [highlights.length, onCount]);

  async function add(event: React.FormEvent) {
    event.preventDefault();
    const text = draft.trim();
    // `content === undefined` means the initial GET hasn't resolved yet —
    // waiting avoids racing a stray create against a note that does exist.
    if (!text || !highlightsPath || busy || content === undefined) return;
    setBusy(true);
    const line = `- ${text} <!-- ${Date.now()} -->`;
    try {
      if (content == null) {
        try {
          await mutateJSON<{ path: string }>("/api/note/create", {
            path: highlightsPath,
            content: renderInitial(line),
          });
        } catch (error) {
          if (!(error instanceof ApiError && error.status === 409)) throw error;
          // Created concurrently since our last read — append instead.
          const fresh = await fetchNoteOrNull(highlightsPath);
          if (fresh !== null) {
            await updateNoteWithRetry(highlightsPath, fresh, (c) => appendLine(c, line));
          }
        }
      } else {
        await updateNoteWithRetry(highlightsPath, content, (c) => appendLine(c, line));
      }
      setDraft("");
      mutate();
    } catch (error) {
      show(`highlight :: save failed — ${error instanceof Error ? error.message : "backend offline?"}`);
    } finally {
      setBusy(false);
    }
  }

  async function remove(highlight: Highlight) {
    if (!highlightsPath || content == null) return;
    const answer = await confirm({
      label: "Delete highlight",
      message: "Delete this highlight?",
      detail: highlight.text.length > 100 ? `${highlight.text.slice(0, 100)}…` : highlight.text,
      confirmLabel: "DELETE",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await updateNoteWithRetry(highlightsPath, content, (c) => removeLine(c, highlight.id));
      mutate();
    } catch (error) {
      show(`highlight :: delete failed — ${error instanceof Error ? error.message : "backend offline?"}`);
    }
  }

  return (
    <Panel label="HIGHLIGHTS.RECENT">
      <form onSubmit={add} className="mb-3 flex items-center gap-2 border border-line px-3 py-2 focus-within:border-lineHi">
        <span className="shrink-0 font-mono text-[var(--ac)]">＋</span>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="paste a highlight…"
          aria-label="Add a highlight"
          disabled={busy || !highlightsPath || content === undefined}
          className="min-w-0 flex-1 bg-transparent text-body placeholder:text-ink-faint focus:outline-none disabled:opacity-50"
        />
      </form>
      <ul className="divide-y divide-line">
        {highlights.map((h) => (
          <li key={h.id} className="group flex items-start gap-2 py-2">
            <span className="min-w-0 flex-1 text-body leading-relaxed text-ink-muted">{h.text}</span>
            <button
              type="button"
              aria-label="Delete highlight"
              onClick={() => remove(h)}
              className="shrink-0 font-mono text-xs text-ink-faint opacity-0 transition-opacity hover:text-danger group-hover:opacity-100"
            >
              ×
            </button>
          </li>
        ))}
        {highlights.length === 0 && (
          <p className="py-2 text-sm text-ink-faint">
            {content === undefined && highlightsPath ? "Loading…" : "No highlights yet."}
          </p>
        )}
      </ul>
      {confirmDialog}
    </Panel>
  );
}
