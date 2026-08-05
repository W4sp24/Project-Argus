"""Tests for the shared I3 privacy predicate.

I3 says ``99-Private/`` and anything tagged ``#no-ai`` is never indexed and
never sent anywhere. This module is the one place that rule lives, so these
tests are the regression guard for every outward-facing read endpoint that
depends on it.

The case that matters most is a ``#no-ai`` note living *outside*
``99-Private/``: the path-only checks used elsewhere in the codebase
(``is_indexable``, ``list_notes``) miss it by design, and an endpoint that
reused only those would leak it.
"""

from __future__ import annotations

import frontmatter
import pytest

from backend.core.taxonomy import Taxonomy
from backend.vault.privacy import (
    NO_AI_TAG,
    is_no_ai,
    is_no_ai_text,
    is_private_path,
    is_visible,
)


def _post(body: str = "hello", **metadata) -> frontmatter.Post:
    post = frontmatter.Post(body)
    post.metadata.update(metadata)
    return post


# --- the #no-ai tag predicate -------------------------------------------------


@pytest.mark.parametrize(
    "tags",
    [
        ["no-ai"],
        ["#no-ai"],
        "no-ai",  # a bare string rather than a list
        "#no-ai",
        ["other", "no-ai"],
        ["  no-ai  "],  # whitespace around the tag
    ],
)
def test_no_ai_frontmatter_tag_is_detected(tags) -> None:
    assert is_no_ai(_post(tags=tags)) is True


def test_no_ai_mentioned_in_the_body_is_detected() -> None:
    assert is_no_ai(_post("private thoughts #no-ai here")) is True


def test_ordinary_note_is_not_no_ai() -> None:
    assert is_no_ai(_post("just a note", tags=["project", "argus"])) is False


def test_missing_and_empty_tags_are_tolerated() -> None:
    assert is_no_ai(_post("body")) is False
    assert is_no_ai(_post("body", tags=[])) is False
    assert is_no_ai(_post("body", tags=None)) is False


def test_non_string_tags_do_not_raise() -> None:
    """Frontmatter is user-authored; a stray int must not 500 a read endpoint."""
    assert is_no_ai(_post("body", tags=[1, 2.5, None])) is False


def test_is_no_ai_text_matches_the_post_variant() -> None:
    assert is_no_ai_text({"tags": ["no-ai"]}, "body") is True
    assert is_no_ai_text({}, "mentions #no-ai") is True
    assert is_no_ai_text({}, "clean") is False


def test_tag_constant_is_the_documented_one() -> None:
    assert NO_AI_TAG == "no-ai"


# --- the directory predicate --------------------------------------------------


@pytest.mark.parametrize(
    "rel_path",
    [
        "99-Private/secret.md",
        "99-Private/nested/deep/secret.md",
        "90-Meta/sessions/2026/x.md",
        ".obsidian/config.json",
        ".argus/automations.json",
        ".git/HEAD",
        ".trash/old.md",
    ],
)
def test_protected_zones_are_private(rel_path: str) -> None:
    assert is_private_path(rel_path) is True


@pytest.mark.parametrize(
    "rel_path",
    ["20-Projects/argus.md", "10-Daily/2026-08-05.md", "notes.md"],
)
def test_ordinary_paths_are_not_private(rel_path: str) -> None:
    assert is_private_path(rel_path) is False


def test_windows_separators_are_handled() -> None:
    """Paths reach this from both os.walk and API payloads, so both separators."""
    assert is_private_path("99-Private\\secret.md") is True


def test_private_dir_follows_a_custom_taxonomy() -> None:
    """The zone name is configurable; the predicate must not hardcode it."""
    tax = Taxonomy(private="Confidential")
    assert is_private_path("Confidential/x.md", taxonomy=tax) is True
    assert is_private_path("99-Private/x.md", taxonomy=tax) is False


# --- the full check, which is what outward-facing endpoints must use ----------


def test_visible_note_passes_both_halves() -> None:
    assert is_visible("20-Projects/argus.md", _post("public")) is True


def test_private_directory_is_not_visible() -> None:
    assert is_visible("99-Private/secret.md", _post("public body")) is False


def test_no_ai_note_outside_the_private_directory_is_not_visible() -> None:
    """The case path-only filtering misses — and the reason is_visible exists.

    ``is_indexable``/``list_notes`` check the directory only, so this note
    passes them. Any endpoint that serves data outward must use the full
    check or it leaks a note the user explicitly marked off-limits.
    """
    assert is_private_path("20-Projects/notes.md") is False  # path half says fine
    assert is_visible("20-Projects/notes.md", _post("x", tags=["no-ai"])) is False


def test_no_ai_in_the_body_outside_a_private_directory_is_not_visible() -> None:
    assert is_visible("10-Daily/2026-08-05.md", _post("thinking #no-ai aloud")) is False
