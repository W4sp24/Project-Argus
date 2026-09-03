"""Deck generation from the corpus, and its job body.

The job runs on a daemon thread in production, so the tests inject a
deterministic runner -- a test that spawned the real thread would be testing
``threading``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.flashcards import generate, jobs, store
from backend.features.flashcards.store import FlashcardsError
from backend.features.ingest import store as jobstore
from backend.main import create_app

REPLY = """\
Q:: what two properties make a problem suit dynamic programming
A:: overlapping subproblems and optimal substructure
Q:: what is memoisation
A:: caching subproblem results so each is computed once
"""


def _corpus(n: int = 2) -> list[dict[str, Any]]:
    return [
        {
            "text": f"chunk {i} about dynamic programming",
            "meta": {
                "path": f"15-Courses/CS201/materials/lecture-{i}.pdf",
                "page": i,
                "course": "CS201",
            },
        }
        for i in range(n)
    ]


def _generator(reply: str) -> Callable[[str], Any]:
    async def run(prompt: str) -> str:
        run.prompt = prompt  # type: ignore[attr-defined]
        return reply

    return run


# --- the generator ---------------------------------------------------------


def test_cards_come_back_parsed() -> None:
    cards = asyncio.run(generate.generate_cards(_generator(REPLY), _corpus(), "CS201"))
    assert [card["front"] for card in cards] == [
        "what two properties make a problem suit dynamic programming",
        "what is memoisation",
    ]


def test_the_prompt_carries_the_sources_and_the_math_contract() -> None:
    gen = _generator(REPLY)
    asyncio.run(generate.generate_cards(gen, _corpus(), "CS201"))
    prompt = gen.prompt  # type: ignore[attr-defined]
    assert "15-Courses/CS201/materials/lecture-0.pdf" in prompt
    # The markdown contract, not the exam's JSON one: cards are markdown, and
    # three of the JSON rules invert.
    assert "$" in prompt


def test_a_duplicate_question_is_dropped() -> None:
    # Models repeat themselves near the end of a long list, and a deck that
    # asks the same thing twice spends two reviews on one fact.
    reply = "Q:: what is P\nA:: polynomial time\nQ:: What is P\nA:: polynomial time again\n"
    cards = asyncio.run(generate.generate_cards(_generator(reply), _corpus(), "CS201"))
    assert len(cards) == 1


def test_the_requested_count_is_a_ceiling() -> None:
    cards = asyncio.run(generate.generate_cards(_generator(REPLY), _corpus(), "CS201", n=1))
    assert len(cards) == 1


def test_an_empty_corpus_is_refused_before_the_model_is_called() -> None:
    async def explode(prompt: str) -> str:
        raise AssertionError("the model must not be called with nothing to read")

    with pytest.raises(FlashcardsError, match="no indexed material"):
        asyncio.run(generate.generate_cards(explode, [], "CS201"))


def test_a_reply_with_nothing_card_shaped_is_an_error() -> None:
    with pytest.raises(FlashcardsError, match="nothing shaped like a flashcard"):
        asyncio.run(
            generate.generate_cards(_generator("Sorry, I cannot help."), _corpus(), "CS201")
        )


# --- the job body ----------------------------------------------------------


def _job(conn: Any, deck_id: int) -> str:
    return jobstore.create_job(
        conn,
        target="15-Courses/CS201/study",
        filenames=["CS201 flashcards"],
        kind="deck",
        params={"course": "CS201", "deck_id": deck_id},
    )


def test_the_job_records_the_deck_and_the_card_count(tmp_path: Path) -> None:
    settings = Settings(_vault_path=tmp_path / "vault")
    conn = connect(settings.db_path)
    init_schema(conn)
    deck_id = store.create_deck(conn, title="CS201", course="CS201", source="generated")
    job_id = _job(conn, deck_id)

    jobs.run_deck_job(
        job_id,
        settings=settings,
        generator=_generator(REPLY),
        corpus=_corpus(),
        course="CS201",
        deck_id=deck_id,
        n=20,
    )

    job = jobstore.get_job(conn, job_id)
    assert job["status"] == "ok"
    assert job["params"]["cards"] == 2
    assert job["params"]["deck_id"] == deck_id
    assert store.load_deck(conn, deck_id).cards == 2


def test_a_failing_job_records_the_reason_and_never_raises(tmp_path: Path) -> None:
    """There is no status code left to return once the 202 has gone out."""
    settings = Settings(_vault_path=tmp_path / "vault")
    conn = connect(settings.db_path)
    init_schema(conn)
    deck_id = store.create_deck(conn, title="CS201", course="CS201", source="generated")
    job_id = _job(conn, deck_id)

    jobs.run_deck_job(
        job_id,
        settings=settings,
        generator=_generator("nothing card shaped here"),
        corpus=_corpus(),
        course="CS201",
        deck_id=deck_id,
        n=20,
    )

    job = jobstore.get_job(conn, job_id)
    assert job["status"] == "failed"
    assert "flashcard" in job["error"]
    # The deck still exists, empty. The user asked for one; it is there, and
    # the job row says why it is empty.
    assert store.load_deck(conn, deck_id).cards == 0


def test_a_job_whose_row_vanished_is_a_no_op(tmp_path: Path) -> None:
    settings = Settings(_vault_path=tmp_path / "vault")
    conn = connect(settings.db_path)
    init_schema(conn)
    deck_id = store.create_deck(conn, title="CS201", course="CS201")

    jobs.run_deck_job(
        job_id="nope",
        settings=settings,
        generator=_generator(REPLY),
        corpus=_corpus(),
        course="CS201",
        deck_id=deck_id,
        n=20,
    )
    assert store.load_deck(conn, deck_id).cards == 0


# --- the route -------------------------------------------------------------


class FakeIndex:
    """Enough of a VaultIndex for `course_corpus` to find CS201 material."""

    def all_chunks(self) -> list[dict[str, Any]]:
        return _corpus()

    def chunk_counts(self) -> dict[str, int]:
        return {chunk["meta"]["path"]: 1 for chunk in _corpus()}


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    vault = tmp_path / "vault"
    (vault / "15-Courses" / "CS201" / "materials").mkdir(parents=True)
    app = create_app(
        Settings(_vault_path=vault),
        generator=_generator(REPLY),
        index_factory=FakeIndex,
        # Deterministic: run the job inline rather than on a daemon thread.
        ingest_job_runner=lambda run: run(),
    )
    return TestClient(app)


def test_generate_accepts_a_job_and_names_the_deck_it_will_fill(client: TestClient) -> None:
    response = client.post(
        "/api/flashcards/decks/generate", json={"course": "CS201", "sources": None}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["job_id"] and body["deck_id"]

    # The runner ran inline, so by now the deck is filled.
    deck = client.get(f"/api/flashcards/decks/{body['deck_id']}").json()
    assert deck["cards"] == 2
    assert deck["source"] == "generated"


def test_a_generated_deck_is_immediately_studiable(client: TestClient) -> None:
    deck_id = client.post("/api/flashcards/decks/generate", json={"course": "CS201"}).json()[
        "deck_id"
    ]
    due = client.get(f"/api/flashcards/decks/{deck_id}/due").json()
    assert len(due) == 2
    assert set(due[0]["preview"]) == {"again", "hard", "good", "easy"}
