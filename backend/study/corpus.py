"""Course discovery and per-course retrieval corpus."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import frontmatter
from pydantic import BaseModel

from backend.rag.index import VaultIndex

COURSES_DIR = "15-Courses"


class CourseInfo(BaseModel):
    """One course folder under 15-Courses/."""

    code: str
    title: str
    path: str
    materials: int
    notes: int


def courses(vault_path: Path) -> list[CourseInfo]:
    """All courses that have a course.md hub note."""
    root = vault_path / COURSES_DIR
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
                path=f"{COURSES_DIR}/{course_dir.name}/course.md",
                materials=sum(1 for _ in (course_dir / "materials").glob("*") if _.is_file())
                if (course_dir / "materials").is_dir()
                else 0,
                notes=sum(1 for _ in (course_dir / "notes").glob("*.md"))
                if (course_dir / "notes").is_dir()
                else 0,
            )
        )
    return found


def course_corpus(index: VaultIndex, course: str) -> list[dict[str, Any]]:
    """Every indexed chunk belonging to one course."""
    return [chunk for chunk in index.all_chunks() if chunk["meta"].get("course") == course]
