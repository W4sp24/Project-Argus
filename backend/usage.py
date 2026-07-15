"""Token-usage log for the dashboard's TOKENS.CLAUDE panel (redesign §14).

Every model call site records ``{ts, feature, session_id, model, in, out}``
from the Anthropic response ``usage`` field into the ``token_usage`` table.
Recording is strictly best-effort (like the audit log): a logging failure
must never break chat, planning, or the 07:00 briefing.

Session definition: one backend **process boot**. ``SESSION_ID`` is minted
when this module first imports; chat conversations today live client-side
with no server-side session rows, so process lifetime is the simplest
correct boundary (documented on :class:`UsageReport`).
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel

from backend.config import FALLBACK_RATE, MODEL_RATES

SESSION_ID = uuid.uuid4().hex  # one per backend process boot

Range = Literal["session", "week", "all"]


class UsagePoint(BaseModel):
    """One point on the usage line chart (an exchange, a day, or a week)."""

    label: str
    input_tokens: int
    output_tokens: int
    total_tokens: int


class FeatureUsage(BaseModel):
    """Per-feature totals (chat / planner / briefing / study / ingest ...)."""

    feature: str
    input_tokens: int
    output_tokens: int
    total_tokens: int


class UsageReport(BaseModel):
    """GET /api/usage payload.

    ``session_id`` identifies the current backend process boot — a "session"
    is the running server's lifetime (chat has no server-side session store,
    so process boot is the session boundary). ``series`` granularity depends
    on ``range``: per-exchange for ``session``, per-day for ``week``,
    per-week for ``all``. ``estimated_cost_usd`` uses the static per-model
    rate table in :mod:`backend.config` — an estimate, not a bill.
    """

    range: Range
    session_id: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    estimated_cost_usd: float
    series: list[UsagePoint]
    features: list[FeatureUsage]


def record_usage(
    db_path: Path | None,
    feature: str,
    input_tokens: int,
    output_tokens: int,
    model: str = "",
) -> None:
    """Insert one usage row. Swallows every error (fire-and-forget)."""
    try:
        if db_path is None:  # call sites without Settings (generate/composer)
            from backend.config import Settings

            db_path = Settings.load().db_path
        from backend.db import connect, init_schema

        conn = connect(db_path)
        try:
            init_schema(conn)
            conn.execute(
                "INSERT INTO token_usage (feature, session_id, model, input_tokens,"
                " output_tokens) VALUES (?, ?, ?, ?, ?)",
                (feature, SESSION_ID, model, int(input_tokens), int(output_tokens)),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception:  # noqa: S110 - usage logging is strictly best-effort
        pass


def record_result_usage(
    db_path: Path | None, feature: str, message: Any, model: str = ""
) -> None:
    """Record from an agent-sdk ResultMessage-like object. Swallows everything."""
    try:
        usage = getattr(message, "usage", None)
        if usage is None:
            return
        if not isinstance(usage, dict):
            usage = vars(usage)
        record_usage(
            db_path,
            feature,
            int(usage.get("input_tokens") or 0),
            int(usage.get("output_tokens") or 0),
            model,
        )
    except Exception:  # noqa: S110 - usage logging is strictly best-effort
        pass


def _cost(rows: list[sqlite3.Row]) -> float:
    total = 0.0
    for row in rows:
        rate = MODEL_RATES.get(row["model"], FALLBACK_RATE)
        total += row["input_tokens"] * rate["input"] / 1_000_000
        total += row["output_tokens"] * rate["output"] / 1_000_000
    return round(total, 4)


def _series(rows: list[sqlite3.Row], range_: Range) -> list[UsagePoint]:
    if range_ == "session":  # one point per exchange (per recorded call)
        return [
            UsagePoint(
                label=row["ts"],
                input_tokens=row["input_tokens"],
                output_tokens=row["output_tokens"],
                total_tokens=row["input_tokens"] + row["output_tokens"],
            )
            for row in rows
        ]
    buckets: dict[str, list[int]] = {}
    for row in rows:
        key = row["ts"][:10] if range_ == "week" else f"{row['ts'][:4]}-w{row['week']}"
        entry = buckets.setdefault(key, [0, 0])
        entry[0] += row["input_tokens"]
        entry[1] += row["output_tokens"]
    return [
        UsagePoint(
            label=label,
            input_tokens=in_out[0],
            output_tokens=in_out[1],
            total_tokens=in_out[0] + in_out[1],
        )
        for label, in_out in sorted(buckets.items())
    ]


def usage_report(
    conn: sqlite3.Connection, range_: Range, session_id: str = SESSION_ID
) -> UsageReport:
    """Aggregate the token_usage table for one range."""
    where, params = "", ()
    if range_ == "session":
        where, params = "WHERE session_id = ?", (session_id,)
    elif range_ == "week":
        where = "WHERE ts >= datetime('now', '-6 days', 'start of day')"
    rows = conn.execute(
        "SELECT ts, feature, model, input_tokens, output_tokens,"
        " strftime('%W', ts) AS week"
        f" FROM token_usage {where} ORDER BY id",
        params,
    ).fetchall()

    total_in = sum(row["input_tokens"] for row in rows)
    total_out = sum(row["output_tokens"] for row in rows)

    by_feature: dict[str, list[int]] = {}
    for row in rows:
        entry = by_feature.setdefault(row["feature"], [0, 0])
        entry[0] += row["input_tokens"]
        entry[1] += row["output_tokens"]

    return UsageReport(
        range=range_,
        session_id=session_id,
        input_tokens=total_in,
        output_tokens=total_out,
        total_tokens=total_in + total_out,
        estimated_cost_usd=_cost(rows),
        series=_series(rows, range_),
        features=[
            FeatureUsage(
                feature=feature,
                input_tokens=in_out[0],
                output_tokens=in_out[1],
                total_tokens=in_out[0] + in_out[1],
            )
            for feature, in_out in sorted(by_feature.items())
        ],
    )
