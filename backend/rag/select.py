"""Choose which of a course's chunks a generator should actually read.

Every generator used to take ``course_corpus`` -- every chunk of a course, in
whatever order chroma returned -- and pack a 60,000-character **prefix** of
it. Three things followed, all of them visible in the author's vault:

* A course with more material than the budget had most of it structurally
  unreachable, and which third survived was decided by the store's iteration
  order rather than by anything about the request.
* ``topics`` was interpolated into one line of the prompt and never narrowed
  the corpus, so "focus on week 9" produced an exam written from weeks 1-4.
* :mod:`backend.rag.retrieve` -- the hybrid semantic + keyword search this
  repo already has, with its RRF fusion and its reranker -- was called by no
  generation path at all. It existed for chat and for the search box, and the
  feature that most needed it did not use it.

So: with a query, retrieve. Without one, sample the course *evenly* rather
than taking a prefix. Either way, report what was left out, because a guide
written from a third of a course has to be able to say so.

This lives in ``rag/`` rather than in either feature because study and
flashcards both need it and no feature may import another.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.core.taxonomy import Taxonomy
from backend.rag.retrieve import retrieve

#: How much source text a generation prompt may carry. Lives here now because
#: all three generators used to define or import their own copy, and one of
#: them reached across a feature boundary to do it.
MAX_PROMPT_CHARS = 60_000

#: Chunks to ask retrieval for per topic. Generous because generation reads
#: many passages rather than answering from the best one or two.
PER_QUERY_K = 24

#: Topics arrive as free text a student typed. Any of these separates two.
_TOPIC_SPLIT = re.compile(r"[,;\n]+")

#: A topic shorter than this is punctuation or a stray letter, not a query.
_MIN_TOPIC_CHARS = 2


@dataclass(frozen=True)
class CorpusSelection:
    """The chunks a generator will read, and what had to be left out."""

    chunks: list[dict[str, Any]]
    queries: list[str] = field(default_factory=list)
    #: Chunks that matched the course and source filters at all.
    total: int = 0
    #: How many of those made it into ``chunks``.
    covered: int = 0
    #: True when the budget, not the material, decided where this stopped.
    truncated: bool = False
    #: True when a topic search found nothing and the whole course was used.
    fell_back: bool = False

    @property
    def files(self) -> list[str]:
        """The source paths represented, in first-appearance order."""
        seen: dict[str, None] = {}
        for chunk in self.chunks:
            path = chunk["meta"].get("path")
            if path:
                seen.setdefault(str(path), None)
        return list(seen)


def topic_queries(topics: str | None) -> list[str]:
    """Split a free-text topic list into one query per topic.

    Separately, not as one string: "Taylor series, Rolle's theorem" as a
    single query matches passages that are vaguely about both and strongly
    about neither, which is the opposite of what a student asking for two
    topics wants.
    """
    if not topics:
        return []
    found = [part.strip() for part in _TOPIC_SPLIT.split(topics)]
    return [part for part in found if len(part) >= _MIN_TOPIC_CHARS]


def _document_key(chunk: dict[str, Any]) -> tuple[int, int]:
    """Where a chunk sits in its own file: page or slide first, then order."""
    meta = chunk["meta"]
    location = meta.get("page") or meta.get("slide") or 0
    try:
        page = int(location)
    except (TypeError, ValueError):
        page = 0
    try:
        seq = int(meta.get("seq") or 0)
    except (TypeError, ValueError):
        seq = 0
    return page, seq


def _course_chunks(
    index: Any, course: str, paths: list[str] | None
) -> list[dict[str, Any]]:
    """Every chunk of one course, honouring the ticked-sources filter.

    ``None`` and ``[]`` are deliberately different, as they are throughout
    retrieval: no restriction versus the user having unticked everything.
    """
    selected = None if paths is None else frozenset(paths)
    return [
        chunk
        for chunk in index.all_chunks()
        if chunk["meta"].get("course") == course
        and (selected is None or chunk["meta"].get("path") in selected)
    ]


def _balanced(chunks: list[dict[str, Any]], budget_chars: int) -> list[dict[str, Any]]:
    """Sample evenly across source files instead of taking a prefix.

    Group by file, put each file back into its own document order, then take
    one chunk from each in turn until the budget is spent. A 200-slide deck
    can no longer eat the whole allowance while the other three lectures of
    the week go unread.
    """
    by_file: dict[str, list[dict[str, Any]]] = {}
    for chunk in chunks:
        by_file.setdefault(str(chunk["meta"].get("path") or ""), []).append(chunk)
    for group in by_file.values():
        group.sort(key=_document_key)

    picked: list[dict[str, Any]] = []
    used = 0
    rounds = max((len(group) for group in by_file.values()), default=0)
    for depth in range(rounds):
        for group in by_file.values():
            if depth >= len(group):
                continue
            cost = len(group[depth]["text"]) + _MARKER_OVERHEAD
            if used + cost > budget_chars:
                continue
            picked.append(group[depth])
            used += cost
    # Back into reading order, so a prompt shows a file's slides in sequence
    # rather than interleaved with three other lectures.
    picked.sort(key=lambda chunk: (str(chunk["meta"].get("path") or ""), *_document_key(chunk)))
    return picked


#: Roughly what one ``[SOURCE ...]`` line costs. Approximate on purpose --
#: it only has to keep the budget honest, not be exact.
_MARKER_OVERHEAD = 64


def _dedupe(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop repeats, keeping first-seen order.

    One chunk can answer two topics, and a passage pasted twice into a prompt
    is budget spent saying the same thing.
    """
    seen: set[tuple[str, int]] = set()
    unique: list[dict[str, Any]] = []
    for chunk in chunks:
        key = (str(chunk["meta"].get("path") or ""), hash(chunk["text"]))
        if key in seen:
            continue
        seen.add(key)
        unique.append(chunk)
    return unique


