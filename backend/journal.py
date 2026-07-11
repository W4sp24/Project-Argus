"""Read-only access to the vault's dev journal (``90-Meta/``).

The journal zone is dev-owned (invariant D1): Claude Code writes it, FRIDAY only
reads it. Every function here enforces the privacy contract (D2): notes tagged
``no-ai`` are invisible, and path input can never escape ``90-Meta/``.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import frontmatter
from pydantic import BaseModel

JOURNAL_DIR = "90-Meta"
NO_AI_TAG = "no-ai"
SESSION_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")
BRANCH_RE = re.compile(r"\*\*branch:\*\*\s*`([^`]+)`")
FILES_RE = re.compile(r"\*\*files changed:\*\*\s*(\d+)")
NARRATIVE_RE = re.compile(r"^## Narrative", re.MULTILINE)


class JournalPathError(ValueError):
    """Raised when a requested note path escapes the journal zone."""


class JournalProject(BaseModel):
    """One project context note under ``90-Meta/projects/``."""

    slug: str
    title: str
    updated: str
    sessions: int
    open_threads: int
    path: str


class JournalSession(BaseModel):
    """One session note under ``90-Meta/sessions/<year>/``."""

    date: str
    project: str
    branch: str | None
    files: int
    has_narrative: bool
    path: str


class JournalNote(BaseModel):
    """Rendered note payload with its Obsidian deep link."""

    path: str
    markdown: str
    obsidian_uri: str


def _load_visible(file_path: Path) -> frontmatter.Post | None:
    """Parse a note, returning None when it is private (D2) or unreadable."""
    try:
        post = frontmatter.load(file_path)
    except Exception:
        return None
    tags = post.metadata.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]
    if NO_AI_TAG in [str(tag).strip().lstrip("#") for tag in tags]:
        return None
    if f"#{NO_AI_TAG}" in post.content:
        return None
    return post


def _title_of(post: frontmatter.Post, fallback: str) -> str:
    title = post.metadata.get("title")
    if isinstance(title, str) and title.strip():
        return title.strip()
    for line in post.content.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def _iter_session_files(vault_path: Path) -> list[tuple[Path, str, str]]:
    """Yield ``(file, date, project)`` for every well-named session note."""
    sessions_root = vault_path / JOURNAL_DIR / "sessions"
    results: list[tuple[Path, str, str]] = []
    if not sessions_root.is_dir():
        return results
    for file_path in sorted(sessions_root.rglob("*.md")):
        match = SESSION_FILE_RE.match(file_path.name)
        if match:
            results.append((file_path, match.group(1), match.group(2)))
    return results


def list_projects(vault_path: Path) -> list[JournalProject]:
    """All visible project notes, most recently updated first."""
    projects_root = vault_path / JOURNAL_DIR / "projects"
    session_counts: dict[str, int] = {}
    for file_path, _, project in _iter_session_files(vault_path):
        if _load_visible(file_path) is None:  # D2: private sessions don't even count
            continue
        session_counts[project] = session_counts.get(project, 0) + 1

    projects: list[JournalProject] = []
    if not projects_root.is_dir():
        return projects
    for file_path in sorted(projects_root.glob("*.md")):
        post = _load_visible(file_path)
        if post is None:
            continue
        slug = file_path.stem
        projects.append(
            JournalProject(
                slug=slug,
                title=_title_of(post, slug),
                updated=datetime.fromtimestamp(file_path.stat().st_mtime, tz=UTC).isoformat(),
                sessions=session_counts.get(slug, 0),
                open_threads=post.content.count("- [ ]"),
                path=file_path.relative_to(vault_path).as_posix(),
            )
        )
    projects.sort(key=lambda project: project.updated, reverse=True)
    return projects


def list_sessions(vault_path: Path, project: str | None = None) -> list[JournalSession]:
    """All visible session notes, newest first, optionally scoped to one project."""
    sessions: list[JournalSession] = []
    for file_path, date, slug in _iter_session_files(vault_path):
        if project and slug != project:
            continue
        post = _load_visible(file_path)
        if post is None:
            continue
        branch_matches = BRANCH_RE.findall(post.content)
        files_matches = FILES_RE.findall(post.content)
        sessions.append(
            JournalSession(
                date=date,
                project=slug,
                branch=branch_matches[-1] if branch_matches else None,
                files=int(files_matches[-1]) if files_matches else 0,
                has_narrative=bool(NARRATIVE_RE.search(post.content)),
                path=file_path.relative_to(vault_path).as_posix(),
            )
        )
    sessions.sort(key=lambda session: (session.date, session.path), reverse=True)
    return sessions


def resolve_journal_path(vault_path: Path, rel_path: str) -> Path:
    """Resolve ``rel_path`` strictly inside the journal zone or raise."""
    candidate = Path(rel_path)
    if candidate.is_absolute():
        raise JournalPathError("absolute paths are not allowed")
    resolved = (vault_path / candidate).resolve()
    journal_root = (vault_path / JOURNAL_DIR).resolve()
    if journal_root != resolved and journal_root not in resolved.parents:
        raise JournalPathError(f"path must stay inside {JOURNAL_DIR}/")
    return resolved


def read_note(vault_path: Path, rel_path: str) -> JournalNote | None:
    """One journal note as raw markdown, or None if missing/private."""
    resolved = resolve_journal_path(vault_path, rel_path)
    if not resolved.is_file() or resolved.suffix != ".md":
        return None
    if _load_visible(resolved) is None:
        return None
    relative = resolved.relative_to(vault_path).as_posix()
    uri = f"obsidian://open?vault={quote(vault_path.name)}&file={quote(relative)}"
    return JournalNote(
        path=relative,
        markdown=resolved.read_text(encoding="utf-8"),
        obsidian_uri=uri,
    )
