# Chat & RAG overhaul — Project Argus

*Drafted 2026-08-09. Committed 2026-08-19, with the corrections below.*

> ## Status
> **Phase 1 (chat spine) — done.** Backend: structured tool events, a message-list
> adapter turn, thread tables, thread REST, and a socket that carries a thread,
> history and a tool trace. Frontend: one long-lived socket, markdown answers,
> a live-then-collapsed tool trace, trace-derived citations, a session rail, and
> a Playwright spec.
> **Phase 2 (retrieval) — done**, minus the cross-encoder rerank (§2.6), which
> was deliberately deferred: it pins torch in the desktop bundle.
> **Phase 3 (write tools) — not started**, except the `edit_note` safety fix.
>
> ## Where this plan was wrong
> Corrected inline below; recorded here so the diff is not mistaken for drift.
> 1. **There is no `citations` frame.** §1.2 specified one; it was never built,
>    and it is not needed. The `tool` end frame carries `paths`, already filtered
>    through `is_indexable`, so citation chips are derived from the trace. That
>    makes invariant I3 structural rather than prompt-enforced — a stronger
>    result than the frame would have given.
> 2. **The trace column is `tools_json`, not `steps_json`**, and thread ids are
>    `INTEGER AUTOINCREMENT`, not `TEXT`. §1.3's DDL is superseded by
>    `backend/core/db.py`'s `SCHEMA`.
> 3. **There is no integration branch.** §"Branch" called for one;
>    `CONTRIBUTING.md:29` forbids it outright. All three phases land on flat
>    `feature/` branches.
> 4. **The `contextvars` hack in §1.1 was unnecessary.** `ClaudeSDKAdapter` can
>    read `ToolUseBlock` and `ToolResultBlock` itself, so one uniform
>    `summarize(args, result_text)` works across all three adapters.
> 5. **`npm test` does not exist** (§Verification). There is no JS unit runner in
>    this repo — no vitest, no jest. Frontend verification is `tsc --noEmit`,
>    `next lint`, `next build`, `npm run perf:budget` and Playwright.
> 6. **`_apply_note_diff` must not be promoted as-is** (§Phase 3). It resolves
>    paths raw and takes no git snapshot, because its only caller guards and
>    snapshots first. Handed to an agent tool unchanged it becomes a write path
>    accepting `../` escapes with no undo point. Use the public `edit_note`
>    added in 4d5706d instead.

## Context

Testing surfaced three problems with the chat feature. Each has a concrete root cause in the code, not a tuning issue.

**Chat is not dynamic** because it has *zero conversation memory*. `ChatAgent.stream_chat(message, model, course)` (`backend/agent/runtime.py:259`) takes one string; every adapter starts `messages = [{"role": "user", ...}]` from scratch (`anthropic_api.py:152`, `openai_compat.py:189`); the WS loop keeps no history (`backend/features/chat/router.py:92-104`); and the frontend opens **a new WebSocket per message** (`web/lib/chat.tsx:83`). The transcript on screen is cosmetic — the model has never seen it, so "what about the second one?" is unanswerable by construction. `prompts/chat.md` compounds it: 25 lines mandating *"Answer ONLY from tool results"* and a verbatim `"That's not in your notes."`, with no date, no user context, no history block.

**Chat is messy** because the protocol is implemented twice — `web/lib/chat.tsx` and a near-copy `CourseChat` inside `web/components/study/CourseHub.tsx:46-185`. Every frame change needs two edits, and they have already drifted (the error path pops one pending bubble in one and two in the other).

**Retrieval is busted** — four real defects in `backend/rag/retrieve.py`:

1. Filters run *after* the pool is cut (L145 fetches an unfiltered top-20, L169 filters). `VaultIndex.query` accepts a chroma `where` clause `retrieve` never passes, so a `course="CS201"` query whose pool holds other courses returns **zero hits** while matching chunks sit in the index.
2. No similarity floor, and similarity is discarded — RRF fuses on rank alone (L164), so 0.95 and 0.31 score identically and a nonsense query still returns 8 confident-looking chunks.
3. Link expansion breaks the `k` contract (L186): unbounded `score: 0.0` pseudo-hits whose whole body is one title line, landing in the model's prompt as if they were evidence.
4. Recency is asymmetric — `exp(-age/45)` deletes anything over ~3 months in `10-Daily/`, while an *undated* note gets `1.0` and outranks everything dated.

**No feedback** even though the events already exist. `ToolUsed(name)` is emitted at `anthropic_api.py:200` and `openai_compat.py:240`, then dropped — `stream_chat` branches only on `TextDelta`/`UsageReported` (`runtime.py:302-309`). The default adapter (`ClaudeSDKAdapter`) never emits it at all. The bottleneck is the type: `ChatRunner = Callable[[str], AsyncIterator[str]]` (`router.py:17`) — a stream of bare strings cannot carry anything else.

