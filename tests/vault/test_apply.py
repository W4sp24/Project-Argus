"""Tests for suggestion application through the writer (P3 exit criteria)."""

import sqlite3
import subprocess
from pathlib import Path

import pytest

from backend.core.db import connect, init_schema
from backend.vault.suggestions import dismiss, dismissal_feedback, get, insert_suggestion, pending
from backend.vault.writer import WriterError, apply_suggestion


def _git_log(vault: Path) -> str:
    return subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True, check=False
    ).stdout


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "20-Projects").mkdir(parents=True)
    (root / "20-Projects" / "thesis.md").write_text(
        "# Thesis\n\n- [ ] Draft outline 📅 2026-07-20\n\nNotes body.\n", encoding="utf-8"
    )
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, check=True)
    return root


@pytest.fixture()
def conn(tmp_path: Path) -> sqlite3.Connection:
    connection = connect(tmp_path / "friday.db")
    init_schema(connection)
    yield connection
    connection.close()


def test_apply_schedule_hits_gcal_and_snapshots_vault(
    conn: sqlite3.Connection, vault: Path
) -> None:
    sid = insert_suggestion(
        conn,
        "schedule",
        {
            "blocks": [
                {"title": "Deep work", "start": "2026-07-13T09:00:00", "end": "2026-07-13T11:00:00"}
            ]
        },
        "Morning focus block",
    )
    inserted: list[tuple] = []
    before = _git_log(vault).count("\n")

    applied = apply_suggestion(
        conn, vault, sid, gcal_insert=lambda t, s, e: inserted.append((t, s, e))
    )

    assert inserted == [("Deep work", "2026-07-13T09:00:00", "2026-07-13T11:00:00")]
    assert _git_log(vault).count("\n") == before + 1, "I2: vault git log must grow by 1"
    assert applied.status == "applied"
    daily = next((vault / "10-Daily").glob("*.md"))
    assert "## Argus log" in daily.read_text(encoding="utf-8")


def test_apply_task_edit_verifies_old_line(conn: sqlite3.Connection, vault: Path) -> None:
    sid = insert_suggestion(
        conn,
        "task",
        {
            "path": "20-Projects/thesis.md",
            "line": 3,
            "old_line": "- [ ] Draft outline 📅 2026-07-20",
            "new_line": "- [ ] Draft outline 📅 2026-07-15 ⏫",
        },
        "Pull the deadline forward",
    )
    apply_suggestion(conn, vault, sid, gcal_insert=lambda *a: None)
    text = (vault / "20-Projects" / "thesis.md").read_text(encoding="utf-8")
    assert "2026-07-15 ⏫" in text


def test_apply_note_diff_fails_clean_on_drift(conn: sqlite3.Connection, vault: Path) -> None:
    diff = (
        "--- a/20-Projects/thesis.md\n+++ b/20-Projects/thesis.md\n"
        "@@ -5,1 +5,1 @@\n-THIS LINE DRIFTED\n+Replacement.\n"
    )
    sid = insert_suggestion(conn, "note", {"path": "20-Projects/thesis.md", "diff": diff}, "edit")
    original = (vault / "20-Projects" / "thesis.md").read_text(encoding="utf-8")

    with pytest.raises(WriterError, match="drifted"):
        apply_suggestion(conn, vault, sid)

    assert (vault / "20-Projects" / "thesis.md").read_text(encoding="utf-8") == original
    assert get(conn, sid).status == "pending", "failed apply must leave the row pending"


def test_apply_note_diff_succeeds_with_matching_context(
    conn: sqlite3.Connection, vault: Path
) -> None:
    diff = "@@ -5,1 +5,2 @@\n-Notes body.\n+Notes body.\n+Added by FRIDAY after approval.\n"
    sid = insert_suggestion(conn, "note", {"path": "20-Projects/thesis.md", "diff": diff}, "append")

    apply_suggestion(conn, vault, sid)

    text = (vault / "20-Projects" / "thesis.md").read_text(encoding="utf-8")
    assert "Added by FRIDAY after approval." in text


