"""The body of one ingest job: save, index, and optionally summarise N files.

Runs on a daemon thread and **never raises** -- an exception escaping here
would surface only in a log nobody is watching, so every failure is recorded
against the row it belongs to. Same contract, and the same reasoning, as
``backend.features.index.router._run_reindex``.

Two properties worth stating outright, because both are the reason a 20-file
job is not 20 one-file jobs:

* The index is constructed **once** per job. A ``VaultIndex``'s embedding
  model is per-instance and takes seconds to load, so calling the factory per
  file would load bge-small once per file.
* The vault is snapshotted **once** per job, before the first file, via
  ``writer.snapshot_vault``. Per-file snapshots mean N full-vault ``git add
  -A`` runs for one undo point -- and because ``_git_snapshot`` runs git with
  ``check=False``, two overlapping ones race on ``.git/index.lock`` and the
  loser fails silently.
"""

from __future__ import annotations

import logging
from pathlib import Path

import frontmatter

from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.vault.privacy import is_no_ai_text, is_private_path, is_visible

logger = logging.getLogger("argus.rag")


def summary_is_permitted(
    vault_path: Path,
    rel_path: str,
    text: str,
    *,
    taxonomy: Taxonomy | None = None,
) -> bool:
    """May this file's text be sent to a model to be summarised? (I3)

    Summarising is the first path in Argus that reads a vault file purely in
    order to hand its contents to a generator, and nothing existing covers it:
    ``guard_user_path`` governs where a file may be *written*, and
    ``_extract_markdown`` returning ``[]`` for ``#no-ai`` guards the *index*.
    Neither stops a `#no-ai` note outside ``99-Private/`` being posted to a
    hosted provider.

    For markdown this is :func:`backend.vault.privacy.is_visible` -- both
    halves of I3. For a PDF/PPTX/DOCX/EML there is no frontmatter to parse, so
    the two halves are applied separately, with the tag half run against the
    text already extracted from the file. That is what makes a ``#no-ai``
    typed on a slide count, which nothing honoured before.

    A missing or unreadable file is refused. Malformed frontmatter is *not*
    treated as a privacy verdict: it falls back to scanning the raw text for
    the tag, exactly as ``backend.agent.runtime._note_is_visible`` does, so a
    typo in a note's YAML does not silently change what Argus will do with it.
    """
    tax = taxonomy or active_taxonomy()
    if is_private_path(rel_path, taxonomy=tax):
        return False
    file_path = vault_path / rel_path
    if not file_path.is_file():
        return False
    if file_path.suffix.lower() != ".md":
        # No frontmatter to read, so only the tag half can apply -- and it
        # applies to the text extracted from the binary, which is what makes
        # a `#no-ai` typed on a slide count.
        return not is_no_ai_text({}, text)
    try:
        post = frontmatter.load(file_path)
    except Exception:  # noqa: BLE001 - a malformed header is not a privacy verdict
        try:
            raw = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return False
        return not is_no_ai_text({}, raw)
    return is_visible(rel_path, post, taxonomy=tax)
