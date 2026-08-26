"""I3 at the summarisation boundary.

Summarising is the first thing in Argus that reads a vault file's text purely
in order to send it to a model. Until now the only LLM-on-ingest path was the
email extractor, which reads text the user had just pasted in.

Nothing else protects this. `guard_user_path` governs where a file may be
*written*, not whose text may be read; `_extract_markdown` returning [] for
`#no-ai` guards the *index*, not the generator. So without this gate, a note
tagged `#no-ai` sitting outside `99-Private/` gets its full text posted to
whichever provider is configured -- which for a hosted model means it leaves
the machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.core.taxonomy import Taxonomy
from backend.features.ingest.pipeline import summary_is_permitted


@pytest.fixture()
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "00-Inbox" / "files").mkdir(parents=True)
    (root / "99-Private").mkdir()
    return root


def test_an_ordinary_note_may_be_summarised(vault: Path) -> None:
    rel = "00-Inbox/files/lecture.md"
    (vault / rel).write_text("# Lecture\n\nOrdinary content.\n", encoding="utf-8")

    assert summary_is_permitted(vault, rel, "Ordinary content.") is True


def test_frontmatter_no_ai_is_refused(vault: Path) -> None:
    rel = "00-Inbox/files/tagged.md"
    (vault / rel).write_text("---\ntags: [no-ai]\n---\n\n# Mine\n", encoding="utf-8")

    assert summary_is_permitted(vault, rel, "# Mine") is False


def test_inline_no_ai_in_the_body_is_refused(vault: Path) -> None:
    rel = "00-Inbox/files/inline.md"
    (vault / rel).write_text("# Notes\n\nkeep this out #no-ai\n", encoding="utf-8")

    assert summary_is_permitted(vault, rel, "keep this out #no-ai") is False


def test_a_file_in_the_private_zone_is_refused(vault: Path) -> None:
    rel = "99-Private/secret.md"
    (vault / rel).write_text("# Secret\n", encoding="utf-8")

    assert summary_is_permitted(vault, rel, "# Secret") is False


def test_no_ai_inside_a_binary_is_refused_from_its_extracted_text(vault: Path) -> None:
    """A PDF has no frontmatter, so the tag can only be caught in its text.

    This is the gap the dev journal flagged: `_extract_pdf` has no privacy
    gate at all, so `#no-ai` typed on a slide was honoured nowhere.
    """
    rel = "00-Inbox/files/deck.pdf"
    (vault / rel).write_bytes(b"%PDF-1.4 fake")

    assert summary_is_permitted(vault, rel, "Slide 1\n#no-ai\nconfidential") is False
    assert summary_is_permitted(vault, rel, "Slide 1\nordinary deck") is True


def test_a_broken_header_falls_back_to_scanning_the_raw_text(vault: Path) -> None:
    """Matches backend.agent.runtime._note_is_visible, deliberately.

    A parse failure is not a privacy verdict: failing closed would make a note
    with a YAML typo silently stop being summarised, for a reason the user
    cannot see. So the tag is looked for in the raw text instead -- and it is
    still honoured when it is there.
    """
    tagged = "00-Inbox/files/broken-private.md"
    (vault / tagged).write_text("---\ntags: [unclosed\n---\n\n#no-ai\n", encoding="utf-8")
    plain = "00-Inbox/files/broken-plain.md"
    (vault / plain).write_text("---\ntags: [unclosed\n---\n\n# Body\n", encoding="utf-8")

    assert summary_is_permitted(vault, tagged, "#no-ai") is False
    assert summary_is_permitted(vault, plain, "# Body") is True


def test_a_missing_file_is_refused(vault: Path) -> None:
    assert summary_is_permitted(vault, "00-Inbox/files/gone.md", "text") is False


def test_honours_a_custom_private_zone(vault: Path) -> None:
    (vault / "Vault-Private").mkdir()
    rel = "Vault-Private/secret.md"
    (vault / rel).write_text("# S\n", encoding="utf-8")

    permitted = summary_is_permitted(
        vault, rel, "# S", taxonomy=Taxonomy(private="Vault-Private")
    )

    assert permitted is False
