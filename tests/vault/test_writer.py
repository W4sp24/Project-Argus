"""Tests for the single-writer vault path (I1/I2)."""

import re
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

import backend
from backend.core.taxonomy import Taxonomy
from backend.vault import writer
from backend.vault.writer import (
    WriterConflict,
    WriterError,
    WriterExists,
    WriterForbidden,
    WriterMissing,
    append_capture,
    create_note,
    edit_note,
    guard_user_path,
    save_ingest_file,
)

# Resolved through the package itself, not by walking up from __file__: this
# test has moved once already, and a stale relative path makes the I1 proof
# below glob nothing and pass vacuously.
BACKEND = Path(backend.__file__).resolve().parent


def _git(vault: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=vault, capture_output=True, text=True, check=False
    ).stdout


def _make_vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Welcome.md").write_text("# Hi\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, check=True)
    return root


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    return _make_vault(tmp_path)


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


def test_snapshot_without_the_git_binary_is_a_writer_error(
    vault: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing git *binary* must not escape as a 500.

    ``check=False`` covers git exiting non-zero; it does nothing for git not
    being on PATH at all, which raises FileNotFoundError out of subprocess and
    used to surface as an opaque 500 from every write route.
    """

    def _no_git(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError(2, "The system cannot find the file specified", "git")

    monkeypatch.setattr(subprocess, "run", _no_git)

    with pytest.raises(WriterError, match="git is not on PATH"):
        append_capture(vault, "hello")


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


def test_guard_rejects_case_variants_of_protected_dirs(vault: Path):
    """NTFS case-insensitivity bypass: reject lowercase variants of protected dirs."""
    for bad in ("99-private/x.md", "90-META/sessions/x.md", "99-PRIVATE/y.md"):
        with pytest.raises(WriterForbidden):
            guard_user_path(vault, bad)


def test_guard_protects_a_renamed_private_folder(vault: Path):
    """I3 with a custom taxonomy: a vault that calls its private zone something
    else entirely must still have that zone protected — and the *old* default
    name must no longer be special once it isn't the configured one."""
    custom = Taxonomy(private="Personal", journal="DevNotes")

    with pytest.raises(WriterForbidden):
        guard_user_path(vault, "Personal/diary.md", taxonomy=custom)

    # The old hardcoded name is just an ordinary folder under this taxonomy.
    resolved = guard_user_path(vault, "99-Private/not-special.md", taxonomy=custom)
    assert resolved == (vault / "99-Private" / "not-special.md").resolve()


def test_append_capture_lands_in_a_renamed_inbox(vault: Path):
    custom = Taxonomy(inbox="Capture")

    rel = append_capture(vault, "renamed inbox test", taxonomy=custom)

    assert rel.startswith("Capture/")
    assert (vault / rel).is_file()
    assert not (vault / "00-Inbox").exists(), "must not also write the default inbox"


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


# --- Note update/delete (P5) ---


def test_update_note_cas_applies_and_logs(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "00-Inbox" / "n.md"
    note.parent.mkdir()
    note.write_text("old body\n", encoding="utf-8")
    writer.update_note(vault, "00-Inbox/n.md", "old body\n", "new body\n")
    assert note.read_text(encoding="utf-8") == "new body\n"
    daily = vault / "10-Daily"
    assert any("## Argus log" in p.read_text(encoding="utf-8") for p in daily.glob("*.md"))


def test_update_note_conflict_on_drift(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "00-Inbox" / "n.md"
    note.parent.mkdir()
    note.write_text("actual\n", encoding="utf-8")
    with pytest.raises(WriterConflict):
        writer.update_note(vault, "00-Inbox/n.md", "what the client saw\n", "new\n")
    assert note.read_text(encoding="utf-8") == "actual\n"


def test_delete_note_removes_file_after_snapshot(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "00-Inbox" / "n.md"
    note.parent.mkdir()
    note.write_text("bye\n", encoding="utf-8")
    writer.delete_note(vault, "00-Inbox/n.md")
    assert not note.exists()
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert "argus: pre-apply snapshot (delete note 00-Inbox/n.md)" in log


def test_delete_note_refuses_protected_and_missing(tmp_path):
    vault = _make_vault(tmp_path)
    with pytest.raises(WriterForbidden):
        writer.delete_note(vault, "99-Private/secret.md")
    with pytest.raises(WriterMissing):
        writer.delete_note(vault, "00-Inbox/ghost.md")


# --- Course deletion (fix: sample data survives a course "delete") ----------


def test_delete_course_tree_removes_folder_after_snapshot(tmp_path):
    vault = _make_vault(tmp_path)
    course = vault / "15-Courses" / "CS201"
    (course / "materials").mkdir(parents=True)
    (course / "materials" / "syllabus.pdf").write_text("x", encoding="utf-8")
    (course / "course.md").write_text("---\ntitle: X\n---\n", encoding="utf-8")

    writer.delete_course_tree(vault, "CS201")

    assert not course.exists()
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert "argus: pre-apply snapshot (delete course CS201)" in log


def test_delete_course_tree_refuses_missing_course(tmp_path):
    vault = _make_vault(tmp_path)
    with pytest.raises(WriterMissing):
        writer.delete_course_tree(vault, "CS999")


def test_delete_course_tree_refuses_traversal(tmp_path):
    vault = _make_vault(tmp_path)
    for bad in ("../escape", "..", "15-Courses/CS201", "a/b", "a\\b", ""):
        with pytest.raises(WriterForbidden):
            writer.delete_course_tree(vault, bad)


def test_delete_course_tree_refuses_a_code_outside_the_courses_dir(tmp_path):
    """A code that isn't a direct child of the courses dir must be refused,
    even if it happens to resolve to a real directory elsewhere in the vault."""
    vault = _make_vault(tmp_path)
    (vault / "20-Projects").mkdir()
    with pytest.raises(WriterForbidden):
        writer.delete_course_tree(vault, "../20-Projects")


def test_delete_course_tree_honours_a_renamed_courses_dir(tmp_path):
    vault = _make_vault(tmp_path)
    custom = Taxonomy(courses="Classes")
    course = vault / "Classes" / "CS301"
    course.mkdir(parents=True)
    (course / "course.md").write_text("x", encoding="utf-8")

    writer.delete_course_tree(vault, "CS301", taxonomy=custom)

    assert not course.exists()


# --- Note creation (redesign §13 quick add-note modal) ----------------------


def test_create_note_writes_new_file_and_snapshots(tmp_path):
    vault = _make_vault(tmp_path)
    rel = writer.create_note(vault, "00-Inbox/2026-07-16-idea.md", "# Idea\n\nbody\n")
    assert rel == "00-Inbox/2026-07-16-idea.md"
    note = vault / rel
    assert note.read_text(encoding="utf-8") == "# Idea\n\nbody\n"
    log = subprocess.run(
        ["git", "log", "--oneline"], cwd=vault, capture_output=True, text=True
    ).stdout
    assert "argus: pre-apply snapshot (create note 00-Inbox/2026-07-16-idea.md)" in log


def test_create_note_makes_parent_dirs(tmp_path):
    vault = _make_vault(tmp_path)
    rel = writer.create_note(vault, "00-Inbox/nested/dir/note.md", "hi\n")
    assert (vault / rel).is_file()


def test_create_note_refuses_existing_file(tmp_path):
    vault = _make_vault(tmp_path)
    note = vault / "00-Inbox" / "n.md"
    note.parent.mkdir()
    note.write_text("original\n", encoding="utf-8")
    with pytest.raises(WriterExists):
        writer.create_note(vault, "00-Inbox/n.md", "clobber\n")
    assert note.read_text(encoding="utf-8") == "original\n"


def test_create_note_refuses_protected_zones(tmp_path):
    vault = _make_vault(tmp_path)
    with pytest.raises(WriterForbidden):
        writer.create_note(vault, "99-Private/secret.md", "x\n")


# --- edit_note (the guarded diff path the chat write tools use) ---------------


def _diff(old: str, new: str) -> str:
    return f"@@ -1,1 +1,1 @@\n-{old}\n+{new}\n"


def test_edit_note_applies_a_diff(vault: Path) -> None:
    edit_note(vault, "Welcome.md", _diff("# Hi", "# Hello"))

    assert (vault / "Welcome.md").read_text(encoding="utf-8") == "# Hello\n"


def test_edit_note_snapshots_before_writing(vault: Path) -> None:
    """I2: the snapshot is the undo. _apply_note_diff takes none of its own --
    it relies on its caller -- so a wrapper that forgot would write
    irreversibly."""
    before = _git(vault, "log", "--oneline").count("\n")

    edit_note(vault, "Welcome.md", _diff("# Hi", "# Hello"))

    log = _git(vault, "log", "--oneline")
    assert log.count("\n") == before + 1, "I2 violation: no pre-apply commit"
    assert "pre-apply snapshot" in log


def test_edit_note_refuses_to_escape_the_vault(vault: Path) -> None:
    """I3: _apply_note_diff resolves vault_path / rel_path raw, so without the
    guard this is a write to an arbitrary file on disk."""
    outside = vault.parent / "escaped.md"
    outside.write_text("# Hi\n", encoding="utf-8")

    with pytest.raises(WriterForbidden):
        edit_note(vault, "../escaped.md", _diff("# Hi", "# Owned"))

    assert outside.read_text(encoding="utf-8") == "# Hi\n", "wrote outside the vault"


def test_edit_note_refuses_a_protected_zone(vault: Path) -> None:
    private = vault / Taxonomy().private
    private.mkdir(parents=True)
    (private / "diary.md").write_text("# Hi\n", encoding="utf-8")

    with pytest.raises(WriterForbidden):
        edit_note(vault, f"{Taxonomy().private}/diary.md", _diff("# Hi", "# Leaked"))

    assert (private / "diary.md").read_text(encoding="utf-8") == "# Hi\n"


def test_edit_note_reports_a_missing_note(vault: Path) -> None:
    with pytest.raises(WriterMissing):
        edit_note(vault, "nope.md", _diff("# Hi", "# Hello"))


def test_edit_note_fails_clean_on_drift(vault: Path) -> None:
    """A drifted diff must leave the file untouched, not half-applied."""
    with pytest.raises(WriterError):
        edit_note(vault, "Welcome.md", _diff("# Something else", "# Hello"))

    assert (vault / "Welcome.md").read_text(encoding="utf-8") == "# Hi\n"


# --- batched writes -----------------------------------------------------------
# A batch ingest saves N files in one user action. Snapshotting per file means
# N full-vault `git add -A` scans and N commits for one undo point, and
# `_git_snapshot` runs git with check=False, so two concurrent snapshots race
# on .git/index.lock and the loser fails *silently* -- I2 broken with no error.


def _commit_count(vault: Path) -> int:
    import subprocess

    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


def test_save_ingest_file_snapshots_by_default(vault: Path) -> None:
    """Every existing caller must keep the behaviour it has today."""
    before = _commit_count(vault)
    save_ingest_file(vault, "00-Inbox/files", "a.md", b"# A\n")

    assert _commit_count(vault) == before + 1


def test_save_ingest_file_can_defer_the_snapshot_to_its_caller(vault: Path) -> None:
    """One undo point per batch, taken by the caller before the first file."""
    before = _commit_count(vault)
    for name in ("a.md", "b.md", "c.md"):
        save_ingest_file(vault, "00-Inbox/files", name, b"# X\n", snapshot=False)

    assert _commit_count(vault) == before, "no snapshot should have been taken"
    for name in ("a.md", "b.md", "c.md"):
        assert (vault / "00-Inbox" / "files" / name).is_file(), "the file must still be saved"


def test_save_ingest_file_can_defer_the_daily_note_line(vault: Path) -> None:
    """20 files should leave one line in the daily note, not 20 rewrites."""
    save_ingest_file(vault, "00-Inbox/files", "a.md", b"# A\n", snapshot=False, log=False)
    save_ingest_file(vault, "00-Inbox/files", "b.md", b"# B\n", snapshot=False, log=False)

    daily = list((vault / "10-Daily").glob("*.md"))
    written = "".join(path.read_text(encoding="utf-8") for path in daily)
    assert "ingested file" not in written


def test_create_note_can_defer_the_snapshot_too(vault: Path) -> None:
    """The summary note a batch writes belongs to the batch's undo point."""
    before = _commit_count(vault)
    create_note(vault, "00-Inbox/files/a.summary.md", "# Summary\n", snapshot=False, log=False)

    assert _commit_count(vault) == before
    assert (vault / "00-Inbox" / "files" / "a.summary.md").is_file()


def test_deferring_the_snapshot_still_refuses_a_protected_zone(vault: Path) -> None:
    """Skipping the snapshot must not skip the path guard (I3)."""
    with pytest.raises(WriterForbidden):
        save_ingest_file(vault, "99-Private", "leak.md", b"# No\n", snapshot=False)


def _commit_count(vault) -> int:
    return int(
        subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=vault,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    )


def test_a_batch_delete_takes_one_snapshot_not_one_each(tmp_path):
    """One user action, one undo point. `_git_snapshot` runs git with
    check=False, so two snapshots racing on .git/index.lock lose one of them
    silently -- which is I2 broken with nothing to show for it. Deleting N
    files therefore has to snapshot once, before the first unlink, the same
    way save_ingest_file's snapshot=False does for a batch ingest."""
    vault = _make_vault(tmp_path)
    inbox = vault / "00-Inbox"
    inbox.mkdir()
    for name in ("a.md", "b.md", "c.md"):
        (inbox / name).write_text("bye", encoding="utf-8")
    before = _commit_count(vault)

    writer.snapshot_vault(vault, "delete 3 sources")
    for name in ("a.md", "b.md", "c.md"):
        writer.delete_note(vault, f"00-Inbox/{name}", snapshot=False, log=False)

    assert _commit_count(vault) == before + 1
    assert not any((inbox / name).exists() for name in ("a.md", "b.md", "c.md"))


def test_delete_note_is_not_markdown_specific(tmp_path):
    """It is the single write path for removing a *source*, which is a PDF as
    often as a note -- the guard and the unlink do not care about the suffix."""
    vault = _make_vault(tmp_path)
    (vault / "00-Inbox" / "files").mkdir(parents=True)
    pdf = vault / "00-Inbox" / "files" / "lecture.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really")

    writer.delete_note(vault, "00-Inbox/files/lecture.pdf")

    assert not pdf.exists()


# --- Recurring tasks (P5a) ----------------------------------------------------
# Obsidian Tasks keeps a series alive by inserting the *next* instance above the
# one you just ticked. Argus used to ignore 🔁 entirely, so completing a
# recurring task destroyed the series -- silent data loss in the user's vault.


def _recurring_note(vault: Path, body: str) -> Path:
    note = vault / "20-Projects" / "p.md"
    note.parent.mkdir(exist_ok=True)
    note.write_text(body, encoding="utf-8")
    return note


def test_completing_a_recurring_task_inserts_the_next_instance(vault: Path) -> None:
    old_line = "- [ ] Water plants 🔁 every week 📅 2026-09-06 ⏫ #home"
    note = _recurring_note(vault, "# P\n\n" + old_line + "\n")

    new_line = writer.toggle_task_line(vault, "20-Projects/p.md", 3, old_line)

    lines = note.read_text(encoding="utf-8").splitlines()
    assert lines == [
        "# P",
        "",
        "- [ ] Water plants 🔁 every week 📅 2026-09-13 ⏫ #home",
        new_line,
    ], "exactly one new instance, directly above the completed line"
    assert new_line.startswith("- [x] Water plants") and "✅" in new_line
    assert "✅" not in lines[2], "the next instance must not be born done"


def test_the_next_instance_advances_the_scheduled_date_too(vault: Path) -> None:
    old_line = "- [ ] Standup 🔁 every monday ⏳ 2026-09-07 📅 2026-09-07"
    note = _recurring_note(vault, old_line + "\n")

    writer.toggle_task_line(vault, "20-Projects/p.md", 1, old_line)

    lines = note.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "- [ ] Standup 🔁 every monday ⏳ 2026-09-14 📅 2026-09-14"


def test_an_undated_recurring_task_advances_from_today(vault: Path) -> None:
    """Matching the plugin: with no date to advance, today is the anchor. An
    undated clone would be an identical twin the user ticks again immediately."""
    old_line = "- [ ] Water plants 🔁 every day #home"
    note = _recurring_note(vault, old_line + "\n")

    writer.toggle_task_line(vault, "20-Projects/p.md", 1, old_line)

    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    lines = note.read_text(encoding="utf-8").splitlines()
    assert lines[0] == f"- [ ] Water plants 🔁 every day #home 📅 {tomorrow}"


def test_uncompleting_a_recurring_task_inserts_nothing(vault: Path) -> None:
    old_line = "- [x] Water plants 🔁 every week 📅 2026-09-06 ✅ 2026-09-06"
    note = _recurring_note(vault, old_line + "\n")

    new_line = writer.toggle_task_line(vault, "20-Projects/p.md", 1, old_line)

    assert new_line == "- [ ] Water plants 🔁 every week 📅 2026-09-06"
    assert note.read_text(encoding="utf-8") == new_line + "\n"


def test_an_unrecognised_recurrence_rule_still_toggles(vault: Path) -> None:
    """A task the user cannot tick off is worse than one that does not repeat."""
    old_line = "- [ ] Water plants 🔁 every blue moon 📅 2026-09-06"
    note = _recurring_note(vault, old_line + "\n")

    new_line = writer.toggle_task_line(vault, "20-Projects/p.md", 1, old_line)

    lines = note.read_text(encoding="utf-8").splitlines()
    assert lines == [new_line], "no next instance for a rule we cannot compute"
    assert new_line.startswith("- [x] Water plants") and "✅" in new_line


def test_a_non_recurring_task_is_unaffected(vault: Path) -> None:
    old_line = "- [ ] ship it 📅 2026-07-20"
    note = _recurring_note(vault, old_line + "\n")

    new_line = writer.toggle_task_line(vault, "20-Projects/p.md", 1, old_line)

    assert note.read_text(encoding="utf-8").splitlines() == [new_line]


def test_a_recurring_toggle_takes_exactly_one_snapshot(vault: Path) -> None:
    """I2: two edits in one user action are still one undo point, and
    ``_git_snapshot`` runs git with check=False -- a second one racing on
    .git/index.lock loses silently."""
    old_line = "- [ ] Water plants 🔁 every week 📅 2026-09-06"
    _recurring_note(vault, old_line + "\n")
    before = _commit_count(vault)

    writer.toggle_task_line(vault, "20-Projects/p.md", 1, old_line)

    assert _commit_count(vault) == before + 1


def test_a_recurring_toggle_still_honours_the_cas_guard(vault: Path) -> None:
    """Drift is refused *before* anything is inserted -- a rolled-forward copy
    of the wrong line is a corrupted note, not a failed request."""
    note = _recurring_note(vault, "- [ ] Water plants 🔁 every week 📅 2026-09-06\n")

    with pytest.raises(WriterConflict):
        writer.toggle_task_line(
            vault, "20-Projects/p.md", 1, "- [ ] Water plants 🔁 every week 📅 2026-09-13"
        )

    assert note.read_text(encoding="utf-8") == "- [ ] Water plants 🔁 every week 📅 2026-09-06\n"


def test_the_next_instance_keeps_the_indentation_of_a_subtask(vault: Path) -> None:
    """The insert is a copy of the original line, so a nested task stays nested
    instead of being promoted to the top level of the list."""
    old_line = "    - [ ] Water plants 🔁 every week 📅 2026-09-06"
    note = _recurring_note(vault, "- [ ] Garden\n" + old_line + "\n")

    writer.toggle_task_line(vault, "20-Projects/p.md", 2, old_line)

    lines = note.read_text(encoding="utf-8").splitlines()
    assert lines[1] == "    - [ ] Water plants 🔁 every week 📅 2026-09-13"
