"""What an ingested file's note says about the rest of the vault.

Two halves, because the feature has two. :func:`note_markdown` is where a
model's ``## Topics`` list becomes links, and it is testable with nothing but
a dict for a resolver. The pipeline half is here because the link index and
the neighbour query are the two things ``note_markdown`` cannot supply itself
-- and a note whose concepts never resolved looks exactly like a note whose
concepts had nothing to resolve to, so the wiring needs evidence of its own.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import frontmatter
import pytest

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.core.taxonomy import Taxonomy
from backend.features.ingest import notes as note_styles
from backend.features.ingest import pipeline, store
from backend.features.ingest.pipeline import run_ingest_job


def _resolver(mapping: dict[str, str]):
    """A ``LinkIndex.resolve`` stand-in: name -> vault-relative path, or None."""
    return lambda name: mapping.get(name)


BODY = (
    "Kavanaugh contrasts Skinner and Sartre.\n\n"
    "## Key points\n\n- a point\n\n"
    "## Topics\n\n- Determinism\n- **Free Will**\n"
)


# --- the note itself ---------------------------------------------------------


def test_the_topics_section_becomes_links_not_a_second_list():
    _, markdown = note_styles.note_markdown(
        "15-Courses/ETHICS/materials/wk1.pdf",
        note_styles.NOTE_STYLES["summary"],
        "",
        BODY,
        resolve=_resolver({"Determinism": "60-Knowledge/General/Determinism.md"}),
        neighbours=[("15-Courses/ETHICS/notes/sartre.notes.md", "Sartre")],
    )
    post = frontmatter.loads(markdown)
    assert "## Topics" not in post.content
    assert "[[60-Knowledge/General/Determinism|Determinism]]" in post.content
    assert "[[Free Will]]" in post.content
    assert "[[15-Courses/ETHICS/notes/sartre.notes|Sartre]]" in post.content


def test_the_source_link_resolves_to_the_pdf_rather_than_to_nothing():
    """The pre-existing bug: ``[[wk1]]`` for ``wk1.pdf`` is a hollow node."""
    _, markdown = note_styles.note_markdown(
        "15-Courses/ETHICS/materials/wk1.pdf",
        note_styles.NOTE_STYLES["summary"],
        "",
        BODY,
    )
    assert "[[15-Courses/ETHICS/materials/wk1.pdf|wk1]]" in markdown
    assert "\n[[wk1]]\n" not in markdown


def test_frontmatter_carries_topics_tags_and_related():
    _, markdown = note_styles.note_markdown(
        "15-Courses/ETHICS/materials/wk1.pdf",
        note_styles.NOTE_STYLES["summary"],
        "",
        BODY,
        resolve=_resolver({}),
    )
    post = frontmatter.loads(markdown)
    assert post["topics"] == ["Determinism", "Free Will"]
    assert "argus/note" in post["tags"]
    assert "course/ETHICS" in post["tags"]
    assert "topic/free-will" in post["tags"]
    assert post["generated_by"] == "argus"
    assert post["source"] == "15-Courses/ETHICS/materials/wk1.pdf"


def test_a_model_that_ignores_the_tail_still_produces_todays_note():
    """The degradation path. No topics, no crash, still a source and a course."""
    _, markdown = note_styles.note_markdown(
        "15-Courses/ETHICS/materials/wk1.pdf",
        note_styles.NOTE_STYLES["summary"],
        "",
        "Just a summary with no topics section.\n",
    )
    post = frontmatter.loads(markdown)
    assert "topics" not in post.metadata
    assert "Just a summary" in post.content
    assert "[[15-Courses/ETHICS/course|ETHICS]]" in post.content


def test_a_caller_with_no_link_index_still_gets_its_concepts_hollow():
    """``resolve`` defaults to "nothing known", which is what keeps every
    pre-existing caller and test working unedited."""
    _, markdown = note_styles.note_markdown("00-Inbox/files/essay.pdf", None, "", BODY)
    post = frontmatter.loads(markdown)
    assert "[[Determinism]]" in post.content
    assert "[[Free Will]]" in post.content
    assert not [tag for tag in post["tags"] if tag.startswith("course/")]


def test_the_course_link_follows_a_renamed_courses_zone():
    """No hardcoded ``15-Courses``: the course link goes through the taxonomy,
    the same as the note's destination already does."""
    tax = Taxonomy(courses="Modules")
    destination, markdown = note_styles.note_markdown(
        "Modules/ETHICS/materials/wk1.pdf",
        note_styles.NOTE_STYLES["summary"],
        "",
        BODY,
        taxonomy=tax,
    )
    assert destination == "Modules/ETHICS/notes/wk1.notes.md"
    assert "[[Modules/ETHICS/course|ETHICS]]" in markdown


def test_the_prompt_asks_for_the_topics_section():
    prompt = note_styles.build_prompt(
        note_styles.NOTE_STYLES["cornell"], "", "a/b.pdf", "text"
    )
    assert "## Topics" in prompt


