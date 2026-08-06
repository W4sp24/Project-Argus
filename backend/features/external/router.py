"""``/api/external/*`` — the inbound surface reachable from outside.

Capture and read only. Two things are **deliberately absent** and must stay
that way: agent invocation and action triggers. Their absence is why there is
no LLM spend behind a public URL, no citation obligation on this surface, and
no long-running request holding a tunnel connection open. Adding either one
reopens all three questions at once.

Every write goes through :mod:`backend.vault.writer` (invariant I1) — nothing
here touches the filesystem directly. Every read goes through
:mod:`backend.features.external.filters` (invariant I3).

Auth, rate limiting and the body cap live in :mod:`.auth` and are applied as
dependencies **before** any handler body runs, so no endpoint can be written
later that forgets one.

**Multi-instance auth (B4).** ``guard()`` authenticates *per instance*: the
bearer token is checked against every registered n8n instance's own token
(:func:`backend.features.external.auth.resolve_token`), and the dependency
returns the id of whichever instance's token matched. Revoking one instance
(deleting it, or rotating its token) therefore has no effect on any other
instance's ability to authenticate. Every write this router makes threads
that matched ``instance_id`` through, so an inbound push is attributed to the
instance that actually sent it, not to whichever instance happened to be
registered first.

**Known, deliberately unfixed gap:** the rate limiter (``bucket``/`throttle`)
stays a single global bucket across every instance — see
:class:`backend.features.external.auth.RateLimiter`'s own docstring. A flood
of requests carrying one leaked (or since-revoked) instance's token still
consumes budget shared with every other instance's legitimate traffic. Making
the limiter per-instance is future work, not this chunk's job.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from datetime import date

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response

from backend.core.config import Settings
from backend.core.db import connect, init_schema
from backend.features.automations import store
from backend.features.automations.schema import (
    WidgetValidationError,
    validate_widget_payload,
)
from backend.features.external import auth
from backend.features.external.filters import VisibilityCache, visible_tasks
from backend.vault.errors import raise_http
from backend.vault.tasks import bucketed_tasks, refresh_cache
from backend.vault.writer import WriterError, append_capture

logger = logging.getLogger(__name__)

#: Returned for every auth failure, whatever the cause. Absent header,
#: malformed header, wrong token and an unreadable keyring are indistinguishable
#: from outside — a 401 that explains itself is a 401 that helps an attacker.
_UNAUTHORIZED = "unauthorized"


def _bearer(authorization: str | None) -> str | None:
    """Extract a bearer token, tolerating case and extra whitespace."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1].strip() or None


