"""Account-wide Claude Code CLI usage, parsed from local session transcripts.

Claude Code persists every session as a JSONL transcript under
``~/.claude/projects/<project-dir>/<session-uuid>.jsonl``. Every ``"assistant"``
line carries a ``message.usage`` object (input/output/cache tokens) and
``message.model`` — this is the only local source of real, account-wide Claude
token consumption, distinct from :mod:`backend.usage` (which only sees tokens
spent by Argus's own chat/planner/study-generate calls).

Parsing is best-effort throughout (like :mod:`backend.usage`): a missing
``~/.claude`` directory, a malformed line, or an unreadable file must never
raise — it just yields nothing for that file. Background subagent transcripts
(nested ``<session>/subagents/agent-*.jsonl`` files) are intentionally excluded
— only top-level session files count.

Parsed rows are cached in the ``cli_usage`` table, keyed by a per-file
``(mtime_ns, size)`` high-water mark in ``cli_usage_files`` — unchanged files
are skipped entirely on repeat scans; changed/new files are fully re-parsed
(transcripts are append-only during a live session, so a size/mtime change
means "more lines", making whole-file re-parse simple and correct without
tracking byte offsets).
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from backend.config import FALLBACK_RATE, MODEL_RATES

DEFAULT_CLAUDE_HOME = Path.home() / ".claude" / "projects"

CliRange = Literal["today", "week", "all"]

_SYNTHETIC_MODEL = "<synthetic>"


class CliModelUsage(BaseModel):
    """Per-model totals across all local Claude Code sessions."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    total_tokens: int


class CliUsagePoint(BaseModel):
    """One point on the CLI usage line chart (a day or a week)."""

    label: str
    total_tokens: int


class CliUsageReport(BaseModel):
    """GET /api/usage/cli payload.

    ``today`` = current calendar day, ``week`` = rolling 7 days (day-bucketed
    series), ``all`` = full local history (week-bucketed series) — distinct
    from :class:`backend.usage.Range`, since CLI transcripts have no
    "backend process boot" concept to map a ``session`` range onto.
    """

    range: CliRange
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    series: list[CliUsagePoint]
    models: list[CliModelUsage]


def scan_projects(root: Path = DEFAULT_CLAUDE_HOME) -> Iterator[Path]:
    """Top-level ``*.jsonl`` transcripts directly under each project dir.

    Never descends into ``subagents/`` (background-agent transcripts are
    intentionally excluded). Yields nothing if ``root`` doesn't exist.
    """
    if not root.is_dir():
        return
    for project_dir in root.iterdir():
        if project_dir.is_dir():
            yield from project_dir.glob("*.jsonl")


def parse_transcript(path: Path) -> Iterator[dict]:
    """Yield one dict per qualifying assistant line in ``path``.

    Skips non-assistant lines, malformed JSON, lines missing usage/model/
    timestamp, and the synthetic ``<synthetic>`` model placeholder. Never
    raises — an unreadable file just yields nothing.
    """
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                if obj.get("type") != "assistant":
                    continue
                message = obj.get("message") or {}
                usage = message.get("usage")
                model = message.get("model")
                ts = obj.get("timestamp")
                if not usage or not model or not ts or model == _SYNTHETIC_MODEL:
                    continue
                yield {
                    "ts": ts,
                    "model": model,
                    "input_tokens": int(usage.get("input_tokens") or 0),
                    "output_tokens": int(usage.get("output_tokens") or 0),
                    "cache_creation_input_tokens": int(
                        usage.get("cache_creation_input_tokens") or 0
                    ),
                    "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0),
                }
    except OSError:
        return


