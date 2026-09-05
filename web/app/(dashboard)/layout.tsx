import ChatDrawer from "@/components/ChatDrawer";
import CommandPalette from "@/components/CommandPalette";
import EnginePicker from "@/components/EnginePicker";
import NoteModal from "@/components/NoteModal";
import SwrCacheProvider from "@/components/SwrCacheProvider";
import TopBar from "@/components/TopBar";
import { ToastProvider } from "@/components/Toast";
import UpdateBanner from "@/components/UpdateBanner";
import { ChatProvider } from "@/lib/chat";
import { JobsProvider } from "@/lib/jobs";
import { ModeProvider } from "@/lib/mode";
import { UiProvider } from "@/lib/ui";

export default function DashboardLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    // Outermost, so every provider below shares one cache. SWR's default is an
    // unbounded module-level Map, and this renderer runs for hours keying a
    // great deal per note, per course and per deck, so nothing was ever
    // released -- see web/lib/swrCache.ts. JobsProvider polls through SWR, so
    // it has to sit inside this.
    <SwrCacheProvider>
      <ChatProvider>
        <ToastProvider>
          {/* ModeProvider needs useToast (mode-change toasts), so it nests inside
              ToastProvider. Its own wrapper div carries --ac/--ac-bg (§2).
              JobsProvider sits between them for the same reason -- it toasts
              when background work lands -- and above the router, which is the
              whole point: a generation started in one route must not be
              forgotten when another route mounts. */}
          <JobsProvider>
            <ModeProvider>
              <UiProvider>
                <div className="min-h-dvh">
                  <TopBar />
                  <main className="px-4 pb-8 pt-6 md:px-8 md:pt-8">
                    <div className="shell">{children}</div>
                  </main>
                  {/* Overlay surfaces (Phase F) — each renders nothing while closed (§10).
                      UpdateBanner leads: it shares z-40 with ChatDrawer, and paint
                      order is what decides which one wins when both are open. */}
                  <UpdateBanner />
                  <ChatDrawer />
                  <CommandPalette />
                  <NoteModal />
                  <EnginePicker />
                </div>
              </UiProvider>
            </ModeProvider>
          </JobsProvider>
        </ToastProvider>
      </ChatProvider>
    </SwrCacheProvider>
  );
}
