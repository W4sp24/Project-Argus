"""Course discovery and per-course retrieval corpus."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.rag.index import VaultIndex

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


def course_corpus(index: VaultIndex, course: str) -> list[dict[str, Any]]:
    """Every indexed chunk belonging to one course."""
    return [chunk for chunk in index.all_chunks() if chunk["meta"].get("course") == course]


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
    index: VaultIndex | None = None,
) -> list[CourseSourceInfo]:
    """Every real file under one course's materials/notes/study zones.

    Unlike :func:`backend.vault.notes.list_notes` (markdown only), this walks
    the actual filesystem, so PDF/PPTX/DOCX materials — invisible to
    ``GET /api/notes`` — show up too. That gap was the reported "uploaded
    files are invisible" bug: a course could have real, indexed materials and
    the SOURCES rail (built on ``useNotes()``) would still show nothing.
    """
    tax = taxonomy or active_taxonomy()
    zones = {
        "materials": tax.course_materials(code),
        "notes": tax.course_notes(code),
        "study": tax.course_study(code),
    }

    chunk_counts: dict[str, int] | None = None
    if index is not None:
        chunk_counts = {}
        for chunk in index.all_chunks():
            chunk_path = chunk["meta"].get("path")
            if chunk_path:
                chunk_counts[chunk_path] = chunk_counts.get(chunk_path, 0) + 1

    found: list[CourseSourceInfo] = []
    for zone, rel_dir in zones.items():
        zone_dir = vault_path / rel_dir
        if not zone_dir.is_dir():
            continue
        for file_path in sorted(zone_dir.iterdir()):
            if not file_path.is_file():
                continue
            rel = file_path.relative_to(vault_path).as_posix()
            title = file_path.stem
            if file_path.suffix.lower() == ".md":
                try:
                    post = frontmatter.load(file_path)
                    fm_title = post.metadata.get("title")
                    if isinstance(fm_title, str) and fm_title.strip():
                        title = fm_title.strip()
                except Exception:
                    pass
            found.append(
                CourseSourceInfo(
                    path=rel,
                    title=title,
                    zone=zone,
                    kind=file_path.suffix.lstrip(".").upper() or "FILE",
                    modified=datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC).isoformat(),
                    chunks=chunk_counts.get(rel) if chunk_counts is not None else None,
                )
            )
    found.sort(key=lambda item: item.modified, reverse=True)
    return found
