"""Tests for the /ws/chat WebSocket bridge (fake agent injected)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
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


def test_ws_chat_surfaces_agent_errors(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=failing_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "hi"})
        frame = ws.receive_json()
    assert frame["type"] == "error"
    assert "agent exploded" in frame["detail"]
