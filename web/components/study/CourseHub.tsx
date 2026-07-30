"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import {
  fetcher,
  generateFlashcardDeck,
  mutateJSON,
  useCourseSources,
  useFlashcardDecks,
  useStudyExams,
  wsBase,
} from "@/lib/api";
import { renderWithCitations } from "@/lib/citations";
import { selectedModel, useSelectedModel } from "@/lib/models";
import { useWeakTopics } from "@/lib/useStudySignals";

const SUGGESTIONS = [
  "Summarize this week's material",
  "What's likely to be on the exam?",
  "Explain the hardest concept so far",
];

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  pending?: boolean;
}

/**
 * Course Hub center pane — real RAG chat scoped to one course (§4 Course
 * Hub). Mirrors `lib/chat.tsx`'s `ChatProvider` (one `/ws/chat` socket per
 * message, same frame protocol) but stays entirely local state, per the
 * original spec: "Conversation state is per-course, separate from the
 * global chat." The one addition to the frame is `course`, which
 * `backend/features/chat/router.py` forwards to `ChatAgent.stream_chat` ->
 * `build_vault_tools` (backend/agent/runtime.py), where it FORCES
 * `search_vault`'s course filter rather than leaving it to the model's
 * discretion — the whole point of asking from inside a course's hub. This
 * used to be `cannedAnswer()`: a hardcoded "[PREVIEW] ... isn't wired to the
 * RAG pipeline yet" string with two fake citations, typed out with
 * `useTypewriter` to look live. No `fetch(` call existed anywhere in it.
 */
export function CourseChat({ code }: { code: string }) {
  const { data: vault } = useSWR<{ name: string }>("/api/vault", fetcher);
  const model = useSelectedModel();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => () => socketRef.current?.close(), []);
  useEffect(() => {
    const container = scrollRef.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [messages]);

  function send(text: string) {
    const question = text.trim();
    if (!question || busy) return;
    setBusy(true);
    setOffline(false);
    setInput("");
    setMessages((prev) => [
      ...prev,
      { role: "user", text: question },
      { role: "assistant", text: "", pending: true },
    ]);

    const ws = new WebSocket(`${wsBase()}/ws/chat`);
    socketRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ message: question, model: selectedModel(), course: code }));
    ws.onmessage = (event) => {
      const frame = JSON.parse(event.data);
      if (frame.type === "delta") {
        setMessages((prev) => {
          const next = [...prev];
          const last = next[next.length - 1];
          next[next.length - 1] = { ...last, text: last.text + frame.text, pending: false };
          return next;
        });
      } else if (frame.type === "done") {
        setBusy(false);
        ws.close();
      } else if (frame.type === "error") {
        setMessages((prev) => {
          const next = [...prev];
          next[next.length - 1] = {
            role: "assistant",
            text: `Something went wrong: ${frame.detail}`,
            pending: false,
          };
          return next;
        });
        setBusy(false);
        ws.close();
      }
    };
    ws.onerror = () => {
      setOffline(true);
      setBusy(false);
      setMessages((prev) => prev.slice(0, -2));
    };
  }

  const vaultName = vault?.name ?? "vault";

  return (
    <Panel label={`ARGUS.CHAT · ${code}`} className="flex h-full flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-4 py-10 text-center">
            <p className="font-mono text-sm text-ink-muted">{`ask ${code}`}</p>
            <div className="flex flex-col gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => send(suggestion)}
                  className="border border-line px-3 py-1.5 text-label text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
                >
                  {suggestion}
                </button>
              ))}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-3">
            {messages.map((message, i) => (
              <div key={i} className={message.role === "user" ? "self-end text-right" : "self-start"}>
                <div
                  className={`inline-block max-w-[85%] border px-3 py-2 text-body ${
                    message.role === "user"
                      ? "border-lineHi bg-[var(--ac-bg)] text-ink"
                      : "border-line bg-void text-ink"
                  }`}
                >
                  {message.pending && !message.text ? (
                    <span className="font-mono text-label text-ink-muted" aria-label="Argus is thinking">
                      processing_query
                      <span className="animate-blink text-[var(--ac)]">▊</span>
                    </span>
                  ) : (
                    <span className="whitespace-pre-wrap">{renderWithCitations(message.text, vaultName)}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
        {offline && (
          <p className="mt-2 text-center text-xs text-danger">
            Can’t reach Argus — is the backend running on :8000?
          </p>
        )}
      </div>
      <form
        onSubmit={(event) => {
          event.preventDefault();
          send(input);
        }}
        className="mt-3 flex gap-2 border-t border-line pt-3"
      >
        <input
          value={input}
          onChange={(event) => setInput(event.target.value)}
          placeholder={busy ? "Argus is answering…" : `ask ${code} · grounded in its materials & notes`}
          disabled={busy}
          className="min-w-0 flex-1 border border-line bg-sunken px-3 py-2 text-body placeholder:text-ink-faint focus:border-lineHi focus:outline-none disabled:opacity-50"
        />
        <button
          type="submit"
          disabled={busy || !input.trim()}
          className="shrink-0 border border-line px-3 py-2 font-mono text-label uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
        >
          SEND
        </button>
      </form>
      <p className="mt-2 font-mono text-meta text-ink-faint">model :: {model}</p>
    </Panel>
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
