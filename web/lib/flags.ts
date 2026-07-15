// Feature flags (§8) — new features ship UI-only.
// Every `preview` panel renders a PREVIEW badge (Panel's `preview` prop),
// uses hardcoded mock data, and MUST NOT call any backend write endpoint.
export const FLAGS = {
  flashcards: "preview", // Study deck + SRS grading
  library: "preview", // Research reading queue
  focusTimer: "preview",
  palette: "enabled", // pure client UI, safe to enable
  activeWork: "preview", // Code PR list (mock data)
  emailCapture: "enabled", // POST /api/ingest/email is wired (Phase H)
  tokenUsage: "enabled", // GET /api/usage is wired (Phase H)
  localModels: "preview", // registration UI works; routing to ollama comes later
  // Phase D: no backend endpoint creates a new course note (PUT /api/note only
  // updates files that already exist — see backend/writer.py update_note).
  // `+ ADD COURSE` is local-only until a create-course/create-note endpoint lands.
  courseCreate: "preview",
  courseHub: "preview", // /study/course/[code] — NotebookLM-style workspace, chat + studio are mock
} as const;
