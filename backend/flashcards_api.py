"""Flashcard endpoints, mounted by ``backend.main.create_app``.

Mirrors ``backend/study/api.py``'s style: a router-builder taking
``Settings``, a per-request sqlite connection, and ``StudyError``-style
domain exceptions mapped to HTTP status codes. Deck generation and grading
are normal validated writes (not best-effort/swallowed like usage logging).
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import Settings
from backend.db import connect, init_schema
from backend.flashcards import (
    DeckSummary,
    DueCard,
    FlashcardsError,
    GradeResult,
    due_cards,
    generate_deck,
    grade_card,
    list_decks,
    load_deck,
)


class GenerateDeckRequest(BaseModel):
    course: str


class GradeRequest(BaseModel):
    grade: str


def build_flashcards_router(settings: Settings) -> APIRouter:
    """All /api/flashcards routes."""
    router = APIRouter(prefix="/api/flashcards")

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    @router.post("/decks", response_model=DeckSummary)
    def create_deck(request: GenerateDeckRequest) -> DeckSummary:
        conn = db()
        try:
            deck_id = generate_deck(settings.vault_path, conn, request.course)
            deck = load_deck(conn, deck_id)
        except FlashcardsError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            conn.close()
        return DeckSummary(
            id=deck["id"],
            course=deck["course"],
            title=deck["title"],
            created_at=deck["created_at"],
            cards=len(deck["cards"]),
        )

    @router.get("/decks", response_model=list[DeckSummary])
    def decks(course: str | None = None) -> list[DeckSummary]:
        conn = db()
        try:
            return list_decks(conn, course)
        finally:
            conn.close()

    @router.get("/decks/{deck_id}/due", response_model=list[DueCard])
    def due(deck_id: int) -> list[DueCard]:
        conn = db()
        try:
            return due_cards(conn, deck_id)
        except FlashcardsError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            conn.close()

    @router.post("/decks/{deck_id}/cards/{card_id}/grade", response_model=GradeResult)
    def grade(deck_id: int, card_id: str, request: GradeRequest) -> GradeResult:
        conn = db()
        try:
            return grade_card(conn, deck_id, card_id, request.grade)
        except FlashcardsError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            conn.close()

    return router
