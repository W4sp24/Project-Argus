"""Check a generated question or card for the flaws that make it useless.

Mechanical and model-free, so it costs nothing and cannot itself hallucinate.
It lives in ``agent/`` rather than in either feature because exams and decks
both need it and no feature may import another -- the same reason
:mod:`backend.agent.formatting` is here.

The rules come from two places. For questions, the NBME item-writing guide:
the one-best-answer format, the "cover-the-options" rule (a good stem is
answerable before the options are read), distractors drawn from the same
category as the key, and the published list of technical flaws that let a
test-wise student score above their knowledge. For cards, SuperMemo's twenty
rules -- chiefly the minimum information principle.

One rule is not from either, and is the reason this module exists. **A
question may not be about the document.** The exam this was written against
asked "What is the title of the theorem on slide 20?", "Which theorem is
listed in the excerpt?" and "What slide sits between the two Examples
slides?" -- nine of ten questions testing whether the reader could navigate a
file they will not have in front of them. That is what the model does when it
is told to ground every question in the sources and given nothing but a table
of contents to ground them in. The extraction fix removes the cause; this
removes the symptom if it recurs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["reject", "warn"]


@dataclass(frozen=True)
class Item:
    """One question or card, in the one shape both generators can produce."""

    stem: str
    options: tuple[str, ...] = ()
    answer: str = ""
    explanation: str = ""


@dataclass(frozen=True)
class Flaw:
    """One reason an item is not worth shipping."""

    code: str
    message: str
    severity: Severity = "warn"


def codes(flaws: list[Flaw]) -> set[str]:
    """The codes in a flaw list, for asserting on and for reporting."""
    return {flaw.code for flaw in flaws}


def rejected(flaws: list[Flaw]) -> bool:
    """Whether any flaw is bad enough to drop the item over."""
    return any(flaw.severity == "reject" for flaw in flaws)


#: Phrasings that make a question about the document rather than the subject.
#: "the excerpt", "is listed", "slide 20", "the passage above".
_DOCUMENT_REFERENCE = re.compile(
    r"\b(?:the\s+)?(?:excerpt|passage|document|deck|slide\s*\d+|page\s*\d+|"
    r"source\s+material)\b"
    r"|\baccording to the (?:excerpt|passage|document|text|deck|slide)\b"
    r"|\bis\s+(?:listed|mentioned|included|shown)\b"
    r"|\bwhich of the following is listed\b"
    r"|\bon slide\b|\bin the deck\b|\bthe slide\b",
    re.IGNORECASE,
)

#: Options that are not options. Both let a partially-prepared student reason
#: their way to the key without knowing the material.
_CATCH_ALL = re.compile(r"^\s*(?:all|none|both)\s+of\s+the\s+(?:above|these|options)\s*\.?\s*$",
                        re.IGNORECASE)

#: Absolute qualifiers. A distractor containing one is almost always false and
#: is read that way, so it does no work.
_ABSOLUTE = re.compile(r"\b(?:always|never|every|all|none|must always|cannot ever)\b",
                       re.IGNORECASE)

#: A negative stem is legitimate when it is emphasised, so a reader cannot
#: miss it. Unmarked, it is a reading test.
_NEGATIVE = re.compile(r"\b(not|except|least likely|incorrect)\b", re.IGNORECASE)

#: Deliberately **case-sensitive**: capitals are the emphasis. Matching this
#: case-insensitively would make every negative stem count as emphasised and
#: the check above would never fire at all.
_EMPHASISED_NEGATIVE = re.compile(r"\b(?:NOT|EXCEPT|LEAST)\b|\*\*(?:not|except|least)\*\*")

#: Words too common to count as a shared content word between stem and key.
#: Written as one string and split so it stays readable as prose; ruff would
#: rather see a list literal, and a 90-element list literal is not clearer.
_STOPWORD_TEXT = (
    "a an and are as at be been but by does do for from given has have here how "
    "if in into is it its of on or that the their then there these this those to "
    "was were what when where which who why will with would you your following "
    "each such about above after before between during over under only also more "
    "most other some than they them we our"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split(" "))

#: An option this much longer than the average distractor is a tell.
_LENGTH_TELL_RATIO = 1.6

#: A one-best-answer item needs this many options to be one.
_MIN_OPTIONS = 3

#: Above this share of one key position across a paper, a guesser profits.
_KEY_BALANCE = 0.4

#: Below this many items, key positions cannot be unbalanced in any useful
#: sense -- three items are allowed to share a letter.
_KEY_BALANCE_MIN_ITEMS = 5

#: SuperMemo's minimum information principle, made checkable. An answer past
#: this is a paragraph, and a paragraph is several cards wearing one coat.
_MAX_ANSWER_WORDS = 25

#: Separators that make an answer a list.
_LIST_SEPARATORS = re.compile(r"[;,]|\band\b|\n\s*[-*]\s")

#: A card whose stem asks two things.
_TWO_QUESTIONS = re.compile(r"\?.*\?|\band what\b|\, and what\b", re.IGNORECASE)

#: A stem answerable without knowing anything.
_YES_NO = re.compile(r"^\s*(?:is|are|was|were|does|do|did|can|could|will|would|should|has|have)\b",
                     re.IGNORECASE)


def _words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in _STOPWORDS}


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _document_flaw(text: str) -> Flaw | None:
    if not _DOCUMENT_REFERENCE.search(text):
        return None
    return Flaw(
        "document-reference",
        "asks about the document rather than the subject — the reader will not "
        "have the document, and the citation already records where it came from",
        "reject",
    )


def check_item(item: Item) -> list[Flaw]:
    """Every flaw in one exam question."""
    flaws: list[Flaw] = []
    stem = item.stem or ""

    if (flaw := _document_flaw(stem)) is not None:
        flaws.append(flaw)

    if _NEGATIVE.search(stem) and not _EMPHASISED_NEGATIVE.search(stem):
        flaws.append(
            Flaw(
                "negative-stem",
                "negatively worded without emphasis — write the negative word in "
                "capitals, or ask the question positively",
            )
        )

    if not item.options:
        return flaws  # a short/problem item; everything below is about options

    flaws.extend(_option_flaws(item))
    return flaws


def _option_flaws(item: Item) -> list[Flaw]:
    """The flaws that only a multiple-choice item can have."""
    flaws: list[Flaw] = []
    options = list(item.options)

    if len(options) < _MIN_OPTIONS:
        flaws.append(
            Flaw(
                "too-few-options",
                f"{len(options)} options — a one-best-answer item needs at least "
                f"{_MIN_OPTIONS} plausible ones",
                "reject",
            )
        )

    normalised = [_normalise(option) for option in options]
    if len(set(normalised)) != len(normalised):
        flaws.append(
            Flaw("duplicate-option", "two options say the same thing", "reject")
        )

    if any(_CATCH_ALL.match(option) for option in options):
        flaws.append(
            Flaw(
                "all-of-the-above",
                '"all/none of the above" is reasoned out rather than known',
                "reject",
            )
        )

    key = _normalise(item.answer)
    if key and key not in normalised:
        flaws.append(
            Flaw(
                "answer-not-in-options",
                "the answer is not one of the options, so nothing can answer this "
                "question correctly",
                "reject",
            )
        )
    elif key:
        flaws.extend(_key_flaws(item, options, normalised.index(key)))

    for option in options:
        if _ABSOLUTE.search(option) and _normalise(option) != key:
            flaws.append(
                Flaw(
                    "absolute-term",
                    "a distractor containing always/never/all reads as false "
                    "without being considered",
                )
            )
            break
    return flaws


def _key_flaws(item: Item, options: list[str], key_index: int) -> list[Flaw]:
    """Flaws that compare the key against its distractors."""
    flaws: list[Flaw] = []
    distractors = [option for index, option in enumerate(options) if index != key_index]
    if distractors:
        average = sum(len(option) for option in distractors) / len(distractors)
        if average and len(options[key_index]) > average * _LENGTH_TELL_RATIO:
            flaws.append(
                Flaw(
                    "length-tell",
                    "the key is much longer than its distractors — the most "
                    "reliable tell a test-wise student reads",
                )
            )

    shared = _words(item.stem) & _words(options[key_index])
    unique_to_key = shared - set().union(*(_words(option) for option in distractors), set())
    if unique_to_key:
        flaws.append(
            Flaw(
                "clang-cue",
                f"the key repeats {', '.join(sorted(unique_to_key))} from the stem "
                "and no distractor does",
            )
        )
    return flaws


def check_set(items: list[Item]) -> dict[int, list[Flaw]]:
    """Flaws across a whole paper, keyed by item index.

    Some flaws only exist in company: a key that sits in position A six times
    out of ten is a paper a guesser scores well on, and no single item shows
    it.
    """
    per_item = {index: check_item(item) for index, item in enumerate(items)}

    positions: dict[int, int] = {}
    for item in items:
        if not item.options:
            continue
        normalised = [_normalise(option) for option in item.options]
        key = _normalise(item.answer)
        if key in normalised:
            positions[normalised.index(key)] = positions.get(normalised.index(key), 0) + 1

    total = sum(positions.values())
    if total >= _KEY_BALANCE_MIN_ITEMS:
        for position, count in positions.items():
            if count / total <= _KEY_BALANCE:
                continue
            letter = "ABCDEFGH"[position] if position < 8 else str(position)
            flaw = Flaw(
                "unbalanced-key",
                f"{count} of {total} keys are option {letter} — spread them",
            )
            for index, item in enumerate(items):
                if not item.options:
                    continue
                normalised = [_normalise(option) for option in item.options]
                if _normalise(item.answer) in normalised and normalised.index(
                    _normalise(item.answer)
                ) == position:
                    per_item[index] = [*per_item[index], flaw]
    return per_item


def check_card(card: Item) -> list[Flaw]:
    """Every flaw in one flashcard.

    A different list from :func:`check_item`, because a card is judged on
    whether it can be *recalled* rather than on whether it can be gamed.
    """
    flaws: list[Flaw] = []
    stem, answer = card.stem or "", card.answer or ""

    if (flaw := _document_flaw(stem)) is not None:
        flaws.append(flaw)

    words = len(answer.split())
    if words > _MAX_ANSWER_WORDS:
        flaws.append(
            Flaw(
                "minimum-information",
                f"the answer is {words} words — one card holds one testable thing, "
                "and a paragraph is several cards wearing one coat",
            )
        )

    separators = len(_LIST_SEPARATORS.findall(answer))
    if separators >= 2:
        flaws.append(
            Flaw(
                "enumeration",
                "the answer is a list — enumerations are the hardest thing to "
                "hold, so split it into one card per item",
            )
        )

    if _TWO_QUESTIONS.search(stem):
        flaws.append(
            Flaw("two-facts", "the question asks two things, so it tests neither")
        )

    if _YES_NO.match(stem.strip()) and "?" in stem:
        flaws.append(
            Flaw(
                "yes-no",
                "answerable yes or no — that is recognition, and half marks for "
                "knowing nothing",
            )
        )
    return flaws


def describe(flaws: list[Flaw]) -> str:
    """One line per flaw, for telling a model what to fix."""
    return "\n".join(f"- {flaw.code}: {flaw.message}" for flaw in flaws)


from functools import cache  # noqa: E402 - kept beside its one user
from pathlib import Path  # noqa: E402

_RULES = Path(__file__).parent / "prompts" / "item_writing.md"
_CARD_RULES = Path(__file__).parent / "prompts" / "card_writing.md"


@cache
def item_writing_rules() -> str:
    """The exam rules, stated to the model as well as checked afterwards.

    Checking alone would mean regenerating constantly. Saying them first is
    what makes the validator mostly a safety net rather than a second pass.
    """
    return _RULES.read_text(encoding="utf-8").strip()


@cache
def card_writing_rules() -> str:
    """The card rules -- a different document, because a card is not a question.

    An exam item is judged on whether a test-wise student could game it. A
    card is judged on whether someone who has forgotten the material can
    retrieve it in a few seconds, months later, which is why these are
    SuperMemo's rules and not the NBME's.
    """
    return _CARD_RULES.read_text(encoding="utf-8").strip()


@dataclass(frozen=True)
class SetReport:
    """What a validation pass made of a whole set of items."""

    kept: list[Item] = field(default_factory=list)
    dropped: list[tuple[Item, list[Flaw]]] = field(default_factory=list)

    @property
    def reasons(self) -> dict[str, int]:
        """How many items each flaw code cost, for the job's summary."""
        counts: dict[str, int] = {}
        for _item, flaws in self.dropped:
            for flaw in flaws:
                if flaw.severity == "reject":
                    counts[flaw.code] = counts.get(flaw.code, 0) + 1
        return counts


def sift(items: list[Item]) -> SetReport:
    """Split a set into what ships and what does not, with the reasons.

    Only ``reject`` flaws drop an item. A warning is real -- a length tell is
    a genuine defect -- but dropping a question over one would throw away
    material a student can still learn from, and this runs against a model
    that will not always do better on a second attempt.
    """
    report = SetReport()
    for index, flaws in check_set(items).items():
        if rejected(flaws):
            report.dropped.append((items[index], flaws))
        else:
            report.kept.append(items[index])
    return report
