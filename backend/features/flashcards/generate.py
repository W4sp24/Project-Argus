"""Flashcards written from the course corpus, not from one hand-authored file.

Deck generation used to mean parsing ``<courses>/<CODE>/flashcards.md``, which
nothing wrote. This generates cards from the same chunks the study guide and
the practice exam read, and honours the same source selection — which is what
lets the Course Hub's deck button drop the apology it used to carry
(*"reads flashcards.md · ignores the selection"*).

The model is asked for ``Q::``/``A::`` pairs rather than JSON, deliberately:
``parsing.parse_qa_pairs`` is then the single parser for every non-generated
route as well as this one, and a model that drifts mid-reply degrades to fewer
cards instead of one unparseable blob. It also means the markdown
``math_contract()`` is the right contract here — unlike the exam, whose JSON
shape inverts three of its rules.
"""

from __future__ import annotations

from typing import Any

from backend.agent.formatting import compose, math_contract
from backend.features.flashcards.parsing import parse_qa_pairs
from backend.features.flashcards.store import FlashcardsError
from backend.features.study.practice_exam import MAX_PROMPT_CHARS, Generator

#: Refuse to write a deck larger than this in one pass. A model asked for 200
#: cards produces filler long before it produces 200 good ones.
MAX_CARDS = 60

#: What each difficulty asks the model for.
#:
#: A *fragment*, not the bare adjective. ``exam_prompt`` interpolates the word
#: itself — ``f"Create a {difficulty} practice exam"`` — which leaves every
#: model to decide privately what "hard" means, and they do not agree. Naming
#: the behaviour is the difference between a setting and a suggestion.
DIFFICULTIES: dict[str, str] = {
    "easy": (
        "Recall level. One fact per card, answerable in a few words. Prefer the "
        "definitions and named quantities a reader must know before anything else "
        "makes sense."
    ),
    "medium": (
        "Understanding level. The answer should be a short explanation rather than "
        "a single term, and the question should be one a reader could get wrong by "
        "having only memorised the definition."
    ),
    "hard": (
        "Exam level. Ask questions that need two or more facts combined, or that "
        "apply the material to a case the excerpts do not state outright. Avoid "
        "anything answerable by pattern-matching a single sentence."
    ),
}

#: What each card style looks like, with the shape the model should produce.
#:
#: Cloze needs no new storage and no new renderer: a blank is just a question
#: whose text contains ``___``, so it parses and typesets exactly like the rest.
CARD_STYLES: dict[str, str] = {
    "definition": (
        "DEFINITION — a term on the front, its meaning on the back.\n"
        '  Q:: What is a stationary point?\n'
        '  A:: A point where the gradient is zero.'
    ),
    "concept": (
        "CONCEPT — a why or how question whose answer is a short explanation.\n"
        '  Q:: Why does merge sort beat insertion sort on large inputs?\n'
        '  A:: It halves the problem each level, so the work is $O(n \\log n)$ '
        "rather than $O(n^2)$."
    ),
    "cloze": (
        "CLOZE — a sentence from the material with one key part replaced by ___, "
        "and only the missing part on the back.\n"
        '  Q:: Merge sort runs in ___ time in the worst case.\n'
        '  A:: $O(n \\log n)$'
    ),
    "application": (
        "APPLICATION — a short scenario the reader must apply the material to.\n"
        '  Q:: You must sort 10M records with 2GB of RAM. Which sort, and why?\n'
        '  A:: External merge sort — it streams runs from disk instead of needing '
        "the whole input in memory."
    ),
}

#: Used when a caller names no style, and it is what the generator did before
#: styles existed: definitions plus conceptual questions.
DEFAULT_STYLES: tuple[str, ...] = ("definition", "concept")

DEFAULT_DIFFICULTY = "medium"

#: A user's own instruction is free text and lands in a prompt, so it is capped
#: rather than trusted to be short. Nothing here is a security boundary — it is
#: the user's own text going to the user's own model — but an unbounded field
#: can silently eat the excerpt budget the cards are supposed to come from.
MAX_INSTRUCTIONS = 600


def summarise_options(difficulty: str, styles: list[str], n: int) -> str:
    """One line naming what a deck was asked for, for its description."""
    return f"{difficulty} · {', '.join(styles)} · up to {n} cards"


