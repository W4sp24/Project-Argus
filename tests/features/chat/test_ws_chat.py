"""Tests for the /ws/chat WebSocket bridge (fake agent injected)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.agent.adapters import Notice
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


def _turn(ws) -> list[dict]:
    """Every frame of one turn, up to and including its terminator.

    Reading a fixed count broke the moment the protocol grew a leading
    ``thread`` frame, and would break again on the next addition; reading
    until the turn actually ends is both shorter and stable.
    """
    frames = []
    while True:
        frame = ws.receive_json()
        frames.append(frame)
        if frame["type"] in ("done", "error"):
            return frames


def test_ws_chat_streams_multiple_deltas_then_done(client: TestClient) -> None:
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "how do I find shortest paths?"})
        frames = _turn(ws)

    deltas = [frame for frame in frames if frame["type"] == "delta"]
    assert len(deltas) > 1, "must stream more than one delta chunk"
    assert frames[-1]["type"] == "done"
    assert "[50-Reference/algorithms.md]" in "".join(delta["text"] for delta in deltas)


def test_ws_chat_announces_the_thread_before_the_first_delta(client: TestClient) -> None:
    """The client needs the id before any content, so a reload can reopen the
    thread even if the answer never finished arriving."""
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "how do I find shortest paths?"})
        frames = _turn(ws)

    assert frames[0]["type"] == "thread"
    assert isinstance(frames[0]["thread_id"], int)
    assert frames[0]["title"], "a thread with no title is unfindable in the sidebar"


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
        frames = _turn(ws)
        assert [f["text"] for f in frames if f["type"] == "delta"] == ["via claude-haiku"]
        assert frames[-1]["type"] == "done"
        ws.send_json({"message": "hi again"})
        frames = _turn(ws)
        assert [f["text"] for f in frames if f["type"] == "delta"] == ["via default"]
        assert frames[-1]["type"] == "done"
    assert seen_models == ["claude-haiku", None]


def test_ws_chat_passes_course_to_a_course_aware_runner(tmp_path: Path) -> None:
    """Course Hub scoping (§4): the `course` frame field must reach the
    runner, which is what forces `search_vault`'s filter in the real agent
    (backend/agent/runtime.py — covered separately in tests/agent/test_runtime.py)."""
    seen_courses: list[str | None] = []

    async def course_runner(
        message: str, model: str | None = None, course: str | None = None
    ) -> AsyncIterator[str]:
        seen_courses.append(course)
        yield f"scoped to {course or 'nothing'}"

    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=course_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "what's due?", "course": "CS201"})
        frames = _turn(ws)
        assert [f["text"] for f in frames if f["type"] == "delta"] == ["scoped to CS201"]
        assert frames[-1]["type"] == "done"
        ws.send_json({"message": "and now?"})
        frames = _turn(ws)
        assert [f["text"] for f in frames if f["type"] == "delta"] == ["scoped to nothing"]
        assert frames[-1]["type"] == "done"
    assert seen_courses == ["CS201", None]


def test_ws_chat_course_field_safe_with_legacy_runner(client: TestClient) -> None:
    """A frame carrying `course` must not break a runner that only knows `message`."""
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "shortest paths?", "course": "CS201"})
        frames = _turn(ws)
    assert frames[-1]["type"] == "done"


def test_ws_chat_model_field_safe_with_legacy_runner(client: TestClient) -> None:
    """A frame carrying ``model`` must not break single-argument runners."""
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "shortest paths?", "model": "claude-haiku"})
        frames = _turn(ws)
    assert frames[-1]["type"] == "done"


def test_ws_chat_surfaces_agent_errors(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=failing_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "hi"})
        frame = _turn(ws)[-1]
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

    assert [frame["type"] for frame in socket.sent] == ["thread", "delta"]
    assert socket.sent[-1]["type"] == "delta", "nothing may be sent after the socket dropped"
    assert not any(frame["type"] == "error" for frame in socket.sent), (
        "an error frame must never be sent to a socket already known to be closed"
    )


# --- the protocol's new carrying capacity ------------------------------------


async def tool_runner(message: str) -> AsyncIterator[object]:
    from backend.agent.adapters import ToolFinished, ToolStarted, ToolSummary

    yield ToolStarted(call_id="t1", name="search_vault", args={"query": "shortest paths"})
    yield ToolFinished(
        call_id="t1",
        name="search_vault",
        summary=ToolSummary(
            label="search_vault",
            detail="shortest paths",
            paths=("50-Reference/algorithms.md", "99-Private/diary.md"),
        ),
    )
    yield "found it"


def test_a_tool_event_becomes_start_and_end_frames(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=tool_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "shortest paths?"})
        frames = _turn(ws)

    tools = [f for f in frames if f["type"] == "tool"]
    assert [f["phase"] for f in tools] == ["start", "end"]
    assert {f["call_id"] for f in tools} == {"t1"}, "a UI pairs the two by call_id"
    assert tools[0]["args"] == {"query": "shortest paths"}
    assert tools[1]["detail"] == "shortest paths"
    assert tools[1]["ok"] is True


def test_a_tool_frame_never_carries_a_private_path(tmp_path: Path) -> None:
    """I3. The tools already exclude the private zone, but a path list leaving
    the process is a new egress surface, so the frame re-checks."""
    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=tool_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "shortest paths?"})
        frames = _turn(ws)

    end = next(f for f in frames if f.get("phase") == "end")
    assert end["paths"] == ["50-Reference/algorithms.md"]
    assert not any("99-Private" in path for path in end["paths"])


def test_a_second_message_reaches_the_runner_with_the_first_turn_as_history(
    tmp_path: Path,
) -> None:
    """The headline fix: without this, "what about the second one?" is
    unanswerable by construction, because the model has never seen the
    transcript the user is looking at."""
    seen: list[list[tuple[str, str]]] = []

    async def history_runner(message: str, history=None, **_kw) -> AsyncIterator[str]:
        seen.append([(m.role, m.text) for m in (history or [])])
        yield "ok"

    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=history_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "first question"})
        thread_id = _turn(ws)[0]["thread_id"]
        ws.send_json({"message": "and the second?", "thread_id": thread_id})
        _turn(ws)

    assert seen[0] == [], "the opening turn has no history"
    assert seen[1] == [
        ("user", "first question"),
        ("assistant", "ok"),
    ], "the second turn must carry the first exchange, and must not repeat itself"


def test_a_runner_that_declares_thread_id_receives_it(tmp_path: Path) -> None:
    seen: list[object] = []

    async def thread_runner(message: str, thread_id=None, **_kw) -> AsyncIterator[str]:
        seen.append(thread_id)
        yield "ok"

    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=thread_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "hi"})
        announced = _turn(ws)[0]["thread_id"]

    assert seen == [announced]


def test_an_unknown_thread_id_is_an_error_frame_not_a_crash(client: TestClient) -> None:
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "hi", "thread_id": 999999})
        frames = _turn(ws)
        assert frames[-1]["type"] == "error"
        # the socket must stay usable afterwards
        ws.send_json({"message": "hi again"})
        assert _turn(ws)[-1]["type"] == "done"


def test_the_turn_is_persisted_and_reloadable(client: TestClient) -> None:
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "how do I find shortest paths?"})
        frames = _turn(ws)
    thread_id = frames[0]["thread_id"]

    body = client.get(f"/api/chat/threads/{thread_id}").json()
    roles = [m["role"] for m in body["messages"]]
    assert roles == ["user", "assistant"]
    assert body["messages"][0]["text"] == "how do I find shortest paths?"
    assert "Dijkstra finds" in body["messages"][1]["text"]
    assert body["thread"]["title"], "the first message must name the thread"


class _DroppingOnToolSocket(_DroppingSocket):
    """Same disconnect, but raced against a ``tool`` frame instead of a delta —
    proving the new send path sits inside the same handlers, not just the
    delta one."""

    async def send_json(self, data: dict) -> None:
        from starlette.websockets import WebSocketDisconnect, WebSocketState

        if self.application_state is not WebSocketState.CONNECTED:
            raise RuntimeError('Cannot call "send" once a close message has been sent.')
        self.sent.append(data)
        if data.get("type") == "tool":
            self.application_state = WebSocketState.DISCONNECTED
            self.client_state = WebSocketState.DISCONNECTED
            raise WebSocketDisconnect(code=1006)


@pytest.mark.anyio
async def test_a_disconnect_on_a_tool_frame_sends_nothing_further(tmp_path: Path) -> None:
    from backend.features.chat.router import build_chat_router

    vault = tmp_path / "vault"
    vault.mkdir()
    router = build_chat_router(Settings(_vault_path=vault), tool_runner)
    socket = _DroppingOnToolSocket()

    await router.routes[0].endpoint(socket)  # must return cleanly, not raise

    assert [frame["type"] for frame in socket.sent] == ["thread", "tool"]
    assert not any(frame["type"] == "error" for frame in socket.sent)


@pytest.mark.anyio
async def test_a_partial_answer_survives_a_mid_stream_disconnect(tmp_path: Path) -> None:
    """The tokens were already spent and the turn really happened; a thread
    that loses its half-written answer because a tab closed is worse than one
    that keeps it. Mirrors how stream_chat already banks partial_usage()."""
    from backend.core.db import connect, init_schema
    from backend.features.chat import store
    from backend.features.chat.router import build_chat_router

    vault = tmp_path / "vault"
    vault.mkdir()
    settings = Settings(_vault_path=vault)
    router = build_chat_router(settings, fake_runner)
    socket = _DroppingSocket()

    await router.routes[0].endpoint(socket)

    conn = connect(settings.db_path)
    init_schema(conn)
    try:
        thread_id = socket.sent[0]["thread_id"]
        messages = store.list_messages(conn, thread_id)
    finally:
        conn.close()

    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["text"] == "Dijkstra finds ", "the delta that got through must persist"


async def turn_limit_runner(message: str) -> AsyncIterator[str | Notice]:
    yield "as far as I got"
    yield Notice(kind="turn_limit", detail="reached the 12-step limit for this turn")


def test_a_turn_limit_reaches_the_browser_without_becoming_a_tool_step(tmp_path: Path) -> None:
    """Running out of tool turns used to be invisible everywhere.

    The reply simply stopped, with no frame, no log and nothing in the
    transcript to distinguish a truncated answer from a complete one. It rides
    its own frame type rather than a tool step: it describes the run, not
    anything the vault did, and `tools_json` is read as a list of tool steps by
    both the client and the thread REST route.
    """
    vault = tmp_path / "vault"
    vault.mkdir()
    client = TestClient(create_app(Settings(_vault_path=vault), chat_runner=turn_limit_runner))

    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "what did I write about graphs"})
        frames = _turn(ws)

    notice = next(f for f in frames if f["type"] == "notice")
    assert notice["kind"] == "turn_limit"
    assert "12-step limit" in notice["detail"]

    thread_id = next(f for f in frames if f["type"] == "thread")["thread_id"]
    stored = client.get(f"/api/chat/threads/{thread_id}").json()["messages"]
    answer = stored[-1]
    assert answer["text"] == "as far as I got", "the partial answer is still banked"
    assert answer["tools"] == [], "a notice is not a tool step"


def test_ws_chat_passes_selected_sources_to_a_sources_aware_runner(tmp_path: Path) -> None:
    """The Course Hub SOURCES rail's ticks reach the runner, which is what
    forces `search_vault`'s per-file filter in the real agent."""
    seen: list[list[str] | None] = []

    async def sources_runner(
        message: str, model: str | None = None, sources: list[str] | None = None
    ) -> AsyncIterator[str]:
        seen.append(sources)
        yield "ok"

    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=sources_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "q", "sources": ["a/b.md", "a/c.pdf"]})
        _turn(ws)
        # Deselecting everything is not the same as sending nothing: [] must
        # reach the runner so retrieval can honour it, rather than being
        # dropped as falsy and silently searching the whole vault.
        ws.send_json({"message": "q", "sources": []})
        _turn(ws)
        ws.send_json({"message": "q"})
        _turn(ws)

    assert seen == [["a/b.md", "a/c.pdf"], [], None]


