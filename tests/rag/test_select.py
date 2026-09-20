"""Tests for corpus selection.

Before this module, every generator took `course_corpus` -- every chunk of a
course in whatever order chroma returned -- and packed a 60,000-character
*prefix* of it. Three consequences, all visible in the author's vault:

* A course with more material than the budget had most of it structurally
  unreachable, and which part survived was decided by the store's iteration
  order rather than by anything about the request.
* `topics` ("focus on Taylor series") was interpolated into one line of the
  prompt and never narrowed the corpus, so an exam on week 9 was written from
  weeks 1-4.
* `rag/retrieve.py` -- the hybrid search this repo already has -- was called
  by no generation path at all.
"""

from backend.rag.select import (
    MAX_PROMPT_CHARS,
    CorpusSelection,
    pack_excerpts,
    select_corpus,
    topic_queries,
)


class FakeIndex:
    """Duck-typed index: generation only ever asks for every chunk."""

    def __init__(self, chunks: list[dict]) -> None:
        self._chunks = chunks

    def all_chunks(self) -> list[dict]:
        return list(self._chunks)


def _chunk(path: str, seq: int, text: str = "x", course: str = "CS26110", **meta) -> dict:
    return {"text": text, "meta": {"path": path, "course": course, "seq": seq, **meta}}


# --- topic_queries ---------------------------------------------------------


def test_topics_split_into_separate_queries() -> None:
    """Each topic is searched on its own; one long query matches nothing well."""
    assert topic_queries("Taylor series, Rolle's theorem") == [
        "Taylor series",
        "Rolle's theorem",
    ]


def test_topics_split_on_newlines_and_semicolons_too() -> None:
    assert topic_queries("limits\ncontinuity; differentiability") == [
        "limits",
        "continuity",
        "differentiability",
    ]


def test_no_topics_means_no_queries() -> None:
    assert topic_queries(None) == []
    assert topic_queries("   ") == []


# --- selection without a query ---------------------------------------------


def test_every_source_is_represented_rather_than_the_first_one(tmp_path) -> None:
    """The bug this replaces.

    A prefix of chroma's order gives whichever file it happened to return
    first, and nothing from the rest. One 200-slide deck should not be able to
    eat the whole budget while the other three lectures go unseen.
    """
    chunks = [_chunk("wk1.pdf", i, "a" * 500) for i in range(40)]
    chunks += [_chunk("wk2.pdf", i, "b" * 500) for i in range(5)]
    chunks += [_chunk("wk3.pdf", i, "c" * 500) for i in range(5)]

    selection = select_corpus(
        FakeIndex(chunks), course="CS26110", budget_chars=6_000, vault_path=tmp_path
    )

    assert set(selection.files) == {"wk1.pdf", "wk2.pdf", "wk3.pdf"}


def test_a_file_keeps_its_own_document_order(tmp_path) -> None:
    """Slide 1 before slide 9, whatever order the store handed them back in."""
    chunks = [
        _chunk("deck.pptx", 2, "third", slide=9),
        _chunk("deck.pptx", 0, "first", slide=1),
        _chunk("deck.pptx", 1, "second", slide=4),
    ]
    selection = select_corpus(FakeIndex(chunks), course="CS26110", vault_path=tmp_path)
    assert [chunk["text"] for chunk in selection.chunks] == ["first", "second", "third"]


def test_selection_reports_what_it_left_out(tmp_path) -> None:
    """A guide written from a third of a course has to be able to say so."""
    chunks = [_chunk("wk1.pdf", i, "a" * 1_000) for i in range(20)]
    selection = select_corpus(
        FakeIndex(chunks), course="CS26110", budget_chars=5_000, vault_path=tmp_path
    )
    assert selection.truncated
    assert selection.total == 20
    assert selection.covered < 20


def test_nothing_left_out_is_not_reported_as_truncated(tmp_path) -> None:
    chunks = [_chunk("wk1.pdf", i) for i in range(3)]
    selection = select_corpus(FakeIndex(chunks), course="CS26110", vault_path=tmp_path)
    assert not selection.truncated
    assert selection.covered == selection.total == 3


def test_another_course_is_never_selected(tmp_path) -> None:
    chunks = [_chunk("a.pdf", 0, course="CS26110"), _chunk("b.pdf", 0, course="ICS26011")]
    selection = select_corpus(FakeIndex(chunks), course="CS26110", vault_path=tmp_path)
    assert selection.files == ["a.pdf"]


def test_ticked_sources_narrow_the_selection(tmp_path) -> None:
    chunks = [_chunk("a.pdf", 0), _chunk("b.pdf", 0)]
    selection = select_corpus(
        FakeIndex(chunks), course="CS26110", paths=["b.pdf"], vault_path=tmp_path
    )
    assert selection.files == ["b.pdf"]


