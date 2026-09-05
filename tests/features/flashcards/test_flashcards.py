"""The flashcard HTTP surface: authoring, importing, exporting, studying.

``POST /decks`` changed meaning in the rows rewrite. It used to take
``{course}`` and generate by parsing that course's ``flashcards.md`` — a file
nothing in Argus ever wrote, which is why the feature was unreachable. It now
creates a deck; parsing ``flashcards.md`` is one case of
``POST /decks/{id}/import/note``, which reads any note.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
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


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(Settings(_vault_path=_vault(tmp_path))))


def _deck(client: TestClient, **body: object) -> dict:
    payload = {"title": "CS201 deck", "course": "CS201", **body}
    response = client.post("/api/flashcards/decks", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def _imported(client: TestClient) -> dict:
    """A deck filled from the course's flashcards.md — the old default path."""
    deck = _deck(client)
    added = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/note",
        json={"path": "15-Courses/CS201/flashcards.md"},
    )
    assert added.status_code == 200, added.text
    assert added.json()["added"] == 3
    return client.get(f"/api/flashcards/decks/{deck['id']}").json()


# --- decks -----------------------------------------------------------------


def test_a_deck_is_created_empty_and_needs_no_course(client: TestClient) -> None:
    created = _deck(client, title="Spanish verbs", course="")
    assert created["cards"] == 0
    assert created["course"] == ""
    assert created["source"] == "manual"


def test_a_blank_title_is_422(client: TestClient) -> None:
    assert client.post("/api/flashcards/decks", json={"title": "  "}).status_code == 422


def test_a_deck_can_be_renamed(client: TestClient) -> None:
    created = _deck(client)
    patched = client.patch(f"/api/flashcards/decks/{created['id']}", json={"title": "Renamed"})
    assert patched.status_code == 200
    assert patched.json()["title"] == "Renamed"


def test_patching_an_unknown_deck_is_404_but_a_blank_title_is_422(client: TestClient) -> None:
    assert client.patch("/api/flashcards/decks/99999", json={"title": "x"}).status_code == 404
    created = _deck(client)
    assert (
        client.patch(f"/api/flashcards/decks/{created['id']}", json={"title": " "}).status_code
        == 422
    )


def test_an_unknown_deck_reads_404(client: TestClient) -> None:
    assert client.get("/api/flashcards/decks/99999").status_code == 404
    assert client.delete("/api/flashcards/decks/99999").status_code == 404


# --- cards -----------------------------------------------------------------


def test_cards_are_added_read_back_and_edited(client: TestClient) -> None:
    created = _deck(client)
    added = client.post(
        f"/api/flashcards/decks/{created['id']}/cards",
        json={"cards": [{"front": "a", "back": "1"}, {"front": "b", "back": "2", "hint": "bee"}]},
    )
    assert added.json() == {"added": 2}

    detail = client.get(f"/api/flashcards/decks/{created['id']}").json()
    assert [card["front"] for card in detail["card_list"]] == ["a", "b"]
    assert detail["card_list"][1]["hint"] == "bee"

    ref = detail["card_list"][0]["ref"]
    patched = client.patch(
        f"/api/flashcards/decks/{created['id']}/cards/{ref}",
        json={"back": "one", "starred": True},
    )
    assert patched.status_code == 200
    assert (patched.json()["back"], patched.json()["starred"]) == ("one", True)


def test_adding_a_card_to_an_unknown_deck_is_404(client: TestClient) -> None:
    response = client.post(
        "/api/flashcards/decks/99999/cards", json={"cards": [{"front": "a", "back": "b"}]}
    )
    assert response.status_code == 404


def test_editing_an_unknown_card_is_404_and_a_blank_face_is_422(client: TestClient) -> None:
    deck = _imported(client)
    assert (
        client.patch(
            f"/api/flashcards/decks/{deck['id']}/cards/nope", json={"front": "x"}
        ).status_code
        == 404
    )
    ref = deck["card_list"][0]["ref"]
    assert (
        client.patch(
            f"/api/flashcards/decks/{deck['id']}/cards/{ref}", json={"front": "  "}
        ).status_code
        == 422
    )


def test_a_card_is_deleted_with_its_reviews(client: TestClient) -> None:
    deck = _imported(client)
    ref = deck["card_list"][0]["ref"]
    client.post(f"/api/flashcards/decks/{deck['id']}/cards/{ref}/grade", json={"grade": "good"})

    removed = client.delete(f"/api/flashcards/decks/{deck['id']}/cards/{ref}")
    assert removed.json() == {"card_ref": ref, "reviews_removed": 1}
    assert client.get(f"/api/flashcards/decks/{deck['id']}").json()["cards"] == 2


