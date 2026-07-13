"""Tests for the recent-activity feed: merged notes + approvals + exams."""

from pathlib import Path

import pytest

from backend.activity import recent_activity
from backend.config import Settings
from backend.db import connect, init_schema


@pytest.fixture()
def env(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "20-Projects").mkdir(parents=True)
    (vault / "20-Projects" / "thesis.md").write_text("# Thesis\n", encoding="utf-8")
    settings = Settings(_vault_path=vault)
    conn = connect(settings.db_path)
    init_schema(conn)
    yield settings, conn
    conn.close()


def test_activity_merges_notes_approvals_exams_newest_first(env):
    settings, conn = env
    conn.execute(
        "INSERT INTO suggestions (kind, payload_json, rationale, status, applied_at)"
        " VALUES ('task', '{}', 'move it', 'applied', '2026-07-12 10:00:00')"
    )
    conn.execute(
        "INSERT INTO exams (course, title, questions_json) VALUES ('ES101', 'Plates', '[]')"
    )
    conn.execute(
        "INSERT INTO attempts (exam_id, score, total, answers_json, created_at)"
        " VALUES (1, 3, 10, '[]', '2026-07-12 11:00:00')"
    )
    conn.commit()

    events = recent_activity(settings, conn, limit=10)
    kinds = {event.kind for event in events}
    assert {"note", "approval", "exam"} <= kinds
    whens = [event.when for event in events]
    assert whens == sorted(whens, reverse=True)


def test_activity_respects_limit(env):
    settings, conn = env
    assert len(recent_activity(settings, conn, limit=1)) == 1
