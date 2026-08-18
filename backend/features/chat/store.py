"""The chat dock's persistence layer: threads and their messages.

Plain ``sqlite3`` CRUD over ``chat_threads``/``chat_messages`` (see
``backend.core.db.SCHEMA``), matching the style already used by
:mod:`backend.features.automations.store` and
:mod:`backend.features.quick_links.store`: module-level functions, no
classes, ``conn: sqlite3.Connection`` always first, every function that
reads the clock takes ``now`` as a keyword-only parameter defaulting to
:func:`_utcnow` so tests can inject a fixed clock (there is no
``freezegun``/``time-machine`` dependency in this repo).

Nothing outside this module reads or writes these tables yet — the router
and the agent-history bridge (a ``history_for`` helper that turns stored
rows back into ``claude_agent_sdk`` ``Message`` objects) land in later
commits. This module is purely additive.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _isoformat(dt: datetime) -> str:
    """ISO-8601 UTC string for a (possibly naive) datetime."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC).isoformat()


def _parse_iso(value: str) -> datetime:
    """Parse a stored ISO-8601 timestamp back into a timezone-aware UTC datetime."""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


# --- threads -----------------------------------------------------------


def _thread_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    # session_id/session_provider/session_model are deliberately absent —
    # they are internal resume plumbing (see backend.core.db.SCHEMA) and
    # must never reach a browser.
    return {
        "id": row["id"],
        "title": row["title"],
        "course": row["course"],
        "archived": bool(row["archived"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "message_count": row["message_count"],
    }


_THREAD_SELECT = (
    "SELECT t.*, "
    "(SELECT COUNT(*) FROM chat_messages m WHERE m.thread_id = t.id) AS message_count "
    "FROM chat_threads t"
)


def create_thread(
    conn: sqlite3.Connection,
    *,
    title: str,
    course: str | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Create a new thread and return its canonical dict.

    ``course`` is fixed at creation — see ``backend.core.db.SCHEMA`` for why
    a thread never changes scope after the fact.
    """
    ts = _isoformat(now())
    cursor = conn.execute(
        "INSERT INTO chat_threads (title, course, created_at, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (title, course, ts, ts),
    )
    conn.commit()
    thread = get_thread(conn, cursor.lastrowid)
    assert thread is not None  # just written above
    return thread


def get_thread(conn: sqlite3.Connection, thread_id: int) -> dict[str, Any] | None:
    """One thread by id, or ``None`` if it doesn't exist."""
    row = conn.execute(f"{_THREAD_SELECT} WHERE t.id = ?", (thread_id,)).fetchone()
    return None if row is None else _thread_row_to_dict(row)


def list_threads(
    conn: sqlite3.Connection,
    *,
    course: str | None = None,
    include_archived: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Threads newest-first (by ``updated_at``).

    ``course`` unset (the default, ``None``) means no filter at all — the
    sidebar wants both global and scoped threads together. Pass a course
    name to see only threads scoped to it; there is no way to ask for "only
    global" here because the sidebar never needs that view.
    """
    query = f"{_THREAD_SELECT} WHERE 1=1"
    params: list[Any] = []
    if course is not None:
        query += " AND t.course = ?"
        params.append(course)
    if not include_archived:
        query += " AND t.archived = 0"
    # id DESC breaks ties on updated_at -- two threads touched within the same
    # datetime('now') second (or, in tests, the same fixed clock) would
    # otherwise sort in whatever order sqlite happens to return them.
    query += " ORDER BY t.updated_at DESC, t.id DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_thread_row_to_dict(row) for row in rows]


def rename_thread(
    conn: sqlite3.Connection, thread_id: int, title: str, *, now: Callable[[], datetime] = _utcnow
) -> dict[str, Any] | None:
    """Rename a thread and bump ``updated_at``. ``None`` for an unknown id."""
    cursor = conn.execute(
        "UPDATE chat_threads SET title = ?, updated_at = ? WHERE id = ?",
        (title, _isoformat(now()), thread_id),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return None
    return get_thread(conn, thread_id)


def archive_thread(
    conn: sqlite3.Connection,
    thread_id: int,
    *,
    archived: bool = True,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any] | None:
    """Archive (or unarchive) a thread and bump ``updated_at``. ``None`` for an unknown id."""
    cursor = conn.execute(
        "UPDATE chat_threads SET archived = ?, updated_at = ? WHERE id = ?",
        (1 if archived else 0, _isoformat(now()), thread_id),
    )
    conn.commit()
    if cursor.rowcount == 0:
        return None
    return get_thread(conn, thread_id)


def delete_thread(conn: sqlite3.Connection, thread_id: int) -> None:
    """Delete a thread and (via ``ON DELETE CASCADE``) its messages. A no-op if unknown."""
    conn.execute("DELETE FROM chat_threads WHERE id = ?", (thread_id,))
    conn.commit()


def touch_thread(
    conn: sqlite3.Connection, thread_id: int, *, now: Callable[[], datetime] = _utcnow
) -> None:
    """Bump ``updated_at`` without changing anything else. A no-op if unknown."""
    conn.execute(
        "UPDATE chat_threads SET updated_at = ? WHERE id = ?", (_isoformat(now()), thread_id)
    )
    conn.commit()


def set_session(
    conn: sqlite3.Connection,
    thread_id: int,
    *,
    session_id: str | None,
    provider: str | None,
    model: str | None,
) -> None:
    """Record the resume token and the exact backend it belongs to.

    All three columns are set together, always — see ``backend.core.db.SCHEMA``
    for why a partial match (e.g. same provider, different model) must never
    be treated as resumable.
    """
    conn.execute(
        "UPDATE chat_threads SET session_id = ?, session_provider = ?, session_model = ? "
        "WHERE id = ?",
        (session_id, provider, model, thread_id),
    )
    conn.commit()


def resumable_session(
    conn: sqlite3.Connection, thread_id: int, *, provider: str, model: str
) -> str | None:
    """The stored session id, only when it was minted by this exact ``provider``/``model``.

    A session id from one backend means nothing to another (see
    ``backend.core.db.SCHEMA``), so a mismatch on either field returns
    ``None`` rather than a token the caller would resume into the wrong
    transcript.
    """
    row = conn.execute(
        "SELECT session_id, session_provider, session_model FROM chat_threads WHERE id = ?",
        (thread_id,),
    ).fetchone()
    if row is None or row["session_id"] is None:
        return None
    if row["session_provider"] != provider or row["session_model"] != model:
        return None
    return row["session_id"]


# --- messages ------------------------------------------------------------


def _message_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    tools: list[dict[str, Any]] = []
    raw = row["tools_json"]
    if raw:
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            tools = parsed
    return {
        "id": row["id"],
        "role": row["role"],
        "text": row["text"],
        "model": row["model"],
        "tools": tools,
        "created_at": row["created_at"],
    }


def append_message(
    conn: sqlite3.Connection,
    thread_id: int,
    *,
    role: str,
    text: str,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    """Append a message and touch its thread's ``updated_at``.

    Calls :func:`touch_thread` internally so the sidebar's newest-first
    ordering can never drift because some call site forgot to bump it —
    every write path for a message goes through here.
    """
    ts = _isoformat(now())
    tools_json = None if tools is None else json.dumps(tools, ensure_ascii=False)
    cursor = conn.execute(
        "INSERT INTO chat_messages (thread_id, role, text, model, tools_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (thread_id, role, text, model, tools_json, ts),
    )
    conn.commit()
    touch_thread(conn, thread_id, now=now)
    row = conn.execute(
        "SELECT * FROM chat_messages WHERE id = ?", (cursor.lastrowid,)
    ).fetchone()
    assert row is not None  # just written above
    return _message_row_to_dict(row)


def list_messages(
    conn: sqlite3.Connection, thread_id: int, *, limit: int | None = None
) -> list[dict[str, Any]]:
    """Messages for a thread, oldest-first."""
    query = "SELECT * FROM chat_messages WHERE thread_id = ? ORDER BY id"
    params: list[Any] = [thread_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    rows = conn.execute(query, params).fetchall()
    return [_message_row_to_dict(row) for row in rows]


# --- titles ----------------------------------------------------------------

#: Markdown noise stripped before a thread title is derived from the first
#: user message: headings, emphasis markers, and code fences/backticks. Not
#: a full markdown parser — just enough that "## What is **Dijkstra's**
#: algorithm?" reads as "What is Dijkstra's algorithm?" in the sidebar.
_MARKDOWN_NOISE_RE = re.compile(r"[#*_`~]+")
_WHITESPACE_RE = re.compile(r"\s+")

DEFAULT_TITLE = "new conversation"


def derive_title(text: str, *, limit: int = 60) -> str:
    """A sidebar-safe title derived from a message, never asked for explicitly.

    Collapses whitespace, strips markdown noise, and truncates on a word
    boundary at ``limit`` characters with an ellipsis. Empty (or
    whitespace/markdown-only) input falls back to :data:`DEFAULT_TITLE`
    rather than leaving a thread with a blank, unfindable title.
    """
    cleaned = _MARKDOWN_NOISE_RE.sub(" ", text)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    if not cleaned:
        return DEFAULT_TITLE
    if len(cleaned) <= limit:
        return cleaned
    truncated = cleaned[:limit]
    boundary = truncated.rfind(" ")
    if boundary > 0:
        truncated = truncated[:boundary]
    return truncated.rstrip() + "…"