def test_reorder_rewrites_the_order_and_refuses_a_partial_one(client: TestClient) -> None:
    deck = _imported(client)
    refs = [card["ref"] for card in deck["card_list"]]

    reordered = client.post(
        f"/api/flashcards/decks/{deck['id']}/cards/reorder",
        json={"order": [refs[2], refs[0], refs[1]]},
    )
    assert reordered.status_code == 200
    assert [card["ref"] for card in reordered.json()["card_list"]] == [refs[2], refs[0], refs[1]]

    partial = client.post(
        f"/api/flashcards/decks/{deck['id']}/cards/reorder", json={"order": refs[:2]}
    )
    assert partial.status_code == 422


# --- import / export -------------------------------------------------------


def test_the_delimiters_endpoint_names_what_the_parser_accepts(client: TestClient) -> None:
    """So the import dialog never offers an option the server will reject."""
    assert client.get("/api/flashcards/import/delimiters").json() == {
        "field": ["comma", "dash", "tab"],
        "row": ["newline", "semicolon"],
    }


def test_import_from_an_arbitrary_note_not_just_flashcards_md(
    tmp_path: Path,
) -> None:
    """The fix for the feature being unreachable: any note with Q::/A:: works.

    Ingest has been writing exactly this tail into every generated note all
    along, and nothing could read it.
    """
    vault = _vault(tmp_path)
    (vault / "50-Reference").mkdir(parents=True)
    (vault / "50-Reference" / "lecture.md").write_text(
        "# Lecture\n\nProse.\n\n## Self-test\n\nQ:: what is P\nA:: polynomial time\n",
        encoding="utf-8",
    )
    client = TestClient(create_app(Settings(_vault_path=vault)))
    deck = _deck(client)

    response = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/note",
        json={"path": "50-Reference/lecture.md"},
    )
    assert response.json() == {"added": 1}


def test_import_from_a_note_with_no_pairs_is_422(client: TestClient) -> None:
    deck = _deck(client)
    response = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/note", json={"path": "nope.md"}
    )
    assert response.status_code == 422


def test_import_refuses_a_path_that_escapes_the_vault(client: TestClient) -> None:
    deck = _deck(client)
    response = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/note", json={"path": "../../secrets.md"}
    )
    assert response.status_code == 422


def test_paste_import_parses_delimited_rows(client: TestClient) -> None:
    deck = _deck(client)
    response = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/paste",
        json={"text": "ser,to be\nestar,to be (temporary)", "field": "comma", "row": "newline"},
    )
    assert response.json() == {"added": 2}
    cards = client.get(f"/api/flashcards/decks/{deck['id']}").json()["card_list"]
    # Split on the first delimiter only, so a definition keeps its commas.
    assert cards[1]["back"] == "to be (temporary)"


def test_paste_import_rejects_an_unknown_delimiter_and_an_empty_result(
    client: TestClient,
) -> None:
    deck = _deck(client)
    unknown = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/paste",
        json={"text": "a|b", "field": "pipe", "row": "newline"},
    )
    assert unknown.status_code == 422

    nothing = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/paste",
        json={"text": "no delimiter anywhere", "field": "tab", "row": "newline"},
    )
    assert nothing.status_code == 422


def test_export_writes_a_note_the_importer_reads_back(client: TestClient) -> None:
    deck = _imported(client)
    exported = client.post(f"/api/flashcards/decks/{deck['id']}/export")
    assert exported.json() == {"path": "15-Courses/CS201/flashcards.md"}

    # Round trip: importing the export back into a fresh deck yields the same
    # three cards.
    fresh = _deck(client, title="round trip")
    back = client.post(
        f"/api/flashcards/decks/{fresh['id']}/import/note",
        json={"path": "15-Courses/CS201/flashcards.md"},
    )
    assert back.json() == {"added": 3}


def test_export_of_a_courseless_deck_is_422(client: TestClient) -> None:
    deck = _deck(client, title="Spanish verbs", course="")
    client.post(
        f"/api/flashcards/decks/{deck['id']}/cards",
        json={"cards": [{"front": "ser", "back": "to be"}]},
    )
    assert client.post(f"/api/flashcards/decks/{deck['id']}/export").status_code == 422


# --- studying --------------------------------------------------------------


def test_due_grade_roundtrip_and_the_preview_on_every_card(client: TestClient) -> None:
    deck = _imported(client)
    due = client.get(f"/api/flashcards/decks/{deck['id']}/due").json()
    assert len(due) == 3
    # Each card says what every grade would cost, which is what makes the
    # grade bar a choice rather than four unlabelled verbs.
    assert set(due[0]["preview"]) == {"again", "hard", "good", "easy"}

    ref = due[0]["id"]
    graded = client.post(
        f"/api/flashcards/decks/{deck['id']}/cards/{ref}/grade", json={"grade": "good"}
    ).json()
    assert graded["card_id"] == ref
    assert graded["due_at"] > datetime.now(UTC).isoformat()
    # The label the button promised is the label the commit reports.
    assert graded["due_label"] == due[0]["preview"]["good"]

    after = client.get(f"/api/flashcards/decks/{deck['id']}/due").json()
    assert ref not in [card["id"] for card in after]


