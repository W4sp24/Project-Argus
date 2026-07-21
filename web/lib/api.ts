"use client";

import useSWR from "swr";

/**
 * Backend base URL. In the browser (dev + `argus web`) this is "" and requests
 * stay relative, resolved by the Next rewrite in next.config.mjs. In the
 * desktop shell there is no rewrite — Next rewrites bake at build time, so a
 * dynamically-allocated backend port can't go through them — and Electron's
 * preload injects the real origin as `window.__ARGUS__`.
 */
interface ArgusBridge {
  apiBase: string;
  wsBase: string;
}

function bridge(): ArgusBridge | undefined {
  return (globalThis as { __ARGUS__?: ArgusBridge }).__ARGUS__;
}

export function apiBase(): string {
  return bridge()?.apiBase ?? "";
}

/** WebSocket origin for /ws/chat. Falls back to the dev backend on :8000. */
export function wsBase(): string {
  const injected = bridge()?.wsBase;
  if (injected) return injected;
  return `ws://${globalThis.location?.hostname ?? "127.0.0.1"}:8000`;
}

/**
 * `fetch` against the Argus backend. Always use this for `/api/*` and
 * `/health` — a bare `fetch("/api/...")` works in dev but silently 404s in
 * the packaged desktop app. Absolute URLs pass through untouched.
 */
export function apiFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(path.startsWith("/") ? apiBase() + path : path, init);
}

/** Shared JSON fetcher for SWR — throws on non-2xx so errors surface in hooks. */
export async function fetcher<T>(url: string): Promise<T> {
  const response = await apiFetch(url);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail ?? `Request failed: ${response.status}`);
  }
  return response.json();
}

export class ApiError extends Error {
  status: number;
  payload: unknown;
  constructor(status: number, payload: unknown, message: string) {
    super(message);
    this.status = status;
    this.payload = payload;
  }
}

/** JSON mutation helper — throws ApiError with the response payload on non-2xx. */
export async function mutateJSON<T>(
  url: string,
  body: unknown,
  method: "POST" | "PUT" | "DELETE" = "POST",
): Promise<T> {
  const response = await apiFetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = (payload as { detail?: unknown }).detail;
    const message =
      typeof detail === "string"
        ? detail
        : ((detail as { message?: string })?.message ?? `Request failed: ${response.status}`);
    throw new ApiError(response.status, payload, message);
  }
  return payload as T;
}

export interface NoteInfo {
  path: string;
  title: string;
  folder: string;
  modified: string;
}

/** All non-private notes in the vault, newest first. */
export function useNotes() {
  return useSWR<NoteInfo[]>("/api/notes", fetcher);
}

export interface VaultInfo {
  name: string;
}

/** Vault identity — used to build `obsidian://` deep links client-side. */
export function useVault() {
  return useSWR<VaultInfo>("/api/vault", fetcher);
}

export interface JournalProject {
  slug: string;
  title: string;
  updated: string;
  sessions: number;
  open_threads: number;
  path: string;
}

export interface JournalSession {
  date: string;
  project: string;
  branch: string | null;
  files: number;
  has_narrative: boolean;
  path: string;
}

export interface JournalNote {
  path: string;
  markdown: string;
  obsidian_uri: string;
}

/** Dev-journal projects (90-Meta/projects), most recently updated first. */
export function useJournalProjects() {
  return useSWR<JournalProject[]>("/api/journal/projects", fetcher);
}

/** Dev-journal sessions, newest first, optionally scoped to one project. */
export function useJournalSessions(project?: string) {
  const query = project ? `?project=${encodeURIComponent(project)}` : "";
  return useSWR<JournalSession[]>(`/api/journal/sessions${query}`, fetcher);
}

/** One journal note's raw markdown + obsidian:// deep link. */
export function useJournalNote(path?: string) {
  return useSWR<JournalNote>(
    path ? `/api/journal/note?path=${encodeURIComponent(path)}` : null,
    fetcher,
  );
}

export interface InsightsSummary {
  completion_trend: { date: string; completed: number }[];
  overdue: { date: string; count: number }[];
  calendar: { date: string; event_hours: number; focus_hours: number }[];
  study: { streak_days: number; courses: { course: string; attempts: { date: string; pct: number }[] }[] };
  configured: { gcal: boolean };
}

/** Insights rollup for stat tiles and charts. */
export function useInsights() {
  return useSWR<InsightsSummary>("/api/insights", fetcher);
}

export interface HeatmapDay {
  date: string;
  total: number;
  tasks: number;
  notes: number;
  study: number;
  captures: number;
}

/** 53 weeks of daily productivity events for the GitHub-style grid. */
export function useHeatmap() {
  return useSWR<{ days: HeatmapDay[] }>("/api/insights/heatmap", fetcher);
}

export interface ActivityEvent {
  when: string;
  kind: "note" | "approval" | "exam";
  title: string;
  path: string | null;
}

/** Latest vault edits, approvals, and exam attempts, newest first. */
export function useActivity() {
  return useSWR<ActivityEvent[]>("/api/activity", fetcher);
}

// --- Study (Phase D) ----------------------------------------------------

export interface CourseInfo {
  code: string;
  title: string;
  path: string;
  materials: number;
  notes: number;
}

/** Courses discovered under 15-Courses/ (each needs a course.md hub note). */
export function useStudyCourses() {
  return useSWR<CourseInfo[]>("/api/study/courses", fetcher);
}

