"""The dashboard's chat dock: a WebSocket bridge onto the agent's stream.

Lifted out of ``backend.main`` so the app factory holds no routes. The
``chat_runner`` indirection is what lets tests drive the socket with a fake
stream instead of the agent SDK.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from starlette.websockets import WebSocketState

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.chat import store

ChatRunner = Callable[[str], AsyncIterator[str]]


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


def _call_runner(
    runner: ChatRunner, message: str, model: str | None, course: str | None
) -> AsyncIterator[str]:
    """Call ``runner`` with the richest signature it accepts.

    Two optional trailing keywords have landed independently: ``model`` (§7
    model selection) and ``course`` (Course Hub scoping — forces
    ``search_vault``'s course filter in ``backend.agent.runtime`` rather than
    leaving it to the model's discretion). A runner that only knows one, or
    neither (every single-argument test fake), must keep working untouched.
    The ``TypeError`` this catches can only be a signature mismatch: it is
    raised while the async-generator function object is being *created*, not
    awaited, so no application code has run yet.
    """
    if model is not None and course is not None:
        try:
            return runner(message, model, course=course)
        except TypeError:
            pass
    if course is not None:
        try:
            return runner(message, course=course)
        except TypeError:
            pass
    if model is not None:
        try:
            return runner(message, model)
        except TypeError:
            pass
    return runner(message)


def _connected(websocket: WebSocket) -> bool:
    """Is this socket still writable?

    Sending to a socket the client has dropped raises ``RuntimeError`` rather
    than ``WebSocketDisconnect``, so the error path has to look before it sends.
    """
    return (
        websocket.application_state is WebSocketState.CONNECTED
        and websocket.client_state is WebSocketState.CONNECTED
    )


def default_chat_runner(settings: Settings) -> ChatRunner:
    """Lazily build the real agent so the app boots without agent deps."""
    import threading

    from backend.agent.runtime import ChatAgent

    agent = ChatAgent(settings)
    threading.Thread(target=agent.warm, daemon=True).start()
    return agent.stream_chat


def build_chat_router(settings: Settings, chat_runner: ChatRunner | None) -> APIRouter:
    router = APIRouter()

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

        Frames in: {message, model?, course?} — ``model`` (a registry name,
        §7) and ``course`` (Course Hub scoping, forces the vault search to
        one course rather than leaving it to the model) are both optional and
        flow through to runners that accept them; runners with the legacy
        single-argument signature keep working.
        Frames out: {type: "delta", text} ... {type: "done"} | {type: "error", detail}.
        """
        await websocket.accept()
        runner = chat_runner or default_chat_runner(settings)
        try:
            while True:
                payload = await websocket.receive_json()
                message = str(payload.get("message", "")).strip()
                model = str(payload.get("model") or "").strip() or None
                course = str(payload.get("course") or "").strip() or None
                if not message:
                    await websocket.send_json({"type": "error", "detail": "empty message"})
                    continue
                try:
                    stream = _call_runner(runner, message, model, course)
                    async for delta in stream:
                        await websocket.send_json({"type": "delta", "text": delta})
                    await websocket.send_json({"type": "done"})
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
