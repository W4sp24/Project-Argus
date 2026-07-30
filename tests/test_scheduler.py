"""Tests for the nightly reindex job (the self-heal for watchdog events missed
while the app was closed)."""

from __future__ import annotations

from pathlib import Path

from backend.core.config import Settings
from backend.scheduler import run_reindex_job


class _FakeResult:
    def __init__(self, total_chunks: int, files: int, errors: dict | None = None) -> None:
        self.total_chunks = total_chunks
        self.files = files
        self.errors = errors or {}


class _FakeIndex:
    calls: list[Path] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def reindex_all(self, vault_path: Path) -> _FakeResult:
        _FakeIndex.calls.append(vault_path)
        return _FakeResult(total_chunks=3, files=1)


def test_run_reindex_job_calls_reindex_all(monkeypatch, tmp_path: Path) -> None:
    _FakeIndex.calls = []
    monkeypatch.setattr("backend.rag.index.VaultIndex", _FakeIndex)

    vault = tmp_path / "vault"
    vault.mkdir()
    run_reindex_job(Settings(_vault_path=vault))

    assert _FakeIndex.calls == [vault]


def test_run_reindex_job_never_raises_on_failure(monkeypatch, tmp_path: Path) -> None:
    class ExplodingIndex:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def reindex_all(self, vault_path: Path):
            raise RuntimeError("chroma is unhappy")

    monkeypatch.setattr("backend.rag.index.VaultIndex", ExplodingIndex)

    # Must not raise.
    run_reindex_job(Settings(_vault_path=tmp_path / "vault"))
