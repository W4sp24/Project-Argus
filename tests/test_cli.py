"""Tests for the `friday init` vault generator."""

from pathlib import Path

import pytest

from backend.cli import InitError, init_vault

EXPECTED_FOLDERS = [
    "00-Inbox",
    "10-Daily",
    "15-Courses/CS000/notes",
    "15-Courses/CS000/materials",
    "15-Courses/CS000/study",
    "20-Projects",
    "30-Areas",
    "40-People",
    "50-Reference",
    "99-Private",
]


def test_init_vault_copies_template_and_inits_git(tmp_path: Path) -> None:
    dest = tmp_path / "vault"
    env_file = tmp_path / ".env"

    created = init_vault(dest, env_file)

    assert created == dest
    for folder in EXPECTED_FOLDERS:
        assert (dest / folder).is_dir(), f"missing {folder}"
    assert (dest / "Welcome.md").is_file()
    assert (dest / "15-Courses" / "CS000" / "course.md").is_file()
    assert (dest / ".git").is_dir(), "vault must be its own git repo (I2)"

    env_text = env_file.read_text(encoding="utf-8")
    assert f"VAULT_PATH={dest.resolve()}" in env_text


def test_init_vault_refuses_non_empty_destination(tmp_path: Path) -> None:
    dest = tmp_path / "occupied"
    dest.mkdir()
    (dest / "existing.md").write_text("hi", encoding="utf-8")

    with pytest.raises(InitError):
        init_vault(dest, tmp_path / ".env")


def test_init_vault_preserves_other_env_keys(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("BACKEND_PORT=9001\n", encoding="utf-8")

    init_vault(tmp_path / "vault", env_file)

    env_text = env_file.read_text(encoding="utf-8")
    assert "BACKEND_PORT=9001" in env_text
    assert "VAULT_PATH=" in env_text
