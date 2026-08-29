// Feature flags (§8) — new features ship UI-only.
// Every `preview` panel renders a PREVIEW badge (Panel's `preview` prop),
// uses hardcoded mock data, and MUST NOT call any backend write endpoint.
export const FLAGS = {
  flashcards: "enabled", // POST /api/flashcards/decks + due/grade are wired (real FSRS)
  // Research reading queue + highlights persist to the vault for real (one
  // note per paper under <areas>/papers/, one running highlights.md).
  library: "enabled",
  focusTimer: "preview",
  palette: "enabled", // pure client UI, safe to enable
  activeWork: "preview", // Code PR list (mock data)
  emailCapture: "enabled", // POST /api/ingest/email is wired (Phase H)
  tokenUsage: "enabled", // GET /api/usage is wired (Phase H)
  // Registration, routing, and the hardware-aware local picker are all wired:
  // chat/planner/study run on Ollama, hosted OpenAI-compatible providers, the
  // Anthropic API, or Claude Code, chosen per model in /system.
  localModels: "enabled",
  // `+ ADD COURSE` renders the vault's course template client-side and creates
  // it for real via POST /api/note/create (backend/writer.py create_note).
  courseCreate: "enabled",
  // /study/course/[code] — NotebookLM-style workspace. Chat is real (course
  // filter forced through search_vault, backend/agent/runtime.py); STUDIO
  // generates real study guides/decks/exams; SOURCES lists real files
  // (GET /api/study/courses/<code>/sources, not markdown-only /api/notes),
  // ingests through POST /api/ingest/jobs with per-file progress, and its
  // checkboxes are a real retrieval scope for both chat and the generators.
  courseHub: "enabled",
  quickLinks: "enabled", // GET/POST/PUT/DELETE /api/quick-links are wired
  // /sources — the corpus as a browsable list, and the ingest form that
  // fills it. GET /api/sources + POST /api/ingest/jobs are wired; progress
  // is polled from GET /api/ingest/jobs/{id}.
  sources: "enabled",
} as const;
