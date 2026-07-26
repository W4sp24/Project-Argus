"""Tests for backend.config settings loading and the model registry."""

from pathlib import Path

import pytest

from backend.config import (
    DEFAULT_MODELS,
    ConfigError,
    Settings,
    save_model_prefs,
    save_user_models,
)


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


def test_load_tolerates_utf8_bom(tmp_path: Path) -> None:
    """Notepad and PowerShell's `Out-File -Encoding utf8` prepend a BOM.

    Python's str.strip() leaves ﻿ in place, so without utf-8-sig the first
    key parses as "﻿VAULT_PATH" and the vault reads as unconfigured.
    """
    vault = tmp_path / "BomVault"
    vault.mkdir()
    env_file = tmp_path / ".env"
    env_file.write_text(f"VAULT_PATH={vault}\nBACKEND_PORT=8123\n", encoding="utf-8-sig")

    settings = Settings.load(env_file)

    assert settings.vault_path == vault
    assert settings.backend_port == 8123


def test_load_respects_port_override(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("VAULT_PATH=./v\nBACKEND_PORT=9001\n", encoding="utf-8")

    settings = Settings.load(env_file)

    assert settings.backend_port == 9001


# --- model registry ---------------------------------------------------------


@pytest.fixture()
def settings(tmp_path: Path) -> Settings:
    vault = tmp_path / "vault"
    vault.mkdir()
    return Settings(_vault_path=vault)


def test_default_model_falls_back_to_the_builtin_flag(settings: Settings) -> None:
    builtin_default = next(entry["name"] for entry in DEFAULT_MODELS if entry.get("default"))
    assert settings.default_model == builtin_default

    flagged = [entry["name"] for entry in settings.models if entry["default"]]
    assert flagged == [builtin_default], "exactly one entry is ever the default"


def test_chosen_default_moves_the_flag(settings: Settings) -> None:
    save_user_models(
        settings.models_file,
        [
            {
                "name": "llama3.1",
                "provider": "openai-compat",
                "endpoint": "http://localhost:11434/v1",
            }
        ],
    )
    save_model_prefs(settings.model_prefs_file, {"default": "llama3.1"})

    assert settings.default_model == "llama3.1"
    flagged = [entry["name"] for entry in settings.models if entry["default"]]
    assert flagged == ["llama3.1"], "the built-in flag yields to the user's choice"


def test_stale_default_falls_back_instead_of_dangling(settings: Settings) -> None:
    """Deleting your default model must not break every model-less caller."""
    save_model_prefs(settings.model_prefs_file, {"default": "a-model-since-deleted"})

    builtin_default = next(entry["name"] for entry in DEFAULT_MODELS if entry.get("default"))
    assert settings.default_model == builtin_default


def test_prefs_live_beside_models_json_not_inside_it(settings: Settings) -> None:
    """models.json's array shape predates this feature and stays untouched."""
    save_user_models(settings.models_file, [{"name": "llama3.1", "endpoint": "http://x/v1"}])
    save_model_prefs(settings.model_prefs_file, {"default": "llama3.1"})

    assert settings.model_prefs_file.parent == settings.models_file.parent
    assert settings.model_prefs_file != settings.models_file
    import json

    assert isinstance(json.loads(settings.models_file.read_text(encoding="utf-8")), list)


def test_registry_preserves_provider_and_model_id(settings: Settings) -> None:
    save_user_models(
        settings.models_file,
        [
            {
                "name": "groq-llama",
                "provider": "openai-compat",
                "endpoint": "https://api.groq.com/openai/v1",
                "key_ref": "model:groq-llama",
                "model_id": "llama-3.3-70b-versatile",
            },
            {"name": "claude-via-key", "provider": "anthropic-api", "key_ref": "model:claude"},
        ],
    )

    by_name = {entry["name"]: entry for entry in settings.models}
    assert by_name["groq-llama"]["model_id"] == "llama-3.3-70b-versatile"
    assert by_name["groq-llama"]["key_ref"] == "model:groq-llama"
    assert by_name["claude-via-key"]["provider"] == "anthropic-api"


def test_legacy_entries_without_a_provider_stay_openai_compat(settings: Settings) -> None:
    """Registries written before this feature must keep working untouched."""
    save_user_models(settings.models_file, [{"name": "llama3", "endpoint": "http://x/v1"}])

    entry = next(e for e in settings.models if e["name"] == "llama3")
    assert entry["provider"] == "openai-compat"
    assert entry["model_id"] is None
