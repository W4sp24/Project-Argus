"use client";

import Link from "next/link";
import { useRef, useState, type DragEvent } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import { useConfirm } from "@/components/ui/useConfirm";
import { ApiError, apiFetch, mutateJSON, useStudyCourses, useStudyExams, useVault } from "@/lib/api";
import { selectedModel } from "@/lib/models";
import { useWeakTopics } from "@/lib/useStudySignals";

const ACCEPTED_EXTENSIONS = [".pdf", ".pptx", ".docx", ".md"];
const SAFE_CODE_RE = /^[A-Za-z0-9._-]+$/;

/** Mirrors the vault template's `CS000/course.md` (under the taxonomy's
 *  courses dir — see `useVault().courses_dir`, never a hardcoded folder
 *  name), interpolated with the submitted code/title and today's date (the
 *  template's `created: "{{date}}"` substitution normally done by the
 *  vault-init flow). */
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
 * (`<courses_dir>/<CODE>/course.md` hub notes). CRUD is honest about what the
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
 *    (backend/writer.py `create_note`), landing at
 *    `<courses_dir>/<CODE>/course.md` — `courses_dir` from `GET /api/vault`,
 *    never a hardcoded folder name (that literal was the taxonomy-refactor
 *    bug this branch is careful not to reintroduce). A 409 (code already
 *    exists) surfaces as a clear toast instead of a generic failure; success
 *    re-fetches `GET /api/study/courses` so the new course appears from the
 *    vault, not from local mock state.
 *  - `×` really deletes the course now: `useConfirm()` gates it (same
 *    danger-tone confirm `AgentUsage.tsx`'s "STOP TRACKING" uses), then
 *    `DELETE /api/study/courses/<CODE>?purge=true` removes the vault folder
 *    (git-snapshotted first, backend/vault/writer.py `delete_course_tree`)
 *    *and* the course's `exams`/`attempts`/`flashcard_decks`/`flashcard_reviews`
 *    rows (backend/features/study/deletes.py) in one call, then revalidates
 *    both `useStudyCourses()` and `useStudyExams()`. Previously this only
 *    ever set local `hidden` state — the row came back on the next reload,
 *    route change, or app restart, which was the reported "still retains
 *    sample data after it is deleted" bug.
 */
export default function CoursesPanel() {
  const { data: courses, mutate: refreshCourses } = useStudyCourses();
  const { mutate: refreshExams } = useStudyExams();
  const { data: vault } = useVault();
  const weakTopics = useWeakTopics();
  const { show } = useToast();
  const { confirm, confirmDialog } = useConfirm();
  // Taxonomy-derived — never hardcode the courses folder name (see corpus.py
  // module docs). Used only for the ADD COURSE write path and copy shown to
  // the user; a `courses` fallback is display-only and never sent anywhere
  // before `vault` has loaded (addCourse guards on it directly).
  const coursesDir = vault?.courses_dir;

  const [addCode, setAddCode] = useState("");
  const [addName, setAddName] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [creating, setCreating] = useState(false);

  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [dragOverCourse, setDragOverCourse] = useState<string | null>(null);
  const fileInputs = useRef<Record<string, HTMLInputElement | null>>({});

  const visible = courses ?? [];

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
    // Study generation honours the same model selection chat uses (§7); the
    // backend falls back to the registry default when this is omitted.
    const model = selectedModel();
    const response = await apiFetch(`/api/study/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(kind === "guide" ? { course, model } : { course, n: 10, model }),
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

  async function removeCourse(code: string) {
    const answer = await confirm({
      label: `Delete ${code}`,
      message: `Delete course ${code}?`,
      detail:
        `This removes ${coursesDir ?? "the course folder"}/${code}/ from the vault ` +
        "(a git snapshot makes it undoable) along with any exams and flashcard decks generated for it.",
      confirmLabel: "DELETE",
      tone: "danger",
    });
    if (answer === null) return;
    try {
      await mutateJSON(
        `/api/study/courses/${encodeURIComponent(code)}?purge=true`,
        undefined,
        "DELETE",
      );
      show(`course :: ${code} deleted`);
      refreshCourses();
      refreshExams();
    } catch (error) {
      show(`course :: delete failed — ${error instanceof Error ? error.message : "backend offline?"}`);
    }
  }

  async function addCourse(event: React.FormEvent) {
    event.preventDefault();
    const code = addCode.trim().toUpperCase();
    const title = addName.trim();
    if (!code || !title || !SAFE_CODE_RE.test(code) || creating || !coursesDir) return;

    setCreating(true);
    const path = `${coursesDir}/${code}/course.md`;
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
    <>
      <Panel
        label="COURSES"
        headerRight={
          <Button
            variant="ghost"
            aria-expanded={showAddForm}
            onClick={() => setShowAddForm((v) => !v)}
            className="hover:text-[var(--ac)]"
          >
            + ADD COURSE
          </Button>
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
              aria-label="Course code"
              className="w-36 border border-line bg-sunken px-2 py-1.5 font-mono text-label placeholder:text-ink-faint focus:border-lineHi"
            />
            <input
              value={addName}
              onChange={(event) => setAddName(event.target.value)}
              placeholder="Course name"
              aria-label="Course name"
              className="min-w-0 flex-1 border border-line bg-sunken px-2 py-1.5 text-body placeholder:text-ink-faint focus:border-lineHi"
            />
            <Button
              type="submit"
              size="md"
              disabled={!addCode.trim() || !addName.trim() || creating || !coursesDir}
            >
              {creating ? "ADDING…" : "ADD"}
            </Button>
          </form>
        )}

        {visible.length === 0 && (
          <p className="text-body text-ink-faint">
            No courses yet — create a folder like{" "}
            <span className="font-mono text-xs">{coursesDir ?? "…"}/CS201/</span> with a{" "}
            <span className="font-mono text-xs">course.md</span> in your vault.
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
                    <p className="flex items-center gap-1.5 font-mono text-meta uppercase tracking-wide text-ink-faint">
                      {course.code}
                    </p>
                    <p className="truncate text-lead font-medium text-ink-bright">{course.title}</p>
                  </div>
                  <button
                    aria-label={`Delete ${course.code}`}
                    onClick={() => void removeCourse(course.code)}
                    className="shrink-0 font-mono text-xs text-ink-faint transition-colors hover:text-danger"
                  >
                    ×
                  </button>
                </div>
                <p className="mt-1 font-mono text-label text-ink-faint">
                  {course.materials} material{course.materials === 1 ? "" : "s"} · {course.notes} note
                  {course.notes === 1 ? "" : "s"}
                  {dragOverCourse === course.code && <span className="ml-2 text-[var(--ac)]">— drop to upload</span>}
                </p>

                {chips.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {chips.map((chip) => (
                      <span
                        key={chip.topic}
                        className="border border-line px-1.5 py-0.5 font-mono text-meta text-ink-muted"
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
                    className="border border-line px-2.5 py-1 font-mono text-meta uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink disabled:opacity-70"
                  >
                    {busyAction === `upload-${course.code}` ? "UPLOADING…" : "+ FILES"}
                  </button>
                  <button
                    onClick={() => generate("guide", course.code)}
                    disabled={busyAction !== null || course.materials === 0}
                    className="border border-line px-2.5 py-1 font-mono text-meta uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink disabled:opacity-70"
                  >
                    {busyAction === `guide-${course.code}` ? "WRITING…" : "GUIDE"}
                  </button>
                  <button
                    onClick={() => generate("exam", course.code)}
                    disabled={busyAction !== null || course.materials === 0}
                    className="border border-line px-2.5 py-1 font-mono text-meta uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi hover:text-ink disabled:opacity-70"
                  >
                    {busyAction === `exam-${course.code}` ? "GENERATING…" : "+ EXAM"}
                  </button>
                  <Link
                    href={`/study/course/${encodeURIComponent(course.code)}`}
                    className="ml-auto font-mono text-meta uppercase tracking-wide text-[var(--ac)] transition-colors hover:opacity-80"
                  >
                    HUB →
                  </Link>
                </div>
              </div>
            );
          })}
        </div>
      </Panel>
      {confirmDialog}
    </>
  );
}
