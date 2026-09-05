"""The dashboard's chat dock: a WebSocket bridge onto the agent's stream.

Lifted out of ``backend.main`` so the app factory holds no routes. The
``chat_runner`` indirection is what lets tests drive the socket with a fake
stream instead of the agent SDK.
"""

from __future__ import annotations

import inspect
import logging
import sqlite3
import threading
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from backend.agent.adapters import Message, Notice, ToolFinished, ToolStarted
from backend.agent.text_tool_calls import is_only_a_tool_call
from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.chat import store
from backend.vault.paths import is_indexable

logger = logging.getLogger("argus.chat")

# ``backend.agent.adapters`` is stdlib-only (no chromadb, no SDK), so importing
# it here is safe -- unlike ``backend.agent.runtime``, which pulls in VaultIndex
# and is why ``default_chat_runner`` imports lazily.
ChatEvent = str | ToolStarted | ToolFinished
ChatRunner = Callable[..., AsyncIterator[ChatEvent]]


class ChatMessageInfo(BaseModel):
    """One stored turn, as returned to the transcript view. Mirrors the
    store's message dict (``backend.features.chat.store``) exactly."""

    id: int
    role: str
    text: str
    model: str | None = None
    tools: list[dict] = []
    created_at: str


class ThreadInfo(BaseModel):
    """One thread's metadata, as returned to the sidebar. Mirrors the
    store's thread dict exactly -- ``session_id``/``session_provider``/
    ``session_model`` are internal resume plumbing (see
    ``backend.features.chat.store``) and must never reach a browser, so
    they have no fields here at all rather than being set to ``None``.
    """

    id: int
    title: str
    course: str | None = None
    archived: bool
    created_at: str
    updated_at: str
    message_count: int


class ThreadCreate(BaseModel):
    title: str | None = None
    course: str | None = None


class ThreadPatch(BaseModel):
    title: str | None = None
    archived: bool | None = None


def _accepted_kwargs(runner: ChatRunner) -> frozenset[str] | None:
    """Which keyword names ``runner`` declares; ``None`` means it takes ``**kwargs``.

    An unreadable signature (a C callable, an exotic partial) yields the empty
    set, which degrades to calling ``runner(message)`` — the one call every
    runner has always accepted.
    """
    try:
        parameters = inspect.signature(runner).parameters
    except (TypeError, ValueError):
        return frozenset()
    names: set[str] = set()
    for name, param in parameters.items():
        if param.kind is inspect.Parameter.VAR_KEYWORD:
            return None
        if param.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            names.add(name)
    return frozenset(names)


def _call_runner(runner: ChatRunner, message: str, **extras: object) -> AsyncIterator[ChatEvent]:
    """Call ``runner`` with whichever of ``extras`` its signature declares.

    This replaces a cascade of ``try: runner(...) except TypeError`` fallbacks,
    one nested block per optional keyword. That cascade was correct — the
    ``TypeError`` could only ever be a signature mismatch, because it is raised
    while the async-generator function object is being *created*, before any
    body code runs — but it needed a branch per subset of the optional
    arguments, so it doubled in size with each one. ``history`` and
    ``thread_id`` would have taken it to sixteen.

    Asking the signature instead is both smaller and stricter: a ``TypeError``
    raised from somewhere else entirely can no longer be silently mistaken for
    "this runner doesn't take that argument". Runners that declare nothing but
    ``message`` (every legacy test fake) still get called with exactly that.
    """
    accepted = _accepted_kwargs(runner)
    kwargs = {
        name: value
        for name, value in extras.items()
        if value is not None and (accepted is None or name in accepted)
    }
    return runner(message, **kwargs)


#: A selection is a filter, not a payload. The Course Hub rail lists one
#: course's files, so this is far above any real selection while still
#: bounding what one frame can push into a chroma `$in` clause.
MAX_FRAME_SOURCES = 200


def _frame_sources(raw: object) -> list[str] | None:
    """The ``sources`` field of an inbound chat frame, or ``None``.

    ``None`` (field absent) and ``[]`` mean different things all the way down
    to :func:`backend.rag.retrieve.retrieve_result`: no restriction versus the
    user having unticked everything. Anything that is not a list is treated as
    absent rather than as an error — an older frontend sending nothing must
    keep working, which is the same tolerance ``_tool_frame`` documents in the
    other direction.
    """
    if not isinstance(raw, list):
        return None
    cleaned = [str(item).strip() for item in raw if str(item).strip()]
    return list(dict.fromkeys(cleaned))[:MAX_FRAME_SOURCES]


