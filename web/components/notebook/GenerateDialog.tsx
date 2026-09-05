"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import IngestDialog from "@/components/sources/IngestDialog";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import SegmentedControl from "@/components/ui/SegmentedControl";
import {
  generateDeck,
  generateDeckFromUpload,
  mutateJSON,
  useCourseSources,
  useGenerateOptions,
  useStudyCourses,
} from "@/lib/api";
import { useJobs } from "@/lib/jobs";
import { selectedModel } from "@/lib/models";

export type GenerateKind = "deck" | "exam";

const DIFFICULTY_BLURB: Record<string, string> = {
  easy: "one fact per card, answerable in a few words",
  medium: "short explanations — more than a definition",
  hard: "combines facts, or applies them to a new case",
};

const STYLE_LABEL: Record<string, string> = {
  definition: "Definition",
  concept: "Concept",
  cloze: "Cloze",
  application: "Application",
};

const STYLE_BLURB: Record<string, string> = {
  definition: "a term on the front, its meaning on the back",
  concept: "a why or how question with a short explanation",
  cloze: "a sentence with one key part blanked out",
  application: "a short scenario you have to apply the material to",
};

/** Where a deck's material comes from, when there is no SOURCES rail to read. */
type SourceMode = "course" | "pick" | "upload";

const MODES = ["course", "pick", "upload"] as const;
const MODE_LABELS: Record<SourceMode, string> = {
  course: "WHOLE COURSE",
  pick: "PICK SOURCES",
  upload: "MY OWN FILE",
};

/** Fallback only — the real list is served by `/generate/options`. */
const FALLBACK_SUFFIXES = [".pdf", ".pptx", ".docx", ".md"];

/** Remembered between sessions — a preference, which is what localStorage is for. */
const STORAGE_KEY = "argus-generate-options";

interface Remembered {
  difficulty: string;
  styles: string[];
  n: number;
  instructions: string;
}

function load(fallback: Remembered): Remembered {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return fallback;
    const saved = JSON.parse(raw) as Partial<Remembered>;
    return {
      difficulty: typeof saved.difficulty === "string" ? saved.difficulty : fallback.difficulty,
      styles: Array.isArray(saved.styles) ? saved.styles : fallback.styles,
      n: typeof saved.n === "number" ? saved.n : fallback.n,
      instructions: typeof saved.instructions === "string" ? saved.instructions : "",
    };
  } catch {
    // Private browsing, cleared site data, or a hand-edited value. Defaults are
    // a fine answer; a broken dialog is not.
    return fallback;
  }
}

/** "15-Courses/CS201/materials/lecture-04.pdf" → "lecture-04". */
function stem(path: string): string {
  const name = path.split("/").pop() ?? path;
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(0, dot) : name;
}

/**
 * Generation, with the dials on it — and, where there is no rail, with a say in
 * what it reads.
 *
 * Every deck used to come out the same shape whatever you needed it for:
 * difficulty, card kind and any instruction of your own were hardcoded inside
 * the prompt. Quizlet's AI tools customise along exactly these three axes, and
 * they are the ones that change what you get.
 *
 * One component serves decks and exams because they differ only in which
 * fields show and where the request goes — and because the exam half needed no
 * backend work at all: `/api/study/exam` has accepted `difficulty` and
 * `topics` since it was written, and nothing has ever sent them, so every exam
 * has silently generated at "medium" with no focus.
 *
 * **The `sources` prop decides the dialog's shape.** An array means the caller
 * has a SOURCES rail and has already answered "from what" — the Course Hub —
 * so the dialog only states the scope. `undefined`/`null` means nobody asked,
 * which was the deck library's whole problem: it opened this dialog with a
 * course dropdown and a line promising to read the *entire* course, with no
 * control anywhere to narrow it and no way at all to point at a file that is
 * not in the vault. That case now gets the SOURCE section: the whole course,
 * the files you tick, or one you hand over that Argus reads once and never
 * keeps.
 *
 * The vocabulary — difficulties, card styles, and what an upload may be —
 * comes from `GET /api/flashcards/generate/options` rather than a second copy
 * here, so the dialog cannot offer a value the server rejects.
 */
