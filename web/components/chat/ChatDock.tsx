"use client";

import { usePathname } from "next/navigation";
import { useState } from "react";
import ChatPanel from "@/components/chat/ChatPanel";

/** Floating chat button + pop-over; hidden where a chat surface already exists. */
export default function ChatDock() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  if (pathname.startsWith("/dashboard") || pathname.startsWith("/chat")) return null;

  return (
    <div className="fixed bottom-20 right-4 z-30 md:bottom-6 md:right-6">
      {open && (
        <div className="glass animate-msg-in mb-3 flex h-[28rem] w-[min(22rem,calc(100vw-2rem))] flex-col p-3">
          <ChatPanel variant="dock" />
        </div>
      )}
      <button
        aria-label={open ? "Close chat" : "Open chat"}
        onClick={() => setOpen((value) => !value)}
        className="ml-auto flex h-12 w-12 items-center justify-center rounded-full bg-gradient-to-br from-primary to-accent text-white shadow-[0_4px_24px_rgba(139,92,246,0.5)] transition-transform hover:scale-105"
      >
        <svg width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" viewBox="0 0 24 24">
          <path d="M21 12a8 8 0 0 1-8 8H4l2.5-2.5A8 8 0 1 1 21 12Z" />
        </svg>
      </button>
    </div>
  );
}
