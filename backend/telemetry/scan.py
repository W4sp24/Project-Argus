"""Shared incremental transcript scanning for local coding-agent CLIs.

Claude Code and Codex both persist sessions as append-only JSONL under a
per-agent home directory, so both want the same machinery: skip files whose
``(mtime_ns, size)`` high-water mark in ``cli_usage_files`` is unchanged,
fully re-parse the ones that moved, and never raise when a file is missing or
malformed. Transcripts are append-only during a live session, so a size/mtime
change means "more lines", which makes whole-file re-parse both simple and
correct without tracking byte offsets.

Rows are tagged with the ``agent`` that produced them so one table serves
every source. Everything recorded before that column existed was Claude Code,
which is exactly what the migration's default backfills.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

CliRange = Literal["today", "week", "all"]

#: The four token counters every source reports, in insert order.
COUNTERS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


class UsagePoint(BaseModel):
    """One point on a usage chart (a day or a week), split by counter."""

    label: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    total_tokens: int = 0


class ModelUsage(BaseModel):
    """Per-model totals across every scanned session of one agent."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    total_tokens: int
    #: Zero when Argus has no published rate for this model — see ``unpriced``.
    estimated_cost_usd: float = 0.0


def sync_rows(
    conn: sqlite3.Connection,
    agent: str,
    files: Iterable[Path],
    parse: Callable[[Path], Iterator[dict]],
) -> int:
    """Ingest new/modified transcripts for one agent. Returns rows inserted.

    Never raises: an unreadable file is skipped, and a missing root simply
    yields no files.
    """
    known = {
        row["path"]: (row["mtime_ns"], row["size"])
        for row in conn.execute(
            "SELECT path, mtime_ns, size FROM cli_usage_files WHERE agent = ?", (agent,)
        )
    }
    inserted = 0
    seen: set[str] = set()
    for file_path in files:
        try:
            stat = file_path.stat()
        except OSError:
            continue
        key = str(file_path)
        seen.add(key)
        stamp = (stat.st_mtime_ns, stat.st_size)
        if known.get(key) == stamp:
            continue  # unchanged — skip entirely
        conn.execute("DELETE FROM cli_usage WHERE file_path = ? AND agent = ?", (key, agent))
        rows = list(parse(file_path))
        conn.executemany(
            "INSERT INTO cli_usage (file_path, agent, ts, model, input_tokens, output_tokens,"
            " cache_creation_input_tokens, cache_read_input_tokens)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    key,
                    agent,
                    row["ts"],
                    row["model"],
                    row["input_tokens"],
                    row["output_tokens"],
                    row["cache_creation_input_tokens"],
                    row["cache_read_input_tokens"],
                )
                for row in rows
            ],
        )
        inserted += len(rows)
        conn.execute(
            "INSERT INTO cli_usage_files (path, agent, mtime_ns, size) VALUES (?, ?, ?, ?)"
            " ON CONFLICT(path, agent) DO UPDATE SET mtime_ns=excluded.mtime_ns,"
            " size=excluded.size, scanned_at=datetime('now')",
            (key, agent, stat.st_mtime_ns, stat.st_size),
        )

    _reap(conn, agent, known.keys() - seen, bool(seen))
    conn.commit()
    return inserted


def _reap(
    conn: sqlite3.Connection, agent: str, vanished: set[str] | Iterable[str], saw_any: bool
) -> None:
    """Drop rows for transcripts that are no longer on disk.

    Agent CLIs rotate and clean up old sessions, and without this their tokens
    are counted forever — the totals only ever go up, and a usage panel that
    permanently over-reports is worse than none. Measured on one real machine
    before this existed: 7 deleted transcripts holding 447M tokens, a fifth of
    the reported figure.

    Reaping is skipped entirely when the scan saw *no* files. A missing root is
    far more often a folder that is temporarily unreachable — an unplugged
    drive, a path typo in a custom source — than a user who deleted every log,
    and wiping real history on that guess is not recoverable. Under-reaping is.
    """
    gone = list(vanished)
    if not gone or not saw_any:
        return
    conn.executemany(
        "DELETE FROM cli_usage WHERE agent = ? AND file_path = ?",
        [(agent, path) for path in gone],
    )
    conn.executemany(
        "DELETE FROM cli_usage_files WHERE agent = ? AND path = ?",
        [(agent, path) for path in gone],
    )


def _window(range_: CliRange) -> str:
    """The WHERE fragment bounding one range (empty for ``all``)."""
    if range_ == "today":
        return "ts >= datetime('now', 'start of day')"
    if range_ == "week":
        return "ts >= datetime('now', '-6 days', 'start of day')"
    return ""


