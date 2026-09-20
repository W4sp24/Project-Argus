"use client";

import Link from "next/link";
import { useRef, useState, type DragEvent } from "react";
import NotebookPanel from "@/components/notebook/NotebookPanel";
import { useToast } from "@/components/Toast";
import { useConfirm } from "@/components/ui/useConfirm";
import {
  ApiError,
  apiFetch,
  mutateJSON,
  useDueSummary,
  useFlashcardDecks,
  useStudyCourses,
  useStudyExams,
  useVault,
} from "@/lib/api";
import { useJobs } from "@/lib/jobs";
import { selectedModel } from "@/lib/models";
import { useNextExamByCourse, useWeakTopics } from "@/lib/useStudySignals";

const ACCEPTED_EXTENSIONS = [".pdf", ".pptx", ".docx", ".md"];
const SAFE_CODE_RE = /^[A-Za-z0-9._-]+$/;

/** The quiet action buttons on a course row. One string, because there are
 *  three of them and they must not drift apart. */
const ROW_ACTION =
  "rounded-ctl border border-nb-line bg-nb-panel px-3 py-2 text-ctl text-nb-ink transition-colors hover:border-nb-lineHi disabled:opacity-70";

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
  const { data: decks } = useFlashcardDecks();
  const { data: dueSummary } = useDueSummary();
  const weakTopics = useWeakTopics();
  const examsByCourse = useNextExamByCourse((courses ?? []).map((course) => course.code));
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

  // Uploads only. A held-open multipart POST genuinely is this component's
  // business; a generation is not, and used to share this flag -- which is why
  // one running guide disabled every button on every course.
  const [uploading, setUploading] = useState<string | null>(null);
  const [dragOverCourse, setDragOverCourse] = useState<string | null>(null);
  const { track, isBusy } = useJobs();

  /** Is a generation of this kind running for this course? */
  const busy = (kind: string, course: string) =>
    isBusy((job) => job.kind === kind && job.params?.course === course);

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
    setUploading(course);
    const body = new FormData();
    body.append("course", course);
    body.append("file", file);
    const response = await apiFetch("/api/study/upload", { method: "POST", body });
    const payload = await response.json();
    show(response.ok ? `saved ${payload.path} — indexing in the background` : `upload failed: ${payload.detail}`);
    setUploading(null);
    refreshCourses();
  }

  function handleDragOver(course: string, event: DragEvent) {
    event.preventDefault();
    if (uploading !== null) return;
    setDragOverCourse(course);
  }
  function handleDragLeave(course: string, event: DragEvent) {
    event.preventDefault();
    if (dragOverCourse === course) setDragOverCourse(null);
  }
  function handleDrop(course: string, event: DragEvent) {
    event.preventDefault();
    setDragOverCourse(null);
    if (uploading !== null) return;
    const file = event.dataTransfer.files?.[0];
    if (file) upload(course, file);
  }

  /**
   * Queue a generation for one course and hand its id to the registry.
   *
   * `background: true` is what stops this being a fetch held open for minutes
   * whose only progress state was `busyAction`, a local of a component that
   * unmounts the moment you leave /notebook. The backend has accepted the flag
   * since the job store was generalised; nothing sent it.
   *
   * The generation is *not* scoped to a source selection here, unlike the
   * Course Hub's STUDIO: this panel has no SOURCES rail, so the whole course
   * is the corpus. Omitting `sources` is what asks for that.
   */
  async function generate(kind: "guide" | "exam", course: string) {
    // Study generation honours the same model selection chat uses (§7); the
    // backend falls back to the registry default when this is omitted.
    const model = selectedModel();
    const response = await apiFetch(`/api/study/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(
        kind === "guide"
          ? { course, model, background: true }
          : { course, n: 10, model, background: true },
      ),
    });
    const payload = await response.json();
    if (!response.ok) {
      show(`${kind} failed: ${payload.detail}`, { tone: "error" });
      return;
    }
    track(payload.job_id, { kind, params: { course } });
    show(`${kind} queued for ${course} — it keeps running if you leave this tab`);
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
      <NotebookPanel
        heading="Courses"
        headerRight={
          <button
            type="button"
            aria-expanded={showAddForm}
            onClick={() => setShowAddForm((v) => !v)}
            className="text-ctl text-[var(--ac)] transition-opacity hover:opacity-80"
          >
            ＋ Add course
          </button>
        }
      >
        {showAddForm && (
          <form
            onSubmit={addCourse}
            className="mb-4 flex flex-wrap items-center gap-2 rounded-tile border border-dashed border-nb-line p-3"
          >
            <input
              value={addCode}
              onChange={(event) => setAddCode(event.target.value)}
              placeholder="CODE (e.g. CS301)"
              aria-label="Course code"
              className="w-36 rounded-ctl border border-nb-line bg-nb-void px-3 py-2 font-mono text-label text-nb-ink placeholder:text-nb-faint focus:border-nb-lineHi"
            />
            <input
              value={addName}
              onChange={(event) => setAddName(event.target.value)}
              placeholder="Course name"
              aria-label="Course name"
              className="min-w-0 flex-1 rounded-ctl border border-nb-line bg-nb-void px-3 py-2 text-body text-nb-ink placeholder:text-nb-faint focus:border-nb-lineHi"
            />
            <button
              type="submit"
              disabled={!addCode.trim() || !addName.trim() || creating || !coursesDir}
              className="rounded-ctl bg-[var(--ac)] px-4 py-2 text-ctl font-semibold text-nb-onAc transition-opacity hover:opacity-90 disabled:opacity-70"
            >
              {creating ? "Adding…" : "Add"}
            </button>
          </form>
        )}

        {visible.length === 0 && (
          <p className="text-body text-nb-faint">
            No courses yet — create a folder like{" "}
            <span className="font-mono text-label text-nb-body">{coursesDir ?? "…"}/CS201/</span>{" "}
            with a <span className="font-mono text-label text-nb-body">course.md</span> in your
            vault.
          </p>
        )}

        {/* A list, not a stack of divs: a course row is reached by role in the
            e2e suite, and `getByRole("listitem")` is the only stable handle a
            card built out of nested divs can offer. */}
        <ul className="flex flex-col gap-2.5">
          {visible.map((course) => {
            const chips = weakTopics.filter((topic) => topic.course === course.code).slice(0, 4);
            const courseDecks = (decks ?? []).filter((deck) => deck.course === course.code);
            const dueHere = (dueSummary?.decks ?? [])
              .filter((deck) => deck.course === course.code)
              .reduce((sum, deck) => sum + deck.due, 0);
            const examIn = examsByCourse[course.code];
            const empty = course.materials === 0 && course.notes === 0;
            const dragging = dragOverCourse === course.code;
            return (
              <li
                key={course.code}
                onDragOver={(event) => handleDragOver(course.code, event)}
                onDragLeave={(event) => handleDragLeave(course.code, event)}
                onDrop={(event) => handleDrop(course.code, event)}
                className={`rounded-tile border p-4 transition-colors ${
                  dragging
                    ? "border-[var(--ac)] bg-nb-acBg"
                    : empty
                      ? "border-dashed border-nb-line"
                      : "border-nb-line bg-nb-raised hover:border-nb-lineHi"
                }`}
              >
                <div className="flex flex-wrap items-baseline gap-2.5">
                  <span className="font-mono text-meta font-semibold tracking-[0.06em] text-[var(--ac)]">
                    {course.code}
                  </span>
                  <span className="min-w-0 truncate text-lead font-semibold text-nb-ink">
                    {course.title}
                  </span>
                  {examIn !== undefined && (
                    <span className="rounded-full bg-nb-dangerBg px-2.5 py-0.5 text-meta font-semibold text-danger">
                      {examIn === 0
                        ? "Exam today"
                        : `Exam in ${examIn} day${examIn === 1 ? "" : "s"}`}
                    </span>
                  )}
                  <button
                    aria-label={`Delete ${course.code}`}
                    onClick={() => void removeCourse(course.code)}
                    className="ml-auto shrink-0 text-nb-faint transition-colors hover:text-danger"
                  >
                    ×
                  </button>
                </div>

                <p className="mt-1.5 text-ctl text-nb-body">
                  {empty
                    ? "No materials yet — drop a lecture here and Argus can write a guide, a deck or an exam from it."
                    : [
                        `${course.materials} material${course.materials === 1 ? "" : "s"}`,
                        `${course.notes} note${course.notes === 1 ? "" : "s"}`,
                        courseDecks.length > 0 &&
                          `${courseDecks.length} deck${courseDecks.length === 1 ? "" : "s"}`,
                        dueHere > 0 && `${dueHere} card${dueHere === 1 ? "" : "s"} due`,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                  {dragging && <span className="ml-2 text-[var(--ac)]">— drop to upload</span>}
                </p>

                {chips.length > 0 && (
                  <div className="mt-2.5 flex flex-wrap gap-1.5">
                    {chips.map((chip) => (
                      <span
                        key={chip.topic}
                        className="rounded-full border border-nb-dangerLine px-2.5 py-0.5 text-label text-danger"
                      >
                        {chip.topic}
                      </span>
                    ))}
                  </div>
                )}

                <div className="mt-3.5 flex flex-wrap items-center gap-2">
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
                    disabled={uploading !== null}
                    className={
                      empty
                        ? "rounded-ctl border border-[var(--ac)] bg-nb-acBg px-3 py-2 text-ctl font-semibold text-[var(--ac)] transition-opacity hover:opacity-80 disabled:opacity-70"
                        : ROW_ACTION
                    }
                  >
                    {uploading === course.code
                      ? "Uploading…"
                      : empty
                        ? "Add your first file"
                        : "Add files"}
                  </button>
                  {!empty && (
                    <>
                      <button
                        onClick={() => generate("guide", course.code)}
                        disabled={busy("guide", course.code) || course.materials === 0}
                        className={ROW_ACTION}
                      >
                        {busy("guide", course.code) ? "Writing…" : "Study guide"}
                      </button>
                      <button
                        onClick={() => generate("exam", course.code)}
                        disabled={busy("exam", course.code) || course.materials === 0}
                        className={ROW_ACTION}
                      >
                        {busy("exam", course.code) ? "Generating…" : "Practice exam"}
                      </button>
                    </>
                  )}
                  <Link
                    href={`/notebook/course/${encodeURIComponent(course.code)}`}
                    className="ml-auto text-ctl font-semibold text-[var(--ac)] transition-opacity hover:opacity-80"
                  >
                    Open hub →
                  </Link>
                </div>
              </li>
            );
          })}
        </ul>
      </NotebookPanel>
      {confirmDialog}
    </>
  );
}
