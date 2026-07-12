"""THE single write path into the vault's user zones (invariant I1).

Every mutation of user notes goes through this module and nothing else.
Before each apply the vault is git-committed (invariant I2) so any write has
a free undo. Study outputs (new files under ``15-Courses/*/study/``) are the
one sanctioned exception and live in ``backend/study/``.

P2 scope: quick capture. P3 adds suggestion application (note edits, task
line changes) behind the approval gate.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path

from backend import suggestions as suggestion_queue
from backend.rag.paths import EXCLUDED_TOP_DIRS
from backend.suggestions import Suggestion

INBOX_DIR = "00-Inbox"
DAILY_DIR = "10-Daily"
ARGUS_LOG_HEADING = "## Argus log"


class WriterError(RuntimeError):
    """Raised when a vault write cannot be performed safely."""


class WriterForbidden(WriterError):  # noqa: N818
    """Write refused: path is outside the user-editable zones (I3/D1)."""


class WriterMissing(WriterError):  # noqa: N818
    """Write refused: target file does not exist."""


class WriterConflict(WriterError):  # noqa: N818
    """Write refused: content drifted since the client read it."""


def _git_snapshot(vault_path: Path, reason: str) -> None:
    """Commit the vault as it is now, so the next write is undoable (I2)."""
    if not (vault_path / ".git").is_dir():
        raise WriterError(
            f"vault {vault_path} is not a git repository — run `git init` there first (I2)"
        )
    subprocess.run(
        ["git", "add", "-A"], cwd=vault_path, capture_output=True, text=True, check=False
    )
    # --allow-empty: the snapshot marks the pre-apply point even on a clean tree.
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", f"argus: pre-apply snapshot ({reason})"],
        cwd=vault_path,
        capture_output=True,
        text=True,
        check=False,
    )


def guard_user_path(vault_path: Path, rel_path: str) -> Path:
    """Resolve a vault-relative path for user CRUD; refuse protected zones."""
    candidate = Path(rel_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise WriterForbidden(f"path {rel_path!r} is not vault-relative")
    if candidate.parts and candidate.parts[0] in EXCLUDED_TOP_DIRS:
        raise WriterForbidden(f"{candidate.parts[0]}/ is protected and cannot be edited")
    resolved = (vault_path / candidate).resolve()
    if vault_path.resolve() not in resolved.parents:
        raise WriterForbidden(f"path {rel_path!r} escapes the vault")
    return resolved


def _checked_line(note: Path, rel_path: str, line_no: int, old_line: str) -> list[str]:
    if not note.is_file():
        raise WriterMissing(f"{rel_path} does not exist")
    lines = note.read_text(encoding="utf-8").splitlines()
    if line_no < 1 or line_no > len(lines) or lines[line_no - 1].strip() != old_line.strip():
        raise WriterConflict(f"{rel_path}:{line_no} has changed since you loaded it — refresh")
    return lines


DONE_STAMP_RE = re.compile(r"\s*✅\s*\d{4}-\d{2}-\d{2}")


def toggle_task_line(vault_path: Path, rel_path: str, line_no: int, old_line: str) -> str:
    """Check/uncheck one task checkbox; stamps/strips the ✅ done date."""
    note = guard_user_path(vault_path, rel_path)
    lines = _checked_line(note, rel_path, line_no, old_line)
    line = lines[line_no - 1]
    if "[ ]" in line:
        new_line = (
            line.replace("[ ]", "[x]", 1).rstrip() + f" ✅ {date.today().isoformat()}"
        )
    elif "[x]" in line or "[X]" in line:
        line_normalized = line.replace("[X]", "[x]").replace("[x]", "[ ]", 1)
        new_line = DONE_STAMP_RE.sub("", line_normalized).rstrip()
    else:
        raise WriterConflict(f"{rel_path}:{line_no} is not a checkbox task")
    _git_snapshot(vault_path, f"toggle task {rel_path}:{line_no}")
    lines[line_no - 1] = new_line
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _argus_log(vault_path, f"toggled task in {rel_path}:{line_no}")
    return new_line


def update_task_line(
    vault_path: Path, rel_path: str, line_no: int, old_line: str, new_line: str
) -> str:
    """Replace one task line verbatim (user-initiated edit)."""
    note = guard_user_path(vault_path, rel_path)
    lines = _checked_line(note, rel_path, line_no, old_line)
    _git_snapshot(vault_path, f"edit task {rel_path}:{line_no}")
    lines[line_no - 1] = new_line
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _argus_log(vault_path, f"edited task in {rel_path}:{line_no}")
    return new_line


def delete_task_line(vault_path: Path, rel_path: str, line_no: int, old_line: str) -> None:
    """Delete one task line (user-initiated)."""
    note = guard_user_path(vault_path, rel_path)
    lines = _checked_line(note, rel_path, line_no, old_line)
    _git_snapshot(vault_path, f"delete task {rel_path}:{line_no}")
    del lines[line_no - 1]
    note.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    _argus_log(vault_path, f"deleted task in {rel_path}:{line_no}")


def append_capture(vault_path: Path, text: str) -> str:
    """Append one captured thought to today's inbox note; returns its vault path."""
    cleaned = " ".join(text.split())
    if not cleaned:
        raise WriterError("nothing to capture")

    _git_snapshot(vault_path, "quick capture")

    inbox = vault_path / INBOX_DIR
    inbox.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    note = inbox / f"capture-{today}.md"
    if not note.exists():
        note.write_text(
            f'---\ntype: capture\ncreated: "{today}"\ntags: [inbox]\n---\n\n'
            f"# Captured — {today}\n\n",
            encoding="utf-8",
        )
    stamp = datetime.now().strftime("%H:%M")
    with note.open("a", encoding="utf-8") as handle:
        handle.write(f"- [ ] {cleaned} ➕ {today} <!-- {stamp} -->\n")
    return f"{INBOX_DIR}/capture-{today}.md"