def _previous_window(range_: CliRange) -> str:
    """The equivalent window immediately before ``range_``, for the delta chip.

    ``all`` has nothing before it, so it gets no comparison rather than a
    fabricated one.
    """
    if range_ == "today":
        return (
            "ts >= datetime('now', '-1 day', 'start of day')"
            " AND ts < datetime('now', 'start of day')"
        )
    if range_ == "week":
        return (
            "ts >= datetime('now', '-13 days', 'start of day')"
            " AND ts < datetime('now', '-6 days', 'start of day')"
        )
    return ""


def fetch_rows(
    conn: sqlite3.Connection, range_: CliRange, agent: str | None = None
) -> list[sqlite3.Row]:
    """Usage rows inside ``range_``, optionally for one agent only."""
    clauses = [clause for clause in (_window(range_), "agent = ?" if agent else "") if clause]
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params = (agent,) if agent else ()
    return conn.execute(
        "SELECT ts, agent, model, input_tokens, output_tokens,"
        " cache_creation_input_tokens, cache_read_input_tokens,"
        f" strftime('%W', ts) AS week FROM cli_usage {where} ORDER BY ts",
        params,
    ).fetchall()


def previous_total(
    conn: sqlite3.Connection, range_: CliRange, agent: str | None = None
) -> int | None:
    """Total tokens in the window before ``range_``; None when there isn't one."""
    window = _previous_window(range_)
    if not window:
        return None
    clauses = [window] + (["agent = ?"] if agent else [])
    params = (agent,) if agent else ()
    row = conn.execute(
        "SELECT COALESCE(SUM(input_tokens + output_tokens + cache_creation_input_tokens"
        f" + cache_read_input_tokens), 0) AS total FROM cli_usage WHERE {' AND '.join(clauses)}",
        params,
    ).fetchone()
    return int(row["total"])


def purge_agent(conn: sqlite3.Connection, agent: str) -> int:
    """Delete one agent's rows and its scan state. Returns rows removed.

    Called when a user-registered source is deleted. Without it the rows would
    outlive the thing that explains them: still counted in the combined total,
    with nothing in the selector to attribute them to, so the parts would
    visibly fail to add up to the whole.
    """
    removed = conn.execute("DELETE FROM cli_usage WHERE agent = ?", (agent,)).rowcount
    conn.execute("DELETE FROM cli_usage_files WHERE agent = ?", (agent,))
    conn.commit()
    return max(0, removed)


def row_total(row: sqlite3.Row | dict) -> int:
    return sum(int(row[counter] or 0) for counter in COUNTERS)


def bucket_key(ts: str, week: str, range_: CliRange) -> str:
    """Which bucket a timestamp falls in, per range.

    ``today`` buckets by **hour**: bucketing a single day by day produces one
    point, which a chart can only draw as a flat block. Hours give the day its
    actual shape. Timestamps are UTC throughout this table (Claude Code writes
    ISO-Z, and the range windows use ``datetime('now')``), so the hour labels
    are UTC — the same basis the day boundary already used.
    """
    if range_ == "today":
        return f"{ts[11:13]}:00"
    if range_ == "week":
        return ts[:10]
    return f"{ts[:4]}-w{week}"


def series(rows: list[sqlite3.Row], range_: CliRange) -> list[UsagePoint]:
    """Hour buckets for today, day for week, ISO-week for all-time."""
    buckets: dict[str, list[int]] = {}
    for row in rows:
        key = bucket_key(row["ts"], row["week"], range_)
        entry = buckets.setdefault(key, [0, 0, 0, 0])
        for index, counter in enumerate(COUNTERS):
            entry[index] += int(row[counter] or 0)
    return [
        UsagePoint(
            label=label,
            input_tokens=totals[0],
            output_tokens=totals[1],
            cache_creation_input_tokens=totals[2],
            cache_read_input_tokens=totals[3],
            total_tokens=sum(totals),
        )
        for label, totals in sorted(buckets.items())
    ]


def totals_by_model(rows: list[sqlite3.Row]) -> dict[str, list[int]]:
    """``{model: [in, out, cache_creation, cache_read]}`` across ``rows``."""
    by_model: dict[str, list[int]] = {}
    for row in rows:
        entry = by_model.setdefault(row["model"], [0, 0, 0, 0])
        for index, counter in enumerate(COUNTERS):
            entry[index] += int(row[counter] or 0)
    return by_model
