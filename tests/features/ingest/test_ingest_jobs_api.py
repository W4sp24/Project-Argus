"""The job routes, the source listing, and the destination list.

The job runner is injected so a test drives the pipeline synchronously; a
test that spawned the real daemon thread would be testing ``threading``.
``POST /api/ingest`` is exercised here too, because the whole point of adding
a job API beside it is that the old synchronous route keeps working -- two
callers in `web/` still read its response body directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.main import create_app


class FakeIndex:
    def upsert_file(self, vault_path, rel_path):
        return 3

    def chunk_counts(self):
        return {"00-Inbox/files/seeded.md": 5}

    def size(self):
        return {"chunks": 5, "files": 1}

    def schema_stale(self):
        return False


class BrokenIndex:
    """Missing [rag] extras. A listing must degrade, never 500."""

    def upsert_file(self, vault_path, rel_path):
        raise ImportError("No module named 'chromadb'")

    def chunk_counts(self):
        raise ImportError("No module named 'chromadb'")


async def fake_generator(prompt: str, model: str | None = None) -> str:
    return "A generated summary."


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "00-Inbox" / "files").mkdir(parents=True)
    (root / "10-Daily").mkdir()
    (root / "15-Courses" / "CS301" / "materials").mkdir(parents=True)
    (root / "99-Private").mkdir()
    (root / "90-Meta").mkdir()
    (root / "00-Inbox" / "files" / "seeded.md").write_text("# Seeded\n", encoding="utf-8")
    (root / "00-Inbox" / "files" / "slides.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "99-Private" / "secret.md").write_text("# Secret\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=root, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"], cwd=root, capture_output=True, check=True
    )
    return root


def _client(vault: Path, *, index=FakeIndex) -> TestClient:
    app = create_app(
        Settings(_vault_path=vault),
        generator=fake_generator,
        index_factory=index,
        # Synchronous: the route's job is to accept and schedule, and a real
        # thread would make every assertion below a race.
        ingest_job_runner=lambda run: run(),
    )
    return TestClient(app)


@pytest.fixture()
def client(vault: Path) -> TestClient:
    return _client(vault)


# --- GET /api/sources ---------------------------------------------------------


def test_sources_lists_the_vault_with_chunk_counts(client: TestClient) -> None:
    payload = client.get("/api/sources").json()

    assert payload["index_available"] is True
    by_path = {item["path"]: item for item in payload["sources"]}
    assert by_path["00-Inbox/files/seeded.md"]["chunks"] == 5
    assert by_path["00-Inbox/files/slides.pdf"]["kind"] == "PDF"
    # Known to have nothing in the index, which is not the same as unknown.
    assert by_path["00-Inbox/files/slides.pdf"]["chunks"] is None


def test_sources_never_lists_a_protected_zone(client: TestClient) -> None:
    paths = {item["path"] for item in client.get("/api/sources").json()["sources"]}

    assert not any(path.startswith(("99-Private", "90-Meta", ".argus")) for path in paths)


def test_sources_can_be_scoped_to_a_folder(client: TestClient, vault: Path) -> None:
    (vault / "15-Courses" / "CS301" / "materials" / "wk1.md").write_text("# W\n", encoding="utf-8")

    paths = {
        item["path"]
        for item in client.get("/api/sources", params={"folder": "15-Courses/CS301"}).json()[
            "sources"
        ]
    }

    assert paths == {"15-Courses/CS301/materials/wk1.md"}


def test_sources_survives_an_index_that_cannot_load(vault: Path) -> None:
    """No [rag] extras must mean unknown counts, not a 500."""
    response = _client(vault, index=BrokenIndex).get("/api/sources")

    assert response.status_code == 200
    payload = response.json()
    assert payload["index_available"] is False
    assert payload["sources"], "the files are still real and still listable"
    assert all(item["chunks"] is None for item in payload["sources"])


def test_sources_only_lists_what_rag_can_read(client: TestClient, vault: Path) -> None:
    (vault / "00-Inbox" / "files" / "photo.png").write_bytes(b"png")

    paths = {item["path"] for item in client.get("/api/sources").json()["sources"]}

    assert "00-Inbox/files/photo.png" not in paths


# --- GET /api/ingest/destinations ---------------------------------------------


def test_destinations_are_taxonomy_derived_and_exclude_protected_zones(
    client: TestClient,
) -> None:
    destinations = client.get("/api/ingest/destinations").json()["destinations"]

    assert "00-Inbox/files" in destinations, "the default target must be offered"
    assert "15-Courses/CS301/materials" in destinations, "a real course folder must be offered"
    assert not any(path.startswith(("99-Private", "90-Meta", ".")) for path in destinations)


def test_destinations_follow_a_renamed_taxonomy(vault: Path) -> None:
    """A hardcoded `15-Courses` here would reintroduce the bug the
    configurable-taxonomy refactor fixed."""
    from backend.core.taxonomy import Taxonomy

    (vault / "Courses" / "CS301").mkdir(parents=True)
    app = create_app(
        Settings(_vault_path=vault, taxonomy=Taxonomy(courses="Courses")),
        generator=fake_generator,
        index_factory=FakeIndex,
    )
    destinations = TestClient(app).get("/api/ingest/destinations").json()["destinations"]

    assert any(path.startswith("Courses/") for path in destinations)
    assert not any(path.startswith("15-Courses") for path in destinations)


# --- POST /api/ingest/jobs ----------------------------------------------------


def _upload(client: TestClient, files, **data):
    return client.post(
        "/api/ingest/jobs",
        files=[("files", (name, body, "application/octet-stream")) for name, body in files],
        data=data,
    )


def test_a_job_accepts_a_batch_and_reports_every_file(client: TestClient, vault: Path) -> None:
    response = _upload(
        client, [("a.md", b"# A\n"), ("b.md", b"# B\n")], target="00-Inbox/files"
    )

    assert response.status_code == 202
    job_id = response.json()["job_id"]

    job = client.get(f"/api/ingest/jobs/{job_id}").json()
    assert job["status"] == "ok"
    assert job["total"] == 2
    assert [item["stage"] for item in job["items"]] == ["done", "done"]
    assert (vault / "00-Inbox" / "files" / "a.md").is_file()
    assert (vault / "00-Inbox" / "files" / "b.md").is_file()


def test_a_job_honours_the_chosen_destination(client: TestClient, vault: Path) -> None:
    """The whole point: `target` was accepted by the API and settable nowhere."""
    _upload(client, [("wk1.pdf", b"%PDF fake")], target="15-Courses/CS301/materials")

    assert (vault / "15-Courses" / "CS301" / "materials" / "wk1.pdf").is_file()


def test_a_job_with_a_prompt_writes_a_summary(client: TestClient, vault: Path) -> None:
    response = _upload(
        client,
        [("lecture.md", b"# Lecture\n\nContent.\n")],
        target="00-Inbox/files",
        summary_prompt="list the key definitions",
    )

    job = client.get(f"/api/ingest/jobs/{response.json()['job_id']}").json()
    assert job["items"][0]["summary_path"] == "00-Inbox/files/lecture.summary.md"
    assert (vault / "00-Inbox" / "files" / "lecture.summary.md").is_file()


def test_an_unsupported_file_type_is_refused_before_anything_is_staged(
    client: TestClient, vault: Path
) -> None:
    response = _upload(client, [("virus.exe", b"MZ")], target="00-Inbox/files")

    assert response.status_code == 422
    assert "exe" in response.json()["detail"]
    assert not (vault / ".argus" / "ingest-staging").exists()


def test_one_bad_type_refuses_the_whole_batch(client: TestClient) -> None:
    """All-or-nothing: a half-accepted batch is worse than a clear refusal."""
    response = _upload(
        client, [("ok.md", b"# A\n"), ("virus.exe", b"MZ")], target="00-Inbox/files"
    )

    assert response.status_code == 422
    assert client.get("/api/ingest/jobs").json()["jobs"] == []


def test_too_many_files_is_refused(client: TestClient) -> None:
    response = _upload(
        client, [(f"f{n}.md", b"# x\n") for n in range(51)], target="00-Inbox/files"
    )

    assert response.status_code == 422
    assert "50" in response.json()["detail"]


def test_an_empty_batch_is_refused(client: TestClient) -> None:
    response = client.post("/api/ingest/jobs", data={"target": "00-Inbox/files"})

    assert response.status_code == 422


def test_a_protected_target_is_refused(client: TestClient, vault: Path) -> None:
    response = _upload(client, [("a.md", b"# A\n")], target="99-Private")

    assert response.status_code == 400
    assert not (vault / "99-Private" / "a.md").exists()


def test_a_traversing_target_is_refused(client: TestClient) -> None:
    response = _upload(client, [("a.md", b"# A\n")], target="../escape")

    assert response.status_code == 400


def test_a_second_job_is_refused_while_one_is_in_flight(vault: Path) -> None:
    """One at a time: two jobs would mean two model loads and two git snapshots."""
    started: list[str] = []
    app = create_app(
        Settings(_vault_path=vault),
        generator=fake_generator,
        index_factory=FakeIndex,
        # Never actually runs the job, so the first one stays in flight.
        ingest_job_runner=lambda run: started.append("scheduled"),
    )
    client = TestClient(app)

    first = _upload(client, [("a.md", b"# A\n")], target="00-Inbox/files")
    second = _upload(client, [("b.md", b"# B\n")], target="00-Inbox/files")

    assert first.status_code == 202
    assert second.status_code == 409
    assert len(started) == 1


def test_jobs_are_listed_newest_first_without_their_items(client: TestClient) -> None:
    first = _upload(client, [("a.md", b"# A\n")], target="00-Inbox/files").json()["job_id"]
    second = _upload(client, [("b.md", b"# B\n")], target="00-Inbox/files").json()["job_id"]

    jobs = client.get("/api/ingest/jobs").json()["jobs"]

    assert [job["id"] for job in jobs] == [second, first]
    assert "items" not in jobs[0]


def test_an_unknown_job_is_a_404(client: TestClient) -> None:
    assert client.get("/api/ingest/jobs/nope").status_code == 404


def test_the_staging_directory_does_not_survive_the_job(client: TestClient, vault: Path) -> None:
    """Staging lives under .argus/, never in the vault where the watcher would
    index it at its temporary path."""
    _upload(client, [("a.md", b"# A\n")], target="00-Inbox/files")

    staging = vault / ".argus" / "ingest-staging"
    assert not staging.exists() or not any(staging.iterdir())


# --- POST /api/ingest/precheck ------------------------------------------------


def test_precheck_reports_an_existing_file_and_its_hash(
    client: TestClient, vault: Path
) -> None:
    payload = client.post(
        "/api/ingest/precheck",
        json={"filename": "seeded.md", "target": "00-Inbox/files"},
    ).json()

    import hashlib

    # Hashed from the bytes actually on disk, not from the source literal:
    # `write_text` translates newlines on Windows, and the browser will be
    # hashing the exact bytes it is about to upload.
    on_disk = (vault / "00-Inbox" / "files" / "seeded.md").read_bytes()
    assert payload["exists"] is True
    assert payload["path"] == "00-Inbox/files/seeded.md"
    assert payload["sha256"] == hashlib.sha256(on_disk).hexdigest()


def test_precheck_reports_a_new_file_as_absent(client: TestClient) -> None:
    payload = client.post(
        "/api/ingest/precheck", json={"filename": "brand-new.md", "target": "00-Inbox/files"}
    ).json()

    assert payload["exists"] is False
    assert payload["sha256"] is None


def test_precheck_refuses_a_protected_target(client: TestClient) -> None:
    response = client.post(
        "/api/ingest/precheck", json={"filename": "secret.md", "target": "99-Private"}
    )

    assert response.status_code == 400


def test_replacing_overwrites_instead_of_writing_a_dash_two(
    client: TestClient, vault: Path
) -> None:
    """Without this, re-ingesting a corrected file leaves the stale copy indexed
    alongside it and every answer can cite either."""
    _upload(client, [("seeded.md", b"# Corrected\n")], target="00-Inbox/files", replace="true")

    body = (vault / "00-Inbox" / "files" / "seeded.md").read_text(encoding="utf-8")
    assert body == "# Corrected\n"
    assert not (vault / "00-Inbox" / "files" / "seeded-2.md").exists()


def test_without_replace_a_collision_still_dedupes(client: TestClient, vault: Path) -> None:
    """The existing behaviour, unchanged, when the user has not asked to replace."""
    _upload(client, [("seeded.md", b"# Another\n")], target="00-Inbox/files")

    body = (vault / "00-Inbox" / "files" / "seeded.md").read_text(encoding="utf-8")
    assert body == "# Seeded\n"
    assert (vault / "00-Inbox" / "files" / "seeded-2.md").is_file()
