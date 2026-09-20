"""The shapes a generated note can take, and which sections each one owns.

Before this there was one shape and three prompts arguing about it. Each note
style said "use exactly these sections"; ``note_quality.md`` then said "end
every note with a self-test"; ``topics.md`` said "finally, after everything
above, add one more section". A model was told three different things about
what came last.

That is not merely untidy. ``relations.parse_topics`` keeps ``body[:
heading.start()]`` of the **last** ``## Topics`` -- so anything a model writes
after that heading is silently deleted on the way into the vault. A note that
followed "end every note with a self-test" literally lost its self-test, and
with it the flashcards that section is parsed into.

So section order is owned here, in one place, and stated once:

    the doc type's own sections -> Self-test (if any) -> Topics (if any)

The types themselves are modelled on what the artefact is *for* rather than on
formatting. A briefing and a study guide differ in what a reader does with
them, and a prompt that knows which one it is writing produces a better
version of either than a prompt asked for "notes".
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts" / "doctypes"


class DocTypeError(ValueError):
    """Raised for a doc type this module has no contract for."""


@dataclass(frozen=True)
class DocType:
    """One shape a generated note can take."""

    key: str
    label: str
    description: str
    #: Whether the reader gets a `## Self-test` of `Q::`/`A::` pairs.
    #:
    #: True for every shape today, including the two that are already
    #: question-shaped. An FAQ's `###` headings and Cornell's cues read well
    #: but neither parses: `flashcards.parsing.parse_qa_pairs` reads `Q::` and
    #: `A::` at the start of a line and nothing else, so a type that skipped
    #: this section seeded no deck at all. Some overlap between an FAQ and its
    #: self-test is a smaller cost than that, and asking the same fact two
    #: ways is the one kind of redundancy spaced repetition rewards.
    wants_self_test: bool = True
    #: Whether the machine-readable `## Topics` tail is appended. Always true
    #: today; a field rather than a constant because it is what links a note
    #: into the vault, and a type that opted out would need to say why.
    wants_topics: bool = True
    #: What this type suits, shown in the UI and used by `suggest`.
    suits: str = ""


DOC_TYPES: dict[str, DocType] = {
    "briefing": DocType(
        key="briefing",
        label="Briefing",
        description="What it covers, what it claims, what to take away.",
        suits="A first pass over something you have not read yet.",
    ),
    "study-guide": DocType(
        key="study-guide",
        label="Study guide",
        description="Concept table, worked examples, and the traps.",
        suits="Revising a lecture or a chapter for an exam.",
    ),
    "concept-map": DocType(
        key="concept-map",
        label="Concept map",
        description="The ideas and, more importantly, how they relate.",
        suits="Material you know piecewise but cannot yet see whole.",
    ),
    "faq": DocType(
        key="faq",
        label="FAQ",
        description="The questions the material answers, answered.",
        suits="Checking understanding of something conceptual.",
    ),
    "timeline": DocType(
        key="timeline",
        label="Timeline",
        description="What happened when, and which moments mattered.",
        suits="History, a process, or anything with an order.",
    ),
    "key-terms": DocType(
        key="key-terms",
        label="Key terms",
        description="Every term the material defines, in its own words.",
        suits="Vocabulary-heavy material before an exam.",
    ),
    "cornell": DocType(
        key="cornell",
        label="Cornell notes",
        description="Cues, notes, and a five-sentence summary.",
        suits="Active recall from a lecture you attended.",
    ),
}

#: What the four original note styles map onto. The old keys stay valid
#: forever: they are written into `ingest_jobs.note_style` on every historical
#: row, and a job row says what was asked for at the time. Rewriting that
#: would be a lie about history, so the alias is resolved at read time.
LEGACY_ALIASES: dict[str, str] = {
    "summary": "briefing",
    "study-guide": "study-guide",
    "cornell": "cornell",
    "key-terms": "key-terms",
}

DEFAULT_DOC_TYPE = "study-guide"


@cache
def contract(key: str) -> str:
    """The section contract for one doc type, read from its prompt file.

    Cached like the rest of :mod:`backend.agent.formatting`: prompt text is
    prose that gets edited like prose, so it lives in a file, and a file read
    per generation is a file read too many.
    """
    return (PROMPT_DIR / f"{key}.md").read_text(encoding="utf-8").strip()


def resolve(key: str | None) -> DocType:
    """The doc type for a key, resolving legacy names; default when absent.

    Raises :class:`DocTypeError` naming the valid keys rather than returning
    the default for an unknown one. A typo that silently produced a briefing
    when a study guide was asked for is a worse outcome than a 422.
    """
    if not key:
        return DOC_TYPES[DEFAULT_DOC_TYPE]
    wanted = LEGACY_ALIASES.get(key, key)
    if wanted not in DOC_TYPES:
        raise DocTypeError(
            f"unknown note type {key!r} — expected one of {', '.join(sorted(DOC_TYPES))}"
        )
    return DOC_TYPES[wanted]


def suggest(text: str) -> str:
    """Pick a doc type from the shape of the material.

    Cheap and lexical on purpose: this runs before any model call, on text
    that is about to be sent to one anyway, and a wrong guess costs a
    differently-shaped note rather than a wrong one. The caller's explicit
    choice always wins; this only answers "what if they did not choose".
    """
    sample = text[:20_000]
    lowered = sample.lower()

    equations = sample.count("$")
    definitions = lowered.count("is defined as") + lowered.count("is called")
    definitions += lowered.count("refers to") + lowered.count(" means ")
    dates = sum(lowered.count(word) for word in ("century", " bce", " ad ", "year "))
    dates += _year_like(sample)

    # Order matters: a maths lecture is full of definitions too, so the
    # equation test has to run before the vocabulary one.
    if equations >= 8:
        return "study-guide"
    if dates >= 6:
        return "timeline"
    if definitions >= 8:
        return "key-terms"
    return DEFAULT_DOC_TYPE


def _year_like(text: str) -> int:
    """Rough count of four-digit years, as a chronology signal."""
    import re

    return len(re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", text))
