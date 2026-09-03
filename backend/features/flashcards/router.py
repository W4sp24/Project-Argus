"""Flashcard endpoints, mounted by ``backend.main.create_app``.

Mirrors ``backend/features/study/router.py``'s style: a router-builder taking
``Settings``, a per-request sqlite connection, and domain exceptions mapped to
HTTP status codes.

One route changed meaning in this rewrite, and it is worth naming.
``POST /decks`` used to take ``{course}`` and generate a deck by parsing that
course's ``flashcards.md``. It now *creates* a deck and nothing else. Parsing
``flashcards.md`` became one case of ``POST /decks/{id}/import/note`` — which
reads any note — and generating from the corpus became
``POST /decks/generate``, an async job. The old endpoint had exactly one input
that nothing in Argus ever wrote, which is why flashcards were unreachable.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.flashcards import generate, store, vault
from backend.features.flashcards.jobs import run_deck_job
from backend.features.flashcards.parsing import (
    FIELD_DELIMITERS,
    ROW_DELIMITERS,
    parse_delimited,
    parse_qa_pairs,
)
from backend.features.flashcards.store import (
    CardInfo,
    DeckDetail,
    DeckSummary,
    DueCard,
    DueSummary,
    FlashcardsError,
    GradeResult,
)
from backend.features.ingest import store as jobstore


class CreateDeckRequest(BaseModel):
    title: str
    course: str = ""
    description: str = ""


class UpdateDeckRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    course: str | None = None


class NewCard(BaseModel):
    front: str
    back: str
    hint: str | None = None


class AddCardsRequest(BaseModel):
    cards: list[NewCard]


class UpdateCardRequest(BaseModel):
    front: str | None = None
    back: str | None = None
    hint: str | None = None
    starred: bool | None = None
    suspended: bool | None = None


class ReorderRequest(BaseModel):
    order: list[str]


class GradeRequest(BaseModel):
    grade: str


class ImportNoteRequest(BaseModel):
    path: str


class ImportPasteRequest(BaseModel):
    text: str
    field: str = "tab"
    row: str = "newline"
    #: "delimited" reads `field`/`row`; "qa" reads Q::/A:: pairs and ignores
    #: both. Defaults to delimited so every existing caller is unaffected.
    format: str = "delimited"


class ExportResult(BaseModel):
    path: str


class MatchScoreRequest(BaseModel):
    elapsed_ms: int = Field(gt=0)
    pairs: int = Field(gt=0)


class MatchBest(BaseModel):
    best_ms: int | None


class GenerateDeckRequest(BaseModel):
    course: str
    #: The SOURCES-rail selection. `None` means the whole course, which is what
    #: a caller with no rail (the Notebook overview) sends.
    sources: list[str] | None = None
    model: str | None = None
    n: int = 20
    title: str | None = None
    difficulty: str = generate.DEFAULT_DIFFICULTY
    #: Which card shapes to write. Empty means the historical default
    #: (definitions plus conceptual questions).
    styles: list[str] = Field(default_factory=list)
    #: The user's own instruction, appended to the prompt.
    instructions: str = ""


class GenerateOptions(BaseModel):
    """What `POST /decks/generate` will accept, so the UI cannot offer more."""

    difficulties: list[str]
    styles: list[str]
    default_difficulty: str
    default_styles: list[str]
    max_cards: int
    max_instructions: int


class CardsAdded(BaseModel):
    added: int


class DeckDeleteSummary(BaseModel):
    deck_id: int
    reviews_removed: int


class CardDeleteSummary(BaseModel):
    card_ref: str
    reviews_removed: int


def _default_job_runner(run: Callable[[], None]) -> None:
    """Run a job on a daemon thread. Replaced in tests by a deterministic one."""
    threading.Thread(target=run, daemon=True).start()


def build_flashcards_router(
    settings: Settings,
    generator: Any = None,
    corpus_for: Callable[[str, list[str] | None], list[dict[str, Any]]] | None = None,
    job_runner: Callable[[Callable[[], None]], None] | None = None,
) -> APIRouter:
    """All /api/flashcards routes.

    ``generator`` and ``corpus_for`` are only needed by ``POST /decks/generate``;
    everything else is pure storage. They are optional so a test that only
    exercises authoring does not have to build a model.

    ``job_runner`` is the same injection the study and ingest routers take, and
    for the same reason: a test that spawned the real daemon thread would be
    testing ``threading``.
    """
    router = APIRouter(prefix="/api/flashcards")
    run_job = job_runner or _default_job_runner

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    def _fail(exc: FlashcardsError, status: int) -> HTTPException:
        return HTTPException(status_code=status, detail=str(exc))

    def _bind_model(model: str | None) -> Any:
        """Bind the injected generator to a chosen model, if it accepts one.

        Same shape as ``study/router.py::_generator_for``: model-aware
        generators get the model, single-argument ones (every test fake) keep
        working untouched.
        """
        if not model:
            return generator

        async def run(prompt: str) -> str:
            try:
                call = generator(prompt, model=model)
            except TypeError:
                call = generator(prompt)
            return await call

        return run

    # --- decks -------------------------------------------------------------

    @router.post("/decks", response_model=DeckSummary)
    def create_deck(request: CreateDeckRequest) -> DeckSummary:
        conn = db()
        try:
            deck_id = store.create_deck(
                conn,
                title=request.title,
                course=request.course,
                description=request.description,
            )
            return store.load_deck(conn, deck_id)
        except FlashcardsError as exc:
            raise _fail(exc, 422) from exc
        finally:
            conn.close()

    @router.post("/decks/generate")
    def generate_deck(request: GenerateDeckRequest) -> Any:
        """Write a deck from the course corpus, in the background.

        The deck row is created here, before the 202, so the response can name
        the deck the cards will land in and the UI can open it immediately. A
        generation that fails therefore leaves an empty deck rather than
        nothing, which is the honest outcome -- and the job row says why.

        The corpus is read here too, not in the job, for the same reason the
        study router reads it in the handler: "none of the selected sources are
        indexed" is a 422 about *this request*, and answering it with a 202 and
        a job that fails a minute later is strictly worse.
        """
        if generator is None or corpus_for is None:
            raise HTTPException(
                status_code=503, detail="deck generation is not configured on this server"
            )
        styles = request.styles or list(generate.DEFAULT_STYLES)
        try:
            # Here, not in the job: an unknown difficulty is a fact about *this
            # request*, and answering it with a 202 and a job that fails a
            # minute later is strictly worse.
            generate.validate_options(request.difficulty, styles)
        except FlashcardsError as exc:
            raise _fail(exc, 422) from exc
        corpus = corpus_for(request.course, request.sources)

        conn = db()
        try:
            deck_id = store.create_deck(
                conn,
                title=request.title or f"{request.course} — generated",
                course=request.course,
                source="generated",
            )
            job_id = jobstore.create_job(
                conn,
                target=settings.taxonomy.course_study(request.course),
                filenames=[f"{request.course} flashcards"],
                kind="deck",
                params={
                    "course": request.course,
                    "deck_id": deck_id,
                    "model": request.model,
                    "difficulty": request.difficulty,
                    "styles": styles,
                },
            )
        except FlashcardsError as exc:
            raise _fail(exc, 422) from exc
        finally:
            conn.close()

        run_job(
            lambda: run_deck_job(
                job_id,
                settings=settings,
                generator=_bind_model(request.model),
                corpus=corpus,
                course=request.course,
                deck_id=deck_id,
                n=request.n,
                difficulty=request.difficulty,
                styles=styles,
                instructions=request.instructions,
            )
        )
        return JSONResponse(status_code=202, content={"job_id": job_id, "deck_id": deck_id})

    @router.get("/decks", response_model=list[DeckSummary])
    def decks(course: str | None = None) -> list[DeckSummary]:
        conn = db()
        try:
            return store.list_decks(conn, course)
        finally:
            conn.close()

    @router.get("/due-summary", response_model=DueSummary)
    def due_summary_route() -> DueSummary:
        conn = db()
        try:
            return store.due_summary(conn)
        finally:
            conn.close()

    @router.get("/decks/{deck_id}", response_model=DeckDetail)
    def deck_detail(deck_id: int) -> DeckDetail:
        conn = db()
        try:
            return store.load_deck(conn, deck_id)
        except FlashcardsError as exc:
            raise _fail(exc, 404) from exc
        finally:
            conn.close()

    @router.patch("/decks/{deck_id}", response_model=DeckSummary)
    def update_deck(deck_id: int, request: UpdateDeckRequest) -> DeckSummary:
        conn = db()
        try:
            return store.update_deck(
                conn,
                deck_id,
                title=request.title,
                description=request.description,
                course=request.course,
            )
        except FlashcardsError as exc:
            # "no such deck" is a 404; "a deck needs a title" is a 422.
            raise _fail(exc, 404 if "no flashcard deck" in str(exc) else 422) from exc
        finally:
            conn.close()

    @router.delete("/decks/{deck_id}", response_model=DeckDeleteSummary)
    def remove_deck(deck_id: int) -> DeckDeleteSummary:
        conn = db()
        try:
            reviews_removed = store.delete_deck(conn, deck_id)
        except FlashcardsError as exc:
            raise _fail(exc, 404) from exc
        finally:
            conn.close()
        return DeckDeleteSummary(deck_id=deck_id, reviews_removed=reviews_removed)

    # --- cards -------------------------------------------------------------

    @router.post("/decks/{deck_id}/cards", response_model=CardsAdded)
    def add_cards(deck_id: int, request: AddCardsRequest) -> CardsAdded:
        conn = db()
        try:
            added = store.add_cards(
                conn, deck_id, [card.model_dump() for card in request.cards]
            )
        except FlashcardsError as exc:
            raise _fail(exc, 404) from exc
        finally:
            conn.close()
        return CardsAdded(added=added)

    @router.patch("/decks/{deck_id}/cards/{card_ref}", response_model=CardInfo)
    def update_card(deck_id: int, card_ref: str, request: UpdateCardRequest) -> CardInfo:
        conn = db()
        try:
            return store.update_card(
                conn,
                deck_id,
                card_ref,
                front=request.front,
                back=request.back,
                hint=request.hint,
                starred=request.starred,
                suspended=request.suspended,
            )
        except FlashcardsError as exc:
            raise _fail(exc, 404 if "no card" in str(exc) else 422) from exc
        finally:
            conn.close()

    @router.delete("/decks/{deck_id}/cards/{card_ref}", response_model=CardDeleteSummary)
    def remove_card(deck_id: int, card_ref: str) -> CardDeleteSummary:
        conn = db()
        try:
            reviews_removed = store.delete_card(conn, deck_id, card_ref)
        except FlashcardsError as exc:
            raise _fail(exc, 404) from exc
        finally:
            conn.close()
        return CardDeleteSummary(card_ref=card_ref, reviews_removed=reviews_removed)

    @router.post("/decks/{deck_id}/cards/reorder", response_model=DeckDetail)
    def reorder(deck_id: int, request: ReorderRequest) -> DeckDetail:
        conn = db()
        try:
            store.reorder_cards(conn, deck_id, request.order)
            return store.load_deck(conn, deck_id)
        except FlashcardsError as exc:
            raise _fail(exc, 404 if "no flashcard deck" in str(exc) else 422) from exc
        finally:
            conn.close()

    # --- import / export ---------------------------------------------------

    @router.get("/generate/options", response_model=GenerateOptions)
    def generate_options() -> GenerateOptions:
        """What generation accepts, so the dialog never offers a rejected value.

        Same idea as `/import/delimiters` below, and for the same reason: the
        vocabulary lives in one Python module, and the UI reads it rather than
        keeping a second copy that drifts.
        """
        return GenerateOptions(
            difficulties=list(generate.DIFFICULTIES),
            styles=list(generate.CARD_STYLES),
            default_difficulty=generate.DEFAULT_DIFFICULTY,
            default_styles=list(generate.DEFAULT_STYLES),
            max_cards=generate.MAX_CARDS,
            max_instructions=generate.MAX_INSTRUCTIONS,
        )

    @router.get("/import/delimiters")
    def delimiters() -> dict[str, list[str]]:
        """What the paste importer accepts, so the UI never invents an option."""
        return {"field": sorted(FIELD_DELIMITERS), "row": sorted(ROW_DELIMITERS)}

    @router.post("/decks/{deck_id}/import/note", response_model=CardsAdded)
    def import_note(deck_id: int, request: ImportNoteRequest) -> CardsAdded:
        conn = db()
        try:
            added = vault.import_from_note(
                settings.vault_path,
                conn,
                deck_id,
                request.path,
                taxonomy=settings.taxonomy,
            )
        except FlashcardsError as exc:
            raise _fail(exc, 422) from exc
        finally:
            conn.close()
        return CardsAdded(added=added)

    @router.post("/decks/{deck_id}/import/paste", response_model=CardsAdded)
    def import_paste(deck_id: int, request: ImportPasteRequest) -> CardsAdded:
        if request.format not in ("delimited", "qa"):
            raise HTTPException(
                status_code=422,
                detail=f"unknown format {request.format!r} — expected 'delimited' or 'qa'",
            )
        try:
            # One parser per shape, shared with every other route: `qa` is the
            # same function that reads a vault note, so a dropped .md and an
            # imported note cannot diverge.
            pairs = (
                parse_qa_pairs(request.text)
                if request.format == "qa"
                else parse_delimited(request.text, field=request.field, row=request.row)
            )
        except KeyError as exc:
            # The delimiter names arrive from a request body, so an unknown one
            # is a client error rather than a crash.
            raise HTTPException(
                status_code=422, detail=f"unknown delimiter {exc.args[0]!r}"
            ) from exc
        if not pairs:
            raise HTTPException(
                status_code=422,
                detail="nothing to import — no row had both a front and a back",
            )
        conn = db()
        try:
            added = store.add_cards(
                conn, deck_id, [{"front": front, "back": back} for front, back in pairs]
            )
        except FlashcardsError as exc:
            raise _fail(exc, 404) from exc
        finally:
            conn.close()
        return CardsAdded(added=added)

    @router.post("/decks/{deck_id}/export", response_model=ExportResult)
    def export(deck_id: int) -> ExportResult:
        conn = db()
        try:
            path = vault.export_deck(
                settings.vault_path, conn, deck_id, taxonomy=settings.taxonomy
            )
        except FlashcardsError as exc:
            raise _fail(exc, 404 if "no flashcard deck" in str(exc) else 422) from exc
        finally:
            conn.close()
        return ExportResult(path=path)

    # --- study -------------------------------------------------------------

    @router.get("/decks/{deck_id}/due", response_model=list[DueCard])
    def due(deck_id: int) -> list[DueCard]:
        conn = db()
        try:
            return store.due_cards(conn, deck_id)
        except FlashcardsError as exc:
            raise _fail(exc, 404) from exc
        finally:
            conn.close()

    @router.post("/decks/{deck_id}/cards/{card_ref}/grade", response_model=GradeResult)
    def grade(deck_id: int, card_ref: str, request: GradeRequest) -> GradeResult:
        conn = db()
        try:
            return store.grade_card(conn, deck_id, card_ref, request.grade)
        except FlashcardsError as exc:
            raise _fail(exc, 404 if "no card" in str(exc) else 422) from exc
        finally:
            conn.close()

    @router.post("/decks/{deck_id}/match-score", response_model=MatchBest)
    def match_score(deck_id: int, request: MatchScoreRequest) -> MatchBest:
        conn = db()
        try:
            best = store.record_match_score(conn, deck_id, request.elapsed_ms, request.pairs)
        except FlashcardsError as exc:
            raise _fail(exc, 404 if "no flashcard deck" in str(exc) else 422) from exc
        finally:
            conn.close()
        return MatchBest(best_ms=best)

    @router.get("/decks/{deck_id}/match-best", response_model=MatchBest)
    def match_best(deck_id: int) -> MatchBest:
        conn = db()
        try:
            store.load_deck(conn, deck_id)
            return MatchBest(best_ms=store.best_match_score(conn, deck_id))
        except FlashcardsError as exc:
            raise _fail(exc, 404) from exc
        finally:
            conn.close()

    return router
