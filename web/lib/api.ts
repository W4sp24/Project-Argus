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
  method: "POST" | "PUT" | "PATCH" | "DELETE" = "POST",
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
  /** Only present when the request asked for it — see `useNotesIn`. */
  frontmatter?: Record<string, unknown>;
}

/** All non-private notes in the vault, newest first. */
export function useNotes() {
  return useSWR<NoteInfo[]>("/api/notes", fetcher);
}

/**
 * Notes under one vault folder (and its subfolders), with an opt-in
 * frontmatter whitelist attached to each — `GET /api/notes?folder=&fields=`.
 * Lets a listing like Research's paper queue read status/progress for every
 * paper in one request instead of one `GET /api/note` per paper. `folder`
 * null/undefined skips the fetch (SWR key null) — used while the folder path
 * is still loading from `useVault()`.
 */
export function useNotesIn(folder: string | null | undefined, fields: string[]) {
  const query = folder
    ? `/api/notes?folder=${encodeURIComponent(folder)}&fields=${fields.map(encodeURIComponent).join(",")}`
    : null;
  return useSWR<NoteInfo[]>(query, fetcher);
}

export interface VaultInfo {
  name: string;
  /** Where Research mode's one-note-per-paper reading queue lives, derived
   * server-side from the configured taxonomy (never hardcode `30-Areas`). */
  papers_dir: string;
  /** The single running highlights log Research mode appends to. */
  highlights_path: string;
  /** Where Study mode's course folders live — never hardcode `15-Courses`. */
  courses_dir: string;
}

/** Vault identity — used to build `obsidian://` deep links client-side. */
export function useVault() {
  return useSWR<VaultInfo>("/api/vault", fetcher);
}

export interface NoteContent {
  path: string;
  content: string;
}

/**
 * One note's raw content, or `null` if it doesn't exist yet (404) — for
 * notes created lazily on first use, like the research highlights log.
 */
export async function fetchNoteOrNull(path: string): Promise<string | null> {
  const response = await apiFetch(`/api/note?path=${encodeURIComponent(path)}`);
  if (response.status === 404) return null;
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body, body.detail ?? `Request failed: ${response.status}`);
  }
  const payload = (await response.json()) as NoteContent;
  return payload.content;
}

/**
 * Replace one note's content through the compare-and-swap `PUT /api/note`,
 * retrying against the current content a 409 (`WriterConflict`) carries
 * rather than clobbering whatever changed underneath — for read-modify-write
 * flows that don't own a note exclusively (status cycling, the append-only
 * highlights log).
 */
export async function updateNoteWithRetry(
  path: string,
  currentContent: string,
  transform: (content: string) => string,
  attempt = 0,
): Promise<string> {
  const next = transform(currentContent);
  try {
    await mutateJSON("/api/note", { path, expected_content: currentContent, new_content: next }, "PUT");
    return next;
  } catch (error) {
    if (error instanceof ApiError && error.status === 409 && attempt < 5) {
      const detail = (error.payload as { detail?: { current_content?: string } } | undefined)?.detail;
      if (typeof detail?.current_content === "string") {
        return updateNoteWithRetry(path, detail.current_content, transform, attempt + 1);
      }
    }
    throw error;
  }
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
  /** Taxonomy-derived write targets — upload here, never a hand-built path,
   * or the upload lands somewhere `materials`/`notes` above never counts. */
  materials_path: string;
  notes_path: string;
}

/** Courses discovered under the taxonomy's courses dir (each needs a
 * course.md hub note; see `useVault().courses_dir` for the folder itself). */
export function useStudyCourses() {
  return useSWR<CourseInfo[]>("/api/study/courses", fetcher);
}

export interface CourseSource {
  path: string;
  title: string;
  zone: "materials" | "notes" | "study";
  /** Uppercased file extension, e.g. "PDF", "MD", "PPTX". */
  kind: string;
  modified: string;
  /** Chunks in the live index for this file, or `null` when not indexed
   * (never `0` for "unknown" — that would misreport "indexed, empty"). */
  chunks: number | null;
}

