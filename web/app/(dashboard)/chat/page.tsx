"use client";

import { useRouter } from "next/navigation";
import ModelSelect from "@/components/ModelSelect";
import ChatPanel from "@/components/chat/ChatPanel";
import { useSelectedModel } from "@/lib/models";

/**
 * Fullscreen chat (§7): centered 780px column, input pinned at the bottom
 * (inside ChatPanel's full variant), model selector in the header, footer
 * privacy status line. One conversation shared with the drawer (ChatProvider
 * lives in the dashboard layout).
 */
export default function ChatPage() {
  const router = useRouter();
  const model = useSelectedModel();

  return (
    <div className="mx-auto flex h-[calc(100dvh-8rem)] max-w-[780px] flex-col md:h-[calc(100dvh-4rem)]">
      <header className="flex items-center gap-3 border-b border-line pb-3 animate-rise">
        <button
          type="button"
          onClick={() => router.back()}
          className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-ink-bright"
        >
          ← BACK
        </button>
        <p className="eyebrow">▍ARGUS.CHAT</p>
        <div className="ml-auto">
          <ModelSelect />
        </div>
      </header>

      <ChatPanel variant="full" />

      <p className="border-t border-line pt-2 text-center font-mono text-[10px] tracking-[0.08em] text-ink-faint">
        ◈ {model} · local index · 99-Private/ and #no-ai never leave your machine
      </p>
    </div>
  );
}
