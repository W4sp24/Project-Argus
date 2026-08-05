"""The I3 boundary for the outward-facing surface.

Everything the local API serves is already directory-filtered: the task cache
(:func:`backend.vault.tasks.refresh_cache`), note listing and the RAG index all
skip ``99-Private/`` and the other protected zones via
:attr:`backend.core.taxonomy.Taxonomy.excluded_top_dirs`.

That is **not sufficient here**, and the gap is concrete rather than theoretical:
those callers check the directory only, so a note tagged ``#no-ai`` living
*outside* ``99-Private/`` is indexed, cached and listed today. Locally that is a
known trade-off. On a surface reachable from the public internet it is a leak,
because ``#no-ai`` is the user saying "never send this anywhere" about a note
they deliberately left in a normal folder.

So every read endpoint routes its results through this module, which applies the
**full** check from :mod:`backend.vault.privacy` — directory *and* tag. Tasks
carry the vault path of the note they were parsed out of, which is what makes
the tag half checkable at all without re-scanning the vault.
"""

from __future__ import annotations

from pathlib import Path

import frontmatter

from backend.core.taxonomy import Taxonomy
from backend.vault.privacy import is_no_ai, is_private_path
from backend.vault.tasks import TaskItem


class VisibilityCache:
    """Per-request memo of "is this vault note safe to expose?".

    An agenda can hold dozens of tasks parsed out of a handful of notes, and
    the naive implementation re-reads and re-parses the same file once per
    task. The cache is deliberately request-scoped rather than global: a note
    that gains a ``#no-ai`` tag must take effect on the very next request, not
    whenever a long-lived process happens to evict it.
    """

    def __init__(self, vault_path: Path, *, taxonomy: Taxonomy | None = None) -> None:
        self._vault_path = vault_path
        self._taxonomy = taxonomy
        self._seen: dict[str, bool] = {}

    def is_visible(self, rel_path: str | None) -> bool:
        """True when a note at ``rel_path`` may leave this machine.

        A task with no recorded path (one supplied by a connector rather than
        parsed from the vault) has no note to check and is not vault content,
        so it passes.
        """
        if not rel_path:
            return True
        cached = self._seen.get(rel_path)
        if cached is not None:
            return cached

        visible = self._compute(rel_path)
        self._seen[rel_path] = visible
        return visible

    def _compute(self, rel_path: str) -> bool:
        if is_private_path(rel_path, taxonomy=self._taxonomy):
            return False
        full_path = self._vault_path / rel_path
        try:
            post = frontmatter.load(full_path)
        except (OSError, ValueError, UnicodeDecodeError):
            # Unreadable or unparseable: fail CLOSED. On a public surface the
            # safe default for "I cannot tell what this note says" is to not
            # send it. Locally an unreadable note is a shrug; here it is the
            # difference between a leak and a missing row.
            return False
        return not is_no_ai(post)


def visible_tasks(
    tasks: list[TaskItem],
    vault_path: Path,
    *,
    taxonomy: Taxonomy | None = None,
    cache: VisibilityCache | None = None,
) -> list[TaskItem]:
    """Drop every task whose source note must never leave this machine."""
    memo = cache or VisibilityCache(vault_path, taxonomy=taxonomy)
    return [task for task in tasks if memo.is_visible(task.path)]