# --- the wiring that supplies what the note cannot know for itself -----------


class _FakeIndex:
    """Records upserts; never loads an embedding model."""

    def __init__(self) -> None:
        self.upserts: list[str] = []

    def upsert_file(self, vault_path: Path, rel_path: str) -> int:
        self.upserts.append(rel_path)
        return 3


class _FakeGenerator:
    def __init__(self, reply: str = BODY) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def __call__(self, prompt: str, model: str | None = None) -> str:
        self.prompts.append(prompt)
        return self.reply


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "00-Inbox" / "files").mkdir(parents=True)
    (root / "10-Daily").mkdir()
    (root / "60-Knowledge" / "General").mkdir(parents=True)
    (root / "60-Knowledge" / "General" / "Determinism.md").write_text(
        "---\ntitle: Determinism\n---\n\nA concept note.\n", encoding="utf-8"
    )
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


def _run(settings, conn, tmp_path, files: dict[str, bytes], *, generator):
    job_id = store.create_job(
        conn,
        target="00-Inbox/files",
        summary_prompt="summarise",
        filenames=list(files),
        note_style="",
    )
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    for ordinal, (name, data) in enumerate(files.items()):
        (staging / f"{ordinal}__{name}").write_bytes(data)
    index = _FakeIndex()
    run_ingest_job(
        job_id,
        settings=settings,
        index_factory=lambda: index,
        generator=generator,
        staging_dir=staging,
    )
    return job_id, index


def test_a_job_resolves_concepts_against_the_vault_it_is_writing_into(
    settings, conn, tmp_path, vault
):
    """The point of the task: a note's concepts are real links, and they are
    real only because the pipeline handed ``note_markdown`` a resolver."""
    _run(
        settings,
        conn,
        tmp_path,
        {"essay.md": b"# Essay\n\nOn choice.\n"},
        generator=_FakeGenerator(),
    )

    written = (vault / "00-Inbox" / "files" / "essay.summary.md").read_text(encoding="utf-8")
    assert "[[60-Knowledge/General/Determinism|Determinism]]" in written
    assert "[[Free Will]]" in written, "an unresolved concept stays bare"


def test_the_link_index_is_built_once_per_job_not_once_per_file(
    settings, conn, tmp_path, vault, monkeypatch
):
    """``build_link_index`` rglobs the vault and parses every note's
    frontmatter. Per file, a twenty-file ingest is twenty vault walks."""
    calls: list[Path] = []
    real = pipeline.build_link_index

    def _counted(vault_path, **kwargs):
        calls.append(vault_path)
        return real(vault_path, **kwargs)

    monkeypatch.setattr(pipeline, "build_link_index", _counted)
    _run(
        settings,
        conn,
        tmp_path,
        {"a.md": b"# A\n\nOne.\n", "b.md": b"# B\n\nTwo.\n"},
        generator=_FakeGenerator(),
    )
    assert len(calls) == 1


def test_a_dead_link_index_costs_the_links_not_the_job(
    settings, conn, tmp_path, vault, monkeypatch
):
    """A note with hollow concept links is a worse note, not a lost one."""

    def _boom(*args, **kwargs):
        raise RuntimeError("the vault walk exploded")

    monkeypatch.setattr(pipeline, "build_link_index", _boom)
    job_id, _ = _run(
        settings,
        conn,
        tmp_path,
        {"essay.md": b"# Essay\n\nOn choice.\n"},
        generator=_FakeGenerator(),
    )

    assert store.get_job(conn, job_id)["status"] == "ok"
    written = (vault / "00-Inbox" / "files" / "essay.summary.md").read_text(encoding="utf-8")
    assert "[[Determinism]]" in written
    assert "60-Knowledge" not in written


def test_the_neighbour_query_never_offers_the_note_itself_or_its_source(
    settings, conn, tmp_path, vault, monkeypatch
):
    """Both would be links the reader already has: the note is the note, and
    the source is rendered separately as its own line."""
    seen: dict = {}

    def _capture(index, vault_path, text, **kwargs):
        seen.update(kwargs)
        seen["text"] = text
        return [("60-Knowledge/General/Determinism.md", "Determinism")]

    monkeypatch.setattr(pipeline, "nearest_notes", _capture)
    _run(
        settings,
        conn,
        tmp_path,
        {"essay.md": b"# Essay\n\nOn choice.\n"},
        generator=_FakeGenerator(),
    )

    assert seen["exclude"] == {"00-Inbox/files/essay.md", "00-Inbox/files/essay.summary.md"}
    assert seen["taxonomy"] is settings.taxonomy
    written = (vault / "00-Inbox" / "files" / "essay.summary.md").read_text(encoding="utf-8")
    assert "[[60-Knowledge/General/Determinism|Determinism]]" in written
