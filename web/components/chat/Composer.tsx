"use client";

import { useEffect, useRef, useState } from "react";
import Button from "@/components/ui/Button";

/**
 * The chat input.
 *
 * Its own component so that `input` lives here rather than on `ChatPanel`.
 * While it sat next to the transcript's `messages.map`, React re-rendered every
 * message in the thread on every keystroke — see `MessageRow`'s note. Typing
 * now re-renders this box and nothing else.
 */
export default function Composer({
  busy,
  onSend,
  onStop,
  placeholder,
}: {
  busy: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
  placeholder?: string;
}) {
  const [input, setInput] = useState("");
  const composerRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the composer. Height is reset before measuring so the box
  // shrinks again when text is deleted; `max-h-40` caps it and lets the
  // textarea scroll past that rather than eating the transcript.
  useEffect(() => {
    const el = composerRef.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [input]);

  function submit() {
    if (busy || !input.trim()) return;
    onSend(input);
    setInput("");
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit();
      }}
    >
      <div className="flex items-end gap-2">
        {/* Deliberately not `FIELD_CONTROL` from ui/Field: that sets
            text-label (13px), which is right for a settings form and
            cramped for the box you compose a paragraph in next to 17px
            prose. Same tokens, one size up. */}
        <textarea
          ref={composerRef}
          rows={1}
          value={input}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            // Enter sends, Shift+Enter newlines. `isComposing` guards an
            // IME candidate window, where Enter means "accept this
            // character" and must not fire the message.
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              submit();
            }
          }}
          placeholder={busy ? "Argus is answering…" : (placeholder ?? "Ask your vault")}
          aria-label="Ask your vault"
          className="max-h-40 min-w-0 flex-1 resize-none border border-line bg-sunken px-3 py-2 text-body placeholder:text-ink-faint focus:border-lineHi"
        />
        {/* The composer stays enabled while a turn runs, so a follow-up can
            be typed while reading the answer. STOP replaces SEND rather
            than greying the whole surface out. */}
        {busy ? (
          <Button
            type="button"
            size="md"
            variant="secondary"
            aria-label="Stop generating"
            onClick={onStop}
            className="shrink-0"
          >
            STOP
          </Button>
        ) : (
          <Button
            type="submit"
            size="md"
            variant="primary"
            aria-label="Send"
            disabled={!input.trim()}
            className="shrink-0"
          >
            SEND
          </Button>
        )}
      </div>
    </form>
  );
}
