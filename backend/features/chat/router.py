"""The dashboard's chat dock: a WebSocket bridge onto the agent's stream.

Lifted out of ``backend.main`` so the app factory holds no routes. The
``chat_runner`` indirection is what lets tests drive the socket with a fake
stream instead of the agent SDK.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from backend.core.config import Settings

ChatRunner = Callable[[str], AsyncIterator[str]]


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

        Frames in: {message, model?} — ``model`` (a registry name, §7) is
        optional and flows through to runners that accept it; runners with
        the legacy single-argument signature keep working.
        Frames out: {type: "delta", text} ... {type: "done"} | {type: "error", detail}.
        """
        await websocket.accept()
        runner = chat_runner or default_chat_runner(settings)
        try:
            while True:
                payload = await websocket.receive_json()
                message = str(payload.get("message", "")).strip()
                model = str(payload.get("model") or "").strip() or None
                if not message:
                    await websocket.send_json({"type": "error", "detail": "empty message"})
                    continue
                try:
                    if model is not None:
                        try:
                            stream = runner(message, model)
                        except TypeError:  # injected runner without model support
                            stream = runner(message)
                    else:
                        stream = runner(message)
                    async for delta in stream:
                        await websocket.send_json({"type": "delta", "text": delta})
                    await websocket.send_json({"type": "done"})
                except Exception as exc:  # agent errors must reach the UI, not kill the socket
                    await websocket.send_json({"type": "error", "detail": str(exc)})
        except WebSocketDisconnect:
            return

    return router
