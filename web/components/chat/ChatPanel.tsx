"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";
import { useChat } from "@/lib/chat";

const EXAMPLES = [
  "What did I write about algorithms?",
  "Summarize my recent daily notes.",
  "What's in my inbox folder?",
];

/** Split answer text into plain segments and [vault/path.md] citation chips. */
function renderWithCitations(text: string, vaultName: string) {
  const parts = text.split(/(\[[^\[\]\n]+?\.(?:md|pdf|pptx|docx)(?:\s+(?:p\.|slide\s)?\d+)?\])/g);
  return parts.map((part, i) => {
    const match = part.match(/^\[([^\[\]]+?)(?:\s+(?:p\.|slide\s)?\d+)?\]$/);
    if (!match) return <span key={i}>{part}</span>;
    const path = match[1];
    return (
      <a
        key={i}
        href={`obsidian://open?vault=${encodeURIComponent(vaultName)}&file=${encodeURIComponent(path)}`}
        className="animate-msg-in mx-0.5 inline-block rounded-md bg-primary/20 px-1.5 py-0.5 font-mono text-[11px] text-primary-soft transition-colors hover:bg-primary/35"
        title={`Open ${path} in Obsidian`}
      >
        {path.split("/").pop()}
      </a>
    );
  });
}

export default function ChatPanel({ variant }: { variant: "dock" | "full" }) {
  const { data: vault } = useSWR<{ name: string }>("/api/vault", fetcher);
  const { messages, busy, offline, send } = useChat();
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  const compact = variant === "dock";

  return (
    <div className={`flex min-h-0 flex-1 flex-col ${compact ? "" : "glass"}`}>
      <div className={`min-h-0 flex-1 space-y-3 overflow-y-auto ${compact ? "pr-1" : "p-5"}`}>
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-3 py-4">
            <p className={`text-center text-ink-muted ${compact ? "text-xs" : "text-sm"}`}>
              Every answer cites the note it came from.
            </p>
            {!compact && (
              <div className="flex flex-wrap justify-center gap-2">
                {EXAMPLES.map((example) => (
                  <button
                    key={example}
                    onClick={() => send(example)}
                    className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2 text-sm text-ink-muted transition-colors hover:border-primary-soft/30 hover:text-ink"
                  >
                    {example}
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {messages.map((message, i) => (
          <div
            key={i}
            className={`animate-msg-in flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`max-w-[85%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
                message.role === "user"
                  ? "bg-gradient-to-r from-primary/30 to-accent/20 text-ink"
                  : "border border-white/10 bg-white/[0.04] text-ink-muted"
              }`}
            >
              {message.pending ? (
                <span className="flex gap-1.5 py-1" aria-label="Argus is thinking">
                  {[0, 1, 2].map((dot) => (
                    <span
                      key={dot}
                      className="h-1.5 w-1.5 animate-breathe rounded-full bg-primary-soft"
                      style={{ animationDelay: `${dot * 0.2}s` }}
                    />
                  ))}
                </span>
              ) : (
                <span className="whitespace-pre-wrap">
                  {renderWithCitations(message.text, vault?.name ?? "vault")}
                </span>
              )}
            </div>
          </div>
        ))}
        {offline && (
          <p className="text-center text-xs text-accent">
            Can’t reach Argus — is the backend running on :8000?
          </p>
        )}
        <div ref={bottomRef} />
      </div>

      <form
        onSubmit={(event) => {
          event.preventDefault();
          send(input);
          setInput("");
        }}
        className={compact ? "pt-2" : "border-t border-white/10 p-3"}
      >
        <div className="flex gap-2">
          <input
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder={busy ? "Argus is answering…" : "Ask your vault"}
            disabled={busy}
            className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-3 py-2 text-sm placeholder:text-ink-faint focus:border-primary-soft/50 focus:outline-none disabled:opacity-50"
          />
          <button
            type="submit"
            disabled={busy || !input.trim()}
            className="shrink-0 rounded-xl bg-gradient-to-r from-primary to-accent px-4 py-2 font-display text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
          >
            Send
          </button>
        </div>
      </form>
    </div>
  );
}
