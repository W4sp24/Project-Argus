"use client";

import { useState } from "react";
import Markdown from "@/components/Markdown";
import NotebookPanel from "@/components/notebook/NotebookPanel";
import { useToast } from "@/components/Toast";
import { apiFetch, fetcher, useStudyCourses, useStudyExams } from "@/lib/api";
import { selectedModel } from "@/lib/models";

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

/**
 * /notebook/exam workspace (§4): real exam data end-to-end —
 * `GET /api/study/exams` lists generated exams, `GET /api/study/exams/{id}`
 * fetches quiz questions (no answers), `POST /api/study/exams/{id}/attempt`
 * grades the whole attempt in one call (backend/study/grader.py). Grading is
 * NOT preview: the endpoint is already wired and used by the pre-redesign
 * page, so no `GRADING: PREVIEW` badge is shown (deviation from the spec's
 * default assumption — see final report).
 */
export default function ExamWorkspace() {
  const { data: courses } = useStudyCourses();
  const { data: exams, mutate: refreshExams } = useStudyExams();
  const { show } = useToast();

  const [genCourse, setGenCourse] = useState("");
  const [generating, setGenerating] = useState(false);

  const [quiz, setQuiz] = useState<{
    examId: number;
    title: string;
    course: string;
    questions: QuizQuestion[];
  } | null>(null);
  const [answers, setAnswers] = useState<string[]>([]);
  const [current, setCurrent] = useState(0);
  const [result, setResult] = useState<AttemptResult | null>(null);

  async function generateExam(event: React.FormEvent) {
    event.preventDefault();
    if (!genCourse) return;
    setGenerating(true);
    show(`generating exam for ${genCourse} — this can take a few minutes…`);
    const response = await apiFetch("/api/study/exam", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // Same model selection chat uses (§7); omitted means the registry default.
      body: JSON.stringify({ course: genCourse, n: 10, model: selectedModel() }),
    });
    const payload = await response.json();
    setGenerating(false);
    if (!response.ok) {
      show(`exam generation failed: ${payload.detail}`);
      return;
    }
    show(`exam ready: ${payload.questions} cited questions`);
    refreshExams();
  }

  async function startQuiz(examId: number) {
    const questions = await fetcher<QuizQuestion[]>(`/api/study/exams/${examId}`);
    // Carried on the quiz so the header can name the exam you are sitting.
    // `exams` is the same SWR list the picker rendered, so this costs nothing.
    const summary = exams?.find((exam) => exam.id === examId);
    setQuiz({
      examId,
      title: summary?.title ?? "Practice exam",
      course: summary?.course ?? "",
      questions,
    });
    setAnswers(Array(questions.length).fill(""));
    setCurrent(0);
    setResult(null);
  }

  async function submitQuiz() {
    if (!quiz) return;
    const response = await apiFetch(`/api/study/exams/${quiz.examId}/attempt`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ answers }),
    });
    const payload: AttemptResult = await response.json();
    setResult(payload);
  }

  // ---- Results ----
  if (result) {
    return (
      <NotebookPanel
        heading="Results"
        headerRight={
          <span className="font-mono text-lead font-semibold tabular-nums text-nb-ink">
            {result.score}
            <span className="text-nb-faint"> / {result.total}</span>
          </span>
        }
      >
        <p className="mb-4 text-body text-nb-body">
          {result.weak_topics.length
            ? "Missed topics were added to the review queue in the vault."
            : "Perfect score — nothing added to the review queue."}
        </p>
        <div className="space-y-3">
          {result.feedback.map((item, i) => {
            const options = quiz?.questions[i]?.options ?? null;
            return (
              <div key={i} className="rounded-tile border border-nb-line p-4">
                <Markdown text={item.q} className="mb-2.5 text-body font-medium text-nb-ink" />
                {options ? (
                  <div className="grid gap-1.5">
                    {options.map((option) => {
                      const isYours = option === item.your_answer;
                      const isCorrectOption = option === item.correct_answer;
                      const border = isCorrectOption
                        ? "border-nb-okLine bg-nb-okBg text-ok"
                        : isYours && !item.correct
                          ? "border-nb-dangerLine bg-nb-dangerBg text-danger"
                          : "border-nb-line text-nb-body";
                      return (
                        <div
                          key={option}
                          className={`rounded-ctl border px-3.5 py-2 text-body ${border}`}
                        >
                          <Markdown text={option} inline className="text-body" />
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className={`text-body ${item.correct ? "text-ok" : "text-danger"}`}>
                    {item.correct ? "✓ " : "✗ "}
                    {item.your_answer || "(no answer)"}
                    {!item.correct && (
                      <span className="text-nb-body">
                        {" — correct: "}
                        <Markdown text={item.correct_answer} inline className="text-body" />
                      </span>
                    )}
                  </p>
                )}
                {item.explanation && (
                  <Markdown text={item.explanation} className="mt-2.5 text-label text-nb-body" />
                )}
                {item.citation && (
                  <p className="mt-2.5 inline-block rounded-full border border-nb-line px-2.5 py-0.5 text-meta text-[var(--ac)]">
                    {item.citation}
                  </p>
                )}
              </div>
            );
          })}
        </div>
        <button
          onClick={() => {
            setResult(null);
            setQuiz(null);
          }}
          className="mt-5 rounded-ctl border border-nb-line px-4 py-2.5 text-ctl text-nb-ink transition-colors hover:border-nb-lineHi"
        >
          Back to exams
        </button>
      </NotebookPanel>
    );
  }

  // ---- Quiz in progress ----
  if (quiz) {
    const question = quiz.questions[current];
    const answered = answers[current] !== "";
    const answeredCount = answers.filter((answer) => answer !== "").length;
    return (
      <NotebookPanel
        heading={quiz.course ? `${quiz.title} · ${quiz.course}` : quiz.title}
        headerRight={
          <span className="font-mono text-label font-semibold tabular-nums text-nb-ink">
            {current + 1}
            <span className="text-nb-faint"> / {quiz.questions.length}</span>
          </span>
        }
      >
        {/* Three segments, not one fill: answered, this one, and the rest.
            "Where am I" and "how much is left" are different questions. */}
        <div className="mb-7 flex h-1.5 gap-[3px]" aria-hidden>
          <span className="rounded-bar bg-[var(--ac)]" style={{ flex: Math.max(answeredCount, 0.001) }} />
          <span className="rounded-bar bg-nb-lineHi" style={{ flex: 1 }} />
          <span
            className="rounded-bar bg-nb-track"
            style={{ flex: Math.max(quiz.questions.length - answeredCount - 1, 0.001) }}
          />
        </div>
        <Markdown
          text={question.q}
          className="mb-6 font-body text-lead font-medium leading-relaxed text-nb-ink"
        />
        {question.options ? (
          <div className="grid gap-2.5">
            {question.options.map((option, i) => {
              const letter = "ABCDEFGH"[i];
              const selected = answers[current] === option;
              return (
                <button
                  key={option}
                  onClick={() => setAnswers((prev) => prev.map((a, j) => (j === current ? option : a)))}
                  className={`flex items-center gap-3 rounded-tile border px-4 py-3.5 text-left text-read transition-colors ${
                    selected
                      ? "border-[var(--ac)] bg-nb-acBg text-nb-ink"
                      : "border-nb-line text-nb-body hover:border-nb-lineHi"
                  }`}
                >
                  <span
                    className={`font-mono text-label font-semibold ${
                      selected ? "text-[var(--ac)]" : "text-nb-faint"
                    }`}
                  >
                    {letter}
                  </span>
                  <Markdown text={option} inline className="text-read" />
                </button>
              );
            })}
          </div>
        ) : (
          <input
            value={answers[current]}
            onChange={(event) => setAnswers((prev) => prev.map((a, j) => (j === current ? event.target.value : a)))}
            placeholder="Type your answer"
            aria-label="Your answer"
            className="min-h-12 w-full rounded-ctl border border-nb-line bg-nb-void px-4 py-3 text-read text-nb-ink placeholder:text-nb-faint focus:border-[var(--ac)]"
          />
        )}
        {/* The question palette. The artboard puts this in the right rail; it
            lives with the question instead, because the quiz state is owned
            here and lifting it into a provider to move a ten-button grid three
            hundred pixels is a poor trade. It does the same job either way:
            see what is answered, and jump. */}
        <div className="mt-7 border-t border-nb-line pt-5">
          <div className="flex flex-wrap gap-2">
            {quiz.questions.map((_, index) => {
              const isAnswered = answers[index] !== "";
              const isCurrent = index === current;
              return (
                <button
                  key={index}
                  type="button"
                  aria-label={`Question ${index + 1}${isAnswered ? ", answered" : ""}`}
                  aria-current={isCurrent}
                  onClick={() => setCurrent(index)}
                  className={`min-h-9 w-10 rounded-ctl font-mono text-label font-semibold transition-colors ${
                    isCurrent
                      ? "border border-[var(--ac)] bg-nb-raised text-nb-ink"
                      : isAnswered
                        ? "bg-nb-acBg text-[var(--ac)]"
                        : "border border-nb-line text-nb-faint hover:border-nb-lineHi"
                  }`}
                >
                  {index + 1}
                </button>
              );
            })}
          </div>
          <p className="mt-3 text-label text-nb-faint">
            {answeredCount} answered · {quiz.questions.length - answeredCount} to go
          </p>
        </div>

        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            onClick={() => setCurrent((v) => Math.max(0, v - 1))}
            disabled={current === 0}
            className="rounded-ctl border border-nb-line px-4.5 py-3 text-body text-nb-body transition-colors hover:border-nb-lineHi disabled:opacity-40"
          >
            ← Previous
          </button>
          <span className="ml-auto text-ctl text-nb-faint">
            Answers are graded at the end, with citations.
          </span>
          {current < quiz.questions.length - 1 ? (
            <button
              onClick={() => setCurrent((v) => v + 1)}
              disabled={!answered}
              className="rounded-ctl bg-[var(--ac)] px-5 py-3 text-body font-semibold text-nb-onAc transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              Next →
            </button>
          ) : (
            <button
              onClick={submitQuiz}
              disabled={!answered}
              className="rounded-ctl bg-[var(--ac)] px-5 py-3 text-body font-semibold text-nb-onAc transition-opacity hover:opacity-90 disabled:opacity-40"
            >
              Grade me
            </button>
          )}
        </div>
      </NotebookPanel>
    );
  }

  // ---- Exam list / generate ----
  return (
    <NotebookPanel
      heading="Your exams"
      headerRight={
        <form onSubmit={generateExam} className="flex flex-wrap items-center gap-2.5">
          <label htmlFor="exam-course" className="sr-only">
            Course
          </label>
          <select
            id="exam-course"
            value={genCourse}
            onChange={(event) => setGenCourse(event.target.value)}
            aria-label="Course"
            className="min-h-10 rounded-ctl border border-nb-line bg-nb-void px-3 py-2 text-ctl text-nb-ink focus:border-nb-lineHi"
          >
            <option value="">Select a course…</option>
            {(courses ?? []).map((course) => (
              <option key={course.code} value={course.code}>
                {course.code}
              </option>
            ))}
          </select>
          <button
            type="submit"
            disabled={!genCourse || generating}
            className="min-h-10 rounded-ctl bg-[var(--ac)] px-4 py-2 text-ctl font-semibold text-nb-onAc transition-opacity hover:opacity-90 disabled:opacity-70"
          >
            {generating ? "Generating…" : "＋ Generate exam"}
          </button>
        </form>
      }
    >
      {!exams || exams.length === 0 ? (
        <p className="text-body text-nb-faint">
          No exams yet — generate one above. It needs a course with material you have
          ingested.
        </p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {exams.map((exam) => (
            <li key={exam.id}>
              <button
                onClick={() => startQuiz(exam.id)}
                className="flex w-full items-center justify-between gap-3 rounded-tile border border-nb-line bg-nb-raised px-4 py-3.5 text-left transition-colors hover:border-nb-lineHi"
              >
                <span className="min-w-0">
                  <span className="block truncate text-body font-medium text-nb-ink">
                    {exam.title}
                  </span>
                  <span className="text-label text-nb-faint">
                    {exam.course} · {exam.questions} questions · {exam.created_at.slice(0, 10)}
                  </span>
                </span>
                <span className="shrink-0 text-ctl font-semibold text-[var(--ac)]">Take →</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </NotebookPanel>
  );
}
