"""Tests for flashcard deck generation, due-queue ordering, and FSRS grading."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.db import connect, init_schema
from backend.flashcards import due_cards, generate_deck, grade_card, parse_qa_pairs
from backend.main import create_app

FLASHCARDS_MD = """\
Q:: What is Big-O of binary search?
A:: O(log n)

Q:: CAP theorem — what do the three letters stand for?
A:: Consistency, Availability, Partition tolerance
   — a distributed system can only guarantee two.

Q:: SOLID: what does the 'S' stand for?
A:: Single Responsibility Principle
"""


def _vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    (vault / "15-Courses" / "CS201").mkdir(parents=True)
    (vault / "15-Courses" / "CS201" / "flashcards.md").write_text(FLASHCARDS_MD, encoding="utf-8")
    return vault


# --- parsing -----------------------------------------------------------


def test_parse_qa_pairs_handles_multiline_and_dashes() -> None:
    pairs = parse_qa_pairs(FLASHCARDS_MD)
    assert len(pairs) == 3
    assert pairs[0] == ("What is Big-O of binary search?", "O(log n)")
    assert pairs[1][0].startswith("CAP theorem")
    assert "Partition tolerance" in pairs[1][1]
    assert "distributed system" in pairs[1][1]


# --- direct module tests (FSRS state, with fast-forwarded clocks) ------


@pytest.fixture()
def conn(tmp_path: Path):
    connection = connect(tmp_path / "argus.db")
    init_schema(connection)
    yield connection
    connection.close()


def test_generate_deck_from_markdown(tmp_path: Path, conn) -> None:
    vault = _vault(tmp_path)
    deck_id = generate_deck(vault, conn, "CS201")

    row = conn.execute("SELECT * FROM flashcard_decks WHERE id = ?", (deck_id,)).fetchone()
    assert row["course"] == "CS201"
    import json

    cards = json.loads(row["cards_json"])
    assert len(cards) == 3
    assert all(card["id"].startswith(f"{deck_id}:") for card in cards)


def test_due_cards_ordering_new_cards_and_soonest_due_first(tmp_path: Path, conn) -> None:
    vault = _vault(tmp_path)
    deck_id = generate_deck(vault, conn, "CS201")

    now = datetime.now(timezone.utc)
    all_due = due_cards(conn, deck_id, now=now)
    assert len(all_due) == 3, "ungraded cards must be due immediately"

    first_card_id = all_due[0].id
    grade_card(conn, deck_id, first_card_id, "easy", now=now)

    remaining_new = due_cards(conn, deck_id, now=now)
    assert len(remaining_new) == 2, "a graded-into-the-future card drops out of the due queue"
    assert first_card_id not in [c.id for c in remaining_new]


def test_grading_increases_due_at_on_successive_good_grades(tmp_path: Path, conn) -> None:
    vault = _vault(tmp_path)
    deck_id = generate_deck(vault, conn, "CS201")
    card_id = due_cards(conn, deck_id)[0].id

    now = datetime.now(timezone.utc)
    intervals = []
    for _ in range(3):
        result = grade_card(conn, deck_id, card_id, "good", now=now)
        due_at = datetime.fromisoformat(result.due_at)
        intervals.append((due_at - now).total_seconds())
        now = due_at  # fast-forward to the new due time, like a real review cadence

    assert intervals[0] < intervals[1] < intervals[2], (
        "successive 'good' grades must schedule further into the future each time"
    )


def test_grading_again_shrinks_interval_relative_to_prior_good_grades(tmp_path: Path, conn) -> None:
    vault = _vault(tmp_path)
    deck_id = generate_deck(vault, conn, "CS201")
    card_id = due_cards(conn, deck_id)[0].id

    now = datetime.now(timezone.utc)
    for _ in range(2):
        result = grade_card(conn, deck_id, card_id, "good", now=now)
        now = datetime.fromisoformat(result.due_at)

    good_interval = (now - datetime.now(timezone.utc)).total_seconds()

    again_result = grade_card(conn, deck_id, card_id, "again", now=now)
    again_interval = (datetime.fromisoformat(again_result.due_at) - now).total_seconds()

    assert again_interval < good_interval, "'again' must reset scheduling to a short interval"


def test_grade_card_nonexistent_card_raises(tmp_path: Path, conn) -> None:
    from backend.flashcards import FlashcardsError

    vault = _vault(tmp_path)
    deck_id = generate_deck(vault, conn, "CS201")
    with pytest.raises(FlashcardsError):
        grade_card(conn, deck_id, "nope", "good")


# --- API tests -----------------------------------------------------------


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    vault = _vault(tmp_path)
    app = create_app(Settings(_vault_path=vault))
    return TestClient(app)


def test_api_generate_list_due_and_grade_roundtrip(client: TestClient) -> None:
    created = client.post("/api/flashcards/decks", json={"course": "CS201"}).json()
    assert created["cards"] == 3

    listing = client.get("/api/flashcards/decks", params={"course": "CS201"}).json()
    assert listing[0]["id"] == created["id"]

    due = client.get(f"/api/flashcards/decks/{created['id']}/due").json()
    assert len(due) == 3

    card_id = due[0]["id"]
    graded = client.post(
        f"/api/flashcards/decks/{created['id']}/cards/{card_id}/grade", json={"grade": "good"}
    ).json()
    assert graded["card_id"] == card_id
    assert graded["due_at"] > datetime.now(timezone.utc).isoformat()

    due_after = client.get(f"/api/flashcards/decks/{created['id']}/due").json()
    assert card_id not in [c["id"] for c in due_after]


def test_api_generate_deck_missing_flashcards_md_is_422(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    (vault / "15-Courses" / "CS999").mkdir(parents=True)
    app = create_app(Settings(_vault_path=vault))
    client = TestClient(app)
    response = client.post("/api/flashcards/decks", json={"course": "CS999"})
    assert response.status_code == 422


def test_api_grade_nonexistent_deck_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/flashcards/decks/99999/cards/99999:0/grade", json={"grade": "good"}
    )
    assert response.status_code == 404


def test_api_grade_nonexistent_card_in_real_deck_is_404(client: TestClient) -> None:
    created = client.post("/api/flashcards/decks", json={"course": "CS201"}).json()
    response = client.post(
        f"/api/flashcards/decks/{created['id']}/cards/nope/grade", json={"grade": "good"}
    )
    assert response.status_code == 404
