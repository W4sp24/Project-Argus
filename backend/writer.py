"""THE single write path into the vault's user zones (invariant I1).

Every mutation of user notes goes through this module and nothing else.
Before each apply the vault is git-committed (invariant I2) so any write has
a free undo. Study outputs (new files under ``15-Courses/*/study/``) are the
one sanctioned exception and live in ``backend/study/``.

P2 scope: quick capture. P3 adds suggestion application (note edits, task
line changes) behind the approval gate.
"""

from __future__ import annotations

import subprocess
from datetime import date, datetime
from pathlib import Path

INBOX_DIR = "00-Inbox"


class WriterError(RuntimeError):
    """Raised when a vault write cannot be performed safely."""


def _git_snapshot(vault_path: Path, reason: str) -> None:
    """Commit the vault as it is now, so the next write is undoable (I2)."""
    if not (vault_path / ".git").is_dir():
        raise WriterError(
            f"vault {vault_path} is not a git repository — run `git init` there first (I2)"
        )
    subprocess.run(
        ["git", "add", "-A"], cwd=vault_path, capture_output=True, text=True, check=False
    )
    # Commit is a no-op (rc=1) when the tree is clean; that's fine.
    subprocess.run(
        ["git", "commit", "-m", f"friday: pre-apply snapshot ({reason})"],
        cwd=vault_path,
        capture_output=True,
        text=True,
        check=False,
    )


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