def _tool_frame(event: ToolStarted | ToolFinished) -> dict:
    """Render a tool event as its wire frame.

    One ``tool`` type with a ``phase`` rather than two frame types: the browser
    ignores frame types it does not know, so an older frontend against a newer
    backend degrades to text-only instead of breaking, and that tolerance is
    worth more the fewer new type names it has to cover.

    The finished frame re-checks every path through ``is_indexable``. The tools
    already exclude the private zone and ``#no-ai`` notes, so this should never
    drop anything — but a path list leaving the process is a new egress surface
    and I3 is binding, so it is cheaper to enforce twice than to reason about
    which upstream guarantee still holds after a refactor.
    """
    if isinstance(event, ToolStarted):
        return {
            "type": "tool",
            "phase": "start",
            "call_id": event.call_id,
            "name": event.name,
            "args": event.args,
        }
    summary = event.summary
    return {
        "type": "tool",
        "phase": "end",
        "call_id": event.call_id,
        "name": event.name,
        "label": summary.label,
        "detail": summary.detail,
        "paths": [path for path in summary.paths if is_indexable(path)],
        "ok": summary.ok,
    }


# Upper bound on rows pulled out of a thread to build history. The agent
# budgets far more tightly than this (backend/agent/history.py); this only
# stops a very long thread from loading its entire transcript into memory
# just to have most of it discarded a moment later.
HISTORY_FETCH_LIMIT = 100


def _open_turn(
    db: Callable[[], sqlite3.Connection],
    thread_id: int | None,
    message: str,
    course: str | None,
) -> tuple[dict, list[Message]]:
    """Resolve (or create) the thread, read prior turns, record the user's.

    History is read *before* the user's message is appended, so the returned
    list is genuinely the prior conversation and the current turn is not
    duplicated into it — the runner receives them as separate arguments.
    """
    conn = db()
    try:
        if thread_id is None:
            thread = store.create_thread(conn, title=store.derive_title(message), course=course)
        else:
            found = store.get_thread(conn, thread_id)
            if found is None:
                raise ValueError(f"no thread {thread_id}")
            thread = found
        history = [
            Message(row["role"], row["text"])
            for row in store.list_messages(conn, thread["id"], limit=HISTORY_FETCH_LIMIT)
        ]
        store.append_message(conn, thread["id"], role="user", text=message)
        # A thread made from the sidebar's "New chat" button has the
        # placeholder title until someone actually says something; this is
        # where it earns a real one.
        if thread["title"] == store.DEFAULT_TITLE:
            renamed = store.rename_thread(conn, thread["id"], store.derive_title(message))
            thread = renamed or thread
        return thread, history
    finally:
        conn.close()


#: Shown in place of a tool call that reached the transcript anyway. See
#: `_persistable_text`.
LEAKED_CALL_REPLACEMENT = (
    "I tried to use one of my tools and it did not go through. Ask again and "
    "I will have another go."
)


def _persistable_text(text_parts: list[str]) -> str:
    """The assistant's body, with a leaked tool call replaced by an apology.

    `backend.agent.text_tool_calls` sieves printed tool calls out of the stream
    before the browser ever sees them, which is the actual fix. This is the net
    underneath it: an envelope shape the sieve has not learned yet would still
    be *stored*, and a stored one is re-rendered on every reload, forever. The
    check is narrow on purpose — the whole message, and a tool the agent really
    has — so an answer that is legitimately nothing but JSON survives.
    """
    text = "".join(text_parts)
    if is_only_a_tool_call(text):
        logger.warning("a tool call reached the transcript as text; replacing it")
        return LEAKED_CALL_REPLACEMENT
    return text


def _close_turn(
    db: Callable[[], sqlite3.Connection],
    thread_id: int,
    text_parts: list[str],
    steps: list[dict],
    model: str | None,
) -> int | None:
    """Persist the assistant turn. Returns its id, or ``None`` if it was empty."""
    if not text_parts and not steps:
        return None
    conn = db()
    try:
        row = store.append_message(
            conn,
            thread_id,
            role="assistant",
            text=_persistable_text(text_parts),
            model=model,
            tools=steps,
        )
        return int(row["id"])
    finally:
        conn.close()


def _connected(websocket: WebSocket) -> bool:
    """Is this socket still writable?

    Sending to a socket the client has dropped raises ``RuntimeError`` rather
    than ``WebSocketDisconnect``, so the error path has to look before it sends.
    """
    return (
        websocket.application_state is WebSocketState.CONNECTED
        and websocket.client_state is WebSocketState.CONNECTED
    )


