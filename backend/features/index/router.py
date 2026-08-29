"""Manual vault reindex + status (Argus v0.2.1).

Before this existed, the only way to fill the chroma collection was the
CLI-only ``argus reindex`` — nothing in the shipped app ever ran it, so a
freshly-ingested or freshly-installed vault searched and chatted against an
empty index with no visible error (``VaultIndex.query`` returns ``[]`` on an
empty collection; see :mod:`backend.rag.index`). This router is the piece that
lets the dashboard actually trigger and observe a rebuild.

Reindexing loads the embedding model and walks the whole vault, so it must
never run on the request thread — it is handed to the same ``job_runner`` the
ingest router uses.

**The status used to live in a module-global ``_State``.** That was a second
job model sitting beside the durable one in ``ingest_jobs``, and neither knew
about the other: an ingest and a reindex each held their own single-flight
lock, so both could run at once against the same embedding model and the same
chroma directory, and ``writer._git_snapshot`` runs git with ``check=False``
so the loser of the ``.git/index.lock`` race failed silently. A reindex is a
row in :mod:`backend.features.ingest.store` now, with ``kind='reindex'``, and
:class:`IndexStatus` is a projection over that row rather than a copy of it.
``ReindexResult``'s per-file counts and errors, which used to be joined into
one string and thrown away, are ``ingest_job_items``.

The one property the old object got for free was resetting on restart: a
process killed mid-rebuild left nothing behind. A table does not get that, so
``store.reconcile_stale_jobs`` (boot-id scoped, run once at construction)
buys it back — a reindex row from a dead process is failed, not polled
forever.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.ingest import store
from backend.vault.writer import WriterForbidden, guard_user_path

logger = logging.getLogger("argus.rag")


class IndexStatus(BaseModel):
    """``GET /api/index/status`` and the immediate reply to a reindex trigger.

    Unchanged in shape across the move onto the job store — every field is a
    projection over the newest ``kind='reindex'`` row: ``indexing`` is
    ``status IN ('queued','running')``, ``last_run`` is ``finished_at``,
    ``last_error`` is ``error``.
    """

    chunks: int
    files: int
    indexing: bool
    last_run: str | None = None
    last_error: str | None = None
    stale: bool = False


class ReindexRequest(BaseModel):
    """Optional body for ``POST /api/index/reindex``.

    ``paths`` scopes the rebuild to named vault-relative files instead of
    walking everything — what an "index these files" button calls.
    ``VaultIndex.upsert_file`` deletes a file's chunks before adding new ones
    and returns 0 for a file that is no longer on disk, so the same call also
    de-indexes a deleted file. Omitted (the only thing every existing caller
    sends) means the full rebuild, unchanged.
    """

    paths: list[str] | None = None


def _guarded_paths(settings: Settings, paths: list[str]) -> list[str]:
    """Validate caller-supplied vault-relative paths, or raise the right error.

    Not optional politeness. ``VaultIndex.upsert_file`` gates on
    ``vault.paths.is_indexable``, which checks the suffix and the excluded top
    directories but says nothing about ``..`` — so an unguarded ``paths`` list
    would let a caller name ``../../secrets.md`` and have its contents read,
    embedded and stored in the collection every chat answer is retrieved from.
    ``guard_user_path`` is the same guard the write side runs, and it also
    refuses ``99-Private/`` and the rest of the protected zones, which must
    never enter the index either (I3).
    """
    clean: list[str] = []
    for raw in paths:
        rel_path = (raw or "").strip().replace("\\", "/").strip("/")
        if not rel_path:
            raise HTTPException(status_code=422, detail="an empty path cannot be indexed")
        try:
            guard_user_path(settings.vault_path, rel_path, taxonomy=settings.taxonomy)
        except WriterForbidden as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        clean.append(rel_path)
    # Deduplicated so a repeated path is not indexed (and reported) twice.
    return list(dict.fromkeys(clean))


def _default_job_runner(run: Callable[[], None]) -> None:
    """Run a job on a daemon thread. Replaced in tests by a synchronous call."""
    threading.Thread(target=run, daemon=True).start()


def run_tracked_reindex(
    job_id: str,
    *,
    settings: Settings,
    index_factory: Any,
    paths: list[str] | None = None,
) -> None:
    """The body of one reindex job. **Never raises.**

    "Tracked" to tell it apart from :func:`backend.scheduler.run_reindex_job`,
    the nightly rebuild, which is still a bare ``reindex_all`` call that leaves
    no row and takes no slot -- so it can still overlap with one of these. That
    is pre-existing and out of this change's scope, but it is the remaining
    hole in "one thing writes chroma at a time".

    Same contract, and the same reasoning, as
    ``backend.features.ingest.pipeline.run_ingest_job``: this runs on a daemon
    thread, so an exception escaping here would surface only in a log nobody
    is watching. Every failure is recorded against the row it belongs to.

    Opens its own connection: :func:`backend.core.db.connect` does not pass
    ``check_same_thread=False``, so one captured from the request handler
    would raise ``sqlite3.ProgrammingError`` on this thread.
    """
    conn = connect(settings.db_path)
    try:
        init_schema(conn)
        job = store.get_job(conn, job_id)
        if job is None:
            logger.warning("reindex job %s vanished before it ran", job_id)
            return
        store.start_job(conn, job_id)
        index = index_factory()
        if paths:
            _reindex_paths(conn, job, index, settings)
        else:
            _reindex_all(conn, job_id, index, settings)
    except Exception as exc:  # a genuinely broken index must be visible, not quiet
        logger.exception("reindex failed")
        try:
            store.finish_job(conn, job_id, status="failed", error=str(exc))
        except Exception:
            logger.exception("reindex job %s could not even record its own failure", job_id)
    finally:
        conn.close()


def _reindex_all(conn: Any, job_id: str, index: Any, settings: Settings) -> None:
    """Full rebuild, then one item per file it touched.

    The items are written afterwards rather than up front because
    ``reindex_all`` walks the vault itself: there is no moment before it runs
    at which a 'queued' row per file would be true. Before this, ``counts``
    and ``errors`` were joined into a single ``last_error`` string and the
    counts discarded entirely.
    """
    result = index.reindex_all(settings.vault_path)
    errors: dict[str, str] = dict(result.errors)
    counts: dict[str, int] = dict(result.counts)
    items = [
        {
            "filename": path.rsplit("/", 1)[-1],
            "path": path,
            "stage": "failed" if path in errors else "done",
            "chunks": counts.get(path, 0),
            "error": errors.get(path),
            # A reindex has exactly one stage a file can stop at.
            "failed_stage": "indexing" if path in errors else None,
        }
        for path in sorted(set(counts) | set(errors))
    ]
    store.add_items(conn, job_id, items)
    _finish(conn, job_id, failed=len(errors), total=len(items), errors=errors)
    logger.info("reindex complete: %d chunks from %d files", result.total_chunks, result.files)


def _reindex_paths(conn: Any, job: dict[str, Any], index: Any, settings: Settings) -> None:
    """Rebuild only the named files, one item at a time.

    Unlike the full rebuild this *does* know its files up front, so the rows
    exist before the work starts and the readout shows real per-file progress
    instead of appearing all at once at the end.
    """
    errors: dict[str, str] = {}
    for item in job["items"]:
        rel_path = item["filename"]
        try:
            store.advance_item(conn, item["id"], stage="indexing", path=rel_path)
            chunks = index.upsert_file(settings.vault_path, rel_path)
            store.advance_item(conn, item["id"], stage="done", chunks=chunks)
        except Exception as exc:  # one bad file must not abort the rest
            logger.warning("reindex failed for %s: %s", rel_path, exc)
            errors[rel_path] = str(exc)
            store.advance_item(
                conn, item["id"], stage="failed", failed_stage="indexing", error=str(exc)
            )
    _finish(conn, job["id"], failed=len(errors), total=len(job["items"]), errors=errors)


def _finish(conn: Any, job_id: str, *, failed: int, total: int, errors: dict[str, str]) -> None:
    """Terminal status, with the error string the old ``last_error`` carried.

    The ``"<path>: <message>"`` join is preserved verbatim: it is what
    ``IndexStatus.last_error`` has always shown, and the per-file rows are an
    addition to it, not a replacement.
    """
    if failed and failed == total:
        status = "failed"
    elif failed:
        status = "partial"
    else:
        status = "ok"
    message = "; ".join(f"{path}: {reason}" for path, reason in errors.items()) or None
    if message:
        logger.warning("reindex finished with %d error(s): %s", failed, message)
    store.finish_job(conn, job_id, status=status, error=message)


def _as_iso(stamp: str | None) -> str | None:
    """SQLite's ``YYYY-MM-DD HH:MM:SS`` back into the ISO-8601 this endpoint
    has always returned.

    The store keeps timestamps in SQLite's own format, but `last_run` is a
    published field with a published shape, and folding reindex into the job
    table is not a reason to change it. Nothing in the frontend renders it
    today, which is exactly why a silent format change here would be found
    much later and by someone else.
    """
    if not stamp:
        return None
    try:
        return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).isoformat()
    except ValueError:  # already ISO, or a shape we did not write
        return stamp


def _job_projection(settings: Settings) -> tuple[bool, str | None, str | None]:
    """``(indexing, last_run, last_error)`` read off the job table.

    Scoped to ``kind='reindex'`` on purpose. An ingest also writes the index,
    but ``indexing`` has always meant "a rebuild is in flight" to the System
    page that polls this, and widening it to every job that touches chroma
    would change what an untouched frontend renders.

    Degrades to "nothing known" rather than raising: an unconfigured vault has
    no database, and this endpoint is polled.
    """
    try:
        conn = connect(settings.db_path)
    except Exception as exc:  # noqa: BLE001 - a vault that isn't configured yet still polls
        logger.warning("index status: job history unavailable: %s", exc)
        return False, None, None
    try:
        init_schema(conn)
        current = store.latest_job(conn, "reindex")
        # Looked up separately from `current`: a rebuild in flight is the
        # newest row, and reading last_run off it would blank the previous
        # run's outcome the moment a new one started.
        previous = store.latest_job(conn, "reindex", finished=True)
    except Exception as exc:  # noqa: BLE001
        logger.warning("index status: job history unavailable: %s", exc)
        return False, None, None
    finally:
        conn.close()
    indexing = current is not None and current["status"] in store.ACTIVE_STATUSES
    if previous is None:
        return indexing, None, None
    return indexing, _as_iso(previous["finished_at"]), previous["error"]


def _status(settings: Settings, index_factory: Any) -> IndexStatus:
    chunks = files = 0
    stale = False
    try:
        index = index_factory()
        size = index.size()
        chunks, files = size["chunks"], size["files"]
        stale = index.schema_stale()
    except Exception as exc:  # index truly unavailable — report it, don't 500
        logger.warning("index status unavailable: %s", exc)
    indexing, last_run, last_error = _job_projection(settings)
    return IndexStatus(
        chunks=chunks,
        files=files,
        indexing=indexing,
        last_run=last_run,
        last_error=last_error,
        stale=stale,
    )


def build_index_router(
    settings: Settings,
    index_factory: Any,
    job_runner: Callable[[Callable[[], None]], None] | None = None,
) -> APIRouter:
    """``/api/index`` routes. ``index_factory`` is injectable (tests use a fake)."""
    router = APIRouter(prefix="/api/index")
    run_job = job_runner or _default_job_runner

    # Once per process, at construction — the property the old in-memory
    # `_State` got for free by resetting on restart. Boot-id scoped, so it
    # never touches a job this process started. Also done by the ingest
    # router; running it twice at boot is a no-op, and neither router may
    # assume the other was mounted.
    try:
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            store.reconcile_stale_jobs(conn)
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 - an unconfigured vault must still boot
        logger.warning("index: could not reconcile stale jobs: %s", exc)

    @router.post("/reindex", status_code=202, response_model=IndexStatus)
    def reindex(request: ReindexRequest | None = None) -> IndexStatus:
        paths: list[str] | None = None
        if request is not None and request.paths is not None:
            # `[]` is refused rather than quietly widened to a full rebuild:
            # the caller asked for a scoped reindex and named nothing, and
            # walking the entire vault instead is the opposite of that. Same
            # reasoning as `sources: []` in the study routes.
            if not request.paths:
                raise HTTPException(
                    status_code=422,
                    detail="no paths were given — omit `paths` entirely to rebuild everything",
                )
            paths = _guarded_paths(settings, request.paths)
        conn = connect(settings.db_path)
        try:
            init_schema(conn)
            blocking = store.running_job(conn, "reindex")
            if blocking is not None:
                # Two different answers, deliberately. Another *reindex* in
                # flight keeps the historical behaviour exactly: a 202 with
                # `indexing: true` and no second rebuild, because a
                # double-clicked REINDEX button is a request for a state the
                # user is already in. An *ingest* in the way is a genuine
                # refusal — it is new that these contend at all (see
                # `store.SLOT_GROUPS`), and 409 is this codebase's idiom for
                # "one at a time".
                if blocking["kind"] != "reindex":
                    raise HTTPException(
                        status_code=409,
                        detail="an ingest is already running — wait for it to finish",
                    )
                return _status(settings, index_factory)
            job_id = store.create_job(
                conn,
                # A reindex writes nothing into the vault, so it has no target
                # folder; '' is the column's "not applicable" value.
                target="",
                filenames=paths or [],
                kind="reindex",
                params={"paths": paths} if paths else None,
            )
        finally:
            conn.close()

        run_job(
            lambda: run_tracked_reindex(
                job_id, settings=settings, index_factory=index_factory, paths=paths
            )
        )
        return _status(settings, index_factory)

    @router.get("/status", response_model=IndexStatus)
    def status() -> IndexStatus:
        return _status(settings, index_factory)

    return router
