"""The body of one ingest job: save, index, and optionally summarise N files.

Runs on a daemon thread and **never raises** -- an exception escaping here
would surface only in a log nobody is watching, so every failure is recorded
against the row it belongs to. Same contract, and the same reasoning, as
``backend.features.index.router._run_reindex``.

Three properties are the reason a 20-file job is not 20 one-file jobs:

* The index is constructed **once** per job. A ``VaultIndex``'s embedding
  model is per-instance and takes seconds to load, so calling the factory per
  file would load bge-small once per file.
* The vault is snapshotted **once**, before the first file, via
  ``writer.snapshot_vault``. Per-file snapshots mean N full-vault ``git add
  -A`` runs for one undo point -- and because ``_git_snapshot`` runs git with
  ``check=False``, two overlapping ones race on ``.git/index.lock`` and the
  loser fails silently.
* The daily-note line is written once, for the same reason.

Files arrive already staged on disk rather than as ``UploadFile`` objects:
Starlette closes a request's spooled temp files when the request scope ends,
so a handler returning 202 cannot hand them to a thread that runs later. The
router stages them under ``.argus/`` -- never inside the vault, where the
watcher would index them at their temporary path -- and this function owns
deleting the staging directory when it is done.

One job runs at a time (enforced by the router), so nothing here contends
with another job for the embedding model or the git index.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import frontmatter

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.features.ingest import store
from backend.vault.privacy import is_no_ai_text, is_private_path, is_visible
from backend.vault.writer import create_note, save_ingest_file, snapshot_vault

logger = logging.getLogger("argus.rag")

Generator = Callable[..., Awaitable[str]]

#: How much of a file's text is handed to the model. Matches the email
#: extractor's budget -- large enough for a lecture, small enough that a
#: 400-page PDF does not become one enormous prompt.
MAX_SUMMARY_CHARS = 12_000

SUMMARY_PROMPT = """{instruction}

Write the summary as markdown. Do not add a title heading; one is already
in the note's frontmatter. Do not invent anything the document does not say.

