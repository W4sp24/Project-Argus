"use client";

import { useState } from "react";
import Link from "next/link";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import ChatPanel from "@/components/chat/ChatPanel";
import {
  generateFlashcardDeck,
  mutateJSON,
  useCourseSources,
  useFlashcardDecks,
  useStudyExams,
  useVault,
} from "@/lib/api";
import { ChatProvider } from "@/lib/chat";
import { obsidianUri } from "@/lib/citations";
import { useCourseSelection } from "@/lib/courseSelection";
import { selectedModel, useSelectedModel } from "@/lib/models";
import { useWeakTopics } from "@/lib/useStudySignals";

const SUGGESTIONS = [
  "Summarize this week's material",
  "What's likely to be on the exam?",
  "Explain the hardest concept so far",
];

/**
 * Course Hub center pane — the shared chat surface, scoped to one course.
 *
 * This used to be a second, near-complete copy of `lib/chat.tsx`'s WebSocket
 * client: its own frame handling, its own `ChatMessage` type saying
 * "assistant" where the original said "argus", its own error path popping two
 * pending bubbles where the original popped one. Every protocol change needed
 * two edits, and the two had already drifted apart.
 *
 * A nested `ChatProvider` shadows the app-wide one from the dashboard layout,
 * so the course still keeps its own thread — "separate from the global chat",
 * as the original spec asked — while there is now one implementation of the
 * protocol instead of two, and every later chat fix lands on both surfaces at
 * once.
 *
 * `course` rides on every outbound frame, where `build_vault_tools`
 * (backend/agent/runtime.py) turns it into a *forced* `search_vault` filter
 * rather than leaving the scope to the model's discretion — the whole point
 * of asking from inside a course's hub. The SOURCES rail's ticks ride along
 * beside it and narrow that filter further, so "what does lecture 3 say"
 * asked with only lecture 3 selected reads only lecture 3.
 *
 * The line under the input states the scope. A user who has narrowed to two
 * files and forgotten needs to be able to see why the answer looks thin,
 * without opening the rail to count checkboxes.
 */
export function CourseChat({ code }: { code: string }) {
  const model = useSelectedModel();
  const { paths, available } = useCourseSelection();
  const scoped = paths.length < available.length;
  return (
    <ChatProvider course={code} sources={paths}>
      <Panel label={`ARGUS.CHAT · ${code}`} className="flex h-full flex-col">
        <ChatPanel
          variant="dock"
          suggestions={SUGGESTIONS}
          placeholder={
            paths.length === 0
              ? `ask ${code} · no sources selected`
              : `ask ${code} · grounded in ${scoped ? `${paths.length} selected source${paths.length === 1 ? "" : "s"}` : "its materials & notes"}`
          }
        />
        <p className="mt-2 font-mono text-meta text-ink-faint">
          model :: {model} · sources :: {paths.length}/{available.length}
        </p>
      </Panel>
    </ChatProvider>
  );
}

/**
 * One STUDIO button, with an honest running state.
 *
 * These used to change their own label to "writing…" and then go silent for
 * however long a provider takes — minutes, for a guide over a whole course.
 * The blinking bar is the same idiom `IngestJobProgress` uses, and for the
 * same reason: it moves only while something is actually running, so a stall
 * looks like a stall rather than like progress.
 */
function StudioAction({
  label,
  running,
  runningLabel,
  disabled,
  onClick,
  note,
}: {
  label: string;
  running: boolean;
  runningLabel: string;
  disabled: boolean;
  onClick: () => void;
  note?: string;
}) {
  return (
    <div>
      <button
        onClick={onClick}
        disabled={disabled}
        aria-busy={running}
        className="w-full border border-line px-3 py-2 text-left font-mono text-label uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink disabled:cursor-not-allowed disabled:opacity-70"
      >
        {running ? `${runningLabel}…` : label}
      </button>
      {running && (
        <div className="mt-1 h-0.5 w-full bg-line" aria-hidden>
          <span className="block h-full w-1/3 animate-blink bg-[var(--ac)]" />
        </div>
      )}
      {note && !running && (
        <p className="mt-1 font-mono text-micro text-ink-faint">{note}</p>
      )}
    </div>
  );
}

