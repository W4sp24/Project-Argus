"""One lister for every real file in the vault (the RAG corpus, as a list).

Two listers already existed and neither answers "what is actually in my
index?". :func:`backend.vault.notes.list_notes` globs ``*.md``, so an
uploaded PDF -- the whole point of ingesting -- can never appear in anything
built on it; that gap is what made the Course Hub SOURCES rail show nothing
for a course full of real, indexed materials.
:func:`backend.features.study.corpus.course_sources` fixed that by walking the
filesystem, but only inside one course's three named zones.

This module is that walk, generalised: any folder, optionally recursive,
optionally restricted to a suffix set, with per-file chunk counts passed in
rather than computed here.

Privacy: this is an outward-facing read, so it uses
:func:`backend.vault.privacy.is_visible` -- **both** halves of I3 -- and not
``is_indexable``, which checks directories only. See privacy.py's module
docstring, which names that exact trap. The honest limit is that the ``#no-ai``
half needs frontmatter to read: for a PDF or a DOCX there is none, so only the
directory half can apply to them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import frontmatter
from pydantic import BaseModel

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.vault.privacy import is_no_ai, is_private_path


class SourceInfo(BaseModel):
    """One real file in the vault, indexed or not.

    Deliberately carries no ``zone``: that field is meaningful for a course's
    materials/notes/study split and meaningless everywhere else, so it stays
    on :class:`~backend.features.study.corpus.CourseSourceInfo`, which is
    built from these.
    """

    path: str
    title: str
    #: The parent directory, vault-relative; ``""`` for a file at the root.
    #: Mirrors :attr:`backend.vault.notes.NoteInfo.folder`.
    folder: str
    #: Uppercased extension, e.g. ``PDF`` / ``MD``; ``FILE`` when there is none.
    kind: str
    modified: str
    size: int
    #: Chunks this file has in the live index, or ``None`` when that is
    #: unknown -- either because no counts were supplied, or because the index
    #: holds nothing for it. Never ``0``: a still-cold index reporting zero
    #: would be indistinguishable from a file that genuinely produced no
    #: chunks, and the UI hides the count rather than asserting one.
    chunks: int | None = None


def _title_of(file_path: Path) -> str:
    """Frontmatter ``title`` when the file is markdown and has one; else the stem."""
    if file_path.suffix.lower() == ".md":
        try:
            title = frontmatter.load(file_path).metadata.get("title")
        except Exception:
            return file_path.stem
        if isinstance(title, str) and title.strip():
            return title.strip()
    return file_path.stem


def _is_visible_file(file_path: Path, rel_path: str, taxonomy: Taxonomy) -> bool:
    """The full I3 check, as far as the file type allows it to be checked."""
    if is_private_path(rel_path, taxonomy=taxonomy):
        return False
    if file_path.suffix.lower() != ".md":
        # No frontmatter to read, so the tag half cannot apply. Binary
        # extraction is where a `#no-ai` inside a PDF gets caught (see
        # backend/features/ingest/pipeline.py); a listing cannot.
        return True
    try:
        return not is_no_ai(frontmatter.load(file_path))
    except Exception:
        # An unparseable note is treated as private rather than public: the
        # same direction backend/agent/runtime.py errs in for read_note.
        return False


def list_sources(
    vault_path: Path,
    *,
    taxonomy: Taxonomy | None = None,
    chunk_counts: dict[str, int] | None = None,
    folder: str | None = None,
    recursive: bool = True,
    suffixes: frozenset[str] | None = None,
) -> list[SourceInfo]:
    """Every visible file under ``folder`` (or the whole vault), newest first.

    ``chunk_counts`` is a plain ``{rel_path: chunks}`` map -- normally
    :meth:`backend.rag.index.VaultIndex.chunk_counts` -- rather than an index,
    so this function never touches chromadb and stays trivially testable.

    ``suffixes`` restricts the walk (pass ``Taxonomy.indexable_suffixes`` to
    list only what RAG can actually read); omitted, every file is listed.
    """
    tax = taxonomy or active_taxonomy()
    root = vault_path
    if folder:
        candidate = (vault_path / folder).resolve()
        # `folder` is caller-supplied (a query string, ultimately), so it is
        # untrusted: `..` must not walk out of the vault.
        if not candidate.is_relative_to(vault_path.resolve()):
            return []
        root = candidate
    if not root.is_dir():
        return []

    found: list[SourceInfo] = []
    for file_path in root.rglob("*") if recursive else root.iterdir():
        if not file_path.is_file():
            continue
        if suffixes is not None and file_path.suffix.lower() not in suffixes:
            continue
        rel = file_path.relative_to(vault_path).as_posix()
        if not _is_visible_file(file_path, rel, tax):
            continue
        stat = file_path.stat()
        parent = file_path.parent.relative_to(vault_path).as_posix()
        found.append(
            SourceInfo(
                path=rel,
                title=_title_of(file_path),
                folder="" if parent == "." else parent,
                kind=file_path.suffix.lstrip(".").upper() or "FILE",
                modified=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
                size=stat.st_size,
                chunks=(chunk_counts or {}).get(rel) if chunk_counts is not None else None,
            )
        )
    found.sort(key=lambda item: item.modified, reverse=True)
    return found
