// Feature flags (§8) — new features ship UI-only.
// Every `preview` panel renders a PREVIEW badge (Panel's `preview` prop),
// uses hardcoded mock data, and MUST NOT call any backend write endpoint.
export const FLAGS = {
  flashcards: "enabled", // POST /api/flashcards/decks + due/grade are wired (real FSRS)
  library: "preview", // Research reading queue
  focusTimer: "preview",
  palette: "enabled", // pure client UI, safe to enable
  activeWork: "preview", // Code PR list (mock data)
  emailCapture: "enabled", // POST /api/ingest/email is wired (Phase H)
  tokenUsage: "enabled", // GET /api/usage is wired (Phase H)
  localModels: "preview", // registration UI works; routing to ollama comes later
  // `+ ADD COURSE` renders the vault's course template client-side and creates
  // it for real via POST /api/note/create (backend/writer.py create_note).
  courseCreate: "enabled",
  courseHub: "preview", // /study/course/[code] — NotebookLM-style workspace, chat + studio are mock
  quickLinks: "enabled", // GET/POST/PUT/DELETE /api/quick-links are wired
} as const;
