"""Deep links, and agreeing with Obsidian's own settings.

Both halves of a reported failure live here: "Document lookup can fail with a
Vault not found error... the application could not locate the vault for an
Obsidian URL pointing to the Second Brain vault and a daily note."
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from urllib.parse import unquote

import pytest

from backend.core.taxonomy import Taxonomy
from backend.vault.obsidian import (
    daily_note_settings,
    is_obsidian_vault,
    note_uri,
)
from backend.vault.writer import daily_note_path


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "Second Brain"
    root.mkdir()
    return root


def config(vault: Path, payload: str) -> None:
    (vault / ".obsidian").mkdir(exist_ok=True)
    (vault / ".obsidian" / "daily-notes.json").write_text(payload, encoding="utf-8")


# --- note_uri ----------------------------------------------------------------


def test_a_link_addresses_the_note_by_absolute_path(vault: Path) -> None:
    """`vault=<folder basename>` is matched against the vault's *registered*
    name in Obsidian, which is set when the vault is added and is independent
    of the directory name afterwards. That mismatch is the "Vault not found"
    dialog. `path=` cannot mismatch."""
    uri = note_uri(vault, "10-Daily/2026-08-21.md")

    assert uri.startswith("obsidian://open?path=")
    assert "vault=" not in uri
    assert unquote(uri.split("path=", 1)[1]) == (vault / "10-Daily/2026-08-21.md").as_posix()


def test_a_vault_name_with_a_space_survives_the_round_trip(vault: Path) -> None:
    """"Second Brain" is the vault from the report. A space that is not encoded
    truncates the URL at the first word."""
    uri = note_uri(vault, "10-Daily/2026-08-21.md")

    assert " " not in uri
    assert "Second Brain" in unquote(uri.split("path=", 1)[1])


def test_a_path_with_an_ampersand_cannot_terminate_the_query(vault: Path) -> None:
    uri = note_uri(vault, "30-Areas/R&D notes.md")

    assert "&" not in uri.split("path=", 1)[1]
    assert unquote(uri.split("path=", 1)[1]).endswith("30-Areas/R&D notes.md")


# --- is_obsidian_vault -------------------------------------------------------


def test_a_folder_obsidian_has_never_opened_is_not_a_vault(vault: Path) -> None:
    assert is_obsidian_vault(vault) is False
    (vault / ".obsidian").mkdir()
    assert is_obsidian_vault(vault) is True


# --- daily-note settings -----------------------------------------------------


def test_no_config_keeps_the_taxonomy_default(vault: Path) -> None:
    assert daily_note_settings(vault, "10-Daily") == ("10-Daily", "%Y-%m-%d")


def test_obsidians_own_folder_and_format_win(vault: Path) -> None:
    """Argus wrote `10-Daily/<ISO>.md` unconditionally, so a vault configured
    this way grew a second set of daily notes the user never opens."""
    config(vault, json.dumps({"folder": "Journal/2026", "format": "YYYY-MM-DD dddd"}))

    assert daily_note_settings(vault, "10-Daily") == ("Journal/2026", "%Y-%m-%d %A")


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("corrupt", "{ not json at all"),
        ("empty object", "{}"),
        ("blank values", json.dumps({"folder": "  ", "format": ""})),
        ("not an object", json.dumps(["nope"])),
        # Moment.js escapes literal text in brackets; reproducing that is more
        # than this needs to do, and a half-understood pattern would silently
        # misname the file.
        ("untranslatable format", json.dumps({"format": "[Day] YYYY"})),
    ],
)
def test_an_unusable_config_falls_back_rather_than_raising(
    vault: Path, label: str, payload: str
) -> None:
    config(vault, payload)

    folder, fmt = daily_note_settings(vault, "10-Daily")

    assert folder == "10-Daily", label
    assert fmt == "%Y-%m-%d", label


def test_a_leading_slash_in_the_folder_is_tolerated(vault: Path) -> None:
    config(vault, json.dumps({"folder": "/Journal/"}))

    assert daily_note_settings(vault, "10-Daily")[0] == "Journal"


# --- the writer's resolution -------------------------------------------------


def test_the_writer_lands_in_the_folder_obsidian_actually_uses(vault: Path) -> None:
    config(vault, json.dumps({"folder": "Journal", "format": "YYYY-MM-DD"}))

    absolute, relative = daily_note_path(vault, Taxonomy())

    today = date.today().isoformat()
    assert relative == f"Journal/{today}.md"
    assert absolute == vault / "Journal" / f"{today}.md"
    assert absolute.parent.is_dir(), "the folder must be created, not assumed"


def test_the_writer_keeps_todays_behaviour_without_a_config(vault: Path) -> None:
    absolute, relative = daily_note_path(vault, Taxonomy())

    today = date.today().isoformat()
    assert relative == f"10-Daily/{today}.md"
    assert absolute == vault / "10-Daily" / f"{today}.md"
