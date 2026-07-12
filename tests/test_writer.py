"""Tests for the single-writer vault path (I1/I2)."""

import re
import subprocess
from pathlib import Path

import pytest

from backend import writer
from backend.writer import (
    WriterConflict,
    WriterError,
    WriterForbidden,
    WriterMissing,
    append_capture,
    guard_user_path,
)

BACKEND = Path(__file__).resolve().parent.parent / "backend"


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=vault, capture_output=True, text=True, check=False
    ).stdout


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Welcome.md").write_text("# Hi\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, check=True)
    return root


def test_capture_appends_task_line(vault: Path) -> None:
    rel = append_capture(vault, "  buy   milk  ")

    note = vault / rel
    assert note.is_file()
    content = note.read_text(encoding="utf-8")
    assert "- [ ] buy milk ➕" in content
    assert content.startswith("---"), "capture note needs frontmatter"

    append_capture(vault, "second thought")
    assert (vault / rel).read_text(encoding="utf-8").count("- [ ]") == 2


def test_capture_snapshots_vault_before_writing(vault: Path) -> None:
    (vault / "dirty.md").write_text("uncommitted\n", encoding="utf-8")
    before = _git(vault, "log", "--oneline").count("\n")

    append_capture(vault, "snapshot me")

    log = _git(vault, "log", "--oneline")
    assert log.count("\n") == before + 1, "I2 violation: no pre-apply commit"
    assert "pre-apply snapshot" in log


def test_capture_requires_git_vault(tmp_path: Path) -> None:
    bare = tmp_path / "no-git"
    bare.mkdir()
    with pytest.raises(WriterError):
        append_capture(bare, "hello")


WRITE_CALL_RE = re.compile(r"\.write_text\(|\.writelines\(|open\([^)]*[\"'][wa][\"']")
# cli.py is the vault *installer* (creates the template before any user data
# exists); study/ may create new files under 15-Courses/*/study/ (I1 exemption).
EXEMPT = {"writer.py", "cli.py"}


def test_single_writer_source_proof() -> None:
    """I1 grep proof: only writer.py combines inbox references with write calls."""
    offenders: list[str] = []
    for module in BACKEND.rglob("*.py"):
        if module.name in EXEMPT or "study" in module.parts:
            continue
        text = module.read_text(encoding="utf-8")
        if "00-Inbox" in text and WRITE_CALL_RE.search(text):
            offenders.append(module.name)
    assert not offenders, f"I1 violation: {offenders} write near the inbox target"


# --- Task line operations (P5) ---


def test_guard_rejects_private_meta_and_traversal(vault: Path):
    for bad in ("99-Private/x.md", "90-Meta/sessions/x.md", "../escape.md", "C:/abs.md"):
        with pytest.raises(WriterForbidden):
            guard_user_path(vault, bad)


def test_toggle_task_line_checks_and_stamps_done_date(vault: Path):
    note = vault / "20-Projects" / "p.md"
    note.parent.mkdir()
    note.write_text("# P\n\n- [ ] ship it 📅 2026-07-20\n", encoding="utf-8")
    new_line = writer.toggle_task_line(vault, "20-Projects/p.md", 3, "- [ ] ship it 📅 2026-07-20")
    assert new_line.startswith("- [x] ship it")
    assert "✅" in new_line
    assert new_line in note.read_text(encoding="utf-8")
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert "argus: pre-apply snapshot" in log


def test_toggle_task_line_unchecks_and_strips_done_date(vault: Path):
    note = vault / "20-Projects" / "p.md"
    note.parent.mkdir()
    note.write_text("- [x] done thing ✅ 2026-07-12\n", encoding="utf-8")
    old_line = "- [x] done thing ✅ 2026-07-12"
    new_line = writer.toggle_task_line(vault, "20-Projects/p.md", 1, old_line)
    assert new_line == "- [ ] done thing"
    assert "✅" not in note.read_text(encoding="utf-8")


def test_task_line_drift_raises_conflict_and_leaves_file(vault: Path):
    note = vault / "20-Projects" / "p.md"
    note.parent.mkdir()
    note.write_text("- [ ] real line\n", encoding="utf-8")
    with pytest.raises(WriterConflict):
        writer.update_task_line(vault, "20-Projects/p.md", 1, "- [ ] stale line", "- [ ] new")
    assert note.read_text(encoding="utf-8") == "- [ ] real line\n"


def test_delete_task_line_removes_line(vault: Path):
    note = vault / "20-Projects" / "p.md"
    note.parent.mkdir()
    note.write_text("- [ ] keep\n- [ ] drop\n", encoding="utf-8")
    writer.delete_task_line(vault, "20-Projects/p.md", 2, "- [ ] drop")
    assert note.read_text(encoding="utf-8") == "- [ ] keep\n"


def test_task_ops_on_missing_file_raise_missing(vault: Path):
    with pytest.raises(WriterMissing):
        writer.toggle_task_line(vault, "20-Projects/nope.md", 1, "- [ ] x")
