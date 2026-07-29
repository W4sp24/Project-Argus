"""Tests for the /ws/chat WebSocket bridge (fake agent injected)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app


async def fake_runner(message: str) -> AsyncIterator[str]:
    yield "Dijkstra finds "
    yield "shortest paths [50-Reference/algorithms.md]"


async def failing_runner(message: str) -> AsyncIterator[str]:
    raise RuntimeError("agent exploded")
    yield  # pragma: no cover


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    vault = tmp_path / "vault"
    vault.mkdir()
    return TestClient(create_app(Settings(_vault_path=vault), chat_runner=fake_runner))


def test_ws_chat_streams_multiple_deltas_then_done(client: TestClient) -> None:
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "how do I find shortest paths?"})
        frames = [ws.receive_json() for _ in range(3)]

    deltas = [frame for frame in frames if frame["type"] == "delta"]
    assert len(deltas) > 1, "must stream more than one delta chunk"
    assert frames[-1] == {"type": "done"}
    assert "[50-Reference/algorithms.md]" in "".join(delta["text"] for delta in deltas)


def test_ws_chat_rejects_empty_message(client: TestClient) -> None:
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "  "})
        assert ws.receive_json()["type"] == "error"


def test_ws_chat_passes_model_to_model_aware_runner(tmp_path: Path) -> None:
    seen_models: list[str | None] = []

    async def model_runner(message: str, model: str | None = None) -> AsyncIterator[str]:
        seen_models.append(model)
        yield f"via {model or 'default'}"

    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=model_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "hi", "model": "claude-haiku"})
        assert ws.receive_json()["text"] == "via claude-haiku"
        assert ws.receive_json()["type"] == "done"
        ws.send_json({"message": "hi again"})
        assert ws.receive_json()["text"] == "via default"
        assert ws.receive_json()["type"] == "done"
    assert seen_models == ["claude-haiku", None]


def test_ws_chat_model_field_safe_with_legacy_runner(client: TestClient) -> None:
    """A frame carrying ``model`` must not break single-argument runners."""
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "shortest paths?", "model": "claude-haiku"})
        frames = [ws.receive_json() for _ in range(3)]
    assert frames[-1] == {"type": "done"}


def test_ws_chat_surfaces_agent_errors(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=failing_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "hi"})
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert "agent exploded" in frame["detail"]


class _DroppingSocket:
    """Starlette's exact behaviour when the browser goes away mid-stream.

    Reproduced rather than mocked loosely, because the bug lives in the *order*
    of these states: ``send`` on a dropped client raises ``WebSocketDisconnect``
    **after** flipping the state to DISCONNECTED, and any further send then
    raises a plain ``RuntimeError`` that ``except WebSocketDisconnect`` does not
    catch.
    """

    def __init__(self) -> None:
        from starlette.websockets import WebSocketState

        self.application_state = WebSocketState.CONNECTED
        self.client_state = WebSocketState.CONNECTED
        self.sent: list[dict] = []

    async def accept(self) -> None:
        return None

    async def receive_json(self) -> dict:
        from starlette.websockets import WebSocketState

        if self.application_state is not WebSocketState.CONNECTED:
            raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')
        return {"message": "a long question"}

    async def send_json(self, data: dict) -> None:
        from starlette.websockets import WebSocketDisconnect, WebSocketState

        if self.application_state is not WebSocketState.CONNECTED:
            raise RuntimeError('Cannot call "send" once a close message has been sent.')
        self.sent.append(data)
        if data.get("type") == "delta":
            # The client dropped. Starlette catches uvicorn's ClientDisconnected
            # (an OSError), marks the socket dead, and re-raises as this.
            self.application_state = WebSocketState.DISCONNECTED
            self.client_state = WebSocketState.DISCONNECTED
            raise WebSocketDisconnect(code=1006)


@pytest.mark.anyio
async def test_a_disconnect_mid_stream_does_not_kill_the_handler(tmp_path: Path) -> None:
    """The bug, seen live in the e2e server log.

    ``WebSocketDisconnect`` *is* an ``Exception``, so the broad handler swallowed
    the disconnect and then sent an error frame down the dead socket — and
    starlette answers that with ``RuntimeError: Cannot call "send" once a close
    message has been sent``, which escaped ``except WebSocketDisconnect`` and
    took the whole handler down with it.
    """
    from backend.features.chat.router import build_chat_router

    vault = tmp_path / "vault"
    vault.mkdir()
    router = build_chat_router(Settings(_vault_path=vault), fake_runner)
    ws_chat = router.routes[0].endpoint
    socket = _DroppingSocket()

    await ws_chat(socket)  # must return cleanly, not raise

    assert [frame["type"] for frame in socket.sent] == ["delta"]
    assert not any(frame["type"] == "error" for frame in socket.sent), (
        "an error frame must never be sent to a socket already known to be closed"
    )
