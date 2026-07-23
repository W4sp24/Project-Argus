"""Quick Links: user-curated shortcut buttons shown on the dashboard.

Mirrors ``backend/flashcards.py``'s plain-``sqlite3``, plain-dict CRUD style.
The one thing that makes this module security-sensitive is
:func:`sanitize_url`: quick links round-trip through the dashboard as plain
``<a href>`` targets, so anything that isn't a real ``https://`` URL (a
``javascript:`` URI, a protocol-relative ``//evil.com``, a control-character
obfuscated scheme, ...) must be rejected before it ever reaches the database.
"""

from __future__ import annotations

import re
import sqlite3
from urllib.parse import urlparse

_MAX_LABEL_LEN = 80
_MAX_ICON_LEN = 8

# ASCII control characters (0x00-0x1F, 0x7F) stripped from anywhere in the
# raw URL before any other check runs. This defeats obfuscation like
# ``java\tscript:`` or ``java\nscript:``, which otherwise collapse into a
# working ``javascript:`` scheme once whitespace-trimmed by a naive check.
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

# Pseudo-schemes that must never be stored, regardless of case or of whether
# the rest of the string looks like a URL.
_DANGEROUS_SCHEMES = (
    "javascript:",
    "data:",
    "file:",
    "vbscript:",
    "blob:",
    "about:",
    "mailto:",
    "tel:",
)


class QuickLinksError(Exception):
    """Raised on invalid quick-link input (bad url/label/icon, or unknown id)."""


def sanitize_url(raw: str) -> str:
    """Normalize ``raw`` to a safe ``https://`` URL, or raise :class:`QuickLinksError`.

    - Strips leading/trailing whitespace and ASCII control characters
      anywhere in the string.
    - ``http://`` is upgraded to ``https://``; ``https://`` is kept as-is.
    - A schemeless value that looks like a host (contains a dot, no spaces)
      is treated as ``https://<value>``.
    - Dangerous pseudo-schemes (``javascript:``, ``data:``, ``file:``,
      ``vbscript:``, ``blob:``, ``about:``, ``mailto:``, ``tel:``) and
      protocol-relative (``//...``) URLs are always rejected, as is any
      other scheme that isn't ``http``/``https``.
    - The final result must parse (via :func:`urllib.parse.urlparse`) with
      ``scheme == "https"`` and a non-empty ``netloc``.
    """
    if raw is None:
        raise QuickLinksError("url is required")
    cleaned = _CONTROL_CHARS_RE.sub("", raw).strip()
    if not cleaned:
        raise QuickLinksError("url is required")

    # Only the scheme is ever lowercased for comparison — never the whole
    # URL, since path/query/fragment can be case-sensitive.
    lowered = cleaned.lower()

    if lowered.startswith("//"):
        raise QuickLinksError("protocol-relative URLs are not allowed")

    for scheme in _DANGEROUS_SCHEMES:
        if lowered.startswith(scheme):
            raise QuickLinksError(f"scheme not allowed: {scheme[:-1]}")

    if "://" in cleaned:
        scheme, _, rest = cleaned.partition("://")
        scheme_lower = scheme.lower()
        if scheme_lower in ("http", "https"):
            normalized = f"https://{rest}"
        else:
            raise QuickLinksError(f"scheme not allowed: {scheme_lower or '(empty)'}")
    else:
        # No scheme at all: only accept it if it plausibly names a host.
        if " " in cleaned or "." not in cleaned:
            raise QuickLinksError("url must be a valid https:// URL")
        normalized = f"https://{cleaned}"

    parsed = urlparse(normalized)
    if parsed.scheme != "https" or not parsed.netloc:
        raise QuickLinksError("url must be a valid https:// URL with a host")
    return normalized


def sanitize_label(raw: str) -> str:
    """Trim, collapse internal whitespace, and cap the length of a label."""
    if raw is None:
        raise QuickLinksError("label is required")
    collapsed = " ".join(raw.split())
    if not collapsed:
        raise QuickLinksError("label is required")
    return collapsed[:_MAX_LABEL_LEN]


def sanitize_icon(raw: str | None) -> str | None:
    """Trim an icon glyph; empty/None becomes ``None``."""
    if raw is None:
        return None
    trimmed = raw.strip()
    return trimmed[:_MAX_ICON_LEN] or None


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "label": row["label"],
        "url": row["url"],
        "icon": row["icon"],
        "sort_order": row["sort_order"],
    }


def _get_link_row(conn: sqlite3.Connection, link_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT id, created_at, label, url, icon, sort_order FROM quick_links WHERE id = ?",
        (link_id,),
    ).fetchone()
    if row is None:
        raise QuickLinksError(f"quick link not found: {link_id}")
    return row


def list_links(conn: sqlite3.Connection) -> list[dict]:
    """All quick links, in display order."""
    rows = conn.execute(
        "SELECT id, created_at, label, url, icon, sort_order FROM quick_links"
        " ORDER BY sort_order, id"
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def create_link(conn: sqlite3.Connection, *, label: str, url: str, icon: str | None) -> dict:
    """Sanitize and insert a new quick link, appended to the end of the order."""
    clean_label = sanitize_label(label)
    clean_url = sanitize_url(url)
    clean_icon = sanitize_icon(icon)

    max_row = conn.execute("SELECT MAX(sort_order) AS max_order FROM quick_links").fetchone()
    next_order = (max_row["max_order"] or 0) + 1

    cursor = conn.execute(
        "INSERT INTO quick_links (label, url, icon, sort_order) VALUES (?, ?, ?, ?)",
        (clean_label, clean_url, clean_icon, next_order),
    )
    conn.commit()
    return _row_to_dict(_get_link_row(conn, int(cursor.lastrowid)))


def update_link(
    conn: sqlite3.Connection,
    link_id: int,
    *,
    label: str | None = None,
    url: str | None = None,
    icon: str | None = None,
    sort_order: int | None = None,
) -> dict:
    """Update only the provided fields (also used for drag-to-reorder via ``sort_order``).

    Raises :class:`QuickLinksError` if ``link_id`` doesn't exist.
    """
    _get_link_row(conn, link_id)  # raises QuickLinksError if missing

    fields: list[str] = []
    values: list[object] = []
    if label is not None:
        fields.append("label = ?")
        values.append(sanitize_label(label))
    if url is not None:
        fields.append("url = ?")
        values.append(sanitize_url(url))
    if icon is not None:
        fields.append("icon = ?")
        values.append(sanitize_icon(icon))
    if sort_order is not None:
        fields.append("sort_order = ?")
        values.append(sort_order)

    if fields:
        values.append(link_id)
        conn.execute(f"UPDATE quick_links SET {', '.join(fields)} WHERE id = ?", values)
        conn.commit()

    return _row_to_dict(_get_link_row(conn, link_id))


def delete_link(conn: sqlite3.Connection, link_id: int) -> None:
    """Delete a quick link. Raises :class:`QuickLinksError` if it doesn't exist."""
    _get_link_row(conn, link_id)  # raises QuickLinksError if missing
    conn.execute("DELETE FROM quick_links WHERE id = ?", (link_id,))
    conn.commit()
