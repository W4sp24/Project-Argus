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
  /** Absolute path to the vault root. `name` cannot build a working
   *  `obsidian://` link on its own — see `obsidianUri` in lib/citations. */
  path: string;
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
 *   PC, or a hosted provider like Groq/DeepSeek/Together/OpenRouter.
 * - `gemini` — Google's Generative Language API, which is not OpenAI-shaped.
 *   It has its own adapter rather than riding Google's compatibility shim; see
 *   backend/agent/gemini_api.py for why.
 */
export type ModelProvider = "anthropic" | "anthropic-api" | "openai-compat" | "gemini";

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


// --- Sources & ingestion ------------------------------------------------

/**
 * `POST /api/ingest` — the single-file, synchronous route.
 *
 * Declared here rather than inline at each call site: two components read
 * this shape and both had their own copy of it, which is how the two drifted
 * over what `indexed: false` means. `index_error` is the difference between
 * "indexing broke" and "the [rag] extras are not installed", which
 * `indexed: false` alone cannot express.
 */
export interface IngestResponse {
  path: string;
  chunks: number;
  indexed: boolean;
  index_error: string | null;
}

/** One real file in the vault, indexed or not. GET /api/sources. */
export interface SourceInfo {
  path: string;
  title: string;
  /** Parent directory, vault-relative; `""` for a file at the vault root. */
  folder: string;
  /** Uppercased extension, e.g. "PDF" / "MD". */
  kind: string;
  modified: string;
  size: number;
  /** Chunks in the live index, or `null` when unknown — either the index holds
   * nothing for this file, or there is no index to ask. Never `0`; see
   * `index_available` for telling those two apart. */
  chunks: number | null;
}

export interface SourcesResponse {
  sources: SourceInfo[];
  /** False when the [rag] extras are missing or chroma is unreadable, which is
   * why every `chunks` came back null. */
  index_available: boolean;
}

/** Everything in the vault that RAG can read. */
export function useSources(folder?: string) {
  const query = folder ? `?folder=${encodeURIComponent(folder)}` : "";
  return useSWR<SourcesResponse>(`/api/sources${query}`, fetcher);
}

/** Vault folders an ingest may be pointed at. Taxonomy-derived, server-side:
 * never build one of these paths in the frontend. */
export function useIngestDestinations() {
  return useSWR<{ destinations: string[] }>("/api/ingest/destinations", fetcher);
}

/** One shape a generated note can take. */
export interface NoteStyle {
  key: string;
  label: string;
  description: string;
}

/**
 * The note shapes the backend actually offers. Fetched rather than listed
 * here for the same reason destinations are: a copy in the dialog is a copy
 * that drifts the first time a style is added in
 * `backend/features/ingest/notes.py`.
 */
export function useIngestNoteStyles() {
  return useSWR<{ styles: NoteStyle[] }>("/api/ingest/note-styles", fetcher);
}

/** Where one file got to. `stage` is rendered directly by the progress list. */
export type IngestStage =
  | "queued"
  | "saving"
  | "indexing"
  | "summarizing"
  | "done"
  | "failed"
  | "skipped";

export interface IngestJobItem {
  id: number;
  filename: string;
  path: string | null;
  stage: IngestStage;
  chunks: number;
  summary_path: string | null;
  error: string | null;
  /** Where an item stopped, when it stopped early. `stage` cannot say: it
   * collapses to `failed`, or to `done` when only the note broke. */
  failed_stage: IngestStage | null;
}

export type IngestJobStatus = "queued" | "running" | "ok" | "partial" | "failed";

export interface IngestJob {
  id: string;
  created_at: string;
  finished_at: string | null;
  status: IngestJobStatus;
  target: string;
  summary_prompt: string;
  /** Which `NoteStyle.key` shaped the notes, or "" for "no note". */
  note_style: string;
  total: number;
  done: number;
  error: string | null;
  /** Present on GET /api/ingest/jobs/{id}, absent from the history listing. */
  items?: IngestJobItem[];
}

const JOB_POLL_MS = 700;

