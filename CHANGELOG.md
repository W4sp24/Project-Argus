# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
Argus is currently pre-1.0 (0.x releases).

## [Unreleased]

### Added
- **Pluggable model backends** — chat, the day planner and study generation now run on whichever model you register: a local [Ollama](https://ollama.com) model, any hosted OpenAI-compatible provider (Groq, Together, Fireworks, OpenRouter, DeepInfra), the Anthropic API on a key, or Claude Code. **Argus no longer requires Claude Code.** One `ToolSpec`/`AgentAdapter` abstraction (`backend/agent/adapters.py`) backs all four; the same tool handlers run whichever provider dispatches them.
- **Guided model configuration** — **System → MODELS → + ADD MODEL** walks through provider, credentials and model choice, and will not save a configuration that hasn't passed a live **Test connection**. The test lists the models an endpoint actually serves, so the model is a dropdown rather than something you have to know. Keys are stored in the OS keyring, referenced by name only in `.argus/models.json`.
- **Hardware-aware local model picker** — **System → LOCAL.MODELS** detects RAM and NVIDIA VRAM, labels each curated model **FITS** / **SLOW** / **TOO BIG** with a plain-language reason, marks a best fit, and installs it through Ollama with live progress. Every catalogued model supports tool calling.
- **`argus mcp-server`** — exposes the vault's read-only tools (`search_vault`, `read_note`, `list_tasks`) over MCP so Claude Code, Codex CLI or Gemini CLI can use your notes from any project directory. The planner's `propose_*` tools are deliberately not exposed.
- **Settable default model** — `POST /api/models/default`, persisted in `.argus/model-prefs.json`. The default was previously pinned to a Claude model with no way to change it.
- **LOCAL / HOSTED badges** on every model row and in the model picker, so whether your notes leave the machine is visible at a glance.
- Model selection now reaches `/plan` and study guide/exam generation, not just chat. The Study tab gains the model selector chat already had.
- `argus doctor` reports Ollama reachability (`WARN`, never `FAIL` — it's one option, not a requirement).
- **Quick Links** — a configurable panel on the Dashboard tab to save and open frequently-used sites (icon glyph + label + https URL), persisted in the backend SQLite store. URLs are sanitized to https-only.

### Changed
- The desktop installer no longer blocks on Claude Code. Only git is required; Claude Code and Ollama are reported as optional, and a new onboarding step explains the three ways to run the AI.
- `list_tasks` returns real vault tasks. It had been a stub replying "the task engine is not built yet (Phase P2)" long after `backend/tasks/` shipped — and it is one of the tools the MCP bridge exposes.
- Token-usage cost estimates price unknown models at **zero** and name them in `unpriced_models`, instead of falling back to Opus rates. Billing a free local model at $15/M would have been actively misleading. `argus`'s Claude Code CLI usage report is unaffected — every model there genuinely is an Anthropic one.
- `httpx` and `mcp` are now explicit dependencies rather than transitives of `claude-agent-sdk`.

### Fixed
- Dev-mode CSP now includes `'unsafe-eval'` **in development only**, so `next dev`'s React Refresh runtime can hydrate client components (previously all dev-mode client hydration silently failed). Production and packaged builds keep the strict, eval-free policy. This also unblocks the Playwright e2e suite.
