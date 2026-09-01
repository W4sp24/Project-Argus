"""Invariant I3 for note relationships, in its own file.

I3 says private content never reaches a model — and, once notes carry links,
never reaches a *link* either. A wikilink into ``99-Private/`` would put a
private note's title into a note that is then indexed, retrieved and cited in
chat; the private note's body never moves, but its existence and its name do,
which is the same breach in a smaller package.

Both halves are inherited rather than reimplemented: ``build_link_index``
skips anything ``is_indexable`` refuses, and the RAG index never held those
chunks to begin with. These tests exist because "inherited" is a claim, and
this is the one place in the feature where being wrong is a privacy breach
rather than a cosmetic defect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.vault import relations
from backend.vault.links import build_link_index


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    (tmp_path / "99-Private").mkdir()
    (tmp_path / "99-Private" / "Therapy Notes.md").write_text(
        "---\ntitle: Therapy Notes\naliases: [Determinism]\n---\n\nprivate\n",
        encoding="utf-8",
    )
    (tmp_path / "90-Meta").mkdir()
    (tmp_path / "90-Meta" / "Determinism.md").write_text("journal\n", encoding="utf-8")
    (tmp_path / "50-Reference").mkdir()
    (tmp_path / "50-Reference" / "Public.md").write_text("public\n", encoding="utf-8")
    return tmp_path


def _resolver(vault_path: Path):
    index = build_link_index(vault_path)
    return lambda name: index.resolve(name, from_path="50-Reference/x.summary.md")


def test_a_private_note_is_not_a_resolvable_concept(vault: Path):
    built = relations.build_relations(
        topics=["Therapy Notes"],
        resolve=_resolver(vault),
        neighbours=[],
        source_rel_path="50-Reference/x.pdf",
        note_rel_path="50-Reference/x.summary.md",
        course=None,
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.resolved is False
    assert "99-Private" not in relations.render_section(built)


def test_a_private_alias_cannot_capture_a_public_concept_name(vault: Path):
    """The private note claims the alias 'Determinism', and the dev journal
    has a note of that name. A hollow link is the correct outcome; a link into
    either zone would be the breach."""
    built = relations.build_relations(
        topics=["Determinism"],
        resolve=_resolver(vault),
        neighbours=[],
        source_rel_path="50-Reference/x.pdf",
        note_rel_path="50-Reference/x.summary.md",
        course=None,
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.wikilink() == "[[Determinism]]"
    section = relations.render_section(built)
    assert "99-Private" not in section
    assert "90-Meta" not in section


def test_a_public_concept_still_resolves(vault: Path):
    """The negative tests above would also pass if resolution were broken
    outright. This is the control."""
    built = relations.build_relations(
        topics=["Public"],
        resolve=_resolver(vault),
        neighbours=[],
        source_rel_path="50-Reference/x.pdf",
        note_rel_path="50-Reference/x.summary.md",
        course=None,
    )
    concept = next(link for link in built.links if link.kind == "concept")
    assert concept.resolved is True
    assert concept.wikilink() == "[[Public]]"


@pytest.mark.parametrize(
    "private_path",
    ["99-Private/Therapy Notes.md", "90-Meta/Determinism.md"],
)
def test_a_protected_path_offered_as_a_neighbour_is_still_refused(vault: Path, private_path):
    """Defence in depth: the index cannot supply one, but if a caller ever
    hands one over it must not become a link."""
    built = relations.build_relations(
        topics=[],
        resolve=_resolver(vault),
        neighbours=[(private_path, "Therapy Notes")],
        source_rel_path="50-Reference/x.pdf",
        note_rel_path="50-Reference/x.summary.md",
        course=None,
    )
    assert not [link for link in built.links if link.kind == "neighbour"]
    section = relations.render_section(built)
    assert "99-Private" not in section
    assert "90-Meta" not in section