/**
 * Poll one ingest job. Polling rather than streaming, deliberately: the job
 * outlives its request, so it must survive a tab close, a navigation away and
 * a reload — none of which a response stream does.
 *
 * `refreshInterval` is the function form so it stops on its own once the job
 * reaches a terminal status; there is no timeout to manage and nothing to
 * clean up on unmount.
 */
export function useIngestJob(jobId: string | null) {
  return useSWR<IngestJob>(jobId ? `/api/ingest/jobs/${jobId}` : null, fetcher, {
    refreshInterval: (job) =>
      job && (job.status === "queued" || job.status === "running") ? JOB_POLL_MS : 0,
  });
}

/** Recent ingest jobs, newest first, without their items. */
export function useIngestJobs() {
  return useSWR<{ jobs: IngestJob[] }>("/api/ingest/jobs", fetcher);
}

export interface IngestPrecheck {
  exists: boolean;
  path: string | null;
  /** SHA-256 of the file already in the vault, for comparing against the
   * browser's hash of the file about to be uploaded. */
  sha256: string | null;
}

/** What is already at `<target>/<filename>`, so the UI can offer Replace. */
export function precheckIngest(filename: string, target: string) {
  return mutateJSON<IngestPrecheck>("/api/ingest/precheck", { filename, target });
}

/**
 * Start a batch ingest. Returns immediately with a job id; poll
 * `useIngestJob` for per-file progress.
 */
export async function startIngestJob(
  files: File[],
  options: { target: string; noteStyle?: string; summaryPrompt?: string; replace?: boolean },
): Promise<string> {
  const body = new FormData();
  files.forEach((file) => body.append("files", file));
  body.append("target", options.target);
  body.append("note_style", options.noteStyle ?? "");
  body.append("summary_prompt", options.summaryPrompt ?? "");
  body.append("replace", String(options.replace ?? false));
  const response = await apiFetch("/api/ingest/jobs", { method: "POST", body });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new ApiError(
      response.status,
      payload,
      typeof payload.detail === "string" ? payload.detail : `ingest failed (${response.status})`,
    );
  }
  return (payload as { job_id: string }).job_id;
}

/**
 * SHA-256 of a picked file, hex, matching the backend's digest of whatever is
 * already at that path. `crypto.subtle` needs a secure context, which
 * localhost is; it returns null rather than throwing if that ever fails, and
 * the caller then treats the collision as "changed" and offers Replace.
 */
export async function hashFile(file: File): Promise<string | null> {
  try {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return Array.from(new Uint8Array(digest))
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  } catch {
    return null;
  }
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
  /** Failure class, so the dialog can tell "wrong key" from "wrong URL"
   * without pattern-matching `detail`, which is prose free to be reworded:
   * "ok" | "auth" | "unreachable" | "timeout" | "not_n8n" | "api_disabled"
   * | "error". */
  reason: string;
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
  /** Which n8n instance this run belongs to — see AutomationCard.instance_id. */
  instance_id: string;
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
  /** Which n8n instance this workflow was pulled from — the join key for the
   * instance filter and for OriginChip. */
  instance_id: string;
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
  /** Every registered instance. Supersedes `instance`, which stays byte-
   * identical for 0/1 registered instances and degrades to `null` for 2+. */
  instances: AutomationInstance[];
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
  /** Grid span, 1..4. Auto-placed widgets still carry a sensible default —
   * this is never null, unlike `position`. */
  grid_cols: number;
  grid_rows: number;
  /** Has the user taken control of THIS widget's layout (drag/resize/reorder)?
   * Per-widget, not global — see AutomationWidgets.tsx's "take control" model. */
  layout_locked: boolean;
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
  /** For a widget-mode run: which widget it wrote. `payload` carries only the
   * kind-specific fields (the backend's ValidatedWidget strips slug and kind),
   * so without these the result can be rendered but never acted on. */
  widget_slug: string | null;
  widget_kind: string | null;
  instance_id: string | null;
}

/** Registered automations + the instance's live connection state. GET /api/automations. */
export function useAutomations() {
  return useSWR<AutomationsResponse>("/api/automations", fetcher);
}