/** An in-app route goes through `next/link`; an `obsidian://` target cannot. */
function LinkOrAnchor({
  href,
  external,
  className,
  children,
}: {
  href: string;
  external?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  if (external) {
    return (
      <a href={href} className={className}>
        {children}
      </a>
    );
  }
  return (
    <Link href={href} className={className}>
      {children}
    </Link>
  );
}

interface GeneratedItem {
  key: string;
  label: string;
  date: string;
  kind: "GUIDE" | "EXAM" | "DECK";
  href?: string;
  /** An obsidian:// target, which `next/link` must not try to route. */
  external?: boolean;
}

/**
 * Course Hub right rail STUDIO (§4 Course Hub) — generation actions now hit
 * the real endpoints `CoursesPanel` already uses (`/api/study/guide`,
 * `/api/study/exam`, `/api/flashcards/decks`), instead of every button just
 * toasting `generation :: preview`. "Generated" lists real artifacts:
 * exams (`GET /api/study/exams?course=`), decks (`useFlashcardDecks`), and
 * study guides — the latter read off `GET /api/study/courses/<code>/sources`
 * (the `study` zone), filtered to `guide-*` files so exam markdown (already
 * covered by the exams list) isn't double-counted.
 */
export function CourseStudio({ code }: { code: string }) {
  const { show } = useToast();
  const { data: exams, mutate: refreshExams } = useStudyExams(code);
  const { data: decks, mutate: refreshDecks } = useFlashcardDecks(code);
  const { data: sources, mutate: refreshSources } = useCourseSources(code);
  const { data: vault } = useVault();
  const { paths, available, refresh: refreshSelection } = useCourseSelection();
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const scoped = paths.length < available.length;
  // A guide or an exam built from nothing is not a request worth sending —
  // the backend refuses it, and disabling the button says so a round trip
  // earlier.
  const nothingSelected = paths.length === 0 && available.length > 0;

  const guides = (sources ?? []).filter(
    (source) => source.zone === "study" && /^guide-/.test(source.path.split("/").pop() ?? ""),
  );

  const generated: GeneratedItem[] = [
    ...(exams ?? []).map((exam) => ({
      key: `exam-${exam.id}`,
      label: exam.title,
      date: exam.created_at,
      kind: "EXAM" as const,
      // Carries its id. Every EXAM row used to point at the bare route, so
      // clicking "EXAM · Midterm review" opened whatever exam the page
      // happened to load rather than that one.
      href: `/study/exam?id=${exam.id}`,
    })),
    ...(decks ?? []).map((deck) => ({
      key: `deck-${deck.id}`,
      label: deck.title,
      date: deck.created_at,
      kind: "DECK" as const,
      href: `/study/flashcards?deck=${deck.id}`,
    })),
    // A guide that took minutes to write used to render as unclickable text,
    // with its path announced only in a toast that had since auto-dismissed.
    // It is a real file in the vault, so the obsidian link is the honest
    // destination -- there is no in-app reader for it.
    ...guides.map((guide) => ({
      key: guide.path,
      label: guide.title,
      date: guide.modified,
      kind: "GUIDE" as const,
      href: vault ? obsidianUri(vault.path, guide.path) : undefined,
      external: true,
    })),
  ].sort((a, b) => (a.date < b.date ? 1 : -1));

  const weakTopics = useWeakTopics().filter((topic) => topic.course === code);

  async function generateGuide() {
    setBusyAction("guide");
    show(`generating study guide for ${code} — this can take a few minutes…`);
    try {
      const payload = await mutateJSON<{ path: string }>("/api/study/guide", {
        course: code,
        model: selectedModel(),
        sources: paths,
      });
      show(`study guide written to ${payload.path}`);
      refreshSources();
      refreshSelection();
    } catch (error) {
      show(`study guide failed: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    } finally {
      setBusyAction(null);
    }
  }

  async function generateExam() {
    setBusyAction("exam");
    show(`generating practice exam for ${code} — this can take a few minutes…`);
    try {
      const payload = await mutateJSON<{ path: string; questions: number }>("/api/study/exam", {
        course: code,
        n: 10,
        model: selectedModel(),
        sources: paths,
      });
      show(`exam ready: ${payload.questions} cited questions → ${payload.path}`);
      refreshExams();
      refreshSources();
      refreshSelection();
    } catch (error) {
      show(`exam generation failed: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    } finally {
      setBusyAction(null);
    }
  }

  async function generateDeck() {
    setBusyAction("deck");
    try {
      const deck = await generateFlashcardDeck(code);
      show(`deck ready :: ${deck.course} — ${deck.cards} cards`);
      refreshDecks();
    } catch (error) {
      show(`deck generation failed: ${error instanceof Error ? error.message : "backend offline?"}`, {
        tone: "error",
      });
    } finally {
      setBusyAction(null);
    }
  }

  const scopeNote = scoped ? ` · ${paths.length} source${paths.length === 1 ? "" : "s"}` : "";

  return (
    <Panel label="STUDIO">
      <div className="flex flex-col gap-2">
        <StudioAction
          label={`study guide${scopeNote}`}
          running={busyAction === "guide"}
          runningLabel="writing the guide"
          disabled={busyAction !== null || nothingSelected}
          onClick={generateGuide}
        />
        <StudioAction
          label="flashcard deck"
          running={busyAction === "deck"}
          runningLabel="parsing flashcards.md"
          disabled={busyAction !== null}
          onClick={generateDeck}
          // Decks are parsed from the course's own flashcards.md, never from
          // the corpus (backend/features/flashcards/store.py), so the source
          // selection genuinely does not apply. Saying so beats printing a
          // count this button would not honour.
          note="reads flashcards.md · ignores the selection"
        />
        <StudioAction
          label={`practice exam${scopeNote}`}
          running={busyAction === "exam"}
          runningLabel="generating questions"
          disabled={busyAction !== null || nothingSelected}
          onClick={generateExam}
        />
      </div>

      {nothingSelected && (
        <p className="mt-2 font-mono text-meta text-warn">
          Nothing is selected — tick a source to generate from it.
        </p>
      )}

      <div className="mt-4 border-t border-line pt-3">
        <p className="mb-2 font-mono text-meta uppercase tracking-[0.16em] text-ink-faint">generated</p>
        {generated.length === 0 ? (
          <p className="text-label text-ink-faint">Nothing generated for {code} yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {generated.slice(0, 8).map((item) =>
              item.href ? (
                <li key={item.key}>
                  {/* `next/link` for an in-app route, so opening an exam no
                      longer costs a full page load that discards the course
                      chat thread; a plain anchor for `obsidian://`, which the
                      router must not try to handle. */}
                  <LinkOrAnchor
                    href={item.href}
                    external={item.external}
                    className="flex items-center justify-between gap-2 text-label transition-colors hover:text-[var(--ac)]"
                  >
                    <span className="min-w-0 truncate text-ink-muted">
                      {item.kind} · {item.label}
                    </span>
                    <span className="shrink-0 font-mono text-meta text-ink-faint">
                      {item.date.slice(0, 10)}
                    </span>
                  </LinkOrAnchor>
                </li>
              ) : (
                <li key={item.key} className="flex items-center justify-between gap-2 text-label">
                  <span className="min-w-0 truncate text-ink-muted">
                    {item.kind} · {item.label}
                  </span>
                  <span className="shrink-0 font-mono text-meta text-ink-faint">
                    {item.date.slice(0, 10)}
                  </span>
                </li>
              ),
            )}
          </ul>
        )}
      </div>

      {weakTopics.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="mb-2 font-mono text-meta uppercase tracking-[0.16em] text-ink-faint">weak topics</p>
          <div className="flex flex-wrap gap-1.5">
            {weakTopics.slice(0, 8).map((topic) => (
              <span key={topic.topic} className="border border-line px-1.5 py-0.5 font-mono text-meta text-ink-muted">
                {topic.topic}
              </span>
            ))}
          </div>
        </div>
      )}
    </Panel>
  );
}
