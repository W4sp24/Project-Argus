"use client";

import { useEffect, useRef, useState } from "react";
import { useToast } from "@/components/Toast";
import Button from "@/components/ui/Button";
import Dialog from "@/components/ui/Dialog";
import SegmentedControl from "@/components/ui/SegmentedControl";
import {
  generateDeck,
  mutateJSON,
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

/**
 * Generation, with the dials on it.
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
 * The vocabulary comes from `GET /api/flashcards/generate/options` rather than
 * a second copy here, so the dialog cannot offer a value the server rejects.
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
  const [busy, setBusy] = useState(false);

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

  function toggleStyle(style: string) {
    setStyles((current) =>
      current.includes(style) ? current.filter((s) => s !== style) : [...current, style],
    );
  }

  async function submit() {
    if (!course) return;
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
        const { job_id } = await generateDeck({
          course,
          sources: sources ?? null,
          model: selectedModel(),
          n: count,
          difficulty,
          styles,
          instructions,
        });
        track(job_id);
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

  return (
    <Dialog
      label={kind === "deck" ? "Generate a flashcard deck" : "Generate a practice exam"}
      onClose={onClose}
      align="center"
      className="w-[min(40rem,92vw)] p-5"
    >
      {!fixedCourse && (
        <label className="mb-4 block">
          <span className="mb-1 block font-mono text-meta uppercase tracking-[0.12em] text-ink-faint">
            Course
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
        </label>
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
        {sources === null || sources === undefined
          ? "reads the whole course"
          : `reads the ${sources.length} source${sources.length === 1 ? "" : "s"} you have ticked`}
        {" · settings are remembered"}
      </p>

      <div className="mt-4 flex justify-end gap-2">
        <Button variant="quiet" onClick={onClose}>
          CANCEL
        </Button>
        <Button disabled={!course || noStyles || busy} onClick={() => void submit()}>
          {busy ? "STARTING…" : "GENERATE"}
        </Button>
      </div>
    </Dialog>
  );
}