# --- Morning briefing (P4) ---------------------------------------------------

BRIEFING_HEADING = "## Briefing"


def write_briefing(vault_path: Path, markdown: str) -> str:
    """Write/replace the ``## Briefing`` section in today's daily note.

    Re-running replaces the existing section (the 07:00 job is idempotent).
    Returns the note's vault-relative path.
    """
    _git_snapshot(vault_path, "morning briefing")

    daily = vault_path / DAILY_DIR
    daily.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    note = daily / f"{today}.md"
    if not note.exists():
        note.write_text(f"# {today}\n", encoding="utf-8")

    lines = note.read_text(encoding="utf-8").splitlines()
    section = [BRIEFING_HEADING, "", markdown.rstrip("\n"), ""]

    start = next((i for i, line in enumerate(lines) if line.strip() == BRIEFING_HEADING), None)
    if start is not None:
        end = next(
            (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines)
        )
        lines[start:end] = section
    else:
        insert_at = 1 if lines and lines[0].startswith("# ") else 0
        lines[insert_at:insert_at] = [""] + section if insert_at else section

    note.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
    return f"{DAILY_DIR}/{today}.md"


# --- Suggestion application (P3): the approval gate's one executor ----------


def _argus_log(vault_path: Path, line: str) -> None:
    """Append an audit line under '## Argus log' in today's daily note."""
    daily = vault_path / DAILY_DIR
    daily.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    note = daily / f"{today}.md"
    if not note.exists():
        note.write_text(f"# {today}\n", encoding="utf-8")
    content = note.read_text(encoding="utf-8")
    stamp = datetime.now().strftime("%H:%M")
    entry = f"- {stamp} — {line}"
    if ARGUS_LOG_HEADING in content:
        content = content.rstrip("\n") + f"\n{entry}\n"
    else:
        content = content.rstrip("\n") + f"\n\n{ARGUS_LOG_HEADING}\n\n{entry}\n"
    note.write_text(content, encoding="utf-8")


def _apply_task_edit(vault_path: Path, payload: dict) -> str:
    rel_path, line_no = payload["path"], int(payload["line"])
    old_line, new_line = payload["old_line"], payload["new_line"]
    note = vault_path / rel_path
    if not note.is_file():
        raise WriterError(f"{rel_path} no longer exists")
    lines = note.read_text(encoding="utf-8").splitlines()
    if line_no < 1 or line_no > len(lines) or lines[line_no - 1].strip() != old_line.strip():
        raise WriterError(f"{rel_path}:{line_no} has drifted — refresh and re-propose")
    lines[line_no - 1] = new_line
    note.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f"task edit in {rel_path}:{line_no}"


