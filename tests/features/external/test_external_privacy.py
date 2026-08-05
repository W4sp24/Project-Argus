"""The I3 boundary test — the most important test in the automations feature.

This surface is reachable from the public internet. A note in ``99-Private/``
and a note tagged ``#no-ai`` must never appear in **any** ``/api/external/*``
response, and that is asserted **per read endpoint** rather than once, because
each endpoint reaches the vault by a different route and a filter added to one
says nothing about the others.

The ``#no-ai``-note-outside-``99-Private/`` case is the one that matters most:
the task cache filters by directory only, so that note's tasks *are* in the
cache. Only the boundary filter keeps them off the wire.

Assertions run against the raw serialised response body, not a parsed field, so
content cannot escape through a nested key nobody thought to check.
"""

from __future__ import annotations

import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.features.external import auth
from backend.features.external.app import create_external_app

TOKEN = "test-token-value"

# Distinctive enough that a substring check cannot pass by accident.
PRIVATE_MARKER = "ZZPRIVATEMARKERZZ"
NOAI_MARKER = "ZZNOAIMARKERZZ"
PUBLIC_MARKER = "ZZPUBLICMARKERZZ"


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """A vault whose private content genuinely *would* surface if unfiltered.

    Every hidden task is due **today** and carries an exam keyword, so it lands
    in the buckets the agenda and briefing actually read and in the briefing's
    exam countdowns. An earlier version of this fixture used undated tasks,
    which fell into the "someday" bucket — the agenda and briefing assertions
    passed whether or not any filtering happened at all. A test that cannot
    fail is worse than no test, so the dates and keywords here are load-bearing.
    """
    root = tmp_path / "vault"
    for folder in (
        "10-Daily",
        "20-Projects",
        "99-Private",
        "90-Meta",
        "15-Courses/CS101/study",
    ):
        (root / folder).mkdir(parents=True)

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # Private zone, due today, exam keyword. Caught by the directory rule.
    (root / "99-Private" / "secret.md").write_text(
        f"# Secret\n\n- [ ] {PRIVATE_MARKER} exam prep \U0001f4c5 {today}\n",
        encoding="utf-8",
    )

    # A #no-ai note OUTSIDE 99-Private/, due today, exam keyword. The directory
    # rule does NOT catch this one — its task is in the cache right now, and
    # only the boundary filter keeps it off the wire.
    (root / "20-Projects" / "sensitive.md").write_text(
        f"---\ntags: [no-ai]\n---\n\n"
        f"- [ ] {NOAI_MARKER} therapy exam \U0001f4c5 {today}\n",
        encoding="utf-8",
    )

    # Yesterday's daily note, tagged #no-ai — feeds briefing.yesterday_unfinished.
    (root / "10-Daily" / f"{yesterday}.md").write_text(
        f"---\ntags: [no-ai]\n---\n\n- [ ] {NOAI_MARKER} unfinished from yesterday\n",
        encoding="utf-8",
    )

    # A course review queue tagged #no-ai — feeds briefing.weak_topics.
    (root / "15-Courses" / "CS101" / "study" / "review-queue.md").write_text(
        f"---\ntags: [no-ai]\n---\n\n- [ ] {NOAI_MARKER} weak topic\n",
        encoding="utf-8",
    )

    # Ordinary content that must still come through, so the assertions cannot
    # pass by filtering everything away.
    (root / "20-Projects" / "public.md").write_text(
        f"# Public\n\n- [ ] {PUBLIC_MARKER} write the docs \U0001f4c5 {today}\n",
        encoding="utf-8",
    )

    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True
    )
    return root


@pytest.fixture()
def client(vault: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(auth, "verify", lambda presented: presented == TOKEN)
    settings = Settings(_vault_path=vault)
    return TestClient(create_external_app(settings))


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


READ_ENDPOINTS = [
    "/api/external/agenda",
    "/api/external/tasks",
    "/api/external/briefing",
]


@pytest.mark.parametrize("endpoint", READ_ENDPOINTS)
def test_private_directory_never_appears(client: TestClient, endpoint: str) -> None:
    response = client.get(endpoint, headers=_auth())
    assert response.status_code == 200, response.text
    assert PRIVATE_MARKER not in response.text


@pytest.mark.parametrize("endpoint", READ_ENDPOINTS)
def test_no_ai_note_outside_private_never_appears(
    client: TestClient, endpoint: str
) -> None:
    """The leak the directory-only filters would allow through."""
    response = client.get(endpoint, headers=_auth())
    assert response.status_code == 200, response.text
    assert NOAI_MARKER not in response.text


def test_ordinary_content_does_come_through(client: TestClient) -> None:
    """Proves the filters above are not passing by returning nothing at all."""
    response = client.get("/api/external/tasks", headers=_auth())
    assert response.status_code == 200, response.text
    assert PUBLIC_MARKER in response.text


def test_no_read_endpoint_is_reachable_without_a_token(client: TestClient) -> None:
    for endpoint in READ_ENDPOINTS:
        assert client.get(endpoint).status_code == 401


def test_unreadable_note_fails_closed(vault: Path, monkeypatch) -> None:
    """A note we cannot parse is withheld rather than served.

    On a public surface the safe default for "I cannot tell what this says" is
    to not send it.
    """
    from backend.features.external.filters import VisibilityCache

    cache = VisibilityCache(vault)
    assert cache.is_visible("20-Projects/does-not-exist.md") is False


def test_task_without_a_path_is_not_withheld(vault: Path) -> None:
    """A connector-supplied task has no vault note, so nothing to check."""
    from backend.features.external.filters import VisibilityCache

    cache = VisibilityCache(vault)
    assert cache.is_visible(None) is True
