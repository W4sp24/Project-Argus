"""Backfill relationships onto notes written before they existed.

Everything Argus wrote before this feature is a leaf: one trailing wikilink
whose extension had been stripped, no tags, no concepts, no ``related``.
Shipping the feature for *new* notes only would leave a vault half-linked and
the feature looking broken on everything already in it -- which, for a tool
whose whole point is that your existing notes become reachable, is the worst
half to ship.

Runs as ``kind='relink'`` on the job store the ingest and the reindex already
share, so it inherits the segmented progress readout, the one-at-a-time slot
and stale-job reconciliation for free. It belongs in that slot: it re-upserts
every note it rewrites and takes a git snapshot, so it contends for the
embedding model and the git index exactly as an ingest does.

Two properties carry the whole module.

**The guard.** Only frontmatter ``generated_by: argus`` makes a note
relinkable, and it is checked twice -- once in the listing and again in
:func:`relink_one`, because a caller can name a path directly. The filename
convention (``.notes.md`` / ``.summary.md``) is deliberately *not* accepted as
evidence: a user is free to name a note that way, and widening the guard to a
naming convention turns a backfill into a data-loss bug.

**Idempotence.** A second run produces byte-identical output and reports no
change. The fenced region makes that nearly free, but not quite: the model's
``## Topics`` section is *consumed* by the first pass, so a second pass has
nothing left to parse and would silently rewrite the note with no concepts at
all. :func:`_topics_of` reads them back out of frontmatter, which is where the
first pass put them.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import frontmatter

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.core.taxonomy import Taxonomy, active_taxonomy
from backend.features.ingest import store
from backend.rag.neighbours import nearest_notes
from backend.vault import relations
from backend.vault.links import build_link_index
from backend.vault.privacy import is_no_ai, is_private_path
from backend.vault.sources import GENERATED_BY
from backend.vault.writer import log_action, snapshot_vault, update_note

logger = logging.getLogger("argus.vault")


def _is_generated(post: frontmatter.Post) -> bool:
    """Did Argus write this note?

    One question, one answer, and the answer is the frontmatter stamp
    :data:`~backend.vault.sources.GENERATED_BY` -- the thing the writer
    actually puts there, and the thing a user would have to delete
    deliberately. Nothing else counts, in particular not the filename.
    """
    return str(post.metadata.get("generated_by") or "") == GENERATED_BY


def _topics_of(post: frontmatter.Post, parsed: list[str]) -> list[str]:
    """The note's concepts: whatever the body still names, else frontmatter.

    This is the one non-obvious part of making a relink idempotent. The first
    run *consumes* the model's ``## Topics`` section -- that is the point of
    it, the reader gets links instead of a second list of bare names -- so a
    second run parses nothing out of the body. Without this fallback the
    re-run would produce a Related region with the Concepts line missing
    entirely, silently downgrading every note it touched on the second pass.
    """
    if parsed:
        return parsed
    raw = post.metadata.get("topics") or []
    if isinstance(raw, str):
        raw = [raw]
    return [topic for topic in (str(item).strip() for item in raw) if topic]


def relinkable_notes(vault_path: Path, *, taxonomy: Taxonomy | None = None) -> list[str]:
    """Every note Argus wrote, vault-relative, sorted for a stable readout.

    Both halves of I3 are applied before the file is even considered: a note
    under a protected zone, and a note tagged ``#no-ai``. The job embeds each
    note's own words as a neighbour query and re-upserts the result into the
    index, so a note that must stay out of the index must stay out of here.
    """
    tax = taxonomy or active_taxonomy()
    found: list[str] = []
    for file_path in vault_path.rglob("*.md"):
        if not file_path.is_file():
            continue
        rel = file_path.relative_to(vault_path).as_posix()
        if is_private_path(rel, taxonomy=tax):
            continue
        try:
            post = frontmatter.load(file_path)
        except Exception:  # noqa: BLE001 - malformed YAML is not a reason to fail a job
            logger.warning("relink: %s could not be parsed, skipped", rel)
            continue
        if is_no_ai(post) or not _is_generated(post):
            continue
        found.append(rel)
    return sorted(found)


def relink_one(
    vault_path: Path,
    rel_path: str,
    *,
    resolve: relations.Resolver,
    neighbours: Sequence[tuple[str, str]],
    taxonomy: Taxonomy | None = None,
    dry_run: bool = False,
) -> bool:
    """Rewrite one note's relationships. ``True`` when anything changed.

    Takes no index: ``resolve`` and ``neighbours`` arrive as plain values, the
    same seam :func:`backend.vault.relations.build_relations` uses, so the
    whole rewrite is testable without chroma or an embedding model.

    Refuses a note Argus did not write even though :func:`relinkable_notes`
    already filtered for that. The listing guard protects the job; this one
    protects everything else, because a caller -- a CLI flag, a future
    "relink this one" button, a mistake -- can name a path directly.
    """
    tax = taxonomy or active_taxonomy()
    file_path = vault_path / rel_path
    if not file_path.is_file():
        return False
    original = file_path.read_text(encoding="utf-8")
    try:
        post = frontmatter.loads(original)
    except Exception:  # noqa: BLE001
        logger.warning("relink: %s could not be parsed, skipped", rel_path)
        return False
    if not _is_generated(post):
        return False

    course = post.metadata.get("course")
    code = str(course) if course else None
    # `strip_section` first, so the fenced region a previous run wrote is gone
    # before topics are parsed -- otherwise the second run would try to read
    # concepts out of the links the first one rendered.
    prose, parsed = relations.parse_topics(relations.strip_section(post.content))
    kind = "guide" if str(post.metadata.get("type") or "") == "guide" else "note"
    built = relations.build_relations(
        topics=_topics_of(post, parsed),
        resolve=resolve,
        neighbours=neighbours,
        # A guide has no single source file. Passing its own path is what
        # `study_guide.guide_markdown` does, and for the same two reasons: it
        # makes the neighbour exclusion correct, and it makes the source link
        # a self-link, which is dropped below rather than shown.
        source_rel_path=str(post.metadata.get("source") or rel_path),
        note_rel_path=rel_path,
        course=code,
        taxonomy=tax,
    )
    if kind == "guide":
        built = relations.Relations(
            topics=built.topics,
            links=[link for link in built.links if link.kind != "source"],
        )
    front = relations.merge_frontmatter(dict(post.metadata), built, kind=kind, course=code)
    content = relations.replace_section(prose, relations.render_section(built))
    rewritten = frontmatter.dumps(frontmatter.Post(content, **front)) + "\n"
    if rewritten == original:
        return False
    if dry_run:
        return True
    # snapshot=False/log=False: the job took one snapshot before the first
    # note and writes one log line after the last. See `update_note`.
    update_note(vault_path, rel_path, original, rewritten, taxonomy=tax, snapshot=False, log=False)
    return True


def neighbour_query(raw: str) -> str:
    """One note's own words, as the query that finds its neighbours.

    Both the frontmatter and any fenced Related region come off first. The
    fence is the load-bearing half: it is a block of near-pure wikilinks, and
    on a second run it would sit inside the query budget and pull the query
    toward the notes this one already links to. Neighbours would then differ
    between run one and run two, and the job would quietly stop being
    idempotent for a reason nothing in the diff would explain.
    """
    try:
        post = frontmatter.loads(raw)
    except Exception:  # noqa: BLE001 - an unparseable note still has words in it
        return raw
    return relations.strip_section(post.content)


def recorded_neighbours(raw: str) -> list[tuple[str, str]] | None:
    """A guide's own cited materials, or ``None`` to fall back to a query.

    A study guide's neighbours are the materials it was written from, and it
    records them in ``sources:`` for exactly this moment: the corpus is long
    gone by the time a relink runs, so recomputing them with a similarity
    query would quietly replace "what this was written from" with "what reads
    like this", and the real citation set could never be recovered.

    Returns ``None`` — not ``[]`` — for anything that is not a guide, or a
    guide written before ``sources:`` existed. The two are different
    instructions to the caller: ``None`` means "you decide", ``[]`` would
    mean "this artefact genuinely has no neighbours".
    """
    try:
        post = frontmatter.loads(raw)
    except Exception:  # noqa: BLE001 - an unparseable note is handled downstream
        return None
    if str(post.metadata.get("type") or "") != "guide":
        return None
    recorded = post.metadata.get("sources") or []
    if isinstance(recorded, str):
        recorded = [recorded]
    cited = [str(path).strip() for path in recorded if str(path).strip()]
    if not cited:
        return None
    return [(path, path.rsplit("/", 1)[-1]) for path in cited]


def run_relink_job(
    job_id: str,
    *,
    settings: Settings,
    index_factory: Any,
    dry_run: bool = False,
) -> None:
    """The body of one relink job. **Never raises.**

    Same contract, and the same reasoning, as
    :func:`backend.features.ingest.pipeline.run_ingest_job` and
    :func:`backend.features.index.router.run_tracked_reindex`: this runs on a
    daemon thread, so an exception escaping here would surface only in a log
    nobody is watching. Every failure is recorded against the row it belongs to.

    Opens its own connection: :func:`backend.core.db.connect` does not pass
    ``check_same_thread=False``, so one captured from the request handler would
    raise ``sqlite3.ProgrammingError`` on this thread.
    """
    conn = connect(settings.db_path)
    try:
        init_schema(conn)
        job = store.get_job(conn, job_id)
        if job is None:
            logger.warning("relink job %s vanished before it ran", job_id)
            return
        store.start_job(conn, job_id)
        _run(conn, job, settings=settings, index_factory=index_factory, dry_run=dry_run)
    except Exception as exc:  # a broken backfill must be visible, not quiet
        logger.exception("relink job %s failed", job_id)
        try:
            store.finish_job(conn, job_id, status="failed", error=str(exc))
        except Exception:
            logger.exception("relink job %s could not even record its own failure", job_id)
    finally:
        conn.close()


def _run(
    conn: Any,
    job: dict[str, Any],
    *,
    settings: Settings,
    index_factory: Any,
    dry_run: bool,
) -> None:
    """One snapshot, one link index, one embedding model, N notes.

    Every "one" there is deliberate. The snapshot is I2 (see
    :func:`~backend.vault.writer.update_note`); the link index is a full vault
    rglob plus a frontmatter parse per note, so building it per note would
    make the job quadratic in the vault; the index instance is where the
    ~7s embedding model load lives.
    """
    tax = settings.taxonomy
    paths = relinkable_notes(settings.vault_path, taxonomy=tax)
    store.add_items(
        conn,
        job["id"],
        [{"filename": path.rsplit("/", 1)[-1], "path": path, "stage": "queued"} for path in paths],
    )
    # Re-read for the item ids `add_items` assigned; the rows did not exist
    # when the job row was created (a relink, like a full reindex, does not
    # know its files until it walks the vault).
    job = store.get_job(conn, job["id"]) or job

    if not dry_run and paths:
        snapshot_vault(settings.vault_path, f"relink {len(paths)} generated note(s)")

    index = index_factory()
    link_index = build_link_index(settings.vault_path, taxonomy=tax)

    errors: dict[str, str] = {}
    changed_count = 0
    for item in job["items"]:
        rel_path = item["path"] or item["filename"]
        stage = "summarizing"
        try:
            store.advance_item(conn, item["id"], stage=stage, path=rel_path)
            raw = (settings.vault_path / rel_path).read_text(encoding="utf-8")
            changed = relink_one(
                settings.vault_path,
                rel_path,
                # Bound to this note's own path: `LinkIndex.resolve` breaks a
                # tie by shortest path *from the asking note*, so resolving
                # from anywhere else can silently cross-wire two same-named
                # files. `_from` is a default argument rather than a closure
                # over the loop variable, which would bind every iteration to
                # the last path.
                resolve=lambda name, _from=rel_path: link_index.resolve(name, from_path=_from),
                # A guide names its own neighbours and they are not
                # recoverable any other way, so they are never recomputed.
                neighbours=recorded_neighbours(raw)
                or nearest_notes(
                    index,
                    settings.vault_path,
                    neighbour_query(raw),
                    exclude={rel_path},
                    taxonomy=tax,
                ),
                taxonomy=tax,
                dry_run=dry_run,
            )
            chunks = 0
            if changed and not dry_run:
                # Re-upserted so retrieval sees the new links: the one-hop
                # expansion reads `wikilinks` off chunk metadata, which stays
                # stale until the note is embedded again -- and those links
                # are the whole reason they went in the body.
                stage = "indexing"
                store.advance_item(conn, item["id"], stage=stage)
                chunks = index.upsert_file(settings.vault_path, rel_path)
            changed_count += int(bool(changed))
            store.advance_item(
                conn, item["id"], stage="done" if changed else "skipped", chunks=chunks
            )
        except Exception as exc:  # one bad note must not abort the rest
            logger.warning("relink failed for %s: %s", rel_path, exc)
            errors[rel_path] = str(exc)
            store.advance_item(
                conn, item["id"], stage="failed", failed_stage=stage, error=str(exc)
            )

    if changed_count and not dry_run:
        # The counterpart to the single snapshot: one daily-note line for one
        # user action, not one per file and not -- as the ingest job settles
        # for -- none at all.
        _log_batch(settings, changed_count)

    total = len(job["items"])
    if errors and len(errors) == total:
        status = "failed"
    elif errors:
        status = "partial"
    else:
        status = "ok"
    store.finish_job(
        conn,
        job["id"],
        status=status,
        error="; ".join(f"{path}: {reason}" for path, reason in errors.items()) or None,
    )
    logger.info("relink complete: %d of %d note(s) rewritten", changed_count, total)


def _log_batch(settings: Settings, changed: int) -> None:
    """The audit line, best-effort.

    A journal write that fails must not fail a job whose real work is already
    on disk -- the same direction :mod:`backend.rag.deindex` errs in.
    """
    try:
        log_action(
            settings.vault_path,
            f"relinked {changed} generated note{'' if changed == 1 else 's'}",
            taxonomy=settings.taxonomy,
        )
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.warning("relink: could not write the journal line: %s", exc)