def _apply_note_diff(vault_path: Path, payload: dict) -> str:
    """Apply a unified diff; fail clean (file untouched) on any drift."""
    rel_path, diff = payload["path"], payload["diff"]
    note = vault_path / rel_path
    if not note.is_file():
        raise WriterError(f"{rel_path} no longer exists")
    original = note.read_text(encoding="utf-8").splitlines()

    result: list[str] = []
    cursor = 0
    lines = [line for line in diff.splitlines() if not line.startswith(("---", "+++"))]
    index = 0
    saw_hunk = False
    while index < len(lines):
        header = lines[index]
        if not header.startswith("@@"):
            index += 1
            continue
        saw_hunk = True
        try:
            old_start = int(header.split("-")[1].split(",")[0].split(" ")[0])
        except (IndexError, ValueError) as exc:
            raise WriterError(f"malformed hunk header: {header}") from exc
        result.extend(original[cursor : old_start - 1])
        cursor = old_start - 1
        index += 1
        while index < len(lines) and not lines[index].startswith("@@"):
            body = lines[index]
            tag, text = (body[0], body[1:]) if body else (" ", "")
            if tag == " ":
                if cursor >= len(original) or original[cursor] != text:
                    raise WriterError(f"{rel_path} has drifted — refresh and re-propose")
                result.append(text)
                cursor += 1
            elif tag == "-":
                if cursor >= len(original) or original[cursor] != text:
                    raise WriterError(f"{rel_path} has drifted — refresh and re-propose")
                cursor += 1
            elif tag == "+":
                result.append(text)
            index += 1
    if not saw_hunk:
        raise WriterError("diff contains no hunks")
    result.extend(original[cursor:])
    note.write_text("\n".join(result) + "\n", encoding="utf-8")
    return f"note edit in {rel_path}"


def apply_suggestion(
    conn: sqlite3.Connection,
    vault_path: Path,
    suggestion_id: int,
    gcal_insert: Callable[[str, str, str], None] | None = None,
) -> Suggestion:
    """Execute one approved suggestion. The ONLY mutation path (I1).

    ``gcal_insert(title, start, end)`` is injectable for tests; the default is
    the real connector (which raises when unconfigured).
    """
    suggestion = suggestion_queue.get(conn, suggestion_id)
    if suggestion is None:
        raise WriterError(f"no suggestion {suggestion_id}")
    if suggestion.status != "pending":
        raise WriterError(f"suggestion {suggestion_id} is already {suggestion.status}")

    _git_snapshot(vault_path, f"apply suggestion #{suggestion_id} ({suggestion.kind})")

    if suggestion.kind == "schedule":
        if gcal_insert is None:
            from backend.connectors.gcal import insert_event as gcal_insert  # noqa: PLR1704
        blocks = suggestion.payload.get("blocks", [])
        if not blocks:
            raise WriterError("schedule suggestion has no blocks")
        try:
            for block in blocks:
                gcal_insert(block["title"], block["start"], block["end"])
        except WriterError:
            raise
        except Exception as exc:  # connector failures must surface, not 500
            raise WriterError(str(exc)) from exc
        summary = f"scheduled {len(blocks)} block(s): " + ", ".join(
            block["title"] for block in blocks
        )
    elif suggestion.kind == "task":
        summary = _apply_task_edit(vault_path, suggestion.payload)
    elif suggestion.kind == "note":
        summary = _apply_note_diff(vault_path, suggestion.payload)
    else:  # pragma: no cover - kind is DB-constrained
        raise WriterError(f"unknown suggestion kind {suggestion.kind}")

    _argus_log(vault_path, f"applied #{suggestion_id}: {summary}")
    suggestion_queue.mark_applied(conn, suggestion_id)
    applied = suggestion_queue.get(conn, suggestion_id)
    assert applied is not None
    return applied
