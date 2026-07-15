"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { useTypewriter } from "@/lib/useTypewriter";

const SUGGESTIONS = [
  "Summarize this week's material",
  "What's likely to be on the exam?",
  "Explain the hardest concept so far",
];

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  citations?: string[];
}

function cannedAnswer(code: string, question: string): { text: string; citations: string[] } {
  return {
    text:
      `[PREVIEW] This course hub isn't wired to the RAG pipeline yet — a real answer would ` +
      `ground "${question}" in your selected ${code} sources. Toggle sources on the left and ` +
      `Phase F swaps this canned reply for a live, cited one.`,
    citations: [`⌗ ${code}-notes.md`, `⌗ syllabus.pdf p.2`],
  };
}

/**
 * Course Hub center pane — scoped chat [PREVIEW] (§4 Course Hub, §8
 * flags.courseHub). Per-course local state only, entirely separate from the
 * global `ChatProvider`/`useChat` (the spec is explicit: "Conversation state
 * is per-course, separate from the global chat"). Never calls `fetch(` — the
 * reply is a canned string typed out with the shared `useTypewriter` hook.
 */
export function CourseChat({ code }: { code: string }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [pendingReply, setPendingReply] = useState<{ text: string; citations: string[] } | null>(null);
  const { output, done } = useTypewriter(pendingReply?.text ?? "", 24);

  function send(question: string) {
    const text = question.trim();
    if (!text) return;
    setMessages((prev) => [...prev, { role: "user", text }]);
    setInput("");
    setPendingReply(cannedAnswer(code, text));
  }

  // Commit the typed-out reply into the message list once typing finishes —
  // an effect, not a render-time call, so setState never fires during render.
  useEffect(() => {
    if (!done || !pendingReply) return;
    const reply = pendingReply;
    setMessages((prev) => [...prev, { role: "assistant", text: reply.text, citations: reply.citations }]);
    setPendingReply(null);
  }, [done, pendingReply]);

  return (
    <Panel label={`ARGUS.CHAT · ${code}`} preview className="flex h-full flex-col">
      <div className="min-h-0 flex-1 overflow-y-auto">
        {messages.length === 0 && !pendingReply ? (
          <div className="flex h-full flex-col items-center justify-center gap-4 py-10 text-center">
            <p className="font-mono text-sm text-ink-muted">{`ask ${code}`}</p>
            <div className="flex flex-col gap-2">
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  onClick={() => send(suggestion)}
                  className="border border-line px-3 py-1.5 text-[12.5px] text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
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
                  className={`inline-block max-w-[85%] border px-3 py-2 text-[13.5px] ${
                    message.role === "user" ? "border-lineHi bg-[var(--ac-bg)] text-ink" : "border-line bg-void text-ink"
                  }`}
                >
                  {message.text}
                </div>
                {message.citations && (
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {message.citations.map((citation) => (
                      <span key={citation} className="font-mono text-[10px] text-[var(--ac)]">
                        {citation}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            ))}
            {pendingReply && (
              <div className="self-start">
                <div className="inline-block max-w-[85%] border border-line bg-void px-3 py-2 text-[13.5px] text-ink">
                  {output}
                  {!done && <span className="animate-blink text-[var(--ac)]">▊</span>}
                </div>
              </div>
            )}
          </div>
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
          placeholder={`ask ${code} · grounded in 0 selected sources`}
          className="min-w-0 flex-1 border border-line bg-sunken px-3 py-2 text-[13px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
        />
        <button
          type="submit"
          disabled={!input.trim()}
          className="shrink-0 border border-line px-3 py-2 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
        >
          SEND
        </button>
      </form>
    </Panel>
  );
}

const GENERATED_MOCK = [
  { label: "Study guide — midterm scope", date: "2026-06-30" },
  { label: "Flashcard deck — chapter 4", date: "2026-06-22" },
];

/**
 * Course Hub right rail STUDIO [PREVIEW] (§4 Course Hub, §8 flags.courseHub):
 * generation actions are mock — every button toasts `generation :: preview`.
 * "Practice exam" additionally routes to the real `/study/exam` page (per
 * spec) since a practice exam workspace does exist there for real, even
 * though nothing new was actually generated from these sources.
 */
export function CourseStudio({ code }: { code: string }) {
  const { show } = useToast();
  const router = useRouter();

  function generate(kind: string, route?: string) {
    show("generation :: preview");
    if (route) router.push(route);
  }

  return (
    <Panel label="STUDIO" preview>
      <div className="flex flex-col gap-2">
        <button
          onClick={() => generate("study guide")}
          className="border border-line px-3 py-2 text-left font-mono text-[11px] uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
        >
          study guide
        </button>
        <button
          onClick={() => generate("flashcard deck")}
          className="border border-line px-3 py-2 text-left font-mono text-[11px] uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
        >
          flashcard deck
        </button>
        <button
          onClick={() => generate("practice exam", "/study/exam")}
          className="border border-line px-3 py-2 text-left font-mono text-[11px] uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
        >
          practice exam
        </button>
        <button
          onClick={() => generate("weak topics")}
          className="border border-line px-3 py-2 text-left font-mono text-[11px] uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
        >
          weak topics
        </button>
      </div>

      <div className="mt-4 border-t border-line pt-3">
        <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.16em] text-ink-faint">generated</p>
        <ul className="space-y-1.5">
          {GENERATED_MOCK.map((item) => (
            <li key={item.label} className="flex items-center justify-between gap-2 text-[12px]">
              <span className="min-w-0 truncate text-ink-muted">{item.label}</span>
              <span className="shrink-0 font-mono text-[10px] text-ink-faint">{item.date}</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="mt-3 font-mono text-[10px] text-ink-faint">{`for ${code} · preview only`}</p>
    </Panel>
  );
}
