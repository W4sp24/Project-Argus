"""The ingest job body: save, index, optionally summarise, record every step.

``run_ingest_job`` is called here synchronously. It is a plain function;
making it a thread is the router's job, and a test that spawned one would be
testing ``threading`` rather than the pipeline.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.ingest import store
from backend.features.ingest.pipeline import run_ingest_job


class FakeIndex:
    """Records upserts; never loads an embedding model."""

    def __init__(self) -> None:
        self.upserts: list[str] = []
        self.fail_on: set[str] = set()

    def upsert_file(self, vault_path: Path, rel_path: str) -> int:
        if any(marker in rel_path for marker in self.fail_on):
            raise RuntimeError("embedding exploded")
        self.upserts.append(rel_path)
        return 3


class FakeGenerator:
    """Stands in for the LLM. Records every prompt it is handed."""

    def __init__(self, reply: str = "A short summary of the thing.") -> None:
        self.prompts: list[str] = []
        self.reply = reply

    async def __call__(self, prompt: str, model: str | None = None) -> str:
        self.prompts.append(prompt)
        return self.reply


def _commits(vault: Path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "00-Inbox" / "files").mkdir(parents=True)
    (root / "10-Daily").mkdir()
    (root / "Welcome.md").write_text("# Hi\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, capture_output=True, check=True)
    return root


@pytest.fixture()
def settings(vault: Path) -> Settings:
    return Settings(_vault_path=vault)


@pytest.fixture()
def conn(settings: Settings):
    connection = connect(settings.db_path)
    init_schema(connection)
    yield connection
    connection.close()


def _stage(tmp_path: Path, files: dict[str, bytes]) -> Path:
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    for index, (name, data) in enumerate(files.items()):
        # Staged names are ordinal-prefixed so two uploads sharing a filename
        # in one batch cannot collide before they reach the vault.
        (staging / f"{index}__{name}").write_bytes(data)
    return staging


def _run(settings, conn, tmp_path, files, *, prompt="", index=None, generator=None):
    job_id = store.create_job(
        conn, target="00-Inbox/files", summary_prompt=prompt, filenames=list(files)
    )
    fake_index = index if index is not None else FakeIndex()
    run_ingest_job(
        job_id,
        settings=settings,
        index_factory=lambda: fake_index,
        generator=generator,
        staging_dir=_stage(tmp_path, files),
    )
    return job_id, fake_index


# --- saving and indexing ------------------------------------------------------


def test_files_land_at_the_chosen_target_and_are_indexed(settings, conn, tmp_path, vault) -> None:
    job_id, index = _run(settings, conn, tmp_path, {"a.md": b"# A\n", "b.md": b"# B\n"})

    job = store.get_job(conn, job_id)
    assert job["status"] == "ok"
    assert job["done"] == 2
    assert (vault / "00-Inbox" / "files" / "a.md").is_file()
    assert (vault / "00-Inbox" / "files" / "b.md").is_file()
    assert set(index.upserts) == {"00-Inbox/files/a.md", "00-Inbox/files/b.md"}
    assert [item["stage"] for item in job["items"]] == ["done", "done"]
    assert [item["chunks"] for item in job["items"]] == [3, 3]


def test_the_index_is_built_once_for_the_whole_job(settings, conn, tmp_path) -> None:
    """A VaultIndex's embedding model is per-instance and takes seconds to load."""
    calls = {"n": 0}
    shared = FakeIndex()

    def counting_factory():
        calls["n"] += 1
        return shared

    job_id = store.create_job(
        conn, target="00-Inbox/files", summary_prompt="", filenames=["a.md", "b.md", "c.md"]
    )
    run_ingest_job(
        job_id,
        settings=settings,
        index_factory=counting_factory,
        generator=None,
        staging_dir=_stage(tmp_path, {"a.md": b"# A\n", "b.md": b"# B\n", "c.md": b"# C\n"}),
    )

    assert calls["n"] == 1, "the factory must not be called per file"


def test_the_whole_job_takes_one_snapshot(settings, conn, tmp_path, vault) -> None:
    """One user action, one undo point -- not one `git add -A` per file."""
    before = _commits(vault)

    _run(settings, conn, tmp_path, {"a.md": b"# A\n", "b.md": b"# B\n", "c.md": b"# C\n"})

    assert _commits(vault) == before + 1


