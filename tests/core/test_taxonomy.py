"""Tests for backend.core.taxonomy: the configurable PARA folder names."""

from __future__ import annotations

import pytest

from backend.core.config import ConfigError
from backend.core.taxonomy import Taxonomy, active_taxonomy, set_active_taxonomy


def test_defaults_match_v0_2_constants() -> None:
    """Pins every field to Argus 0.2's hardcoded names — the regression guard
    for the whole refactor. If a *folder* name here ever needs to change, the
    refactor has broken byte-identical behaviour for existing installs."""
    tax = Taxonomy()

    assert tax.inbox == "00-Inbox"
    assert tax.daily == "10-Daily"
    assert tax.courses == "15-Courses"
    assert tax.projects == "20-Projects"
    assert tax.areas == "30-Areas"
    assert tax.people == "40-People"
    assert tax.reference == "50-Reference"
    assert tax.journal == "90-Meta"
    assert tax.private == "99-Private"
    # .eml is a deliberate addition to 0.2's set, not a refactor slip: the
    # ingest route already accepted .eml while nothing could index it, so a
    # dropped email saved and then yielded zero chunks. Widening the set is
    # additive — it can only make more files searchable, never fewer.
    assert tax.indexable_suffixes == {".md", ".pdf", ".pptx", ".docx", ".eml"}


# --- derived properties ------------------------------------------------------


def test_ingest_and_email_dirs_derive_from_inbox() -> None:
    tax = Taxonomy()
    assert tax.ingest_files == "00-Inbox/files"
    assert tax.inbox_emails == "00-Inbox/emails"


def test_excluded_top_dirs_includes_private_journal_and_dotdirs() -> None:
    tax = Taxonomy()
    assert tax.excluded_top_dirs == {
        "99-Private",
        "90-Meta",
        ".obsidian",
        ".argus",
        ".git",
        ".trash",
    }


def test_recency_dirs_are_daily_and_inbox_with_trailing_slash() -> None:
    assert Taxonomy().recency_dirs == ("10-Daily/", "00-Inbox/")


def test_course_helpers_build_expected_subpaths() -> None:
    tax = Taxonomy()
    assert tax.course_dir("CS301") == "15-Courses/CS301"
    assert tax.course_notes("CS301") == "15-Courses/CS301/notes"
    assert tax.course_materials("CS301") == "15-Courses/CS301/materials"
    assert tax.course_study("CS301") == "15-Courses/CS301/study"


def test_review_queue_glob_and_preferences_note() -> None:
    tax = Taxonomy()
    assert tax.review_queue_glob == "15-Courses/*/study/review-queue.md"
    assert tax.preferences_note == "30-Areas/assistant-preferences.md"


def test_seed_folders_replaces_cli_empty_folders() -> None:
    assert Taxonomy().seed_folders() == [
        "00-Inbox",
        "10-Daily",
        "15-Courses/CS000/notes",
        "15-Courses/CS000/materials",
        "15-Courses/CS000/study",
        "20-Projects",
        "30-Areas",
        "40-People",
        "50-Reference",
        "99-Private",
    ]


# --- from_env -----------------------------------------------------------------


def test_from_env_with_no_keys_yields_defaults() -> None:
    assert Taxonomy.from_env({}) == Taxonomy()


def test_from_env_overrides_only_the_given_fields() -> None:
    tax = Taxonomy.from_env({"VAULT_INBOX_DIR": "Inbox", "VAULT_PRIVATE_DIR": "Private"})

    assert tax.inbox == "Inbox"
    assert tax.private == "Private"
    assert tax.daily == "10-Daily"  # untouched fields keep the default


def test_from_env_blank_value_keeps_default() -> None:
    tax = Taxonomy.from_env({"VAULT_INBOX_DIR": "   "})
    assert tax.inbox == "00-Inbox"


def test_from_env_all_nine_keys() -> None:
    tax = Taxonomy.from_env(
        {
            "VAULT_INBOX_DIR": "Inbox",
            "VAULT_DAILY_DIR": "Daily",
            "VAULT_COURSES_DIR": "Courses",
            "VAULT_PROJECTS_DIR": "Projects",
            "VAULT_AREAS_DIR": "Areas",
            "VAULT_PEOPLE_DIR": "People",
            "VAULT_REFERENCE_DIR": "Reference",
            "VAULT_JOURNAL_DIR": "Journal",
            "VAULT_PRIVATE_DIR": "Private",
        }
    )
    assert tax == Taxonomy(
        inbox="Inbox",
        daily="Daily",
        courses="Courses",
        projects="Projects",
        areas="Areas",
        people="People",
        reference="Reference",
        journal="Journal",
        private="Private",
    )


def test_from_env_parses_indexable_suffixes() -> None:
    tax = Taxonomy.from_env({"VAULT_INDEXABLE_SUFFIXES": ".md, txt, .pdf"})
    assert tax.indexable_suffixes == {".md", ".txt", ".pdf"}


def test_from_env_does_not_reread_the_file() -> None:
    """from_env must only ever consume the dict handed to it."""
    # No filesystem access at all — passing a dict with irrelevant keys (as
    # parse_env_file would return for an unrelated env file) must not raise
    # or attempt any I/O.
    tax = Taxonomy.from_env({"VAULT_PATH": "/somewhere", "BACKEND_PORT": "9001"})
    assert tax == Taxonomy()


# --- validation ----------------------------------------------------------------


def test_empty_name_rejected() -> None:
    with pytest.raises(ConfigError, match="must not be empty"):
        Taxonomy(inbox="")


def test_whitespace_only_name_rejected() -> None:
    with pytest.raises(ConfigError, match="must not be empty"):
        Taxonomy(daily="   ")


@pytest.mark.parametrize("bad", ["a/b", "a\\b", "/leading", "trailing/"])
def test_separator_in_name_rejected(bad: str) -> None:
    with pytest.raises(ConfigError, match="single path segment"):
        Taxonomy(courses=bad)


def test_dotdot_in_name_rejected() -> None:
    with pytest.raises(ConfigError, match=r"\.\."):
        Taxonomy(projects="..")


def test_leading_dot_rejected() -> None:
    with pytest.raises(ConfigError, match="must not start with"):
        Taxonomy(areas=".hidden")


def test_duplicate_names_rejected() -> None:
    with pytest.raises(ConfigError, match="distinct"):
        Taxonomy(inbox="Shared", daily="Shared")


def test_aliasing_private_onto_another_zone_is_rejected() -> None:
    """The I3-critical case: private must never silently alias another zone."""
    with pytest.raises(ConfigError, match="distinct"):
        Taxonomy(private="20-Projects")


def test_empty_indexable_suffixes_rejected() -> None:
    with pytest.raises(ConfigError, match="indexable_suffixes must not be empty"):
        Taxonomy(indexable_suffixes=frozenset())


def test_suffix_without_leading_dot_rejected() -> None:
    with pytest.raises(ConfigError, match="must start with"):
        Taxonomy(indexable_suffixes=frozenset({"md"}))


# --- active_taxonomy / set_active_taxonomy ------------------------------------


def test_active_taxonomy_defaults_to_defaults() -> None:
    # tests/conftest.py resets this fixture-wide, so this reflects the
    # documented default rather than leakage from another test.
    assert active_taxonomy() == Taxonomy()


def test_set_active_taxonomy_changes_the_fallback() -> None:
    custom = Taxonomy(inbox="Capture")
    set_active_taxonomy(custom)
    try:
        assert active_taxonomy() == custom
        assert active_taxonomy().inbox == "Capture"
    finally:
        set_active_taxonomy(Taxonomy())
