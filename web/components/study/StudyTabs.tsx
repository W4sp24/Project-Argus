"use client";

import { usePathname, useRouter } from "next/navigation";

interface Tab {
  href: string;
  label: string;
  match: (pathname: string) => boolean;
}

const TABS: Tab[] = [
  { href: "/study", label: "OVERVIEW", match: (p) => p === "/study" },
  { href: "/study/flashcards", label: "FLASHCARDS", match: (p) => p.startsWith("/study/flashcards") },
  { href: "/study/exam", label: "PRACTICE EXAM", match: (p) => p.startsWith("/study/exam") },
];

/**
 * Study in-mode sub-nav (§4 Study, §5 file plan): OVERVIEW | FLASHCARDS |
 * PRACTICE EXAM — router.push between the three deep-linkable /study* routes.
 * Styled like TopBar's mode tabs (segmented, accent underline on the active
 * tab). Not rendered on /study/course/[code] — the Course Hub is its own
 * fullscreen workspace with a distinct header (§4 Course Hub), not part of
 * this triad.
 */
export default function StudyTabs() {
  const pathname = usePathname() ?? "/study";
  const router = useRouter();

  return (
    <div
      role="tablist"
      aria-label="Study sections"
      className="mb-6 flex border border-line font-mono text-[11px] uppercase tracking-[0.14em]"
    >
      {TABS.map((tab) => {
        const active = tab.match(pathname);
        return (
          <button
            key={tab.href}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => router.push(tab.href)}
            className={`border-r border-line px-3 py-2 transition-colors last:border-r-0 ${
              active
                ? "bg-[var(--ac-bg)] text-[var(--ac)] shadow-[inset_0_-2px_0_var(--ac)]"
                : "text-ink-faint hover:text-ink-muted"
            }`}
          >
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}
