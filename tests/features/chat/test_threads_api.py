"""Tests for the REST /api/chat/threads surface (the websocket is untouched)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.chat import store
from backend.main import create_app


async def fake_runner(message: str) -> AsyncIterator[str]:
    yield "unused"


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    path = tmp_path / "vault"
    path.mkdir()
    return path


@pytest.fixture()
def client(vault: Path) -> TestClient:
    return TestClient(create_app(Settings(_vault_path=vault), chat_runner=fake_runner))


def test_post_creates_thread_and_get_lists_it(client: TestClient) -> None:
    resp = client.post("/api/chat/threads", json={"title": "CS201 midterm", "course": "CS201"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "CS201 midterm"
    assert body["course"] == "CS201"
    assert body["archived"] is False
    assert body["message_count"] == 0

    listed = client.get("/api/chat/threads").json()
    assert [t["id"] for t in listed] == [body["id"]]


def test_post_with_no_title_gets_placeholder(client: TestClient) -> None:
    resp = client.post("/api/chat/threads", json={})
    assert resp.status_code == 201
    assert resp.json()["title"] == store.DEFAULT_TITLE


def test_post_with_blank_title_gets_placeholder(client: TestClient) -> None:
    resp = client.post("/api/chat/threads", json={"title": "   "})
    assert resp.status_code == 201
    assert resp.json()["title"] == store.DEFAULT_TITLE


def test_get_thread_returns_messages_in_insertion_order(client: TestClient, vault: Path) -> None:
    created = client.post("/api/chat/threads", json={"title": "t"}).json()
    thread_id = created["id"]

    conn = connect(Settings(_vault_path=vault).db_path)
    init_schema(conn)
    try:
        store.append_message(conn, thread_id, role="user", text="hello")
        store.append_message(conn, thread_id, role="assistant", text="hi there")
    finally:
        conn.close()

    resp = client.get(f"/api/chat/threads/{thread_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["thread"]["id"] == thread_id
    assert [m["text"] for m in body["messages"]] == ["hello", "hi there"]
    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["tools"] == []


def test_patch_renames(client: TestClient) -> None:
    created = client.post("/api/chat/threads", json={"title": "old"}).json()
    resp = client.patch(f"/api/chat/threads/{created['id']}", json={"title": "new"})
    assert resp.status_code == 200
    assert resp.json()["title"] == "new"


def test_patch_archives(client: TestClient) -> None:
    created = client.post("/api/chat/threads", json={"title": "t"}).json()
    resp = client.patch(f"/api/chat/threads/{created['id']}", json={"archived": True})
    assert resp.status_code == 200
    assert resp.json()["archived"] is True


def test_patch_with_both_fields_applies_both(client: TestClient) -> None:
    created = client.post("/api/chat/threads", json={"title": "t"}).json()
    resp = client.patch(
        f"/api/chat/threads/{created['id']}", json={"title": "renamed", "archived": True}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "renamed"
    assert body["archived"] is True


def test_patch_with_neither_field_is_400(client: TestClient) -> None:
    created = client.post("/api/chat/threads", json={"title": "t"}).json()
    resp = client.patch(f"/api/chat/threads/{created['id']}", json={})
    assert resp.status_code == 400


def test_patch_with_blank_title_is_400(client: TestClient) -> None:
    created = client.post("/api/chat/threads", json={"title": "t"}).json()
    resp = client.patch(f"/api/chat/threads/{created['id']}", json={"title": "   "})
    assert resp.status_code == 400


def test_archived_threads_hidden_from_default_list_shown_with_query(client: TestClient) -> None:
    created = client.post("/api/chat/threads", json={"title": "t"}).json()
    client.patch(f"/api/chat/threads/{created['id']}", json={"archived": True})

    default_list = client.get("/api/chat/threads").json()
    assert created["id"] not in [t["id"] for t in default_list]

    with_archived = client.get("/api/chat/threads", params={"archived": "true"}).json()
    assert created["id"] in [t["id"] for t in with_archived]


def test_course_filters_and_unfiltered_list_includes_both(client: TestClient) -> None:
    global_thread = client.post("/api/chat/threads", json={"title": "global"}).json()
    scoped_thread = client.post(
        "/api/chat/threads", json={"title": "scoped", "course": "CS201"}
    ).json()

    scoped_list = client.get("/api/chat/threads", params={"course": "CS201"}).json()
    assert [t["id"] for t in scoped_list] == [scoped_thread["id"]]

    unfiltered = client.get("/api/chat/threads").json()
    ids = {t["id"] for t in unfiltered}
    assert {global_thread["id"], scoped_thread["id"]} <= ids


def test_delete_removes_thread_second_delete_is_404(client: TestClient) -> None:
    created = client.post("/api/chat/threads", json={"title": "t"}).json()
    resp = client.delete(f"/api/chat/threads/{created['id']}")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}

    resp2 = client.delete(f"/api/chat/threads/{created['id']}")
    assert resp2.status_code == 404


def test_unknown_thread_id_is_404_on_get_patch_delete(client: TestClient) -> None:
    assert client.get("/api/chat/threads/999999").status_code == 404
    assert client.patch("/api/chat/threads/999999", json={"title": "x"}).status_code == 404
    assert client.delete("/api/chat/threads/999999").status_code == 404


def test_session_fields_never_leak_to_the_response(client: TestClient, vault: Path) -> None:
    created = client.post("/api/chat/threads", json={"title": "t"}).json()

    conn = connect(Settings(_vault_path=vault).db_path)
    init_schema(conn)
    try:
        store.set_session(
            conn, created["id"], session_id="abc123", provider="claude-cli", model="sonnet"
        )
    finally:
        conn.close()

    forbidden = {"session_id", "session_provider", "session_model"}

    get_body = client.get(f"/api/chat/threads/{created['id']}").json()
    assert forbidden.isdisjoint(get_body["thread"].keys())

    list_body = client.get("/api/chat/threads").json()
    assert all(forbidden.isdisjoint(t.keys()) for t in list_body)

    patch_body = client.patch(
        f"/api/chat/threads/{created['id']}", json={"title": "renamed"}
    ).json()
    assert forbidden.isdisjoint(patch_body.keys())