/**
 * A course's real files (materials/notes/study), including non-markdown
 * material `GET /api/notes` can never see. Powers the Course Hub SOURCES
 * rail (§4) and its "generated" list (study guides live under the `study`
 * zone; exams/decks have their own endpoints below).
 */
export function useCourseSources(code: string) {
  return useSWR<CourseSource[]>(`/api/study/courses/${encodeURIComponent(code)}/sources`, fetcher);
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
  /**
   * Models that ran but have no published rate in Argus's table — a local
   * Ollama model, or a hosted provider whose prices Argus does not track.
   * They contribute nothing to `estimated_cost_usd`, so the figure is an
   * honest partial rather than a local model billed at Claude rates.
   */
  unpriced_models?: string[];
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

/** CLAUDE CODE only — GET /api/usage/cli. Superseded by `useAgentUsage`. */
export function useCliUsage(range: CliUsageRange) {
  return useSWR<CliUsageReport>(`/api/usage/cli?range=${range}`, fetcher);
}

export interface AgentModelUsage {
  model: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  /** Argus has no published rate for this model — the cost is a floor, not a bill. */
  unpriced: boolean;
}

export interface AgentUsagePoint {
  label: string;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
}

/** One agent's slice of the usage report — or the combined view, id `all`. */
export interface AgentUsage {
  id: string;
  label: string;
  /** Whether this agent is installed. False still carries zeroes, never an error. */
  detected: boolean;
  install_hint: string;
  /** False for user-registered sources — only those can be deleted. */
  builtin: boolean;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number;
  /** Total for the equivalent window before this one; null for the all-time range. */
  previous_total_tokens: number | null;
  series: AgentUsagePoint[];
  models: AgentModelUsage[];
  unpriced_models: string[];
  /**
   * Set when transcripts were found but none of them parsed. A zero we cannot
   * trust must not render as a confident zero — the Codex reader in particular
   * has never been validated against a real install.
   */
  unreadable?: string | null;
}

export interface AgentsUsageReport {
  range: CliUsageRange;
  /** One entry per known agent, installed or not — an absent tab reads as a bug. */
  agents: AgentUsage[];
  combined: AgentUsage;
}

/**
 * AGENT.USAGE — every local coding agent Argus can read, from
 * `GET /api/usage/agents?range=today|week|all`. Replaces `useCliUsage`, which
 * only ever saw Claude Code.
 */
export function useAgentUsage(range: CliUsageRange) {
  return useSWR<AgentsUsageReport>(`/api/usage/agents?range=${range}`, fetcher);
}

export interface AgentScanResult {
  ok: boolean;
  detail: string;
  format: string | null;
  format_label: string | null;
  glob: string;
  files: number;
  turns: number;
  total_tokens: number;
  models: string[];
  first_ts: string | null;
  last_ts: string | null;
  /** True when the preview read only part of the matched files. */
  sampled: boolean;
  sample_paths: string[];
}

/**
 * Look at a folder and report what is really in it, saving nothing — the
 * add-agent equivalent of `testModel`. Omitting `glob` means "work it out".
 */
export function scanAgentFolder(body: { path: string; glob?: string }) {
  return mutateJSON<AgentScanResult>("/api/usage/agents/scan", body);
}

export interface CustomAgentInfo {
  id: string;
  label: string;
  path: string;
  glob: string;
  format: string;
}

/** Register a folder as a tracked agent. The backend re-scans before saving. */
export function addCustomAgent(body: {
  name: string;
  path: string;
  glob?: string;
  format?: string;
}) {
  return mutateJSON<CustomAgentInfo>("/api/usage/agents/custom", body);
}

/** Remove a custom agent and purge every usage row that belonged to it. */
export function deleteCustomAgent(id: string) {
  return mutateJSON<{ status: string; id: string; rows_removed: number }>(
    `/api/usage/agents/custom/${encodeURIComponent(id)}`,
    undefined,
    "DELETE",
  );
}

/**
 * `needs-credentials`: a prerequisite file is missing, not just the consent.
 * `failing`: a credential IS stored but the connector can't actually answer
 * right now (e.g. this build is missing the client library) — see `error`.
 */
export type ConnectorStatus = "wired" | "not-connected" | "needs-credentials" | "failing";

export interface ConnectorInfo {
  id: string;
  name: string;
  status: ConnectorStatus;
  detail: string;
  can_connect: boolean;
  /** Set only when status === "failing". */
  error?: string | null;
}

export interface McpServerInfo {
  name: string;
  transport: "stdio" | "http";
  command?: string | null;
  args: string[];
  url?: string | null;
  /** Tool names captured when the server last passed a test. */
  tools: string[];
  /** A bearer token is stored for this server. The token itself never leaves the keyring (I4). */
  has_key: boolean;
  /** ...and whether the keyring could be read at all. See ModelInfo.key_state. */
  key_state: KeyState;
}

export interface IntegrationsResponse {
  connectors: ConnectorInfo[];
  mcp_servers: McpServerInfo[];
  /** False until registered MCP tools are callable from Argus chat. */
  mcp_tools_in_chat: boolean;
}

/** INTEGRATIONS (§12) — GET /api/integrations. */
export function useIntegrations() {
  return useSWR<IntegrationsResponse>("/api/integrations", fetcher);
}

export interface ConnectResult {
  ok: boolean;
  detail: string;
}

/** Verify a Todoist token against the API, then store it in the OS keyring. */
export function connectTodoist(token: string) {
  return mutateJSON<ConnectResult>("/api/integrations/todoist/connect", { token });
}

/** Save the Google OAuth *client* JSON so the consent flow has something to run. */
export function uploadGcalCredentials(credentials_json: string) {
  return mutateJSON<ConnectResult>("/api/integrations/gcal/credentials", { credentials_json });
}

/** Run the browser consent flow. Resolves once the redirect completes. */
export function connectGcal() {
  return mutateJSON<ConnectResult>("/api/integrations/gcal/connect", undefined);
}

/** Forget a connector's stored credential. */
export function disconnectIntegration(id: string) {
  return mutateJSON<ConnectResult>(`/api/integrations/${id}`, undefined, "DELETE");
}

export interface McpServerBody {
  name: string;
  transport?: "stdio" | "http";
  command?: string;
  args?: string[];
  url?: string;
  headers?: Record<string, string>;
  env?: Record<string, string>;
  token?: string;
  verify?: boolean;
}

export interface McpProbeResult {
  ok: boolean;
  detail: string;
  latency_ms: number;
  tools: string[];
}

/** Handshake with an MCP server and list its tools. Saves nothing. */
export function testMcpServer(body: McpServerBody) {
  return mutateJSON<McpProbeResult>("/api/integrations/mcp/test", body);
}

/** Register an MCP server. The backend verifies the handshake before it saves. */
export function addMcpServer(body: McpServerBody) {
  return mutateJSON<McpServerInfo>("/api/integrations/mcp", body);
}

export function deleteMcpServer(name: string) {
  return mutateJSON<ConnectResult>(
    `/api/integrations/mcp/${encodeURIComponent(name)}`,
    undefined,
    "DELETE",
  );
}

/** Copy-paste config exposing *Argus* to your coding agents (the other direction). */
export function useMcpSnippets() {
  return useSWR<Record<string, string>>("/api/integrations/mcp/snippets", fetcher);
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

/**
 * A registry provider (§7).
 * - `anthropic` — Claude through the Claude Code CLI, on your subscription.
 * - `anthropic-api` — Claude through an API key. No Claude Code needed.
 * - `openai-compat` — anything speaking the OpenAI chat API: Ollama on this
 *   PC, or a hosted provider like Groq/Together/Fireworks/OpenRouter.
 */
export type ModelProvider = "anthropic" | "anthropic-api" | "openai-compat";

export interface ModelInfo {
  name: string;
  provider: string;
  endpoint?: string | null;
  key_ref?: string | null;
  model_id?: string | null;
  default: boolean;
  builtin: boolean;
  /** Runs entirely on this machine — drives the LOCAL/HOSTED badge. */
  local: boolean;
  /** A key is stored for this model. The key itself is never returned (I4). */
  has_key: boolean;
  /**
   * Whether a key is stored — and whether we could even tell. `has_key` alone
   * reported a temporarily unreadable OS keyring as "no key", so the UI told
   * people to re-enter a key they already had.
   */
  key_state: KeyState;
}

/** A stored-credential answer, including "the keyring would not tell us". */
export type KeyState = "present" | "absent" | "unknown";

/** Model registry (§7/§12) — GET /api/models. Built-ins first, then local. */
export function useModels() {
  return useSWR<ModelInfo[]>("/api/models", fetcher);
}

export interface AddModelBody {
  name: string;
  provider?: ModelProvider;
  endpoint?: string;
  api_key?: string;
  model_id?: string;
  verify?: boolean;
}

/** Register a model. The backend probes tool calling before it saves. */
export function addModel(body: AddModelBody) {
  return mutateJSON<ModelInfo>("/api/models", body);
}

/** Make a model the one used when nothing else is chosen. */
export function setDefaultModel(name: string) {
  return mutateJSON<ModelInfo>("/api/models/default", { name });
}

export interface TestModelBody {
  provider: ModelProvider;
  endpoint?: string;
  api_key?: string;
  model_id?: string;
  name?: string;
}

export interface TestModelResult {
  ok: boolean;
  detail: string;
  tool_calling: boolean;
  latency_ms: number;
  available_models: string[];
}

/**
 * Check a configuration before saving it — the Test button. Nothing is
 * persisted, and a key sent here is used for the one call and discarded.
 */
export function testModel(body: TestModelBody) {
  return mutateJSON<TestModelResult>("/api/models/test", body);
}

/** How well a catalog model suits the detected machine. */
export type FitVerdict = "fits" | "slow" | "insufficient" | "unknown";

export interface CatalogEntry {
  name: string;
  label: string;
  parameters: string;
  size_gb: number;
  summary: string;
  tool_calling: boolean;
  min_ram_gb: number;
  min_vram_gb: number;
  verdict: FitVerdict;
  reason: string;
  installed: boolean;
}

export interface HardwareInfo {
  /** `null` means Argus could not detect it — never "zero". */
  ram_gb: number | null;
  vram_gb: number | null;
  gpu_name: string | null;
  platform: string;
  ollama_url: string;
  ollama_models_dir: string;
}

export interface CatalogResponse {
  hardware: HardwareInfo;
  recommended: string | null;
  models: CatalogEntry[];
}

/** Curated tool-calling local models, scored against this machine. */
export function useModelCatalog() {
  return useSWR<CatalogResponse>("/api/models/catalog", fetcher);
}

export interface InstallEvent {
  type: "progress" | "done" | "error";
  status?: string;
  completed?: number;
  total?: number;
  detail?: string;
  name?: string;
}

/**
 * Download a model through Ollama, streaming NDJSON progress.
 *
 * A download runs for minutes, so the response body is read incrementally
 * rather than awaited whole — `onEvent` fires per line as it arrives.
 *
 * `signal` makes it cancellable. That matters more than it sounds: these are
 * multi-gigabyte downloads with a 3600s server timeout, and without a way out
 * the only escape from a wrong pick was quitting the app. Aborting closes the
 * response body, which drops the backend's `StreamingResponse` generator and
 * with it the connection to Ollama's `/api/pull`.
 */
export async function installModel(
  name: string,
  onEvent: (event: InstallEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await apiFetch("/api/models/install", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
    signal,
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body, body.detail ?? `Request failed: ${response.status}`);
  }
  const reader = response.body?.getReader();
  if (!reader) throw new Error("this browser cannot stream the download progress");

  const decoder = new TextDecoder();
  let buffer = "";
  // An abort mid-`read()` rejects the pending promise; releasing the reader
  // first keeps that from surfacing as an unhandled rejection.
  signal?.addEventListener("abort", () => void reader.cancel().catch(() => {}), { once: true });
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    // A chunk can split mid-line, so the tail is held back until its newline.
    const lines = buffer.split("\n");
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.trim()) continue;
      try {
        onEvent(JSON.parse(line) as InstallEvent);
      } catch {
        // A partial or malformed line is not worth aborting a download over.
      }
    }
  }
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

