"""Configurable PARA-style vault folder names (the "taxonomy").

Argus originally hardcoded nine folder names (``00-Inbox``, ``10-Daily``, ...)
across the backend, assuming every vault follows its own PARA-flavoured
layout. A user pointing Argus at a pre-existing Obsidian vault with different
folder names got broken features: quick capture writing into a folder that
doesn't exist, the private-zone guard (I3) protecting the wrong folder,
course discovery scanning a folder nobody has, and so on.

``Taxonomy`` is the single source of truth for these nine names (plus the set
of file suffixes RAG will index). Every field's **default is exactly the
historical hardcoded value**, so an install with no ``VAULT_*_DIR`` keys in
its env file behaves byte-identically to before this module existed — that
property is what makes this refactor safe to land in one branch, and it is
pinned by ``tests/core/test_taxonomy.py::test_defaults_match_v0_2_constants``.

Process-level default (read this before reaching for it)
----------------------------------------------------------
:func:`active_taxonomy` / :func:`set_active_taxonomy` hold one mutable
module-level ``Taxonomy`` as the fallback for code that only has a
``vault_path: Path`` in scope, not a ``Settings``. This is a real cost — it
is global mutable state, and in theory two vaults with different taxonomies
loaded in the same process would stomp on each other. It is accepted here for
two reasons: (1) it is *exactly* the shape the code already had — nine
module-level constants, one per file that needed one — so this refactor adds
no new failure mode, just a configurable version of the one that already
existed; and (2) it keeps the diff off every call site's signature: a
function that already took only ``vault_path`` gains one optional trailing
keyword (``taxonomy: Taxonomy | None = None``) instead of threading
``Settings`` — and therefore the whole layering — through modules that were
never meant to import it (see README's dependency rule: ``vault/`` and
``rag/`` sit below ``core/``, not above it).

Mitigations: every call site that already has a ``Settings`` in scope passes
``taxonomy=settings.taxonomy`` explicitly, so in practice the global is only
ever consulted as the documented default. ``Settings.load`` calls
:func:`set_active_taxonomy`, so the taxonomy from the most recently loaded
settings (normally the only one in a process) is what the fallback resolves
to. Tests reset the global between tests via the autouse fixture in
``tests/conftest.py`` — without it, a test that loads a custom taxonomy would
leak it into whichever test runs next.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_DEFAULT_SUFFIXES = frozenset({".md", ".pdf", ".pptx", ".docx"})

# field name -> its VAULT_*_DIR env key (backend/core/config.py:28-43 parses
# the env file into a dict; from_env() below only ever reads that dict).
_FIELD_ENV_KEYS = {
    "inbox": "VAULT_INBOX_DIR",
    "daily": "VAULT_DAILY_DIR",
    "courses": "VAULT_COURSES_DIR",
    "projects": "VAULT_PROJECTS_DIR",
    "areas": "VAULT_AREAS_DIR",
    "people": "VAULT_PEOPLE_DIR",
    "reference": "VAULT_REFERENCE_DIR",
    "journal": "VAULT_JOURNAL_DIR",
    "private": "VAULT_PRIVATE_DIR",
}


def _config_error(message: str) -> Exception:
    """Build a :class:`~backend.core.config.ConfigError`, imported lazily.

    ``backend.core.config`` imports this module at the top level (``Settings``
    needs ``Taxonomy`` as a real class object, not just a string annotation),
    so importing ``ConfigError`` back at *this* module's top level would be a
    cycle. Deferring it — and only actually performing the import on an error
    path, never on the default-construction path every import of this module
    exercises — breaks that cycle without breaking the type.
    """
    from backend.core.config import ConfigError

    return ConfigError(message)


def _validate_name(field_name: str, value: str) -> None:
    """A taxonomy name must be exactly one path segment.

    No separators (so it can't smuggle in a sub-path), no ``..`` (so it can't
    traverse), no leading dot (so it can't collide with ``.obsidian``/``.argus``
    dotdir semantics). Enforced independently of the OS path separator since
    a Windows-authored ``.env`` should not be allowed to write ``\\`` either.
    """
    if not value or not value.strip():
        raise _config_error(f"taxonomy.{field_name} must not be empty")
    if "/" in value or "\\" in value:
        raise _config_error(
            f"taxonomy.{field_name} ({value!r}) must be a single path segment, not a path"
        )
    if ".." in value:
        raise _config_error(f"taxonomy.{field_name} ({value!r}) must not contain '..'")
    if value.startswith("."):
        raise _config_error(f"taxonomy.{field_name} ({value!r}) must not start with '.'")


@dataclass(frozen=True)
class Taxonomy:
    """The nine configurable vault zones, plus which file suffixes RAG indexes.

    Field defaults are exactly Argus 0.2's hardcoded folder names — see the
    module docstring for why that must never change. Every caller that builds
    a vault-relative path should go through one of the derived properties
    below rather than concatenating a field itself.
    """

    inbox: str = "00-Inbox"
    daily: str = "10-Daily"
    courses: str = "15-Courses"
    projects: str = "20-Projects"
    areas: str = "30-Areas"
    people: str = "40-People"
    reference: str = "50-Reference"
    journal: str = "90-Meta"
    private: str = "99-Private"
    indexable_suffixes: frozenset[str] = field(default_factory=lambda: _DEFAULT_SUFFIXES)

    def __post_init__(self) -> None:
        names = {name: getattr(self, name) for name in _FIELD_ENV_KEYS}
        for name, value in names.items():
            _validate_name(name, value)
        if len(set(names.values())) != len(names):
            # A duplicate would silently alias two zones — and aliasing
            # `private` onto anything else breaches invariant I3.
            raise _config_error(
                "taxonomy folder names must all be distinct — a duplicate would silently "
                "alias two zones (e.g. private/ onto another folder, breaching I3)"
            )
        if not self.indexable_suffixes:
            raise _config_error("taxonomy.indexable_suffixes must not be empty")
        for suffix in self.indexable_suffixes:
            if not suffix.startswith("."):
                raise _config_error(
                    f"taxonomy.indexable_suffixes entries must start with '.': {suffix!r}"
                )

    # --- derived properties: nothing outside this module concatenates paths ---

    @property
    def ingest_files(self) -> str:
        """Where uploaded files land by default (``vault/writer.save_ingest_file``)."""
        return f"{self.inbox}/files"

    @property
    def inbox_emails(self) -> str:
        """Where captured emails are archived (``vault/writer.archive_email``)."""
        return f"{self.inbox}/emails"

    @property
    def excluded_top_dirs(self) -> frozenset[str]:
        """Never indexed, never listed, never directly writable (I3 + dev journal + dotdirs)."""
        return frozenset({self.private, self.journal, ".obsidian", ".argus", ".git", ".trash"})

    @property
    def recency_dirs(self) -> tuple[str, str]:
        """Path prefixes whose chunks get the retrieval recency boost."""
        return (f"{self.daily}/", f"{self.inbox}/")

    def course_dir(self, code: str) -> str:
        return f"{self.courses}/{code}"

    def course_notes(self, code: str) -> str:
        return f"{self.course_dir(code)}/notes"

    def course_materials(self, code: str) -> str:
        return f"{self.course_dir(code)}/materials"

    def course_study(self, code: str) -> str:
        return f"{self.course_dir(code)}/study"

    @property
    def review_queue_glob(self) -> str:
        """Glob (relative to the vault root) for every course's review queue."""
        return f"{self.courses}/*/study/review-queue.md"

    @property
    def preferences_note(self) -> str:
        return f"{self.areas}/assistant-preferences.md"

    def seed_folders(self) -> list[str]:
        """Empty folders ``argus init`` creates — replaces the old ``cli.EMPTY_FOLDERS``.

        CS000 is a sample course seeded on every fresh vault (removing it is a
        later branch's job; this just expresses the same three entries through
        the taxonomy so a renamed courses dir still gets the sample in the
        right place).
        """
        return [
            self.inbox,
            self.daily,
            self.course_notes("CS000"),
            self.course_materials("CS000"),
            self.course_study("CS000"),
            self.projects,
            self.areas,
            self.people,
            self.reference,
            self.private,
        ]

    @classmethod
    def from_env(cls, values: dict[str, str]) -> Taxonomy:
        """Build from the dict :func:`backend.core.config.parse_env_file` returns.

        Every key is optional; an absent or blank key keeps that field's
        default. Never re-reads the env file — ``values`` is already parsed.
        """
        kwargs: dict[str, object] = {}
        for field_name, env_key in _FIELD_ENV_KEYS.items():
            raw = values.get(env_key)
            if raw and raw.strip():
                kwargs[field_name] = raw.strip()
        raw_suffixes = values.get("VAULT_INDEXABLE_SUFFIXES")
        if raw_suffixes and raw_suffixes.strip():
            kwargs["indexable_suffixes"] = frozenset(
                part if part.startswith(".") else f".{part}"
                for part in (item.strip().lower() for item in raw_suffixes.split(","))
                if part
            )
        return cls(**kwargs)


_ACTIVE = Taxonomy()


def active_taxonomy() -> Taxonomy:
    """The process-level fallback taxonomy — see the module docstring."""
    return _ACTIVE


def set_active_taxonomy(taxonomy: Taxonomy) -> None:
    """Set the process-level fallback; called by :meth:`Settings.load`."""
    global _ACTIVE
    _ACTIVE = taxonomy