def test_one_bad_file_does_not_kill_the_rest_of_the_job(settings, conn, tmp_path) -> None:
    index = FakeIndex()
    index.fail_on = {"b.md"}

    job_id, _ = _run(
        settings,
        conn,
        tmp_path,
        {"a.md": b"# A\n", "b.md": b"# B\n", "c.md": b"# C\n"},
        index=index,
    )

    job = store.get_job(conn, job_id)
    stages = {item["filename"]: item["stage"] for item in job["items"]}
    assert job["status"] == "partial"
    assert stages == {"a.md": "done", "b.md": "failed", "c.md": "done"}
    assert job["done"] == 3, "a failed file is still a finished file"
    errors = [item["error"] for item in job["items"] if item["filename"] == "b.md"]
    assert errors and "embedding exploded" in errors[0]


def test_a_job_whose_every_file_fails_is_failed_not_partial(settings, conn, tmp_path) -> None:
    index = FakeIndex()
    index.fail_on = {".md"}

    job_id, _ = _run(settings, conn, tmp_path, {"a.md": b"# A\n"}, index=index)

    assert store.get_job(conn, job_id)["status"] == "failed"


def test_the_job_body_never_raises(settings, conn, tmp_path) -> None:
    """It runs on a daemon thread; an escaping exception is invisible."""

    def exploding_factory():
        raise RuntimeError("no [rag] extras")

    job_id = store.create_job(conn, target="00-Inbox/files", summary_prompt="", filenames=["a.md"])
    run_ingest_job(
        job_id,
        settings=settings,
        index_factory=exploding_factory,
        generator=None,
        staging_dir=_stage(tmp_path, {"a.md": b"# A\n"}),
    )

    job = store.get_job(conn, job_id)
    assert job["status"] == "failed"
    assert "no [rag] extras" in (job["error"] or "")


def test_the_staging_directory_is_cleaned_up(settings, conn, tmp_path) -> None:
    staging = _stage(tmp_path, {"a.md": b"# A\n"})
    job_id = store.create_job(conn, target="00-Inbox/files", summary_prompt="", filenames=["a.md"])

    run_ingest_job(
        job_id,
        settings=settings,
        index_factory=FakeIndex,
        generator=None,
        staging_dir=staging,
    )

    assert not staging.exists()


def test_a_forbidden_target_fails_the_items_not_the_process(settings, conn, tmp_path) -> None:
    job_id = store.create_job(conn, target="99-Private", summary_prompt="", filenames=["a.md"])
    run_ingest_job(
        job_id,
        settings=settings,
        index_factory=FakeIndex,
        generator=None,
        staging_dir=_stage(tmp_path, {"a.md": b"# A\n"}),
    )

    job = store.get_job(conn, job_id)
    assert job["status"] == "failed"
    assert job["items"][0]["stage"] == "failed"


# --- summarising --------------------------------------------------------------


def test_no_prompt_means_the_generator_is_never_called(settings, conn, tmp_path) -> None:
    generator = FakeGenerator()

    _run(settings, conn, tmp_path, {"a.md": b"# A\n"}, prompt="", generator=generator)

    assert generator.prompts == []


def test_a_prompt_writes_a_summary_note_beside_its_source(settings, conn, tmp_path, vault) -> None:
    generator = FakeGenerator("Dijkstra, explained.")

    job_id, index = _run(
        settings,
        conn,
        tmp_path,
        {"lecture.md": b"# Lecture\n\nShortest paths.\n"},
        prompt="list the key definitions",
        generator=generator,
    )

    summary = vault / "00-Inbox" / "files" / "lecture.summary.md"
    assert summary.is_file()
    body = summary.read_text(encoding="utf-8")
    assert "type: summary" in body
    assert "source: 00-Inbox/files/lecture.md" in body
    assert "list the key definitions" in body, "the instruction is recorded verbatim"
    assert "Dijkstra, explained." in body
    assert "[[lecture]]" in body, "wikilink back to the source"

    item = store.get_job(conn, job_id)["items"][0]
    assert item["summary_path"] == "00-Inbox/files/lecture.summary.md"
    assert "00-Inbox/files/lecture.summary.md" in index.upserts, "the summary is indexed too"