export default function GenerateDialog({
  kind,
  course: fixedCourse,
  sources,
  onClose,
}: {
  kind: GenerateKind;
  /** Set when opened from a Course Hub; omitted from the deck library. */
  course?: string;
  /** The SOURCES-rail ticks, when there is a rail to read them from. */
  sources?: string[] | null;
  onClose: () => void;
}) {
  const { show } = useToast();
  const { track } = useJobs();
  const { data: options } = useGenerateOptions();
  const { data: courses } = useStudyCourses();

  const [course, setCourse] = useState(fixedCourse ?? "");
  const [difficulty, setDifficulty] = useState("medium");
  const [styles, setStyles] = useState<string[]>(["definition", "concept"]);
  const [count, setCount] = useState(kind === "deck" ? 20 : 10);
  const [instructions, setInstructions] = useState("");
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);

  // The SOURCE section's state. Only read when there is no rail.
  const [mode, setMode] = useState<SourceMode>("course");
  const [picked, setPicked] = useState<string[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [alsoSave, setAlsoSave] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  /** Set after a queued upload when the user asked to keep the file too. */
  const [saving, setSaving] = useState<File | null>(null);

  // A rail answers "from what" before this dialog opens. Without one, the
  // dialog has to ask — which is the whole point of the SOURCE section.
  const hasRail = Array.isArray(sources);
  const picking = !hasRail && kind === "deck";

  const { data: courseSources } = useCourseSources(picking ? course : "");
  const selectable = useMemo(
    // `study` is Argus's own output — guides and exam markdown — and feeding it
    // back in as a source is a loop, not context. Same exclusion the rail makes.
    () => (courseSources ?? []).filter((source) => source.zone !== "study"),
    [courseSources],
  );

  // Once, and only once.
  //
  // This waits for the server's defaults so a first run shows the real ones
  // rather than this component's guess. But the options request is in flight
  // while the dialog is already interactive, so without the guard a choice
  // made in that window is silently reverted the moment it lands — which is
  // exactly what an e2e test caught: HARD clicked, "medium" sent.
  const hydrated = useRef(false);
  useEffect(() => {
    if (!options || hydrated.current) return;
    hydrated.current = true;
    const remembered = load({
      difficulty: options.default_difficulty,
      styles: options.default_styles,
      n: kind === "deck" ? 20 : 10,
      instructions: "",
    });
    setDifficulty(remembered.difficulty);
    setStyles(remembered.styles);
    setCount(remembered.n);
    setInstructions(remembered.instructions);
  }, [options, kind]);

  // A selection belongs to the course it was made in. Leaving it behind would
  // send another course's paths, which `course_corpus` drops on the floor —
  // and the deck would come back empty for no stated reason.
  useEffect(() => {
    setPicked([]);
  }, [course]);

  function toggleStyle(style: string) {
    setStyles((current) =>
      current.includes(style) ? current.filter((s) => s !== style) : [...current, style],
    );
  }

  function togglePicked(path: string) {
    setPicked((current) =>
      current.includes(path) ? current.filter((p) => p !== path) : [...current, path],
    );
  }

  const accepted = options?.upload_suffixes ?? FALLBACK_SUFFIXES;
  const maxUploadBytes = options?.max_upload_bytes ?? 20 * 1024 * 1024;

  /**
   * Take one dropped or browsed file.
   *
   * Refused here as well as by the server, because a 20MB round trip to be told
   * "that is not a PDF" is a worse way to learn it. The server still checks.
   */
  function takeFile(candidate: File | undefined) {
    if (!candidate) return;
    const name = candidate.name.toLowerCase();
    if (!accepted.some((extension) => name.endsWith(extension))) {
      show(`"${candidate.name}" isn't a kind Argus can read — use ${accepted.join(", ")}`, {
        tone: "error",
      });
      return;
    }
    if (candidate.size > maxUploadBytes) {
      show(
        `"${candidate.name}" is larger than ${Math.round(maxUploadBytes / (1024 * 1024))} MB`,
        { tone: "error" },
      );
      return;
    }
    setFile(candidate);
    setMode("upload");
  }

  /** What the deck is called if you do not name it. Shown, so it is correctable. */
  const suggestedTitle = useMemo(() => {
    if (!picking) return "";
    if (mode === "upload") return file ? stem(file.name) : "";
    if (mode === "pick") {
      if (picked.length === 1) return stem(picked[0]);
      if (picked.length > 1) return `${course} — ${picked.length} sources`;
      return "";
    }
    return course ? `${course} — ${difficulty}` : "";
  }, [picking, mode, file, picked, course, difficulty]);

  async function submit() {
    const uploading = picking && mode === "upload";
    if (!uploading && !course) return;
    setBusy(true);
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({ difficulty, styles, n: count, instructions }),
      );
    } catch {
      // Not remembering the settings is not a reason to refuse to generate.
    }
    try {
      if (kind === "deck") {
        const chosenTitle = title.trim() || suggestedTitle;
        if (uploading && file) {
          const { job_id } = await generateDeckFromUpload({
            file,
            course,
            title: chosenTitle,
            model: selectedModel(),
            n: count,
            difficulty,
            styles,
            instructions,
          });
          track(job_id);
          // Keeping the file is a vault write, so it goes through the normal
          // ingest flow rather than being smuggled in on the generate request.
          if (alsoSave && course) {
            show("flashcard deck queued — now say where to keep the file");
            setSaving(file);
            setBusy(false);
            return;
          }
        } else {
          const { job_id } = await generateDeck({
            course,
            sources: picking ? (mode === "pick" ? picked : null) : (sources ?? null),
            model: selectedModel(),
            n: count,
            difficulty,
            styles,
            instructions,
            title: chosenTitle || null,
          });
          track(job_id);
        }
      } else {
        const { job_id } = await mutateJSON<{ job_id: string }>("/api/study/exam", {
          course,
          sources: sources ?? null,
          model: selectedModel(),
          n: count,
          difficulty,
          // The exam's existing field for "what should this be about" — the
          // same box, wired to the name the backend already has.
          topics: instructions.trim() || null,
          background: true,
        });
        track(job_id);
      }
      show(
        `${kind === "deck" ? "flashcard deck" : "practice exam"} queued — it keeps running if you leave this tab`,
      );
      onClose();
    } catch (error) {
      show(
        `could not start: ${error instanceof Error ? error.message : "backend offline?"}`,
        { tone: "error" },
      );
    } finally {
      setBusy(false);
    }
  }

  const difficulties = options?.difficulties ?? ["easy", "medium", "hard"];
  const noStyles = kind === "deck" && styles.length === 0;
  const materialsPath = (courses ?? []).find((entry) => entry.code === course)?.materials_path;
  const needsSource =
    picking &&
    ((mode === "pick" && picked.length === 0) ||
      (mode === "upload" && !file) ||
      // Asked to keep the file, but named nowhere to keep it. Silently not
      // saving it would be the worst of the three possible behaviours.
      (mode === "upload" && alsoSave && !course) ||
      (mode !== "upload" && !course));
  const blocked = noStyles || busy || needsSource || (!picking && !course);

  // The file is queued and the user asked to keep it. One dialog at a time:
  // stacking generate under ingest would leave two Escape targets and two
  // GENERATE-shaped buttons on screen.
  if (saving) {
    return (
      <IngestDialog
        initialFiles={[saving]}
        lockedTarget={materialsPath}
        defaultNoteStyle=""
        onStarted={(jobId) => track(jobId)}
        onClose={onClose}
      />
    );
  }

  return (
    <Dialog
      label={kind === "deck" ? "Generate a flashcard deck" : "Generate a practice exam"}
      onClose={onClose}
      align="center"
      className="w-[min(40rem,92vw)] p-5"
    >
      {/* The whole dialog is a drop target, not just the zone — dropping onto
          whichever part happens to be under the cursor is what people do, and
          landing on a dead surface reads as "this app can't do that". */}
      <div
        onDragOver={
          picking
            ? (event) => {
                event.preventDefault();
                setDragOver(true);
              }
            : undefined
        }
        onDragLeave={picking ? () => setDragOver(false) : undefined}
        onDrop={
          picking
            ? (event) => {
                event.preventDefault();
                setDragOver(false);
                takeFile(event.dataTransfer.files?.[0]);
              }
            : undefined
        }
        className={dragOver ? "outline outline-1 outline-[var(--ac)]" : ""}
      >
        {picking && (
          <div className="mb-4">
            <p className="mb-1 font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Source
            </p>
            <SegmentedControl
              options={MODES}
              labels={MODE_LABELS}
              value={mode}
              onChange={setMode}
            />
          </div>
        )}

        {!fixedCourse && (
          <label className="mb-4 block">
            <span className="mb-1 block font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Course{picking && mode === "upload" ? " (optional)" : ""}
            </span>
            <select
              value={course}
              onChange={(event) => setCourse(event.target.value)}
              aria-label="Course"
              className="min-h-9 w-full border border-line bg-sunken px-2 py-1.5 font-mono text-label text-ink focus:border-lineHi"
            >
              <option value="">select a course…</option>
              {(courses ?? []).map((entry) => (
                <option key={entry.code} value={entry.code}>
                  {entry.code}
                </option>
              ))}
            </select>
            {picking && mode === "upload" && (
              // Optional, but not pointless: it is what files the deck under a
              // course, which is what puts it in that course's DECKS panel.
              <span className="mt-1 block font-mono text-micro text-ink-faint">
                a deck can be about a file rather than a course — naming one
                just files it there
              </span>
            )}
          </label>
        )}

        {picking && mode === "pick" && (
          <div className="mb-4">
            {!course ? (
              <p className="text-label text-ink-faint">Choose a course to see its files.</p>
            ) : !courseSources ? (
              <p className="text-label text-ink-faint">Reading this course…</p>
            ) : selectable.length === 0 ? (
              <p className="text-label text-ink-faint">
                Nothing in {course} yet. Ingest a lecture from Sources, or hand one over with
                MY OWN FILE.
              </p>
            ) : (
              <>
                <ul className="max-h-56 space-y-1 overflow-auto border border-line p-2">
                  {selectable.map((source) => (
                    <li key={source.path}>
                      {/* The whole row is the control, and `aria-label` fixes the
                          accessible name so it cannot pick up the chunk count. */}
                      <button
                        type="button"
                        role="checkbox"
                        aria-checked={picked.includes(source.path)}
                        aria-label={`Use ${source.title} as a source`}
                        onClick={() => togglePicked(source.path)}
                        className="flex w-full items-start gap-2 border border-transparent px-2 py-1.5 text-left transition-colors hover:border-lineHi"
                      >
                        <span
                          aria-hidden
                          className={`mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center border transition-colors ${
                            picked.includes(source.path)
                              ? "border-[var(--ac)] bg-[var(--ac)] text-void"
                              : "border-line"
                          }`}
                        >
                          {picked.includes(source.path) && "✓"}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-label text-ink">
                            {source.title}
                          </span>
                          <span className="mt-0.5 block font-mono text-meta text-ink-faint">
                            {source.kind}
                            {source.chunks === null
                              ? " · not indexed"
                              : ` · ${source.chunks} chunk${source.chunks === 1 ? "" : "s"}`}
                          </span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
                <p className="mt-1 font-mono text-micro text-ink-faint">
                  {picked.length === 0
                    ? "Tick at least one file."
                    : `reads ${picked.length} of ${selectable.length}`}
                  {" · a file that is not indexed has nothing to read yet"}
                </p>
              </>
            )}
          </div>
        )}

        {picking && mode === "upload" && (
          <div className="mb-4">
            {/* A label wrapping a hidden input: clickable, focusable, and
                announced — everything a bare div listening for `drop` is not,
                and what lets one code path serve the pointer and the keyboard. */}
            <label
              className={`flex min-h-28 cursor-pointer flex-col items-center justify-center gap-1 border border-dashed p-5 text-center transition-colors ${
                dragOver ? "border-[var(--ac)] bg-[var(--ac-bg)]" : "border-line hover:border-lineHi"
              }`}
            >
              <input
                type="file"
                accept={accepted.join(",")}
                className="hidden"
                onChange={(event) => {
                  takeFile(event.target.files?.[0]);
                  event.target.value = "";
                }}
              />
              <span className="font-mono text-label uppercase tracking-[0.12em] text-ink">
                {file ? `▍${file.name}` : "drop a file, or click to browse"}
              </span>
              <span className="font-mono text-meta text-ink-faint">
                {accepted.join(" · ")} — read once for this deck, never stored
              </span>
            </label>
            <label className="mt-2 flex items-start gap-2">
              <input
                type="checkbox"
                checked={alsoSave}
                onChange={(event) => setAlsoSave(event.target.checked)}
                className="mt-1 h-3.5 w-3.5 shrink-0 accent-[var(--ac)]"
              />
              <span className="min-w-0">
                <span className="block text-label text-ink">Also keep this file</span>
                <span className="block font-mono text-micro text-ink-faint">
                  {course
                    ? `saves and indexes it under ${course} — opens the ingest dialog once the deck is queued`
                    : "pick a course above to say where it should be saved"}
                </span>
              </span>
            </label>
          </div>
        )}

        <div className="mb-4">
          <p className="mb-1 font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
            Difficulty
          </p>
          <SegmentedControl
            options={difficulties}
            labels={Object.fromEntries(difficulties.map((d) => [d, d.toUpperCase()]))}
            value={difficulty}
            onChange={setDifficulty}
          />
          <p className="mt-1 font-mono text-micro text-ink-faint">
            {DIFFICULTY_BLURB[difficulty] ?? ""}
          </p>
        </div>

        {kind === "deck" && (
          <fieldset className="mb-4">
            <legend className="mb-1 font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Card types
            </legend>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {(options?.styles ?? []).map((style) => (
                <label
                  key={style}
                  className="flex cursor-pointer items-start gap-2 border border-line px-2 py-1.5 transition-colors hover:border-lineHi"
                >
                  <input
                    type="checkbox"
                    checked={styles.includes(style)}
                    onChange={() => toggleStyle(style)}
                    className="mt-1 h-3.5 w-3.5 shrink-0 accent-[var(--ac)]"
                  />
                  <span className="min-w-0">
                    <span className="block text-label text-ink">{STYLE_LABEL[style] ?? style}</span>
                    <span className="block font-mono text-micro text-ink-faint">
                      {STYLE_BLURB[style] ?? ""}
                    </span>
                  </span>
                </label>
              ))}
            </div>
            {noStyles && (
              <p className="mt-1 font-mono text-meta text-warn">Pick at least one card type.</p>
            )}
          </fieldset>
        )}

        <label className="mb-4 block">
          <span className="mb-1 block font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
            How many
          </span>
          <input
            type="number"
            min={1}
            max={options?.max_cards ?? 60}
            value={count}
            onChange={(event) => setCount(Number(event.target.value) || 1)}
            className="min-h-9 w-24 border border-line bg-sunken px-2 py-1.5 font-mono text-label text-ink focus:border-lineHi"
          />
        </label>

        {/* Deck name, not "deck title": the library's create form already owns
            that label, and two elements answering one page-level query is a
            strict-mode failure in every test that types into either. */}
        {kind === "deck" && (
          <label className="mb-4 block">
            <span className="mb-1 block font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
              Deck name (optional)
            </span>
            <input
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              aria-label="Deck name"
              placeholder={suggestedTitle || "named after what it reads"}
              className="min-h-9 w-full border border-line bg-sunken px-2 py-1.5 font-body text-body text-ink focus:border-lineHi"
            />
          </label>
        )}

        <label className="block">
          <span className="mb-1 block font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
            {kind === "deck" ? "Your instructions (optional)" : "Focus on (optional)"}
          </span>
          <textarea
            value={instructions}
            onChange={(event) => setInstructions(event.target.value)}
            rows={3}
            maxLength={options?.max_instructions ?? 600}
            placeholder={
              kind === "deck"
                ? "e.g. keep answers under ten words, use my professor's terminology"
                : "e.g. dynamic programming and greedy algorithms"
            }
            className="w-full border border-line bg-sunken px-2 py-1.5 font-body text-body text-ink focus:border-lineHi"
          />
        </label>
        <p className="mt-1 font-mono text-micro text-ink-faint">
          {hasRail
            ? sources === null
              ? "reads the whole course"
              : `reads the ${sources?.length ?? 0} source${sources?.length === 1 ? "" : "s"} you have ticked`
            : mode === "course"
              ? "reads everything indexed under the course"
              : mode === "pick"
                ? "reads only the files you tick"
                : "reads the file you hand over, once"}
          {" · settings are remembered"}
        </p>

        <div className="mt-4 flex justify-end gap-2">
          <Button variant="quiet" onClick={onClose}>
            CANCEL
          </Button>
          <Button disabled={blocked} onClick={() => void submit()}>
            {busy ? "STARTING…" : "GENERATE"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
