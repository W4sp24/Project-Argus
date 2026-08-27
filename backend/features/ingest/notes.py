"""What note an ingested file gets, and where that note is written.

Split out of :mod:`backend.features.ingest.pipeline`, which owns *running* a
job: the shape of the note a document turns into is a separate concern, and
one that now has four answers instead of one. The pipeline calls three
functions from here -- :func:`build_prompt`, :func:`note_destination`,
:func:`note_markdown` -- and knows nothing else about note formatting.

The placement rule is the reason this module exists at all. A course document
saved into ``<courses>/<CODE>/materials/`` should produce a note in that
course's ``notes/`` zone, not a ``.summary.md`` sitting beside the PDF: the
Course Hub's SOURCES rail groups by zone, ``corpus.courses()`` counts
``notes/`` to decide whether a course has any notes at all, and a study note
filed under ``materials/`` is invisible to both. Everything outside a course
keeps the old behaviour, because there is no better place for it to go.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

import frontmatter

from backend.agent.formatting import compose, math_contract, note_quality
from backend.core.taxonomy import Taxonomy, active_taxonomy

#: How much of a file's text is handed to the model. Matches the email
#: extractor's budget -- large enough for a lecture, small enough that a
#: 400-page PDF does not become one enormous prompt.
MAX_NOTE_CHARS = 12_000


@dataclass(frozen=True)
class NoteStyle:
    """One offered note shape.

    ``instruction`` is what actually reaches the model; ``label`` and
    ``description`` exist so ``GET /api/ingest/note-styles`` can populate the
    dialog without the frontend hardcoding a list that would drift the moment
    a style is added here.
    """

    key: str
    label: str
    description: str
    instruction: str


_STYLES: tuple[NoteStyle, ...] = (
    NoteStyle(
        key="summary",
        label="Summary",
        description="Condensed prose plus the key takeaways.",
        instruction=(
            "Summarise this document for a student revising it later. Write a short "
            "opening paragraph saying what the document covers, then a `## Key points` "
            "list of its substantive claims, then a `## Takeaways` list of what a reader "
            "should remember. Keep the document's own terminology."
        ),
    ),
    NoteStyle(
        key="study-guide",
        label="Study guide",
        description="Outline, key concepts with definitions, then worked examples.",
        # Deliberately the same three-section shape as
        # backend/features/study/study_guide.py's course-wide guide, scoped to
        # one document -- two different structures for "study guide" in one app
        # would be a worse answer than one structure at two scales.
        instruction=(
            "Turn this document into a study guide. Use exactly these sections:\n"
            "`## Outline` -- the topic map, in the document's own order.\n"
            "`## Key concepts` -- each concept as a bullet with a one-line definition, "
            "and the page or slide it is introduced on where the document says so.\n"
            "`## Worked examples` -- two or three step-by-step examples taken from the "
            "document. Omit this section entirely if the document contains no examples."
        ),
    ),
    NoteStyle(
        key="cornell",
        label="Cornell notes",
        description="Cue questions beside the notes, with a summary block.",
        instruction=(
            "Write Cornell-style notes for this document. Use exactly these sections:\n"
            "`## Cues` -- the questions this material answers, one per line.\n"
            "`## Notes` -- the detailed notes, as nested bullets, in the document's order.\n"
            "`## Summary` -- one paragraph, at most five sentences, of the whole document."
        ),
    ),
    NoteStyle(
        key="key-terms",
        label="Key terms + Q&A",
        description="Definitions plus self-test questions with answers.",
        instruction=(
            "Extract the examinable content of this document. Use exactly these sections:\n"
            "`## Key terms` -- every term the document defines, as `**term** -- definition`.\n"
            "`## Self-test` -- eight to twelve questions covering the material, each written "
            "as `Q:: <question>` on one line and `A:: <answer>` on the next, so the list can "
            "be parsed into flashcards as it stands."
        ),
    ),
)

#: Keyed by ``key`` for validation and lookup. Insertion order is the order
#: the dialog offers them.
NOTE_STYLES: dict[str, NoteStyle] = {style.key: style for style in _STYLES}

#: Written into a generated note's frontmatter, and read by
#: ``study_guide.notes_gap_list`` so a note Argus wrote is never counted as
#: one the user wrote.
GENERATED_BY = "argus"

#: Suffix for a note generated from a course material. Distinct from the
#: ``.summary.md`` used outside a course so the two are told apart on sight
#: -- and by ``notes_gap_list`` -- without parsing frontmatter.
COURSE_NOTE_SUFFIX = ".notes.md"

#: Everything about the note that is true whatever style was picked. The
#: notation and note-quality halves come from
#: :mod:`backend.agent.formatting`, shared with the chat agent and the
#: course-wide study guide -- so a guide and the note beside it are held to one
#: set of rules rather than to whichever sentence each prompt happened to grow.
_HOUSE_RULES = """Do not add a title heading; one is already in the note's
frontmatter, and a second one just repeats it."""

_PROMPT = """{instruction}