/** Parse `Q:: A::` pairs from the course's `flashcards.md` into a new deck. */
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

export interface DeckDueSummary {
  deck_id: number;
  course: string;
  title: string;
  due: number;
}

export interface DueSummary {
  total: number;
  decks: DeckDueSummary[];
}

/**
 * Cards due across every deck, in one request. Backs the Study overview's
 * real "cards due" stat + FLASHCARDS panel — previously a hardcoded
 * `MOCK_CARDS_DUE = 7` because `useDueCards()` only takes a single deck id
 * and there was no whole-vault total to show instead.
 */
export function useDueSummary() {
  return useSWR<DueSummary>("/api/flashcards/due-summary", fetcher);
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

// --- Index (reindex) -----------------------------------------------------

export interface IndexStatus {
  chunks: number;
  files: number;
  indexing: boolean;
  last_run: string | null;
  last_error: string | null;
  /** The persisted chroma schema predates the current chunk shape — the
   * backend rebuilds automatically on boot when this is true, but the UI can
   * still surface it while a rebuild is in flight. */
  stale: boolean;
}

/** Poll reindex progress. GET /api/index/status. */
export function useIndexStatus() {
  return useSWR<IndexStatus>("/api/index/status", fetcher);
}

/** Trigger a full vault reindex. POST /api/index/reindex — 202, runs on a
 * background thread; poll useIndexStatus() for progress/completion. */
export function reindexVault() {
  return mutateJSON<IndexStatus>("/api/index/reindex", undefined);
}

// --- Quick Links --------------------------------------------------------

/** How a Quick Link's icon is stored. `null` kind => fall back to the `icon` glyph. */
export type QuickLinkIconKind = "preset" | "image";

export interface QuickLink {
  id: number;
  label: string;
  url: string;
  icon: string | null;
  /** `"preset"` (icon_value = a bundled SVG key) or `"image"` (icon_value = a PNG data URI). */
  icon_kind: QuickLinkIconKind | null;
  icon_value: string | null;
  sort_order: number;
  created_at?: string;
}

/** The icon fields shared by create/update request bodies. */
export interface QuickLinkIconFields {
  icon?: string | null;
  icon_kind?: QuickLinkIconKind | null;
  icon_value?: string | null;
}

/** User's pinned quick-launch links, in `sort_order`. GET /api/quick-links. */
export function useQuickLinks() {
  // Backend returns a bare array (mirrors the flashcards `GET /decks` convention).
  return useSWR<QuickLink[]>("/api/quick-links", fetcher);
}

/** Create a quick link. POST /api/quick-links. */
export function createQuickLink(body: { label: string; url: string } & QuickLinkIconFields) {
  return mutateJSON<QuickLink>("/api/quick-links", body, "POST");
}

/**
 * Partially update a quick link. PUT /api/quick-links/{id}. Only the keys
 * present in `body` are changed server-side (the backend forwards just the
 * fields it received), so a reorder can send `{ sort_order }` alone without
 * disturbing the icon, and an edit can clear a custom icon by sending
 * `icon_kind: null`.
 */
export function updateQuickLink(
  id: number,
  body: { label?: string; url?: string; sort_order?: number } & QuickLinkIconFields,
) {
  return mutateJSON<QuickLink>(`/api/quick-links/${id}`, body, "PUT");
}

/** Delete a quick link. DELETE /api/quick-links/{id}. */
export function deleteQuickLink(id: number) {
  return mutateJSON<{ ok: boolean }>(`/api/quick-links/${id}`, undefined, "DELETE");
}

// --- Automations (n8n) ---------------------------------------------------
//
// Mirrors backend/features/automations/router.py exactly — see that file's
// response models for the source of truth. Two directions of credential are
// in play here and must never be conflated: `AutomationInstance`/the
// register|test|delete flow is Argus calling *out* to n8n (n8n's API key);
// n8n calling *back into* Argus (the external/tunnel bearer token) belongs to
// a different, not-yet-built surface — see ConnectN8nDialog's step 3.

/** The single registered n8n instance. Never carries its API key (I4). */
export interface AutomationInstance {
  id: string;
  name: string;
  /** "LOCAL" | "REMOTE" — where the instance runs, not how it is reached. */
  kind: string;
  base_url: string;
  has_key: boolean;
  key_state: KeyState;
  /** Live reachability, re-probed per instance on every list call. A false
   * here is a normal degraded state, not an error: cached widgets and cards
   * keep rendering regardless. */
  connected: boolean;
}

/** Mirrors N8nProbeResult — what the "Test connection" step renders. */
export interface N8nProbeResult {
  ok: boolean;
  detail: string;
  latency_ms: number | null;
  workflow_count: number | null;
}

/** One parsed Form Trigger field — `dataclasses.asdict(FormField)` on the wire. */
export interface AutomationField {
  name: string;
  label: string;
  type: string;
  required: boolean;
  placeholder: string | null;
  default: string | null;
  options: string[] | null;
  multiple: boolean;
  html: string | null;
  secret: boolean;
  hidden: boolean;
  unrecognized: boolean;
  raw_type: string | null;
}

/** One row of `automation_runs`. */
export interface AutomationRun {
  id: string;
  workflow_id: string;
  workflow_name: string | null;
  started_at: string;
  finished_at: string | null;
  /** "running" | "ok" | "failed" | "timeout" */
  status: string;
  /** "ack" | "status" | "widget" | null */
  mode: string | null;
  message: string | null;
  execution_id: string | null;
  payload: unknown;
}

/** One cached workflow, parsed for the dashboard/management page. */
export interface AutomationCard {
  id: string;
  name: string | null;
  tags: string[];
  /** "form" | "button" | "none" */
  kind: string;
  fields: AutomationField[];
  webhook_id: string | null;
  webhook_path: string | null;
  basic_auth: boolean;
  /** argus:confirm — the UI must gate firing behind a ConfirmDialog. */
  confirm: boolean;
  /** argus:async, wire key "async" (FastAPI serializes by alias). */
  async: boolean;
  active: boolean;
  last_seen_at: string;
  last_run: AutomationRun | null;
}

export interface AutomationsResponse {
  instance: AutomationInstance | null;
  workflows: AutomationCard[];
  /** Whether n8n answered the live reachability check on this request — false
   * (with cached cards still populated) is a normal, fully-supported state. */
  connected: boolean;
  detail: string;
}

/** Computed, not stored — see backend store.widget_state. */
export type WidgetState = "live" | "stale" | "empty" | "waiting";

export interface AutomationWidget {
  slug: string;
  /** Which n8n instance pushed this. The same slug on two instances is two
   * widgets, not one, so this is part of the widget's identity — not a label. */
  instance_id: string;
  title: string | null;
  /** "metric" | "list" | "table" | "timeline" | "text" | "chart" */
  kind: string;
  payload: unknown;
  last_seen_at: string | null;
  expected_interval_seconds: number | null;
  created_at: string;
  position: number | null;
  pinned: boolean;
  hidden: boolean;
  state: WidgetState;
}

export interface AutomationRefreshResult {
  ok: boolean;
  count: number;
  dropped: number;
}

/** Mirrors RunResponse — the result of firing one workflow. */
export interface AutomationRunResult {
  run_id: string;
  /** "running" | "ok" | "failed" | "timeout" */
  status: string;
  /** "ack" | "status" | "widget" | null */
  mode: string | null;
  message: string | null;
  execution_id: string | null;
  execution_url: string | null;
  payload: unknown;
}

/** Registered automations + the instance's live connection state. GET /api/automations. */
export function useAutomations() {
  return useSWR<AutomationsResponse>("/api/automations", fetcher);
}

/** Run history, newest first, optionally scoped to one workflow. GET /api/automations/runs. */
export function useAutomationRuns(workflowId?: string, limit?: number) {
  const params = new URLSearchParams();
  if (workflowId) params.set("workflow_id", workflowId);
  if (limit) params.set("limit", String(limit));
  const qs = params.toString();
  return useSWR<AutomationRun[]>(`/api/automations/runs${qs ? `?${qs}` : ""}`, fetcher);
}

/** Dashboard widgets pushed by workflows, with computed state. GET /api/automations/widgets. */
export function useAutomationWidgets() {
  return useSWR<AutomationWidget[]>("/api/automations/widgets", fetcher);
}

/** Every registered n8n instance, each re-probed for reachability.
 * GET /api/automations/instances. */
export function useAutomationInstances() {
  return useSWR<AutomationInstance[]>("/api/automations/instances", fetcher);
}

/** Probe an n8n instance without saving anything — the TEST CONNECTION step. */
export function testN8nInstance(body: { base_url: string; api_key: string }) {
  return mutateJSON<N8nProbeResult>("/api/automations/instance/test", body);
}

/** Register the n8n instance. The backend re-probes before it saves (409 if one exists, 422 on probe failure). */
export function registerN8nInstance(body: { name: string; base_url: string; api_key: string }) {
  return mutateJSON<AutomationInstance>("/api/automations/instance", body);
}

/** Forget the registered n8n instance and its stored API key. */
export function deleteN8nInstance() {
  return mutateJSON<ConnectResult>("/api/automations/instance", undefined, "DELETE");
}

/** Re-pull workflows tagged `argus` from n8n and reconcile the cache. */
export function refreshAutomations() {
  return mutateJSON<AutomationRefreshResult>("/api/automations/refresh", undefined);
}

/** Fire one workflow's trigger. `payload` is forwarded verbatim as the form/webhook body. */
export function runAutomation(workflowId: string, payload: Record<string, unknown> = {}) {
  return mutateJSON<AutomationRunResult>(
    `/api/automations/${encodeURIComponent(workflowId)}/run`,
    { payload },
  );
}

/** Pin/hide/reorder a widget. Any call marks the dashboard layout as user-controlled. */
export function patchAutomationWidget(
  slug: string,
  body: { pinned?: boolean; hidden?: boolean; position?: number },
) {
  return mutateJSON<AutomationWidget>(
    `/api/automations/widgets/${encodeURIComponent(slug)}`,
    body,
    "PATCH",
  );
}

/** A bundled n8n workflow template from the shipped gallery. */
export interface AutomationTemplate {
  id: string;
  name: string;
  description: string;
  kind: "display" | "action";
  widget_slug: string | null;
  /** The connector module this template replaces, when it replaces one. */
  replaces: string | null;
  /** Credentials the user must grant in n8n — the one manual step by design. */
  requires: string[];
  installed: boolean;
}

export interface TemplateInstallResult {
  workflow_id: string;
  /** Where the user goes to grant the credential; installing cannot do it. */
  open_in_n8n: string;
}

export function useAutomationTemplates() {
  return useSWR<AutomationTemplate[]>("/api/automations/templates", fetcher);
}

export function installAutomationTemplate(templateId: string) {
  return mutateJSON<TemplateInstallResult>(
    `/api/automations/templates/${encodeURIComponent(templateId)}/install`,
    undefined,
  );
}

/** The inbound surface's configuration — never its token. */
export interface ExternalSurfaceInfo {
  enabled: boolean;
  port: number;
  base_url: string;
  token_state: KeyState;
}

/** A freshly issued bearer token. The only response that ever carries its value. */
export interface ExternalTokenResult {
  token: string;
  rotated: boolean;
  header_name: string;
  header_value: string;
  base_url: string;
}

export function useExternalSurface() {
  return useSWR<ExternalSurfaceInfo>("/api/automations/external", fetcher);
}

/**
 * Issue or rotate the token n8n uses to call back into Argus.
 *
 * The value comes back exactly once — the keyring holds the only copy and
 * there is no read-it-back endpoint — so the caller must show it immediately.
 */
export function issueExternalToken() {
  return mutateJSON<ExternalTokenResult>("/api/automations/external/token", undefined);
}

/** Remove a widget from the dashboard. */
export function deleteAutomationWidget(slug: string, instanceId?: string) {
  // A bare slug is ambiguous once two instances push the same one — the
  // backend 409s rather than guessing, so scope it whenever we know.
  const qs = instanceId ? `?instance_id=${encodeURIComponent(instanceId)}` : "";
  return mutateJSON<ConnectResult>(
    `/api/automations/widgets/${encodeURIComponent(slug)}${qs}`,
    undefined,
    "DELETE",
  );
}