**Outcome:** chat becomes a real agent — persistent threads with history, a visible and inspectable trace of what it is doing, honest retrieval that returns nothing when it has nothing, and the ability to act on the vault behind an inline approval card.

**Binding invariants:** **I1** every vault mutation goes through `backend/vault/writer.py`; **I2** git pre-apply snapshot; **I3** `99-Private/` + `#no-ai` never indexed or sent; **I6** citations on every RAG answer.

**Branch:** flat `feature/` branches, three phases landing in order (see correction 3). Phase 1 is the enabling refactor; 2 and 3 both depend on it.

---

## Phase 1 — The chat spine

### 1.1 Structured event protocol

`backend/agent/adapters.py` — replace `ToolUsed(name)` (L128-132) with two events so a chip can appear *while* a tool runs:

```python
@dataclass(frozen=True)
class ToolStarted:   id: str; name: str; args: dict[str, Any]
@dataclass(frozen=True)
class ToolFinished:  id: str; name: str; summary: ToolSummary | None; error: str | None = None
```

Add an optional `summarize: Callable[[dict, Any], ToolSummary] | None = None` field to `ToolSpec` (L61-78). The adapter calls it after dispatch. This keeps presentation next to the tool and leaves the MCP dispatch contract (`text_result`/`flatten_tool_result`) untouched, which matters because the same `ToolSpec`s are re-exposed over stdio MCP (`backend/agent/mcp_server.py`).

Emit sites:

- `anthropic_api.py:198-208` and `openai_compat.py:238-248` — already dispatch in a loop; wrap with start/finish. Both already have the tool-call id (`tool_use_id` / `tool_call_id`) to use as `ToolStarted.id`.
- `ClaudeSDKAdapter._run_with_tools` (`adapters.py:264-310`) — currently reads only `TextBlock`. Handle `ToolUseBlock`/`ToolResultBlock` (present at `claude_agent_sdk/types.py:937,946`) from `AssistantMessage.content` and the `content_block_start` stream event. **This is the one place the SDK's opacity bites**: `summarize` cannot run here because the SDK dispatches handlers internally. Fix by having the handler stash its summary on a per-run `contextvars.ContextVar` keyed by tool name, which the adapter reads on the matching `ToolResultBlock`. If that proves unreliable in a spike, fall back to name-and-args-only chips for this adapter.

### 1.2 Wire protocol

`backend/features/chat/router.py` — `ChatRunner` becomes `Callable[..., AsyncIterator[ChatEvent]]`. Delete the `_call_runner` signature shim (L20-49); it exists only for legacy one-arg fakes, and those tests get updated. Keep `_connected` (L52-61) and the `WebSocketDisconnect`-before-broad-`except` ordering (L105-124) exactly as-is — both are load-bearing regression fixes.

Inbound: `{type:"message", text, model?, course?, thread_id?}`, `{type:"cancel"}`, `{type:"confirm_response", id, approved}` (Phase 3).

Outbound:

```
{type:"thread",    thread_id, title}
{type:"delta",     text}
{type:"tool",      id, name, phase:"start"|"end", label, detail?}
{type:"citations", items:[{path,title,heading,page,slide,score}]}
{type:"done",      message_id}
{type:"error",     detail}
```

`citations` makes I6 structural rather than prompt-enforced, and retires the regex scraping in `web/lib/citations.tsx:13-30` (whose own docstring says it exists only because the protocol carries no citations field).

### 1.3 Persistent threads

Two additive tables in `backend/core/db.py`'s `SCHEMA` (L13-244). `CREATE TABLE IF NOT EXISTS` means **no `init_schema` migration guard is needed** — unlike the `ALTER`-based migrations at L268-332.

```sql
CREATE TABLE IF NOT EXISTS chat_threads (
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    title      TEXT NOT NULL DEFAULT '',
    course     TEXT                       -- NULL = global; set = Course Hub thread
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id      TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    role           TEXT NOT NULL CHECK (role IN ('user','assistant')),
    text           TEXT NOT NULL,
    steps_json     TEXT NOT NULL DEFAULT '[]',
    citations_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread ON chat_messages(thread_id, id);
```

`steps_json` persists the tool trace so reloading a thread restores its chips, not just its text.

New `backend/features/chat/store.py` — `create_thread`, `list_threads`, `thread_messages`, `append_message`, `rename_thread`, `delete_thread`. Model it on `backend/features/automations/store.py`. New REST routes alongside the socket: `GET/POST /api/chat/threads`, `GET/PATCH/DELETE /api/chat/threads/{id}`.

