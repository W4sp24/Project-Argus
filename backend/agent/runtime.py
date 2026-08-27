"""Argus's chat agent: provider-agnostic, with in-process vault tools.

Tools are read-only (P1 agent) and every result carries the metadata the model
needs for citations (invariant I6). Which engine runs them depends on the
registry entry the caller names — the Claude Code CLI on the user's
subscription (invariant I5, still the default), the Anthropic API on a key, or
any OpenAI-compatible endpoint including local Ollama. See
:mod:`backend.agent.adapters`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import AsyncIterator, Iterable, Sequence
from datetime import date
from pathlib import Path
from typing import Any

import frontmatter

from backend.agent.adapters import (
    Message,
    Notice,
    TextDelta,
    ToolFinished,
    ToolSpec,
    ToolStarted,
    ToolSummary,
    UsageReported,
    json_schema,
    resolve_run_target,
    text_result,
)
from backend.agent.history import budget_history
from backend.agent.text_tool_calls import BUILTIN_CHAT_TOOL_NAMES
from backend.core.config import Settings
from backend.core.taxonomy import Taxonomy
from backend.rag.index import VaultIndex
from backend.telemetry.audit import log_prompt
from backend.vault.paths import is_indexable
from backend.vault.privacy import is_no_ai_text, is_private_path, is_visible

logger = logging.getLogger("argus.rag")

MODEL = "claude-opus-4-8"
PROMPT_PATH = Path(__file__).parent / "prompts" / "chat.md"
MAX_NOTE_CHARS = 20_000
# How many paths one `list_notes` call may return. A vault is thousands of
# notes; handing all of them to a model burns the context the answer needs
# and buys nothing, since anything past the first screenful is noise. The
# reply says when it truncated, so the model narrows rather than guesses.
MAX_LIST_PATHS = 50
# A search -> read -> re-search cycle costs three turns before the model has
# said anything, and a weaker model spends more of them recovering from a thin
# first search. Eight left too little room to escalate; the last turn is now
# forced to text (see OpenAICompatAdapter.run), so a higher bound buys real
# looking rather than a longer silence.
MAX_TURNS = 12
DISALLOWED_TOOLS = ("Bash", "Write", "Edit")  # read-only agent (P1)
# Upper bound on waiting for the background index warm before searching anyway.
# Generous: a cold embedding-model load is ~20s, and proceeding early means a
# failed search rather than a slow one.
WARM_TIMEOUT_SECONDS = 90.0


def _load_system_prompt(taxonomy: Taxonomy, today: date | None = None) -> str:
    """``chat.md`` with the taxonomy's folder names and today's date templated in.

    The prompt tells the model which folder is off-limits (I3); if the
    taxonomy is configurable, a prompt naming the wrong folder would actively
    mislead the model into thinking a *different* (possibly non-private)
    folder is the protected one. Simple string substitution, not str.format,
    so the markdown's own braces (none today, but future-proof) are never at
    risk of a KeyError.

    ``{{TODAY}}`` is substituted for the same reason the folder name is: a
    model with no date cannot resolve "this week" or "since Friday" against a
    vault whose notes are all dated, and would quietly answer as of its
    training cutoff instead.
    """
    return (
        PROMPT_PATH.read_text(encoding="utf-8")
        .replace("{{PRIVATE_DIR}}", taxonomy.private)
        .replace("{{TODAY}}", (today or date.today()).isoformat())
    )


def _tool_text(payload: Any) -> dict[str, Any]:
    """Wrap a payload as an MCP text content result."""
    return text_result(payload)


# A search's cited paths ride the trace persisted per chat message and sent
# over the websocket; capping keeps one broad query (up to 8 hits, plus any
# future link expansion) from bloating either.
MAX_SUMMARY_PATHS = 8


def _parse_json(result_text: str) -> Any | None:
    """Best-effort JSON parse of a flattened tool result, or None.

    Handlers sometimes return plain text instead of a JSON payload (read_note
    on failure returns ``"error: ..."``), and summarizers must be total — a
    summarizer that raises on that just falls back to a bare ``ToolSummary``
    via :func:`summarize_tool_result`, silently losing the detail a chip could
    otherwise show.
    """
    try:
        return json.loads(result_text)
    except (json.JSONDecodeError, TypeError):
        return None


def _capped_unique_paths(paths: Iterable[Any]) -> tuple[str, ...]:
    """Dedupe, drop falsy values, and cap at :data:`MAX_SUMMARY_PATHS`."""
    seen: list[str] = []
    for path in paths:
        if not path or str(path) in seen:
            continue
        seen.append(str(path))
        if len(seen) >= MAX_SUMMARY_PATHS:
            break
    return tuple(seen)


def _summarize_search_vault(args: dict[str, Any], result_text: str) -> ToolSummary:
    """I6 (every citation traces to a tool result) becomes machine-checkable
    here: the paths a chip would show are exactly the ones parsed back out of
    the same JSON the model was handed."""
    query = str(args.get("query") or "")
    payload = _parse_json(result_text)
    results = payload.get("results") if isinstance(payload, dict) else None
    if not results:
        # Finding nothing is a successful search, not a failed call.
        detail = f"no matches for {query!r}" if query else "no matches"
        return ToolSummary(label="search_vault", detail=detail)
    paths = _capped_unique_paths(hit.get("path") for hit in results if isinstance(hit, dict))
    return ToolSummary(label="search_vault", detail=query, paths=paths)


def _summarize_read_note(args: dict[str, Any], result_text: str) -> ToolSummary:
    path = str(args.get("path") or "")
    return ToolSummary(
        label="read_note",
        detail=path,
        paths=(path,) if path else (),
        ok=not result_text.startswith("error:"),
    )


def _summarize_list_notes(args: dict[str, Any], result_text: str) -> ToolSummary:
    """Deliberately carries no ``paths``.

    Citation chips are derived from the trace (the plan's correction #1), which
    makes I6 structural rather than prompt-enforced. A listed path is not a
    read one — the model has seen only a filename — so putting them here would
    manufacture citations for content nothing has looked at.
    """
    payload = _parse_json(result_text)
    paths = payload.get("paths") if isinstance(payload, dict) else None
    where = str(args.get("folder") or "the vault")
    count = len(paths) if isinstance(paths, list) else 0
    return ToolSummary(label="list_notes", detail=f"{count} notes in {where}")


def _summarize_list_tasks(_args: dict[str, Any], result_text: str) -> ToolSummary:
    payload = _parse_json(result_text)
    buckets = payload.values() if isinstance(payload, dict) else []
    count = sum(len(bucket) for bucket in buckets if isinstance(bucket, list))
    return ToolSummary(label="list_tasks", detail=f"{count} tasks")


def build_vault_tools(
    settings: Settings,
    index: VaultIndex,
    model_label: str = MODEL,
    ready: threading.Event | None = None,
    course: str | None = None,
    sources: list[str] | None = None,
) -> list[ToolSpec]:
    """The read-only tool belt shared by chat, and re-exposed over MCP.

    ``model_label`` only labels the audit rows (§ ``/api/audit`` reports which
    model read which paths), so it follows whichever model actually ran.

    ``ready``, when given, is set once :meth:`ChatAgent.warm` has finished
    loading the embedding model on its background thread. ``search_vault``
    waits on it, because using the chroma client from the event loop while that
    load is still in flight fails with a bare
    ``'RustBindingsAPI' object has no attribute 'bindings'``.

    This only became reachable when non-Claude providers arrived: warming
    starts when the chat socket connects, and the Claude Code CLI's spin-up was
    always slow enough to hide it. A local Ollama model answers immediately and
    loses the race, so the first question after opening chat would come back
    with no citations at all. ``None`` means nothing is warming (the MCP server
    and tests), so nothing waits.

    ``course``, when given, is a *fixed* retrieval scope: every
    ``search_vault`` call is filtered to it regardless of what the model
    passes as its own ``course`` tool argument. This is how the Course Hub's
    per-course chat (§4) stays scoped to the course the user opened, rather
    than trusting the model to always remember and pass it. Global chat (the
    dock, ``/chat``) passes ``None`` here, so it keeps the model's own
    judgment call on when to filter by course.

    ``sources``, likewise, is a *fixed* per-file scope: the vault-relative
    paths the user ticked in the Course Hub's SOURCES rail. It narrows
    ``course`` rather than competing with it, and the model is told about it
    in the tool description — a model that thinks it can see the whole course
    will confidently report that something is absent when it was simply not
    selected. ``None`` means no per-file restriction; an empty list means the
    user has deselected everything, and is honoured as such.
    """
    scoped_note = ""
    if sources:
        scoped_note = (
            " The user has restricted this conversation to "
            f"{len(sources)} specific file(s); results come only from those."
        )

    async def _await_index() -> None:
        if ready is not None and not ready.is_set():
            # to_thread, not Event.wait directly: blocking the event loop here
            # would also stall the deltas already streaming to the browser.
            await asyncio.to_thread(ready.wait, WARM_TIMEOUT_SECONDS)

    async def search_vault(args: dict[str, Any]) -> dict[str, Any]:
        from backend.rag.retrieve import retrieve

        query = str(args.get("query") or "").strip()
        if not query:
            # Named, with an example. Indexing this key blind raises KeyError,
            # which reaches the model as `error: 'query'` — not enough to
            # correct from, so it guesses again and the step budget drains.
            return _tool_text(
                'error: search_vault needs a non-empty "query" string, '
                'e.g. {"query": "dijkstra shortest path"}'
            )

        await _await_index()
        # A fixed course (Course Hub) always wins over whatever the model
        # itself passed — the user already scoped this whole conversation to
        # one course by opening it from there.
        effective_course = course or (str(args["course"]) if args.get("course") else None)
        if sources is not None and not sources:
            return _tool_text(
                {
                    "results": [],
                    "note": "no sources are selected — every file is unticked in the "
                    "SOURCES rail, so there is nothing to search",
                }
            )
        # to_thread, not a direct call: retrieve() is synchronous and can take
        # seconds against a cold index. On the MCP stdio server that would stall
        # the whole event loop; in chat it would stall the deltas already
        # streaming to the browser.
        hits = await asyncio.to_thread(
            retrieve,
            index,
            query,
            settings.vault_path,
            k=8,
            course=effective_course,
            paths=sources,
            taxonomy=settings.taxonomy,
            rerank=settings.rerank_enabled,
        )
        if not hits:
            return _tool_text({"results": [], "note": "no matches in the vault"})
        log_prompt(
            settings.db_path,
            "chat",
            model_label,
            [str(hit["meta"].get("path")) for hit in hits if hit["meta"].get("path")],
        )
        return _tool_text(
            {
                "results": [
                    {
                        "text": hit["text"][:1500],
                        "path": hit["meta"].get("path"),
                        "title": hit["meta"].get("title"),
                        "heading": hit["meta"].get("heading"),
                        "page": hit["meta"].get("page"),
                        "slide": hit["meta"].get("slide"),
                    }
                    for hit in hits
                ]
            }
        )

    async def read_note(args: dict[str, Any]) -> dict[str, Any]:
        if not str(args.get("path") or "").strip():
            return _tool_text(
                'error: read_note needs a "path" string — a vault-relative path as '
                "returned by search_vault or list_notes, "
                'e.g. {"path": "15-Courses/CS201/Lecture-03.md"}'
            )
        rel_path = str(args["path"]).replace("\\", "/")
        if not is_indexable(rel_path, taxonomy=settings.taxonomy):
            return _tool_text("error: that path is not readable")
        # `is_indexable` filters directory names and suffixes; it has no
        # opinion on `..`, so "../../elsewhere.md" walks straight out of the
        # vault. The model chooses this argument, so the containment check
        # belongs here rather than in a caller that might forget it.
        vault_root = settings.vault_path.resolve()
        file_path = (settings.vault_path / rel_path).resolve()
        if not file_path.is_relative_to(vault_root):
            return _tool_text("error: that path is not readable")
        if not file_path.is_file():
            return _tool_text(f"error: no note at {rel_path}")
        text = file_path.read_text(encoding="utf-8", errors="ignore")
        # The other half of I3. `is_indexable` is the directory half only, so a
        # `#no-ai` note living outside the private folder passed it: indexing
        # excludes such a note, meaning search can never surface the path — but
        # a model that guessed the path, or was told it, could read the file.
        # The prompt's claim that "the tools already exclude them" was true of
        # search and not of this. `is_visible` is the full check, and its own
        # docstring says an outward-facing read must use it.
        if not is_visible(rel_path, frontmatter.loads(text), taxonomy=settings.taxonomy):
            return _tool_text("error: that path is not readable")
        log_prompt(settings.db_path, "chat", model_label, [rel_path])
        return _tool_text(text[:MAX_NOTE_CHARS])

    def _note_is_visible(rel_path: str) -> bool:
        """The full I3 check for one listed path, tolerating a broken header.

        ``backend.vault.notes.list_notes`` treats unparseable frontmatter as
        "no frontmatter" and lists the note anyway. Failing closed here instead
        would make a note with a typo in its YAML silently vanish from browse —
        which is the exact complaint this tool exists to answer. So a parse
        failure falls back to scanning the raw text for the tag, which is the
        half of ``is_no_ai_text`` that does not need the header.
        """
        file_path = settings.vault_path / rel_path
        try:
            post = frontmatter.load(file_path)
        except Exception:  # noqa: BLE001 - a malformed header is not a privacy verdict
            try:
                text = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return False
            return not is_private_path(rel_path, taxonomy=settings.taxonomy) and not (
                is_no_ai_text({}, text)
            )
        return is_visible(rel_path, post, taxonomy=settings.taxonomy)

    async def list_notes(args: dict[str, Any]) -> dict[str, Any]:
        from backend.vault.notes import list_notes as walk_vault

        folder = str(args["folder"]).strip() if args.get("folder") else None
        needle = (
            str(args["name_contains"]).strip().lower() if args.get("name_contains") else None
        )

        def _collect() -> tuple[list[str], bool]:
            # Filter cheaply first, then pay the privacy check only on what
            # survives. Checking every note in the vault would re-read the
            # whole thing; capping before the check would let a `#no-ai` note
            # consume a slot and, worse, decide which paths get returned.
            paths: list[str] = []
            for note in walk_vault(
                settings.vault_path, taxonomy=settings.taxonomy, folder=folder
            ):
                if needle and needle not in note.path.lower():
                    continue
                if not _note_is_visible(note.path):
                    continue
                if len(paths) >= MAX_LIST_PATHS:
                    return paths, True
                paths.append(note.path)
            return paths, False

        # to_thread for the same reason search_vault uses it: rglob over a real
        # vault is seconds of blocking I/O, and the deltas are already
        # streaming to the browser.
        paths, truncated = await asyncio.to_thread(_collect)
        if not paths:
            where = f" under {folder}" if folder else ""
            match = f" matching {needle!r}" if needle else ""
            return _tool_text(
                {"paths": [], "note": f"no notes{where}{match} — try a broader folder or name"}
            )
        payload: dict[str, Any] = {"paths": paths}
        if truncated:
            payload["note"] = (
                f"only the {MAX_LIST_PATHS} most recently edited are shown — "
                "narrow it with folder or name_contains"
            )
        return _tool_text(payload)

    async def list_tasks(_args: dict[str, Any]) -> dict[str, Any]:
        from backend.core.db import connect, init_schema
        from backend.vault.tasks import bucketed_tasks, refresh_cache

        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            refresh_cache(conn, settings.vault_path, taxonomy=settings.taxonomy)
            buckets = bucketed_tasks(conn, today=date.today())
        finally:
            conn.close()
        return _tool_text(
            {bucket: [task.model_dump() for task in tasks] for bucket, tasks in buckets.items()}
        )

    return [
        ToolSpec(
            name="search_vault",
            description=(
                "Hybrid semantic+keyword search over the user's vault (notes and course "
                "materials). Call this before answering anything about the user's life, "
                "notes, or courses. Returns chunks with path/page/slide for citations."
                + scoped_note
            ),
            parameters=json_schema(
                {
                    "query": {"type": "string", "description": "what to search for"},
                    "course": {"type": "string", "description": "optional course code filter"},
                },
                required=["query"],
            ),
            handler=search_vault,
            summarize=_summarize_search_vault,
        ),
        ToolSpec(
            name="read_note",
            description=(
                "Read one full markdown note from the vault by its vault-relative path "
                "(as returned by search_vault)."
            ),
            parameters=json_schema({"path": {"type": "string"}}),
            handler=read_note,
            summarize=_summarize_read_note,
        ),
        ToolSpec(
            name="list_notes",
            description=(
                "List note paths in the vault, newest first, optionally under one folder "
                "and/or matching a fragment of the path. Use this when search_vault comes "
                "back thin or empty and you need to see what actually exists — for example "
                "when the user names a note, a course or a topic by a word that may be in "
                "the filename rather than the text. Returns paths only; follow up with "
                "read_note to see what one says."
            ),
            parameters=json_schema(
                {
                    "folder": {
                        "type": "string",
                        "description": (
                            "optional vault-relative folder, e.g. '15-Courses/CS201'"
                        ),
                    },
                    "name_contains": {
                        "type": "string",
                        "description": "optional case-insensitive fragment of the path",
                    },
                },
                required=[],
            ),
            handler=list_notes,
            summarize=_summarize_list_notes,
        ),
        ToolSpec(
            name="list_tasks",
            description=(
                "List the user's tasks from the vault, bucketed into overdue / today / "
                "week / someday. Takes no arguments."
            ),
            parameters=json_schema({}),
            handler=list_tasks,
            summarize=_summarize_list_tasks,
        ),
    ]


def _describe_step(summary: ToolSummary) -> str:
    """One clause describing what a single tool call did, in plain language."""
    label, detail = summary.label, summary.detail
    if label.startswith("run_automation_"):
        # The summarizer appends the run status ("Meeting prep (success)"),
        # which reads as a stutter once the clause says the same thing.
        what = detail.split(" (")[0] or "an automation"
        if summary.ok:
            return f"ran the automation {what}"
        return f"tried to run the automation {what}, without success"
    if label == "search_vault":
        return f"searched your vault for {detail}" if detail else "searched your vault"
    if label == "read_note":
        return f"read {detail}" if detail else "read a note"
    if label == "list_notes":
        return f"browsed your vault ({detail})" if detail else "browsed your vault"
    if label == "list_tasks":
        return "checked your tasks"
    return label.replace("_", " ")


def acknowledge_tool_steps(summaries: Sequence[ToolSummary]) -> str:
    """A message for a turn that ran tools and then said nothing.

    This exists because of a reported failure: the agent created a note in the
    vault and its reply never mentioned it, so from the user's side nothing had
    happened. The loop can genuinely end with no text — a model that spends its
    last step on a tool call (``openai_compat``'s turn budget) never gets to
    answer, and an empty assistant turn renders as a tool chip followed by
    silence.

    Silence is the one thing that must not happen. Reporting what ran, and that
    no answer came of it, is worse than a good answer and far better than
    nothing.
    """
    if not summaries:
        return ""
    clauses = [_describe_step(summary) for summary in summaries]
    # Deduplicate consecutive identical clauses — three searches for the same
    # phrase should read as one, not as a stutter.
    deduped: list[str] = []
    for clause in clauses:
        if not deduped or deduped[-1] != clause:
            deduped.append(clause)
    did = deduped[0] if len(deduped) == 1 else ", ".join(deduped[:-1]) + f" and {deduped[-1]}"
    writes = [summary for summary in summaries if summary.label not in BUILTIN_CHAT_TOOL_NAMES]
    paths = [path for summary in summaries for path in summary.paths]
    where = f" The file is at {paths[0]}." if writes and paths else ""
    # A write that worked is a result in itself, and framing it as a failure to
    # answer would misreport what actually happened — the exact complaint the
    # review raised, in the opposite direction.
    if writes and all(summary.ok for summary in writes):
        tail = "Tell me if you want anything else doing with it."
    else:
        tail = (
            "I could not put an answer together after that — "
            "ask again and I will pick up from here."
        )
    return f"I {did}.{where} {tail}"


class ChatAgent:
    """Streams RAG-grounded chat answers. One instance per app process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._index = VaultIndex(settings.db_path.parent / "chroma", taxonomy=settings.taxonomy)
        # Set when warming finishes, so vault searches can wait rather than
        # race the still-initializing chroma client. See build_vault_tools.
        self._ready = threading.Event()

    def warm(self) -> None:
        """Load the embedding model + chroma now, off the chat hot path.

        The first vault tool call otherwise pays ~20s of model loading inside
        the agent's event loop.
        """
        try:
            # Warming is best-effort; real errors surface on actual queries.
            # Not swallowed silently, though — a warm that fails every single
            # time (a genuinely broken index, not just "not built yet") used
            # to leave no trace anywhere a person would look.
            self._index.query("warmup", n_results=1)
        except Exception:
            logger.warning(
                "chat index warm-up failed (will retry on first real query)", exc_info=True
            )
        finally:
            # Always release waiters, including when warming failed — a search
            # that then fails on its own is far better than one that hangs.
            self._ready.set()

    async def stream_chat(
        self,
        message: str,
        model: str | None = None,
        course: str | None = None,
        sources: list[str] | None = None,
        history: Sequence[Message] | None = None,
        thread_id: int | None = None,
    ) -> AsyncIterator[str | ToolStarted | ToolFinished | Notice]:
        """Yield the turn's events, on whichever backend is chosen.

        Text arrives as plain ``str`` deltas — the shape every caller has
        always consumed — with tool events interleaved as themselves, so the
        websocket can show what the agent is doing while it does it rather
        than only what it eventually said.

        ``history`` is the *prior* conversation; ``message`` is the current
        turn and is never inside it. Trimming to what actually fits is
        :func:`backend.agent.history.budget_history`'s job, applied here so
        every caller gets the same policy without having to know it.

        ``course``, when given, fixes ``search_vault``'s retrieval scope to
        one course — see :func:`build_vault_tools`. Passed by the Course Hub
        chat frame (``backend.features.chat.router``); the global chat dock
        omits it. ``sources`` narrows that further to the files ticked in the
        Course Hub's SOURCES rail, and rides per-turn rather than being stored
        on the thread: it is a live filter the user changes mid-conversation,
        unlike the course the thread belongs to.

        ``thread_id`` is accepted but not yet used: session resume reads it
        in the commit that follows.
        """
        from backend.telemetry.usage import record_result_usage

        adapter, resolved_model = resolve_run_target(
            self._settings,
            model,
            tool_namespace="argus",
            disallowed_tools=DISALLOWED_TOOLS,
            fallback_model=MODEL,
        )
        tools = build_vault_tools(
            self._settings,
            self._index,
            model_label=resolved_model,
            ready=self._ready,
            course=course,
            sources=sources,
        )
        # Registered automations are callable from chat too, so "run my meeting
        # prep" works in the dock as well as by button. Chat only — these are
        # write actions and are deliberately absent from the MCP allow-list;
        # see backend/features/automations/tools.py.
        from backend.features.automations.tools import build_automation_tools

        tools = tools + build_automation_tools(self._settings)

        turns = budget_history([*(history or []), Message("user", message)])

        recorded = False
        # Everything the turn actually did, so a turn that ends without text
        # can still say so. See acknowledge_tool_steps.
        steps: list[ToolSummary] = []
        said_something = False
        try:
            async for event in adapter.run(
                system_prompt=_load_system_prompt(self._settings.taxonomy),
                messages=turns,
                tools=tools,
                max_turns=MAX_TURNS,
            ):
                if isinstance(event, TextDelta):
                    said_something = said_something or bool(event.text.strip())
                    yield event.text
                elif isinstance(event, ToolStarted | ToolFinished):
                    if isinstance(event, ToolFinished):
                        steps.append(event.summary)
                    yield event
                elif isinstance(event, Notice):
                    # Worth a log line as well as a frame: a model that keeps
                    # hitting the step limit is a retrieval problem to look
                    # into, and it used to leave no trace anywhere at all.
                    logger.warning("chat turn limit reached (%s): %s", resolved_model, event.detail)
                    yield event
                elif isinstance(event, UsageReported):
                    # Fire-and-forget usage logging (§14) — never breaks chat.
                    record_result_usage(
                        self._settings.db_path, "chat", event, model=resolved_model
                    )
                    recorded = True
            if steps and not said_something:
                logger.warning(
                    "chat turn ran %d tool(s) and produced no text (%s)", len(steps), resolved_model
                )
                yield acknowledge_tool_steps(steps)
        finally:
            # Every adapter yields UsageReported *last*, so closing the tab
            # mid-answer used to drop the whole turn — and the abandoned answers
            # are the long, expensive ones. The tokens were already billed by
            # the provider, so bank what the adapter counted before we stopped
            # reading. record_result_usage swallows its own errors, which is
            # what makes it safe to call while unwinding.
            if not recorded:
                partial = getattr(adapter, "partial_usage", lambda: None)()
                if partial is not None and any(partial.usage.values()):
                    record_result_usage(
                        self._settings.db_path, "chat", partial, model=resolved_model
                    )