DOCUMENT ({path}):
{text}
"""


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
    Neither stops a ``#no-ai`` note outside ``99-Private/`` being posted to a
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


def _source_text(vault_path: Path, rel_path: str) -> str:
    """The file's text, for the summary prompt.

    Goes through the same extractors the index uses, so a PDF or PPTX is
    summarised from its real contents rather than skipped for not being
    markdown. Heavy parsers are imported lazily inside ``extract_blocks``, so
    this costs nothing for a vault of plain notes.
    """
    from backend.rag.extract import extract_blocks

    blocks = extract_blocks(vault_path / rel_path)
    return "\n\n".join(block.text for block in blocks).strip()[:MAX_SUMMARY_CHARS]


def _summary_note(rel_path: str, instruction: str, body: str) -> tuple[str, str]:
    """``(summary_rel_path, markdown)`` for one source file.

    The name is derived from the **deduped** source path, not from the
    uploaded filename: ``_dedupe`` renames a colliding upload to
    ``lecture-2.md``, and a summary still called ``lecture.summary.md`` would
    collide with the previous one. ``create_note`` is deliberately create-only,
    so that collision would fail the item for a reason the user cannot act on.

    The trailing wikilink is load-bearing rather than decorative: it is what
    lets ``retrieve.py``'s existing one-hop link expansion reach the source
    from the summary, with no new retrieval code. It resolves only when the
    source is itself markdown -- ``build_link_index`` is markdown-only -- so
    for a PDF the link is a readable breadcrumb and nothing more.
    """
    source = Path(rel_path)
    summary_path = source.with_name(f"{source.stem}.summary.md").as_posix()
    front = {
        "title": f"{source.stem} — summary",
        "type": "summary",
        "generated_by": "argus",
        "source": rel_path,
        "prompt": instruction,
    }
    post = frontmatter.Post(f"{body.strip()}\n\n[[{source.stem}]]\n", **front)
    return summary_path, frontmatter.dumps(post) + "\n"


def _staged_files(staging_dir: Path) -> list[Path]:
    """Staged uploads in the order the router wrote them.

    Names are ``<ordinal>__<filename>``, so sorting by the ordinal keeps a
    batch's items lined up with their rows even when two uploads share a
    filename.
    """
    def ordinal(path: Path) -> int:
        head, _, _ = path.name.partition("__")
        return int(head) if head.isdigit() else 0

    return sorted((path for path in staging_dir.iterdir() if path.is_file()), key=ordinal)


def _generate(generator: Generator, prompt: str) -> str:
    """Await one generator call from this synchronous thread.

    ``asyncio.run`` is safe here precisely because this is a worker thread
    with no running loop of its own.
    """
    return asyncio.run(generator(prompt))


def _summarise(
    *,
    settings: Settings,
    generator: Generator,
    instruction: str,
    rel_path: str,
    index: Any,
    conn: Any,
    item_id: int,
) -> None:
    """Generate one summary note and index it. Never raises.

    A failure here must not undo the ingest: by this point the file is saved
    and indexed, and a provider being down is not a reason to lose it. So the
    item still finishes ``done``, carrying the error as a note on why no
    summary appeared.
    """
    text = _source_text(settings.vault_path, rel_path)
    # The privacy verdict is taken BEFORE the empty-text check, not after.
    # `extract_blocks` already returns [] for a `#no-ai` markdown note, so an
    # "is there anything to summarise?" short-circuit placed first would send
    # exactly the private files down the same path as a blank one -- reporting
    # them 'done' with no summary, which is indistinguishable from success and
    # tells the user nothing about why their instruction was ignored.
    if not summary_is_permitted(settings.vault_path, rel_path, text, taxonomy=settings.taxonomy):
        store.advance_item(
            conn,
            item_id,
            stage="skipped",
            error="tagged #no-ai — summary skipped, nothing was sent to the model",
        )
        return
    if not text:
        return

    store.advance_item(conn, item_id, stage="summarizing")
    prompt = SUMMARY_PROMPT.format(instruction=instruction, path=rel_path, text=text)
    body = _generate(generator, prompt)
    summary_path, markdown = _summary_note(rel_path, instruction, body)
    written = create_note(
        settings.vault_path,
        summary_path,
        markdown,
        taxonomy=settings.taxonomy,
        snapshot=False,
        log=False,
    )
    chunks = index.upsert_file(settings.vault_path, written)
    logger.info("ingest: summarised %s -> %s (%d chunks)", rel_path, written, chunks)
    store.advance_item(conn, item_id, stage="summarizing", summary_path=written)


def run_ingest_job(
    job_id: str,
    *,
    settings: Settings,
    index_factory: Callable[[], Any],
    generator: Generator | None,
    staging_dir: Path,
) -> None:
    """Save, index and optionally summarise every staged file. Never raises."""
    conn = connect(settings.db_path)
    try:
        init_schema(conn)
        job = store.get_job(conn, job_id)
        if job is None:
            logger.warning("ingest job %s vanished before it ran", job_id)
            return
        _run(conn, job, settings=settings, index_factory=index_factory, generator=generator,
             staging_dir=staging_dir)
    except Exception as exc:  # a job that dies silently is the bug this replaces
        logger.exception("ingest job %s failed", job_id)
        try:
            store.finish_job(conn, job_id, status="failed", error=str(exc))
        except Exception:
            logger.exception("ingest job %s could not even record its own failure", job_id)
    finally:
        conn.close()
        shutil.rmtree(staging_dir, ignore_errors=True)


def _run(
    conn: Any,
    job: dict[str, Any],
    *,
    settings: Settings,
    index_factory: Callable[[], Any],
    generator: Generator | None,
    staging_dir: Path,
) -> None:
    """The happy path, with per-file failures contained to their own item."""
    job_id = job["id"]
    target = job["target"]
    instruction = (job["summary_prompt"] or "").strip()
    store.start_job(conn, job_id)

    # Both of these can fail for the whole job rather than for one file: no
    # embedding model means nothing can be indexed, and no snapshot means I2
    # is not satisfied for any of the writes that would follow.
    index = index_factory()
    snapshot_vault(settings.vault_path, f"ingest {len(job['items'])} file(s) into {target}")

    staged = _staged_files(staging_dir)
    outcomes: list[str] = []
    for item, staged_path in zip(job["items"], staged, strict=False):
        outcomes.append(
            _run_one(
                conn,
                item,
                staged_path,
                settings=settings,
                index=index,
                generator=generator,
                instruction=instruction,
                target=target,
            )
        )

    failed = outcomes.count("failed")
    if failed == len(outcomes) and outcomes:
        status = "failed"
    elif failed:
        status = "partial"
    else:
        status = "ok"
    store.finish_job(
        conn,
        job_id,
        status=status,
        error=f"{failed} of {len(outcomes)} file(s) failed" if failed else None,
    )


def _run_one(
    conn: Any,
    item: dict[str, Any],
    staged_path: Path,
    *,
    settings: Settings,
    index: Any,
    generator: Generator | None,
    instruction: str,
    target: str,
) -> str:
    """One file through save -> index -> summary. Returns its terminal stage."""
    item_id = item["id"]
    try:
        store.advance_item(conn, item_id, stage="saving")
        rel_path = save_ingest_file(
            settings.vault_path,
            target,
            item["filename"],
            staged_path.read_bytes(),
            taxonomy=settings.taxonomy,
            snapshot=False,
            log=False,
        )

        store.advance_item(conn, item_id, stage="indexing", path=rel_path)
        chunks = index.upsert_file(settings.vault_path, rel_path)
        store.advance_item(conn, item_id, stage="indexing", chunks=chunks)
    except Exception as exc:  # WriterError (bad target, I3 refusal) included
        logger.warning("ingest: %s failed: %s", item["filename"], exc)
        store.advance_item(conn, item_id, stage="failed", error=str(exc))
        return "failed"

    if not instruction or generator is None:
        store.advance_item(conn, item_id, stage="done")
        return "done"

    try:
        _summarise(
            settings=settings,
            generator=generator,
            instruction=instruction,
            rel_path=rel_path,
            index=index,
            conn=conn,
            item_id=item_id,
        )
    except Exception as exc:
        # The file is already saved and indexed; a dead provider is not a
        # reason to lose it. Record why no summary appeared and move on.
        logger.warning("ingest: summary for %s failed: %s", rel_path, exc)
        store.advance_item(conn, item_id, stage="done", error=str(exc))
        return "done"

    # _summarise marks the item 'skipped' itself when I3 refuses it, and that
    # is a terminal stage the caller must not overwrite with 'done'.
    current = conn.execute(
        "SELECT stage FROM ingest_job_items WHERE id = ?", (item_id,)
    ).fetchone()["stage"]
    if current == "skipped":
        return "skipped"
    store.advance_item(conn, item_id, stage="done")
    return "done"
