"""Wikilink target resolution: one vault walk builds a stem/path/alias index.

``backend.rag.retrieve``'s link expansion used to resolve every ``[[Note]]``
with ``vault_path.rglob(f"{name}.md")`` — a full-vault directory walk, once
per link, per query — and had no way to resolve ``[[Note|alias]]`` display
text (the alias half is already stripped by the wikilink regex before it gets
here, so that part "just worked"), ``[[folder/Note]]`` path-qualified links,
or a note's frontmatter ``aliases:``. :func:`build_link_index` walks the
vault exactly once and returns a :class:`LinkIndex` that resolves any of
those forms with a dict lookup.

Ambiguity rule
--------------
Multiple files can share a stem or an alias (two notes both named
``Overview.md`` in different folders, or two notes both claiming the same
frontmatter alias). When :meth:`LinkIndex.resolve` has more than one
candidate for a name, it picks, in order:

1. a candidate in the *same folder* as the linking note — mirrors how a
   reader skimming ``[[Overview]]`` from inside a folder expects "the
   Overview note next to this one" unless told otherwise;
2. otherwise the candidate with the shortest vault-relative path (fewest
   characters), ties broken alphabetically so the choice is deterministic
   and reproducible across runs.

This is a deliberately simple, documented stand-in for Obsidian's own
(unspecified) resolver — good enough for citations, and testable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import frontmatter

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.vault.paths import is_indexable


@dataclass
class LinkIndex:
    """Vault-relative-path lookup for wikilink targets, built once per walk."""

    by_stem: dict[str, list[str]] = field(default_factory=dict)
    by_path_no_suffix: dict[str, list[str]] = field(default_factory=dict)
    by_alias: dict[str, list[str]] = field(default_factory=dict)

    def resolve(self, name: str, *, from_path: str) -> str | None:
        """Resolve a wikilink target to a vault-relative path, or ``None``.

        ``name`` is the link target *after* ``|alias`` and ``#heading`` have
        already been stripped (``rag/chunk.py``'s ``WIKILINK_RE`` does that
        before this is ever called) — e.g. ``"Note"``, ``"folder/Note"``, or
        an alias string. A name that resolves into the private zone (or any
        other excluded dir) was never added to this index in the first
        place, so it correctly resolves to ``None`` (invariant I3).
        """
        name = name.strip()
        if not name:
            return None
        candidates = (
            self.by_path_no_suffix.get(name)
            or self.by_alias.get(name)
            or self.by_stem.get(name.rsplit("/", 1)[-1])
        )
        if not candidates:
            return None
        return _pick(candidates, from_path)


def _pick(candidates: list[str], from_path: str) -> str:
    from_dir = from_path.rsplit("/", 1)[0] if "/" in from_path else ""
    same_dir = [c for c in candidates if (c.rsplit("/", 1)[0] if "/" in c else "") == from_dir]
    pool = same_dir or candidates
    return sorted(pool, key=lambda path: (len(path), path))[0]


def build_link_index(vault_path: Path, *, taxonomy: Taxonomy | None = None) -> LinkIndex:
    """Walk the vault once and index every markdown note by stem/path/alias.

    Only markdown notes are indexable wikilink *targets* (``[[...]]`` always
    points at a note, never a PDF/PPTX page), and only files
    :func:`~backend.vault.paths.is_indexable` allows: a file under the
    taxonomy's private zone, journal, or any other excluded dir is skipped
    here exactly as it is skipped by RAG indexing — so a wikilink pointing
    into the private zone has no entry to resolve to, never gets followed,
    and invariant I3 (private content never reaches a model) holds for link
    expansion the same way it holds for the index itself.
    """
    tax = taxonomy or active_taxonomy()
    index = LinkIndex()
    for file_path in vault_path.rglob("*.md"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(vault_path).as_posix()
        if not is_indexable(rel, taxonomy=tax):
            continue
        stem = file_path.stem
        no_suffix = rel[: -len(file_path.suffix)]
        index.by_stem.setdefault(stem, []).append(rel)
        index.by_path_no_suffix.setdefault(no_suffix, []).append(rel)
        try:
            post = frontmatter.load(file_path)
        except Exception:
            continue
        aliases = post.metadata.get("aliases") or []
        if isinstance(aliases, str):
            aliases = [aliases]
        for alias in aliases:
            alias_name = str(alias).strip()
            if alias_name:
                index.by_alias.setdefault(alias_name, []).append(rel)
    return index
