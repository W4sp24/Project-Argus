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
