import Sidebar from "@/components/Sidebar";

export default function DashboardLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <div className="min-h-dvh">
      <Sidebar />
      <main className="px-4 pb-24 pt-6 md:ml-64 md:px-8 md:pb-8 md:pt-8">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}
