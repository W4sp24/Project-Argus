"""Tests for /api/study endpoints (fake generator + fake index)."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.core.config import Settings
from backend.core.taxonomy import Taxonomy
from backend.main import create_app

CORPUS = [
    {
        "text": "Binary search halves the search space each step, giving O(log n).",
        "meta": {
            "path": "15-Courses/CS201/materials/algos.pdf",
            "page": 12,
            "course": "CS201",
            "heading": "Binary search",
            "title": "algos",
        },
    }
]

RAW_EXAM = json.dumps(
    {
        "title": "CS201 quiz",
        "questions": [
            {
                "q": "What is the complexity of binary search?",
                "type": "short",
                "answer": "O(log n)",
                "explanation": "Halving each step.",
                "citation": {
                    "path": "15-Courses/CS201/materials/algos.pdf",
                    "page": 12,
                    "quote": "giving O(log n)",
                },
            }
        ],
    }
)


class FakeIndex:
    def __init__(self) -> None:
        self.upserts: list[str] = []

    def all_chunks(self):
        return CORPUS

    def chunk_counts(self):
        counts: dict[str, int] = {}
        for chunk in CORPUS:
            path = chunk["meta"].get("path")
            if path:
                counts[path] = counts.get(path, 0) + 1
        return counts

    def upsert_file(self, vault_path, rel_path):
        self.upserts.append(rel_path)
        return 1


async def fake_generator(prompt: str) -> str:
    if "study guide" in prompt.lower():
        return "## Outline\n\n- Binary search [algos.pdf p.12]\n"
    return RAW_EXAM


@pytest.fixture()
def client(tmp_path: Path) -> TestClient:
    vault = tmp_path / "vault"
    (vault / "15-Courses" / "CS201" / "materials").mkdir(parents=True)
    (vault / "15-Courses" / "CS201" / "course.md").write_text(
        "---\ntitle: Algorithms\n---\n# CS201\n", encoding="utf-8"
    )
    # `/api/study/upload` writes through `save_ingest_file` now, which
    # snapshots first (I2) and so needs a real repository. The old raw
    # `write_bytes` was exactly what let this fixture get away without one.
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=vault, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=vault,
        capture_output=True,
        check=True,
    )
    app = create_app(
        Settings(_vault_path=vault),
        chat_runner=fake_generator,  # unused here
        generator=fake_generator,
        index_factory=FakeIndex,
    )
    return TestClient(app)


def test_courses_listed(client: TestClient) -> None:
    payload = client.get("/api/study/courses").json()
    assert payload[0]["code"] == "CS201"
    assert payload[0]["title"] == "Algorithms"


def test_course_discovery_honours_a_renamed_courses_dir(tmp_path: Path) -> None:
    """The bug this branch fixes: a vault that doesn't call it 15-Courses/."""
    vault = tmp_path / "vault"
    (vault / "Classes" / "CS301" / "materials").mkdir(parents=True)
    (vault / "Classes" / "CS301" / "course.md").write_text(
        "---\ntitle: Data Structures\n---\n# CS301\n", encoding="utf-8"
    )
    settings = Settings(_vault_path=vault, taxonomy=Taxonomy(courses="Classes"))
    client = TestClient(create_app(settings, generator=fake_generator, index_factory=FakeIndex))

    payload = client.get("/api/study/courses").json()

    assert payload[0]["code"] == "CS301"
    assert payload[0]["title"] == "Data Structures"
    assert payload[0]["path"] == "Classes/CS301/course.md"


