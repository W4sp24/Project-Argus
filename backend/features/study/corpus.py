"""Course discovery and per-course retrieval corpus."""

from __future__ import annotations

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
