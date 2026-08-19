"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import Button from "@/components/ui/Button";
import { fetcher } from "@/lib/api";
import { useChat } from "@/lib/chat";
import { renderWithCitations } from "@/lib/citations";
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

function Pending() {
  return (
    <span className="font-mono text-label text-ink-muted" aria-label="Argus is thinking">
      processing_query
      <span className="animate-blink text-[var(--ac)]">▊</span>
    </span>
  );
}

/**
 * Shared chat surface. `dock` = compact bubbles (drawer); `full` = /chat
 * standard-chatbot layout: assistant orb + name row + unboxed prose, user
 * messages as right-aligned tinted bubbles, input pinned at the bottom.
 */
export default function ChatPanel({ variant }: { variant: "dock" | "full" }) {
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
            {!compact && (
              <div className="flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((example) => (
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
              <div className="max-w-[85%] border border-line bg-void px-3.5 py-2.5 text-body leading-relaxed text-ink-muted">
                {message.status === "streaming" && !message.text ? (
                  <Pending />
                ) : (
                  <span className="whitespace-pre-wrap">
                    {renderWithCitations(message.text, vaultName)}
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div key={message.key} className="animate-msg-in flex gap-3">
              <Orb />
              <div className="min-w-0 flex-1">
                <p className="mb-1 font-mono text-meta uppercase tracking-[0.14em] text-ink-faint">
                  ARGUS · {model}
                </p>
                {message.status === "streaming" && !message.text ? (
                  <Pending />
                ) : (
                  <p className="whitespace-pre-wrap font-body text-lead leading-[1.7] text-ink">
                    {renderWithCitations(message.text, vaultName)}
                  </p>
                )}
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
            placeholder={busy ? "Argus is answering…" : "Ask your vault"}
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