def sync_cli_usage(conn: sqlite3.Connection, root: Path = DEFAULT_CLAUDE_HOME) -> int:
    """Ingest new/modified transcript files into ``cli_usage``.

    Files whose ``(mtime_ns, size)`` match the stored high-water mark in
    ``cli_usage_files`` are skipped entirely. Returns the count of newly
    ingested rows. Never raises — a missing/corrupt ``root`` yields 0.
    """
    known = {
        row["path"]: (row["mtime_ns"], row["size"])
        for row in conn.execute("SELECT path, mtime_ns, size FROM cli_usage_files")
    }
    inserted = 0
    for file_path in scan_projects(root):
        try:
            stat = file_path.stat()
        except OSError:
            continue
        key = str(file_path)
        stamp = (stat.st_mtime_ns, stat.st_size)
        if known.get(key) == stamp:
            continue  # unchanged — skip entirely
        conn.execute("DELETE FROM cli_usage WHERE file_path = ?", (key,))
        rows = list(parse_transcript(file_path))
        conn.executemany(
            "INSERT INTO cli_usage (file_path, ts, model, input_tokens, output_tokens,"
            " cache_creation_input_tokens, cache_read_input_tokens)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    key,
                    r["ts"],
                    r["model"],
                    r["input_tokens"],
                    r["output_tokens"],
                    r["cache_creation_input_tokens"],
                    r["cache_read_input_tokens"],
                )
                for r in rows
            ],
        )
        inserted += len(rows)
        conn.execute(
            "INSERT INTO cli_usage_files (path, mtime_ns, size) VALUES (?, ?, ?)"
            " ON CONFLICT(path) DO UPDATE SET mtime_ns=excluded.mtime_ns, size=excluded.size,"
            " scanned_at=datetime('now')",
            (key, stat.st_mtime_ns, stat.st_size),
        )
    conn.commit()
    return inserted


def _cost(rows: list[sqlite3.Row]) -> float:
    total = 0.0
    for row in rows:
        rate = MODEL_RATES.get(row["model"], FALLBACK_RATE)
        total += row["input_tokens"] * rate["input"] / 1_000_000
        total += row["output_tokens"] * rate["output"] / 1_000_000
    return round(total, 4)


def _row_total(row: sqlite3.Row) -> int:
    return (
        row["input_tokens"]
        + row["output_tokens"]
        + row["cache_creation_input_tokens"]
        + row["cache_read_input_tokens"]
    )


def _series(rows: list[sqlite3.Row], range_: CliRange) -> list[CliUsagePoint]:
    buckets: dict[str, int] = {}
    for row in rows:
        key = row["ts"][:10] if range_ != "all" else f"{row['ts'][:4]}-w{row['week']}"
        buckets[key] = buckets.get(key, 0) + _row_total(row)
    return [CliUsagePoint(label=label, total_tokens=total) for label, total in sorted(buckets.items())]


def cli_usage_report(
    conn: sqlite3.Connection, range_: CliRange, root: Path = DEFAULT_CLAUDE_HOME
) -> CliUsageReport:
    """Sync then aggregate the ``cli_usage`` table for one range."""
    sync_cli_usage(conn, root)

    where = ""
    if range_ == "today":
        where = "WHERE ts >= datetime('now', 'start of day')"
    elif range_ == "week":
        where = "WHERE ts >= datetime('now', '-6 days', 'start of day')"
    rows = conn.execute(
        "SELECT ts, model, input_tokens, output_tokens, cache_creation_input_tokens,"
        " cache_read_input_tokens, strftime('%W', ts) AS week"
        f" FROM cli_usage {where} ORDER BY ts",
        (),
    ).fetchall()

    total_in = sum(row["input_tokens"] for row in rows)
    total_out = sum(row["output_tokens"] for row in rows)
    total_cache_creation = sum(row["cache_creation_input_tokens"] for row in rows)
    total_cache_read = sum(row["cache_read_input_tokens"] for row in rows)

    by_model: dict[str, list[int]] = {}
    for row in rows:
        entry = by_model.setdefault(row["model"], [0, 0, 0, 0])
        entry[0] += row["input_tokens"]
        entry[1] += row["output_tokens"]
        entry[2] += row["cache_creation_input_tokens"]
        entry[3] += row["cache_read_input_tokens"]

    return CliUsageReport(
        range=range_,
        input_tokens=total_in,
        output_tokens=total_out,
        cache_creation_input_tokens=total_cache_creation,
        cache_read_input_tokens=total_cache_read,
        total_tokens=total_in + total_out + total_cache_creation + total_cache_read,
        estimated_cost_usd=_cost(rows),
        series=_series(rows, range_),
        models=[
            CliModelUsage(
                model=model,
                input_tokens=totals[0],
                output_tokens=totals[1],
                cache_creation_input_tokens=totals[2],
                cache_read_input_tokens=totals[3],
                total_tokens=sum(totals),
            )
            for model, totals in sorted(by_model.items())
        ],
    )