export interface ExamSummary {
  id: number;
  course: string;
  title: string;
  created_at: string;
  questions: number;
}

/** Generated practice exams, optionally scoped to one course. */
export function useStudyExams(course?: string) {
  const query = course ? `?course=${encodeURIComponent(course)}` : "";
  return useSWR<ExamSummary[]>(`/api/study/exams${query}`, fetcher);
}

export interface TaskItem {
  text: string;
  done: boolean;
  due: string | null;
  scheduled: string | null;
  priority: string | null;
  tags: string[];
  source: string;
  path: string | null;
  line: number | null;
}

/** Full task board (overdue/today/week/someday buckets) — used to derive
 * study-adjacent signals (e.g. the next exam-flavored deadline) from real
 * vault tasks rather than inventing a scheduling model that doesn't exist. */
export function useTasksBoard() {
  return useSWR<Record<string, TaskItem[]>>("/api/tasks", fetcher);
}

// --- System (Phase H: usage, doctor, models) --------------------------------

export interface UsagePoint {
  label: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
}

export interface FeatureUsage {
  feature: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
}

export type UsageRange = "session" | "week" | "all";

export interface UsageReport {
  range: UsageRange;
  session_id: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  series: UsagePoint[];
  features: FeatureUsage[];
}

/** ARGUS.USAGE (§14) — GET /api/usage?range=session|week|all. */
export function useUsage(range: UsageRange) {
  return useSWR<UsageReport>(`/api/usage?range=${range}`, fetcher);
}

export interface CliModelUsage {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
}

export interface CliUsagePoint {
  label: string;
  total_tokens: number;
}

export type CliUsageRange = "today" | "week" | "all";

export interface CliUsageReport {
  range: CliUsageRange;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  series: CliUsagePoint[];
  models: CliModelUsage[];
}

/** CLAUDE CODE — account-wide CLI usage, GET /api/usage/cli?range=today|week|all. */
export function useCliUsage(range: CliUsageRange) {
  return useSWR<CliUsageReport>(`/api/usage/cli?range=${range}`, fetcher);
}

export interface DoctorCheck {
  name: string;
  status: "OK" | "WARN" | "FAIL";
  detail: string;
}

/**
 * DOCTOR (§12) — `POST /api/doctor` (not a GET, so the fetcher is inline).
 * Keyed by a fixed SWR key so DoctorPanel and SetupGuide share one result
 * set and one `mutate()` (RUN AGAIN) revalidates both.
 */
export function useDoctor() {
  return useSWR<DoctorCheck[]>("/api/doctor", () => mutateJSON<DoctorCheck[]>("/api/doctor", undefined));
}

export interface ModelInfo {
  name: string;
  provider: string;
  endpoint?: string | null;
  key_ref?: string | null;
  default: boolean;
  builtin: boolean;
}

/** Model registry (§7/§12) — GET /api/models. Built-ins first, then local. */
export function useModels() {
  return useSWR<ModelInfo[]>("/api/models", fetcher);
}

// --- Flashcards (real FSRS spaced repetition) --------------------------

export interface FlashcardDeck {
  id: number;
  course: string;
  title: string;
  created_at: string;
  cards: number;
}

export interface DueCard {
  id: string;
  front: string;
  back: string;
  due_at: string;
  state: string;
}

export type FlashcardGrade = "again" | "hard" | "good" | "easy";

export interface FlashcardGradeResult {
  card_id: string;
  grade: FlashcardGrade;
  stability: number;
  difficulty: number;
  due_at: string;
  state: string;
}

/** Generated flashcard decks, optionally scoped to one course. */
export function useFlashcardDecks(course?: string) {
  const query = course ? `?course=${encodeURIComponent(course)}` : "";
  return useSWR<FlashcardDeck[]>(`/api/flashcards/decks${query}`, fetcher);
}

/** Cards due for review in one deck, soonest-due first. */
export function useDueCards(deckId: number | null) {
  return useSWR<DueCard[]>(
    deckId !== null ? `/api/flashcards/decks/${deckId}/due` : null,
    fetcher,
  );
}

/** Parse `Q:: A::` pairs from `15-Courses/<CODE>/flashcards.md` into a new deck. */
export function generateFlashcardDeck(course: string) {
  return mutateJSON<FlashcardDeck>("/api/flashcards/decks", { course });
}

/** Grade one card — updates its FSRS state and schedules the next `due_at`. */
export function gradeFlashcard(deckId: number, cardId: string, grade: FlashcardGrade) {
  return mutateJSON<FlashcardGradeResult>(
    `/api/flashcards/decks/${deckId}/cards/${encodeURIComponent(cardId)}/grade`,
    { grade },
  );
}

// --- Search (command palette) -----------------------------------------

export interface SearchResult {
  snippet: string;
  source_path: string;
  title: string | null;
  score: number;
}

/** Standalone semantic search — hybrid vector+BM25 citations only, no chat
 * loop. GET /api/search?q=. Not an SWR hook: the palette calls this once per
 * keystroke/submit, not as a subscribed resource. */
export async function searchVault(query: string): Promise<SearchResult[]> {
  const q = query.trim();
  if (!q) return [];
  return fetcher<SearchResult[]>(`/api/search?q=${encodeURIComponent(q)}`);
}
