/**
 * /study/flashcards [PREVIEW] seed data (§8 flags.flashcards, §9 file plan).
 * Static only — no network calls. Real decks land as `Q:: A::` pairs in
 * `15-Courses/<CODE>/flashcards.md` per the spec once this ships for real.
 */
export interface Flashcard {
  id: string;
  front: string;
  back: string;
}

export const SEED_CARDS: Flashcard[] = [
  { id: "c1", front: "What is Big-O of binary search?", back: "O(log n)" },
  {
    id: "c2",
    front: "CAP theorem — what do the three letters stand for?",
    back: "Consistency, Availability, Partition tolerance — a distributed system can only guarantee two.",
  },
  { id: "c3", front: "SOLID: what does the 'S' stand for?", back: "Single Responsibility Principle" },
  {
    id: "c4",
    front: "Define a race condition.",
    back: "A bug where the outcome depends on the non-deterministic timing of concurrent operations.",
  },
  { id: "c5", front: "Average-case time complexity of quicksort?", back: "O(n log n)" },
];

export type Grade = "again" | "hard" | "good" | "easy";

/** Mock SRS interval per grade — the schedule a real spaced-repetition
 * scheduler would compute; here it's just what the toast reports. */
export const GRADE_INTERVAL: Record<Grade, string> = {
  again: "10m",
  hard: "1d",
  good: "3d",
  easy: "6d",
};

export const GRADE_LABEL: Record<Grade, string> = {
  again: "AGAIN",
  hard: "HARD",
  good: "GOOD",
  easy: "EASY",
};