def select_corpus(
    index: Any,
    *,
    course: str,
    paths: list[str] | None = None,
    queries: list[str] | None = None,
    vault_path: Path | None = None,
    taxonomy: Taxonomy | None = None,
    budget_chars: int = MAX_PROMPT_CHARS,
    per_query_k: int = PER_QUERY_K,
    rerank: bool = False,
) -> CorpusSelection:
    """Pick the chunks a generator should read for one request.

    With ``queries``, each is put through :func:`backend.rag.retrieve.retrieve`
    scoped to the course and the ticked sources. Without them, the course is
    sampled evenly. A topic search that finds nothing falls back to the whole
    course rather than generating from an empty corpus -- a broad exam is a
    worse answer than a targeted one, but it is a far better answer than the
    "all questions failed their citation check" the empty path produced.
    """
    available = _course_chunks(index, course, paths)
    if paths is not None and not paths:
        return CorpusSelection(chunks=[], queries=list(queries or []))

    asked = list(queries or [])
    fell_back = False
    if asked:
        found: list[dict[str, Any]] = []
        for query in asked:
            found.extend(
                retrieve(
                    index,
                    query,
                    vault_path,
                    k=per_query_k,
                    course=course,
                    paths=paths,
                    taxonomy=taxonomy,
                    rerank=rerank,
                )
            )
        candidates = _dedupe(found)
        if not candidates:
            candidates, fell_back = available, True
    else:
        candidates = available

    chosen = _balanced(candidates, budget_chars)
    return CorpusSelection(
        chunks=chosen,
        queries=asked,
        total=len(candidates),
        covered=len(chosen),
        truncated=len(chosen) < len(candidates),
        fell_back=fell_back,
    )


def source_marker(chunk: dict[str, Any]) -> str:
    """The ``[SOURCE ...]`` line for one chunk.

    One spelling, everywhere. The three generators had drifted into two:
    ``[SOURCE <path> p.3]`` for guides and ``[SOURCE path=<path> page 3]`` for
    exams and decks, while the guide prompt separately told the model to cite
    as ``[<file> p.N]``. A model handed one format and asked for another
    guesses, and `practice_exam._citation_verified` matches on the path it
    actually finds -- so the renderer and the verifier have to agree.
    """
    meta = chunk["meta"]
    if meta.get("page"):
        where = f"page {meta['page']}"
    elif meta.get("slide"):
        where = f"slide {meta['slide']}"
    else:
        where = "note"
    return f"[SOURCE path={meta.get('path')} {where}]"


def pack_excerpts(
    chunks: list[dict[str, Any]], budget_chars: int = MAX_PROMPT_CHARS
) -> str:
    """Render chunks as the prompt's SOURCES block.

    Skips an oversized chunk rather than stopping at it. The three loops this
    replaces all used ``break``, so one fat chunk truncated the corpus and
    everything after it was dropped however small and however relevant -- a
    silent, order-dependent loss of most of a course.
    """
    blocks: list[str] = []
    used = 0
    for chunk in chunks:
        block = f"{source_marker(chunk)}\n{chunk['text']}\n"
        if used + len(block) > budget_chars:
            continue
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks)
