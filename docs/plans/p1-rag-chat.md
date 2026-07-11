# Phase P1 — RAG + Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Index the vault (markdown + course materials) locally and chat over it with citations — read-only agent, subscription auth.

**Architecture:** `backend/rag/` pipeline: extract (md/pdf/pptx/docx → text+meta blocks) → chunk (heading-aware ~350 tok) → embed (bge-small-en-v1.5, ChromaDB `.friday/chroma`) → retrieve (vector + BM25 → RRF → recency boost → wikilink expansion). A watchdog watcher keeps the index fresh; `friday reindex` rebuilds. `backend/agent/` wraps claude-agent-sdk with in-process MCP tools (`search_vault`, `read_note`, `list_tasks` stub); `/ws/chat` bridges SDK streaming to the browser; `web/chat` renders streaming text + citation chips with `obsidian://` links.

**Tech Stack (pinned, playbook §6):** sentence-transformers `BAAI/bge-small-en-v1.5` (normalized, batch 64) · chromadb PersistentClient, collection `vault`, ids `sha1(path::idx)` · rank_bm25 + RRF k=60 · recency `exp(-age_days/45)` on `10-Daily`/`00-Inbox` · pdfplumber / python-pptx / python-docx with page/slide meta (I6) · claude-agent-sdk, model `claude-opus-4-8`, NO api key (I5) · watchdog 2s debounce.

## Global Constraints

- I3: `99-Private/`, `no-ai`-tagged notes, `.friday/`, `.git/`, `.obsidian/`, `90-Meta/` (FRIDAY indexes read-only journal via journal API, not RAG — D2 keeps journal out of prompts unless later opted in) never enter the index.
- I5: subscription auth — never set ANTHROPIC_API_KEY.
- I6: every answer cites `[path]` or `[file p.N / slide N]`; empty retrieval → "not in your notes".
- Heavy deps stay in `[rag]` extra; modules import them lazily so the base app still boots without them.

## Tasks

### T1 — extract.py
`extract_blocks(file_path) -> list[Block]` where `Block{text, meta{path, page?, slide?}}`. `.md` via frontmatter (skip no-ai → return []), `.pdf` pdfplumber per-page, `.pptx` per-slide, `.docx` whole-doc paragraphs. Tests: md strips frontmatter; pdf/pptx/docx fixtures generated in-test (skip pdf fixture if pdfplumber can't author — build tiny PDF with raw syntax); no-ai md → [].

### T2 — chunk.py
`chunk_blocks(blocks, rel_path, vault_ctx) -> list[Chunk]` — heading-aware split, target ~350 "tokens" (≈ words·1.3; simple whitespace token proxy), 50 overlap; meta: path, title, heading, date (frontmatter/daily filename), tags, wikilinks (`[[...]]`), course (from `15-Courses/<CODE>/`), page/slide passthrough. Tests: heading split, course/tags/wikilinks meta, page meta survives.

### T3 — index.py (embed + store)
`VaultIndex(db_dir)` lazy-loads model+chroma. `.upsert_file(vault, rel_path)`, `.delete_file(rel_path)` (delete-by-path then add, sha1 ids), `.reindex_all(vault)`, `.query(text, n, filters)`. Skip I3 paths via shared `is_indexable(rel_path)`. Tests (marked `rag`, skipped when deps missing): private/no-ai never indexed; pdf chunk carries page meta; reindex idempotent.

### T4 — retrieve.py
`retrieve(index, query, k=8, course=None, tags=None)` — vector top-20 + BM25 top-20 (corpus cached from chroma) → RRF(k=60) → ×exp(-age_days/45) for 10-Daily/00-Inbox → top-k → 1-hop wikilink title-line expansion. Test: seeded fact retrieved; daily-note recency boost ordering.

### T5 — watcher.py + CLI
`friday reindex` (full rebuild, prints count); `friday watch` (observer, 2s debounce, upsert/delete on events, ignores I3 dirs). Tests: debounce queue logic unit-tested without real observer.

### T6 — agent/ + /ws/chat
`backend/agent/runtime.py`: ClaudeSDKClient, model claude-opus-4-8, in-process MCP server `friday` with tools search_vault/read_note/list_tasks(stub); prompt `backend/agent/prompts/chat.md` (cite-or-say-missing rules). `/ws/chat`: accept → receive {message} → stream {type:"delta"|"citation"|"done"} frames. Test: ws streams >1 delta with a fake agent injected (dependency-injected runner so tests don't hit the real SDK).

### T7 — web/chat page
Streaming chat UI: message list, glass bubbles, typing indicator, citation chips (`[path]` parsed from text → obsidian:// links), input with Enter-to-send; graceful offline state. `npm run build` green.

### T8 — exit criteria + gate
pytest (incl. rag-marked if deps present); manual seeded-vault QA (PDF fact cited, absent fact → "not in your notes"); ruff; commit/merge/push; BUILD_STATE/LOG.
