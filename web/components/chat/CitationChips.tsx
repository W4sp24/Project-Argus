"use client";

import React from "react";
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
function CitationChips({
  steps,
  vaultPath,
}: {
  steps: ToolStep[];
  vaultPath: string | undefined;
}) {
  // A path can be cited by more than one tool call in a turn — a search that
  // surfaced it and a read that opened it — and should still appear once.
  const paths = Array.from(new Set(steps.flatMap((step) => step.paths ?? [])));
  if (paths.length === 0) return null;

  return (
    <ul className="mt-2 flex flex-wrap gap-1.5" aria-label="Sources for this answer">
      {paths.map((path) => {
        const chip = `⌗ ${path.split("/").pop()}`;
        const shared =
          "inline-block border border-line bg-[var(--ac-bg)] px-1.5 py-0.5 font-mono text-micro text-[var(--ac)]";
        // Until /api/vault answers there is no vault root, and a link built
        // without one is a link that is certain to fail. Showing the source as
        // plain text for that moment is better than an obsidian:// URL that
        // opens an error dialog.
        return (
          <li key={path}>
            {vaultPath ? (
              <a
                href={obsidianUri(vaultPath, path)}
                title={`Open ${path} in Obsidian`}
                className={`${shared} transition-colors hover:border-lineHi`}
              >
                {chip}
              </a>
            ) : (
              <span title={path} className={shared}>
                {chip}
              </span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** Memoised for the same reason ToolTrace is: this rebuilds a deduped path set
 *  out of the whole step list, and did so for every message in the thread on
 *  every render of the transcript. */
export default React.memo(CitationChips);
