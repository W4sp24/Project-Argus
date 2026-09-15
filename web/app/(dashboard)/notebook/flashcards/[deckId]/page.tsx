"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useToast } from "@/components/Toast";
import DeckEditor from "@/components/notebook/flashcards/DeckEditor";
import ImportDialog from "@/components/notebook/flashcards/ImportDialog";
import NotebookPanel from "@/components/notebook/NotebookPanel";
import ProgressRing from "@/components/notebook/ProgressRing";
import { exportDeck, updateDeck, useDeck, useDueCards, useMatchBest } from "@/lib/api";

/** One study activity, and what it does to the schedule. Saying so is the point. */
const ACTIVITIES = [
  {
    slug: "review",
    label: "Review",
    blurb: "Spaced repetition. Schedules your next visit.",
    schedules: true,
  },
  {
    slug: "learn",
    label: "Learn",
    blurb: "Choice first, then typing. Schedules too.",
    schedules: true,
  },
  {
    slug: "cards",
    label: "Browse",
    blurb: "Cram freely. Changes nothing.",
    schedules: false,
  },
  {
    slug: "match",
    label: "Match",
    blurb: "A game against the clock. Changes nothing.",
    schedules: false,
  },
] as const;

const META_ACTION =
  "rounded-ctl border border-nb-line px-3.5 py-2 text-center text-ctl text-nb-ink transition-colors hover:border-nb-lineHi disabled:opacity-70";

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
  const { data: matchBest } = useMatchBest(Number.isFinite(deckId) ? deckId : null);
  const { show } = useToast();
  const [importing, setImporting] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState("");
  const [course, setCourse] = useState("");
  const [saving, setSaving] = useState(false);

  /** Open the editor seeded with what the server currently holds. */
  function startEditing(current: { title: string; course: string }) {
    setName(current.title);
    setCourse(current.course);
    setEditing(true);
  }

  /**
   * Rename the deck, and file it under a course.
   *
   * The course half is not a nicety. Export writes to the course's
   * `flashcards.md`, so a deck without one cannot export -- and the disabled
   * button has been telling people to "set a course on this deck" since it
   * shipped, with nothing anywhere in the app able to do it.
   */
  async function saveMeta(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim() || saving) return;
    setSaving(true);
    try {
      await updateDeck(deckId, { title: name.trim(), course: course.trim() });
      setEditing(false);
      await refresh();
    } catch (error) {
      show(`could not save: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    } finally {
      setSaving(false);
    }
  }

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
    return <p className="text-body text-nb-faint">Loading deck…</p>;
  }

  const dueCount = due?.length ?? 0;

  return (
    <>
      <Link
        href="/notebook/flashcards"
        className="mb-3.5 inline-block text-ctl text-nb-faint transition-colors hover:text-nb-ink"
      >
        ← All decks
      </Link>

      <NotebookPanel className="mb-5">
        <div className="flex flex-wrap items-center gap-6">
          <ProgressRing value={deck.mastered} total={deck.cards} />

          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-2.5">
              {deck.course && (
                <span className="font-mono text-meta font-semibold tracking-[0.06em] text-[var(--ac)]">
                  {deck.course}
                </span>
              )}
              <h1 className="min-w-0 font-body text-title font-semibold text-nb-ink">
                {deck.title}
              </h1>
              {/* A constant name, not `Rename ${deck.title}`. An accessible name
                  that interpolates a deck's title collides with whatever else is
                  on screen -- a deck called "Imported deck" made this button
                  answer to "IMPORT" alongside the Import button beside it. There
                  is one deck on this page, so the title adds nothing anyway. */}
              <button
                type="button"
                aria-label="Rename this deck"
                onClick={() => startEditing(deck)}
                className="text-ctl text-nb-faint transition-colors hover:text-nb-ink"
              >
                Rename
              </button>
            </div>

            <p className="mt-1.5 text-body text-nb-body">
              {[
                `${deck.cards} card${deck.cards === 1 ? "" : "s"}`,
                `${dueCount} due`,
                deck.description,
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>

            {matchBest?.best_ms != null && (
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <span className="rounded-full border border-nb-line px-3 py-1 text-label font-semibold text-nb-body">
                  Match best {(matchBest.best_ms / 1000).toFixed(1)}s
                </span>
              </div>
            )}
          </div>

          <div className="flex shrink-0 flex-col gap-2">
            <button type="button" onClick={() => setImporting(true)} className={META_ACTION}>
              Import
            </button>
            <button
              type="button"
              disabled={exporting || deck.cards === 0 || !deck.course}
              onClick={() => void runExport()}
              title={
                deck.course
                  ? "Write these cards to the course's flashcards.md"
                  : "Set a course on this deck to export it"
              }
              className={META_ACTION}
            >
              {exporting ? "Exporting…" : "Export"}
            </button>
          </div>
        </div>

        {editing && (
          <form
            onSubmit={saveMeta}
            className="mt-5 flex flex-wrap items-end gap-3 border-t border-nb-line pt-5"
          >
            <label className="flex min-w-0 flex-1 flex-col gap-1.5">
              <span className="text-ctl text-nb-body">Deck name</span>
              <input
                autoFocus
                value={name}
                onChange={(event) => setName(event.target.value)}
                aria-label="Deck name"
                className="min-h-10 rounded-ctl border border-nb-line bg-nb-void px-3 py-2.5 text-body text-nb-ink focus:border-nb-lineHi"
              />
            </label>
            <label className="flex w-40 flex-col gap-1.5">
              <span className="text-ctl text-nb-body">Deck course</span>
              <input
                value={course}
                onChange={(event) => setCourse(event.target.value)}
                aria-label="Deck course"
                placeholder="CS201"
                className="min-h-10 rounded-ctl border border-nb-line bg-nb-void px-3 py-2.5 font-mono text-label uppercase text-nb-ink placeholder:text-nb-faint focus:border-nb-lineHi"
              />
            </label>
            <button
              type="submit"
              disabled={!name.trim() || saving}
              className="min-h-10 rounded-ctl bg-[var(--ac)] px-4 py-2.5 text-ctl font-semibold text-nb-onAc transition-opacity hover:opacity-90 disabled:opacity-70"
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button type="button" onClick={() => setEditing(false)} className={META_ACTION}>
              Cancel
            </button>
            <p className="w-full text-label text-nb-faint">
              A course is where Export writes this deck&apos;s{" "}
              <code className="font-mono">flashcards.md</code>. Leave it blank for a deck that
              belongs to no course.
            </p>
          </form>
        )}

        {deck.cards === 0 ? (
          <p className="mt-5 border-t border-nb-line pt-5 text-body text-nb-faint">
            Add a card to start studying.
          </p>
        ) : (
          <>
            <ul className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
              {ACTIVITIES.map((activity) => {
                const primary = activity.slug === "review" && dueCount > 0;
                return (
                  <li key={activity.slug} className="flex">
                    <Link
                      href={`/notebook/flashcards/${deck.id}/${activity.slug}`}
                      className={`flex-1 rounded-tile border p-4 transition-colors ${
                        primary
                          ? "border-[var(--ac)] bg-nb-acBg"
                          : "border-nb-line bg-nb-raised hover:border-nb-lineHi"
                      }`}
                    >
                      <span
                        className={`block text-ctl font-semibold ${
                          primary ? "text-[var(--ac)]" : "text-nb-ink"
                        }`}
                      >
                        {activity.label}
                        {primary ? ` · ${dueCount} due` : ""}
                      </span>
                      <span
                        className={`mt-1 block text-label ${
                          activity.schedules ? "text-nb-body" : "text-nb-faint"
                        }`}
                      >
                        {activity.blurb}
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
            <p className="mt-3 text-label text-nb-faint">
              Only Review and Learn change when a card comes back.
            </p>
          </>
        )}
      </NotebookPanel>

      <DeckEditor deck={deck} onChanged={() => void refresh()} />

      {importing && (
        <ImportDialog
          deckId={deck.id}
          deckTitle={deck.title}
          course={deck.course || undefined}
          onClose={() => setImporting(false)}
          onImported={() => void refresh()}
        />
      )}
    </>
  );
}
