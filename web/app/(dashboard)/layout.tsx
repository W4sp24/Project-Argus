import TopBar from "@/components/TopBar";
import { ToastProvider } from "@/components/Toast";
import ChatDock from "@/components/chat/ChatDock";
import { ChatProvider } from "@/lib/chat";
import { ModeProvider } from "@/lib/mode";

export default function DashboardLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <ChatProvider>
      <ToastProvider>
        {/* ModeProvider needs useToast (mode-change toasts), so it nests inside
            ToastProvider. Its own wrapper div carries --ac/--ac-bg (§2). */}
        <ModeProvider>
          <div className="min-h-dvh">
            <TopBar />
            <main className="px-4 pb-8 pt-6 md:px-8 md:pt-8">
              <div className="mx-auto max-w-6xl">{children}</div>
            </main>
            <ChatDock />
          </div>
        </ModeProvider>
      </ToastProvider>
    </ChatProvider>
  );
}
