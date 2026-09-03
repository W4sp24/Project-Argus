"""The retrieval half of note relationships."""

from __future__ import annotations

from backend.rag import neighbours


class _FakeIndex:
    """Stands in for VaultIndex. ``retrieve_result`` is monkeypatched, so this
    only has to be an object identity the call is threaded through."""


def _result(hits: list[dict]):
    return type("R", (), {"results": hits, "related": []})()


def test_nearest_notes_dedupes_by_path_and_keeps_rank_order(monkeypatch, tmp_path):
    """A strong match contributes several chunks; three chunks of one note is
    not three neighbours."""
    hits = [
        {"meta": {"path": "50-Reference/a.md", "title": "A"}, "text": "", "score": 0.9},
        {"meta": {"path": "50-Reference/a.md", "title": "A"}, "text": "", "score": 0.8},
        {"meta": {"path": "50-Reference/b.md", "title": "B"}, "text": "", "score": 0.7},
    ]
    monkeypatch.setattr(neighbours, "retrieve_result", lambda *a, **k: _result(hits))
    found = neighbours.nearest_notes(_FakeIndex(), tmp_path, "text", exclude=set())
    assert found == [("50-Reference/a.md", "A"), ("50-Reference/b.md", "B")]


def test_nearest_notes_honours_exclude_and_limit(monkeypatch, tmp_path):
    hits = [
        {"meta": {"path": f"50-Reference/{name}.md", "title": name}, "text": "", "score": 0.9}
        for name in ("self", "a", "b", "c", "d")
    ]
    monkeypatch.setattr(neighbours, "retrieve_result", lambda *a, **k: _result(hits))
    found = neighbours.nearest_notes(
        _FakeIndex(), tmp_path, "text", exclude={"50-Reference/self.md"}, limit=2
    )
    assert [path for path, _ in found] == ["50-Reference/a.md", "50-Reference/b.md"]


def test_nearest_notes_asks_for_first_order_neighbours_only(monkeypatch, tmp_path):
    """expand_links=True would return neighbours-of-neighbours, which is not
    what 'also in your vault' means."""
    seen: dict = {}

    def _capture(*args, **kwargs):
        seen.update(kwargs)
        return _result([])

    monkeypatch.setattr(neighbours, "retrieve_result", _capture)
    neighbours.nearest_notes(_FakeIndex(), tmp_path, "text", exclude=set())
    assert seen["expand_links"] is False


def test_a_note_with_no_text_never_queries_the_index(monkeypatch, tmp_path):
    def _boom(*args, **kwargs):
        raise AssertionError("an empty note must not reach the index")

    monkeypatch.setattr(neighbours, "retrieve_result", _boom)
    assert neighbours.nearest_notes(_FakeIndex(), tmp_path, "   ", exclude=set()) == []


def test_a_dead_index_yields_no_neighbours_rather_than_failing_the_note(monkeypatch, tmp_path):
    """A note whose neighbours could not be computed is still a good note.
    Losing it because chroma is unavailable is not a trade worth making."""

    def _boom(*args, **kwargs):
        raise RuntimeError("chroma is unavailable")

    monkeypatch.setattr(neighbours, "retrieve_result", _boom)
    assert neighbours.nearest_notes(_FakeIndex(), tmp_path, "text", exclude=set()) == []


def test_a_hit_with_no_title_falls_back_to_its_filename(monkeypatch, tmp_path):
    hits = [{"meta": {"path": "50-Reference/a.md"}, "text": "", "score": 0.9}]
    monkeypatch.setattr(neighbours, "retrieve_result", lambda *a, **k: _result(hits))
    assert neighbours.nearest_notes(_FakeIndex(), tmp_path, "text", exclude=set()) == [
        ("50-Reference/a.md", "a.md")
    ]
