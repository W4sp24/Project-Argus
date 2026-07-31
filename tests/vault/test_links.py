"""Tests for wikilink target resolution (backend/vault/links.py).

The private-zone case is the important one: link expansion feeds retrieved
context to a model, so a wikilink that resolved into 99-Private would breach
invariant I3 just as surely as indexing the file would.
"""

from pathlib import Path

import pytest

from backend.core.taxonomy import Taxonomy
from backend.vault.links import build_link_index


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    for folder in ("50-Reference", "20-Projects", "99-Private", "90-Meta"):
        (tmp_path / folder).mkdir()
    return tmp_path


def _write(vault: Path, rel: str, body: str = "body\n") -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_resolves_a_plain_stem(vault: Path) -> None:
    _write(vault, "50-Reference/Hoare Partition.md")
    index = build_link_index(vault)
    assert (
        index.resolve("Hoare Partition", from_path="50-Reference/algorithms.md")
        == "50-Reference/Hoare Partition.md"
    )


def test_resolves_a_path_qualified_link(vault: Path) -> None:
    _write(vault, "20-Projects/Overview.md")
    index = build_link_index(vault)
    assert (
        index.resolve("20-Projects/Overview", from_path="50-Reference/x.md")
        == "20-Projects/Overview.md"
    )


def test_resolves_a_frontmatter_alias(vault: Path) -> None:
    _write(
        vault,
        "50-Reference/Hoare Partition.md",
        "---\naliases: [the partition scheme]\n---\n\nTwo pointers.\n",
    )
    index = build_link_index(vault)
    assert (
        index.resolve("the partition scheme", from_path="50-Reference/algorithms.md")
        == "50-Reference/Hoare Partition.md"
    )


def test_a_single_string_alias_is_accepted(vault: Path) -> None:
    """Obsidian allows `aliases: foo` as well as a list."""
    _write(vault, "50-Reference/Note.md", "---\naliases: shorthand\n---\n\nbody\n")
    index = build_link_index(vault)
    assert index.resolve("shorthand", from_path="20-Projects/x.md") == "50-Reference/Note.md"


def test_unknown_target_resolves_to_none(vault: Path) -> None:
    index = build_link_index(vault)
    assert index.resolve("Nothing Here", from_path="50-Reference/x.md") is None
    assert index.resolve("   ", from_path="50-Reference/x.md") is None


# --- ambiguity rule ----------------------------------------------------------


def test_ambiguity_prefers_a_note_in_the_linking_notes_folder(vault: Path) -> None:
    _write(vault, "50-Reference/Overview.md")
    _write(vault, "20-Projects/Overview.md")
    index = build_link_index(vault)

    assert (
        index.resolve("Overview", from_path="20-Projects/plan.md") == "20-Projects/Overview.md"
    )
    assert (
        index.resolve("Overview", from_path="50-Reference/x.md") == "50-Reference/Overview.md"
    )


def test_ambiguity_without_a_same_folder_match_is_deterministic(vault: Path) -> None:
    """Shortest path wins, ties broken alphabetically — same answer every run."""
    _write(vault, "20-Projects/deep/nested/Overview.md")
    _write(vault, "50-Reference/Overview.md")
    index = build_link_index(vault)

    first = index.resolve("Overview", from_path="90-Meta/unrelated.md")
    assert first == "50-Reference/Overview.md"
    assert build_link_index(vault).resolve("Overview", from_path="90-Meta/unrelated.md") == first


# --- invariant I3 ------------------------------------------------------------


def test_a_link_into_the_private_zone_is_never_resolved(vault: Path) -> None:
    """I3: private notes must not be reachable through link expansion."""
    _write(vault, "99-Private/diary.md")
    index = build_link_index(vault)

    assert index.resolve("diary", from_path="50-Reference/x.md") is None
    assert index.resolve("99-Private/diary", from_path="50-Reference/x.md") is None


def test_a_private_alias_is_not_resolved_either(vault: Path) -> None:
    _write(vault, "99-Private/diary.md", "---\naliases: [journal]\n---\n\nsecret\n")
    index = build_link_index(vault)
    assert index.resolve("journal", from_path="50-Reference/x.md") is None


def test_the_dev_journal_zone_is_excluded_too(vault: Path) -> None:
    _write(vault, "90-Meta/session.md")
    index = build_link_index(vault)
    assert index.resolve("session", from_path="50-Reference/x.md") is None


def test_exclusion_follows_a_renamed_private_folder(vault: Path) -> None:
    """The guard is the configured taxonomy, not the literal '99-Private'."""
    tax = Taxonomy(private="Secrets")
    (vault / "Secrets").mkdir()
    _write(vault, "Secrets/diary.md")
    _write(vault, "50-Reference/public.md")

    index = build_link_index(vault, taxonomy=tax)

    assert index.resolve("diary", from_path="50-Reference/x.md") is None
    assert index.resolve("public", from_path="50-Reference/x.md") == "50-Reference/public.md"


def test_unreadable_frontmatter_does_not_abort_the_walk(vault: Path) -> None:
    """One malformed note must not cost the whole vault its link index."""
    _write(vault, "50-Reference/broken.md", "---\n: : not: valid: yaml\n---\n")
    _write(vault, "50-Reference/fine.md")

    index = build_link_index(vault)

    assert index.resolve("fine", from_path="50-Reference/x.md") == "50-Reference/fine.md"
