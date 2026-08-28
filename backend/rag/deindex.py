"""Removing a deleted file's chunks — the half of a delete that was missing.

:meth:`backend.rag.index.VaultIndex.delete_file` existed from the start, but
until this module its only production caller was ``upsert_file``, as that
method's internal delete-then-add step. Nothing called it when a file was
*deleted*, so every delete path in the app left the file's chunks behind:
``DELETE /api/note`` unlinked a note, a course purge ``rmtree``'d a whole
course, and both left chunks that retrieval kept scoring and chat kept citing.
``reindex_all`` does not repair them either — it walks the files that exist,
so a path that no longer exists is never visited and its chunks survive a full
rebuild, disappearing only when a ``SCHEMA_VERSION`` bump recreates the whole
collection.

Two rules hold for every caller here:

* **The index never fails the delete.** The file being gone is the user's
  intent, and a missing ``[rag]`` extra (or a broken chroma directory) is not
  a reason to refuse it. Every failure is logged and swallowed, the same
  posture ``/api/sources`` already takes around ``chunk_counts()``.
* **Chunks go last.** A crash between the unlink and this leaves a file that
  is gone but still searchable — annoying, and repairable by a reindex of that
  path. The opposite order would leave a file that exists and is unsearchable
  for no visible reason.

Counts come from one ``chunk_counts()`` call rather than one per path: it is
the same metadata-only ``get`` the source listing already makes, and asking
per file would turn a batch delete into N full scans of the collection.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger("argus.rag")


def _resolve(index_factory: Callable[[], Any] | None) -> tuple[Any | None, dict[str, int]]:
    """The index and its ``{path: chunks}`` map, or ``(None, {})``.

    ``None`` means "there is no index to ask" — no ``[rag]`` extras, no
    chroma directory, or a factory that raised. The caller carries on
    regardless; see this module's docstring.
    """
    if index_factory is None:
        return None, {}
    try:
        index = index_factory()
        counts = dict(index.chunk_counts())
    except Exception as exc:
        logger.warning("de-index: index unavailable, chunks were left behind: %s", exc)
        return None, {}
    return index, counts


def _forget(index: Any, counts: dict[str, int], rel_paths: Iterable[str]) -> int:
    removed = 0
    for rel_path in rel_paths:
        try:
            index.delete_file(rel_path)
        except Exception as exc:
            logger.warning("de-index: could not remove the chunks of %s: %s", rel_path, exc)
            continue
        removed += int(counts.get(rel_path, 0))
    return removed


def forget_paths(index_factory: Callable[[], Any] | None, rel_paths: Iterable[str]) -> int:
    """Drop every chunk of each vault-relative path. Returns chunks removed.

    The count is what the index held for those paths *before* the delete, so
    a caller can report "3 files, 12 chunks" truthfully. A path the index
    never held contributes 0 rather than failing.
    """
    paths = [path for path in dict.fromkeys(rel_paths) if path]
    if not paths:
        return 0
    index, counts = _resolve(index_factory)
    return 0 if index is None else _forget(index, counts, paths)


def forget_tree(index_factory: Callable[[], Any] | None, rel_dir: str) -> int:
    """Drop the chunks of every indexed file under ``rel_dir``. Returns the count.

    Driven by what the *index* holds rather than by what is on disk, because
    the one caller — a course purge — has already ``rmtree``'d the directory
    by the time this runs, so walking the filesystem would find nothing to
    forget. That is exactly the bug: the files are gone and their chunks are
    not.
    """
    root = rel_dir.strip("/")
    if not root:
        return 0
    index, counts = _resolve(index_factory)
    if index is None:
        return 0
    inside = [path for path in counts if path == root or path.startswith(f"{root}/")]
    return _forget(index, counts, inside)