def test_ws_chat_cleans_up_a_sources_frame(tmp_path: Path) -> None:
    """Blanks, duplicates and non-strings come off the wire; the cap keeps one
    frame from pushing an unbounded `$in` clause into chroma."""
    seen: list[list[str] | None] = []

    async def sources_runner(message: str, sources: list[str] | None = None) -> AsyncIterator[str]:
        seen.append(sources)
        yield "ok"

    vault = tmp_path / "vault"
    vault.mkdir()
    app = create_app(Settings(_vault_path=vault), chat_runner=sources_runner)
    with TestClient(app).websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "q", "sources": ["a.md", "  ", "a.md", "b.md"]})
        _turn(ws)
        ws.send_json({"message": "q", "sources": [f"f{i}.md" for i in range(300)]})
        _turn(ws)
        # Not a list — an older or confused client must degrade to "no
        # restriction", not to an error frame.
        ws.send_json({"message": "q", "sources": "a.md"})
        _turn(ws)

    assert seen[0] == ["a.md", "b.md"]
    assert len(seen[1]) == 200
    assert seen[2] is None


def test_ws_chat_sources_field_safe_with_legacy_runner(client: TestClient) -> None:
    with client.websocket_connect("/ws/chat") as ws:
        ws.send_json({"message": "shortest paths?", "sources": ["a.md"]})
        frames = _turn(ws)
    assert frames[-1]["type"] == "done"


def test_the_agent_is_built_once_for_the_process_not_once_per_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every reconnect used to build a whole new ChatAgent.

    The old line was ``runner = chat_runner or default_chat_runner(settings)``
    *inside* the socket handler, and production passes ``chat_runner=None`` --
    so a reload, a STOP-then-ask, a sleep/wake or a second window each built a
    new ``ChatAgent``, and with it a new ``VaultIndex``, a new chroma client and
    a new SentenceTransformer on a new warm thread (~20s, see ``ChatAgent.warm``),
    while the previous one stayed resident and its caches restarted cold.
    """
    from backend.features.chat import router as chat_router

    builds = 0

    def counting_default_runner(settings, index_factory=None):
        nonlocal builds
        builds += 1
        return fake_runner

    monkeypatch.setattr(chat_router, "default_chat_runner", counting_default_runner)

    vault = tmp_path / "vault"
    vault.mkdir()
    # chat_runner=None is the production wiring -- that is the whole point.
    client = TestClient(create_app(Settings(_vault_path=vault), chat_runner=None))

    for _ in range(3):
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_json({"message": "hello"})
            _turn(ws)

    assert builds == 1, f"built the agent {builds} times across three connections"
