"""Remember what a page said, so OCR is paid once and not once per reindex.

Reading an image-only PDF page costs roughly seven seconds: render at 200dpi,
then run a detection and a recognition model over it. ICS26011's four decks
are 133 such pages, so a full ``reindex_all`` would spend about fifteen
minutes re-deriving text that has not changed -- and ``reindex_all`` runs
whenever the chunk schema moves, which is any release that touches chunking.

Keyed by content hash rather than by path, because a file that was renamed,
moved between courses or re-uploaded is the same pixels and deserves the same
answer. That also makes the table self-invalidating: edit the PDF and the
hash changes, so nothing has to notice.

Everything here is derived. Dropping the table costs one re-extraction and
nothing else, which is why it carries no foreign keys and is never migrated.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

#: Read in chunks so hashing a 200MB scan does not hold it all in memory.
_HASH_CHUNK = 1 << 20


def file_hash(path: Path) -> str:
    """A content hash for one file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def get_page(conn: sqlite3.Connection, content_hash: str, page: int) -> tuple[str, dict] | None:
    """The remembered text and extraction metadata for one page, if any."""
    row = conn.execute(
        "SELECT text, method FROM extraction_cache WHERE file_hash = ? AND page = ?",
        (content_hash, page),
    ).fetchone()
    if row is None:
        return None
    try:
        meta = json.loads(row["method"])
    except (TypeError, ValueError):
        meta = {"method": "ocr"}
    return row["text"], meta


def put_page(
    conn: sqlite3.Connection,
    content_hash: str,
    page: int,
    text: str,
    meta: dict[str, Any],
) -> None:
    """Remember one page's text.

    ``INSERT OR REPLACE`` rather than a read-then-write: two ingests of the
    same file can race, and the second writing the same answer over the first
    is harmless, whereas a UNIQUE violation would fail an ingest over a cache.
    """
    conn.execute(
        "INSERT OR REPLACE INTO extraction_cache (file_hash, page, text, method) "
        "VALUES (?, ?, ?, ?)",
        (content_hash, page, text, json.dumps(meta, sort_keys=True)),
    )
    conn.commit()


def forget_file(conn: sqlite3.Connection, content_hash: str) -> int:
    """Drop every remembered page for one file. Returns the rows removed."""
    cursor = conn.execute(
        "DELETE FROM extraction_cache WHERE file_hash = ?", (content_hash,)
    )
    conn.commit()
    return cursor.rowcount