def test_grading_an_unknown_deck_or_card_is_404(client: TestClient) -> None:
    assert (
        client.post(
            "/api/flashcards/decks/99999/cards/anything/grade", json={"grade": "good"}
        ).status_code
        == 404
    )
    deck = _imported(client)
    assert (
        client.post(
            f"/api/flashcards/decks/{deck['id']}/cards/nope/grade", json={"grade": "good"}
        ).status_code
        == 404
    )


def test_an_invalid_grade_is_422(client: TestClient) -> None:
    deck = _imported(client)
    ref = deck["card_list"][0]["ref"]
    response = client.post(
        f"/api/flashcards/decks/{deck['id']}/cards/{ref}/grade", json={"grade": "brilliant"}
    )
    assert response.status_code == 422


def test_a_suspended_card_leaves_the_due_queue(client: TestClient) -> None:
    deck = _imported(client)
    ref = deck["card_list"][0]["ref"]
    client.patch(f"/api/flashcards/decks/{deck['id']}/cards/{ref}", json={"suspended": True})
    due = client.get(f"/api/flashcards/decks/{deck['id']}/due").json()
    assert ref not in [card["id"] for card in due]
    assert len(due) == 2


def test_due_summary_reports_total_and_per_deck(client: TestClient) -> None:
    """Backs the Notebook overview's real "cards due" stat."""
    deck = _imported(client)

    summary = client.get("/api/flashcards/due-summary").json()
    assert summary["total"] == 3
    assert summary["decks"] == [
        {"deck_id": deck["id"], "course": "CS201", "title": deck["title"], "due": 3}
    ]

    ref = client.get(f"/api/flashcards/decks/{deck['id']}/due").json()[0]["id"]
    client.post(f"/api/flashcards/decks/{deck['id']}/cards/{ref}/grade", json={"grade": "easy"})

    after = client.get("/api/flashcards/due-summary").json()
    assert after["total"] == 2, "a card graded into the future drops out of the due total"


def test_due_summary_is_zero_with_no_decks(client: TestClient) -> None:
    assert client.get("/api/flashcards/due-summary").json() == {"total": 0, "decks": []}


# --- match -----------------------------------------------------------------


def test_match_records_a_best_time_without_touching_the_schedule(client: TestClient) -> None:
    deck = _imported(client)
    assert client.get(f"/api/flashcards/decks/{deck['id']}/match-best").json() == {"best_ms": None}

    before = client.get("/api/flashcards/due-summary").json()["total"]
    posted = client.post(
        f"/api/flashcards/decks/{deck['id']}/match-score", json={"elapsed_ms": 14_500, "pairs": 3}
    )
    assert posted.json() == {"best_ms": 14_500}
    # Match is a game. Playing it must not spend a card's review.
    assert client.get("/api/flashcards/due-summary").json()["total"] == before

    client.post(
        f"/api/flashcards/decks/{deck['id']}/match-score", json={"elapsed_ms": 30_000, "pairs": 3}
    )
    assert client.get(f"/api/flashcards/decks/{deck['id']}/match-best").json() == {
        "best_ms": 14_500
    }


def test_a_nonsense_match_score_is_refused(client: TestClient) -> None:
    deck = _imported(client)
    response = client.post(
        f"/api/flashcards/decks/{deck['id']}/match-score", json={"elapsed_ms": 0, "pairs": 3}
    )
    assert response.status_code == 422


def test_paste_import_can_read_a_qa_body(client: TestClient) -> None:
    """What a dropped .md becomes.

    The `qa` branch runs the same `parse_qa_pairs` that reads a vault note, so
    a dropped file and an imported note cannot diverge.
    """
    deck = _deck(client)
    response = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/paste",
        json={
            "text": "# Notes\n\nProse.\n\nQ:: what is P\nA:: polynomial time\n",
            "format": "qa",
        },
    )
    assert response.json() == {"added": 1}
    cards = client.get(f"/api/flashcards/decks/{deck['id']}").json()["card_list"]
    assert cards[0]["front"] == "what is P"


def test_paste_import_defaults_to_delimited_so_old_callers_are_unaffected(
    client: TestClient,
) -> None:
    deck = _deck(client)
    response = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/paste",
        json={"text": "a\tb", "field": "tab", "row": "newline"},
    )
    assert response.json() == {"added": 1}


def test_paste_import_rejects_an_unknown_format(client: TestClient) -> None:
    deck = _deck(client)
    response = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/paste",
        json={"text": "a\tb", "format": "yaml"},
    )
    assert response.status_code == 422
    assert "yaml" in response.json()["detail"]


def test_a_qa_body_with_no_pairs_is_422_rather_than_a_silent_zero(client: TestClient) -> None:
    deck = _deck(client)
    response = client.post(
        f"/api/flashcards/decks/{deck['id']}/import/paste",
        json={"text": "just some prose with no pairs", "format": "qa"},
    )
    assert response.status_code == 422
