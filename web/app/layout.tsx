import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
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

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className={`${body.variable} ${mono.variable}`}>{children}</body>
    </html>
  );
}