/**
 * Run history, newest first, optionally scoped to one workflow. GET
 * /api/automations/runs.
 *
 * `options.refreshInterval` turns this into a poll — the command palette's
 * RUN mode uses it to watch an `argus:async` workflow's fire-and-forget run
 * settle, since the initial POST response for those never carries the final
 * status (see `RunResponse.status === "running"`). `options.enabled: false`
 * suppresses the request entirely (a `null` SWR key) rather than fetching
 * and discarding — most callers of this hook don't want to poll at all.
 */
export function useAutomationRuns(
  workflowId?: string,
  limit?: number,
  options?: { refreshInterval?: number; enabled?: boolean },
) {
  const enabled = options?.enabled ?? true;
  const params = new URLSearchParams();
  if (workflowId) params.set("workflow_id", workflowId);
  if (limit) params.set("limit", String(limit));
  const qs = params.toString();
  const key = enabled ? `/api/automations/runs${qs ? `?${qs}` : ""}` : null;
  return useSWR<AutomationRun[]>(
    key,
    fetcher,
    options?.refreshInterval ? { refreshInterval: options.refreshInterval } : undefined,
  );
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

/**
 * Register an n8n instance. The backend re-probes before it saves (409 on a
 * duplicate name, 422 on probe failure) — `POST /api/automations/instances`,
 * the multi-instance route (F5). `kind` defaults server-side to "REMOTE"
 * when omitted.
 */
export function registerN8nInstance(body: {
  name: string;
  base_url: string;
  api_key: string;
  kind?: "LOCAL" | "REMOTE";
}) {
  return mutateJSON<AutomationInstance>("/api/automations/instances", body);
}

/** Forget the registered n8n instance and its stored API key. Deletes
 * `instances[0]`, 404 when empty — the pre-multi-instance compat route. */
export function deleteN8nInstance() {
  return mutateJSON<ConnectResult>("/api/automations/instance", undefined, "DELETE");
}

/** Forget one registered n8n instance (by id) and its stored API key —
 * `DELETE /api/automations/instances/{id}`. */
export function deleteAutomationInstance(id: string) {
  return mutateJSON<ConnectResult>(
    `/api/automations/instances/${encodeURIComponent(id)}`,
    undefined,
    "DELETE",
  );
}

/** Re-pull workflows tagged `argus` from n8n and reconcile the cache. */
export function refreshAutomations() {
  return mutateJSON<AutomationRefreshResult>("/api/automations/refresh", undefined);
}

/** Re-pull workflows tagged `argus` from one instance and reconcile its
 * cache — `POST /api/automations/instances/{id}/refresh`. Also doubles as
 * "try reconnecting": it re-runs the same call the reachability probe does. */
export function refreshAutomationInstance(id: string) {
  return mutateJSON<AutomationRefreshResult>(
    `/api/automations/instances/${encodeURIComponent(id)}/refresh`,
    undefined,
  );
}

/**
 * Fire one workflow's trigger. `payload` is forwarded verbatim as the
 * form/webhook body.
 *
 * Pass `instanceId` (every `AutomationCard` carries one) to hit the
 * instance-scoped route, `POST /automations/instances/{instance_id}/workflows/
 * {workflow_id}/run` — required once two instances can register the same
 * workflow id, and the route F9 keeps once the unscoped compat shim below is
 * removed. `instanceId` is optional only so callers outside this chunk's
 * scope that still invoke the two-argument form keep working unchanged
 * against the compat route (`POST /automations/{workflow_id}/run`), which
 * 409s if the id turns out to be ambiguous across instances rather than
 * guessing.
 */
export function runAutomation(
  workflowId: string,
  payload: Record<string, unknown> = {},
  instanceId?: string,
) {
  const path = instanceId
    ? `/api/automations/instances/${encodeURIComponent(instanceId)}/workflows/${encodeURIComponent(workflowId)}/run`
    : `/api/automations/${encodeURIComponent(workflowId)}/run`;
  return mutateJSON<AutomationRunResult>(path, { payload });
}

/**
 * Best-effort cancellation of a still-`running` run — stops it via n8n's
 * stop API when an execution id was recorded, marks the run `failed` with a
 * message saying a human stopped it (the backend never uses a `'cancelled'`
 * status value; see `router.cancel_run`), and frees the workflow's in-flight
 * lock so it can be re-run immediately.
 */
export function cancelAutomationRun(runId: string) {
  return mutateJSON<AutomationRun>(
    `/api/automations/runs/${encodeURIComponent(runId)}/cancel`,
    undefined,
    "POST",
  );
}

/**
 * Pin/hide/reorder/resize a widget. `instanceId` scopes the slug the same
 * way `deleteAutomationWidget` does — a bare slug is ambiguous once two
 * instances push the same one, and the backend has no default to fall back
 * on for a PATCH the way it does for a DELETE.
 *
 * Any call marks that widget's `layout_locked` true server-side — this is
 * unconditional and per-widget, never global (see AutomationWidgets.tsx's
 * "take control" model). There is no documented way to clear it back through
 * this endpoint; see the comment on `restoreAutoPlace` there.
 */
export function patchAutomationWidget(
  slug: string,
  instanceId: string,
  body: { pinned?: boolean; hidden?: boolean; position?: number; grid_cols?: number; grid_rows?: number },
) {
  return mutateJSON<AutomationWidget>(
    `/api/automations/widgets/${encodeURIComponent(slug)}?instance_id=${encodeURIComponent(instanceId)}`,
    body,
    "PATCH",
  );
}

/** Hand every widget back to auto-placement.
 *
 * One route rather than a patch per widget: every layout write implicitly
 * locks the widget it touches, so unlocking through that path is impossible
 * by construction — sending the patch is what re-locks it. */
export function resetAutomationLayout() {
  return mutateJSON<AutomationRefreshResult>(
    "/api/automations/widgets/layout/reset",
    undefined,
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
  /** Short factual badges (renderer, field count, cadence) derived from the
   * bundled definition — rendered as small bordered tags on the card. */
  chips: string[];
  installed: boolean;
}

export interface TemplateInstallResult {
  workflow_id: string;
  /** Where the user goes to grant the credential; installing cannot do it. */
  open_in_n8n: string;
  /**
   * Whether n8n accepted the activation. `false` is the expected outcome for
   * any template with a non-empty `requires` — n8n refuses to activate a
   * workflow whose credentials are not configured yet, and they are granted
   * after this install, via `open_in_n8n`.
   */
  active: boolean;
  /** n8n's own words for a refused activation, or null when it succeeded. */
  activation_error: string | null;
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

export interface WorkflowActivateResult {
  workflow_id: string;
  active: boolean;
}

/**
 * Activate a workflow that installed inactive — the other half of the
 * credential hand-off, once the user has granted it in n8n.
 */
export function activateAutomationWorkflow(instanceId: string, workflowId: string) {
  return mutateJSON<WorkflowActivateResult>(
    `/api/automations/instances/${encodeURIComponent(instanceId)}/workflows/${encodeURIComponent(workflowId)}/activate`,
    undefined,
  );
}

/**
 * Destroy the workflow in n8n. Irreversible — Argus keeps no copy of the
 * definition, so there is nothing to restore from.
 */
export function deleteAutomationWorkflow(instanceId: string, workflowId: string) {
  return mutateJSON<ConnectResult>(
    `/api/automations/instances/${encodeURIComponent(instanceId)}/workflows/${encodeURIComponent(workflowId)}`,
    undefined,
    "DELETE",
  );
}

/**
 * Drop the `argus` tag: the workflow leaves Argus but survives in n8n, and
 * re-tagging it there brings it back.
 */
export function unregisterAutomationWorkflow(instanceId: string, workflowId: string) {
  return mutateJSON<ConnectResult>(
    `/api/automations/instances/${encodeURIComponent(instanceId)}/workflows/${encodeURIComponent(workflowId)}/unregister`,
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
export function issueExternalToken(instanceId?: string) {
  // Scoped when we know which instance: each one carries its own token, so
  // revoking one never silences the others. The unscoped compat route is
  // only valid while exactly one instance is registered.
  const path = instanceId
    ? `/api/automations/instances/${encodeURIComponent(instanceId)}/external/token`
    : "/api/automations/external/token";
  return mutateJSON<ExternalTokenResult>(path, undefined);
}

/** One `argus`-tagged workflow found on an instance that is not registered yet. */
export interface DiscoveredWorkflow {
  id: string;
  name: string | null;
  active: boolean;
  /** "display" | "action" */
  kind: string;
  tagged: boolean;
}

/**
 * The `argus`-tagged workflows on an instance, before registering it.
 *
 * Persists nothing and needs no registration — it exists so the connect
 * dialog can show what it is about to register before committing. Only
 * tagged workflows come back: the tag is the consent.
 */
export function discoverN8nWorkflows(body: { base_url: string; api_key: string }) {
  return mutateJSON<DiscoveredWorkflow[]>("/api/automations/instances/discover", body);
}

/** One input an action workflow's trigger asks for. `name` is the payload key. */
export interface AutomationActionField {
  name: string;
  type: string;
  required: boolean;
}

/**
 * An installed workflow that provides a capability a native panel can use.
 *
 * A panel knows it wants to *create a task*; it cannot know the workflow id,
 * because the user's own n8n minted that at install time. So it asks by
 * `action_slug` and fires whatever comes back.
 */
export interface AutomationAction {
  /** "calendar.create" | "task.create" | "task.complete" */
  action_slug: string;
  template_id: string;
  workflow_id: string;
  workflow_name: string;
  instance_id: string;
  instance_name: string;
  /** Installed but inactive — nearly always an ungranted credential in n8n. */
  active: boolean;
  fields: AutomationActionField[];
}

/**
 * Installed action workflows, indexed by slug — GET /api/automations/actions.
 *
 * Returns a lookup rather than the raw list because every caller wants "is
 * this one capability available?", never the whole set.
 */
export function useAutomationActions(): {
  actions: Record<string, AutomationAction>;
  isLoading: boolean;
} {
  const { data, isLoading } = useSWR<AutomationAction[]>("/api/automations/actions", fetcher);
  const actions: Record<string, AutomationAction> = {};
  for (const action of data ?? []) actions[action.action_slug] = action;
  return { actions, isLoading };
}

/**
 * Fire an action workflow with a payload keyed by its own field names.
 *
 * `values` is keyed by the *label* the workflow's form declares, which is why
 * callers build it from `action.fields` rather than hardcoding: renaming a
 * field in n8n changes the payload keys, and a hardcoded key would silently
 * send nothing under the new name.
 */
export function runAutomationAction(
  action: AutomationAction,
  values: Record<string, unknown>,
): Promise<AutomationRunResult> {
  return runAutomation(action.workflow_id, values, action.instance_id);
}

/** Find an action's field name case-insensitively, or undefined if it has none. */
export function actionFieldName(
  action: AutomationAction,
  wanted: string,
): string | undefined {
  return action.fields.find((f) => f.name.toLowerCase() === wanted.toLowerCase())?.name;
}

/** One calendar event on the agenda. `source` is "gcal" or "n8n". */
export interface AgendaEvent {
  title: string;
  start: string;
  end: string;
  all_day: boolean;
  source?: string;
  location?: string | null;
}

/** One task on the agenda — from the vault, a connector, or an n8n widget. */
export interface AgendaTask {
  text: string;
  done: boolean;
  due: string | null;
  scheduled: string | null;
  priority: string | null;
  tags: string[];
  source: string;
  path: string | null;
  line: number | null;
  external_id?: string | null;
  href?: string | null;
}

/** GET /api/agenda — everything the Today view needs for one date. */
export interface Agenda {
  date: string;
  events: AgendaEvent[];
  tasks: AgendaTask[];
  top_tasks: AgendaTask[];
  configured: { gcal: boolean; todoist: boolean };
  /** Populated only for a connector that failed *this* request. */
  connector_errors?: Record<string, string>;
}

/**
 * The agenda for one day, or today when `day` is omitted.
 *
 * One hook rather than the three hand-mirrored `useSWR("/api/agenda")` calls
 * this replaced: those each declared their own local `Agenda` interface, and
 * each had drifted to a different subset of the real response —
 * `connector_errors` was in none of them, which is why a failing connector
 * rendered as an empty panel with a healthy badge.
 *
 * Omitting `day` keeps the key as the bare `/api/agenda`, so the server and
 * client agree on the first render; a date only appears in the key once the
 * user navigates, which is necessarily after mount.
 */
export function useAgenda(day?: string | null) {
  return useSWR<Agenda>(day ? `/api/agenda?day=${encodeURIComponent(day)}` : "/api/agenda", fetcher);
}

/** Which path is currently answering for each migratable source. */
export interface SourceProvenance {
  /** "n8n" | "connector" */
  calendar: string;
  tasks: string;
}

/**
 * Provenance for the dashboard's `VIA N8N` markers.
 *
 * Read from the backend rather than inferred from widget state here: the
 * server already applies a freshness rule to decide which path supplies the
 * data, and re-deriving that in the client would be a second copy of the
 * decision, free to drift from the one that actually picks it.
 */
export function useSourceProvenance() {
  return useSWR<SourceProvenance>("/api/automations/sources", fetcher);
}

/** One row of `automation_events` — the ACTIVITY tab's real feed of pushes,
 * runs, installs, and captures, not only runs. */
export interface AutomationEvent {
  ts: string;
  instance_id: string;
  /** "RUN" | "PUSH" | "FAIL" | "INSTALL" | "CAPTURE" */
  tag: string;
  /** The workflow/template name responsible, when known. */
  subject: string | null;
  text: string;
}

/**
 * Activity feed, newest first — GET /api/automations/events. `tag` is sent
 * as-is; the backend normalizes case before matching (`RUN`/`run` both
 * work), so callers can pass the lowercase filter chip value directly.
 */
export function useAutomationEvents(tag?: string, instanceId?: string, limit?: number) {
  const params = new URLSearchParams();
  if (tag) params.set("tag", tag);
  if (instanceId) params.set("instance_id", instanceId);
  if (limit) params.set("limit", String(limit));
  const qs = params.toString();
  return useSWR<AutomationEvent[]>(`/api/automations/events${qs ? `?${qs}` : ""}`, fetcher);
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

// --- Chat threads (persistent conversations) --------------------------------

export interface ThreadInfo {
  id: number;
  title: string;
  course: string | null;
  archived: boolean;
  created_at: string;
  updated_at: string;
  message_count: number;
}

/**
 * One persisted turn. `tools` is whatever `_tool_frame()` produced at write
 * time, so the restore path treats it as read-only and shape-tolerant: a
 * later protocol change must degrade to "no chips on old rows", never throw.
 */
export interface ChatMessageInfo {
  id: number;
  role: "user" | "assistant";
  text: string;
  model: string | null;
  tools: Record<string, unknown>[];
  created_at: string;
}

export interface ThreadDetail {
  thread: ThreadInfo;
  messages: ChatMessageInfo[];
}

/**
 * The thread list behind the /chat rail.
 *
 * Passing no `course` deliberately returns course-scoped threads as well —
 * `store.list_threads` filters only when asked, because the rail wants the
 * global conversation and the one started inside a Course Hub side by side.
 * They are told apart by `ThreadInfo.course`, not by living in separate lists.
 */
export function useChatThreads(course?: string, archived = false) {
  const params = new URLSearchParams();
  if (course) params.set("course", course);
  if (archived) params.set("archived", "true");
  const query = params.toString();
  return useSWR<ThreadInfo[]>(`/api/chat/threads${query ? `?${query}` : ""}`, fetcher);
}

/** One thread with its whole transcript. The backend does not paginate these
 *  yet, so a very long thread loads in full. */
export function getChatThread(id: number) {
  return fetcher<ThreadDetail>(`/api/chat/threads/${id}`);
}

export function createChatThread(body: { title?: string; course?: string } = {}) {
  return mutateJSON<ThreadInfo>("/api/chat/threads", body);
}

/** Rename and/or archive. The backend 400s on an empty patch and on a blank
 *  title rather than silently doing nothing, so callers must send a change. */
export function patchChatThread(id: number, body: { title?: string; archived?: boolean }) {
  return mutateJSON<ThreadInfo>(`/api/chat/threads/${id}`, body, "PATCH");
}

export function deleteChatThread(id: number) {
  return mutateJSON<{ status: string }>(`/api/chat/threads/${id}`, undefined, "DELETE");
}
