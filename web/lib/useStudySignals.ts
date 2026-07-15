"use client";

import useSWR from "swr";
import { fetcher, useNotes, useTasksBoard } from "@/lib/api";

const EXAM_KEYWORD_RE = /\b(exam|midterm|final|quiz)\b/i;

function parseLocalDate(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, (m ?? 1) - 1, d ?? 1);
}

/**
 * "Next exam" for the Study stat row: there's no exam-scheduling model in
 * the data (CourseInfo has no date, exams.created_at is when it was
 * generated, not when it's due) — but the syllabus importer
 * (backend/study/syllabus.py) turns lines like "Midterm: Oct 12" into task
 * *suggestions*; once approved they're ordinary vault tasks with a `due`
 * date. So the honest "next exam" signal is: the nearest upcoming, undone
 * task whose text mentions exam/midterm/final/quiz — derived from the real
 * `/api/tasks` board, not invented.
 */
export function useNextExam(): { text: string; days: number } | null {
  const { data: buckets } = useTasksBoard();
  if (!buckets) return null;

  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const candidates = Object.values(buckets)
    .flat()
    .filter((task) => !task.done && task.due && EXAM_KEYWORD_RE.test(task.text))
    .map((task) => ({ text: task.text, dueDate: parseLocalDate(task.due as string) }))
    .filter((task) => task.dueDate.getTime() >= today.getTime())
    .sort((a, b) => a.dueDate.getTime() - b.dueDate.getTime());

  if (candidates.length === 0) return null;
  const days = Math.round((candidates[0].dueDate.getTime() - today.getTime()) / 86_400_000);
  return { text: candidates[0].text, days };
}

export interface WeakTopic {
  course: string;
  topic: string;
}

const REVIEW_QUEUE_RE = /^15-Courses\/([^/]+)\/study\/review-queue\.md$/;

/**
 * Weak topics restyled from real data (§4 REVIEW.QUEUE): grade_attempt()
 * appends unchecked `- [ ] Review: {topic}` lines to
 * `15-Courses/<CODE>/study/review-queue.md` (backend/study/grader.py). No
 * endpoint aggregates these, so we find the review-queue notes via the real
 * `/api/notes` listing, then read each one's content via the real
 * `GET /api/note` and parse out the still-open topics. Read-only — never
 * writes.
 */
export function useWeakTopics(): WeakTopic[] {
  const { data: notes } = useNotes();
  const reviewPaths = (notes ?? [])
    .filter((note) => REVIEW_QUEUE_RE.test(note.path))
    .map((note) => note.path);

  const key = reviewPaths.length > 0 ? ["study-weak-topics", ...reviewPaths] : null;
  const { data } = useSWR<WeakTopic[]>(key, async () => {
    const contents = await Promise.all(
      reviewPaths.map((path) => fetcher<{ path: string; content: string }>(`/api/note?path=${encodeURIComponent(path)}`)),
    );
    const seen = new Set<string>();
    const topics: WeakTopic[] = [];
    for (const note of contents) {
      const match = REVIEW_QUEUE_RE.exec(note.path);
      const course = match ? match[1] : "?";
      for (const line of note.content.split("\n")) {
        const topicMatch = /^- \[ \] Review: (.+)$/.exec(line.trim());
        if (!topicMatch) continue;
        const dedupeKey = `${course}::${topicMatch[1]}`;
        if (seen.has(dedupeKey)) continue;
        seen.add(dedupeKey);
        topics.push({ course, topic: topicMatch[1].trim() });
      }
    }
    return topics;
  });

  return data ?? [];
}
