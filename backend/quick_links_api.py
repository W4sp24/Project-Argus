"""Quick Links endpoints, mounted by ``backend.main.create_app``.

Mirrors ``backend/flashcards_api.py``'s style: a router-builder taking
``Settings``, a per-request sqlite connection, and ``QuickLinksError``-style
domain exceptions mapped to HTTP status codes.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.config import Settings
from backend.db import connect, init_schema
from backend.quick_links import (
    QuickLinksError,
    create_link,
    delete_link,
    list_links,
    update_link,
)


class QuickLink(BaseModel):
    """One quick-link row, as returned to the dashboard."""

    id: int
    created_at: str
    label: str
    url: str
    icon: str | None = None
    # Custom icon (additive; NULL kind => fall back to the ``icon`` glyph).
    # ``icon_kind`` is 'preset' (icon_value = a bundled SVG key) or 'image'
    # (icon_value = a downscaled PNG data URI).
    icon_kind: str | None = None
    icon_value: str | None = None
    sort_order: int


class CreateLinkRequest(BaseModel):
    label: str
    url: str
    icon: str | None = None
    icon_kind: str | None = None
    icon_value: str | None = None


class UpdateLinkRequest(BaseModel):
    label: str | None = None
    url: str | None = None
    icon: str | None = None
    icon_kind: str | None = None
    icon_value: str | None = None
    sort_order: int | None = None


def build_quick_links_router(settings: Settings) -> APIRouter:
    """All /api/quick-links routes."""
    router = APIRouter(prefix="/api/quick-links")

    def db() -> sqlite3.Connection:
        conn = connect(settings.db_path)
        init_schema(conn)
        return conn

    @router.get("", response_model=list[QuickLink])
    def links() -> list[QuickLink]:
        conn = db()
        try:
            return [QuickLink(**row) for row in list_links(conn)]
        finally:
            conn.close()

    @router.post("", response_model=QuickLink)
    def create(request: CreateLinkRequest) -> QuickLink:
        conn = db()
        try:
            row = create_link(
                conn,
                label=request.label,
                url=request.url,
                icon=request.icon,
                icon_kind=request.icon_kind,
                icon_value=request.icon_value,
            )
        except QuickLinksError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            conn.close()
        return QuickLink(**row)

    @router.put("/{link_id}", response_model=QuickLink)
    def update(link_id: int, request: UpdateLinkRequest) -> QuickLink:
        # Forward ONLY the fields the client actually sent, so a drag-reorder
        # (``{sort_order}`` alone) leaves every icon column untouched, while an
        # edit that includes the icon fields can clear them to NULL. The writer
        # uses an _UNSET sentinel to tell "omitted" from an explicit null.
        sent = request.model_fields_set
        kwargs: dict[str, object] = {}
        for name in ("label", "url", "icon", "icon_kind", "icon_value", "sort_order"):
            if name in sent:
                kwargs[name] = getattr(request, name)

        conn = db()
        try:
            row = update_link(conn, link_id, **kwargs)  # type: ignore[arg-type]
        except QuickLinksError as exc:
            if str(exc).startswith("quick link not found"):
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            conn.close()
        return QuickLink(**row)

    @router.delete("/{link_id}")
    def delete(link_id: int) -> dict:
        conn = db()
        try:
            delete_link(conn, link_id)
        except QuickLinksError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        finally:
            conn.close()
        return {"ok": True}

    return router
