// Feature flags (§8) — new features ship UI-only.
// Every `preview` panel renders a PREVIEW badge (Panel's `preview` prop),
// uses hardcoded mock data, and MUST NOT call any backend write endpoint.
export const FLAGS = {
  flashcards: "preview", // Study deck + SRS grading
  library: "preview", // Research reading queue
  focusTimer: "preview",
  palette: "enabled", // pure client UI, safe to enable
  activeWork: "preview", // Code PR list (mock data)
  emailCapture: "preview", // manual email extraction (§11)
  tokenUsage: "preview", // until a real usage log exists (§14)
  localModels: "preview", // registration UI works; routing to ollama comes later
} as const;