`stream_chat(history, model=None, course=None, thread_id=None)` replaces the single-string signature. `AgentAdapter.run` takes `messages: Sequence[Message]` instead of `user_message: str`:

- `AnthropicAPIAdapter` / `OpenAICompatAdapter` — mechanical; they already build a `messages` list.
- `ClaudeSDKAdapter` — hold one `ClaudeSDKClient` per live thread and call `client.query()` per turn (the client carries session state). For a thread resumed after restart, use `ClaudeAgentOptions.resume` (`claude_agent_sdk/types.py:1790`) with the stored session id, falling back to prompt-serialized history if resume fails. **Spike this first** — it is the riskiest unknown in Phase 1.

History budget: last 20 messages or 24k chars, oldest-first truncation, always keeping the current user turn. No summarization — YAGNI.

### 1.4 System prompt

Rewrite `backend/agent/prompts/chat.md` for a grounded *agent*, not a lookup box. Keep rules 2 (cite) and 4 (privacy) verbatim. Replace rule 1's *"Answer ONLY from tool results"* with: search the vault whenever the question touches the user's life/notes/courses; answer directly from your own knowledge otherwise, and say which you did. Replace rule 3's mandated string with guidance to say plainly that the vault has nothing on it. Add `{{TODAY}}` alongside the existing `{{PRIVATE_DIR}}` substitution (`runtime.py:48-58` — keep `str.replace`, not `str.format`; the existing comment explains why). Tell the model to pass **self-contained** `search_vault` queries — with history in context this is the LLM query-rewriting step, done for free by the main model.

### 1.5 Frontend

- `web/lib/chat.tsx` — one long-lived socket with reconnect/backoff, replacing new-WS-per-message (L83). `ChatProvider` gains a `course` prop and a `thread_id`. `ChatMessage` gains `steps: ToolStep[]` and `citations: Citation[]`.
- **Delete `CourseChat` from `web/components/study/CourseHub.tsx:46-185`**; `CourseHub` mounts `<ChatProvider course={code}>` and renders the shared `ChatPanel`. This is the single largest "messy" fix — one protocol implementation instead of two.
- New `web/components/chat/ToolTrace.tsx` — collapsed chips (`▸ searched vault · 6 results`) expanding to the query, hit paths, and scores. Replaces the static `Pending()` at `ChatPanel.tsx:29-36`.
- `web/lib/citations.tsx` — render chips from the `citations` frame; keep the existing regex only to *strip* inline `[path.md]` markers from displayed text so they don't double-render.
- Thread sidebar on `web/app/(dashboard)/chat/page.tsx` — list, new, rename, delete.

---

## Phase 2 — Retrieval

All in `backend/rag/`.

