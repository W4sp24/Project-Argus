# Phase P3 — Planner + Suggest-then-Approve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The agent plans the day by inserting suggestion rows; nothing lands in the vault/calendar/Todoist without a click. Review UI with diffs; `/plan` in chat.

**Architecture:** `backend/suggestions.py` (queue model over the existing table + `dismiss_reason` migration). `backend/writer.py` grows `apply_suggestion` — the ONLY executor (I1): git snapshot (I2) → apply by kind (schedule→gcal insert [injectable], task→verified line edit, note→unified-diff apply that fails clean on drift) → append `## FRIDAY log` line to today's daily note → mark applied. Planner = agent run with propose_* MCP tools (strict schemas) whose handlers insert suggestion rows; prompt from agenda+buckets+review-queues+`30-Areas/assistant-preferences.md`. Review API + page; chat intercepts `/plan`.

## Tasks

### T1 — suggestions.py + migration
`Suggestion` model; `insert_suggestion(conn, kind, payload, rationale) -> id`; `pending(conn)`; `get(conn, id)`; `dismiss(conn, id, reason)` (adds `dismiss_reason` column via idempotent ALTER in init_schema). Tests: roundtrip, dismissed reason retrievable.

### T2 — writer.apply_suggestion (TDD)
Kinds: `schedule` `{blocks:[{title,start,end}]}` → `gcal_insert(block)` injectable, WriterError if gcal unconfigured; `task` `{path,line,old_line,new_line}` → verify old_line at line (strip-compare) else fail clean; `note` `{path,diff}` unified diff → context-verified hunk apply, WriterError on drift, file untouched. All: pre-apply git snapshot; `## FRIDAY log` append via writer; row → applied. Tests: schedule hits fake gcal AND vault log grows; task line edited; drifted note diff fails clean (content unchanged, row pending); FRIDAY log line appended.

### T3 — planner agent
`backend/agent/planner.py`: propose_schedule/propose_task_changes/propose_note_edit tools (strict schemas) inserting rows; context assembled from agenda, buckets, review-queue.md files, preferences note; `agent/prompts/planner.md` (never move fixed events; study blocks target weak topics; breaks between blocks; propose, never write). `run_planner(settings, instruction) -> int` (suggestions created). Unit test: tool handlers insert rows (no SDK).

### T4 — API + chat /plan + review UI
`GET /api/review`, `POST /api/review/{id}/approve`, `POST /api/review/{id}/dismiss {reason}`, `POST /api/plan {instruction}` (202-style; runs planner, returns created count; injectable planner for tests). Review page: kind-badged glass cards — schedule blocks list, task old→new lines, note diff red/green rendering, rationale, Approve/Dismiss(+reason). Chat: `/plan …` posts to /api/plan and reports. Preferences note seeded in vault-template + Scientia.

### T5 — exit criteria
pytest: apply(schedule) mocked-gcal + git log +1; dismissed reason retrievable; drifted diff fails clean. Manual: real planner `/plan tomorrow` on Scientia → rows; approve a task/note suggestion → vault git pre-apply commit + FRIDAY log. gcal live insert BLOCKED on credentials. Merge, push, journal.
