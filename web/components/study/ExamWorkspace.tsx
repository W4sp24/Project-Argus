"use client";

import { useState } from "react";
import Panel from "@/components/Panel";
import { useToast } from "@/components/Toast";
import { apiFetch, fetcher, useStudyCourses, useStudyExams } from "@/lib/api";

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
 * /study/exam workspace (§4): real exam data end-to-end —
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

  const [quiz, setQuiz] = useState<{ examId: number; questions: QuizQuestion[] } | null>(null);
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
      body: JSON.stringify({ course: genCourse, n: 10 }),
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
    setQuiz({ examId, questions });
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
      <Panel label="RESULTS" headerRight={<span className="font-mono text-[11px] text-ink-muted">{result.score} / {result.total}</span>}>
        <p className="mb-4 text-[13px] text-ink-muted">
          {result.weak_topics.length
            ? "Missed topics were added to the review queue in the vault."
            : "Perfect score — nothing added to the review queue."}
        </p>
        <div className="space-y-3">
          {result.feedback.map((item, i) => {
            const options = quiz?.questions[i]?.options ?? null;
            return (
              <div key={i} className="border border-line p-3">
                <p className="mb-2 text-[14px] text-ink">{item.q}</p>
                {options ? (
                  <div className="grid gap-1.5">
                    {options.map((option) => {
                      const isYours = option === item.your_answer;
                      const isCorrectOption = option === item.correct_answer;
                      const border = isCorrectOption
                        ? "border-ok text-ok"
                        : isYours && !item.correct
                          ? "border-danger text-danger"
                          : "border-line text-ink-muted";
                      return (
                        <div key={option} className={`border px-3 py-1.5 text-[13px] ${border}`}>
                          {option}
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className={`text-[13px] ${item.correct ? "text-ok" : "text-danger"}`}>
                    {item.correct ? "✓ " : "✗ "}
                    {item.your_answer || "(no answer)"}
                    {!item.correct && <span className="text-ink-muted"> — correct: {item.correct_answer}</span>}
                  </p>
                )}
                {item.explanation && <p className="mt-2 text-[12.5px] text-ink-muted">{item.explanation}</p>}
                {item.citation && (
                  <p className="mt-2 font-mono text-[11px] text-[var(--ac)]">{`⌗ ${item.citation}`}</p>
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
          className="mt-4 border border-line px-4 py-2 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi"
        >
          BACK TO EXAMS
        </button>
      </Panel>
    );
  }

  // ---- Quiz in progress ----
  if (quiz) {
    const question = quiz.questions[current];
    const answered = answers[current] !== "";
    const pct = Math.round(((current + 1) / quiz.questions.length) * 100);
    return (
      <Panel label="PRACTICE.EXAM" headerRight={<span className="font-mono text-[11px] text-ink-muted">Q{current + 1}/{quiz.questions.length}</span>}>
        <div className="mb-4 h-1 w-full bg-sunken">
          <div className="h-1 bg-[var(--ac)] transition-[width]" style={{ width: `${pct}%` }} />
        </div>
        <p className="mb-5 font-body text-[17px] text-ink-bright">{question.q}</p>
        {question.options ? (
          <div className="grid gap-2">
            {question.options.map((option, i) => {
              const letter = "ABCDEFGH"[i];
              const selected = answers[current] === option;
              return (
                <button
                  key={option}
                  onClick={() => setAnswers((prev) => prev.map((a, j) => (j === current ? option : a)))}
                  className={`border px-4 py-2.5 text-left text-[13.5px] transition-colors ${
                    selected ? "border-[var(--ac)] bg-[var(--ac-bg)] text-ink" : "border-line text-ink-muted hover:border-lineHi"
                  }`}
                >
                  <span className="mr-2 font-mono text-xs text-[var(--ac)]">{letter})</span>
                  {option}
                </button>
              );
            })}
          </div>
        ) : (
          <input
            value={answers[current]}
            onChange={(event) => setAnswers((prev) => prev.map((a, j) => (j === current ? event.target.value : a)))}
            placeholder="Type your answer"
            className="w-full border border-line bg-sunken px-4 py-2.5 text-[13.5px] placeholder:text-ink-faint focus:border-lineHi focus:outline-none"
          />
        )}
        <div className="mt-6 flex items-center justify-between">
          <button
            onClick={() => setCurrent((v) => Math.max(0, v - 1))}
            disabled={current === 0}
            className="border border-line px-4 py-2 font-mono text-[11px] uppercase tracking-wide text-ink-muted transition-colors hover:border-lineHi disabled:opacity-40"
          >
            ← PREV
          </button>
          {current < quiz.questions.length - 1 ? (
            <button
              onClick={() => setCurrent((v) => v + 1)}
              disabled={!answered}
              className="border border-[var(--ac)] px-4 py-2 font-mono text-[11px] uppercase tracking-wide text-[var(--ac)] transition-opacity hover:opacity-80 disabled:opacity-40"
            >
              NEXT →
            </button>
          ) : (
            <button
              onClick={submitQuiz}
              disabled={!answered}
              className="border border-[var(--ac)] bg-[var(--ac-bg)] px-4 py-2 font-mono text-[11px] uppercase tracking-wide text-[var(--ac)] transition-opacity hover:opacity-80 disabled:opacity-40"
            >
              GRADE ME
            </button>
          )}
        </div>
      </Panel>
    );
  }

  // ---- Exam list / generate ----
  return (
    <Panel label="PRACTICE.EXAM">
      <form onSubmit={generateExam} className="mb-4 flex flex-wrap items-center gap-2 border-b border-line pb-4">
        <select
          value={genCourse}
          onChange={(event) => setGenCourse(event.target.value)}
          className="border border-line bg-sunken px-2 py-1.5 font-mono text-[12px] text-ink focus:border-lineHi focus:outline-none"
        >
          <option value="">select course…</option>
          {(courses ?? []).map((course) => (
            <option key={course.code} value={course.code}>
              {course.code}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={!genCourse || generating}
          className="border border-line px-3 py-1.5 font-mono text-[11px] uppercase tracking-wide text-ink transition-colors hover:border-lineHi disabled:opacity-40"
        >
          {generating ? "GENERATING…" : "+ GENERATE EXAM"}
        </button>
      </form>

      {!exams || exams.length === 0 ? (
        <p className="text-[13px] text-ink-faint">
          No exams yet — generate one above (needs a course with uploaded materials).
        </p>
      ) : (
        <ul className="space-y-2">
          {exams.map((exam) => (
            <li key={exam.id}>
              <button
                onClick={() => startQuiz(exam.id)}
                className="flex w-full items-center justify-between border border-line px-4 py-3 text-left transition-colors hover:border-lineHi"
              >
                <span className="min-w-0">
                  <span className="block truncate text-[13.5px] text-ink">{exam.title}</span>
                  <span className="font-mono text-[11px] text-ink-faint">
                    {exam.course} · {exam.questions} questions
                  </span>
                </span>
                <span className="shrink-0 font-mono text-[11px] uppercase tracking-wide text-[var(--ac)]">
                  TAKE →
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