def test_dismiss_stores_retrievable_reason(conn: sqlite3.Connection) -> None:
    sid = insert_suggestion(conn, "task", {"x": 1}, "Do a thing")
    dismiss(conn, sid, "I already did this by hand")

    row = get(conn, sid)
    assert row.status == "dismissed"
    assert row.dismiss_reason == "I already did this by hand"
    assert any("already did this" in line for line in dismissal_feedback(conn))
    assert all(item.id != sid for item in pending(conn))


def test_double_apply_rejected(conn: sqlite3.Connection, vault: Path) -> None:
    sid = insert_suggestion(
        conn, "schedule", {"blocks": [{"title": "X", "start": "s", "end": "e"}]}, "r"
    )
    apply_suggestion(conn, vault, sid, gcal_insert=lambda *a: None)
    with pytest.raises(WriterError, match="already applied"):
        apply_suggestion(conn, vault, sid, gcal_insert=lambda *a: None)


# --- Where an approved schedule block goes when the caller names no target ---


def _schedule_suggestion(conn: sqlite3.Connection) -> int:
    return insert_suggestion(
        conn,
        "schedule",
        {
            "blocks": [
                {"title": "Deep work", "start": "2026-07-13T09:00:00", "end": "2026-07-13T11:00:00"}
            ]
        },
        "Morning focus block",
    )


def test_a_schedule_block_lands_locally_when_google_is_not_connected(
    conn: sqlite3.Connection, vault: Path, monkeypatch
) -> None:
    """The default install must be able to approve its own planner's output.

    This raised `RuntimeError("Google Calendar is not connected")` before —
    the planner proposed blocks and the approve button could not place any of
    them without a Cloud Console project.
    """
    from backend.connectors import gcal

    monkeypatch.setattr(gcal, "configured", lambda: False)
    sid = _schedule_suggestion(conn)

    applied = apply_suggestion(conn, vault, sid)

    assert applied.status == "applied"
    rows = conn.execute("SELECT title, start, end FROM calendar_events").fetchall()
    assert [tuple(row) for row in rows] == [
        ("Deep work", "2026-07-13T09:00:00", "2026-07-13T11:00:00")
    ]


def test_google_still_wins_when_it_is_connected(
    conn: sqlite3.Connection, vault: Path, monkeypatch
) -> None:
    """Nothing changes for anyone already using the connector.

    The local calendar is the floor underneath Google, not a replacement for
    it — a user who went through the OAuth flow chose where their blocks go.
    """
    from backend.connectors import gcal

    inserted: list[tuple] = []
    monkeypatch.setattr(gcal, "configured", lambda: True)
    monkeypatch.setattr(gcal, "insert_event", lambda t, s, e: inserted.append((t, s, e)))
    sid = _schedule_suggestion(conn)

    apply_suggestion(conn, vault, sid)

    assert inserted == [("Deep work", "2026-07-13T09:00:00", "2026-07-13T11:00:00")]
    assert conn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()["n"] == 0


def test_an_unreadable_keyring_degrades_to_a_local_write(
    conn: sqlite3.Connection, vault: Path, monkeypatch
) -> None:
    """An unreadable keyring must not turn into a failed approval.

    `configured()` reads the keyring, and an unreadable one *raises* rather
    than answering False — KeyringUnavailableError derives from RuntimeError,
    not from CredentialError, which is a distinction this repo has already
    been caught by once (listing n8n instances 500'd for exactly this).
    """
    from backend.connectors import gcal

    def _explode() -> bool:
        raise RuntimeError("the keyring is locked")

    monkeypatch.setattr(gcal, "configured", _explode)
    sid = _schedule_suggestion(conn)

    applied = apply_suggestion(conn, vault, sid)

    assert applied.status == "applied"
    assert conn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()["n"] == 1
