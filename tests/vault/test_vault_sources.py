"""The vault-wide source lister (backend.vault.sources).

This is the generic walker behind both ``GET /api/sources`` and
``course_sources``. It exists because :func:`backend.vault.notes.list_notes`
is markdown-only, so an uploaded PDF -- the whole point of ingesting -- was
invisible to every listing built on it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.taxonomy import Taxonomy
from backend.vault.sources import list_sources


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "00-Inbox" / "files").mkdir(parents=True)
    (root / "00-Inbox" / "files" / "nested").mkdir()
    (root / "15-Courses" / "CS301").mkdir(parents=True)
    (root / "99-Private").mkdir()
    (root / "90-Meta").mkdir()
    (root / ".argus").mkdir()

    (root / "00-Inbox" / "files" / "titled.md").write_text(
        "---\ntitle: A Proper Title\n---\n\n# Ignored heading\n", encoding="utf-8"
    )
    (root / "00-Inbox" / "files" / "lecture.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "00-Inbox" / "files" / "nested" / "deep.md").write_text("# Deep\n", encoding="utf-8")
    (root / "15-Courses" / "CS301" / "syllabus.docx").write_bytes(b"docx bytes")
    (root / "99-Private" / "secret.md").write_text("# Secret\n", encoding="utf-8")
    (root / "90-Meta" / "journal.md").write_text("# Journal\n", encoding="utf-8")
    (root / ".argus" / "state.md").write_text("# State\n", encoding="utf-8")
    return root


def test_lists_non_markdown_files_that_list_notes_cannot_see(vault: Path) -> None:
    """The reported "uploaded files are invisible" bug, at its root."""
    paths = {source.path for source in list_sources(vault)}

    assert "00-Inbox/files/lecture.pdf" in paths
    assert "15-Courses/CS301/syllabus.docx" in paths


def test_excludes_every_protected_zone(vault: Path) -> None:
    """I3's directory half, plus the dev journal and dotdirs."""
    paths = {source.path for source in list_sources(vault)}

    assert not any(path.startswith(("99-Private", "90-Meta", ".argus")) for path in paths)


def test_no_ai_notes_are_excluded_even_outside_the_private_zone(vault: Path) -> None:
    """I3's *tag* half.

    ``is_indexable`` and ``list_notes`` check directories only, for their own
    historical reasons. backend/vault/privacy.py says outright that a new
    outward-facing read must not repeat that gap, and a source listing is one.
    """
    (vault / "00-Inbox" / "files" / "tagged.md").write_text(
        "---\ntags: [no-ai]\n---\n\n# Private thoughts\n", encoding="utf-8"
    )
    (vault / "00-Inbox" / "files" / "inline.md").write_text(
        "# Notes\n\nsomething #no-ai here\n", encoding="utf-8"
    )

    paths = {source.path for source in list_sources(vault)}

    assert "00-Inbox/files/tagged.md" not in paths
    assert "00-Inbox/files/inline.md" not in paths
    assert "00-Inbox/files/titled.md" in paths, "an ordinary note must survive"


def test_title_prefers_frontmatter_then_falls_back_to_the_stem(vault: Path) -> None:
    by_path = {source.path: source for source in list_sources(vault)}

    assert by_path["00-Inbox/files/titled.md"].title == "A Proper Title"
    assert by_path["00-Inbox/files/lecture.pdf"].title == "lecture"


def test_kind_is_the_uppercased_suffix(vault: Path) -> None:
    by_path = {source.path: source for source in list_sources(vault)}

    assert by_path["00-Inbox/files/lecture.pdf"].kind == "PDF"
    assert by_path["00-Inbox/files/titled.md"].kind == "MD"


def test_folder_is_the_parent_and_root_files_report_empty(vault: Path) -> None:
    (vault / "loose.md").write_text("# Loose\n", encoding="utf-8")
    by_path = {source.path: source for source in list_sources(vault)}

    assert by_path["00-Inbox/files/titled.md"].folder == "00-Inbox/files"
    assert by_path["loose.md"].folder == ""


def test_folder_filter_scopes_the_walk(vault: Path) -> None:
    paths = {source.path for source in list_sources(vault, folder="15-Courses/CS301")}

    assert paths == {"15-Courses/CS301/syllabus.docx"}


def test_recursive_false_stays_flat(vault: Path) -> None:
    """``course_sources`` is flat (iterdir), so the generic walker must be able to be."""
    flat = {source.path for source in list_sources(vault, folder="00-Inbox/files", recursive=False)}
    deep = {source.path for source in list_sources(vault, folder="00-Inbox/files")}

    assert "00-Inbox/files/nested/deep.md" not in flat
    assert "00-Inbox/files/nested/deep.md" in deep


def test_suffix_filter_restricts_to_the_indexable_set(vault: Path) -> None:
    (vault / "00-Inbox" / "files" / "screenshot.png").write_bytes(b"png")

    unfiltered = {source.path for source in list_sources(vault)}
    filtered = {
        source.path for source in list_sources(vault, suffixes=Taxonomy().indexable_suffixes)
    }

    assert "00-Inbox/files/screenshot.png" in unfiltered
    assert "00-Inbox/files/screenshot.png" not in filtered


def test_chunks_is_none_when_no_counts_are_supplied(vault: Path) -> None:
    """None, never 0 — a cold index reporting 0 would read as "indexed, empty"."""
    assert all(source.chunks is None for source in list_sources(vault))


def test_chunks_comes_from_the_supplied_counts(vault: Path) -> None:
    counts = {"00-Inbox/files/lecture.pdf": 7}
    by_path = {source.path: source for source in list_sources(vault, chunk_counts=counts)}

    assert by_path["00-Inbox/files/lecture.pdf"].chunks == 7
    # Absent from the map means "the index has nothing for it", which stays
    # None rather than 0 so the UI can hide the count instead of asserting one.
    assert by_path["00-Inbox/files/titled.md"].chunks is None


def test_sorted_newest_first(vault: Path) -> None:
    sources = list_sources(vault)

    assert [source.modified for source in sources] == sorted(
        (source.modified for source in sources), reverse=True
    )


def test_a_missing_folder_is_empty_not_an_error(vault: Path) -> None:
    assert list_sources(vault, folder="15-Courses/NOPE") == []


def test_a_folder_outside_the_vault_is_refused(vault: Path) -> None:
    """A caller-supplied folder is untrusted input; `..` must not escape."""
    assert list_sources(vault, folder="../elsewhere") == []


def test_honours_a_custom_taxonomy(vault: Path) -> None:
    """Folder names are configurable; nothing here may hardcode `99-Private`."""
    (vault / "Vault-Private").mkdir()
    (vault / "Vault-Private" / "secret.md").write_text("# S\n", encoding="utf-8")
    tax = Taxonomy(private="Vault-Private")

    paths = {source.path for source in list_sources(vault, taxonomy=tax)}

    assert "Vault-Private/secret.md" not in paths
