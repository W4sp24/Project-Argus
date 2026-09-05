"use client";

import { useRouter } from "next/navigation";
import { EngineTrigger } from "@/components/EnginePicker";
import ChatPanel from "@/components/chat/ChatPanel";
import ThreadRail from "@/components/chat/ThreadRail";
import { useChatMeta } from "@/lib/chat";
import { useSelectedModel } from "@/lib/models";

/**
 * Fullscreen chat (§7): a thread rail beside a centered 780px column, input
 * pinned at the bottom (inside ChatPanel's full variant), model selector in
 * the header, footer privacy status line.
 *
 * The rail is deliberately this route's alone. The drawer shares the same
 * `ChatProvider` from the dashboard layout, so it follows whichever thread is
 * open here — one conversation across both surfaces, as it has always been —
 * but a 16rem list of conversations inside a 360px drawer would be a worse
 * version of this page rather than a useful second one.
 */
export default function ChatPage() {
  const router = useRouter();
  const model = useSelectedModel();
  const { threadTitle } = useChatMeta();

  return (
    <div className="flex h-[calc(100dvh-8rem)] md:h-[calc(100dvh-4rem)]">
      {/* Below md the rail would take the reading column's width rather than
          share it, so it drops out and the transcript keeps the screen. */}
      <div className="hidden md:flex">
        <ThreadRail />
      </div>

      <div className="mx-auto flex min-w-0 max-w-[780px] flex-1 flex-col md:px-6">
        <header className="animate-rise flex items-center gap-3 border-b border-line pb-3">
          <button
            type="button"
            onClick={() => router.back()}
            className="shrink-0 font-mono text-label uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-ink-bright"
          >
            ← BACK
          </button>
          <p className="eyebrow shrink-0">▍ARGUS.CHAT</p>
          {threadTitle && (
            <p className="min-w-0 flex-1 truncate font-mono text-meta text-ink-faint">
              · {threadTitle}
            </p>
          )}
          <div className="ml-auto shrink-0">
            <EngineTrigger />
          </div>
        </header>

        <ChatPanel variant="full" />

        <p className="border-t border-line pt-2 text-center font-mono text-meta tracking-[0.08em] text-ink-faint">
          ◈ {model} · local index · 99-Private/ and #no-ai never leave your machine
        </p>
      </div>
    </div>
  );
}
