"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import DeckEditor from "@/components/notebook/flashcards/DeckEditor";
import ImportDialog from "@/components/notebook/flashcards/ImportDialog";
import NotebookStatusLine from "@/components/notebook/NotebookStatusLine";
import { exportDeck, useDeck, useDueCards } from "@/lib/api";

/** One study activity, and what it does to the schedule. Saying so is the point. */
const ACTIVITIES = [
  {
    slug: "review",
    label: "REVIEW",
    blurb: "Spaced repetition. Grades what you know and schedules the next visit.",
    schedules: true,
  },
  {
    slug: "cards",
    label: "FLASHCARDS",
    blurb: "Flip through the deck. Sort into piles without touching your schedule.",
    schedules: false,
  },
  {
    slug: "learn",
    label: "LEARN",
    blurb: "Multiple choice, then typing, escalating as you get things right.",
    schedules: true,
  },
  {
    slug: "match",
    label: "MATCH",
    blurb: "Pair terms against the clock. A game — it changes nothing.",
    schedules: false,
  },
] as const;

/**
 * /notebook/flashcards/[deckId] — one deck: its cards, and the four ways to
 * study them.
 *
 * Each activity says whether it touches the schedule, because that is the
 * distinction the whole design turns on: cramming a deck before a lecture must
 * not rewrite a schedule built over weeks.
 */
export default function DeckPage() {
  const params = useParams<{ deckId: string }>();
  const deckId = Number(params.deckId);
  const { data: deck, mutate: refresh } = useDeck(Number.isFinite(deckId) ? deckId : null);
  const { data: due } = useDueCards(Number.isFinite(deckId) ? deckId : null);
  const { show } = useToast();
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);

  async function runExport() {
    setExporting(true);
    try {
      const { path } = await exportDeck(deckId);
      show(`exported :: ${path}`);
    } catch (error) {
      show(`export failed: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    } finally {
      setExporting(false);
    }
  }

  if (!deck) {
    return (
      <>
        <NotebookStatusLine title="Deck" />
        <p className="text-body text-ink-faint">Loading deck…</p>
      </>
    );
  }

  const dueCount = due?.length ?? 0;

  return (
    <>
      <NotebookStatusLine title={deck.title} />

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <Link
          href="/notebook/flashcards"
          className="font-mono text-label uppercase tracking-[0.12em] text-ink-faint transition-colors hover:text-ink"
        >
          ← all decks
        </Link>
        <span className="font-mono text-meta text-ink-faint">
          {deck.cards} card{deck.cards === 1 ? "" : "s"}
          {deck.course ? ` · ${deck.course}` : ""} · {dueCount} due
        </span>
        <div className="ml-auto flex gap-2">
          <Button variant="quiet" onClick={() => setImporting(true)}>
            IMPORT
          </Button>
          <Button
            variant="quiet"
            disabled={exporting || deck.cards === 0 || !deck.course}
            onClick={() => void runExport()}
            title={
              deck.course
                ? "Write these cards to the course's flashcards.md"
                : "Set a course on this deck to export it"
            }
          >
            {exporting ? "EXPORTING…" : "EXPORT"}
          </Button>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-shell">
        <DeckEditor deck={deck} onChanged={() => void refresh()} />

        <Panel label="STUDY">
          {deck.cards === 0 ? (
            <p className="text-body text-ink-faint">Add a card to start studying.</p>
          ) : (
            <ul className="space-y-2">
              {ACTIVITIES.map((activity) => (
                <li key={activity.slug}>
                  <Link
                    href={`/notebook/flashcards/${deck.id}/${activity.slug}`}
                    className="block border border-line px-3 py-2 transition-colors hover:border-lineHi"
                  >
                    <span className="flex items-center justify-between gap-2">
                      <span className="font-mono text-label uppercase tracking-wide text-ink">
                        {activity.label}
                      </span>
                      {activity.slug === "review" && dueCount > 0 && (
                        <span className="font-mono text-meta text-[var(--ac)]">
                          {dueCount} due
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block text-label text-ink-faint">{activity.blurb}</span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
          <p className="mt-3 border-t border-line pt-2 font-mono text-micro text-ink-faint">
            Only REVIEW and LEARN change when a card comes back.
          </p>
        </Panel>
      </div>

      {importing && (
        <ImportDialog
          deckId={deck.id}
          course={deck.course || undefined}
          onClose={() => setImporting(false)}
          onImported={() => void refresh()}
        />
      )}
    </>
  );
}
