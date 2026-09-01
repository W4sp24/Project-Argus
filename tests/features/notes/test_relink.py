"""Backfilling relationships onto notes written before the feature existed.

Two properties carry the whole thing, and both are the kind that look fine in
a demo and go wrong in a vault:

* **The guard.** A note the user wrote must never be rewritten. It is checked
  in the listing *and* again in ``relink_one``, because a caller can name a
  path directly, so both are pinned here.
* **Idempotence.** A second run must produce byte-identical output. The trap
  is not the fence -- ``replace_section`` is idempotent by construction -- it
  is that the model's ``## Topics`` section is *consumed* by the first pass,
  so a naive second run parses nothing and silently downgrades the note to
  zero concepts. That has its own test.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import frontmatter
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.ingest import store
from backend.features.notes import relink
from backend.features.notes.router import build_notes_router
from backend.vault import relations

WK1 = "15-Courses/ETHICS/notes/wk1.notes.md"
WK2 = "15-Courses/ETHICS/notes/wk2.notes.md"
MINE = "15-Courses/ETHICS/notes/mine.md"
HAND = "15-Courses/ETHICS/notes/hand.notes.md"


class FakeIndex:
    """Enough of ``VaultIndex`` for a relink: records upserts, retrieves nothing.

    ``query``/``all_chunks`` are here so :func:`~backend.rag.neighbours.nearest_notes`
    runs its real path and finds nothing, rather than landing in its
    never-raises fallback — a test that depends on a swallowed exception for
    its determinism is testing the wrong thing.
    """

    def __init__(self) -> None:
        self.upserted: list[str] = []

    def query(self, text: str, n_results: int = 8, where: dict | None = None) -> list[dict]:
        return []

    def all_chunks(self) -> list[dict]:
        return []

    def upsert_file(self, vault_path: Path, rel_path: str) -> int:
        self.upserted.append(rel_path)
        return 3


@pytest.fixture()
def fake_index() -> FakeIndex:
    return FakeIndex()


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    """A throwaway vault holding one of each population the guard must separate."""
    root = tmp_path / "vault"
    (root / "15-Courses" / "ETHICS" / "notes").mkdir(parents=True)
    (root / "15-Courses" / "ETHICS" / "materials").mkdir(parents=True)
    (root / "99-Private").mkdir(parents=True)
    (root / "15-Courses/ETHICS/materials/wk1.pdf").write_bytes(b"%PDF-")

    # An old generated note: one trailing wikilink whose extension was
    # stripped (so it resolves to nothing), no tags, no Related region. This
    # is what every note in the vault looks like before a relink.
    (root / WK1).write_text(
        "---\ntitle: wk1 summary\ntype: note\ngenerated_by: argus\n"
        "source: 15-Courses/ETHICS/materials/wk1.pdf\ncourse: ETHICS\n---\n\n"
        "The old body.\n\n[[wk1]]\n",
        encoding="utf-8",
    )
    # A second one, so a test can tell one snapshot per *job* from one per
    # note — and this one names concepts, which is what makes the second run
    # a real test rather than a tautology.
    (root / WK2).write_text(
        "---\ntitle: wk2 summary\ntype: note\ngenerated_by: argus\n"
        "source: 15-Courses/ETHICS/materials/wk2.pdf\ncourse: ETHICS\n---\n\n"
        "Kavanaugh on freedom.\n\n## Topics\n\n- Determinism\n- **Free Will**\n",
        encoding="utf-8",
    )
    (root / MINE).write_text(
        "---\ntitle: my own notes\n---\n\nHand written. Do not touch.\n", encoding="utf-8"
    )
    # Named exactly like something Argus wrote, but nobody stamped it. The
    # filename is a convention, not a licence to rewrite.
    (root / HAND).write_text(
        "---\ntitle: also mine\n---\n\nAlso hand written.\n", encoding="utf-8"
    )
    (root / "99-Private/therapy.notes.md").write_text(
        "---\ntitle: therapy\ntype: note\ngenerated_by: argus\n---\n\nprivate\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=root, check=True)
    return root


@pytest.fixture()
def vault_settings(vault: Path) -> Settings:
    """Settings over the throwaway vault. ``_vault_path`` is the field name —
    ``vault_path`` is a read-only property (see ``backend/core/config.py``),
    and every existing test in ``tests/features/`` builds one this way."""
    return Settings(_vault_path=vault)


def _commits(vault: Path) -> int:
    result = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    )
    return int(result.stdout.strip())


# --- the guard ---------------------------------------------------------------


def test_only_notes_argus_wrote_are_relinkable(vault: Path):
    found = relink.relinkable_notes(vault)
    assert WK1 in found
    assert WK2 in found
    assert MINE not in found


def test_a_generated_looking_filename_is_not_a_licence_to_rewrite(vault: Path):
    """The guard is ``generated_by: argus`` in frontmatter, and nothing else.

    A user is free to name a note ``anything.notes.md``. Widening the guard to
    the filename convention turns a backfill into a data-loss bug.
    """
    assert HAND not in relink.relinkable_notes(vault)
    before = (vault / HAND).read_text(encoding="utf-8")
    assert relink.relink_one(vault, HAND, resolve=lambda name: None, neighbours=[]) is False
    assert (vault / HAND).read_text(encoding="utf-8") == before


def test_a_user_note_is_refused_even_if_named_directly(vault: Path):
    """The guard is per-note, not only per-listing: a hand-picked path must
    not become a way past it."""
    before = (vault / MINE).read_text(encoding="utf-8")
    assert relink.relink_one(vault, MINE, resolve=lambda name: None, neighbours=[]) is False
    assert (vault / MINE).read_text(encoding="utf-8") == before


def test_the_private_zone_is_never_listed(vault: Path):
    """I3. A generated note *inside* ``99-Private/`` is still private, and
    the writer would refuse it anyway — it must never reach the writer."""
    assert not [path for path in relink.relinkable_notes(vault) if path.startswith("99-Private")]


# --- rewriting ---------------------------------------------------------------


def test_relinking_adds_the_region_and_keeps_the_body(vault: Path):
    changed = relink.relink_one(vault, WK1, resolve=lambda name: None, neighbours=[])
    assert changed is True
    post = frontmatter.load(vault / WK1)
    assert "The old body." in post.content
    assert relations.FENCE_START in post.content
    # The bug this feature exists for: [[wk1]] resolved to nothing.
    assert "[[15-Courses/ETHICS/materials/wk1.pdf|wk1]]" in post.content
    assert "[[15-Courses/ETHICS/course|ETHICS]]" in post.content
    assert "argus/note" in post["tags"]
    assert "course/ETHICS" in post["tags"]
    assert post["title"] == "wk1 summary"


def test_a_second_run_changes_nothing(vault: Path):
    relink.relink_one(vault, WK1, resolve=lambda name: None, neighbours=[])
    first = (vault / WK1).read_text(encoding="utf-8")
    changed = relink.relink_one(vault, WK1, resolve=lambda name: None, neighbours=[])
    assert changed is False
    assert (vault / WK1).read_text(encoding="utf-8") == first


def test_the_concepts_survive_a_second_run(vault: Path):
    """The idempotence trap, and the only one that is not obvious.

    The first pass *consumes* the model's ``## Topics`` section — that is the
    point of it — so a second pass has nothing left to parse. Reading the
    topics back out of frontmatter is what makes the re-run a genuine no-op
    instead of a silent downgrade to a note with no concepts at all.
    """
    resolve = {"Determinism": "60-Knowledge/General/Determinism.md"}.get
    relink.relink_one(vault, WK2, resolve=resolve, neighbours=[])
    first = (vault / WK2).read_text(encoding="utf-8")
    assert "## Topics" not in first
    assert "[[60-Knowledge/General/Determinism|Determinism]]" in first
    assert "[[Free Will]]" in first

    changed = relink.relink_one(vault, WK2, resolve=resolve, neighbours=[])
    assert changed is False
    again = (vault / WK2).read_text(encoding="utf-8")
    assert again == first
    post = frontmatter.load(vault / WK2)
    assert post["topics"] == ["Determinism", "Free Will"]
    assert "topic/free-will" in post["tags"]


def test_a_hand_edit_to_a_generated_note_survives_a_relink(vault: Path):
    relink.relink_one(vault, WK1, resolve=lambda name: None, neighbours=[])
    path = vault / WK1
    path.write_text(
        path.read_text(encoding="utf-8").replace("The old body.", "The old body.\n\nMy note."),
        encoding="utf-8",
    )
    relink.relink_one(vault, WK1, resolve=lambda name: None, neighbours=[])
    content = path.read_text(encoding="utf-8")
    assert "My note." in content
    assert content.count(relations.FENCE_START) == 1


def test_a_key_the_user_added_is_never_dropped(vault: Path):
    path = vault / WK1
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "course: ETHICS\n", "course: ETHICS\nrating: 5\ntags: [reading/2026]\n"
        ),
        encoding="utf-8",
    )
    relink.relink_one(vault, WK1, resolve=lambda name: None, neighbours=[])
    post = frontmatter.load(path)
    assert post["rating"] == 5
    assert "reading/2026" in post["tags"]
    assert "argus/note" in post["tags"]


def test_dry_run_reports_without_writing(vault: Path):
    before = (vault / WK1).read_text(encoding="utf-8")
    changed = relink.relink_one(
        vault, WK1, resolve=lambda name: None, neighbours=[], dry_run=True
    )
    assert changed is True
    assert (vault / WK1).read_text(encoding="utf-8") == before


# --- the job -----------------------------------------------------------------


def _run_job(settings: Settings, index: FakeIndex, *, dry_run: bool = False) -> dict:
    conn = connect(settings.db_path)
    init_schema(conn)
    job_id = store.create_job(conn, target="", filenames=[], kind="relink")
    conn.close()
    relink.run_relink_job(
        job_id, settings=settings, index_factory=lambda: index, dry_run=dry_run
    )
    conn = connect(settings.db_path)
    job = store.get_job(conn, job_id)
    conn.close()
    assert job is not None
    return job


def test_the_job_rewrites_every_generated_note_and_reports_ok(
    vault_settings: Settings, fake_index: FakeIndex
):
    job = _run_job(vault_settings, fake_index)
    assert job["status"] == "ok"
    assert {item["path"] for item in job["items"]} == {WK1, WK2}
    assert relations.FENCE_START in (vault_settings.vault_path / WK1).read_text(encoding="utf-8")


def test_the_job_re_upserts_what_it_rewrote(vault_settings: Settings, fake_index: FakeIndex):
    """Without this, retrieval keeps the stale ``wikilinks`` chunk metadata and
    the one-hop expansion — the whole point of putting links in the body —
    never sees them."""
    _run_job(vault_settings, fake_index)
    assert sorted(fake_index.upserted) == [WK1, WK2]


def test_the_job_takes_one_snapshot_for_the_whole_run(
    vault_settings: Settings, fake_index: FakeIndex
):
    """I2. ``_git_snapshot`` runs git with ``check=False``, so two overlapping
    snapshots race on ``.git/index.lock`` and the loser fails silently — N
    per-file snapshots is not a slower undo point, it is an unreliable one."""
    before = _commits(vault_settings.vault_path)
    _run_job(vault_settings, fake_index)
    assert _commits(vault_settings.vault_path) == before + 1


def test_a_dry_run_job_writes_nothing_and_indexes_nothing(
    vault_settings: Settings, fake_index: FakeIndex
):
    before = (vault_settings.vault_path / WK1).read_text(encoding="utf-8")
    commits = _commits(vault_settings.vault_path)
    job = _run_job(vault_settings, fake_index, dry_run=True)
    assert job["status"] == "ok"
    assert (vault_settings.vault_path / WK1).read_text(encoding="utf-8") == before
    assert fake_index.upserted == []
    assert _commits(vault_settings.vault_path) == commits


def test_a_second_job_reports_every_note_as_skipped(
    vault_settings: Settings, fake_index: FakeIndex
):
    _run_job(vault_settings, fake_index)
    job = _run_job(vault_settings, fake_index)
    assert {item["stage"] for item in job["items"]} == {"skipped"}
    assert job["status"] == "ok"


def test_one_unwritable_note_does_not_abort_the_rest(
    vault_settings: Settings, fake_index: FakeIndex, monkeypatch
):
    """One bad note is a partial job, not a failed one — the same contract the
    ingest and reindex bodies already keep."""
    real = relink.relink_one

    def _explode(vault_path, rel_path, **kwargs):
        if rel_path == WK1:
            raise OSError("disk full")
        return real(vault_path, rel_path, **kwargs)

    monkeypatch.setattr(relink, "relink_one", _explode)
    job = _run_job(vault_settings, fake_index)
    assert job["status"] == "partial"
    failed = [item for item in job["items"] if item["stage"] == "failed"]
    assert [item["path"] for item in failed] == [WK1]
    assert relations.FENCE_START in (vault_settings.vault_path / WK2).read_text(encoding="utf-8")


# --- the route ---------------------------------------------------------------


def _client(settings: Settings, index: FakeIndex) -> TestClient:
    app = FastAPI()
    # A synchronous job runner: the route answers 202 and the work is already
    # done by the time the test reads the vault, which makes this a test of
    # behaviour rather than of timing.
    app.include_router(build_notes_router(settings, lambda: index, job_runner=lambda run: run()))
    return TestClient(app)


def test_the_route_relinks_and_reports_how_many_notes_it_found(
    vault_settings: Settings, fake_index: FakeIndex
):
    response = _client(vault_settings, fake_index).post("/api/notes/relink")
    assert response.status_code == 202
    assert response.json()["notes"] == 2
    assert response.json()["job_id"]
    assert relations.FENCE_START in (vault_settings.vault_path / WK1).read_text(encoding="utf-8")


def test_the_route_refuses_while_an_ingest_holds_the_index(
    vault_settings: Settings, fake_index: FakeIndex
):
    conn = connect(vault_settings.db_path)
    init_schema(conn)
    store.create_job(conn, target="00-Inbox/files", filenames=["a.pdf"], kind="ingest")
    conn.close()

    response = _client(vault_settings, fake_index).post("/api/notes/relink")
    assert response.status_code == 409
    assert "ingest" in response.json()["detail"]
    # And nothing was written while it refused.
    assert relations.FENCE_START not in (vault_settings.vault_path / WK1).read_text(
        encoding="utf-8"
    )


def test_the_route_says_so_when_there_is_no_index_to_relink_against(vault_settings: Settings):
    app = FastAPI()
    app.include_router(build_notes_router(vault_settings))
    assert TestClient(app).post("/api/notes/relink").status_code == 503


# --- the command line --------------------------------------------------------


def _env_file(tmp_path: Path, vault: Path) -> Path:
    env_file = tmp_path / "relink.env"
    env_file.write_text(f"VAULT_PATH={vault}\n", encoding="utf-8")
    return env_file


def test_the_cli_relinks_the_vault(tmp_path: Path, vault: Path, fake_index: FakeIndex, monkeypatch, capsys):
    from backend import cli

    monkeypatch.setattr(
        "backend.rag.index.make_index_factory", lambda *args, **kwargs: (lambda: fake_index)
    )
    assert cli.main(["relink", "--env-file", str(_env_file(tmp_path, vault))]) == 0
    assert "2" in capsys.readouterr().out
    assert relations.FENCE_START in (vault / WK1).read_text(encoding="utf-8")


def test_the_cli_dry_run_writes_nothing(
    tmp_path: Path, vault: Path, fake_index: FakeIndex, monkeypatch, capsys
):
    from backend import cli

    monkeypatch.setattr(
        "backend.rag.index.make_index_factory", lambda *args, **kwargs: (lambda: fake_index)
    )
    before = (vault / WK1).read_text(encoding="utf-8")
    assert cli.main(["relink", "--dry-run", "--env-file", str(_env_file(tmp_path, vault))]) == 0
    assert (vault / WK1).read_text(encoding="utf-8") == before
    assert fake_index.upserted == []
