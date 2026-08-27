"""Course discovery and per-course retrieval corpus."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.rag.index import VaultIndex
from backend.vault.sources import list_sources

# Deprecated for 0.3 — bound to Taxonomy()'s default; prefer
# settings.taxonomy.courses / active_taxonomy().courses.
COURSES_DIR = Taxonomy().courses


class CourseInfo(BaseModel):
    """One course folder under the taxonomy's courses dir."""

    code: str
    title: str
    path: str
    materials: int
    notes: int
    # Taxonomy-derived write targets the frontend must not hardcode (a
    # literal `15-Courses/<code>` here would reintroduce the bug the
    # configurable-taxonomy refactor fixed — see backend.core.taxonomy).
    # Uploading to `materials_path` is what makes `materials` above move off
    # zero; a caller that targets the course root instead saves the file
    # somewhere `materials` never counts.
    materials_path: str
    notes_path: str


def courses(vault_path: Path, *, taxonomy: Taxonomy | None = None) -> list[CourseInfo]:
    """All courses that have a course.md hub note."""
    tax = taxonomy or active_taxonomy()
    root = vault_path / tax.courses
    found: list[CourseInfo] = []
    if not root.is_dir():
        return found
    for course_dir in sorted(root.iterdir()):
        hub = course_dir / "course.md"
        if not hub.is_file():
            continue
        try:
            post = frontmatter.load(hub)
            title = str(post.metadata.get("title") or course_dir.name)
        except Exception:
            title = course_dir.name
        found.append(
            CourseInfo(
                code=course_dir.name,
                title=title,
                path=f"{tax.courses}/{course_dir.name}/course.md",
                materials=sum(1 for _ in (course_dir / "materials").glob("*") if _.is_file())
                if (course_dir / "materials").is_dir()
                else 0,
                notes=sum(1 for _ in (course_dir / "notes").glob("*.md"))
                if (course_dir / "notes").is_dir()
                else 0,
                materials_path=tax.course_materials(course_dir.name),
                notes_path=tax.course_notes(course_dir.name),
            )
        )
    return found


def course_corpus(
    index: VaultIndex, course: str, paths: list[str] | None = None
) -> list[dict[str, Any]]:
    """Every indexed chunk belonging to one course.

    ``paths``, when given, narrows that to the files the user ticked in the
    Course Hub's SOURCES rail, so "make a guide from just these three
    lectures" means what it says. As in
    :func:`backend.rag.retrieve.retrieve_result`, ``None`` and ``[]`` are
    different: no restriction versus nothing selected. The callers turn an
    empty corpus into a ``StudyError`` either way, which is the honest answer
    to "generate from nothing".
    """
    selected = None if paths is None else frozenset(paths)
    return [
        chunk
        for chunk in index.all_chunks()
        if chunk["meta"].get("course") == course
        and (selected is None or chunk["meta"].get("path") in selected)
    ]


class CourseSourceInfo(BaseModel):
    """One real file under a course's materials/, notes/, or study/ zone."""

    path: str
    title: str
    zone: str  # "materials" | "notes" | "study"
    kind: str  # uppercased file extension, e.g. "PDF", "MD", "PPTX"
    modified: str
    # None (not 0) when no index was supplied — a still-cold index reporting
    # "0 chunks" would be indistinguishable from "genuinely not indexed yet".
    chunks: int | None = None


def course_sources(
    vault_path: Path,
    code: str,
    *,
    taxonomy: Taxonomy | None = None,
    chunk_counts: dict[str, int] | None = None,
) -> list[CourseSourceInfo]:
    """Every real file under one course's materials/notes/study zones.

    A thin, zone-stamping wrapper over :func:`backend.vault.sources.list_sources`
    -- the same filesystem walk, restricted to three folders. It stays a
    separate function because ``zone`` is meaningful here and nowhere else:
    the Course Hub rail switches on it, so it cannot live on the generic
    ``SourceInfo``.

    Each zone is walked non-recursively, as it always has been, so a file in
    ``materials/week1/`` is still not listed.

    ``chunk_counts`` is a plain ``{rel_path: chunks}`` map (from
    :meth:`backend.rag.index.VaultIndex.chunk_counts`) rather than an index,
    so this module no longer reaches into chromadb -- and no longer pays
    ``all_chunks()``'s whole-vault *document* fetch just to take a length.
    """
    tax = taxonomy or active_taxonomy()
    zones = {
        "materials": tax.course_materials(code),
        "notes": tax.course_notes(code),
        "study": tax.course_study(code),
    }

    found: list[CourseSourceInfo] = []
    for zone, rel_dir in zones.items():
        for source in list_sources(
            vault_path,
            taxonomy=tax,
            chunk_counts=chunk_counts,
            folder=rel_dir,
            recursive=False,
        ):
            found.append(
                CourseSourceInfo(
                    path=source.path,
                    title=source.title,
                    zone=zone,
                    kind=source.kind,
                    modified=source.modified,
                    chunks=source.chunks,
                )
            )
    found.sort(key=lambda item: item.modified, reverse=True)
    return found
