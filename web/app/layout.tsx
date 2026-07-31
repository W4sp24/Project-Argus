import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import UiScaleSync from "@/components/UiScaleSync";
import { UI_SCALE_BOOT_SCRIPT } from "@/lib/uiScale";
import "./globals.css";

// Two-font system (v2 typography): Inter for everything the user reads as a
// sentence, JetBrains Mono for terminal chrome. The legacy `font-display`
// Tailwind alias is gone (Phase H) — all usages now say `font-body` directly.
const body = Inter({
  subsets: ["latin"],
  variable: "--font-body",
  display: "swap",
});
const mono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "Argus",
  description: "Your second brain, on your machine. Built on Obsidian.",
};

// Declared rather than left to Next's default so the intent is on the record.
// Deliberately no `maximumScale`/`userScalable: false` — pinch-zoom is how
// someone with low vision reads a dense panel, and taking it away is not ours
// to do.
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${body.variable} ${mono.variable}`}>
        {/* Applies the saved interface size before first paint, so the app
            never renders at one scale and then jumps to another. */}
        <script dangerouslySetInnerHTML={{ __html: UI_SCALE_BOOT_SCRIPT }} />
        {/* ...and puts it back if React rebuilds the root, which discards
            anything the script above set on <html>. See UiScaleSync. */}
        <UiScaleSync />
        {children}
      </body>
    </html>
  );
}
