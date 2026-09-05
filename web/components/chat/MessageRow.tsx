"use client";

import React, { useMemo } from "react";
import { useToast } from "@/components/Toast";
import CitationChips from "@/components/chat/CitationChips";
import Orb from "@/components/chat/Orb";
import ToolTrace from "@/components/chat/ToolTrace";
import Markdown from "@/components/Markdown";
import type { ChatMessage } from "@/lib/chat";
import { stripCitationMarkers } from "@/lib/citations";

/** Copy a finished answer. Only claims success once `writeText` actually
 *  resolves: `navigator.clipboard` is undefined outside a secure context and
 *  rejects when the document is unfocused, and a toast that lies about it
 *  sends the reader off to paste something else entirely
 *  (web/components/system/ConnectN8nDialog.tsx:88 makes the same argument). */
function CopyAnswer({ text }: { text: string }) {
  const { show } = useToast();
  return (
    <button
      type="button"
      aria-label="Copy answer"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          show("copied :: answer on the clipboard");
        } catch {
          show("copy failed :: select the text and copy it by hand", { tone: "error" });
        }
      }}
      className="font-mono text-meta lowercase text-ink-faint opacity-0 transition-opacity hover:text-ink focus-visible:opacity-100 group-hover:opacity-100"
    >
      copy
    </button>
  );
}

/** How a turn ended, when it did not end well. The old client overwrote the
 *  answer with "Something went wrong: …", which also threw away whatever the
 *  agent had already said; the partial text now stays and this sits under it. */
function StatusLine({ message }: { message: ChatMessage }) {
  // Run-level warnings sit above the ending, and show even on a turn that
  // ended cleanly — the whole point is that a truncated answer used to look
  // like a complete one.
  const notices = (message.notices ?? []).map((detail, i) => (
    <p
      key={i}
      className="flex items-start gap-1.5 border border-ink-faint bg-void px-3 py-1.5 font-mono text-meta text-ink-faint"
    >
      <span aria-hidden="true">!</span>
      <span className="min-w-0 flex-1">{detail}</span>
    </p>
  ));

  if (notices.length > 0) {
    return (
      <>
        {notices}
        <StatusEnding message={message} />
      </>
    );
  }
  return <StatusEnding message={message} />;
}

function StatusEnding({ message }: { message: ChatMessage }) {
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

interface MessageRowProps {
  message: ChatMessage;
  /** `dock` bubbles (drawer) vs `full` prose (/chat). */
  compact: boolean;
  /** Named on the assistant's byline; from the engine picker, not the message. */
  model: string;
  /** Absent until /api/vault answers — see CitationChips. */
  vaultPath: string | undefined;
}

/**
 * One turn of the transcript.
 *
 * Extracted out of `ChatPanel` and memoised, which is the whole point. The map
 * over `messages` used to be inline in the same component that holds the
 * composer's `input` state, so **every keystroke re-rendered the entire
 * thread**: `stripCitationMarkers` (a global regex) ran over the full text of
 * every message, and `ToolTrace` and `CitationChips` were re-invoked for all of
 * them, on every character typed. The same O(N) pass repeated on every batched
 * delta while streaming.
 *
 * The memo bites immediately because `patchLast` (web/lib/chat.tsx) only ever
 * replaces the tail of the array — every other `ChatMessage` object keeps its
 * identity across a flush, so only the turn actually being written re-renders.
 */
function MessageRow({ message, compact, model, vaultPath }: MessageRowProps) {
  // Keyed on the text so a streaming turn still re-strips as it grows, while a
  // finished one never does again.
  const body = useMemo(() => stripCitationMarkers(message.text), [message.text]);

  if (message.role === "user") {
    return (
      <div className="animate-msg-in flex justify-end">
        <div
          className={`max-w-[85%] border border-lineHi bg-[var(--ac-bg)] px-3.5 py-2.5 leading-relaxed text-ink ${
            compact ? "text-body" : "text-lead"
          }`}
        >
          <span className="whitespace-pre-wrap">{message.text}</span>
        </div>
      </div>
    );
  }

  if (compact) {
    return (
      <div className="animate-msg-in flex justify-start">
        <div className="min-w-0 max-w-[85%] flex-1 space-y-2">
          <ToolTrace
            steps={message.steps}
            status={message.status}
            startedAt={message.startedAt}
            endedAt={message.endedAt}
          />
          {message.text && (
            <div className="border border-line bg-void px-3.5 py-2.5 text-body leading-relaxed text-ink-muted">
              <Markdown text={body} streaming={message.status === "streaming"} />
              <CitationChips steps={message.steps} vaultPath={vaultPath} />
            </div>
          )}
          <StatusLine message={message} />
        </div>
      </div>
    );
  }

  return (
    <div className="animate-msg-in group flex gap-3">
      <Orb />
      <div className="min-w-0 flex-1 space-y-2">
        {/* `text-ink-muted` rather than `text-ink-faint`: this line
            names which model answered and whether the answer is being
            kept. Both are facts a user acts on, so it cannot be the
            faintest text in the transcript. */}
        <p className="font-mono text-meta uppercase tracking-[0.14em] text-ink-muted">
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
              text={body}
              className="text-lead leading-[1.7] text-ink"
              streaming={message.status === "streaming"}
            />
            <CitationChips steps={message.steps} vaultPath={vaultPath} />
          </>
        )}
        <StatusLine message={message} />
        {message.text && message.status !== "streaming" && <CopyAnswer text={message.text} />}
      </div>
    </div>
  );
}

export default React.memo(MessageRow);
