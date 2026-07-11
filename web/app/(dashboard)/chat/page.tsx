"use client";

import { useEffect, useRef, useState } from "react";
import useSWR from "swr";
import { fetcher } from "@/lib/api";

interface ChatMessage {
  role: "user" | "friday";
  text: string;
  pending?: boolean;
}

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
        className="mx-0.5 inline-block rounded-md bg-primary/20 px-1.5 py-0.5 font-mono text-[11px] text-primary-soft transition-colors hover:bg-primary/35"
        title={`Open ${path} in Obsidian`}
      >
        {path.split("/").pop()}
      </a>
    );
  });
}

export default function ChatPage() {
  const { data: vault } = useSWR<{ name: string }>("/api/vault", fetcher);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [offline, setOffline] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => () => socketRef.current?.close(), []);

  function send(text: string) {
    const message = text.trim();
    if (!message || busy) return;
    setBusy(true);
    setOffline(false);
    setInput("");
    setMessages((prev) => [
      ...prev,
      { role: "user", text: message },
      { role: "friday", text: "", pending: true },
    ]);

    const ws = new WebSocket(`ws://${window.location.hostname}:8000/ws/chat`);
    socketRef.current = ws;
    ws.onopen = () => ws.send(JSON.stringify({ message }));
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
            role: "friday",
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
      setMessages((prev) => prev.slice(0, -1));
    };
  }

  return (
    <div className="flex h-[calc(100dvh-8rem)] flex-col md:h-[calc(100dvh-4rem)]">
      <header className="mb-4 animate-rise">
        <p className="eyebrow mb-2">{`// CHAT`}</p>
        <h1 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">
          Ask your <span className="gradient-text">second brain</span>
        </h1>
      </header>

      <div className="glass flex min-h-0 flex-1 flex-col">
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
          {messages.length === 0 && (
            <div className="flex h-full flex-col items-center justify-center gap-4">
              <p className="text-sm text-ink-muted">
                Every answer cites the note it came from — click a citation to open it in
                Obsidian.
              </p>
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
              {offline && (
                <p className="text-sm text-accent">
                  Can&apos;t reach FRIDAY — start the backend with{" "}
                  <span className="font-mono text-xs">uvicorn backend.main:app --port 8000</span>
                </p>
              )}
            </div>
          )}

          {messages.map((message, i) => (
            <div
              key={i}
              className={`flex ${message.role === "user" ? "justify-end" : "justify-start"}`}
            >
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed ${
                  message.role === "user"
                    ? "bg-gradient-to-r from-primary/30 to-accent/20 text-ink"
                    : "border border-white/10 bg-white/[0.04] text-ink-muted"
                }`}
              >
                {message.pending ? (
                  <span className="flex gap-1.5 py-1" aria-label="FRIDAY is thinking">
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
          {offline && messages.length > 0 && (
            <p className="text-center text-sm text-accent">
              Connection lost — is the backend running on :8000?
            </p>
          )}
          <div ref={bottomRef} />
        </div>

        <form
          onSubmit={(event) => {
            event.preventDefault();
            send(input);
          }}
          className="border-t border-white/10 p-3"
        >
          <div className="flex gap-2">
            <input
              value={input}
              onChange={(event) => setInput(event.target.value)}
              placeholder={busy ? "FRIDAY is answering…" : "Ask about anything in your vault"}
              disabled={busy}
              className="min-w-0 flex-1 rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm placeholder:text-ink-faint focus:border-primary-soft/50 focus:outline-none disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="shrink-0 rounded-xl bg-gradient-to-r from-primary to-accent px-5 py-2.5 font-display text-sm font-medium text-white transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              Send
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
