"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

interface NavItem {
  href: string;
  name: string;
  icon: ReactNode;
}

const iconProps = {
  width: 18,
  height: 18,
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.7,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
};

const NAV_ITEMS: NavItem[] = [
  {
    href: "/dashboard",
    name: "Dashboard",
    icon: (
      <svg {...iconProps} viewBox="0 0 24 24">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M19.1 4.9L17 7M7 17l-2.1 2.1" />
      </svg>
    ),
  },
  {
    href: "/tasks",
    name: "Tasks",
    icon: (
      <svg {...iconProps} viewBox="0 0 24 24">
        <path d="M4 6h11M4 12h11M4 18h7" />
        <path d="m17 16 2 2 4-4" />
      </svg>
    ),
  },
  {
    href: "/chat",
    name: "Chat",
    icon: (
      <svg {...iconProps} viewBox="0 0 24 24">
        <path d="M21 12a8 8 0 0 1-8 8H4l2.5-2.5A8 8 0 1 1 21 12Z" />
      </svg>
    ),
  },
  {
    href: "/study",
    name: "Study",
    icon: (
      <svg {...iconProps} viewBox="0 0 24 24">
        <path d="M4 19V5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2Z" />
        <path d="M4 19a2 2 0 0 0 2 2h13" />
      </svg>
    ),
  },
  {
    href: "/journal",
    name: "Journal",
    icon: (
      <svg {...iconProps} viewBox="0 0 24 24">
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
      </svg>
    ),
  },
  {
    href: "/review",
    name: "Review",
    icon: (
      <svg {...iconProps} viewBox="0 0 24 24">
        <path d="M9 11V7a3 3 0 0 1 6 0v4" />
        <rect x="5" y="11" width="14" height="9" rx="2" />
      </svg>
    ),
  },
  {
    href: "/insights",
    name: "Insights",
    icon: (
      <svg {...iconProps} viewBox="0 0 24 24">
        <path d="M4 20V10M10 20V4M16 20v-7M22 20H2" />
      </svg>
    ),
  },
];

/** Fixed glass rail with Argus's breathing core orb and the six pages. */
export default function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-x-3 bottom-3 z-20 flex items-center justify-around border border-line bg-panel px-2 py-2 md:inset-x-auto md:left-4 md:top-4 md:bottom-4 md:w-56 md:flex-col md:items-stretch md:justify-start md:px-4 md:py-6">
      <Link
        href="/dashboard"
        className="mb-0 hidden items-center gap-3 px-2 md:mb-8 md:flex"
        aria-label="Argus home"
      >
        <span className="relative flex h-9 w-9 items-center justify-center">
          {/* Static glow — nothing in the rail animates at rest (§10: only the
              blink cursor may loop). Sidebar is replaced by TopBar in Phase B. */}
          <span className="absolute -inset-1 rounded-full bg-[radial-gradient(circle,rgba(167,139,250,0.85),rgba(217,70,239,0.4)_55%,transparent_72%)] opacity-70" />
          <span className="relative h-5 w-5 rounded-full bg-gradient-to-br from-primary-soft to-accent shadow-[0_0_12px_rgba(167,139,250,0.8)]" />
        </span>
        <span>
          <span className="block font-display text-lg font-semibold tracking-wide">Argus</span>
          <span className="block font-mono text-[10px] uppercase tracking-[0.2em] text-ink-faint">
            second brain
          </span>
        </span>
      </Link>

      <nav className="flex w-full items-center justify-between gap-0.5 md:flex-col md:items-stretch md:justify-start md:gap-1">
        {NAV_ITEMS.map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? "page" : undefined}
              aria-label={item.name}
              className={`flex flex-1 flex-col items-center gap-1 rounded-xl px-1.5 py-2 text-xs transition-colors md:flex-none md:flex-row md:gap-3 md:px-3 md:text-sm ${
                active
                  ? "bg-gradient-to-r from-primary/25 to-accent/15 text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.08)]"
                  : "text-ink-muted hover:bg-white/5 hover:text-ink"
              }`}
            >
              <span className={active ? "text-primary-soft" : ""}>{item.icon}</span>
              <span className="sr-only md:not-sr-only">{item.name}</span>
            </Link>
          );
        })}
      </nav>

      <p className="mt-auto hidden px-2 pt-6 font-mono text-[10px] text-ink-faint md:block">
        local · private · yours
      </p>
    </aside>
  );
}
