"use client";

import { usePathname, useRouter } from "next/navigation";
import { useDueSummary, useStudyExams } from "@/lib/api";

interface Tab {
  href: string;
  label: string;
  match: (pathname: string) => boolean;
}

const TABS: Tab[] = [
  { href: "/notebook", label: "Overview", match: (p) => p === "/notebook" },
  {
    href: "/notebook/flashcards",
    label: "Flashcards",
    match: (p) => p.startsWith("/notebook/flashcards"),
  },
  {
    href: "/notebook/exam",
    label: "Practice exam",
    match: (p) => p.startsWith("/notebook/exam"),
  },
];

/**
 * Notebook in-mode sub-nav: Overview | Flashcards | Practice exam — router.push
 * between the three deep-linkable /notebook* routes. Not rendered on
 * /notebook/course/[code]: the Course Hub is its own workspace with a distinct
 * header, not part of this triad.
 *
 * The counts are the point of the redesign's version. "Flashcards" alone says
 * nothing about whether it is worth opening; "Flashcards 42" is the whole
 * reason to go there. Both hooks are already fetched by the pages below, so SWR
 * serves them from cache rather than issuing a second request.
 *
 * The due count is accented and the exam count is not, deliberately: cards due
 * is a number that changes every day and asks for attention, while the exam
 * count is inventory.
 */
export default function NotebookTabs() {
  const pathname = usePathname() ?? "/notebook";
  const router = useRouter();
  const { data: dueSummary } = useDueSummary();
  const { data: exams } = useStudyExams();

  const counts: Record<string, { value: number; accent: boolean } | undefined> = {
    "/notebook/flashcards": dueSummary?.total
      ? { value: dueSummary.total, accent: true }
      : undefined,
    "/notebook/exam": exams?.length ? { value: exams.length, accent: false } : undefined,
  };

  return (
    <div
      role="tablist"
      aria-label="Notebook sections"
      className="mb-6 flex gap-6 border-b border-nb-line"
    >
      {TABS.map((tab) => {
        const active = tab.match(pathname);
        const count = counts[tab.href];
        return (
          <button
            key={tab.href}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => router.push(tab.href)}
            className={`inline-flex items-center gap-2 px-0.5 pb-3 text-body transition-colors ${
              active
                ? "font-semibold text-nb-ink shadow-[inset_0_-2px_0_var(--ac)]"
                : "font-medium text-nb-faint hover:text-nb-body"
            }`}
          >
            {tab.label}
            {count && (
              <span
                className={`rounded-full px-2 py-px text-meta font-semibold ${
                  count.accent
                    ? "bg-nb-acBg text-[var(--ac)]"
                    : "bg-nb-raised text-nb-body"
                }`}
              >
                {count.value}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
