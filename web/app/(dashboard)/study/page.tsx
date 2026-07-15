"use client";

import { useRef, useState, type DragEvent } from "react";
import useSWR from "swr";
import GlassCard from "@/components/GlassCard";
import PageHeader from "@/components/PageHeader";
import { fetcher } from "@/lib/api";

interface CourseInfo {
  code: string;
  title: string;
  materials: number;
  notes: number;
}

interface ExamSummary {
  id: number;
  course: string;
  title: string;
  created_at: string;
  questions: number;
}

interface QuizQuestion {
  q: string;
  type: string;
  options: string[] | null;
}

interface Feedback {
  q: string;
  your_answer: string;
  correct_answer: string;
  correct: boolean;
  explanation: string;
  citation: string;
}

interface AttemptResult {
  score: number;
  total: number;
  feedback: Feedback[];
  weak_topics: string[];
}

export default function StudyPage() {
  const { data: courses, mutate: refreshCourses } = useSWR<CourseInfo[]>(
    "/api/study/courses",
    fetcher,
  );
  const { data: exams, mutate: refreshExams } = useSWR<ExamSummary[]>("/api/study/exams", fetcher);

  const [status, setStatus] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [dragOverCourse, setDragOverCourse] = useState<string | null>(null);
  const fileInputs = useRef<Record<string, HTMLInputElement | null>>({});

  const ACCEPTED_EXTENSIONS = [".pdf", ".pptx", ".docx", ".md"];

  function isAcceptedFile(file: File) {
    const name = file.name.toLowerCase();
    return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
  }

  // Quiz state
  const [quiz, setQuiz] = useState<{ examId: number; questions: QuizQuestion[] } | null>(null);
  const [answers, setAnswers] = useState<string[]>([]);
  const [current, setCurrent] = useState(0);
  const [result, setResult] = useState<AttemptResult | null>(null);

  async function upload(course: string, file: File) {
    if (!isAcceptedFile(file)) {
      setStatus(`"${file.name}" isn't a supported type — use ${ACCEPTED_EXTENSIONS.join(", ")}.`);
      return;
    }
    setBusyAction(`upload-${course}`);
    setStatus(null);
    const body = new FormData();
    body.append("course", course);
    body.append("file", file);
    const response = await fetch("/api/study/upload", { method: "POST", body });
    const payload = await response.json();
    setStatus(
      response.ok
        ? `Saved ${payload.path} — indexing in the background.`
        : `Upload failed: ${payload.detail}`,
    );
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
    setStatus(`Generating ${kind} for ${course} — this can take a few minutes…`);
    const response = await fetch(`/api/study/${kind}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(kind === "guide" ? { course } : { course, n: 10 }),
    });
    const payload = await response.json();
    if (!response.ok) {
      setStatus(`${kind} failed: ${payload.detail}`);
    } else if (kind === "exam") {
      setStatus(`Exam ready: ${payload.questions} cited questions → ${payload.path}`);
      refreshExams();
    } else {
      setStatus(`Study guide written to ${payload.path}`);
    }
    setBusyAction(null);
  }

  async function startQuiz(examId: number) {
    const questions: QuizQuestion[] = await fetcher(`/api/study/exams/${examId}`);
    setQuiz({ examId, questions });
    setAnswers(Array(questions.length).fill(""));
    setCurrent(0);
    setResult(null);
  }

  async function submitQuiz() {
    if (!quiz) return;
    const response = await fetch(`/api/study/exams/${quiz.examId}/attempt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers }),
    });
    setResult(await response.json());
    setQuiz(null);
  }

  // ---- Quiz mode view ----
  if (quiz) {
    const question = quiz.questions[current];
    const answered = answers[current] !== "";
    return (
      <>
        <PageHeader
          label="QUIZ"
          title={`Question ${current + 1} of ${quiz.questions.length}`}
          subtitle="Citations are revealed with your results."
        />
        <GlassCard>
          <p className="mb-4 text-base text-ink">{question.q}</p>
          {question.options ? (
            <div className="grid gap-2">
              {question.options.map((option, i) => {
                const letter = "ABCDEFGH"[i];
                const selected = answers[current] === option;
                return (
                  <button
                    key={option}
                    onClick={() =>
                      setAnswers((prev) => prev.map((a, j) => (j === current ? option : a)))
                    }
                    className={`rounded-xl border px-4 py-2.5 text-left text-sm transition-colors ${
                      selected
                        ? "border-primary-soft/60 bg-primary/20 text-ink"
                        : "border-white/10 bg-white/[0.03] text-ink-muted hover:border-primary-soft/30"
                    }`}
                  >
                    <span className="mr-2 font-mono text-xs text-primary-soft">{letter})</span>
                    {option}
                  </button>
                );
              })}
            </div>
          ) : (
            <input
              value={answers[current]}
              onChange={(event) =>
                setAnswers((prev) => prev.map((a, j) => (j === current ? event.target.value : a)))
              }
              placeholder="Type your answer"
              className="w-full rounded-xl border border-white/10 bg-white/5 px-4 py-2.5 text-sm placeholder:text-ink-faint focus:border-primary-soft/50 focus:outline-none"
            />
          )}
          <div className="mt-6 flex items-center justify-between">
            <button
              onClick={() => setCurrent((value) => Math.max(0, value - 1))}
              disabled={current === 0}
              className="rounded-xl px-4 py-2 text-sm text-ink-muted hover:bg-white/5 disabled:opacity-40"
            >
              ← Back
            </button>
            {current < quiz.questions.length - 1 ? (
              <button
                onClick={() => setCurrent((value) => value + 1)}
                disabled={!answered}
                className="rounded-xl bg-gradient-to-r from-primary to-accent px-5 py-2 font-display text-sm text-white disabled:opacity-40"
              >
                Next →
              </button>
            ) : (
              <button
                onClick={submitQuiz}
                disabled={!answered}
                className="rounded-xl bg-gradient-to-r from-primary to-accent px-5 py-2 font-display text-sm text-white disabled:opacity-40"
              >
                Grade me
              </button>
            )}
          </div>
        </GlassCard>
      </>
    );
  }

  // ---- Results view ----
  if (result) {
    return (
      <>
        <PageHeader
          label="RESULTS"
          title={`${result.score} / ${result.total}`}
          subtitle={
            result.weak_topics.length
              ? "Missed topics were added to your review queue in the vault."
              : "Perfect score — nothing added to the review queue."
          }
        />
        <div className="space-y-3">
          {result.feedback.map((item, i) => (
            <GlassCard key={i} className={item.correct ? "" : "border-accent/30"}>
              <p className="mb-2 text-sm text-ink">{item.q}</p>
              <p className="text-sm">
                <span className={item.correct ? "text-signal" : "text-accent"}>
                  {item.correct ? "✓ " : "✗ "}
                  {item.your_answer || "(no answer)"}
                </span>
                {!item.correct && (
                  <span className="text-ink-muted"> — correct: {item.correct_answer}</span>
                )}
              </p>
              {item.explanation && (
                <p className="mt-2 text-sm text-ink-muted">{item.explanation}</p>
              )}
              <p className="mt-2 font-mono text-[11px] text-primary-soft">{item.citation}</p>
            </GlassCard>
          ))}
          <button
            onClick={() => setResult(null)}
            className="rounded-xl bg-gradient-to-r from-primary to-accent px-5 py-2 font-display text-sm text-white"
          >
            Back to courses
          </button>
        </div>
      </>
    );
  }

  // ---- Courses view ----
  return (
    <>
      <PageHeader
        label="STUDY"
        title="Course hub"
        subtitle="Drop slides and syllabi into a course — Argus turns them into study guides and cited practice exams."
      />

      {status && (
        <p className="mb-4 rounded-xl border border-white/10 bg-white/[0.04] px-4 py-3 text-sm text-ink-muted">
          {status}
        </p>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        {(courses ?? []).map((course) => (
          <GlassCard
            key={course.code}
            label={course.code}
            title={course.title}
            className={
              dragOverCourse === course.code
                ? "border-primary-soft/60 bg-primary/10 ring-1 ring-primary-soft/40"
                : ""
            }
            onDragOver={(event) => handleDragOver(course.code, event)}
            onDragLeave={(event) => handleDragLeave(course.code, event)}
            onDrop={(event) => handleDrop(course.code, event)}
          >
            <p className="mb-4 font-mono text-[11px] text-ink-faint">
              {course.materials} material{course.materials === 1 ? "" : "s"} · {course.notes} note
              {course.notes === 1 ? "" : "s"}
              {dragOverCourse === course.code && (
                <span className="ml-2 text-primary-soft">— drop to upload</span>
              )}
            </p>
            <div className="flex flex-wrap gap-2">
              <input
                ref={(el) => {
                  fileInputs.current[course.code] = el;
                }}
                type="file"
                accept=".pdf,.pptx,.docx,.md"
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
                className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2 text-sm text-ink-muted transition-colors hover:border-primary-soft/30 hover:text-ink disabled:opacity-40"
              >
                {busyAction === `upload-${course.code}` ? "Uploading…" : "Upload material"}
              </button>
              <button
                onClick={() => generate("guide", course.code)}
                disabled={busyAction !== null || course.materials === 0}
                className="rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2 text-sm text-ink-muted transition-colors hover:border-primary-soft/30 hover:text-ink disabled:opacity-40"
              >
                {busyAction === `guide-${course.code}` ? "Writing…" : "Study guide"}
              </button>
              <button
                onClick={() => generate("exam", course.code)}
                disabled={busyAction !== null || course.materials === 0}
                className="rounded-xl bg-gradient-to-r from-primary/80 to-accent/70 px-4 py-2 font-display text-sm text-white transition-opacity hover:opacity-90 disabled:opacity-40"
              >
                {busyAction === `exam-${course.code}` ? "Generating…" : "Practice exam"}
              </button>
            </div>
          </GlassCard>
        ))}
        {courses && courses.length === 0 && (
          <GlassCard label="EMPTY" title="No courses yet">
            <p className="text-sm text-ink-muted">
              Create a folder like <span className="font-mono text-xs">15-Courses/CS201/</span>{" "}
              with a <span className="font-mono text-xs">course.md</span> in your vault.
            </p>
          </GlassCard>
        )}
      </div>

      {exams && exams.length > 0 && (
        <section className="mt-8">
          <p className="eyebrow mb-3">{`// PRACTICE EXAMS`}</p>
          <div className="space-y-2">
            {exams.map((exam) => (
              <button
                key={exam.id}
                onClick={() => startQuiz(exam.id)}
                className="flex w-full items-center justify-between border border-line bg-panel px-4 py-3 text-left transition-colors hover:border-lineHi"
              >
                <span className="min-w-0">
                  <span className="block truncate text-sm">{exam.title}</span>
                  <span className="font-mono text-[11px] text-ink-faint">
                    {exam.course} · {exam.questions} questions
                  </span>
                </span>
                <span className="shrink-0 rounded-full bg-primary/20 px-3 py-1 font-mono text-[11px] text-primary-soft">
                  Take quiz →
                </span>
              </button>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