def default_chat_runner(
    settings: Settings, index_factory: Callable[[], Any] | None = None
) -> ChatRunner:
    """Lazily build the real agent so the app boots without agent deps.

    ``index_factory`` is the app's one shared index (``main.py``'s
    ``_default_index_factory``). Without it the agent builds a ``VaultIndex`` of
    its own -- see :meth:`backend.agent.runtime.ChatAgent.__init__`.
    """
    import threading

    from backend.agent.runtime import ChatAgent

    agent = ChatAgent(settings, index_factory=index_factory)
    threading.Thread(target=agent.warm, daemon=True).start()
    return agent.stream_chat


def build_chat_router(
    settings: Settings,
    chat_runner: ChatRunner | None,
    index_factory: Callable[[], Any] | None = None,
) -> APIRouter:
    router = APIRouter()

    # One agent for this process, built on the first connection that needs it.
    #
    # This used to be `chat_runner or default_chat_runner(settings)` *inside*
    # the socket handler, and production passes `chat_runner=None` -- so every
    # connect built a new ChatAgent, and with it a new VaultIndex, a new chroma
    # client and a new SentenceTransformer on a new warm thread (~20s, see
    # ChatAgent.warm). A reload, a STOP-then-ask, a sleep/wake or a second
    # window each paid that again and left the previous model resident, while
    # the index's all_chunks/bm25/link_index caches restarted cold every time.
    #
    # Double-checked under a lock rather than built eagerly: construction stays
    # lazy so an install without the `[rag]` extras still boots, and the
    # warm-up thread is still started exactly once. Same idiom, and the same
    # reasoning, as `backend.rag.index.make_index_factory`.
    runner_lock = threading.Lock()
    resolved_runner: list[ChatRunner] = []

    def get_runner() -> ChatRunner:
        if chat_runner is not None:
            return chat_runner
        if not resolved_runner:
            with runner_lock:
                if not resolved_runner:
                    resolved_runner.append(default_chat_runner(settings, index_factory))
        return resolved_runner[0]

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    # The websocket route is registered first and stays first: a test
    # (tests/features/chat/test_ws_chat.py) grabs it via
    # ``router.routes[0].endpoint`` to drive it directly with a fake
    # starlette-shaped socket, bypassing TestClient's websocket support for a
    # scenario TestClient cannot reproduce (a send racing a client disconnect).
    # REST routes below must not shift that index.
    @router.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket) -> None:
        """Bridge agent streaming deltas to the browser.

        Frames in: {message, model?, course?, sources?} — ``model`` (a registry
        name, §7), ``course`` (Course Hub scoping, forces the vault search to
        one course rather than leaving it to the model) and ``sources`` (the
        vault-relative files ticked in that hub's SOURCES rail) are all
        optional and flow through to runners that accept them; runners with
        the legacy single-argument signature keep working.
        Frames out: {type: "delta", text} | {type: "tool", ...} |
        {type: "notice", kind, detail} ... {type: "done"} | {type: "error", detail}.
        """
        await websocket.accept()
        runner = get_runner()
        try:
            while True:
                payload = await websocket.receive_json()
                message = str(payload.get("message", "")).strip()
                model = str(payload.get("model") or "").strip() or None
                course = str(payload.get("course") or "").strip() or None
                sources = _frame_sources(payload.get("sources"))
                raw_thread = payload.get("thread_id")
                thread_id = int(raw_thread) if isinstance(raw_thread, int | str) and (
                    str(raw_thread).strip().isdigit()
                ) else None
                if not message:
                    await websocket.send_json({"type": "error", "detail": "empty message"})
                    continue
                try:
                    thread, history = _open_turn(db, thread_id, message, course)
                    await websocket.send_json(
                        {"type": "thread", "thread_id": thread["id"], "title": thread["title"]}
                    )
                    text_parts: list[str] = []
                    steps: list[dict] = []
                    try:
                        stream = _call_runner(
                            runner,
                            message,
                            model=model,
                            course=course or thread["course"],
                            sources=sources,
                            history=history,
                            thread_id=thread["id"],
                        )
                        async for item in stream:
                            if isinstance(item, str):
                                text_parts.append(item)
                                await websocket.send_json({"type": "delta", "text": item})
                            elif isinstance(item, Notice):
                                # Not a tool step, so deliberately not in
                                # `steps`: it describes this run, not anything
                                # the vault did, and persisting it would put a
                                # non-tool row in `tools_json` for every reader
                                # of that column to special-case.
                                await websocket.send_json(
                                    {"type": "notice", "kind": item.kind, "detail": item.detail}
                                )
                            else:
                                frame = _tool_frame(item)
                                steps.append(frame)
                                await websocket.send_json(frame)
                    finally:
                        # Send-free on purpose. This runs on the disconnect path
                        # too, and a send from a `finally` on a socket already
                        # known to be gone is precisely the bug
                        # test_a_disconnect_mid_stream_does_not_kill_the_handler
                        # exists to prevent, one level deeper than where it bit.
                        #
                        # Banking a partial answer mirrors what stream_chat
                        # already does with partial_usage(): the turn happened,
                        # the tokens were spent, and a thread that loses its
                        # half-written answer because a tab closed is worse than
                        # one that keeps it.
                        message_id = _close_turn(db, thread["id"], text_parts, steps, model)
                    await websocket.send_json({"type": "done", "message_id": message_id})
                except WebSocketDisconnect:
                    # Must be re-raised before the broad handler below, which
                    # would otherwise swallow it and try to send an error frame
                    # down a socket starlette has already marked DISCONNECTED —
                    # raising RuntimeError('Cannot call "send" once a close
                    # message has been sent') straight out of the handler.
                    raise
                except Exception as exc:  # agent errors must reach the UI, not kill the socket
                    if not _connected(websocket):
                        return
                    await websocket.send_json({"type": "error", "detail": str(exc)})
        except WebSocketDisconnect:
            return
        except RuntimeError as exc:
            # starlette raises a plain RuntimeError, not WebSocketDisconnect,
            # for a send or receive on a socket that is already gone. Losing the
            # race with a closing browser tab is normal, not a server fault.
            if "close message has been sent" in str(exc) or "not connected" in str(exc).lower():
                return
            raise

    @router.get("/api/chat/threads", response_model=list[ThreadInfo])
    def list_chat_threads(course: str | None = None, archived: bool = False) -> list[ThreadInfo]:
        conn = db()
        try:
            threads = store.list_threads(conn, course=course, include_archived=archived)
        finally:
            conn.close()
        return [ThreadInfo(**t) for t in threads]

    @router.post("/api/chat/threads", response_model=ThreadInfo, status_code=201)
    def create_chat_thread(request: ThreadCreate) -> ThreadInfo:
        # No title from the caller yet is normal, not an error: the socket
        # derives a real one from the first user message via
        # store.derive_title once the conversation actually starts (a thread
        # created from a sidebar "New chat" button has no message to derive
        # from at creation time). DEFAULT_TITLE is the same placeholder
        # derive_title itself falls back to, so a thread never has two
        # different "untitled" spellings depending on how it was made.
        title = (request.title or "").strip() or store.DEFAULT_TITLE
        conn = db()
        try:
            thread = store.create_thread(conn, title=title, course=request.course)
        finally:
            conn.close()
        return ThreadInfo(**thread)

    @router.get("/api/chat/threads/{thread_id}")
    def get_chat_thread(thread_id: int) -> dict:
        conn = db()
        try:
            thread = store.get_thread(conn, thread_id)
            if thread is None:
                raise HTTPException(status_code=404, detail="thread not found")
            messages = store.list_messages(conn, thread_id)
        finally:
            conn.close()
        return {
            "thread": ThreadInfo(**thread),
            "messages": [ChatMessageInfo(**m) for m in messages],
        }

    @router.patch("/api/chat/threads/{thread_id}", response_model=ThreadInfo)
    def patch_chat_thread(thread_id: int, request: ThreadPatch) -> ThreadInfo:
        sent = request.model_fields_set
        if not sent:
            raise HTTPException(status_code=400, detail="nothing to update")
        if "title" in sent and (request.title is None or not request.title.strip()):
            # A blank title makes a thread unfindable in the sidebar -- the
            # whole reason titles are derived rather than left optional.
            raise HTTPException(status_code=400, detail="title must not be blank")

        conn = db()
        try:
            thread = store.get_thread(conn, thread_id)
            if thread is None:
                raise HTTPException(status_code=404, detail="thread not found")
            if "title" in sent:
                thread = store.rename_thread(conn, thread_id, request.title.strip())
            if "archived" in sent:
                thread = store.archive_thread(conn, thread_id, archived=bool(request.archived))
        finally:
            conn.close()
        assert thread is not None  # existence already confirmed by get_thread above
        return ThreadInfo(**thread)

    @router.delete("/api/chat/threads/{thread_id}")
    def delete_chat_thread(thread_id: int) -> dict:
        conn = db()
        try:
            # delete_thread() returns None whether or not the id existed, so
            # existence has to be checked first to give a real 404 instead of
            # a false "ok" on an id that was never there.
            if store.get_thread(conn, thread_id) is None:
                raise HTTPException(status_code=404, detail="thread not found")
            store.delete_thread(conn, thread_id)
        finally:
            conn.close()
        return {"status": "ok"}

    return router
