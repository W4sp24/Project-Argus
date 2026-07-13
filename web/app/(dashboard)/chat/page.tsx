"use client";

import ChatPanel from "@/components/chat/ChatPanel";

export default function ChatPage() {
  return (
    <div className="flex h-[calc(100dvh-8rem)] flex-col md:h-[calc(100dvh-4rem)]">
      <header className="mb-4 animate-rise">
        <p className="eyebrow mb-2">{`// CHAT`}</p>
        <h1 className="font-display text-3xl font-semibold tracking-tight md:text-4xl">
          Ask your <span className="gradient-text">second brain</span>
        </h1>
      </header>
      <ChatPanel variant="full" />
    </div>
  );
}