def build_external_router(
    settings: Settings,
    *,
    limiter: auth.RateLimiter | None = None,
    now: Callable[[], date] | None = None,
) -> APIRouter:
    """The external router. ``limiter``/``now`` are injectable for tests."""
    router = APIRouter(prefix="/api/external")
    bucket = limiter if limiter is not None else auth.RateLimiter()
    today = now or date.today

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    # --- activity events (B5) -----------------------------------------------
    #
    # Same non-fatal guarantee as backend.features.automations.router's own
    # _safe_record_event: a logging write must never be able to fail (or roll
    # back) the push/capture/task it describes.

    def _safe_record_event(
        conn: sqlite3.Connection,
        *,
        tag: str,
        text: str,
        instance_id: str,
        subject: str | None = None,
    ) -> None:
        try:
            store.record_event(conn, tag=tag, text=text, instance_id=instance_id, subject=subject)
        except Exception:
            logger.exception(
                "failed to record %s activity event for instance %r", tag, instance_id
            )

    def _safe_record_standalone_event(
        *, tag: str, text: str, instance_id: str, subject: str | None = None
    ) -> None:
        """Same guarantee as ``_safe_record_event``, but also covers opening
        the connection itself. ``/capture`` and ``/tasks`` don't already have
        one open — their write goes through the vault writer, not sqlite —
        so failing to even open a db connection here must not fail a
        capture/task write that already succeeded.
        """
        try:
            conn = db()
            try:
                _safe_record_event(
                    conn, tag=tag, text=text, instance_id=instance_id, subject=subject
                )
            finally:
                conn.close()
        except Exception:
            logger.exception("failed to open db for a %s activity event", tag)

    async def throttle() -> None:
        """The rate limit, applied to **every** route including unauthenticated ones.

        Separate from :func:`guard` so that ``/ping`` — which takes no
        credential by design — is still bounded. An unauthenticated endpoint
        with no rate limit is a free amplifier pointed at this process.
        """
        if not bucket.allow():
            raise HTTPException(status_code=429, detail="rate limit exceeded")

    async def guard(
        request: Request,
        authorization: str | None = Header(default=None),
        _throttled: None = Depends(throttle),
    ) -> str:
        """Body cap, then auth — after the rate limit has already ticked.

        Order is deliberate. The rate limit runs first (via ``throttle``) so a
        flood of *unauthenticated* requests is throttled too; checking auth
        first would leave the token brute-forceable at full speed, since
        rejected requests would never consume budget. The body cap is enforced
        before the body is ever parsed, because an unbounded public endpoint
        writing into sqlite and a vault is a disk-fill vector.

        Returns the id of the instance whose token was presented — every
        write in this router threads that id through so an inbound push is
        attributed to the instance that actually sent it, not to whichever
        instance happened to be registered first.
        """
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > auth.MAX_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="body too large")
            except ValueError:
                raise HTTPException(status_code=413, detail="body too large") from None

        instances = store.load_instances(settings.automations_file)
        instance_id = auth.resolve_token(_bearer(authorization), instances)
        if instance_id is None:
            raise HTTPException(status_code=401, detail=_UNAUTHORIZED)
        return instance_id

    guarded = [Depends(guard)]
    throttled = [Depends(throttle)]

    # --- liveness -----------------------------------------------------------

    @router.get("/ping", status_code=204, dependencies=throttled)
    async def ping() -> Response:
        """Tunnel liveness only: no auth, no body, no vault access.

        Unauthenticated on purpose — a tunnel health check should not need to
        hold a credential, and this reveals nothing beyond "something is
        listening". Still rate limited: unauthenticated does not mean free.
        """
        return Response(status_code=204)

    # --- writes -------------------------------------------------------------

    @router.post("/capture")
    async def capture(payload: dict, instance_id: str = Depends(guard)) -> dict:
        body = str(payload.get("body") or "").strip()
        if not body:
            raise HTTPException(status_code=422, detail="'body' is required")
        title = payload.get("title")
        tags = payload.get("tags") or []
        text = body if not title else f"{title}: {body}"
        if tags:
            text = f"{text} " + " ".join(f"#{str(t).lstrip('#')}" for t in tags)
        try:
            path = append_capture(settings.vault_path, text, taxonomy=settings.taxonomy)
        except WriterError as exc:
            raise_http(exc)
        _safe_record_standalone_event(
            tag="CAPTURE",
            text=f"capture written to {path}",
            instance_id=instance_id,
            subject=str(title) if title else None,
        )
        return {"ok": True, "path": path}

    @router.post("/tasks")
    async def create_task(payload: dict, instance_id: str = Depends(guard)) -> dict:
        text = str(payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=422, detail="'text' is required")
        line = f"- [ ] {text}"
        if payload.get("priority"):
            line = f"{line} !{str(payload['priority']).lstrip('!')}"
        if payload.get("due"):
            line = f"{line} @due({payload['due']})"
        for tag in payload.get("tags") or []:
            line = f"{line} #{str(tag).lstrip('#')}"
        try:
            path = append_capture(settings.vault_path, line, taxonomy=settings.taxonomy)
        except WriterError as exc:
            raise_http(exc)
        _safe_record_standalone_event(
            tag="CAPTURE",
            text=f"task written to {path}",
            instance_id=instance_id,
            subject="task",
        )
        return {"ok": True, "path": path}

    @router.post("/widget/{slug}")
    async def push_widget(
        slug: str, payload: dict, run: str | None = None, instance_id: str = Depends(guard)
    ) -> dict:
        try:
            widget = validate_widget_payload(payload)
        except WidgetValidationError as exc:
            # 422 naming what was wrong, and the previously stored payload is
            # left exactly as it was. A broken push must never replace good
            # data with nothing — a panel showing yesterday's numbers beats a
            # panel showing an error.
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        payload_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        conn = db()
        try:
            if run:
                # The async half of a run: this payload belongs to a card that
                # is still waiting, not to the dashboard.
                if store.get_run(conn, run) is None:
                    raise HTTPException(status_code=404, detail=f"unknown run {run!r}")
                store.finish_run(
                    conn, run, "ok", mode="widget", payload=widget.payload
                )
                _safe_record_event(
                    conn,
                    tag="PUSH",
                    text=f"{widget.kind} push, {payload_bytes} bytes (run {run})",
                    instance_id=instance_id,
                    subject=slug,
                )
                return {"ok": True, "run": run}

            store.upsert_widget(
                conn,
                slug,
                widget.kind,
                widget.payload,
                title=widget.title,
                expected_interval_seconds=widget.expected_interval_seconds,
                instance_id=instance_id,
            )
            _safe_record_event(
                conn,
                tag="PUSH",
                text=f"{widget.kind} push, {payload_bytes} bytes",
                instance_id=instance_id,
                subject=slug,
            )
        finally:
            conn.close()
        return {"ok": True, "slug": slug}

    # --- reads (I3-filtered, every one of them) ------------------------------

    @router.get("/agenda", dependencies=guarded)
    async def agenda(date_: str | None = None) -> dict:
        target = date.fromisoformat(date_) if date_ else today()
        conn = db()
        try:
            refresh_cache(conn, settings.vault_path, taxonomy=settings.taxonomy)
            buckets = bucketed_tasks(conn, today=target)
        finally:
            conn.close()

        cache = VisibilityCache(settings.vault_path, taxonomy=settings.taxonomy)
        day = visible_tasks(
            buckets["overdue"] + buckets["today"], settings.vault_path, cache=cache
        )
        return {
            "date": target.isoformat(),
            "tasks": [t.model_dump() for t in day],
            "top_tasks": [t.model_dump() for t in day[:3]],
        }

    @router.get("/tasks", dependencies=guarded)
    async def open_tasks(status: str = "open") -> dict:
        conn = db()
        try:
            refresh_cache(conn, settings.vault_path, taxonomy=settings.taxonomy)
            buckets = bucketed_tasks(conn)
        finally:
            conn.close()

        cache = VisibilityCache(settings.vault_path, taxonomy=settings.taxonomy)
        out: dict[str, list[dict]] = {}
        for name, items in buckets.items():
            filtered = visible_tasks(items, settings.vault_path, cache=cache)
            if status == "open":
                filtered = [t for t in filtered if not t.done]
            out[name] = [t.model_dump() for t in filtered]
        return out

    @router.get("/briefing", dependencies=guarded)
    async def briefing(date_: str | None = None) -> dict:
        from backend.features.briefing.service import briefing_data, render_briefing

        target = date.fromisoformat(date_) if date_ else today()
        cache = VisibilityCache(settings.vault_path, taxonomy=settings.taxonomy)
        conn = db()
        try:
            # The predicate goes *in*, not a filter over the result: the
            # briefing derives exam countdowns from the same task list, so
            # filtering the output would still leak a withheld task's text.
            data = briefing_data(settings, conn, target, note_visible=cache.is_visible)
        finally:
            conn.close()
        return {"date": target.isoformat(), "markdown": render_briefing(data)}

    return router
