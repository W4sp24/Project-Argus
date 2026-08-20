"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import ChatPanel from "@/components/chat/ChatPanel";
import {
  generateFlashcardDeck,
  mutateJSON,
  useCourseSources,
  useFlashcardDecks,
  useStudyExams,
} from "@/lib/api";
import { ChatProvider } from "@/lib/chat";
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
 * of asking from inside a course's hub.
 */
export function CourseChat({ code }: { code: string }) {
  const model = useSelectedModel();
  return (
    <ChatProvider course={code}>
      <Panel label={`ARGUS.CHAT · ${code}`} className="flex h-full flex-col">
        <ChatPanel
          variant="dock"
          suggestions={SUGGESTIONS}
          placeholder={`ask ${code} · grounded in its materials & notes`}
        />
        <p className="mt-2 font-mono text-meta text-ink-faint">model :: {model}</p>
      </Panel>
    </ChatProvider>
  );
}

interface GeneratedItem {
  key: string;
  label: string;
  date: string;
  kind: "GUIDE" | "EXAM" | "DECK";
  href?: string;
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
  const [busyAction, setBusyAction] = useState<string | null>(null);

  const guides = (sources ?? []).filter(
    (source) => source.zone === "study" && /^guide-/.test(source.path.split("/").pop() ?? ""),
  );

  const generated: GeneratedItem[] = [
    ...(exams ?? []).map((exam) => ({
      key: `exam-${exam.id}`,
      label: exam.title,
      date: exam.created_at,
      kind: "EXAM" as const,
      href: "/study/exam",
    })),
    ...(decks ?? []).map((deck) => ({
      key: `deck-${deck.id}`,
      label: deck.title,
      date: deck.created_at,
      kind: "DECK" as const,
      href: "/study/flashcards",
    })),
    ...guides.map((guide) => ({
      key: guide.path,
      label: guide.title,
      date: guide.modified,
      kind: "GUIDE" as const,
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
      });
      show(`study guide written to ${payload.path}`);
      refreshSources();
    } catch (error) {
      show(`study guide failed: ${error instanceof Error ? error.message : "backend offline?"}`);
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
      });
      show(`exam ready: ${payload.questions} cited questions → ${payload.path}`);
      refreshExams();
      refreshSources();
    } catch (error) {
      show(`exam generation failed: ${error instanceof Error ? error.message : "backend offline?"}`);
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
      show(`deck generation failed: ${error instanceof Error ? error.message : "backend offline?"}`);
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <Panel label="STUDIO">
      <div className="flex flex-col gap-2">
        <button
          onClick={generateGuide}
          disabled={busyAction !== null}
          className="border border-line px-3 py-2 text-left font-mono text-label uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink disabled:opacity-40"
        >
          {busyAction === "guide" ? "writing…" : "study guide"}
        </button>
        <button
          onClick={generateDeck}
          disabled={busyAction !== null}
          className="border border-line px-3 py-2 text-left font-mono text-label uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink disabled:opacity-40"
        >
          {busyAction === "deck" ? "parsing…" : "flashcard deck"}
        </button>
        <button
          onClick={generateExam}
          disabled={busyAction !== null}
          className="border border-line px-3 py-2 text-left font-mono text-label uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink disabled:opacity-40"
        >
          {busyAction === "exam" ? "generating…" : "practice exam"}
        </button>
      </div>

      <div className="mt-4 border-t border-line pt-3">
        <p className="mb-2 font-mono text-meta uppercase tracking-[0.16em] text-ink-faint">generated</p>
        {generated.length === 0 ? (
          <p className="text-label text-ink-faint">Nothing generated for {code} yet.</p>
        ) : (
          <ul className="space-y-1.5">
            {generated.slice(0, 8).map((item) =>
              item.href ? (
                <li key={item.key}>
                  <a
                    href={item.href}
                    className="flex items-center justify-between gap-2 text-label transition-colors hover:text-[var(--ac)]"
                  >
                    <span className="min-w-0 truncate text-ink-muted">
                      {item.kind} · {item.label}
                    </span>
                    <span className="shrink-0 font-mono text-meta text-ink-faint">
                      {item.date.slice(0, 10)}
                    </span>
                  </a>
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
