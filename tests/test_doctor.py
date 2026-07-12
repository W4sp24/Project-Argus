"""Tests for `friday doctor` — environment health checks."""

import subprocess
from pathlib import Path

import pytest

from backend.cli import main
from backend.config import Settings
from backend.doctor import run_checks


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


def test_cli_doctor_exit_codes(healthy_vault: Path, tmp_path: Path, capsys) -> None:
    env_ok = tmp_path / "ok.env"
    env_ok.write_text(f"VAULT_PATH={healthy_vault}\n", encoding="utf-8")
    assert main(["doctor", "--env-file", str(env_ok)]) == 0
    assert "vault" in capsys.readouterr().out

    env_bad = tmp_path / "bad.env"
    env_bad.write_text(f"VAULT_PATH={tmp_path / 'missing'}\n", encoding="utf-8")
    assert main(["doctor", "--env-file", str(env_bad)]) == 1