{house_rules}

DOCUMENT ({path}):
{text}
"""


class NoteStyleError(KeyError):
    """A style key that names nothing. Carries the valid keys for the 422."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(key)

    def __str__(self) -> str:
        return f"unknown note style {self.key!r} — choose one of {', '.join(NOTE_STYLES)}"


def resolve_style(key: str | None) -> NoteStyle | None:
    """The style for ``key``, or ``None`` for "no style chosen".

    Raises :class:`NoteStyleError` for a non-empty key that names no style,
    so the router turns it into a 422 listing the valid ones. Falling back to
    "no note" would look exactly like the feature being broken.
    """
    clean = (key or "").strip()
    if not clean:
        return None
    try:
        return NOTE_STYLES[clean]
    except KeyError:
        raise NoteStyleError(clean) from None


def build_prompt(style: NoteStyle | None, instruction: str, rel_path: str, text: str) -> str:
    """The prompt for one document's note.

    A free-text instruction *appends to* the style's own instruction rather
    than replacing it: the user picked "Study guide" and then asked for
    something extra, and dropping the structure they chose because they also
    typed a sentence would be the opposite of what they asked for. With no
    style at all the instruction stands alone -- which is exactly how this
    behaved before styles existed.
    """
    return _PROMPT.format(
        instruction=compose(style.instruction if style else "", instruction),
        house_rules=compose(_HOUSE_RULES, note_quality(), math_contract()),
        path=rel_path,
        text=text[:MAX_NOTE_CHARS],
    )


def course_of(rel_path: str, *, taxonomy: Taxonomy | None = None) -> str | None:
    """The course code whose ``materials/`` holds this file, if any.

    Matched against :class:`~backend.core.taxonomy.Taxonomy` rather than a
    literal ``15-Courses/``: the configurable-taxonomy refactor exists because
    hardcoding that name was a real bug, and a renamed courses zone has to
    carry this rule with it.
    """
    tax = taxonomy or active_taxonomy()
    parts = PurePosixPath(rel_path).parts
    # <courses>/<CODE>/materials/<name> -- at least four parts. The file may
    # sit in a subfolder of materials/ without changing which course it is.
    if len(parts) < 4 or parts[0] != tax.courses or parts[2] != "materials":
        return None
    return parts[1]


def note_destination(rel_path: str, *, taxonomy: Taxonomy | None = None) -> str:
    """Where the note for ``rel_path`` goes.

    Derived from the **deduped** source path the writer actually returned, not
    from the uploaded filename: ``_dedupe`` renames a colliding upload to
    ``lecture-2.pdf``, and a note still named for ``lecture`` would collide
    with the previous one. ``create_note`` is deliberately create-only, so
    that collision fails the item for a reason the user cannot act on.
    """
    tax = taxonomy or active_taxonomy()
    source = PurePosixPath(rel_path)
    code = course_of(rel_path, taxonomy=tax)
    if code is not None:
        return f"{tax.course_notes(code)}/{source.stem}{COURSE_NOTE_SUFFIX}"
    return source.with_name(f"{source.stem}.summary.md").as_posix()


def note_markdown(
    rel_path: str,
    style: NoteStyle | None,
    instruction: str,
    body: str,
    *,
    taxonomy: Taxonomy | None = None,
) -> tuple[str, str]:
    """``(note_rel_path, markdown)`` for one source file.

    The trailing wikilink is load-bearing rather than decorative: it is what
    lets ``retrieve.py``'s existing one-hop link expansion reach the source
    from the note, with no new retrieval code. It resolves only when the
    source is itself markdown -- ``build_link_index`` is markdown-only -- so
    for a PDF the link is a readable breadcrumb and nothing more.
    """
    tax = taxonomy or active_taxonomy()
    source = PurePosixPath(rel_path)
    destination = note_destination(rel_path, taxonomy=tax)
    front = {
        "title": f"{source.stem} — {style.label.lower() if style else 'summary'}",
        "type": "note",
        "generated_by": GENERATED_BY,
        "note_style": style.key if style else "custom",
        "source": rel_path,
        "prompt": instruction,
    }
    code = course_of(rel_path, taxonomy=tax)
    if code is not None:
        # Chunk metadata already carries `course` derived from the path, but
        # the frontmatter is what a human -- and Obsidian's own search -- reads.
        front["course"] = code
    post = frontmatter.Post(f"{body.strip()}\n\n[[{source.stem}]]\n", **front)
    return destination, frontmatter.dumps(post) + "\n"
