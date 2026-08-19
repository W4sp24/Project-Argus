"use client";

import type { ToolStep } from "@/lib/chat";
import { obsidianUri } from "@/lib/citations";

/**
 * The sources behind an answer (I6), drawn from the turn's tool trace rather
 * than scraped out of its prose.
 *
 * Every path here came off a `tool` end frame, which the backend built in
 * `_tool_frame()` after passing each one through `is_indexable` — so a
 * `99-Private/` note cannot reach this list even if the model names it in the
 * answer text. That is the whole reason the chips moved off the regex.
 */
export default function CitationChips({
  steps,
  vaultName,
}: {
  steps: ToolStep[];
  vaultName: string;
}) {
  // A path can be cited by more than one tool call in a turn — a search that
  // surfaced it and a read that opened it — and should still appear once.
  const paths = Array.from(new Set(steps.flatMap((step) => step.paths ?? [])));
  if (paths.length === 0) return null;

  return (
    <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Sources for this answer">
      {paths.map((path) => (
        <li key={path}>
          <a
            href={obsidianUri(vaultName, path)}
            title={`Open ${path} in Obsidian`}
            className="inline-block border border-line bg-[var(--ac-bg)] px-1.5 py-0.5 font-mono text-micro text-[var(--ac)] transition-colors hover:border-lineHi"
          >
            ⌗ {path.split("/").pop()}
          </a>
        </li>
      ))}
    </ul>
  );
}
