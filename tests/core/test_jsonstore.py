"""The durability guarantees the small JSON registries in ``.argus/`` rely on.

The bug these pin down: ``Path.write_text`` truncates before it writes, and
every loader returned ``[]`` on a parse error, so one crash mid-write deleted
every model the user had added — and the next save overwrote the evidence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.core.jsonstore import corrupt_files, load_json, save_json


def test_round_trip_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "models.json"
    save_json(path, [{"name": "llama3.2:3b"}])
    assert load_json(path, []) == [{"name": "llama3.2:3b"}]


def test_absent_file_is_silent(tmp_path: Path) -> None:
    """A registry nobody has written yet is normal, not a problem to report."""
    path = tmp_path / "never-written.json"
    assert load_json(path, []) == []
    assert corrupt_files(tmp_path) == []


def test_save_leaves_no_temp_file_behind(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    save_json(path, [{"name": "a"}])
    assert [p.name for p in tmp_path.iterdir()] == ["models.json"]


def test_save_replaces_atomically(tmp_path: Path) -> None:
    """The old contents survive intact until the new file is complete."""
    path = tmp_path / "models.json"
    save_json(path, [{"name": "first"}])
    save_json(path, [{"name": "second"}])
    assert load_json(path, []) == [{"name": "second"}]


def test_a_truncated_file_is_quarantined_not_returned(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    path.write_text('[{"name": "ollama-local", "endpo', encoding="utf-8")  # crash mid-write

    assert load_json(path, []) == []
    assert not path.exists()
    quarantined = tmp_path / "models.json.corrupt"
    assert "ollama-local" in quarantined.read_text(encoding="utf-8")


def test_an_empty_file_is_quarantined(tmp_path: Path) -> None:
    """Zero bytes is what a power loss during write_text actually leaves."""
    path = tmp_path / "models.json"
    path.write_text("", encoding="utf-8")

    assert load_json(path, []) == []
    assert (tmp_path / "models.json.corrupt").exists()


def test_the_next_save_cannot_bury_a_quarantined_file(tmp_path: Path) -> None:
    """load -> append -> save was the data-loss path; the bytes must outlive it."""
    path = tmp_path / "models.json"
    path.write_text('[{"name": "my-endpoint"', encoding="utf-8")

    models = load_json(path, [])
    models.append({"name": "added-later"})
    save_json(path, models)

    assert load_json(path, []) == [{"name": "added-later"}]
    assert "my-endpoint" in (tmp_path / "models.json.corrupt").read_text(encoding="utf-8")


def test_corrupt_files_lists_what_the_doctor_should_report(tmp_path: Path) -> None:
    (tmp_path / "models.json").write_text("{{{", encoding="utf-8")
    (tmp_path / "mcp-servers.json").write_text("nope", encoding="utf-8")
    load_json(tmp_path / "models.json", [])
    load_json(tmp_path / "mcp-servers.json", [])

    assert [p.name for p in corrupt_files(tmp_path)] == [
        "mcp-servers.json.corrupt",
        "models.json.corrupt",
    ]


def test_a_valid_file_is_never_quarantined(tmp_path: Path) -> None:
    path = tmp_path / "model-prefs.json"
    path.write_text(json.dumps({"default": "llama3.2:3b"}), encoding="utf-8")

    assert load_json(path, {}) == {"default": "llama3.2:3b"}
    assert path.exists()
    assert corrupt_files(tmp_path) == []


def test_an_unserialisable_payload_leaves_the_old_file_intact(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    save_json(path, [{"name": "keep-me"}])

    with pytest.raises(TypeError):
        save_json(path, [{"name": object()}])

    assert load_json(path, []) == [{"name": "keep-me"}]
    assert not (tmp_path / "models.json.tmp").exists()
