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


# --- Hourly calendar sync ----------------------------------------------------


def test_build_scheduler_registers_the_calendar_sync_job(tmp_path: Path) -> None:
    """Registered by build_scheduler only, so test apps still spawn nothing."""
    from backend.scheduler import build_scheduler

    scheduler = build_scheduler(Settings(_vault_path=tmp_path))
    assert "calendar-sync" in {job.id for job in scheduler.get_jobs()}


def test_calendar_sync_job_never_raises(monkeypatch, tmp_path: Path) -> None:
    """A scheduler job that raises is a stack trace in a log nobody reads.

    The per-feed outcome is recorded on each calendar row instead, which is
    what the UI shows — so this only has to survive, not report.
    """
    from backend.features.calendar import sync
    from backend.scheduler import run_calendar_sync_job

    def _explode(*_args, **_kwargs):
        raise RuntimeError("network is on fire")

    monkeypatch.setattr(sync, "sync_all", _explode)
    vault = tmp_path / "vault"
    vault.mkdir()

    run_calendar_sync_job(Settings(_vault_path=vault))  # must not raise


def test_calendar_sync_job_syncs_every_subscription(monkeypatch, tmp_path: Path) -> None:
    from backend.features.calendar import sync
    from backend.scheduler import run_calendar_sync_job

    seen: list[str] = []
    monkeypatch.setattr(
        sync, "sync_all", lambda conn, client=None: seen.append("ran") or []
    )
    vault = tmp_path / "vault"
    vault.mkdir()

    run_calendar_sync_job(Settings(_vault_path=vault))

    assert seen == ["ran"]
