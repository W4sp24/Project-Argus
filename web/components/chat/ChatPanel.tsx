"use client";

import { useEffect, useRef, useState } from "react";
import Composer from "@/components/chat/Composer";
import MessageRow from "@/components/chat/MessageRow";
import Orb from "@/components/chat/Orb";
import { useVault } from "@/lib/api";
import { useChatActions, useChatMessages, useChatMeta } from "@/lib/chat";
import { useSelectedModel } from "@/lib/models";

const EXAMPLES = [
  "What did I write about algorithms?",
  "Summarize my recent daily notes.",
  "What's in my inbox folder?",
];

/**
 * Shared chat surface. `dock` = compact bubbles (drawer); `full` = /chat
 * standard-chatbot layout: assistant orb + name row + unboxed prose, user
 * messages as right-aligned tinted bubbles, input pinned at the bottom.
 *
 * One turn is a memoised `MessageRow` and the input is a separate `Composer`,
 * which is what keeps this cheap on a long thread: neither typing a character
 * nor receiving a batched delta re-renders any message but the one that
 * actually changed.
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
  const { data: vault } = useVault();
  const messages = useChatMessages();
  const { busy, offline } = useChatMeta();
  const { send, stop } = useChatActions();
  const model = useSelectedModel();
  const [pinned, setPinned] = useState(true);
  const scrollRef = useRef<HTMLDivElement>(null);

  // §7 scroll fix: set scrollTop on the container instead of scrollIntoView
  // (scrollIntoView can scroll ancestor containers / the page itself).
  //
  // Only while the reader is already at the bottom, though. This used to run
  // on every change to `messages`, which meant scrolling up to re-read an
  // earlier answer put you in a fight with the streaming reply for control of
  // the viewport — it yanked you back down on every batched delta.
  useEffect(() => {
    if (!pinned) return;
    const container = scrollRef.current;
    if (container) container.scrollTop = container.scrollHeight;
  }, [messages, pinned]);

  function trackPinned(event: React.UIEvent<HTMLDivElement>) {
    const el = event.currentTarget;
    setPinned(el.scrollHeight - el.scrollTop - el.clientHeight < 64);
  }

  const compact = variant === "dock";
  // No `?? "vault"` fallback. While /api/vault is still loading -- or 503s on
  // an unconfigured install -- a guessed name produced a link that was certain
  // to fail, and a chip that quietly stays plain text beats one that opens an
  // error dialog.
  const vaultPath = vault?.path;
  const prompts = suggestions ?? (compact ? [] : EXAMPLES);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* `log` is the transcript role: it names the region for a screen reader
          and marks new turns as they arrive. ToolTrace keeps its own narrower
          live region inside for step-by-step progress. */}
      <div
        ref={scrollRef}
        onScroll={trackPinned}
        role="log"
        aria-label="Conversation"
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

        {messages.map((message) => (
          <MessageRow
            key={message.key}
            message={message}
            compact={compact}
            model={model}
            vaultPath={vaultPath}
          />
        ))}
        {offline && (
          <p className="text-center text-xs text-danger">
            Can’t reach Argus — is the backend running on :8000?
          </p>
        )}
      </div>

      <div className={compact ? "relative pt-2" : "relative border-t border-line pt-3"}>
        {!pinned && (
          <button
            type="button"
            onClick={() => setPinned(true)}
            className="animate-rise absolute -top-9 left-1/2 -translate-x-1/2 border border-line bg-panel px-2.5 py-1 font-mono text-meta text-ink-muted transition-colors hover:border-lineHi hover:text-ink"
          >
            ↓ jump to latest
          </button>
        )}
        <Composer busy={busy} onSend={send} onStop={stop} placeholder={placeholder} />
      </div>
    </div>
  );
}