def test_upload_lands_in_materials(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/study/upload",
        data={"course": "CS201"},
        files={"file": ("deck.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert response.status_code == 200
    saved = tmp_path / "vault" / "15-Courses" / "CS201" / "materials" / "deck.pdf"
    assert saved.is_file(), "upload must land in materials/"


def test_course_info_reports_its_real_write_targets(client: TestClient) -> None:
    """The frontend must never rebuild these paths from a bare folder literal
    (they'd break the moment the taxonomy is reconfigured) — it has to read
    them off CourseInfo instead."""
    course = client.get("/api/study/courses").json()[0]
    assert course["materials_path"] == "15-Courses/CS201/materials"
    assert course["notes_path"] == "15-Courses/CS201/notes"


def test_ingest_with_the_reported_materials_path_makes_courses_report_it(
    client: TestClient, tmp_path: Path
) -> None:
    """The reported bug, end to end: Study's upload target used to be the
    course *root* (`15-Courses/<code>`), not `materials/` — the file saved
    fine, but `courses()` only ever counts files inside `materials/`, so the
    row kept reading "0 materials" and GUIDE/EXAM stayed disabled. The fix is
    for callers to target whatever `materials_path` this API reports, not a
    hardcoded path — proven here by going through the generic `/api/ingest`
    upload path (what `IngestPanel` posts to), not the course-specific one.
    """
    # /api/ingest snapshots the vault into git before writing (I2), unlike
    # /api/study/upload — this test's client fixture never git-inits its
    # vault, so this specific test does it itself.
    vault = tmp_path / "vault"
    subprocess.run(["git", "init"], cwd=vault, capture_output=True, check=True)
    subprocess.run(["git", "add", "-A"], cwd=vault, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"], cwd=vault, capture_output=True, check=True
    )

    course = client.get("/api/study/courses").json()[0]
    assert course["materials"] == 0

    response = client.post(
        "/api/ingest",
        data={"target": course["materials_path"]},
        files={"file": ("syllabus.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert response.status_code == 200

    updated = client.get("/api/study/courses").json()[0]
    assert updated["materials"] == 1, "materials must move off zero once the real target is used"


def test_course_sources_lists_non_markdown_materials(client: TestClient) -> None:
    """The bug: `GET /api/notes` only ever lists `*.md`, so an uploaded PDF/
    PPTX/DOCX could never appear in the Course Hub SOURCES rail even though
    it was really saved and indexed. This endpoint walks the real files."""
    client.post(
        "/api/study/upload",
        data={"course": "CS201"},
        files={"file": ("slides.pptx", b"fake pptx bytes", "application/octet-stream")},
    )

    sources = client.get("/api/study/courses/CS201/sources").json()
    by_name = {item["path"].rsplit("/", 1)[-1]: item for item in sources}

    assert "slides.pptx" in by_name, "a non-markdown material must be listed"
    assert by_name["slides.pptx"]["zone"] == "materials"
    assert by_name["slides.pptx"]["kind"] == "PPTX"
    # Not in the fixture's fake index -> None, not 0 (0 would falsely claim
    # "indexed, zero chunks" rather than "not indexed at all").
    assert by_name["slides.pptx"]["chunks"] is None


def test_course_sources_shape_is_unchanged(client: TestClient) -> None:
    """Regression guard for rebuilding course_sources on vault.sources.

    The Course Hub rail switches on ``zone`` (three usages in
    CourseSourcesPanel.tsx) and the TS type unions it, so the JSON this
    endpoint returns must not drift while its implementation is replaced.
    """
    client.post(
        "/api/study/upload",
        data={"course": "CS201"},
        files={"file": ("handout.pdf", b"fake pdf", "application/octet-stream")},
    )

    sources = client.get("/api/study/courses/CS201/sources").json()

    assert sources, "fixture course must have at least one file"
    for item in sources:
        assert set(item) == {"path", "title", "zone", "kind", "modified", "chunks"}
        assert item["zone"] in {"materials", "notes", "study"}


def test_course_sources_stays_flat(client: TestClient, tmp_path: Path) -> None:
    """course_sources walks each zone with iterdir, not rglob.

    The generic lister defaults to recursive; the course rail must not
    silently start showing `materials/week1/slides.pdf`, which it never has.
    """
    nested = tmp_path / "vault" / "15-Courses" / "CS201" / "materials" / "week1"
    nested.mkdir(parents=True, exist_ok=True)
    (nested / "deep.pdf").write_bytes(b"fake pdf")

    paths = {item["path"] for item in client.get("/api/study/courses/CS201/sources").json()}

    assert not any("week1" in path for path in paths)


def test_course_sources_hides_a_no_ai_note(client: TestClient, tmp_path: Path) -> None:
    """Behaviour change, deliberate: the rail used to list `#no-ai` notes.

    course_sources only ever checked directories, so a note tagged `#no-ai`
    inside a course folder was listed (and offered as retrieval context) even
    though I3 says it is never indexed or sent anywhere. Rebuilding on
    vault.sources, which uses the full `is_visible` predicate, closes that.
    """
    notes_dir = tmp_path / "vault" / "15-Courses" / "CS201" / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    (notes_dir / "private-thoughts.md").write_text(
        "---\ntags: [no-ai]\n---\n\n# Mine\n", encoding="utf-8"
    )
    (notes_dir / "shareable.md").write_text("# Fine\n", encoding="utf-8")

    paths = {item["path"] for item in client.get("/api/study/courses/CS201/sources").json()}

    assert not any("private-thoughts" in path for path in paths), "I3 violation"
    assert any("shareable" in path for path in paths)


def test_exam_generation_quiz_and_attempt_roundtrip(client: TestClient, tmp_path: Path) -> None:
    created = client.post("/api/study/exam", json={"course": "CS201", "n": 1}).json()
    assert created["questions"] == 1
    exam_path = tmp_path / "vault" / created["path"]
    assert exam_path.is_file(), "exam markdown must be written under study/"

    quiz = client.get(f"/api/study/exams/{created['exam_id']}").json()
    assert quiz[0]["q"].startswith("What is the complexity")
    assert "answer" not in quiz[0], "quiz payload must not leak answers"

    graded = client.post(
        f"/api/study/exams/{created['exam_id']}/attempt", json={"answers": ["O(log n)"]}
    ).json()
    assert graded["score"] == 1
    assert graded["feedback"][0]["citation"] == "algos.pdf p.12"

    listing = client.get("/api/study/exams", params={"course": "CS201"}).json()
    assert listing[0]["id"] == created["exam_id"]


def test_guide_written_with_gap_list(client: TestClient, tmp_path: Path) -> None:
    response = client.post("/api/study/guide", json={"course": "CS201", "scope": "midterm"}).json()
    guide = (tmp_path / "vault" / response["path"]).read_text(encoding="utf-8")
    assert "## Outline" in guide
    assert "haven't taken notes on" in guide, "gap list expected (no notes/ chunks in corpus)"


# --- generating from a hand-picked selection ----------------------------------


def test_a_guide_reads_only_the_selected_sources(client: TestClient) -> None:
    """ "Make a guide from just these lectures" has to mean it — the corpus the
    prompt is built from is narrowed, not merely labelled."""
    response = client.post(
        "/api/study/guide",
        json={"course": "CS201", "sources": ["15-Courses/CS201/materials/algos.pdf"]},
    )

    assert response.status_code == 200
    assert response.json()["path"].startswith("15-Courses/CS201/study/")


def test_selecting_only_unindexed_files_says_that_rather_than_upload_first(
    client: TestClient,
) -> None:
    """The generators' own message is "upload to materials/ first", which is
    wrong once a selection is in play — the file is already there, it is the
    selection that matched nothing."""
    response = client.post(
        "/api/study/guide",
        json={"course": "CS201", "sources": ["15-Courses/CS201/materials/not-indexed.pdf"]},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "selected" in detail
    assert "upload to materials/" not in detail


def test_selecting_nothing_is_refused_rather_than_widened_to_the_course(
    client: TestClient,
) -> None:
    response = client.post("/api/study/guide", json={"course": "CS201", "sources": []})

    assert response.status_code == 422
    assert "no sources are selected" in response.json()["detail"]


def test_omitting_sources_still_reads_the_whole_course(client: TestClient) -> None:
    """Every existing client sends no `sources` field at all."""
    assert client.post("/api/study/guide", json={"course": "CS201"}).status_code == 200


def test_an_exam_honours_the_selection_too(client: TestClient) -> None:
    scoped = client.post(
        "/api/study/exam",
        json={"course": "CS201", "n": 1, "sources": ["15-Courses/CS201/materials/algos.pdf"]},
    )
    assert scoped.status_code == 200

    empty = client.post(
        "/api/study/exam",
        json={"course": "CS201", "n": 1, "sources": ["15-Courses/CS201/materials/nope.pdf"]},
    )
    assert empty.status_code == 422
    assert "selected" in empty.json()["detail"]


# --- the upload path is a guarded, snapshotted write --------------------------


def test_upload_refuses_a_course_that_escapes_the_vault(client: TestClient, tmp_path: Path) -> None:
    """This used to be `destination.write_bytes` with no path guard at all."""
    response = client.post(
        "/api/study/upload",
        data={"course": "../../../etc"},
        files={"file": ("evil.md", b"x", "text/markdown")},
    )

    assert response.status_code == 404, "no such course folder — nothing is written"
    assert not (tmp_path / "vault" / ".." / "etc").exists()


def test_upload_takes_an_undo_point(client: TestClient, tmp_path: Path) -> None:
    """I2: the write is snapshot-first, so it is one `git revert` away."""
    vault = tmp_path / "vault"
    before = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    )

    client.post(
        "/api/study/upload",
        data={"course": "CS201"},
        files={"file": ("deck.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )

    after = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"],
        cwd=vault,
        capture_output=True,
        text=True,
        check=True,
    )
    assert int(after.stdout) > int(before.stdout)


def test_upload_dedupes_rather_than_overwriting(client: TestClient, tmp_path: Path) -> None:
    """The raw write clobbered whatever was already at that name."""
    for _ in range(2):
        client.post(
            "/api/study/upload",
            data={"course": "CS201"},
            files={"file": ("deck.pdf", b"%PDF-1.4 fake", "application/pdf")},
        )

    materials = tmp_path / "vault" / "15-Courses" / "CS201" / "materials"
    assert {path.name for path in materials.iterdir()} == {"deck.pdf", "deck-2.pdf"}
