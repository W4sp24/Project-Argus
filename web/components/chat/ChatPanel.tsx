"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import Button from "@/components/ui/Button";
import { fetcher } from "@/lib/api";
import { useChat, type ChatMessage } from "@/lib/chat";
import CitationChips from "@/components/chat/CitationChips";
import Markdown from "@/components/chat/Markdown";
import ToolTrace from "@/components/chat/ToolTrace";
import { stripCitationMarkers } from "@/lib/citations";
import { useSelectedModel } from "@/lib/models";

const EXAMPLES = [
  "What did I write about algorithms?",
  "Summarize my recent daily notes.",
  "What's in my inbox folder?",
];

/** Small accent circle — the assistant's avatar (§7). */
function Orb({ size = "h-6 w-6" }: { size?: string }) {
  return (
    <span
      aria-hidden
      className={`flex shrink-0 items-center justify-center rounded-full border border-[var(--ac)] ${size}`}
    >
      <span className="h-[35%] w-[35%] rounded-full bg-[var(--ac)]" />
    </span>
  );
}

/** How a turn ended, when it did not end well. The old client overwrote the
 *  answer with "Something went wrong: …", which also threw away whatever the
 *  agent had already said; the partial text now stays and this sits under it. */
function StatusLine({ message }: { message: ChatMessage }) {
  if (message.status === "error") {
    return (
      <p className="flex items-start gap-1.5 border border-danger bg-void px-3 py-1.5 font-mono text-meta text-danger">
        <span aria-hidden="true">✕</span>
        <span className="min-w-0 flex-1">{message.error ?? "something went wrong"}</span>
      </p>
    );
  }
  if (message.status === "stopped") {
    return <p className="font-mono text-meta text-ink-faint">■ stopped</p>;
  }
  return null;
}

/**
 * Shared chat surface. `dock` = compact bubbles (drawer); `full` = /chat
 * standard-chatbot layout: assistant orb + name row + unboxed prose, user
 * messages as right-aligned tinted bubbles, input pinned at the bottom.
 */
export default function ChatPanel({
  variant,
  suggestions,
  placeholder,
}: {
  variant: "dock" | "full";
  /** Empty-state prompt buttons. The fullscreen surface falls back to the
   *  generic EXAMPLES; the drawer shows none unless a caller supplies its own,
   *  which is how the Course Hub keeps its course-specific prompts. */
  suggestions?: string[];
  placeholder?: string;
}) {
  const { data: vault } = useSWR<{ name: string }>("/api/vault", fetcher);
  const { messages, busy, offline, send } = useChat();
  const model = useSelectedModel();
  const [input, setInput] = useState("");
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // §7 scroll fix: set scrollTop on the container instead of scrollIntoView
    // (scrollIntoView can scroll ancestor containers / the page itself).
    const container = scrollRef.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [messages]);

  const compact = variant === "dock";
  const vaultName = vault?.name ?? "vault";
  const prompts = suggestions ?? (compact ? [] : EXAMPLES);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div
        ref={scrollRef}
        className={`min-h-0 flex-1 overflow-y-auto ${compact ? "space-y-3 pr-1" : "space-y-5 py-4"}`}
      >
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-4 py-4">
            {!compact && <Orb size="h-10 w-10" />}
            <p className={`text-center text-ink-muted ${compact ? "text-xs" : "text-sm"}`}>
              ask your vault — every answer cites the note it came from.
            </p>
            {prompts.length > 0 && (
              <div className="flex flex-wrap justify-center gap-2">
                {prompts.map((example) => (
                  <button
                    key={example}
                    type="button"
                    onClick={() => send(example)}
                    className="border border-line bg-panel px-3.5 py-2 text-body text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
                  >
                    {example}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {messages.map((message) =>
          message.role === "user" ? (
            <div key={message.key} className="animate-msg-in flex justify-end">
              <div
                className={`max-w-[85%] border border-lineHi bg-[var(--ac-bg)] px-3.5 py-2.5 leading-relaxed text-ink ${
                  compact ? "text-body" : "text-lead"
                }`}
              >
                <span className="whitespace-pre-wrap">{message.text}</span>
              </div>
            </div>
          ) : compact ? (
            <div key={message.key} className="animate-msg-in flex justify-start">
              <div className="min-w-0 max-w-[85%] flex-1 space-y-2">
                <ToolTrace
                  steps={message.steps}
                  status={message.status}
                  startedAt={message.startedAt}
                  endedAt={message.endedAt}
                />
                {message.text && (
                  <div className="border border-line bg-void px-3.5 py-2.5 text-body leading-relaxed text-ink-muted">
                    <Markdown text={stripCitationMarkers(message.text)} />
                    <CitationChips steps={message.steps} vaultName={vaultName} />
                  </div>
                )}
                <StatusLine message={message} />
              </div>
            </div>
          ) : (
            <div key={message.key} className="animate-msg-in flex gap-3">
              <Orb />
              <div className="min-w-0 flex-1 space-y-2">
                <p className="font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">
                  ARGUS · {model}
                  {message.local && <span className="ml-2 normal-case">· not saved to this thread</span>}
                </p>
                <ToolTrace
                  steps={message.steps}
                  status={message.status}
                  startedAt={message.startedAt}
                  endedAt={message.endedAt}
                />
                {message.text && (
                  <>
                    <Markdown
                      text={stripCitationMarkers(message.text)}
                      className="text-lead leading-[1.7] text-ink"
                    />
                    <CitationChips steps={message.steps} vaultName={vaultName} />
                  </>
                )}
                <StatusLine message={message} />
              </div>
            </div>
          ),
        )}
        {offline && (
          <p className="text-center text-xs text-danger">
            Can’t reach Argus — is the backend running on :8000?
          </p>
        )}
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          send(input);
          setInput("");
        }}
        className={compact ? "pt-2" : "border-t border-line pt-3"}
      >
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={busy ? "Argus is answering…" : (placeholder ?? "Ask your vault")}
            aria-label="Ask your vault"
            disabled={busy}
            className="min-w-0 flex-1 border border-line bg-sunken px-3 py-2 text-body placeholder:text-ink-faint focus:border-lineHi disabled:opacity-50"
          />
          <Button
            type="submit"
            size="md"
            variant="primary"
            aria-label="Send"
            disabled={busy || !input.trim()}
            className="shrink-0"
          >
            SEND
          </Button>
        </div>
      </form>
    </div>
  );
}