def test_no_paths_and_empty_paths_are_different(tmp_path) -> None:
    """`None` is "no restriction"; `[]` is "the user unticked everything"."""
    chunks = [_chunk("a.pdf", 0)]
    assert select_corpus(
        FakeIndex(chunks), course="CS26110", paths=None, vault_path=tmp_path
    ).chunks
    assert not select_corpus(
        FakeIndex(chunks), course="CS26110", paths=[], vault_path=tmp_path
    ).chunks


# --- selection with a query ------------------------------------------------


def test_a_query_goes_through_retrieval(tmp_path, monkeypatch) -> None:
    """The first time any generation path uses the hybrid search we ship."""
    asked: list[dict] = []
    hit = _chunk("wk9.pdf", 0, "Taylor series expansion")

    def fake_retrieve(index, query, vault_path, **kwargs):
        asked.append({"query": query, **kwargs})
        return [hit]

    monkeypatch.setattr("backend.rag.select.retrieve", fake_retrieve)
    chunks = [_chunk("wk1.pdf", 0, "limits"), hit]
    selection = select_corpus(
        FakeIndex(chunks),
        course="CS26110",
        queries=["Taylor series"],
        vault_path=tmp_path,
    )

    assert [entry["query"] for entry in asked] == ["Taylor series"]
    assert asked[0]["course"] == "CS26110", "retrieval must stay inside the course"
    assert selection.chunks == [hit]


def test_each_topic_is_searched_separately(tmp_path, monkeypatch) -> None:
    seen: list[str] = []

    def fake_retrieve(index, query, vault_path, **kwargs):
        seen.append(query)
        return [_chunk(f"{query}.pdf", 0)]

    monkeypatch.setattr("backend.rag.select.retrieve", fake_retrieve)
    selection = select_corpus(
        FakeIndex([]),
        course="CS26110",
        queries=["limits", "continuity"],
        vault_path=tmp_path,
    )

    assert seen == ["limits", "continuity"]
    assert len(selection.chunks) == 2


def test_the_same_chunk_found_twice_appears_once(tmp_path, monkeypatch) -> None:
    shared = _chunk("wk9.pdf", 0, "Taylor series")
    monkeypatch.setattr(
        "backend.rag.select.retrieve", lambda index, query, vault_path, **kw: [shared]
    )
    selection = select_corpus(
        FakeIndex([]), course="CS26110", queries=["a", "b"], vault_path=tmp_path
    )
    assert len(selection.chunks) == 1


def test_a_query_that_finds_nothing_falls_back_to_the_whole_course(
    tmp_path, monkeypatch
) -> None:
    """Better a broad exam than the "all questions failed" the old path gave."""
    monkeypatch.setattr(
        "backend.rag.select.retrieve", lambda index, query, vault_path, **kw: []
    )
    chunks = [_chunk("wk1.pdf", 0, "limits")]
    selection = select_corpus(
        FakeIndex(chunks), course="CS26110", queries=["astrophysics"], vault_path=tmp_path
    )
    assert selection.chunks
    assert selection.fell_back


# --- packing ---------------------------------------------------------------


def test_the_source_marker_carries_the_path_and_the_location() -> None:
    packed = pack_excerpts([_chunk("deck.pptx", 0, "Rolle's theorem", slide=14)])
    assert "[SOURCE path=deck.pptx slide 14]" in packed
    assert "Rolle's theorem" in packed


def test_a_page_is_labelled_as_a_page() -> None:
    packed = pack_excerpts([_chunk("notes.pdf", 0, "text", page=7)])
    assert "[SOURCE path=notes.pdf page 7]" in packed


def test_a_note_has_no_location() -> None:
    packed = pack_excerpts([_chunk("note.md", 0, "text")])
    assert "[SOURCE path=note.md note]" in packed


def test_one_oversized_chunk_does_not_end_the_packing() -> None:
    """The old loop used `break`, so a single fat chunk truncated the corpus.

    Everything after it was dropped, however small and however relevant --
    which is a silent, order-dependent loss of most of a course.
    """
    chunks = [
        _chunk("a.pdf", 0, "a" * 5_000),
        _chunk("b.pdf", 0, "keep me"),
    ]
    packed = pack_excerpts(chunks, budget_chars=1_000)
    assert "keep me" in packed


def test_packing_stays_within_its_budget() -> None:
    chunks = [_chunk(f"{i}.pdf", 0, "x" * 400) for i in range(50)]
    assert len(pack_excerpts(chunks, budget_chars=2_000)) <= 2_000


def test_the_default_budget_is_the_shared_one() -> None:
    assert MAX_PROMPT_CHARS == 60_000
    assert isinstance(CorpusSelection(chunks=[], queries=[]), CorpusSelection)
