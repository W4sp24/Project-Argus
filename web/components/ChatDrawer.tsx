"use client";

import Link from "next/link";
import { useEffect, useRef } from "react";
import ChatPanel from "@/components/chat/ChatPanel";
import { useSelectedModel } from "@/lib/models";
import { useUi } from "@/lib/ui";

/**
 * Chat drawer (§7): fixed right, 360px (full-width on mobile), slides in via
 * a transform-only animation — no overlay, no backdrop-blur. Toggled by the
 * TopBar CHAT control and by the palette. Renders nothing when closed (§10).
 * The thread lives in ChatProvider, shared with /chat (one conversation).
 */
export default function ChatDrawer() {
  const { drawerOpen, setDrawerOpen } = useUi();
  const model = useSelectedModel();
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!drawerOpen) return;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen, setDrawerOpen]);

  if (!drawerOpen) return null;

  return (
    <aside
      role="dialog"
      aria-label="Argus chat"
      className="animate-drawer fixed inset-y-0 right-0 z-40 flex w-full flex-col border-l border-line bg-panel sm:w-[360px]"
    >
      <header className="flex items-center gap-2 border-b border-line px-4 py-3">
        <p className="eyebrow min-w-0 flex-1 truncate">{`▍ARGUS.CHAT · ${model}`}</p>
        <Link
          href="/chat"
          aria-label="Open fullscreen chat"
          onClick={() => setDrawerOpen(false)}
          className="border border-line px-2 py-1 font-mono text-[11px] text-ink-faint transition-colors hover:border-lineHi hover:text-ink-muted"
        >
          ⛶
        </Link>
        <button
          ref={closeRef}
          type="button"
          aria-label="Close chat"
          onClick={() => setDrawerOpen(false)}
          className="border border-line px-2 py-1 font-mono text-[11px] text-ink-faint transition-colors hover:border-lineHi hover:text-ink-muted"
        >
          ✕
        </button>
      </header>
      <div className="flex min-h-0 flex-1 flex-col p-4">
        <ChatPanel variant="dock" />
      </div>
    </aside>
  );
}
