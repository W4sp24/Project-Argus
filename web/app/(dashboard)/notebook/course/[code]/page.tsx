"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { EngineTrigger } from "@/components/EnginePicker";
import CourseDecksPanel from "@/components/notebook/CourseDecksPanel";
import { CourseChat, CourseStudio } from "@/components/notebook/CourseHub";
import CourseSourcesPanel from "@/components/notebook/CourseSourcesPanel";
import { useStudyCourses } from "@/lib/api";
import { CourseSelectionProvider } from "@/lib/courseSelection";

/**
 * Course Hub (§4 Course Hub) — NotebookLM-style 3-pane workspace opened via a
 * course row's `HUB →`. This is deliberately NOT part of the
 * OVERVIEW | FLASHCARDS | PRACTICE EXAM sub-nav triad (no <NotebookTabs/> here)
 * — it's a separate fullscreen workspace with its own back-button header,
 * matching the spec's Course Hub section which never mentions the tab row.
 *
 * Every pane is real data now (§8 flags.courseHub: enabled): SOURCES from
 * `GET /api/study/courses/<code>/sources`, chat over `/ws/chat` with the
 * course forced into the retrieval filter (backend/agent/runtime.py), and
 * STUDIO's generate buttons + "generated" list hitting the same endpoints
 * `CoursesPanel`/`Flashcards` use — see components/notebook/CourseHub.tsx.
 *
 * The three panes share one `CourseSelectionProvider`: SOURCES ticks the
 * boxes, chat sends them on every frame, STUDIO sends them to the generators.
 * That shared state is the whole reason the checkboxes stopped being
 * decoration, and it has to live above all three because they are siblings.
 */
const PANES = ["sources", "chat", "studio"] as const;

export default function CourseHubPage({ params }: { params: { code: string } }) {
  const router = useRouter();
  // Chat first: it is the pane a narrow screen is most likely to be opened
  // for, and the other two are a tap away.
  const [active, setActive] = useState<(typeof PANES)[number]>("chat");
  const code = decodeURIComponent(params.code);
  const { data: courses } = useStudyCourses();
  const course = courses?.find((c) => c.code === code);

  return (
    <div className="flex h-[calc(100dvh-8rem)] flex-col md:h-[calc(100dvh-4rem)]">
      <header className="mb-4 flex flex-wrap items-center gap-3 animate-rise">
        <button
          type="button"
          onClick={() => router.back()}
          className="font-mono text-label uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-ink-bright"
        >
          ← BACK
        </button>
        <div className="min-w-0">
          <p className="eyebrow">{`▍COURSE.HUB · ${code}`}</p>
          <p className="truncate text-lead font-medium text-ink-bright">
            {course?.title ?? "Unknown course"}
          </p>
        </div>
        {/* Was a disabled button with `claude-sonnet-5` baked into it, which
            misreported the model the moment anything else was selected. */}
        <div className="ml-auto">
          <EngineTrigger />
        </div>
      </header>

      <CourseSelectionProvider code={code}>
        {/* Below `lg` the three panes used to stack inside a fixed-height
            column, so each got roughly a third of the viewport with its own
            scrollbar nested inside the page scroll — cramped at a tablet
            width and genuinely unusable below about 700px. One pane at a time
            gets the whole height instead, which is what a narrow screen can
            actually show. The selection provider stays above all three, so
            switching tabs never touches what is ticked. */}
        <div className="mb-3 flex gap-1 lg:hidden" role="tablist" aria-label="Course hub pane">
          {PANES.map((pane) => (
            <button
              key={pane}
              role="tab"
              aria-selected={active === pane}
              onClick={() => setActive(pane)}
              className={`flex-1 border px-2 py-1.5 font-mono text-meta uppercase tracking-[0.14em] transition-colors ${
                active === pane
                  ? "border-[var(--ac)] text-ink-bright"
                  : "border-line text-ink-muted hover:border-lineHi hover:text-ink"
              }`}
            >
              {pane}
            </button>
          ))}
        </div>

        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[300px_minmax(0,1fr)_270px]">
          <div
            className={`min-h-0 overflow-y-auto ${active === "sources" ? "" : "hidden"} lg:block`}
          >
            <CourseSourcesPanel code={code} materialsPath={course?.materials_path} />
          </div>
          {/* Kept mounted, not unmounted: the chat thread and any in-flight
              answer belong to the pane, and re-mounting on every tab switch
              would throw both away. */}
          <div className={`min-h-0 ${active === "chat" ? "" : "hidden"} lg:block`}>
            <CourseChat code={code} />
          </div>
          <div
            className={`min-h-0 overflow-y-auto ${active === "studio" ? "" : "hidden"} lg:block`}
          >
            <div className="flex flex-col gap-4">
              <CourseStudio code={code} />
              {/* Under STUDIO, not above it: you come here to make something,
                  and what you have already made is what you scroll to. */}
              <CourseDecksPanel code={code} />
            </div>
          </div>
        </div>
      </CourseSelectionProvider>
    </div>
  );
}
