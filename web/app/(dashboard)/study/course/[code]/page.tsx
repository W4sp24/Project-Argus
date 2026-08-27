"use client";

import { useRouter } from "next/navigation";
import { EngineTrigger } from "@/components/EnginePicker";
import { CourseChat, CourseStudio } from "@/components/study/CourseHub";
import CourseSourcesPanel from "@/components/study/CourseSourcesPanel";
import { useStudyCourses } from "@/lib/api";
import { CourseSelectionProvider } from "@/lib/courseSelection";

/**
 * Course Hub (§4 Course Hub) — NotebookLM-style 3-pane workspace opened via a
 * course row's `HUB →`. This is deliberately NOT part of the
 * OVERVIEW | FLASHCARDS | PRACTICE EXAM sub-nav triad (no <StudyTabs/> here)
 * — it's a separate fullscreen workspace with its own back-button header,
 * matching the spec's Course Hub section which never mentions the tab row.
 *
 * Every pane is real data now (§8 flags.courseHub: enabled): SOURCES from
 * `GET /api/study/courses/<code>/sources`, chat over `/ws/chat` with the
 * course forced into the retrieval filter (backend/agent/runtime.py), and
 * STUDIO's generate buttons + "generated" list hitting the same endpoints
 * `CoursesPanel`/`Flashcards` use — see components/study/CourseHub.tsx.
 *
 * The three panes share one `CourseSelectionProvider`: SOURCES ticks the
 * boxes, chat sends them on every frame, STUDIO sends them to the generators.
 * That shared state is the whole reason the checkboxes stopped being
 * decoration, and it has to live above all three because they are siblings.
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
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[300px_minmax(0,1fr)_270px]">
          <div className="min-h-0 overflow-y-auto">
            <CourseSourcesPanel materialsPath={course?.materials_path} />
          </div>
          <div className="min-h-0">
            <CourseChat code={code} />
          </div>
          <div className="min-h-0 overflow-y-auto">
            <CourseStudio code={code} />
          </div>
        </div>
      </CourseSelectionProvider>
    </div>
  );
}
