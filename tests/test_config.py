"""Tests for backend.config settings loading."""

from pathlib import Path

import pytest

from backend.config import ConfigError, Settings


def test_load_reads_vault_path_and_derives_db_path(tmp_path: Path) -> None:
    vault = tmp_path / "MyVault"
    vault.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(f"# comment\nVAULT_PATH={vault}\n", encoding="utf-8")

    settings = Settings.load(env_file)

    assert settings.vault_path == vault
    assert settings.db_path == vault / ".argus" / "argus.db"
    assert settings.backend_port == 8000


def test_load_missing_env_file_defers_error_until_vault_access(tmp_path: Path) -> None:
    settings = Settings.load(tmp_path / "absent.env")

    with pytest.raises(ConfigError):
        _ = settings.vault_path


def test_load_respects_port_override(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("VAULT_PATH=./v\nBACKEND_PORT=9001\n", encoding="utf-8")

    settings = Settings.load(env_file)

    assert settings.backend_port == 9001
