import Sidebar from "@/components/Sidebar";
import ChatDock from "@/components/chat/ChatDock";
import { ChatProvider } from "@/lib/chat";

export default function DashboardLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <ChatProvider>
      <div className="min-h-dvh">
        <Sidebar />
        <main className="px-4 pb-24 pt-6 md:ml-64 md:px-8 md:pb-8 md:pt-8">
          <div className="mx-auto max-w-6xl">{children}</div>
        </main>
        <ChatDock />
      </div>
    </ChatProvider>
  );
}