1. **Push filters into the pool.** Pass `where={"course": course}` to `index.query` (`retrieve.py:145`; `VaultIndex.query` at `index.py:247` already accepts it) and apply the same predicate to the BM25 candidate list before ranking. Tags stay post-filtered (chroma can't do Obsidian nested-tag semantics over the comma-joined `tags` string) but over-fetch the pool when tags are set. Fixes the starvation bug.
2. **Keep similarity and add a floor.** Carry the vector `score` (`index.py:247-261`) through fusion; drop hits below `MIN_SIMILARITY` unless BM25 matched strongly. **Return `[]` when nothing clears** — this is what makes "not in your notes" honest instead of prompt-enforced, and it is what stops the model citing noise.
3. **Bound link expansion.** Cap at `MAX_LINK_EXPANSIONS`, tag each `kind: "link"`, and return them as a separate `related` list rather than concatenated into `top` (`retrieve.py:186`) so `k` means `k`. Update the `search_vault` payload (`runtime.py:135-149`) to keep `results` and `related` distinct.
4. **Fix recency.** Undated notes get `RECENCY_UNDATED_MULTIPLIER` (~0.5), not `1.0` (`retrieve.py:36-37`). Raise τ to ~120 days and floor the decay (`max(decay, 0.15)`) so an old-but-highly-relevant note is demoted, not deleted.
5. **Cache the link index.** `build_link_index` walks the whole vault on *every* `retrieve()` call (L185). Memoise on `VaultIndex` keyed by `(_version, collection.count())`, exactly as `all_chunks`/`bm25` already are (`index.py:89-126`), and invalidate on upsert/delete.
6. **Cross-encoder rerank.** New `backend/rag/rerank.py` — `CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")` from the already-installed `sentence-transformers` (`pyproject.toml:45`), so no new dependency. Rerank the fused pool (top ~30) down to `k`. Lazy-load on the existing `ChatAgent.warm` thread (`runtime.py:224-243`) so the `ready` gate already covers it. Fall back to fusion order when the model is unavailable or `HF_HUB_OFFLINE` blocks it. Pre-bake weights in the desktop bundle alongside the bge embedder (`.github/workflows/_package.yml:50-56`, `desktop/lib/paths.js:35`).

> **Trade-off accepted:** the cross-encoder pins torch in the desktop bundle, cutting against the torch-drop follow-up. The ONNX-quantized route (~25MB, `onnxruntime`) would have avoided it at some quality cost. Chose quality; the torch drop now has a second blocker, not one.

Constants (`RRF_K`, `POOL_SIZE`, `RECENCY_TAU_DAYS`, `MIN_SIMILARITY`, `MAX_LINK_EXPANSIONS`, rerank on/off) move to one documented block, with rerank toggleable via settings.

---

## Phase 3 — Write tools

New `backend/agent/tools_write.py`, appended to the chat tool belt next to `build_automation_tools` (`runtime.py:290-292`). Every handler is a **thin wrapper** over an existing function in `backend/vault/writer.py` — no new write code, so I1/I2 hold by construction:

| Tool | Wraps |
|---|---|
| `create_task` | `append_capture` (writer.py:193) |
| `complete_task` / `reschedule_task` | `toggle_task_line` / `update_task_line` (L126, L155) |
| `append_to_note` | `update_note` (L455) |
| `create_note` | `create_note` (L431) |
| `edit_note` | `_apply_note_diff` (L383), promoted to public |

Path safety is already handled by `guard_user_path` (L85) and the `WriterForbidden`/`WriterConflict` family (L35-51); surface those as tool errors the model can recover from. Calendar/schedule writes are **out of scope** — chat keeps deferring those to the planner's `propose_schedule`.

**Inline confirm card.** Each write tool emits `{type:"confirm", id, tool, title, diff}` and awaits an `asyncio.Future` resolved by the client's `{type:"confirm_response", id, approved}`. Approve → the writer call runs (git snapshot, I2) and the tool returns success so the agent continues in the same turn. Reject or timeout → the tool returns `"user declined"`. `DISALLOWED_TOOLS = ("Bash","Write","Edit")` (`runtime.py:41`) stays — these tools are the only write path.

```
▸ create_task · awaiting approval
┌─ Add to 20-Tasks/inbox.md ──────────┐
│ + - [ ] Email Dr. Chen re: extension │
│   📅 due 2026-08-12                  │
└──────────────────────────────────────┘
   [ Apply ]  [ Discard ]
```

New `web/components/chat/ConfirmCard.tsx` renders the diff with Apply/Discard. Only buildable because Phase 1 made the channel bidirectional.

---

## Verification

**Per phase, backend** — use the venv python; bare `python`/`pytest` fails collection:

```
.venv/Scripts/python -m pytest tests/ -q
.venv/Scripts/python -m ruff check backend/
```

New tests:

- **P1** — WS round-trip emits `tool` start/end frames in order; a second message on one socket reaches the runner with prior turns in `history`; thread rows persist and reload restores `steps_json`; history budget truncates oldest-first while keeping the current turn. Extend `tests/features/chat/test_ws_chat.py`.
- **P2** — *the starvation regression*: seed two courses, query with `course=` where the unfiltered top-20 is all the other course, assert non-empty. Plus: an irrelevant query returns `[]`; `len(results) <= k` with `related` separate; an undated daily note ranks below a recent dated one; the link-index cache builds once and invalidates on upsert. Extend `tests/rag/test_index.py` (respect its `HAS_RAG` skipif) and add `tests/rag/test_retrieve.py`.
- **P3** — a rejected confirm performs no write (assert file mtime unchanged); an approved one calls the writer with a git snapshot; `guard_user_path` refusal surfaces as a tool error, not a 500.

**Frontend**

```
cd web; npx tsc --noEmit; npm run lint; npm run build
npx playwright test          # e2e — see the keyring constraint in the harness notes
```

E2E: send two messages on one thread and assert the second answer references the first; assert a tool chip renders and expands; assert the confirm card blocks the write until Apply.

**End to end, manually** — `argus serve` (or the desktop shell), then in `/chat`:

1. "What's on my plate this week?" → tool chips appear *during* the search, expand to real paths and scores.
2. Follow up with "what about the second one?" → answered from history. *This is the headline fix.*
3. Ask something the vault has nothing on → says so plainly, no invented citations (P2's floor, not the prompt).
4. Open a Course Hub chat → same UI, scoped to the course, no duplicate implementation behind it.
5. "Add a task to email Dr. Chen by Friday" → confirm card with a diff; Discard writes nothing, Apply writes and continues the turn.
6. Reload the page → thread, text, chips, and citations all restore.
