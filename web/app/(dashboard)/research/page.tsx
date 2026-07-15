"use client";

import Link from "next/link";
import { useCallback, useState } from "react";
import ModeHeader from "@/components/ModeHeader";
import Panel from "@/components/Panel";
import StatRow from "@/components/StatRow";
import HighlightsRecent from "@/components/preview/HighlightsRecent";
import LibraryQueue, { type LibraryCounts } from "@/components/preview/LibraryQueue";
import { useNotes } from "@/lib/api";

/**
 * Research mode (§4) — LIBRARY.QUEUE + HIGHLIGHTS.RECENT are local-state
 * [PREVIEW] (see LibraryQueue.tsx for why: no create-note endpoint exists
 * yet). `notes` in the stat row is the one real number here: vault notes
 * already living under `30-Areas` via the real `/api/notes` listing.
 */
export default function ResearchPage() {
  const [libraryCounts, setLibraryCounts] = useState<LibraryCounts>({ papers: 0, queued: 0, reading: 0 });
  const [highlightCount, setHighlightCount] = useState(0);
  const { data: notes } = useNotes();

  const handleLibraryCounts = useCallback((counts: LibraryCounts) => setLibraryCounts(counts), []);
  const handleHighlightCount = useCallback((count: number) => setHighlightCount(count), []);

  const areaNotes = (notes ?? []).filter((note) => note.folder.startsWith("30-Areas")).length;

  return (
    <>
      <ModeHeader mode="research" greeting="Research workspace online." />

      <div className="flex flex-col gap-4">
        <StatRow
          items={[
            { href: "/research", label: "papers", value: libraryCounts.papers },
            { href: "/research", label: "queued", value: libraryCounts.queued },
            { href: "/research", label: "reading", value: libraryCounts.reading },
            { href: "/research", label: "notes", value: areaNotes },
            { href: "/research", label: "highlights", value: highlightCount },
          ]}
        />

        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_340px]">
          <div className="flex min-w-0 flex-col gap-4">
            <LibraryQueue onCounts={handleLibraryCounts} />
          </div>

          <div className="flex min-w-0 flex-col gap-4">
            <HighlightsRecent onCount={handleHighlightCount} />
            <Panel label="ASK.VAULT">
              <p className="mb-3 text-[13px] leading-relaxed text-ink-muted">
                Ask Argus anything about your reading queue — every answer cites the passage it came from.
              </p>
              <Link
                href="/chat"
                className="inline-block border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi"
              >
                open chat →
              </Link>
            </Panel>
          </div>
        </div>
      </div>
    </>
  );
}
