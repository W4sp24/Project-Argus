"""The dashboard's chat dock: a WebSocket bridge onto the agent's stream.

Lifted out of ``backend.main`` so the app factory holds no routes. The
``chat_runner`` indirection is what lets tests drive the socket with a fake
stream instead of the agent SDK.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from backend.core.config import Settings

ChatRunner = Callable[[str], AsyncIterator[str]]


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

    return router
