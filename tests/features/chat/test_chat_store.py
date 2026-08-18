"""Tests for the chat thread/message persistence layer."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.features.chat import store


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "chat.db")
    init_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


def _clock(dt: datetime):
    """A ``now`` callable that always returns ``dt`` — the fixed-clock pattern."""
    return lambda: dt


EARLY = datetime(2026, 1, 1, tzinfo=UTC)
LATE = datetime(2026, 1, 2, tzinfo=UTC)


def test_create_thread_stores_title_and_returns_canonical_dict(conn) -> None:
    thread = store.create_thread(conn, title="Dijkstra questions", now=_clock(EARLY))

    assert thread["title"] == "Dijkstra questions"
    assert thread["course"] is None
    assert thread["archived"] is False
    assert thread["message_count"] == 0
    assert thread["created_at"] == thread["updated_at"]
    assert "session_id" not in thread
    assert "session_provider" not in thread
    assert "session_model" not in thread


def test_derive_title_collapses_whitespace_and_truncates_on_word_boundary() -> None:
    assert store.derive_title("  What   is\n\nDijkstra's   algorithm?  ") == (
        "What is Dijkstra's algorithm?"
    )

    long_text = "explain " * 20  # well past the default 60-char limit
    title = store.derive_title(long_text)
    assert len(title) <= 61  # limit + ellipsis
    assert title.endswith("…")
    assert not title[:-1].endswith(" ")  # truncated cleanly, not mid-word


def test_derive_title_empty_input_gets_fallback() -> None:
    assert store.derive_title("") == store.DEFAULT_TITLE
    assert store.derive_title("   ") == store.DEFAULT_TITLE
    assert store.derive_title("### ***") == store.DEFAULT_TITLE


def test_append_message_bumps_thread_updated_at(conn) -> None:
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    assert thread["updated_at"] == store._isoformat(EARLY)

    store.append_message(conn, thread["id"], role="user", text="hi", now=_clock(LATE))

    updated = store.get_thread(conn, thread["id"])
    assert updated["updated_at"] == store._isoformat(LATE)


def test_list_threads_orders_newest_first_and_hides_archived_by_default(conn) -> None:
    older = store.create_thread(conn, title="older", now=_clock(EARLY))
    newer = store.create_thread(conn, title="newer", now=_clock(LATE))
    store.archive_thread(conn, older["id"], now=_clock(LATE))

    visible = store.list_threads(conn)
    assert [t["id"] for t in visible] == [newer["id"]]

    with_archived = store.list_threads(conn, include_archived=True)
    assert [t["id"] for t in with_archived] == [newer["id"], older["id"]]


def test_list_threads_filters_by_course(conn) -> None:
    global_thread = store.create_thread(conn, title="global", now=_clock(EARLY))
    scoped = store.create_thread(conn, title="scoped", course="CS101", now=_clock(LATE))

    only_scoped = store.list_threads(conn, course="CS101")
    assert [t["id"] for t in only_scoped] == [scoped["id"]]

    everything = store.list_threads(conn, course=None)
    ids = {t["id"] for t in everything}
    assert ids == {global_thread["id"], scoped["id"]}


def test_delete_thread_cascades_to_messages(conn) -> None:
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    store.append_message(conn, thread["id"], role="user", text="hi", now=_clock(EARLY))
    store.append_message(conn, thread["id"], role="assistant", text="hello", now=_clock(LATE))

    store.delete_thread(conn, thread["id"])

    rows = conn.execute("SELECT * FROM chat_messages WHERE thread_id = ?", (thread["id"],))
    assert rows.fetchall() == []


def test_resumable_session_none_when_provider_differs(conn) -> None:
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    store.set_session(conn, thread["id"], session_id="sess-1", provider="claude", model="sonnet")

    assert store.resumable_session(conn, thread["id"], provider="ollama", model="sonnet") is None


def test_resumable_session_none_when_model_differs(conn) -> None:
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    store.set_session(conn, thread["id"], session_id="sess-1", provider="claude", model="sonnet")

    assert store.resumable_session(conn, thread["id"], provider="claude", model="opus") is None


def test_resumable_session_returns_token_when_both_match(conn) -> None:
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    store.set_session(conn, thread["id"], session_id="sess-1", provider="claude", model="sonnet")

    assert (
        store.resumable_session(conn, thread["id"], provider="claude", model="sonnet")
        == "sess-1"
    )


def test_tools_json_round_trips_through_append_and_list(conn) -> None:
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    trace = [{"name": "search_vault", "input": {"query": "dijkstra"}, "output": "3 hits"}]

    store.append_message(
        conn, thread["id"], role="assistant", text="found it", tools=trace, now=_clock(EARLY)
    )

    [message] = store.list_messages(conn, thread["id"])
    assert message["tools"] == trace


def test_corrupt_tools_json_yields_empty_list_not_raise(conn) -> None:
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    store.append_message(conn, thread["id"], role="user", text="hi", now=_clock(EARLY))
    conn.execute(
        "UPDATE chat_messages SET tools_json = ? WHERE thread_id = ?",
        ("{not valid json", thread["id"]),
    )
    conn.commit()

    [message] = store.list_messages(conn, thread["id"])
    assert message["tools"] == []


def test_null_tools_json_yields_empty_list(conn) -> None:
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    store.append_message(conn, thread["id"], role="user", text="hi", now=_clock(EARLY))

    [message] = store.list_messages(conn, thread["id"])
    assert message["tools"] == []


def test_rename_and_archive_thread_return_none_for_unknown_id(conn) -> None:
    assert store.rename_thread(conn, 999, "new title") is None
    assert store.archive_thread(conn, 999) is None


def test_message_count_is_correct_in_list_threads(conn) -> None:
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    store.append_message(conn, thread["id"], role="user", text="hi", now=_clock(EARLY))
    store.append_message(conn, thread["id"], role="assistant", text="hello", now=_clock(LATE))

    [listed] = store.list_threads(conn)
    assert listed["message_count"] == 2


def test_tools_json_is_none_on_user_rows_by_default(conn) -> None:
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    store.append_message(conn, thread["id"], role="user", text="hi", now=_clock(EARLY))

    row = conn.execute(
        "SELECT tools_json FROM chat_messages WHERE thread_id = ?", (thread["id"],)
    ).fetchone()
    assert row["tools_json"] is None


def test_derive_title_double_check_stored_json(conn) -> None:
    # sanity: json.dumps/loads round trip via the store, not hand-rolled here
    thread = store.create_thread(conn, title="t", now=_clock(EARLY))
    trace = [{"name": "x"}]
    store.append_message(
        conn, thread["id"], role="assistant", text="a", tools=trace, now=_clock(EARLY)
    )
    row = conn.execute(
        "SELECT tools_json FROM chat_messages WHERE thread_id = ?", (thread["id"],)
    ).fetchone()
    assert json.loads(row["tools_json"]) == trace
