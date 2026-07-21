"use client";

import Link from "next/link";
import { useRef, useState, type DragEvent } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { ApiError, apiFetch, mutateJSON, useStudyCourses, useStudyExams } from "@/lib/api";
import { useWeakTopics } from "@/lib/useStudySignals";

const ACCEPTED_EXTENSIONS = [".pdf", ".pptx", ".docx", ".md"];
const SAFE_CODE_RE = /^[A-Za-z0-9._-]+$/;

/** Mirrors `vault-template/15-Courses/CS000/course.md`, interpolated with the
 *  submitted code/title and today's date (the template's `created: "{{date}}"`
 *  substitution normally done by the vault-init flow). */
function renderCourseTemplate(code: string, title: string): string {
  const date = new Date().toISOString().slice(0, 10);
  return `---
type: course
code: ${code}
title: ${title}
created: "${date}"
tags: [course]
status: active
---

# ${code} — ${title}

## Info

- **Professor:**
- **Schedule:**
- **Grading:**

## Folders

- \`notes/\` — your own lecture & reading notes (markdown)
- \`materials/\` — drop slides, readings, and the syllabus here (PDF/PPTX/DOCX); Argus indexes them automatically
- \`study/\` — Argus writes study guides, practice exams, and review queues here
`;
}

/**
 * COURSES (§4 Study Overview): lists real courses from `GET /api/study/courses`
 * (15-Courses/<CODE>/course.md hub notes). CRUD is honest about what the
 * backend can actually do:
 *  - `+ FILES` and drag-drop upload real material to `POST /api/study/upload`
 *    (backend/study/api.py) — this endpoint exists and already worked in the
 *    pre-redesign page, so it's wired for real, not marked preview.
 *  - `GUIDE` / `EXAM` generate via the real `/api/study/guide` and
 *    `/api/study/exam` endpoints (kept from the old page — dropping them
 *    would regress working functionality even though the spec text doesn't
 *    call them out explicitly for this panel).
 *  - `+ ADD COURSE` renders the vault's course template with the submitted
 *    code/title and creates it for real via `POST /api/note/create`
 *    (backend/writer.py `create_note`), landing at `15-Courses/<CODE>/course.md`.
 *    A 409 (code already exists) surfaces as a clear toast instead of a
 *    generic failure; success re-fetches `GET /api/study/courses` so the new
 *    course appears from the vault, not from local mock state.
 *  - `×` never deletes vault files (spec: "removes the course entry, never
 *    deletes vault files") — it hides the row locally and toasts that the
 *    vault folder is untouched, since courses are derived from vault folders
 *    and there is nothing safe to delete via the API.
 */