def test_the_prompt_carries_the_source_text(settings, conn, tmp_path) -> None:
    generator = FakeGenerator()

    _run(
        settings,
        conn,
        tmp_path,
        {"a.md": b"# A\n\nThe mitochondria is the powerhouse.\n"},
        prompt="summarise",
        generator=generator,
    )

    assert generator.prompts, "the generator was never called"
    assert "powerhouse" in generator.prompts[0]
    assert "summarise" in generator.prompts[0]


def test_a_deduped_source_gets_a_matching_summary_name(settings, conn, tmp_path, vault) -> None:
    """`_dedupe` renames a colliding upload, so the derived name must follow it.

    Otherwise a second ingest of `lecture.md` writes `lecture-2.md` but tries
    to create `lecture.summary.md` again, and `create_note` -- deliberately
    create-only -- fails the item for a reason the user cannot act on.
    """
    (vault / "00-Inbox" / "files" / "lecture.md").write_text("# Old\n", encoding="utf-8")
    (vault / "00-Inbox" / "files" / "lecture.summary.md").write_text("# Old\n", encoding="utf-8")

    job_id, _ = _run(
        settings,
        conn,
        tmp_path,
        {"lecture.md": b"# New\n"},
        prompt="summarise",
        generator=FakeGenerator(),
    )

    job = store.get_job(conn, job_id)
    assert job["items"][0]["stage"] == "done"
    assert job["items"][0]["path"] == "00-Inbox/files/lecture-2.md"
    assert (vault / "00-Inbox" / "files" / "lecture-2.summary.md").is_file()


def test_a_no_ai_file_is_skipped_and_never_reaches_the_generator(
    settings, conn, tmp_path, vault
) -> None:
    """I3. The one behaviour here whose regression is a privacy incident."""
    generator = FakeGenerator()

    job_id, _ = _run(
        settings,
        conn,
        tmp_path,
        {"private.md": b"---\ntags: [no-ai]\n---\n\n# Mine\n", "public.md": b"# Fine\n"},
        prompt="summarise",
        generator=generator,
    )

    job = store.get_job(conn, job_id)
    stages = {item["filename"]: item["stage"] for item in job["items"]}
    assert stages["private.md"] == "skipped"
    assert stages["public.md"] == "done"
    assert len(generator.prompts) == 1, "only the public file may reach the model"
    assert "Mine" not in generator.prompts[0]
    assert not (vault / "00-Inbox" / "files" / "private.summary.md").exists()
    skipped = [item for item in job["items"] if item["filename"] == "private.md"][0]
    assert "no-ai" in (skipped["error"] or "").lower(), "the user must see why"


def test_a_skipped_summary_still_saves_and_indexes_the_file(
    settings, conn, tmp_path, vault
) -> None:
    """Skipping the *summary* must not skip the ingest -- the file still counts."""
    _, index = _run(
        settings,
        conn,
        tmp_path,
        {"private.md": b"---\ntags: [no-ai]\n---\n\n# Mine\n"},
        prompt="summarise",
        generator=FakeGenerator(),
    )

    assert (vault / "00-Inbox" / "files" / "private.md").is_file()
    assert "00-Inbox/files/private.md" in index.upserts


def test_a_generator_failure_does_not_lose_the_file(settings, conn, tmp_path, vault) -> None:
    """The file is already saved and indexed; a dead model must not undo that."""

    async def exploding(prompt: str, model: str | None = None) -> str:
        raise RuntimeError("provider is down")

    job_id, index = _run(
        settings, conn, tmp_path, {"a.md": b"# A\n"}, prompt="summarise", generator=exploding
    )

    job = store.get_job(conn, job_id)
    assert (vault / "00-Inbox" / "files" / "a.md").is_file()
    assert "00-Inbox/files/a.md" in index.upserts
    assert job["items"][0]["stage"] == "done"
    assert "provider is down" in (job["items"][0]["error"] or "")
