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


def deck_prompt(course: str, corpus: list[dict[str, Any]], n: int) -> str:
    """Ask for ``n`` cards grounded only in the excerpts.

    Packs excerpts to the same ``MAX_PROMPT_CHARS`` budget the exam uses, and
    stops at the same boundary, so the two features fill a context window the
    same way.
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

    task = f"""Write up to {n} flashcards for course {course}, grounded ONLY in the
source excerpts below.

Return ONLY the cards, in this exact line format and nothing else:

Q:: <the question, one line>
A:: <the answer, one line>

Rules:
- One fact per card. A card testing two things tests neither.
- The question must be answerable from the excerpts alone.
- Prefer "why" and "how" over "what" where the material supports it.
- No card may repeat another's question.
- Do not number the cards, do not add headings, do not add commentary."""

    return compose(task, math_contract(), f"SOURCES:\n{''.join(excerpts)}")


async def generate_cards(
    generator: Generator,
    corpus: list[dict[str, Any]],
    course: str,
    n: int = 20,
) -> list[dict[str, str]]:
    """Generate cards from a corpus. Raises :class:`FlashcardsError` if none survive.

    Duplicate questions are dropped rather than stored: a deck that asks the
    same thing twice wastes two reviews on one fact, and models repeat
    themselves near the end of a long list.
    """
    if not corpus:
        raise FlashcardsError(f"no indexed material for {course} to write cards from")
    wanted = max(1, min(n, MAX_CARDS))

    reply = await generator(deck_prompt(course, corpus, wanted))
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