export default function CoursesPanel() {
  const { data: courses, mutate: refreshCourses } = useStudyCourses();
  const { mutate: refreshExams } = useStudyExams();
  const weakTopics = useWeakTopics();
  const { show } = useToast();

  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [addCode, setAddCode] = useState("");
  const [addName, setAddName] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [creating, setCreating] = useState(false);

  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [dragOverCourse, setDragOverCourse] = useState<string | null>(null);
  const fileInputs = useRef<Record<string, HTMLInputElement | null>>({});

  const visible = (courses ?? []).filter((course) => !hidden.has(course.code));

  function isAcceptedFile(file: File) {
    const name = file.name.toLowerCase();
    return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
  }

  async function upload(course: string, file: File) {
    if (!isAcceptedFile(file)) {
      show(`"${file.name}" isn't supported — use ${ACCEPTED_EXTENSIONS.join(", ")}`);
      return;
    }
    setBusyAction(`upload-${course}`);
    const body = new FormData();
    body.append("course", course);
    body.append("file", file);
    const response = await apiFetch("/api/study/upload", { method: "POST", body });
    const payload = await response.json();
    show(response.ok ? `saved ${payload.path} — indexing in the background` : `upload failed: ${payload.detail}`);
    setBusyAction(null);
    refreshCourses();
  }

  function handleDragOver(course: string, event: DragEvent) {
    event.preventDefault();
    if (busyAction !== null) return;
    setDragOverCourse(course);
  }
  function handleDragLeave(course: string, event: DragEvent) {
    event.preventDefault();
    if (dragOverCourse === course) setDragOverCourse(null);
  }
  function handleDrop(course: string, event: DragEvent) {
    event.preventDefault();
    setDragOverCourse(null);
    if (busyAction !== null) return;
    const file = event.dataTransfer.files?.[0];
    if (file) upload(course, file);
  }

  async function generate(kind: "guide" | "exam", course: string) {
    setBusyAction(`${kind}-${course}`);
    show(`generating ${kind} for ${course} — this can take a few minutes…`);
    const response = await apiFetch(`/api/study/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(kind === "guide" ? { course } : { course, n: 10 }),
    });
    const payload = await response.json();
    if (!response.ok) {
      show(`${kind} failed: ${payload.detail}`);
    } else if (kind === "exam") {
      show(`exam ready: ${payload.questions} cited questions → ${payload.path}`);
      refreshExams();
    } else {
      show(`study guide written to ${payload.path}`);
    }
    setBusyAction(null);
  }

  function hideCourse(code: string) {
    setHidden((prev) => new Set(prev).add(code));
    show(`hidden :: ${code} — 15-Courses/${code}/ is untouched in the vault`);
  }

  async function addCourse(event: React.FormEvent) {
    event.preventDefault();
    const code = addCode.trim().toUpperCase();
    const title = addName.trim();
    if (!code || !title || !SAFE_CODE_RE.test(code) || creating) return;

    setCreating(true);
    const path = `15-Courses/${code}/course.md`;
    try {
      await mutateJSON<{ path: string }>("/api/note/create", {
        path,
        content: renderCourseTemplate(code, title),
      });
      show(`course :: created → ${path}`);
      setAddCode("");
      setAddName("");
      setShowAddForm(false);
      refreshCourses();
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        show(`course :: "${code}" already exists — pick a different code`);
      } else {
        show(`course :: create failed — ${error instanceof Error ? error.message : "backend offline?"}`);
      }
    } finally {
      setCreating(false);
    }
  }

  return (
    <Panel
      label="COURSES"
      headerRight={
        <button
          type="button"
          onClick={() => setShowAddForm((v) => !v)}
          className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-[0.14em] text-ink-faint transition-colors hover:text-[var(--ac)]"
        >
          + ADD COURSE
        </button>
      }
    >
      {showAddForm && (
        <form
          onSubmit={addCourse}
          className="mb-4 flex flex-wrap items-center gap-2 border border-dashed border-line px-3 py-3"
        >
          <input
            value={addCode}
            onChange={(event) => setAddCode(event.target.value)}
            placeholder="CODE (e.g. CS301)"
            className="w-36 border border-line bg-sunken px-2 py-1.5 font-mono text-[12px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
          />
          <input
            value={addName}
            onChange={(event) => setAddName(event.target.value)}
            placeholder="Course name"
            className="min-w-0 flex-1 border border-line bg-sunken px-2 py-1.5 text-[13px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
          />
          <button
            type="submit"
            disabled={!addCode.trim() || !addName.trim() || creating}
            className="border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
          >
            {creating ? "ADDING…" : "ADD"}
          </button>
        </form>
      )}

      {visible.length === 0 && (
        <p className="text-[13px] text-ink-faint">
          No courses yet — create a folder like <span className="font-mono text-xs">15-Courses/CS201/</span>{" "}
          with a <span className="font-mono text-xs">course.md</span> in your vault.
        </p>
      )}

      <div className="space-y-3">
        {visible.map((course) => {
          const chips = weakTopics.filter((topic) => topic.course === course.code).slice(0, 4);
          return (
            <div
              key={course.code}
              onDragOver={(event) => handleDragOver(course.code, event)}
              onDragLeave={(event) => handleDragLeave(course.code, event)}
              onDrop={(event) => handleDrop(course.code, event)}
              className={`border p-3 transition-colors ${
                dragOverCourse === course.code ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="flex items-center gap-1.5 font-mono text-[10px] uppercase tracking-wide text-ink-faint">
                    {course.code}
                  </p>
                  <p className="truncate text-[15px] font-medium text-ink-bright">{course.title}</p>
                </div>
                <button
                  aria-label={`Remove ${course.code} from this list`}
                  onClick={() => hideCourse(course.code)}
                  className="shrink-0 font-mono text-xs text-ink-faint transition-colors hover:text-danger"
                >
                  ×
                </button>
              </div>
              <p className="mt-1 font-mono text-[11px] text-ink-faint">
                {course.materials} material{course.materials === 1 ? "" : "s"} · {course.notes} note
                {course.notes === 1 ? "" : "s"}
                {dragOverCourse === course.code && <span className="ml-2 text-[var(--ac)]">— drop to upload</span>}
              </p>

              {chips.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {chips.map((chip) => (
                    <span
                      key={chip.topic}
                      className="border border-line px-1.5 py-0.5 font-mono text-[10px] text-ink-muted"
                    >
                      {chip.topic}
                    </span>
                  ))}
                </div>
              )}

              <div className="mt-3 flex flex-wrap items-center gap-2">
                <input
                  ref={(el) => {
                    fileInputs.current[course.code] = el;
                  }}
                  type="file"
                  accept={ACCEPTED_EXTENSIONS.join(",")}
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (file) upload(course.code, file);
                    event.target.value = "";
                  }}
                />
                <button
                  onClick={() => fileInputs.current[course.code]?.click()}
                  disabled={busyAction !== null}
                  className="border border-line px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink disabled:opacity-40"
                >
                  {busyAction === `upload-${course.code}` ? "UPLOADING…" : "+ FILES"}
                </button>
                <button
                  onClick={() => generate("guide", course.code)}
                  disabled={busyAction !== null || course.materials === 0}
                  className="border border-line px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink disabled:opacity-40"
                >
                  {busyAction === `guide-${course.code}` ? "WRITING…" : "GUIDE"}
                </button>
                <button
                  onClick={() => generate("exam", course.code)}
                  disabled={busyAction !== null || course.materials === 0}
                  className="border border-line px-2.5 py-1 font-mono text-[10px] uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink disabled:opacity-40"
                >
                  {busyAction === `exam-${course.code}` ? "GENERATING…" : "+ EXAM"}
                </button>
                <Link
                  href={`/study/course/${encodeURIComponent(course.code)}`}
                  className="ml-auto font-mono text-[10px] uppercase tracking-wide text-[var(--ac)] transition-colors hover:opacity-80"
                >
                  HUB →
                </Link>
              </div>
            </div>
          );
        })}
      </div>
    </Panel>
  );
}