def deck_prompt(
    course: str,
    corpus: list[dict[str, Any]],
    n: int,
    *,
    difficulty: str = DEFAULT_DIFFICULTY,
    styles: list[str] | None = None,
    instructions: str = "",
) -> str:
    """Ask for ``n`` cards grounded only in the excerpts.

    Packs excerpts to the same ``MAX_PROMPT_CHARS`` budget the exam uses, and
    stops at the same boundary, so the two features fill a context window the
    same way.

    ``instructions`` is the user's own free text and goes **last**, where a
    later instruction beats an earlier one for most models — that is the point
    of a custom prompt. What it may not beat is the output format, so the
    format line is restated after it. An instruction like "answer in JSON"
    would otherwise produce a reply ``parse_qa_pairs`` reads as zero cards,
    which is a robustness problem rather than a safety one: the text is the
    user's, going to the user's model.
    """
    excerpts: list[str] = []
    used = 0
    for chunk in corpus:
        meta = chunk["meta"]
        where = (
            f"page {meta['page']}"
            if meta.get("page")
            else (f"slide {meta['slide']}" if meta.get("slide") else "note")
        )
        block = f"[SOURCE path={meta.get('path')} {where}]\n{chunk['text']}\n"
        if used + len(block) > MAX_PROMPT_CHARS:
            break
        excerpts.append(block)
        used += len(block)

    chosen = list(styles) if styles else list(DEFAULT_STYLES)
    style_block = "\n\n".join(CARD_STYLES[style] for style in chosen)
    spread = (
        "Spread the cards across those styles."
        if len(chosen) > 1
        else "Every card must use that style."
    )

    task = f"""Write up to {n} flashcards for course {course}, grounded ONLY in the
source excerpts below.

Return ONLY the cards, in this exact line format and nothing else:

Q:: <the question, one line>
A:: <the answer, one line>

DIFFICULTY
{DIFFICULTIES[difficulty]}

CARD STYLES
{style_block}

{spread}

Rules:
- One fact per card. A card testing two things tests neither.
- The question must be answerable from the excerpts alone.
- No card may repeat another's question.
- Do not number the cards, do not add headings, do not add commentary."""

    extra = instructions.strip()[:MAX_INSTRUCTIONS]
    user_block = (
        f"ADDITIONAL INSTRUCTIONS FROM THE USER\n{extra}\n\n"
        "Follow those wherever they do not conflict with the line format above: "
        "every card is still a Q:: line followed by an A:: line, and nothing else."
        if extra
        else ""
    )

    return compose(task, math_contract(), user_block, f"SOURCES:\n{''.join(excerpts)}")


def validate_options(difficulty: str, styles: list[str]) -> None:
    """Refuse a difficulty or style this module has no fragment for.

    Raises :class:`FlashcardsError`. Checked rather than passed through: an
    unknown value would otherwise reach ``DIFFICULTIES[...]`` as a KeyError on
    a worker thread, where it surfaces as a failed job with an opaque message
    instead of a 422 on the request that was actually wrong.
    """
    if difficulty not in DIFFICULTIES:
        raise FlashcardsError(
            f"unknown difficulty {difficulty!r} — expected one of {', '.join(DIFFICULTIES)}"
        )
    unknown = [style for style in styles if style not in CARD_STYLES]
    if unknown:
        raise FlashcardsError(
            f"unknown card style {unknown[0]!r} — expected one of {', '.join(CARD_STYLES)}"
        )


async def generate_cards(
    generator: Generator,
    corpus: list[dict[str, Any]],
    course: str,
    n: int = 20,
    *,
    difficulty: str = DEFAULT_DIFFICULTY,
    styles: list[str] | None = None,
    instructions: str = "",
) -> list[dict[str, str]]:
    """Generate cards from a corpus. Raises :class:`FlashcardsError` if none survive.

    Duplicate questions are dropped rather than stored: a deck that asks the
    same thing twice wastes two reviews on one fact, and models repeat
    themselves near the end of a long list.
    """
    if not corpus:
        raise FlashcardsError(f"no indexed material for {course} to write cards from")
    chosen = list(styles) if styles else list(DEFAULT_STYLES)
    validate_options(difficulty, chosen)
    wanted = max(1, min(n, MAX_CARDS))

    reply = await generator(
        deck_prompt(
            course,
            corpus,
            wanted,
            difficulty=difficulty,
            styles=chosen,
            instructions=instructions,
        )
    )
    pairs = parse_qa_pairs(reply)
    if not pairs:
        raise FlashcardsError(
            "the model returned nothing shaped like a flashcard — try again, or a larger model"
        )

    cards: list[dict[str, str]] = []
    seen: set[str] = set()
    for front, back in pairs:
        key = front.strip().casefold()
        if key in seen:
            continue
        seen.add(key)
        cards.append({"front": front, "back": back})
        if len(cards) >= wanted:
            break
    return cards
