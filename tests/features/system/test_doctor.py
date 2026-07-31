"""Tests for `friday doctor` — environment health checks."""

import subprocess
from pathlib import Path

import pytest

from backend.cli import main
from backend.core.config import Settings
from backend.features.system.doctor import run_checks


@pytest.fixture()
def healthy_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "vault"
    vault.mkdir()
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    return vault


def test_healthy_vault_has_no_failures(healthy_vault: Path) -> None:
    checks = run_checks(Settings(_vault_path=healthy_vault))

    names = {check.name for check in checks}
    assert {"vault", "vault-git", "database", "chroma", "keyring", "gcal", "todoist"} <= names
    assert [check for check in checks if check.status == "FAIL"] == []
    by_name = {check.name: check for check in checks}
    assert by_name["vault"].status == "OK"
    assert by_name["vault-git"].status == "OK"
    assert by_name["database"].status == "OK"
    assert by_name["gcal"].status in ("OK", "WARN")  # WARN until credentials exist


def test_missing_vault_fails(tmp_path: Path) -> None:
    checks = run_checks(Settings(_vault_path=tmp_path / "nope"))
    by_name = {check.name: check for check in checks}
    assert by_name["vault"].status == "FAIL"


def test_ungitted_vault_fails_git_check(tmp_path: Path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    checks = run_checks(Settings(_vault_path=vault))
    by_name = {check.name: check for check in checks}
    assert by_name["vault-git"].status == "FAIL"
    assert "git init" in by_name["vault-git"].detail


def test_chroma_ok_when_vault_has_no_indexable_files(healthy_vault: Path) -> None:
    """A fresh vault with nothing to index yet is not a broken install."""
    checks = run_checks(Settings(_vault_path=healthy_vault))
    by_name = {check.name: check for check in checks}
    assert by_name["chroma"].status == "OK"


def test_chroma_warns_when_notes_exist_but_index_is_empty(healthy_vault: Path) -> None:
    """The bug this check exists to catch: notes present, nothing indexed."""
    (healthy_vault / "50-Reference").mkdir()
    (healthy_vault / "50-Reference" / "note.md").write_text("# Note\n\nhello\n", encoding="utf-8")

    checks = run_checks(Settings(_vault_path=healthy_vault))
    by_name = {check.name: check for check in checks}
    assert by_name["chroma"].status == "WARN"
    assert "reindex" in by_name["chroma"].detail


def test_chroma_ok_when_index_actually_has_chunks(healthy_vault: Path, monkeypatch) -> None:
    class FakeCollection:
        def count(self) -> int:
            return 42

    class FakeIndex:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        @property
        def collection(self):
            return FakeCollection()

    monkeypatch.setattr("backend.rag.index.VaultIndex", FakeIndex)

    checks = run_checks(Settings(_vault_path=healthy_vault))
    by_name = {check.name: check for check in checks}
    assert by_name["chroma"].status == "OK"
    assert "42" in by_name["chroma"].detail


def test_chroma_fails_when_index_is_unreadable(healthy_vault: Path, monkeypatch) -> None:
    class BrokenIndex:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("chroma directory is corrupt")

    monkeypatch.setattr("backend.rag.index.VaultIndex", BrokenIndex)

    checks = run_checks(Settings(_vault_path=healthy_vault))
    by_name = {check.name: check for check in checks}
    assert by_name["chroma"].status == "FAIL"
    assert "corrupt" in by_name["chroma"].detail


def test_chroma_retries_a_transient_open_failure(healthy_vault: Path, monkeypatch) -> None:
    """First-launch race with the boot indexer must not be reported as FAIL.

    Reproduced against the frozen backend: the very first /api/doctor of a
    fresh vault raced the boot-time index thread creating the same chroma
    directory, and the loser raised. Every later call succeeded. Telling a
    brand-new user their index is corrupt while it is quietly building is the
    wrong answer, so one retry decides it.
    """
    attempts = {"n": 0}

    class FlakyCollection:
        def count(self) -> int:
            return 7

    class FlakyIndex:
        def __init__(self, *_args, **_kwargs) -> None:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("chroma directory is being created")

        @property
        def collection(self):
            return FlakyCollection()

    monkeypatch.setattr("backend.rag.index.VaultIndex", FlakyIndex)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)

    checks = run_checks(Settings(_vault_path=healthy_vault))
    by_name = {check.name: check for check in checks}

    assert attempts["n"] == 2, "the transient failure was not retried"
    assert by_name["chroma"].status == "OK"
    assert "7" in by_name["chroma"].detail


def test_cli_doctor_exit_codes(healthy_vault: Path, tmp_path: Path, capsys) -> None:
    env_ok = tmp_path / "ok.env"
    env_ok.write_text(f"VAULT_PATH={healthy_vault}\n", encoding="utf-8")
    assert main(["doctor", "--env-file", str(env_ok)]) == 0
    assert "vault" in capsys.readouterr().out

    env_bad = tmp_path / "bad.env"
    env_bad.write_text(f"VAULT_PATH={tmp_path / 'missing'}\n", encoding="utf-8")
    assert main(["doctor", "--env-file", str(env_bad)]) == 1
