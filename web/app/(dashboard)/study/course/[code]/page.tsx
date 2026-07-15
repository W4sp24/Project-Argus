"use client";

import { useRouter } from "next/navigation";
import { CourseChat, CourseStudio } from "@/components/preview/CourseHub";
import CourseSourcesPanel from "@/components/study/CourseSourcesPanel";
import { useStudyCourses } from "@/lib/api";

/**
 * Course Hub (§4 Course Hub, `[PREVIEW]`) — NotebookLM-style 3-pane workspace
 * opened via a course row's `HUB →`. This is deliberately NOT part of the
 * OVERVIEW | FLASHCARDS | PRACTICE EXAM sub-nav triad (no <StudyTabs/> here)
 * — it's a separate fullscreen workspace with its own back-button header,
 * matching the spec's Course Hub section which never mentions the tab row.
 *
 * Only the SOURCES rail is real data (`GET /api/notes`, filtered to
 * `15-Courses/<CODE>/`); the course title comes from the real
 * `GET /api/study/courses` list. Chat and STUDIO are mock (§8
 * flags.courseHub) — see components/preview/CourseHub.tsx.
 */
export default function CourseHubPage({ params }: { params: { code: string } }) {
  const router = useRouter();
  const code = decodeURIComponent(params.code);
  const { data: courses } = useStudyCourses();
  const course = courses?.find((c) => c.code === code);

  return (
    <div className="flex h-[calc(100dvh-8rem)] flex-col md:h-[calc(100dvh-4rem)]">
      <header className="mb-4 flex flex-wrap items-center gap-3 animate-rise">
        <button
          type="button"
          onClick={() => router.back()}
          className="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-ink-bright"
        >
          ← BACK
        </button>
        <div className="min-w-0">
          <p className="eyebrow">{`▍COURSE.HUB · ${code}`}</p>
          <p className="truncate text-[15px] font-medium text-ink-bright">
            {course?.title ?? "Unknown course"}
          </p>
        </div>
        <span className="border border-[#3d2f66] px-1 py-px font-mono text-[8px] uppercase tracking-[0.16em] text-[#8b7bc0]">
          PREVIEW
        </span>
        <button
          type="button"
          aria-label="Model selector — wired in a later phase"
          disabled
          className="ml-auto border border-line px-2.5 py-1.5 font-mono text-[11px] text-ink-faint"
        >
          claude-sonnet-5 ▾
        </button>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[300px_minmax(0,1fr)_270px]">
        <div className="min-h-0 overflow-y-auto">
          <CourseSourcesPanel code={code} />
        </div>
        <div className="min-h-0">
          <CourseChat code={code} />
        </div>
        <div className="min-h-0 overflow-y-auto">
          <CourseStudio code={code} />
        </div>
      </div>
    </div>
  );
}
